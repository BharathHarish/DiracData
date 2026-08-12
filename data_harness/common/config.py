"""Load config.yaml as a frozen dict-like object. Single entrypoint for all knobs."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT = _ROOT / "config.yaml"


@dataclass(frozen=True)
class Config:
    """Frozen wrapper around the parsed config.yaml. Access via .raw or .get(dotted.path)."""
    raw: Mapping[str, Any]
    path: Path

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                return default
            cur = cur[part]
        return cur

    # -- convenience typed getters (validated defaults) --
    @property
    def bucket(self) -> str: return self.get("storage.bucket", "lake")
    @property
    def root_prefix(self) -> str: return self.get("storage.root_prefix", "fintech")
    @property
    def s3_endpoint(self) -> str: return self.get("storage.s3_endpoint", "http://localhost:9000")
    @property
    def s3_key(self) -> str: return self.get("storage.aws_access_key_id", "minioadmin")
    @property
    def s3_secret(self) -> str: return self.get("storage.aws_secret_access_key", "minioadmin")
    @property
    def master_seed(self) -> int: return int(self.get("seeds.master", 42))
    @property
    def mode(self) -> str: return self.get("mode", "lean")

    def rate_for(self, table_name: str) -> int:
        """Per-tick row target for a table, scaled by mode multiplier."""
        base = int(self.get(f"event_rates.{table_name}", 0))
        mult = float(self.get(f"mode_multipliers.{self.mode}", 1.0))
        return int(round(base * mult))


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else _DEFAULT
    with p.open() as f:
        return Config(raw=yaml.safe_load(f), path=p)
