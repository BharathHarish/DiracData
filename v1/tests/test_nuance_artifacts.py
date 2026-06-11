import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import (
    ColumnProfile,
    LearningCollection,
    LearningScope,
    NuanceArtifactBuilder,
    TableProfile,
)
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.storage import LocalObjectStore


class NuanceArtifactBuilderTest(unittest.TestCase):
    def test_builds_null_confounder_invariant_and_question_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="pod", database="analytics", schema="main")
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            library_manifest_key = _write_library_patterns(settings, store, collection.run_id)
            store.write_json(
                active_learning_artifact_key(settings, relative_path="manifest.json"),
                {"active_run_id": collection.run_id},
            )
            builder = NuanceArtifactBuilder(settings=settings, object_store=store)

            result = builder.build(
                collection=collection,
                business_grounding=_business_grounding(),
                query_libraries_manifest_artifact_key=library_manifest_key,
            )

            nulls = yaml.safe_load(store.read_text(result.null_semantics_artifact_key))
            confounders = _read_jsonl(store.read_text(result.confounders_artifact_key))
            invariants = yaml.safe_load(store.read_text(result.invariants_artifact_key))
            questions = yaml.safe_load(store.read_text(result.analyst_questions_artifact_key))
            review_pack = store.read_text(result.review_pack_artifact_key)
            active_manifest = store.read_json(
                active_learning_artifact_key(settings, relative_path="manifest.json")
            )

        self.assertEqual(nulls["artifact_type"], "null_semantics_candidates")
        self.assertEqual(len(nulls["candidates"]), 1)
        self.assertEqual(nulls["candidates"][0]["column_name"], "segment")
        self.assertTrue(
            any(row["confounder_type"] == "exact_column_name" for row in confounders)
        )
        invariant_ids = {row["id"] for row in invariants["invariants"]}
        self.assertIn("invariant:grounding:event_time_default", invariant_ids)
        self.assertIn("invariant:metric_contract:conversion_rate", invariant_ids)
        self.assertTrue(any(row["invariant_type"] == "join_pattern" for row in invariants["invariants"]))
        self.assertTrue(
            any(question["question_type"] == "null_semantics" for question in questions["questions"])
        )
        self.assertIn("Learning Review Pack", review_pack)
        self.assertIn("nuance_manifest_artifact_key", active_manifest["active_artifacts"])


def _collection(settings: DiracDataSettings) -> LearningCollection:
    run_id = "nuance_test"
    return LearningCollection(
        run_id=run_id,
        scope=LearningScope(settings.catalog, settings.database, settings.schema),
        table_profiles=[
            TableProfile(
                "events",
                100,
                "samples/events.csv",
                [
                    ColumnProfile("events", "event_ref", "VARCHAR", 0, 0.0, 100),
                    ColumnProfile("events", "user_ref", "VARCHAR", 0, 0.0, 80),
                    ColumnProfile("events", "event_time", "TIMESTAMP", 0, 0.0, 100),
                    ColumnProfile("events", "status", "VARCHAR", 0, 0.0, 3),
                ],
            ),
            TableProfile(
                "users",
                80,
                "samples/users.csv",
                [
                    ColumnProfile("users", "user_ref", "VARCHAR", 0, 0.0, 80),
                    ColumnProfile("users", "signup_time", "TIMESTAMP", 0, 0.0, 80),
                    ColumnProfile("users", "status", "VARCHAR", 0, 0.0, 3),
                    ColumnProfile(
                        "users",
                        "segment",
                        "VARCHAR",
                        12,
                        0.15,
                        4,
                        distinct_values=["startup", "enterprise"],
                    ),
                ],
            ),
        ],
        profile_artifact_key=learning_artifact_key(
            settings,
            run_id=run_id,
            relative_path="profiles/table_profiles.json",
        ),
        llm_context_artifact_key=learning_artifact_key(
            settings,
            run_id=run_id,
            relative_path="profiles/llm_context.json",
        ),
    )


def _write_library_patterns(
    settings: DiracDataSettings,
    store: LocalObjectStore,
    run_id: str,
) -> str:
    pattern_key = learning_artifact_key(
        settings,
        run_id=run_id,
        relative_path="libraries/query_patterns.jsonl",
    )
    manifest_key = learning_artifact_key(
        settings,
        run_id=run_id,
        relative_path="libraries/manifest.json",
    )
    pattern = {
        "id": "library_pattern:event_user",
        "query_count": 8,
        "compact_contract": {
            "fact_table": "events",
            "metrics": ["conversion_rate"],
            "required_joins": ["events.user_ref = users.user_ref"],
            "avoid_joins": ["events.status = users.status"],
        },
    }
    store.write_text(pattern_key, json.dumps(pattern) + "\n")
    store.write_json(
        manifest_key,
        {
            "canonical_artifacts": {
                "query_patterns_artifact_key": pattern_key,
            }
        },
    )
    return manifest_key


def _business_grounding() -> dict[str, object]:
    return {
        "defaults": [
            {
                "id": "event_time_default",
                "policy": "Use event_time for event metrics.",
                "field": "events.event_time",
            }
        ],
        "metrics": [
            {
                "id": "conversion_rate",
                "name": "Conversion rate",
                "parameterized_sql": {
                    "sql_contract": {
                        "numerator": {"column": "events.status", "value": "converted"},
                        "denominator": {"grain": "events.event_ref"},
                        "time_column": "events.event_time",
                    }
                },
            }
        ],
    }


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
