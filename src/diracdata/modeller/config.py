"""Modeller runtime config — SAFETY BUDGETS ONLY.

DELIBERATELY MINIMAL. Anything that looks like a judgement (min_pattern_runs,
min_saving_multiple, min_confidence, anti_churn_days) has been removed —
those are agent decisions, not config. If you feel tempted to add a threshold
here, ask instead: "shouldn't the agent decide this?"

What lives here:
  - How to reach MinIO (endpoint, keys, bucket, prefix)
  - Where the modeller's own outputs live (proposals/state/experiences/audit)
  - HARD safety budgets (wall-clock, tokens, per-query kill)
  - Which chat model to use

Nothing that shapes what the agent proposes.
"""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModellerConfig:
    # ---------- storage (MinIO the harness writes to) ----------
    s3_endpoint: str = os.getenv("DIRACDATA_S3_ENDPOINT_URL", "http://localhost:9000")
    s3_key:      str = os.getenv("DIRACDATA_AWS_ACCESS_KEY_ID", "minioadmin")
    s3_secret:   str = os.getenv("DIRACDATA_AWS_SECRET_ACCESS_KEY", "minioadmin")
    bucket:      str = os.getenv("DIRACDATA_LAKE_BUCKET", "lake")
    root_prefix: str = os.getenv("DIRACDATA_MODELLER_ROOT", "fintech")

    # ---------- modeller output paths ----------
    modeller_prefix:      str = "modeller"
    proposals_subprefix:  str = "proposals"
    state_subprefix:      str = "state"
    experiences_subkey:   str = "experiences.md"
    audit_subprefix:      str = "audit"
    checkpoints_subprefix: str = "checkpoints"

    # ---------- SAFETY BUDGETS (seat belts, not judgement) ----------
    # A round dies if these are hit. The agent doesn't decide these.
    max_run_tokens:     int   = int(os.getenv("DIRACDATA_MODELLER_MAX_TOKENS",   "400000"))
    max_run_seconds:    int   = int(os.getenv("DIRACDATA_MODELLER_MAX_SECONDS",  "1200"))    # 20 min
    max_query_seconds:  int   = int(os.getenv("DIRACDATA_MODELLER_MAX_QUERY_S",  "30"))
    max_query_scan_gb:  float = float(os.getenv("DIRACDATA_MODELLER_MAX_SCAN_GB","2.0"))
    max_proposals_per_run: int = int(os.getenv("DIRACDATA_MODELLER_MAX_PROPS",  "5"))       # blast-radius cap
    max_react_steps:    int   = int(os.getenv("DIRACDATA_MODELLER_MAX_STEPS",   "80"))       # avoid runaway ReAct
    tool_result_cap_chars: int = int(os.getenv("DIRACDATA_MODELLER_TOOL_CAP",  "4000"))     # per tool result

    # ---------- LLM ----------
    chat_model_profile: str = os.getenv("DIRACDATA_MODELLER_MODEL", "fireworks_deepseek_v4_flash")

    # ---------- trigger ----------
    # NOT judgement — just plumbing for the outer scheduler
    poll_interval_seconds: int = int(os.getenv("DIRACDATA_MODELLER_POLL_S", "300"))    # 5 min

    # ---------- derived path helpers ----------
    @property
    def lineage_key(self) -> str:          return f"{self.root_prefix}/lineage.json"
    @property
    def query_history_prefix(self) -> str: return f"{self.root_prefix}/query_history/"
    @property
    def raw_prefix(self) -> str:           return f"{self.root_prefix}/raw/"
    @property
    def silver_prefix(self) -> str:        return f"{self.root_prefix}/silver/"
    @property
    def gold_prefix(self) -> str:          return f"{self.root_prefix}/gold/"
    @property
    def modeller_root(self) -> str:        return f"{self.root_prefix}/{self.modeller_prefix}"
    @property
    def proposals_prefix(self) -> str:     return f"{self.modeller_root}/{self.proposals_subprefix}/"
    @property
    def state_prefix(self) -> str:         return f"{self.modeller_root}/{self.state_subprefix}/"
    @property
    def experiences_key(self) -> str:      return f"{self.modeller_root}/{self.experiences_subkey}"
    @property
    def audit_prefix(self) -> str:         return f"{self.modeller_root}/{self.audit_subprefix}/"
    @property
    def checkpoints_prefix(self) -> str:   return f"{self.modeller_root}/{self.checkpoints_subprefix}/"


def load_config() -> ModellerConfig:
    """Loads .env if present, returns frozen config."""
    env_file = os.getenv("DIRACDATA_ENV_FILE", ".env")
    if os.path.exists(env_file):
        for ln in open(env_file):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    return ModellerConfig()
