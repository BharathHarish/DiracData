TASK: nuance_learning

You are learning semantic guardrails for a trustworthy data agent.

Return only valid JSON. Do not include markdown.

Requirements:

- Learn only confounders and invariants.
- Use schema descriptions, profile evidence, business grounding, join evidence, and SQL libraries.
- Confounders should capture similar naming, overlapping meanings, or business phrases that could map to the wrong column.
- Invariants should be short do/do-not rules that prevent likely semantic mistakes.
- Prioritize the highest-value failure modes for NL2SQL:
  - same-name columns across tables
  - similar business concepts with different semantics
  - time-column confusion
  - metric grain confusion
  - numerator/denominator mistakes
  - fact-vs-dimension value confusion
  - wrong join path despite matching keys
- Prefer fewer, sharper confounders over broad or generic observations.
- Prefer invariants that are directly actionable by an answering agent.
- If business grounding already defines a metric/default clearly, do not merely restate it in vague prose; turn it into a crisp operational rule.
- If query history shows a risky alternative, capture it explicitly in either `resolution_policy` or `avoid_joins`.
- Use exact table and column names from evidence.
- Keep everything evidence-based and compact.
- Do not invent business logic that is not supported by evidence.
- Avoid duplicate invariants that say the same thing with minor wording changes.
- When in doubt, prefer the invariant that is most useful at answer time.

JSON shape:

{
  "confounders": [
    {
      "id": "confounder:<stable_snake_case>",
      "artifact_type": "confounder",
      "confounder_type": "business_term|exact_column_name|similar_column_name|value_overlap|metric_grain",
      "term": "...",
      "columns": ["table.column"],
      "reason": "...",
      "resolution_policy": "...",
      "evidence": ["schema_description", "profile_values", "business_grounding", "query_history", "join_evidence"],
      "confidence": "high|medium|low|needs_review"
    }
  ],
  "invariants": [
    {
      "id": "invariant:<stable_snake_case>",
      "invariant_type": "business_default|metric_contract|join_pattern|confounder_resolution|time_semantics|grain",
      "rule": "...",
      "columns": ["table.column"],
      "required_joins": ["table.column = table.column"],
      "avoid_joins": ["table.column = table.column"],
      "metrics": ["metric_id_or_name"],
      "source": "business_grounding|query_history_library|schema_profile|agentic_learning",
      "evidence": ["schema_description", "business_grounding", "query_history", "join_evidence"],
      "confidence": "high|medium|low|needs_review",
      "approval_status": "candidate"
    }
  ]
}

Output guidance:

- A good `confounder` should explain exactly what an agent may confuse and how to resolve it.
- A good `resolution_policy` should name the right column, join path, or business interpretation explicitly.
- A good `invariant` should be directly usable as a guardrail during SQL construction.
- Prefer invariants that mention required joins, forbidden filters, metric thresholds, or the correct time column when evidence supports them.

Learning evidence:

```json
{{nuance_learning_context_json}}
```
