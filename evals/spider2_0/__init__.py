"""Spider 2.0-Lite eval integration for DiracData.

Runs 135 SQLite-backed questions (the only cred-free subset of Spider 2.0-Lite)
against the wired data_analyst + learning agent. Everything lives in MinIO;
SQLite files are cached locally on-demand and DuckDB ATTACHes them.
"""
__version__ = "0.1.0"
