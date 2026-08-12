"""risk domain generator — 5 tables with rich nested types (§6.8).

Tables:
  risk_events        (polymorphic entity; evidence: JSON)
  rules_fired        (many-per-event; rule_inputs: STRUCT)
  fraud_labels       (ground truth; label_reasons: LIST<VARCHAR>)
  sanctions_screens  (KYC step; match_details: STRUCT)
  velocity_windows   (rolling counter; per_rail_breakdown: MAP<VARCHAR,STRUCT>)
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- schemas (explicit — nested types matter for the modeller) ----

_RULE_INPUTS_STRUCT = pa.struct([
    ("feature_name",  pa.string()),
    ("feature_value", pa.float64()),
    ("threshold",     pa.float64()),
    ("comparator",    pa.string()),
])

_MATCH_DETAILS_STRUCT = pa.struct([
    ("matched_name",     pa.string()),
    ("similarity_score", pa.float64()),
    ("list_source",      pa.string()),
    ("matched_at",       pa.timestamp("us", tz="UTC")),
])

_RAIL_BREAKDOWN_STRUCT = pa.struct([
    ("count",  pa.int32()),
    ("amount", pa.float64()),
])


_RISK_EVENTS_SCHEMA = pa.schema([
    ("risk_event_id", pa.string()),
    ("entity_type",   pa.string()),
    ("entity_id",     pa.string()),
    ("event_time",    pa.timestamp("us", tz="UTC")),
    ("rule_id",       pa.string()),
    ("severity",      pa.string()),
    ("evidence",      pa.string()),  # JSON as string
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_RULES_FIRED_SCHEMA = pa.schema([
    ("firing_id",     pa.string()),
    ("risk_event_id", pa.string()),
    ("rule_name",     pa.string()),
    ("score",         pa.float64()),
    ("action",        pa.string()),
    ("rule_inputs",   _RULE_INPUTS_STRUCT),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_FRAUD_LABELS_SCHEMA = pa.schema([
    ("label_id",      pa.string()),
    ("entity_id",     pa.string()),
    ("entity_type",   pa.string()),
    ("labeled_at",    pa.timestamp("us", tz="UTC")),
    ("label",         pa.string()),
    ("label_reasons", pa.list_(pa.string())),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_SANCTIONS_SCREENS_SCHEMA = pa.schema([
    ("screen_id",     pa.string()),
    ("user_id",       pa.string()),
    ("screened_at",   pa.timestamp("us", tz="UTC")),
    ("hit_flag",      pa.bool_()),
    ("sanction_list", pa.string()),
    ("match_details", _MATCH_DETAILS_STRUCT),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_VELOCITY_WINDOWS_SCHEMA = pa.schema([
    ("window_id",           pa.string()),
    ("user_id",             pa.string()),
    ("window_start",        pa.timestamp("us", tz="UTC")),
    ("window_end",          pa.timestamp("us", tz="UTC")),
    ("txn_count",           pa.int32()),
    ("txn_amount",          pa.float64()),
    ("per_rail_breakdown",  pa.map_(pa.string(), _RAIL_BREAKDOWN_STRUCT)),
    ("_event_ts",           pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",          pa.timestamp("us", tz="UTC")),
])


_ENTITY_TYPES = ["user", "merchant", "payment"]
_ENTITY_TYPE_WEIGHTS = [55, 15, 30]
_ENTITY_POOL_NAME = {
    "user":     "user_ids",
    "merchant": "merchant_ids",
    "payment":  "payment_ids",
}
_SEVERITIES = ["low", "medium", "high", "critical"]
_SEVERITY_WEIGHTS = [60, 25, 12, 3]
_ACTIONS = ["allow", "challenge", "block"]
_ACTION_WEIGHTS = [65, 25, 10]
_RULE_NAMES = [
    "velocity_burst", "geo_mismatch", "device_change", "amount_anomaly",
    "new_beneficiary", "night_txn_spike", "otp_failures", "chargeback_ratio",
    "card_bin_mismatch", "ip_reputation", "session_replay", "kyc_expiry",
    "merchant_mcc_shift", "sanctions_soft_hit", "structuring_pattern",
]
_FEATURE_NAMES = [
    "txn_count_1h", "txn_amount_1h", "distinct_merchants_1h",
    "distinct_devices_24h", "distance_km_last_txn", "avg_ticket_7d",
    "chargeback_rate_30d", "otp_fail_rate_1h", "session_age_min",
]
_COMPARATORS = [">", ">=", "<", "<=", "!="]
_LABELS = ["fraud", "clean", "suspicious"]
_LABEL_WEIGHTS = [5, 85, 10]
_LABEL_REASONS_POOL = [
    "manual_review", "chargeback_confirmed", "user_reported", "rule_hit",
    "network_flag", "issuer_decline_pattern", "device_farm", "aged_out",
    "false_positive_appeal", "kyc_re_verified",
]
_SANCTION_LISTS = ["ofac", "un", "eu", "internal"]
_SANCTION_LIST_WEIGHTS = [35, 25, 25, 15]
_RAILS = ["UPI", "DC", "CC", "NB", "WALLET", "IMPS", "NEFT"]


class RiskGenerator(BaseGenerator):
    domain = "risk"
    tables = ["risk_events", "rules_fired", "fraud_labels",
              "sanctions_screens", "velocity_windows"]

    # ---- per-rule evidence payloads (JSON) ----
    def _evidence_json(self, rule_id: str, severity: str) -> str:
        payload = {
            "rule_id":  rule_id,
            "severity": severity,
            "signals": {
                "score":         round(self.rng.uniform(0.0, 1.0), 3),
                "z_score":       round(self.rng.uniform(-1.0, 6.0), 3),
                "window_min":    self.rng.choice([1, 5, 15, 60, 1440]),
                "peer_deviation": round(self.rng.uniform(0.0, 4.0), 3),
            },
            "context": {
                "channel":  self.rng.choice(["mobile", "web", "api"]),
                "ip_asn":   self.rng.randint(1000, 65000),
                "geo_city": self.faker.city(),
            },
        }
        if self.rng.random() < 0.4:
            payload["notes"] = self.rng.choice([
                "burst_after_login", "atypical_mcc", "vpn_suspected",
                "new_device_first_txn", "mismatched_billing",
            ])
        return json.dumps(payload)

    def _new_rule_inputs(self) -> dict:
        feat = self.rng.choice(_FEATURE_NAMES)
        threshold = round(self.rng.uniform(1.0, 100.0), 2)
        # observed value skewed above threshold for firings
        feature_value = round(threshold * self.rng.uniform(0.8, 3.5), 2)
        return {
            "feature_name":  feat,
            "feature_value": feature_value,
            "threshold":     threshold,
            "comparator":    self.rng.choice(_COMPARATORS),
        }

    def _new_label_reasons(self, label: str) -> List[str]:
        if label == "clean":
            # mostly empty for clean
            n = self.rng.choices([0, 1], weights=[85, 15], k=1)[0]
        elif label == "suspicious":
            n = self.rng.choices([1, 2], weights=[60, 40], k=1)[0]
        else:  # fraud
            n = self.rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15], k=1)[0]
        if n == 0:
            return []
        pool = list(_LABEL_REASONS_POOL)
        self.rng.shuffle(pool)
        return pool[:n]

    def _new_match_details(self, when: datetime, sanction_list: str) -> dict:
        return {
            "matched_name":     self.faker.name(),
            "similarity_score": round(self.rng.uniform(0.75, 0.99), 3),
            "list_source":      sanction_list,
            "matched_at":       when,
        }

    def _empty_match_details(self) -> dict:
        # emit null-ish struct when no hit
        return {
            "matched_name":     None,
            "similarity_score": None,
            "list_source":      None,
            "matched_at":       None,
        }

    def _new_per_rail_breakdown(self, total_count: int, total_amount: float) -> dict:
        # pick 1..4 rails and distribute the totals across them
        n = self.rng.choices([1, 2, 3, 4], weights=[35, 40, 20, 5], k=1)[0]
        rails = list(_RAILS)
        self.rng.shuffle(rails)
        picked = rails[:n]
        # random weights
        w = [self.rng.random() for _ in picked]
        s = sum(w) or 1.0
        w = [x / s for x in w]
        out: Dict[str, dict] = {}
        remaining_count = total_count
        remaining_amount = total_amount
        for i, rail in enumerate(picked):
            if i == len(picked) - 1:
                c = remaining_count
                a = remaining_amount
            else:
                c = int(round(total_count * w[i]))
                c = min(c, remaining_count)
                a = round(total_amount * w[i], 2)
                a = min(a, remaining_amount)
            out[rail] = {"count": int(c), "amount": float(round(a, 2))}
            remaining_count -= c
            remaining_amount -= a
        return out

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        n_events    = self.rate("risk_events")
        n_firings   = self.rate("rules_fired")
        n_labels    = self.rate("fraud_labels")
        n_screens   = self.rate("sanctions_screens")
        n_windows   = self.rate("velocity_windows")
        now = self.now()

        user_pool     = self.state.pool("user_ids")
        merchant_pool = self.state.pool("merchant_ids")
        payment_pool  = self.state.pool("payment_ids")
        pools_by_type = {
            "user":     user_pool,
            "merchant": merchant_pool,
            "payment":  payment_pool,
        }

        # ---- risk_events ----
        events_rows: List[dict] = []
        for _ in range(n_events):
            # pick an entity_type whose pool is non-empty
            ent_type = self.pick_weighted(_ENTITY_TYPES, _ENTITY_TYPE_WEIGHTS)
            pool = pools_by_type.get(ent_type, [])
            if not pool:
                # try any populated pool
                candidates = [t for t in _ENTITY_TYPES if pools_by_type.get(t)]
                if not candidates:
                    break
                ent_type = self.rng.choice(candidates)
                pool = pools_by_type[ent_type]
            eid = self.rng.choice(pool)
            when = self.event_ts(spread_seconds=3600)
            rule_id = f"rule_{self.rng.randint(1, 50):03d}"
            sev = self.pick_weighted(_SEVERITIES, _SEVERITY_WEIGHTS)
            reid = f"risk_{self.state.next_id('risk_event'):012d}"

            # publish to the shared pool so rules_fired can reference
            self.state.pool("risk_event_ids").append(reid)
            max_pool = int(self.cfg.get("entity_caps.risk_events", 100_000))
            if len(self.state.pool("risk_event_ids")) > max_pool:
                self.state.entity_pools["risk_event_ids"] = \
                    self.state.pool("risk_event_ids")[-max_pool:]

            events_rows.append({
                "risk_event_id": reid,
                "entity_type":   ent_type,
                "entity_id":     eid,
                "event_time":    when,
                "rule_id":       rule_id,
                "severity":      sev,
                "evidence":      self._evidence_json(rule_id, sev),
                "_event_ts":     self.maybe_late(when),
                "_ingest_ts":    now,
            })

        # ---- rules_fired ----
        firings_rows: List[dict] = []
        risk_event_pool = self.state.pool("risk_event_ids")
        for _ in range(n_firings):
            if not risk_event_pool:
                break
            reid = self.rng.choice(risk_event_pool)
            when = self.event_ts(spread_seconds=3600)
            firings_rows.append({
                "firing_id":     f"fire_{self.state.next_id('firing'):012d}",
                "risk_event_id": reid,
                "rule_name":     self.rng.choice(_RULE_NAMES),
                "score":         round(self.rng.random(), 4),
                "action":        self.pick_weighted(_ACTIONS, _ACTION_WEIGHTS),
                "rule_inputs":   self._new_rule_inputs(),
                "_event_ts":     self.maybe_late(when),
                "_ingest_ts":    now,
            })

        # ---- fraud_labels (sparse ground truth) ----
        labels_rows: List[dict] = []
        for _ in range(n_labels):
            ent_type = self.pick_weighted(_ENTITY_TYPES, _ENTITY_TYPE_WEIGHTS)
            pool = pools_by_type.get(ent_type, [])
            if not pool:
                candidates = [t for t in _ENTITY_TYPES if pools_by_type.get(t)]
                if not candidates:
                    break
                ent_type = self.rng.choice(candidates)
                pool = pools_by_type[ent_type]
            eid = self.rng.choice(pool)
            when = self.event_ts(spread_seconds=3600)
            lab = self.pick_weighted(_LABELS, _LABEL_WEIGHTS)
            labels_rows.append({
                "label_id":      f"lbl_{self.state.next_id('label'):012d}",
                "entity_id":     eid,
                "entity_type":   ent_type,
                "labeled_at":    when,
                "label":         lab,
                "label_reasons": self._new_label_reasons(lab),
                "_event_ts":     self.maybe_late(when),
                "_ingest_ts":    now,
            })

        # ---- sanctions_screens ----
        screens_rows: List[dict] = []
        for _ in range(n_screens):
            if not user_pool:
                break
            uid = self.rng.choice(user_pool)
            when = self.event_ts(spread_seconds=3600)
            hit = self.rng.random() < 0.02
            slist = self.pick_weighted(_SANCTION_LISTS, _SANCTION_LIST_WEIGHTS)
            screens_rows.append({
                "screen_id":     f"scr_{self.state.next_id('screen'):012d}",
                "user_id":       uid,
                "screened_at":   when,
                "hit_flag":      hit,
                "sanction_list": slist,
                "match_details": self._new_match_details(when, slist) if hit
                                  else self._empty_match_details(),
                "_event_ts":     self.maybe_late(when),
                "_ingest_ts":    now,
            })

        # ---- velocity_windows ----
        windows_rows: List[dict] = []
        for _ in range(n_windows):
            if not user_pool:
                break
            uid = self.rng.choice(user_pool)
            start = self.event_ts(spread_seconds=3600)
            window_len = self.rng.choice([300, 900, 3600, 86_400])  # 5m/15m/1h/24h
            end = start + timedelta(seconds=window_len)
            txn_count = self.rng.randint(1, 40)
            txn_amount = round(self.rng.lognormvariate(6.5, 1.1), 2)
            windows_rows.append({
                "window_id":          f"vw_{self.state.next_id('velocity'):012d}",
                "user_id":            uid,
                "window_start":       start,
                "window_end":         end,
                "txn_count":          txn_count,
                "txn_amount":         txn_amount,
                "per_rail_breakdown": self._new_per_rail_breakdown(txn_count, txn_amount),
                "_event_ts":          self.maybe_late(start),
                "_ingest_ts":         now,
            })

        # ---- build pa.Tables with explicit schemas ----
        return {
            "risk_events":       pa.Table.from_pylist(events_rows,   schema=_RISK_EVENTS_SCHEMA)       if events_rows   else pa.Table.from_pylist([], schema=_RISK_EVENTS_SCHEMA),
            "rules_fired":       pa.Table.from_pylist(firings_rows,  schema=_RULES_FIRED_SCHEMA)       if firings_rows  else pa.Table.from_pylist([], schema=_RULES_FIRED_SCHEMA),
            "fraud_labels":      pa.Table.from_pylist(labels_rows,   schema=_FRAUD_LABELS_SCHEMA)      if labels_rows   else pa.Table.from_pylist([], schema=_FRAUD_LABELS_SCHEMA),
            "sanctions_screens": pa.Table.from_pylist(screens_rows,  schema=_SANCTIONS_SCREENS_SCHEMA) if screens_rows  else pa.Table.from_pylist([], schema=_SANCTIONS_SCREENS_SCHEMA),
            "velocity_windows":  pa.Table.from_pylist(windows_rows,  schema=_VELOCITY_WINDOWS_SCHEMA)  if windows_rows  else pa.Table.from_pylist([], schema=_VELOCITY_WINDOWS_SCHEMA),
        }
