TARGET DIALECT = DuckDB. Use DuckDB syntax exactly:
- Lists/arrays are 1-INDEXED: list[1] is the FIRST element (not list[0]); list[-1] is the last.
- Dates: date_trunc('month', d), extract('year' FROM d) / date_part('year', d), date_diff('day', a, b), d + INTERVAL 1 DAY, strptime/strftime for text<->date, current_date, year(d).
- Split/access: string_split(s,','), a list you then index 1-based.
If unsure a function exists in DuckDB, probe it with a tiny run_sql before relying on it.
