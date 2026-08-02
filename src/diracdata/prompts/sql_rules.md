GOLDEN RULES OF CORRECT SQL (a wrong query runs clean and returns a plausible-but-WRONG number --
so treat each of these as a check you must actively clear, not assume):

1. GRAIN & FAN-OUT. Know each table's grain (one row per WHAT). A one-to-many join BEFORE aggregating
   inflates SUM/COUNT -- aggregate at the right grain or COUNT(DISTINCT key), and confirm a join did
   not multiply rows (row count before vs after).
2. NULLS -- the silent killer. For EVERY nullable column you join/filter/segment on:
   - NULL never equals anything: a join on a null key SILENTLY DROPS those rows; `NOT IN (... NULL)`
     returns nothing (use NOT EXISTS); `= NULL` / `<> NULL` are UNKNOWN, never true.
   - Measures: SUM/AVG skip NULLs; COUNT(col) != COUNT(*).
   - Decide what NULL MEANS here (missing vs zero vs not-applicable) from the column's description,
     and tailor the SQL to it (COALESCE, an explicit bucket, or an intentional exclusion you state).
3. EXHAUSTIVE, MUTUALLY-EXCLUSIVE BUCKETS (MECE). A CASE/segmentation must cover EVERY row -- including
   NULLs and out-of-range values -- or rows fall into no bucket. When you present a TOTAL plus a
   BREAKDOWN, the parts MUST sum to the whole; if they don't, find and NAME the residual (e.g. rows
   with no identifiable entity), never hide it.
4. JOINS. Verify the key and direction (fact->dimension) with join_path. Check orphan %: a join that
   returns 0 rows, or far fewer than expected, means a WRONG KEY or a referential gap -- investigate,
   don't ship. INNER silently drops unmatched rows (undercount); LEFT keeps them (watch introduced NULLs).
5. FILTERS. Profile the REAL distinct values before filtering (casing, codes, whitespace, an
   Unknown/NULL bucket) so a predicate can't silently match ZERO. Sanity-check selectivity -- did the
   count collapse to 0 or jump to something implausible?
6. TIME & PERIODS. Nail boundaries: inclusive vs exclusive, the exact day-key range, calendar vs
   fiscal year, and a "current" attribute vs the value "at the time of the event".
7. SEMANTIC BINDING. Bind business terms/metrics to their DEFINED SQL (`define`), never a convenient
   look-alike column; use the defined formula and decompose down its depends_on.
8. AGGREGATION MATH. Choose COUNT(*) vs COUNT(DISTINCT) vs COUNT(col) deliberately; guard ratio
   DENOMINATORS against 0; keep numerator and denominator on the SAME cohort/grain; don't average an
   average; round only at the end; dedupe the entity population before counting it.
9. SANITY OF THE RESULT. Is the magnitude plausible (against row counts, a known total, the prior
   period)? An empty result, a lone 0, or a surprise NULL cell is a SIGNAL to investigate -- not an answer.
10. DIALECT. Write for the TARGET engine's SQL dialect EXACTLY. Two things silently break across
    engines: (a) date/time functions -- truncation, extraction, date diffs, intervals, text<->date
    casting -- have different names/signatures per engine; (b) ARRAY/LIST indexing base is 0- vs
    1-based per engine, so the same subscript grabs the wrong element. The target engine's specifics
    are stated below; if unsure a function exists or how it behaves here, probe it with a tiny run_sql
    before trusting it.
