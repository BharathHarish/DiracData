from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SchemaAgnosticFrameworkTest(unittest.TestCase):
    def test_agent_prompts_middleware_and_tools_do_not_contain_fixture_vocabulary(self) -> None:
        production_files = [
            *sorted((ROOT / "src/diracdata/agents/prompts").glob("*.md")),
            ROOT / "src/diracdata/agents/middleware.py",
            ROOT / "src/diracdata/agents/analyst_compiler.py",
            ROOT / "src/diracdata/agents/data_analyst_agent.py",
            ROOT / "src/diracdata/retrieval/candidate_search.py",
            *sorted((ROOT / "src/diracdata/tools").glob("*.py")),
        ]
        blocked_terms = [
            "fintech",
            "retail",
            "tpcds",
            "razorpay",
            "commerce_pod",
            "fintech_pod",
            "retail_pod",
            "clients",
            "client_profiles",
            "online_purchases",
            "mail_order",
            "store_purchases",
            "merchandise",
            "marketing_campaign",
            "calendar_days",
            "payments",
            "payment_attributes",
            "payment_status",
            "payment_time",
            "orders",
            "checkout",
            "karnataka",
            "arizona",
            "california",
            "jewelry",
            "upi",
            "psr",
            "tpv",
            "mau",
            "dau",
            "retained user",
            "churned user",
        ]

        violations = []
        for path in production_files:
            text = path.read_text(encoding="utf-8").lower()
            for term in blocked_terms:
                if _contains_term(text, term):
                    violations.append(f"{path.relative_to(ROOT)} contains fixture term {term!r}")

        self.assertEqual(violations, [])

    def test_agent_sql_planner_prompt_uses_configured_dialect(self) -> None:
        prompt = (ROOT / "src/diracdata/agents/prompts/SQL_PLAN_PROMPT_V1.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("configured dialect", prompt)
        self.assertIn("sql_dialect", prompt)
        self.assertNotIn("DuckDB SQL", prompt)


def _contains_term(text: str, term: str) -> bool:
    if "_" in term or " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None
