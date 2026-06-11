import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.learning.schema_ast import SchemaASTBuilder


class SchemaASTLearningTests(unittest.TestCase):
    def test_ast_compiles_hierarchy_and_library_links(self):
        schema_graph = {
            "run_id": "graph",
            "nodes": [
                {
                    "id": "schema:fintech.analytics.main",
                    "kind": "domain",
                    "name": "root",
                    "path": ["fintech", "analytics", "main"],
                    "metadata": {"node_role": "schema_root"},
                },
                {
                    "id": "domain:payments",
                    "kind": "domain",
                    "name": "Payments",
                    "description": "Payment domain.",
                    "path": ["fintech", "analytics", "main", "payments"],
                },
                {
                    "id": "entity:payment_attempt",
                    "kind": "entity",
                    "name": "Payment Attempt",
                    "path": ["fintech", "analytics", "main", "payments", "payment_attempt"],
                },
                {
                    "id": "table:payments",
                    "kind": "table",
                    "name": "payments",
                    "sql_ref": "payments",
                    "grain": "one row per payment attempt",
                    "path": [
                        "fintech",
                        "analytics",
                        "main",
                        "payments",
                        "payment_attempt",
                        "payments",
                    ],
                },
                {
                    "id": "column:payments.payment_status",
                    "kind": "column",
                    "name": "payment_status",
                    "sql_ref": "payments.payment_status",
                    "metadata": {"role": "status"},
                    "path": [
                        "fintech",
                        "analytics",
                        "main",
                        "payments",
                        "payment_attempt",
                        "payments",
                        "payment_status",
                    ],
                },
            ],
            "indexes": {
                "children_by_node": {
                    "schema:fintech.analytics.main": ["domain:payments"],
                    "domain:payments": ["entity:payment_attempt"],
                    "entity:payment_attempt": ["table:payments"],
                    "table:payments": ["column:payments.payment_status"],
                }
            },
        }
        sql_library = {
            "run_id": "library",
            "entries": {
                "history:psr": {
                    "tables": ["payments"],
                    "columns": ["payments.payment_status"],
                    "sql": "SELECT ...",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = SchemaASTBuilder().build(
                schema_graph=schema_graph,
                sql_library=sql_library,
                catalog="fintech",
                database="analytics",
                schema="main",
                run_id="ast",
                output_dir=Path(tmp),
            )

        domain = result.document["domains"][0]
        table = domain["entities"][0]["tables"][0]
        column = table["columns"][0]

        self.assertEqual(domain["id"], "domain:payments")
        self.assertEqual(table["grain"], "one row per payment attempt")
        self.assertEqual(column["role"], "status")
        self.assertEqual(column["sql_library_ids"], ["history:psr"])
        self.assertIn("history:psr", domain["sql_library_ids"])
        json.dumps(result.document)


if __name__ == "__main__":
    unittest.main()
