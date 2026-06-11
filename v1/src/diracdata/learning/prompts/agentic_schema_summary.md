TASK: schema_summary_learning

You are learning a concise business summary for a data schema used by a trustworthy data agent.

Return only valid JSON. Do not include markdown.

Requirements:

- Use only the provided evidence.
- Write business-friendly language.
- Keep `short_summary` under 50 words.
- Keep `long_summary` under 300 words.
- Focus on what the schema represents, what business process it tracks, and what kinds of questions it can answer.
- Do not invent entities, joins, or metrics.

JSON shape:

{
  "schema_summary": {
    "short_summary": "...",
    "long_summary": "..."
  }
}

Learning evidence:

```json
{{schema_summary_learning_context_json}}
```
