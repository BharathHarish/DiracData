You are helping build trustworthy semantic metadata for a data agent.

Use the provided business context and schema/profile evidence to generate descriptions for each table and column.

The descriptions will later be used for semantic search against natural-language business questions from non-technical users. Optimize for meaning, business concepts, synonyms, intent matching, and human understanding.

Do not merely translate technical table or column names. The table/column names are identifiers, not the description. Use them only to anchor the JSON keys.

The JSON evidence contains one or more active tables under `tables`. It may also contain an `available_tables` index so you can understand the surrounding business pod. Wide schemas are split into `description_batch` chunks. Generate descriptions only for active tables and active columns in `tables`; do not describe tables that appear only in `available_tables`, and do not describe columns outside the active `description_batch`.

Requirements:

- Return only valid JSON.
- Do not invent facts not supported by business context or profile evidence.
- Keep every `short_description` under 50 words.
- Keep every `long_description` under 300 words.
- Use business-friendly language.
- Mention uncertainty when the evidence is weak.
- Prefer natural phrases a business user might search for, using only concepts supported by the provided evidence.
- Avoid raw technical phrasing such as "integer surrogate key" unless the business meaning is genuinely unavailable.
- Use user-provided business descriptions, glossary, table descriptions, column descriptions, metric definitions, SQL templates, default policies, and ground-truth SQL examples as the strongest signal.
- If `business_grounding` is present, use it to connect schema fields to business concepts, metric names, glossary terms, synonyms, default interpretations, and SQL idioms.
- When metric or SQL-template evidence maps a column to a business concept, include the business meaning in the description using natural language. Do not copy long SQL into descriptions.
- If a column appears to be an identifier or join key, describe the business entity it links to when evidence supports it; otherwise say it appears to be an internal identifier.
- If evidence is insufficient, say that the meaning is uncertain instead of guessing.
- Include every active table and every active column exactly once in the JSON output.
- Write for business users first. Technical evidence such as data type, null rate, row count, and values is only evidence; it is not the description.
- Use natural-language synonyms only when they are supported by evidence from business context, profiling, table relationships, values, or metric definitions.
- Do not expand abbreviations unless the business context, values, or surrounding table evidence supports the expansion.

JSON shape:

```json
{
  "tables": {
    "table_name": {
      "short_description": "...",
      "long_description": "..."
    }
  },
  "columns": {
    "table_name": {
      "column_name": {
        "short_description": "...",
        "long_description": "..."
      }
    }
  }
}
```

Business context and profile evidence:

```json
{{learning_context_json}}
```
