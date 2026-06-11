import unittest

from diracdata_v2.context import (
    ContextSlice,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    ReviewStatus,
    SqlLibraryEntry,
    TrustLevel,
)


class ContextContractTests(unittest.TestCase):
    def test_column_node_carries_only_sql_affecting_context(self) -> None:
        node = GraphNode(
            id="column:user_attributes.kyc_status",
            kind=NodeKind.COLUMN,
            name="kyc_status",
            path=("fintech", "users", "user_attributes", "kyc_status"),
            description="KYC status used for user verification grouping.",
            sql_ref="user_attributes.kyc_status",
            aliases=("verification status",),
            allowed_values=("verified", "pending", "rejected"),
            null_meaning="unknown or not submitted",
            sql_guidance="When grouping, preserve unknowns explicitly if NULLs exist.",
        )

        payload = node.to_dict()

        self.assertEqual(payload["kind"], "column")
        self.assertEqual(payload["sql_ref"], "user_attributes.kyc_status")
        self.assertEqual(payload["allowed_values"], ["verified", "pending", "rejected"])
        self.assertIn("null_meaning", payload)

    def test_sql_library_entry_is_the_single_pattern_contract(self) -> None:
        entry = SqlLibraryEntry(
            id="sql_library:psr_core",
            domain="payments",
            intent_terms=("psr", "payment success rate"),
            sql="COUNT_IF(payment_status = 'SUCCESS')::DOUBLE / COUNT(*)",
            parameters=("start_time", "end_time"),
            required_nodes=("column:payments.payment_status",),
            rules=("Do not pre-filter successful payments before computing denominator.",),
            source=TrustLevel.USER_PROVIDED,
            review_status=ReviewStatus.APPROVED,
        )

        payload = entry.to_dict()

        self.assertEqual(payload["id"], "sql_library:psr_core")
        self.assertEqual(payload["source"], "user_provided")
        self.assertEqual(payload["review_status"], "approved")
        self.assertEqual(
            payload["rules"],
            ["Do not pre-filter successful payments before computing denominator."],
        )

    def test_context_slice_serializes_graph_and_library_together(self) -> None:
        node = GraphNode(
            id="table:payments",
            kind=NodeKind.TABLE,
            name="payments",
            path=("fintech", "payments"),
            grain="one row per payment attempt",
        )
        edge = GraphEdge(
            id="join:payments.user_ref.users.user_ref",
            kind=EdgeKind.JOINS,
            from_node="column:payments.user_ref",
            to_node="column:users.user_ref",
            sql_condition="payments.user_ref = users.user_ref",
            relationship="many_to_one",
            grain_effect="preserves payment-attempt grain",
            source=TrustLevel.QUERY_HISTORY,
            confidence="high",
        )
        library_entry = SqlLibraryEntry(
            id="sql_library:payments_by_user_segment",
            domain="payments",
            intent_terms=("payments by user segment",),
            sql="SELECT users.merchant_type, COUNT(*) FROM payments JOIN users ON ...",
            required_edges=(edge.id,),
            source=TrustLevel.QUERY_HISTORY,
        )

        context = ContextSlice(
            question="show payment attempts by merchant type",
            selected_nodes=(node,),
            join_edges=(edge,),
            sql_library=(library_entry,),
        )

        payload = context.to_dict()

        self.assertEqual(
            payload["selected_nodes"][0]["grain"], "one row per payment attempt"
        )
        self.assertEqual(
            payload["join_edges"][0]["grain_effect"],
            "preserves payment-attempt grain",
        )
        self.assertEqual(
            payload["sql_library"][0]["id"],
            "sql_library:payments_by_user_segment",
        )


if __name__ == "__main__":
    unittest.main()
