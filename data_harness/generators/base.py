"""BaseGenerator — seeded Faker, persistent state, one emit_tick() per subclass.

State (last-emitted counters, entity pools) is persisted to MinIO under
lake/fintech/generator_state/<domain>.json so runs are resumable.
"""
from __future__ import annotations
import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import pyarrow as pa
from faker import Faker
from data_harness.common.config import Config
from data_harness.common.minio_client import download_json, upload_json
from data_harness.common.paths import state_key, utc_now


def _seed_from(master: int, domain: str) -> int:
    h = hashlib.sha1(f"{master}:{domain}".encode()).digest()
    return int.from_bytes(h[:4], "big")


@dataclass
class GeneratorState:
    domain: str
    counters: Dict[str, int] = field(default_factory=dict)
    entity_pools: Dict[str, List[Any]] = field(default_factory=dict)
    last_tick_at: Optional[str] = None

    def next_id(self, kind: str) -> int:
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        return n

    def pool(self, name: str) -> List[Any]:
        return self.entity_pools.setdefault(name, [])

    def to_dict(self) -> Dict:
        return {"domain": self.domain, "counters": self.counters,
                "entity_pools": self.entity_pools, "last_tick_at": self.last_tick_at}

    @classmethod
    def from_dict(cls, d: Dict) -> "GeneratorState":
        return cls(domain=d["domain"], counters=d.get("counters", {}),
                   entity_pools=d.get("entity_pools", {}),
                   last_tick_at=d.get("last_tick_at"))


_SHARED_POOLS_KEY_SUFFIX = "_shared_pools"


class BaseGenerator:
    """Subclass sets `domain`, `tables`, and implements `emit_tick()`.

    emit_tick() returns {table_name: pa.Table}. The runner buffers + writes.

    Cross-domain FK pools (user_ids, merchant_ids, order_ids, etc.) live in a
    single SHARED state file so downstream domains within the same tick can see
    what earlier domains just published. Per-domain state holds only counters
    (device_%d etc) that are truly domain-local.
    """
    domain: str = ""
    tables: List[str] = []

    def __init__(self, cfg: Config, s3):
        self.cfg = cfg
        self.s3 = s3
        seed = _seed_from(cfg.master_seed, self.domain)
        self.rng = random.Random(seed)
        self.faker = Faker(["en_IN"])  # India locale (§Decision #3)
        Faker.seed(seed)
        # Load own state (counters + own metadata)
        stored = download_json(s3, cfg.bucket, state_key(cfg, self.domain), default=None)
        self.state = GeneratorState.from_dict(stored) if stored else GeneratorState(domain=self.domain)
        # Load shared cross-domain pools — merge into self.state.entity_pools by reference
        shared = download_json(s3, cfg.bucket, state_key(cfg, _SHARED_POOLS_KEY_SUFFIX), default=None) or {}
        for k, v in shared.items():
            if isinstance(v, list):
                self.state.entity_pools[k] = v  # shared pool overrides own copy

    def save_state(self) -> None:
        """Persist own state + push cross-domain pools to the shared store."""
        self.state.last_tick_at = utc_now().isoformat()
        # Cross-domain pool keys (those ending in _ids) are shared; others stay per-domain.
        shared_pools = {k: v for k, v in self.state.entity_pools.items() if k.endswith("_ids")}
        own_pools    = {k: v for k, v in self.state.entity_pools.items() if not k.endswith("_ids")}
        # Merge with existing shared (in case another generator wrote something we didn't touch)
        existing = download_json(self.s3, self.cfg.bucket,
                                 state_key(self.cfg, _SHARED_POOLS_KEY_SUFFIX), default=None) or {}
        for k, v in shared_pools.items():
            existing[k] = v  # last-writer-wins per tick — fine, generators are sequential
        upload_json(self.s3, self.cfg.bucket,
                    state_key(self.cfg, _SHARED_POOLS_KEY_SUFFIX), existing)
        # Own state without the shared pools
        own_state = GeneratorState(domain=self.state.domain,
                                   counters=self.state.counters,
                                   entity_pools=own_pools,
                                   last_tick_at=self.state.last_tick_at)
        upload_json(self.s3, self.cfg.bucket, state_key(self.cfg, self.domain),
                    own_state.to_dict())

    # -- utilities every generator uses --

    def rate(self, table_name: str) -> int:
        return self.cfg.rate_for(table_name)

    def now(self) -> datetime:
        return utc_now()

    def event_ts(self, base: datetime | None = None, spread_seconds: int = 3600) -> datetime:
        """A random timestamp in the recent past, ~uniformly spread over spread_seconds ending at base."""
        base = base or self.now()
        offset = self.rng.randint(0, spread_seconds)
        return base - timedelta(seconds=offset)

    def maybe_late(self, ts: datetime) -> datetime:
        """§4: 10% of events land with _event_ts earlier than _ingest_ts (5-30 min lag)."""
        if not self.cfg.get("late_arrivals.enabled", True):
            return ts
        if self.rng.random() >= float(self.cfg.get("late_arrivals.fraction", 0.10)):
            return ts
        lag_min = self.rng.randint(int(self.cfg.get("late_arrivals.lag_minutes_min", 5)),
                                   int(self.cfg.get("late_arrivals.lag_minutes_max", 30)))
        return ts - timedelta(minutes=lag_min)

    def pick_weighted(self, choices: list, weights: list):
        return self.rng.choices(choices, weights=weights, k=1)[0]

    def pick_state(self) -> Dict:
        """Zipf-weighted Indian state per §Decision #3."""
        states = self.cfg.get("geo.states", [])
        weights = [s["weight"] for s in states]
        return self.rng.choices(states, weights=weights, k=1)[0]

    def pick_language(self) -> str:
        langs = self.cfg.get("geo.languages", [])
        weights = [l["weight"] for l in langs]
        return self.rng.choices(langs, weights=weights, k=1)[0]["code"]

    def pick_rail(self) -> str:
        rails = self.cfg.get("rails.weights", {})
        codes = list(rails.keys())
        weights = list(rails.values())
        return self.rng.choices(codes, weights=weights, k=1)[0]

    def pick_currency(self) -> str:
        mix = self.cfg.get("rails.currency_mix", {"INR": 100})
        codes = list(mix.keys())
        weights = list(mix.values())
        return self.rng.choices(codes, weights=weights, k=1)[0]

    def emit_tick(self) -> Dict[str, pa.Table]:
        raise NotImplementedError
