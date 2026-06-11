import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.tools.ast_search import ASTSearchService


class ASTSearchToolTests(unittest.TestCase):
    def test_search_returns_columns_and_linked_library(self):
        schema_ast = {
            "domains": [
                {
                    "id": "domain:payments",
                    "name": "Payments",
                    "description": "Payment attempts and success rate.",
                    "sql_library_ids": ["sql:psr"],
                    "entities": [
                        {
                            "id": "entity:payment_attempt",
                            "name": "Payment Attempt",
                            "description": "One attempted payment.",
                            "sql_library_ids": ["sql:psr"],
                            "tables": [
                                {
                                    "id": "table:payments",
                                    "name": "payments",
                                    "description": "Payment attempts.",
                                    "grain": "one row per payment attempt",
                                    "sql_library_ids": ["sql:psr"],
                                    "columns": [
                                        {
                                            "id": "column:payments.payment_status",
                                            "name": "payment_status",
                                            "sql_ref": "payments.payment_status",
                                            "role": "status",
                                            "description": "Outcome used for PSR.",
                                            "aliases": ["payment outcome"],
                                            "sql_guidance": "Do not pre-filter successes for PSR denominator.",
                                            "sql_library_ids": ["sql:psr"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        sql_library = {
            "entries": {
                "sql:psr": {
                    "template": "PSR core",
                    "source": "query_history",
                    "review_status": "observed",
                    "tables": ["payments"],
                    "columns": ["payments.payment_status"],
                    "sql": "SELECT ...",
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            ast_path = Path(tmp) / "ast.json"
            lib_path = Path(tmp) / "lib.json"
            ast_path.write_text(json.dumps(schema_ast))
            lib_path.write_text(json.dumps(sql_library))
            service = ASTSearchService.from_files(schema_ast_path=ast_path, sql_library_path=lib_path)

        result = service.search(query="payment success rate", max_columns=5)

        self.assertEqual(result["matched_columns"][0]["sql_ref"], "payments.payment_status")
        self.assertEqual(result["sql_library"][0]["id"], "sql:psr")


if __name__ == "__main__":
    unittest.main()

