import json
import math
import re
import tempfile
import unittest
from collections import Counter

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.retrieval import CandidateBindingSearchService, compact_candidate_binding_context
from diracdata.retrieval.candidate_search import _heuristic_extraction
from diracdata.storage import LocalObjectStore
from diracdata.tools import build_data_analyst_tools
from diracdata.tools.candidate_tools import build_candidate_tools


class CandidateBindingSearchTest(unittest.TestCase):
    def test_resolves_low_risk_users_and_rejects_payment_risk_confounder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_candidate_search_limit=20,
                agent_candidate_search_per_query_limit=20,
                agent_candidate_search_max_queries=12,
            )
            store = LocalObjectStore(tmpdir)
            _write_candidate_artifacts(settings=settings, store=store)
            service = CandidateBindingSearchService(settings=settings, object_store=store)

            result = service.search(
                "For April 2026 checkout orders from verified low-risk users in Karnataka, "
                "compare TPV by authentication mode."
            )

        self.assertEqual(result["status"], "ok")
        selected_columns = {
            binding["selected_column"]
            for binding in result["predicate_bindings"]
        }
        rejected_refs = {
            row["column_ref"]
            for row in result["rejected_confounders"]
            if row["user_phrase"].lower().startswith("verified low-risk users")
        }
        self.assertIn("user_attributes.risk_band", selected_columns)
        self.assertIn("payment_attributes.risk_band", rejected_refs)
        self.assertTrue(
            any(
                candidate["column_ref"] == "user_attributes.risk_band"
                and candidate["bm25_rank"] is not None
                for candidate in result["candidate_columns"]
            )
        )

    def test_resolves_payment_rail_risk_when_entity_phrase_points_to_rail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="fintech_pod", database="analytics", schema="fintech_schema")
            store = LocalObjectStore(tmpdir)
            _write_candidate_artifacts(settings=settings, store=store)
            service = CandidateBindingSearchService(settings=settings, object_store=store)

            result = service.search("Show low risk payment rail performance by authentication mode.")

        selected_columns = {
            binding["selected_column"]
            for binding in result["predicate_bindings"]
            if "risk" in binding["user_phrase"].lower()
        }
        rejected_columns = {row["column_ref"] for row in result["rejected_confounders"]}
        self.assertIn("payment_attributes.risk_band", selected_columns)
        self.assertIn("user_attributes.risk_band", rejected_columns)

    def test_candidate_search_tool_uses_same_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="fintech_pod", database="analytics", schema="fintech_schema")
            store = LocalObjectStore(tmpdir)
            _write_candidate_artifacts(settings=settings, store=store)
            service = CandidateBindingSearchService(settings=settings, object_store=store)
            tool = build_candidate_tools(service=service)[0]

            result = tool.invoke({"nl_query": "verified low-risk users in Karnataka"})

        compact = compact_candidate_binding_context(result)
        self.assertEqual(compact["status"], "ok")
        self.assertTrue(compact["predicate_bindings"])
        self.assertTrue(compact["rejected_confounders"])

    def test_tool_factory_uses_supplied_candidate_search_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings()
            store = LocalObjectStore(tmpdir)
            service = _RecordingCandidateService()
            tools = build_data_analyst_tools(
                settings=settings,
                object_store=store,
                query_engine=object(),
                candidate_search_service=service,  # type: ignore[arg-type]
            )
            tool_by_name = {tool.name: tool for tool in tools}

            result = tool_by_name["candidate_search_tool"].invoke({"nl_query": "find the right column"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(service.queries, ["find the right column"])

    def test_heuristic_extraction_preserves_quoted_literals(self) -> None:
        result = _heuristic_extraction("Show rows where status is 'verified' and type is \"premium\".")
        literals = {
            literal
            for phrase in result["phrases"]
            for literal in phrase.get("literals", [])
        }

        self.assertIn("verified", literals)
        self.assertIn("premium", literals)


class _RecordingCandidateService:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, nl_query: str) -> dict[str, object]:
        self.queries.append(nl_query)
        return {"status": "ok", "query": nl_query}


def _write_candidate_artifacts(*, settings: DiracDataSettings, store: LocalObjectStore) -> None:
    docs = [
        _doc(
            "retrieval:table:user_attributes",
            "table_container",
            "user_attributes",
            None,
            "user attributes demographic geographic risk kyc users merchants Karnataka verified low risk",
        ),
        _doc(
            "retrieval:table:payment_attributes",
            "table_container",
            "payment_attributes",
            None,
            "payment attributes rail route method authentication payment rail risk low medium high",
        ),
        _doc(
            "retrieval:column:user_attributes.risk_band",
            "column",
            "user_attributes",
            "risk_band",
            "user_attributes risk_band user risk segment low medium high users merchants fraud monitoring",
        ),
        _doc(
            "retrieval:column:payment_attributes.risk_band",
            "column",
            "payment_attributes",
            "risk_band",
            "payment_attributes risk_band payment rail route risk low medium high method monitoring",
        ),
        _doc(
            "retrieval:column:user_attributes.kyc_status",
            "column",
            "user_attributes",
            "kyc_status",
            "user_attributes kyc_status verified pending rejected user verification",
        ),
        _doc(
            "retrieval:column:user_attributes.state",
            "column",
            "user_attributes",
            "state",
            "user_attributes state Karnataka Maharashtra location geography user region",
        ),
        _doc(
            "retrieval:column:payment_attributes.authentication_mode",
            "column",
            "payment_attributes",
            "authentication_mode",
            "payment_attributes authentication mode upi pin otp bank auth payment verification",
        ),
    ]
    store.write_text(
        active_learning_artifact_key(settings, relative_path="retrieval/documents.jsonl"),
        "\n".join(json.dumps(doc) for doc in docs) + "\n",
    )
    store.write_json(
        active_learning_artifact_key(settings, relative_path="retrieval/bm25_plus_index.json"),
        _bm25_index(docs),
    )
    store.write_json(
        active_learning_artifact_key(settings, relative_path="descriptions/metadata_descriptions.json"),
        {
            "tables": {
                "user_attributes": {
                    "short_description": "Demographic, geographic, risk, and KYC attributes for each user or merchant.",
                    "long_description": "Used to segment user payment activity by location, verification, and user risk.",
                },
                "payment_attributes": {
                    "short_description": "Payment rail and routing attributes for each payment method or route.",
                    "long_description": "Used to slice payments by rail, authentication, issuer, settlement, and rail risk.",
                },
            },
            "columns": {
                "user_attributes": {
                    "risk_band": {
                        "short_description": "Risk segment assigned to the user for transaction monitoring: low, medium, or high."
                    },
                    "kyc_status": {
                        "short_description": "KYC identity verification status for the user: verified, pending, or rejected."
                    },
                    "state": {
                        "short_description": "Indian state where the user is located."
                    },
                },
                "payment_attributes": {
                    "risk_band": {
                        "short_description": "Risk classification of the payment rail for transaction monitoring: low, medium, or high."
                    },
                    "authentication_mode": {
                        "short_description": "Authentication method used to verify the payment."
                    },
                },
            },
        },
    )
    profile_key = active_learning_artifact_key(settings, relative_path="profiles/table_profiles.json")
    store.write_json(
        active_learning_artifact_key(settings, relative_path="contexts/learned_context.json"),
        {"profile_artifact_key": profile_key},
    )
    store.write_json(
        profile_key,
        {
            "tables": [
                {
                    "table_name": "user_attributes",
                    "columns": [
                        _profile_column("risk_band", ["low", "medium", "high"]),
                        _profile_column("kyc_status", ["verified", "pending", "rejected"]),
                        _profile_column("state", ["Karnataka", "Maharashtra"]),
                    ],
                },
                {
                    "table_name": "payment_attributes",
                    "columns": [
                        _profile_column("risk_band", ["low", "medium", "high"]),
                        _profile_column("authentication_mode", ["upi_pin", "otp_or_3ds"]),
                    ],
                },
            ]
        },
    )


def _doc(
    document_id: str,
    retrieval_type: str,
    table_name: str | None,
    column_name: str | None,
    text: str,
) -> dict[str, object]:
    return {
        "id": document_id,
        "retrieval_type": retrieval_type,
        "source_type": retrieval_type,
        "table_name": table_name,
        "column_name": column_name,
        "text_for_bm25": text,
        "text_for_embedding": text if retrieval_type == "column" else None,
        "metadata": {},
    }


def _profile_column(column_name: str, values: list[str]) -> dict[str, object]:
    return {
        "column_name": column_name,
        "top_values": [{"value": value, "count": 10} for value in values],
        "distinct_values": values,
    }


def _bm25_index(docs: list[dict[str, object]]) -> dict[str, object]:
    tokenized_docs = []
    document_frequencies: Counter[str] = Counter()
    for doc in docs:
        tokens = re.findall(r"[a-z0-9]+", str(doc["text_for_bm25"]).lower())
        counts = Counter(tokens)
        tokenized_docs.append(
            {
                "id": doc["id"],
                "length": len(tokens),
                "term_frequencies": dict(sorted(counts.items())),
            }
        )
        document_frequencies.update(set(tokens))
    doc_count = len(tokenized_docs)
    avgdl = sum(doc["length"] for doc in tokenized_docs) / doc_count
    return {
        "artifact_type": "bm25_plus_index",
        "algorithm": "bm25_plus",
        "parameters": {"k1": 1.2, "b": 0.75, "delta": 1.0},
        "document_count": doc_count,
        "average_document_length": avgdl,
        "documents": tokenized_docs,
        "idf": {
            term: math.log((doc_count + 1) / (df + 0.5))
            for term, df in sorted(document_frequencies.items())
        },
    }


if __name__ == "__main__":
    unittest.main()
