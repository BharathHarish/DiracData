"""checkouts domain generator — 4 tables with rich nested types (§6.3).

Tables:
  checkout_sessions      (funnel top; utm: STRUCT)
  checkout_items         (many-per-session; variant_attrs: MAP)
  checkout_events        (funnel steps; event_payload: JSON)
  checkout_abandonments  (funnel bottom; funnel_steps: LIST<STRUCT>)
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- schemas (explicit — nested types matter for the modeller) ----

_UTM_STRUCT = pa.struct([
    ("source",   pa.string()),
    ("medium",   pa.string()),
    ("campaign", pa.string()),
    ("content",  pa.string()),
])

_FUNNEL_STEP_STRUCT = pa.struct([
    ("step_name",   pa.string()),
    ("entered_at",  pa.timestamp("us", tz="UTC")),
    ("duration_ms", pa.int64()),
    ("completed",   pa.bool_()),
])


_SESSIONS_SCHEMA = pa.schema([
    ("session_id",    pa.string()),
    ("merchant_id",   pa.string()),
    ("user_id",       pa.string()),
    ("initiated_at",  pa.timestamp("us", tz="UTC")),
    ("status",        pa.string()),
    ("total_amount",  pa.float64()),
    ("utm",           _UTM_STRUCT),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_ITEMS_SCHEMA = pa.schema([
    ("item_id",       pa.string()),
    ("session_id",    pa.string()),
    ("sku",           pa.string()),
    ("qty",           pa.int32()),
    ("unit_price",    pa.float64()),
    ("variant_attrs", pa.map_(pa.string(), pa.string())),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_EVENTS_SCHEMA = pa.schema([
    ("event_id",      pa.string()),
    ("session_id",    pa.string()),
    ("event_type",    pa.string()),
    ("event_time",    pa.timestamp("us", tz="UTC")),
    ("event_payload", pa.string()),  # JSON
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_ABANDONMENTS_SCHEMA = pa.schema([
    ("session_id",    pa.string()),
    ("abandoned_at",  pa.timestamp("us", tz="UTC")),
    ("last_step",     pa.string()),
    ("funnel_steps",  pa.list_(_FUNNEL_STEP_STRUCT)),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])


_STATUSES = ["in_progress", "converted", "abandoned", "expired"]
_STATUS_WEIGHTS = [5, 50, 40, 5]

_UTM_SOURCES = ["google", "facebook", "instagram", "youtube", "direct",
                "email", "sms", "affiliate", "organic", "whatsapp"]
_UTM_MEDIUMS = ["cpc", "cpm", "social", "email", "organic", "referral",
                "push", "display"]
_UTM_CAMPAIGNS = ["diwali_sale", "monsoon_deals", "back_to_school", "flash_sale",
                  "new_user_offer", "cashback_week", "brand_awareness",
                  "retargeting_v2", "prime_time", "weekend_boost"]
_UTM_CONTENTS = ["banner_top", "video_preroll", "carousel_a", "carousel_b",
                 "sidebar_v1", "footer_cta", "hero_1", "hero_2", "listing_promo"]

_EVENT_TYPES = ["page_view", "item_add", "item_remove", "payment_start",
                "payment_success", "payment_fail"]
_EVENT_TYPE_WEIGHTS = [45, 25, 5, 12, 10, 3]

_FUNNEL_STEP_NAMES = ["cart", "address", "payment", "review", "otp", "confirm"]
_LAST_STEPS = ["address", "payment", "review", "otp"]
_LAST_STEP_WEIGHTS = [15, 55, 15, 15]

_ERROR_CODES = ["INSUFFICIENT_FUNDS", "OTP_TIMEOUT", "BANK_DECLINED",
                "RISK_BLOCK", "NETWORK_ERROR", "USER_CANCELLED", "3DS_FAILED"]


class CheckoutsGenerator(BaseGenerator):
    domain = "checkouts"
    tables = ["checkout_sessions", "checkout_items", "checkout_events",
              "checkout_abandonments"]

    # ---- helpers ----

    def _lognormal_inr(self) -> float:
        # median ~450, tail to ~50k
        mu = math.log(450.0)
        sigma = 1.15
        v = self.rng.lognormvariate(mu, sigma)
        return round(min(v, 50_000.0), 2)

    def _pick_sku(self) -> str:
        pool = self.state.pool("sku_ids")
        cap = int(self.cfg.get("entity_caps.skus", 5000))
        if pool and (len(pool) >= cap or self.rng.random() < 0.85):
            return self.rng.choice(pool)
        sku = f"sku_{self.state.next_id('sku'):06d}"
        pool.append(sku)
        if len(pool) > cap:
            self.state.entity_pools["sku_ids"] = pool[-cap:]
        return sku

    def _new_utm(self) -> dict:
        return {
            "source":   self.rng.choice(_UTM_SOURCES),
            "medium":   self.rng.choice(_UTM_MEDIUMS),
            "campaign": self.rng.choice(_UTM_CAMPAIGNS),
            "content":  self.rng.choice(_UTM_CONTENTS),
        }

    def _new_variant_attrs(self) -> dict:
        attrs: Dict[str, str] = {}
        n = self.rng.choices([0, 1, 2, 3], weights=[10, 40, 35, 15], k=1)[0]
        pool = [
            ("size",     self.rng.choice(["XS", "S", "M", "L", "XL", "XXL"])),
            ("color",    self.rng.choice(["red", "black", "white", "blue", "green", "beige"])),
            ("material", self.rng.choice(["cotton", "polyester", "denim", "leather", "silk"])),
            ("fit",      self.rng.choice(["slim", "regular", "relaxed"])),
            ("pattern",  self.rng.choice(["solid", "striped", "printed", "checked"])),
        ]
        self.rng.shuffle(pool)
        for k, v in pool[:n]:
            attrs[k] = v
        return attrs

    def _new_event_payload(self, event_type: str) -> str:
        if event_type == "page_view":
            payload = {
                "page":       self.rng.choice(["cart", "address", "payment", "review", "otp"]),
                "referrer":   self.rng.choice(["home", "search", "listing", "recommendation", "email"]),
                "load_ms":    self.rng.randint(80, 3200),
            }
        elif event_type in ("item_add", "item_remove"):
            payload = {
                "sku":      self._pick_sku(),
                "qty_delta": 1 if event_type == "item_add" else -1,
                "source":    self.rng.choice(["cart", "recommendation", "wishlist"]),
            }
        elif event_type == "payment_start":
            payload = {
                "rail":         self.pick_rail(),
                "amount":       round(self.rng.uniform(50.0, 20000.0), 2),
                "currency":     self.pick_currency(),
                "form_fields":  self.rng.randint(3, 8),
            }
        elif event_type == "payment_success":
            payload = {
                "rail":       self.pick_rail(),
                "amount":     round(self.rng.uniform(50.0, 20000.0), 2),
                "latency_ms": self.rng.randint(400, 8000),
                "click_xy":   [self.rng.randint(0, 400), self.rng.randint(0, 900)],
            }
        elif event_type == "payment_fail":
            payload = {
                "rail":       self.pick_rail(),
                "error_code": self.rng.choice(_ERROR_CODES),
                "retry":      self.rng.random() < 0.4,
                "latency_ms": self.rng.randint(200, 15000),
            }
        else:
            payload = {}
        return json.dumps(payload)

    def _new_funnel_steps(self, abandoned_at: datetime, last_step: str) -> list:
        # Sequence up through the "last_step"; last step is uncompleted.
        try:
            stop_idx = _FUNNEL_STEP_NAMES.index(last_step)
        except ValueError:
            stop_idx = 1
        steps = []
        cursor = abandoned_at - timedelta(seconds=self.rng.randint(60, 900))
        for i, name in enumerate(_FUNNEL_STEP_NAMES[: stop_idx + 1]):
            duration_ms = self.rng.randint(2_000, 120_000)
            completed = i < stop_idx
            steps.append({
                "step_name":   name,
                "entered_at":  cursor,
                "duration_ms": duration_ms,
                "completed":   completed,
            })
            cursor = cursor + timedelta(milliseconds=duration_ms)
        return steps

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        n_sessions      = self.rate("checkout_sessions")
        n_items         = self.rate("checkout_items")
        n_events        = self.rate("checkout_events")
        n_abandonments  = self.rate("checkout_abandonments")
        now = self.now()

        merchant_pool = self.state.pool("merchant_ids")
        user_pool     = self.state.pool("user_ids")
        session_pool  = self.state.pool("checkout_session_ids")
        abandoned_pool = self.state.pool("abandoned_session_ids")

        # ---- checkout_sessions ----
        sessions_rows: List[dict] = []
        for _ in range(n_sessions):
            if not merchant_pool or not user_pool:
                break
            sid = f"chk_{self.state.next_id('session'):012d}"
            session_pool.append(sid)
            max_pool = int(self.cfg.get("entity_caps.users", 50_000))
            if len(session_pool) > max_pool:
                self.state.entity_pools["checkout_session_ids"] = session_pool[-max_pool:]
                session_pool = self.state.pool("checkout_session_ids")

            initiated = self.event_ts(spread_seconds=3600)
            status = self.pick_weighted(_STATUSES, _STATUS_WEIGHTS)
            if status == "abandoned":
                abandoned_pool.append(sid)
                if len(abandoned_pool) > max_pool:
                    self.state.entity_pools["abandoned_session_ids"] = abandoned_pool[-max_pool:]
                    abandoned_pool = self.state.pool("abandoned_session_ids")

            sessions_rows.append({
                "session_id":   sid,
                "merchant_id":  self.rng.choice(merchant_pool),
                "user_id":      self.rng.choice(user_pool),
                "initiated_at": initiated,
                "status":       status,
                "total_amount": self._lognormal_inr(),
                "utm":          self._new_utm(),
                "_event_ts":    self.maybe_late(initiated),
                "_ingest_ts":   now,
            })

        # ---- checkout_items ----
        items_rows: List[dict] = []
        if session_pool:
            # Distribute n_items across a subset of sessions (1-4 items each)
            remaining = n_items
            while remaining > 0:
                sid = self.rng.choice(session_pool)
                per = min(remaining, self.rng.randint(1, 4))
                when = self.event_ts(spread_seconds=3600)
                for _ in range(per):
                    items_rows.append({
                        "item_id":       f"cki_{self.state.next_id('item'):014d}",
                        "session_id":    sid,
                        "sku":           self._pick_sku(),
                        "qty":           self.rng.randint(1, 5),
                        "unit_price":    round(self.rng.uniform(49.0, 4999.0), 2),
                        "variant_attrs": self._new_variant_attrs(),
                        "_event_ts":     self.maybe_late(when),
                        "_ingest_ts":    now,
                    })
                remaining -= per

        # ---- checkout_events ----
        events_rows: List[dict] = []
        for _ in range(n_events):
            if not session_pool:
                break
            sid = self.rng.choice(session_pool)
            etype = self.pick_weighted(_EVENT_TYPES, _EVENT_TYPE_WEIGHTS)
            when = self.event_ts(spread_seconds=3600)
            events_rows.append({
                "event_id":      f"cke_{self.state.next_id('event'):014d}",
                "session_id":    sid,
                "event_type":    etype,
                "event_time":    when,
                "event_payload": self._new_event_payload(etype),
                "_event_ts":     self.maybe_late(when),
                "_ingest_ts":    now,
            })

        # ---- checkout_abandonments ----
        abandonments_rows: List[dict] = []
        for _ in range(n_abandonments):
            if not abandoned_pool:
                break
            sid = self.rng.choice(abandoned_pool)
            abandoned_at = self.event_ts(spread_seconds=3600)
            last_step = self.pick_weighted(_LAST_STEPS, _LAST_STEP_WEIGHTS)
            abandonments_rows.append({
                "session_id":   sid,
                "abandoned_at": abandoned_at,
                "last_step":    last_step,
                "funnel_steps": self._new_funnel_steps(abandoned_at, last_step),
                "_event_ts":    self.maybe_late(abandoned_at),
                "_ingest_ts":   now,
            })

        # ---- build pa.Tables with explicit schemas ----
        return {
            "checkout_sessions":     pa.Table.from_pylist(sessions_rows,     schema=_SESSIONS_SCHEMA)     if sessions_rows     else pa.Table.from_pylist([], schema=_SESSIONS_SCHEMA),
            "checkout_items":        pa.Table.from_pylist(items_rows,        schema=_ITEMS_SCHEMA)        if items_rows        else pa.Table.from_pylist([], schema=_ITEMS_SCHEMA),
            "checkout_events":       pa.Table.from_pylist(events_rows,       schema=_EVENTS_SCHEMA)       if events_rows       else pa.Table.from_pylist([], schema=_EVENTS_SCHEMA),
            "checkout_abandonments": pa.Table.from_pylist(abandonments_rows, schema=_ABANDONMENTS_SCHEMA) if abandonments_rows else pa.Table.from_pylist([], schema=_ABANDONMENTS_SCHEMA),
        }
