import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.tools import (
    CandidateSearchService,
    SQLPatternSearchService,
    SchemaInfoService,
)


class RetrievalToolsTests(unittest.TestCase):
    def test_pattern_search_returns_matching_sql_template(self):
        sql_library = {
            "entries": {
                "history:customers:gender_state:abc": {
                    "sql": "SELECT COUNT(*) FROM clients c JOIN client_profiles cp ON ...",
                }
            },
            "patterns": {
                "pattern:history:customers:gender_state:abc": {
                    "entry_id": "history:customers:gender_state:abc",
                    "canonical_question": "Count customers by gender and state.",
                    "paraphrases": ["How many male customers are from California?"],
                    "intent_signature": {
                        "grain": "customer",
                        "measure": "count customers",
                        "filters": ["gender", "state"],
                        "dimensions": ["customer profile", "customer address"],
                    },
                    "tables": ["clients", "client_profiles", "addresses"],
                    "columns": ["client_profiles.gender", "addresses.state"],
                    "sql_template": "SELECT COUNT(*) FROM clients ...",
                    "review_status": "observed",
                    "source_entry_ids": ["history:customers:gender_state:abc"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sql_library.json"
            path.write_text(json.dumps(sql_library), encoding="utf-8")
            service = SQLPatternSearchService.from_file(path)

        result = service.search(query="count male customers from california", top_k=3)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["patterns"][0]["id"],
            "pattern:history:customers:gender_state:abc",
        )
        self.assertIn("sql_template", result["patterns"][0])

    def test_candidate_search_returns_relevant_columns(self):
        metadata = {
            "tables": {
                "clients": {"short_description": "Customer accounts."},
                "addresses": {"short_description": "Customer addresses."},
            },
            "columns": {
                "clients": {
                    "client_record": {"short_description": "Customer identifier."},
                },
                "addresses": {
                    "state": {"short_description": "US state abbreviation for the customer."},
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.json"
            path.write_text(json.dumps(metadata), encoding="utf-8")
            service = CandidateSearchService.from_files(metadata_descriptions_path=path)

        result = service.search(
            query="customers from california state",
            search_terms=["state", "customer"],
            top_k=5,
        )
        column_refs = {
            f"{item['table_name']}.{item['column_name']}"
            for item in result["candidate_columns"]
        }

        self.assertIn("addresses.state", column_refs)
        self.assertGreaterEqual(len(result["candidate_groups"]), 2)
        self.assertIn("description_snippet", result["candidate_columns"][0])
        self.assertNotIn("text", result["candidate_columns"][0])
        self.assertNotIn("metadata", result["candidate_columns"][0])

    def test_schema_info_tools_return_exact_descriptions(self):
        metadata = {
            "tables": {"clients": {"short_description": "Customer accounts."}},
            "columns": {
                "clients": {
                    "gender": {
                        "short_description": "Customer gender.",
                        "long_description": "Gender code for the customer.",
                    }
                }
            },
        }
        service = SchemaInfoService(metadata_descriptions=metadata)

        self.assertEqual(service.get_tables()["tables"][0]["table_name"], "clients")
        self.assertEqual(
            service.get_table_columns(table_name="clients")["columns"][0]["column_name"],
            "gender",
        )
        self.assertEqual(
            service.get_table_columns(table_name="clients")["table_description"],
            {"short_description": "Customer accounts."},
        )
        self.assertEqual(
            service.get_column_description(table_name="clients", column_name="gender")[
                "long_description"
            ],
            "Gender code for the customer.",
        )


if __name__ == "__main__":
    unittest.main()
