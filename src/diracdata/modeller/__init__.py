"""AI Data Modeller — proposes new gold materialisations by mining query_history.

Long-running agent. Reads lake/fintech/{lineage.json,query_history,silver,gold,raw} +
its own state under lake/fintech/modeller/. Writes proposal JSONs and never mutates
the harness substrate. See DESIGN.md.
"""
__version__ = "0.1.0"
