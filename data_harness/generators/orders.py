"""orders domain generator — 5 tables including deeply-nested fulfillment (§6.4).

Tables:
  orders                (grain order_id; nested: line_items LIST<STRUCT>,
                        fulfillment STRUCT{shipments LIST<STRUCT{items LIST<STRUCT>}>} —
                        DEEPLY nested STRUCT-of-LIST-of-STRUCT-with-LIST-of-STRUCT,
                        fraud_signals STRUCT, metadata JSON)
  order_items           (flat child; derived from the same orders rows —
                        intentional redundancy per §6.10, modeller decides which
                        representation to prefer per query-pattern)
  order_status_history  (multiple rows per order across time; state_transition STRUCT)
  fulfillment_events    (per-order event; nested tracking_scans LIST<STRUCT>)
  order_notes           (sparse per-order note)

Cross-domain FKs drawn from state pools populated by other generators:
  merchant_ids · user_ids · checkout_session_ids
Writes:
  order_ids  (consumed by payments, attribution, refunds, …)
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- schemas (explicit — deeply nested types matter for the modeller) ----

_LINE_ITEM_STRUCT = pa.struct([
    ("sku",         pa.string()),
    ("qty",         pa.int32()),
    ("unit_price",  pa.float64()),
    ("category",    pa.string()),
    ("discount",    pa.float64()),
])

# Deeply nested: STRUCT{warehouse, shipments: LIST<STRUCT{..., items: LIST<STRUCT>}>}
# (STRUCT-of-LIST-of-STRUCT-with-LIST-of-STRUCT — §6.10 "Deeply nested" catalog entry)
_SHIPMENT_ITEM_STRUCT = pa.struct([
    ("sku",  pa.string()),
    ("qty",  pa.int32()),
])

_SHIPMENT_STRUCT = pa.struct([
    ("carrier",       pa.string()),
    ("tracking_ref",  pa.string()),
    ("shipped_at",    pa.timestamp("us", tz="UTC")),
    ("delivered_at",  pa.timestamp("us", tz="UTC")),
    ("items",         pa.list_(_SHIPMENT_ITEM_STRUCT)),
])

_FULFILLMENT_STRUCT = pa.struct([
    ("warehouse",   pa.string()),
    ("shipments",   pa.list_(_SHIPMENT_STRUCT)),
])

_FRAUD_SIGNALS_STRUCT = pa.struct([
    ("score",          pa.float64()),
    ("model_version",  pa.string()),
    ("rules",          pa.list_(pa.string())),
])

_STATE_TRANSITION_STRUCT = pa.struct([
    ("from_status",      pa.string()),
    ("to_status",        pa.string()),
    ("reason",           pa.string()),
    ("changed_by_type",  pa.string()),  # system | user | agent
])

_TRACKING_SCAN_STRUCT = pa.struct([
    ("scanned_at",  pa.timestamp("us", tz="UTC")),
    ("location",    pa.string()),
    ("status",      pa.string()),
])


_ORDERS_SCHEMA = pa.schema([
    ("order_id",             pa.string()),
    ("checkout_session_id",  pa.string()),   # nullable — direct orders exist
    ("merchant_id",          pa.string()),
    ("user_id",              pa.string()),
    ("order_time",           pa.timestamp("us", tz="UTC")),
    ("order_amount",         pa.float64()),
    ("status",               pa.string()),
    ("line_items",           pa.list_(_LINE_ITEM_STRUCT)),
    ("fulfillment",          _FULFILLMENT_STRUCT),
    ("fraud_signals",        _FRAUD_SIGNALS_STRUCT),
    ("metadata",             pa.string()),   # JSON as string
    ("_event_ts",            pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",           pa.timestamp("us", tz="UTC")),
])

_ORDER_ITEMS_SCHEMA = pa.schema([
    ("order_item_id",  pa.string()),
    ("order_id",       pa.string()),
    ("sku",            pa.string()),
    ("qty",            pa.int32()),
    ("unit_price",     pa.float64()),
    ("discount",       pa.float64()),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_ORDER_STATUS_HISTORY_SCHEMA = pa.schema([
    ("order_id",          pa.string()),
    ("status",            pa.string()),
    ("changed_at",        pa.timestamp("us", tz="UTC")),
    ("changed_by",        pa.string()),
    ("state_transition",  _STATE_TRANSITION_STRUCT),
    ("_event_ts",         pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",        pa.timestamp("us", tz="UTC")),
])

_FULFILLMENT_EVENTS_SCHEMA = pa.schema([
    ("fulfillment_event_id",  pa.string()),
    ("order_id",              pa.string()),
    ("event_type",            pa.string()),
    ("event_time",            pa.timestamp("us", tz="UTC")),
    ("warehouse",             pa.string()),
    ("tracking_scans",        pa.list_(_TRACKING_SCAN_STRUCT)),
    ("_event_ts",             pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",            pa.timestamp("us", tz="UTC")),
])

_ORDER_NOTES_SCHEMA = pa.schema([
    ("note_id",     pa.string()),
    ("order_id",    pa.string()),
    ("note_text",   pa.string()),
    ("noted_by",    pa.string()),
    ("noted_at",    pa.timestamp("us", tz="UTC")),
    ("_event_ts",   pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",  pa.timestamp("us", tz="UTC")),
])


# ---- domain vocab ----

_ORDER_STATUSES = ["placed", "shipped", "delivered", "cancelled", "returned"]
_ORDER_STATUS_WEIGHTS = [5, 25, 60, 8, 2]

_CARRIERS = ["Delhivery", "BlueDart", "Ecom Express", "DTDC", "XpressBees", "IndiaPost", "Shadowfax", "Ekart"]
_WAREHOUSES = ["MUM-WH1", "BLR-WH1", "DEL-WH1", "HYD-WH1", "CCU-WH1", "MAA-WH1", "AMD-WH1", "PNQ-WH1"]
_HUB_LOCATIONS = ["MUM-HUB", "BLR-HUB", "DEL-HUB", "HYD-HUB", "CCU-HUB", "MAA-HUB"]
_SCAN_STATUSES = ["in_transit", "arrived_hub", "out_for_delivery", "delivery_attempted", "delivered", "held_customs"]

_CATEGORIES = ["electronics", "apparel", "grocery", "beauty", "home",
               "books", "sports", "toys", "auto", "pharmacy"]

_FRAUD_MODEL_VERSIONS = ["fraud-v1.3", "fraud-v1.4", "fraud-v2.0", "fraud-v2.1"]
_FRAUD_RULES_POOL = ["velocity_high", "new_device", "geo_mismatch",
                     "amount_outlier", "bin_country_mismatch", "email_domain_disp",
                     "night_hour_spend", "high_risk_pin"]

_FE_EVENT_TYPES = ["picked", "packed", "shipped", "out_for_delivery", "delivered", "returned"]
_FE_EVENT_WEIGHTS = [15, 15, 20, 20, 25, 5]

_CHANGED_BY_TYPES = ["system", "user", "agent"]
_CHANGED_BY_WEIGHTS = [70, 15, 15]

_STATE_REASONS = ["auto_progression", "customer_request", "carrier_confirmation",
                  "warehouse_scan", "fraud_hold", "payment_failure", "address_issue",
                  "sla_breach"]

_NOTE_AUTHOR_TYPES = ["cs_agent", "ops_team", "warehouse_ops", "fraud_team", "customer"]
_NOTE_TEMPLATES = [
    "Customer requested delivery slot change.",
    "Fraud review approved.",
    "Address correction applied.",
    "Refund initiated per customer request.",
    "Shipment delayed at hub, awaiting update.",
    "COD verification pending.",
    "Escalated to warehouse team.",
    "Delivery re-attempt scheduled.",
    "Customer marked as VIP; expedite.",
    "Return pickup arranged.",
]

_PROMO_CODES = ["WELCOME10", "FEST20", "UPI5", "FREESHIP", "APPONLY15", "REPEAT12", "NEWUSER"]
_CHANNELS = ["app_ios", "app_android", "web_desktop", "web_mobile"]


class OrdersGenerator(BaseGenerator):
    domain = "orders"
    tables = ["orders", "order_items", "order_status_history",
              "fulfillment_events", "order_notes"]

    # ---- helpers ----

    def _new_line_item(self) -> dict:
        qty = self.rng.choices([1, 2, 3, 4, 5], weights=[55, 25, 12, 5, 3], k=1)[0]
        # Log-normal INR unit price — median ~₹450, tail to ~₹50k (§6.11).
        unit_price = round(self.rng.lognormvariate(6.0, 1.0), 2)
        gross = unit_price * qty
        discount = round(self.rng.uniform(0.05, 0.30) * gross, 2) if self.rng.random() < 0.35 else 0.0
        return {
            "sku":         f"sku_{self.rng.randint(1, int(self.cfg.get('entity_caps.skus', 5000))):06d}",
            "qty":         qty,
            "unit_price":  unit_price,
            "category":    self.rng.choice(_CATEGORIES),
            "discount":    discount,
        }

    def _new_shipment(self, order_time: datetime, line_items: List[dict]) -> dict:
        shipped_at   = order_time + timedelta(hours=self.rng.randint(2, 48))
        delivered_at = shipped_at + timedelta(hours=self.rng.randint(6, 96))
        # 1-3 line-item entries per shipment (subset of the order's line_items)
        n_items = self.rng.randint(1, min(3, max(1, len(line_items))))
        sample = self.rng.sample(line_items, n_items) if len(line_items) >= n_items else line_items
        return {
            "carrier":      self.rng.choice(_CARRIERS),
            "tracking_ref": f"TRK{self.rng.getrandbits(48):012X}",
            "shipped_at":   shipped_at,
            "delivered_at": delivered_at,
            "items":        [{"sku": it["sku"], "qty": it["qty"]} for it in sample],
        }

    def _new_fulfillment(self, order_time: datetime, line_items: List[dict]) -> dict:
        # 1-2 shipments per order (most orders single-shipment)
        n_ship = self.rng.choices([1, 2], weights=[80, 20], k=1)[0]
        return {
            "warehouse": self.rng.choice(_WAREHOUSES),
            "shipments": [self._new_shipment(order_time, line_items) for _ in range(n_ship)],
        }

    def _new_fraud_signals(self) -> dict:
        score = round(self.rng.random(), 4)
        n_rules = self.rng.choices([0, 1, 2, 3], weights=[70, 20, 8, 2], k=1)[0]
        rules = (self.rng.sample(_FRAUD_RULES_POOL, min(n_rules, len(_FRAUD_RULES_POOL)))
                 if n_rules else [])
        return {
            "score":         score,
            "model_version": self.rng.choice(_FRAUD_MODEL_VERSIONS),
            "rules":         rules,
        }

    def _metadata_json(self) -> str:
        promo: Optional[dict] = None
        if self.rng.random() < 0.4:
            promo = {
                "promo_code":   self.rng.choice(_PROMO_CODES),
                "discount_pct": self.rng.choice([5, 10, 15, 20, 25]),
            }
        return json.dumps({
            "channel":  self.rng.choice(_CHANNELS),
            "gift":     self.rng.random() < 0.05,
            "cod":      self.rng.random() < 0.15,
            "promo":    promo,
        })

    def _new_tracking_scan(self, base_ts: datetime, i: int) -> dict:
        return {
            "scanned_at":  base_ts + timedelta(minutes=self.rng.randint(0, 240) + i * 60),
            "location":    self.rng.choice(_WAREHOUSES + _HUB_LOCATIONS),
            "status":      self.rng.choice(_SCAN_STATUSES),
        }

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        n_orders   = self.rate("orders")
        n_history  = self.rate("order_status_history")
        n_ful_evts = self.rate("fulfillment_events")
        n_notes    = self.rate("order_notes")
        now = self.now()

        merchant_pool = self.state.pool("merchant_ids")
        user_pool     = self.state.pool("user_ids")
        checkout_pool = self.state.pool("checkout_session_ids")
        order_pool    = self.state.pool("order_ids")

        # ---- orders + derived flat order_items ----
        orders_rows: List[dict] = []
        order_items_rows: List[dict] = []

        # Cross-domain guard: cannot mint orders without merchants + users.
        if merchant_pool and user_pool:
            for _ in range(n_orders):
                oid = f"ord_{self.state.next_id('order'):012d}"
                order_pool.append(oid)
                # Cap the pool so JSON state doesn't grow unboundedly.
                max_pool = int(self.cfg.get("entity_caps.orders", 200_000))
                if len(order_pool) > max_pool:
                    self.state.entity_pools["order_ids"] = order_pool[-max_pool:]
                    order_pool = self.state.entity_pools["order_ids"]

                merchant_id = self.rng.choice(merchant_pool)
                user_id     = self.rng.choice(user_pool)
                # 80% of orders came through a checkout session; 20% direct.
                checkout_session_id: Optional[str] = None
                if checkout_pool and self.rng.random() < 0.8:
                    checkout_session_id = self.rng.choice(checkout_pool)

                order_time = self.event_ts(spread_seconds=3600)
                n_items = self.rng.randint(3, 8)
                line_items = [self._new_line_item() for _ in range(n_items)]
                order_amount = round(
                    sum(li["qty"] * li["unit_price"] - li["discount"] for li in line_items), 2
                )
                status        = self.pick_weighted(_ORDER_STATUSES, _ORDER_STATUS_WEIGHTS)
                fulfillment   = self._new_fulfillment(order_time, line_items)
                fraud_signals = self._new_fraud_signals()

                orders_rows.append({
                    "order_id":            oid,
                    "checkout_session_id": checkout_session_id,
                    "merchant_id":         merchant_id,
                    "user_id":             user_id,
                    "order_time":          order_time,
                    "order_amount":        order_amount,
                    "status":              status,
                    "line_items":          line_items,
                    "fulfillment":         fulfillment,
                    "fraud_signals":       fraud_signals,
                    "metadata":            self._metadata_json(),
                    "_event_ts":           self.maybe_late(order_time),
                    "_ingest_ts":          now,
                })

                # Flat child derived from the same rows (intentional redundancy — §6.10).
                for li in line_items:
                    order_items_rows.append({
                        "order_item_id":  f"oi_{self.state.next_id('order_item'):014d}",
                        "order_id":       oid,
                        "sku":            li["sku"],
                        "qty":            li["qty"],
                        "unit_price":     li["unit_price"],
                        "discount":       li["discount"],
                        "_event_ts":      self.maybe_late(order_time),
                        "_ingest_ts":     now,
                    })

        # ---- order_status_history (multiple rows per order across time) ----
        history_rows: List[dict] = []
        for _ in range(n_history):
            if not order_pool:
                break
            oid = self.rng.choice(order_pool)
            from_status = self.rng.choice(_ORDER_STATUSES)
            to_status   = self.pick_weighted(_ORDER_STATUSES, _ORDER_STATUS_WEIGHTS)
            changed_at  = self.event_ts(spread_seconds=3600)
            changed_by_type = self.pick_weighted(_CHANGED_BY_TYPES, _CHANGED_BY_WEIGHTS)
            if changed_by_type == "system":
                changed_by = "system_scheduler"
            elif changed_by_type == "agent":
                changed_by = f"agent_{self.rng.randint(1, 50)}"
            else:
                changed_by = f"usr_{self.rng.randint(1, 10_000):010d}"
            history_rows.append({
                "order_id":    oid,
                "status":      to_status,
                "changed_at":  changed_at,
                "changed_by":  changed_by,
                "state_transition": {
                    "from_status":      from_status,
                    "to_status":        to_status,
                    "reason":           self.rng.choice(_STATE_REASONS),
                    "changed_by_type":  changed_by_type,
                },
                "_event_ts":   self.maybe_late(changed_at),
                "_ingest_ts":  now,
            })

        # ---- fulfillment_events ----
        fe_rows: List[dict] = []
        for _ in range(n_ful_evts):
            if not order_pool:
                break
            oid = self.rng.choice(order_pool)
            event_time = self.event_ts(spread_seconds=3600)
            event_type = self.pick_weighted(_FE_EVENT_TYPES, _FE_EVENT_WEIGHTS)
            n_scans = self.rng.choices([1, 2, 3, 4], weights=[40, 30, 20, 10], k=1)[0]
            fe_rows.append({
                "fulfillment_event_id": f"fe_{self.state.next_id('fulfillment_event'):014d}",
                "order_id":             oid,
                "event_type":           event_type,
                "event_time":           event_time,
                "warehouse":            self.rng.choice(_WAREHOUSES),
                "tracking_scans":       [self._new_tracking_scan(event_time, i)
                                         for i in range(n_scans)],
                "_event_ts":            self.maybe_late(event_time),
                "_ingest_ts":           now,
            })

        # ---- order_notes (sparse) ----
        notes_rows: List[dict] = []
        for _ in range(n_notes):
            if not order_pool:
                break
            oid = self.rng.choice(order_pool)
            when = self.event_ts(spread_seconds=3600)
            author_type = self.rng.choice(_NOTE_AUTHOR_TYPES)
            notes_rows.append({
                "note_id":    f"nt_{self.state.next_id('order_note'):012d}",
                "order_id":   oid,
                "note_text":  self.rng.choice(_NOTE_TEMPLATES),
                "noted_by":   f"{author_type}_{self.rng.randint(1, 200)}",
                "noted_at":   when,
                "_event_ts":  self.maybe_late(when),
                "_ingest_ts": now,
            })

        # ---- build pa.Tables with explicit schemas ----
        return {
            "orders":               pa.Table.from_pylist(orders_rows,      schema=_ORDERS_SCHEMA)               if orders_rows      else pa.Table.from_pylist([], schema=_ORDERS_SCHEMA),
            "order_items":          pa.Table.from_pylist(order_items_rows, schema=_ORDER_ITEMS_SCHEMA)          if order_items_rows else pa.Table.from_pylist([], schema=_ORDER_ITEMS_SCHEMA),
            "order_status_history": pa.Table.from_pylist(history_rows,     schema=_ORDER_STATUS_HISTORY_SCHEMA) if history_rows     else pa.Table.from_pylist([], schema=_ORDER_STATUS_HISTORY_SCHEMA),
            "fulfillment_events":   pa.Table.from_pylist(fe_rows,          schema=_FULFILLMENT_EVENTS_SCHEMA)   if fe_rows          else pa.Table.from_pylist([], schema=_FULFILLMENT_EVENTS_SCHEMA),
            "order_notes":          pa.Table.from_pylist(notes_rows,       schema=_ORDER_NOTES_SCHEMA)          if notes_rows       else pa.Table.from_pylist([], schema=_ORDER_NOTES_SCHEMA),
        }
