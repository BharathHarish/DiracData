import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "v2" / "evals" / "Goldset_retail_queries.csv"
BENCHMARK_PATH = ROOT / "v2" / "evals" / "Benchmark_retail_customer_history.csv"
METADATA_PATH = ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json"


class RetailEvalSetTests(unittest.TestCase):
    def test_generated_gold_and_benchmark_sets_have_expected_size_and_coverage(self):
        gold = _read_rows(GOLD_PATH)
        benchmark = _read_rows(BENCHMARK_PATH)

        self.assertEqual(len(gold), 120)
        self.assertEqual(len(benchmark), 300)
        self.assertEqual(_tables(gold), _all_tables())
        self.assertEqual(_tables(benchmark), _all_tables())
        self.assertGreaterEqual(len(_columns(gold)), 100)
        self.assertGreaterEqual(len(_columns(benchmark)), 100)
        self.assertGreaterEqual(len(_join_edges(gold)), 40)
        self.assertGreaterEqual(len(_join_edges(benchmark)), 40)

    def test_generated_sets_reference_only_known_schema_objects(self):
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        known_tables = set(metadata["tables"])
        known_columns = {
            f"{table}.{column}"
            for table, columns in metadata["columns"].items()
            for column in columns
        }

        for path in (GOLD_PATH, BENCHMARK_PATH):
            for row in _read_rows(path):
                self.assertTrue(row["nl_query"])
                self.assertTrue(row["sql"])
                for table in _split(row["tables_used"]):
                    self.assertIn(table, known_tables, f"{path.name}:{row}")
                for column in _split(row["columns_used"]):
                    self.assertIn(column, known_columns, f"{path.name}:{row}")
                for edge in _split(row["join_edges"]):
                    left, right = [item.strip() for item in edge.split("=", 1)]
                    self.assertIn(left, known_columns, f"{path.name}:{row}")
                    self.assertIn(right, known_columns, f"{path.name}:{row}")


def _read_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _split(value: str):
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def _tables(rows):
    output = set()
    for row in rows:
        output.update(_split(row["tables_used"]))
    return output


def _columns(rows):
    output = set()
    for row in rows:
        output.update(_split(row["columns_used"]))
    return output


def _join_edges(rows):
    output = set()
    for row in rows:
        output.update(_split(row["join_edges"]))
    return output


def _all_tables():
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    return set(metadata["tables"])


if __name__ == "__main__":
    unittest.main()
