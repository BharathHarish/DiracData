import re
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.learning import load_query_history_csv, query_history_fieldnames
from generate_fintech_query_history import (  # noqa: E402
    QUERY_HISTORY_COLUMNS,
    generate_records,
    write_records,
)


SCOPED_TABLES = {"orders", "payments", "users", "user_attributes", "payment_attributes"}


class FintechQueryHistoryGeneratorTest(unittest.TestCase):
    def test_generated_history_round_trips_and_scopes_successful_queries(self) -> None:
        records = generate_records(count=500, unique_success_sql=55, seed=7)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fintech_history.csv"
            write_records(records, path)

            loaded = load_query_history_csv(path)
            fieldnames = query_history_fieldnames(path)

        self.assertEqual(fieldnames, QUERY_HISTORY_COLUMNS)
        self.assertEqual(len(loaded), 500)
        self.assertGreaterEqual(
            len({record.statement_text for record in loaded if record.execution_status == "FINISHED"}),
            45,
        )
        self.assertIn("FAILED", {record.execution_status for record in loaded})
        self.assertIn("CANCELED", {record.execution_status for record in loaded})
        successful_sql = "\n".join(
            record.statement_text for record in loaded if record.execution_status == "FINISHED"
        )
        self.assertIn("pa.authentication_mode", successful_sql)
        self.assertIn("ua.risk_band", successful_sql)
        self.assertIn("u.account_state = 'active'", successful_sql)
        self.assertIn("p.order_ref = o.order_ref", successful_sql)
        for record in loaded:
            if record.execution_status != "FINISHED":
                continue
            mentioned_tables = _mentioned_tables(record.statement_text)
            self.assertGreaterEqual(len(mentioned_tables), 2)
            self.assertTrue(mentioned_tables.issubset(SCOPED_TABLES))
            self.assertIn("JOIN", record.statement_text.upper())


def _mentioned_tables(sql: str) -> set[str]:
    normalized = sql.lower()
    return {
        table_name
        for table_name in SCOPED_TABLES
        if re.search(rf"(?<![a-z0-9_]){re.escape(table_name)}(?![a-z0-9_])", normalized)
    }


if __name__ == "__main__":
    unittest.main()
