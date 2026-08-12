# Spider 2.0-Lite eval — 135 SQLite instances, all local

This folder wires Spider 2.0-Lite's cred-free SQLite subset (135 questions, `local*` prefix)
into the DiracData learning + query agents. Everything lives in MinIO — no data files in the repo.

## What it is

Spider 2.0 (ICLR 2025 Oral, Yale + xlang-ai) is the enterprise-scale text-to-SQL benchmark that
replaced Spider 1.0. Of its 547 Spider 2.0-Lite questions, **135 run against local SQLite** (the
others need BigQuery or Snowflake credentials).

- SOTA on Spider 2.0-Lite (Aug 2026): **~60%** exec accuracy (ProSPy w/ Claude Opus 4.5), ~41% w/ DeepSeek V3.2
- Our target for the first pass: prove the pipeline works end-to-end + get a defensible baseline number

## Layout

```
evals/spider2.0/
  README.md          — this file
  __init__.py
  store.py           — SpiderStore: MinIO abstraction (manifest, docs, gold, predictions)
  grader.py          — vendored execution-accuracy grader (SQLite-only, no google.cloud dep)
  scripts/
    bootstrap.py     — one-shot: clone repo, upload text artifacts + SQLite bundle to MinIO
    verify_setup.py  — smoke test: run 1 gold SQL through DuckDB+SQLite, grade it
  outputs/           — gitignored: run logs, per-question CSVs (local scratch only)
  sqlite_cache/      — gitignored: local SQLite cache (auto-populated by SpiderStore on demand)
```

## MinIO layout (`s3://<bucket>/spider2/`)

```
manifest.jsonl                        — every instance (547 rows; we filter to local* on read)
gold/eval_index.jsonl                 — per-instance grader metadata (condition_cols, ignore_order, temporal)
gold/sql/local*.sql                   — gold SQL (135 files)
gold/csv/local*_[a-z].csv             — gold result CSVs (variants — grader accepts any match)
docs/*.md                             — external_knowledge markdown docs (~800 files)
sqlite/<db_id>.sqlite                 — SQLite database blobs (fetched on-demand to sqlite_cache/)
predictions/<run_id>/<instance_id>.csv — our predictions per run
results/<run_id>.json                 — grader output per run
```

## Bootstrap (one-time)

```bash
# 1. Upload text artifacts (clones Spider2 into a scratch dir, uploads jsonl+docs+gold to MinIO)
PYTHONPATH=. .venv/bin/python -m evals.spider2_0.scripts.bootstrap

# 2. Download the SQLite bundle (435 MB, one-time manual step — Google Drive blocks scripts)
#    Open in browser: https://drive.google.com/uc?id=1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG
#    Click "Download anyway" when Drive warns about virus scan
#    Save to: /private/tmp/spider2_bootstrap/local_sqlite.zip

# 3. Upload the SQLite bundle to MinIO
PYTHONPATH=. .venv/bin/python -m evals.spider2_0.scripts.bootstrap --only-sqlite

# 4. Smoke test — pick a random local* instance, run its gold SQL, verify grader agrees
PYTHONPATH=. .venv/bin/python -m evals.spider2_0.scripts.verify_setup
```

## Engine: DuckDB ATTACH SQLite (no new engine)

We keep DuckDB as the query engine. SQLite files are attached at query time via
`ATTACH 'file.sqlite' AS spider_db (TYPE SQLITE)`. SQLite blobs are cached locally in
`sqlite_cache/` (gitignored) on first fetch, then reused.

## What comes next

- **Learning agent adapter** (phase B): compile a per-DB semantic model from SQLite catalog +
  external_knowledge docs. Fabric lands in `diracdata://fabric/spider2_<db_id>/`.
- **Query agent runner** (phase C): given an instance_id, run `data_analyst`, execute the final SQL
  against SQLite, write CSV to `predictions/<run_id>/<instance_id>.csv`.
- **Grader batch run** (phase D): `grade_run(store, run_id)` scores + writes `results/<run_id>.json`.

Sources:
- [xlang-ai/Spider2 (official repo)](https://github.com/xlang-ai/Spider2)
- [Spider 2.0 site](https://spider2-sql.github.io/)
