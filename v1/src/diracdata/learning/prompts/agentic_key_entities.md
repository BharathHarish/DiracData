TASK: key_entities_learning

You are learning the key business entities in a data schema for a trustworthy data agent.

Return only valid JSON. Do not include markdown.

Requirements:

- Use only the provided evidence.
- Stay column-level and business-friendly.
- Prefer entities that will help an agent map natural-language questions to the right columns.
- Include primary columns that best identify the entity and supporting columns that explain or slice it.
- Mention uncertainty when the evidence is weak.
- Do not invent entities, columns, or business terms.

JSON shape:

{
  "key_entities": [
    {
      "id": "entity:<stable_snake_case>",
      "name": "...",
      "description": "...",
      "primary_columns": ["table.column"],
      "supporting_columns": ["table.column"],
      "business_terms": ["..."],
      "evidence": ["schema_description", "business_grounding", "profile_values", "query_history"],
      "confidence": "high|medium|low|needs_review"
    }
  ]
}

Learning evidence:

```json
{{key_entities_learning_context_json}}
```
