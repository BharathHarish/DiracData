TASK: sql_library_learning

You are learning reusable SQL libraries for a trustworthy data agent.

Return only valid JSON. Do not include markdown.

Requirements:

- Use successful query history, business grounding, join evidence, schema descriptions, and schema profile evidence.
- Prefer patterns actually supported by query history or explicit metric contracts.
- If business grounding already provides governed metric SQL or customer SQL templates, preserve that contract instead of inventing a new one.
- Treat customer-provided metric SQL as highest-authority truth for that metric.
- Produce reusable parameterized SQL snippets instead of long prose.
- Prefer one compact reusable library entry per business intent over many near-duplicates.
- Use actual table and column names exactly as given in the evidence.
- Keep output compact and useful for a small model at answer time.
- Do not invent tables, columns, joins, or SQL patterns not supported by evidence.
- If query history and business grounding disagree, prefer business grounding and note the evidence.
- Every entry should be useful for SQL construction at answer time.

JSON shape:

{
  "sql_library": [
    {
      "id": "sql_library:<stable_snake_case>",
      "kind": "pattern|metric_contract|template",
      "name": "short reusable name",
      "query_count": 1,
      "fact_table": "table_name",
      "tables": ["table_name"],
      "metrics": ["metric_id_or_name"],
      "dimension_columns": ["table.column"],
      "filter_columns": ["table.column"],
      "required_joins": ["table.column = table.column"],
      "avoid_joins": ["table.column = table.column"],
      "compact_contract": {
        "fact_table": "table_name",
        "tables": ["table_name"],
        "metrics": ["metric_id_or_name"],
        "dimension_columns": ["table.column"],
        "filter_columns": ["table.column"],
        "required_joins": ["table.column = table.column"],
        "avoid_joins": ["table.column = table.column"]
      },
      "parameters": ["start_time", "end_time"],
      "sql": "SELECT ...",
      "rules": ["short SQL authoring rules"],
      "metric_contract": {},
      "evidence": ["business_grounding", "query_history"],
      "confidence": "high|medium|low|needs_review"
    }
  ]
}

Output guidance:

- `sql_library` should be the only library artifact family.
- Each entry should combine the useful parts of pattern, template, and metric-contract learning.
- Prefer entries that are directly reusable during SQL construction.
- If a business metric has a formal SQL contract, prefer `confidence = high`.

Learning evidence:

```json
{{sql_library_learning_context_json}}
```
