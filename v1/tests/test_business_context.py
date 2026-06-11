import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.learning import BusinessContext


class BusinessContextTest(unittest.TestCase):
    def test_loads_structured_business_context_from_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "business_context.json"
            path.write_text(
                json.dumps(
                    {
                        "text": "Commerce analytics pod.",
                        "table_descriptions": {"orders": "Customer purchase facts."},
                        "column_descriptions": {"orders": {"revenue": "Money from purchases."}},
                        "glossary": {"customer": "A shopper or account."},
                    }
                ),
                encoding="utf-8",
            )

            context = BusinessContext.from_json_file(path)

        self.assertEqual(context.text, "Commerce analytics pod.")
        self.assertEqual(context.table_descriptions["orders"], "Customer purchase facts.")
        self.assertEqual(context.column_descriptions["orders"]["revenue"], "Money from purchases.")
        self.assertEqual(context.glossary["customer"], "A shopper or account.")


if __name__ == "__main__":
    unittest.main()
