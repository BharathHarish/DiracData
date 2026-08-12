"""payments domain generator — 7 tables with very rich nested types (§6.5).

Tables:
  payment_attempts     (attempt_id;    risk_checks LIST<STRUCT>,
                                       routing_decision STRUCT)
  payment_events       (event_id;      event_attributes STRUCT (rail-specific),
                                       processor_response JSON)
  payment_methods      (method_id;     method_details STRUCT — polymorphic
                                       per method_type)
  refunds              (refund_id;     reason_metadata JSON)
  chargebacks          (chargeback_id; evidence_docs LIST<STRUCT>)
  settlement_batches   (batch_id;      breakdown LIST<STRUCT> — per-rail split)
  disputes             (dispute_id;    timeline LIST<STRUCT>)

Cross-domain FK integrity via cross-domain pools (order_ids, user_ids,
merchant_ids). Sub-tables that depend on payments live off the internally-
tracked pools (attempt_ids for events, payment_ids for refunds/chargebacks,
chargeback_ids for disputes).
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- nested type building blocks ----

_RISK_CHECK_STRUCT = pa.struct([
    ("check_name", pa.string()),
    ("result",     pa.string()),  # pass | fail | warn
    ("score",      pa.float64()),
])

_ROUTING_DECISION_STRUCT = pa.struct([
    ("primary_rail",    pa.string()),
    ("fallback_rail",   pa.string()),
    ("decision_reason", pa.string()),
    ("latency_ms",      pa.int32()),
])

_EVENT_ATTRIBUTES_STRUCT = pa.struct([
    ("gateway_ref", pa.string()),
    ("rail_ref",    pa.string()),
    ("terminal_id", pa.string()),
    ("mcc_code",    pa.string()),
])

# Polymorphic VARIANT — every column present in schema, but only fields
# relevant to method_type populated per row; the rest are null.
_METHOD_DETAILS_STRUCT = pa.struct([
    ("upi_vpa",       pa.string()),
    ("card_bin",      pa.string()),
    ("card_network",  pa.string()),
    ("wallet_handle", pa.string()),
    ("expiry_month",  pa.int32()),
    ("expiry_year",   pa.int32()),
])

_EVIDENCE_DOC_STRUCT = pa.struct([
    ("doc_type",    pa.string()),
    ("filename",    pa.string()),
    ("uploaded_at", pa.timestamp("us", tz="UTC")),
    ("verified",    pa.bool_()),
])

_BREAKDOWN_STRUCT = pa.struct([
    ("rail_type", pa.string()),
    ("count",     pa.int64()),
    ("gross",     pa.float64()),
    ("mdr",       pa.float64()),
    ("net",       pa.float64()),
])

_TIMELINE_STRUCT = pa.struct([
    ("event_type", pa.string()),
    ("event_time", pa.timestamp("us", tz="UTC")),
    ("actor",      pa.string()),
])


# ---- explicit table schemas ----

_ATTEMPTS_SCHEMA = pa.schema([
    ("attempt_id",       pa.string()),
    ("order_id",         pa.string()),
    ("user_id",          pa.string()),
    ("merchant_id",      pa.string()),
    ("amount",           pa.float64()),
    ("rail_type",        pa.string()),
    ("attempted_at",     pa.timestamp("us", tz="UTC")),
    ("status",           pa.string()),  # success | failed | pending
    ("risk_checks",      pa.list_(_RISK_CHECK_STRUCT)),
    ("routing_decision", _ROUTING_DECISION_STRUCT),
    ("_event_ts",        pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",       pa.timestamp("us", tz="UTC")),
])

_EVENTS_SCHEMA = pa.schema([
    ("event_id",           pa.string()),
    ("attempt_id",         pa.string()),
    ("event_type",         pa.string()),  # initiated|authorized|captured|settled|failed|refunded
    ("event_time",         pa.timestamp("us", tz="UTC")),
    ("event_attributes",   _EVENT_ATTRIBUTES_STRUCT),
    ("processor_response", pa.string()),  # JSON as string
    ("_event_ts",          pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",         pa.timestamp("us", tz="UTC")),
])

_METHODS_SCHEMA = pa.schema([
    ("method_id",      pa.string()),
    ("user_id",        pa.string()),
    ("method_type",    pa.string()),  # card | upi | netbanking | wallet
    ("issuer",         pa.string()),
    ("added_at",       pa.timestamp("us", tz="UTC")),
    ("method_details", _METHOD_DETAILS_STRUCT),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_REFUNDS_SCHEMA = pa.schema([
    ("refund_id",           pa.string()),
    ("original_payment_id", pa.string()),
    ("refund_amount",       pa.float64()),
    ("refund_time",         pa.timestamp("us", tz="UTC")),
    ("reason",              pa.string()),
    ("reason_metadata",     pa.string()),  # JSON as string
    ("_event_ts",           pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",          pa.timestamp("us", tz="UTC")),
])

_CHARGEBACKS_SCHEMA = pa.schema([
    ("chargeback_id",       pa.string()),
    ("original_payment_id", pa.string()),
    ("dispute_reason",      pa.string()),
    ("filed_at",            pa.timestamp("us", tz="UTC")),
    ("resolved_at",         pa.timestamp("us", tz="UTC")),  # nullable
    ("evidence_docs",       pa.list_(_EVIDENCE_DOC_STRUCT)),
    ("_event_ts",           pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",          pa.timestamp("us", tz="UTC")),
])

_BATCHES_SCHEMA = pa.schema([
    ("batch_id",     pa.string()),
    ("merchant_id",  pa.string()),
    ("batch_date",   pa.date32()),
    ("gross_amount", pa.float64()),
    ("mdr_amount",   pa.float64()),
    ("net_amount",   pa.float64()),
    ("status",       pa.string()),  # pending | settled | failed
    ("breakdown",    pa.list_(_BREAKDOWN_STRUCT)),
    ("_event_ts",    pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",   pa.timestamp("us", tz="UTC")),
])

_DISPUTES_SCHEMA = pa.schema([
    ("dispute_id",            pa.string()),
    ("chargeback_id",         pa.string()),
    ("evidence_submitted_at", pa.timestamp("us", tz="UTC")),
    ("resolution",            pa.string()),  # merchant_won | customer_won | pending
    ("resolved_at",           pa.timestamp("us", tz="UTC")),  # nullable
    ("timeline",              pa.list_(_TIMELINE_STRUCT)),
    ("_event_ts",             pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",            pa.timestamp("us", tz="UTC")),
])


# ---- constants (India-realistic, matches §6.5 / §6.11) ----

_STATUS_ATTEMPT         = ["success", "failed", "pending"]
_STATUS_ATTEMPT_WEIGHTS = [82, 15, 3]

_RISK_CHECK_NAMES = [
    "velocity_check", "device_reputation", "amount_threshold",
    "geo_mismatch", "issuer_bin_check", "3ds_challenge",
    "aml_screen", "behavioural_score",
]
_RISK_RESULTS         = ["pass", "fail", "warn"]
_RISK_RESULT_WEIGHTS  = [88, 4, 8]

_ROUTING_REASONS = [
    "primary_rail_healthy", "lowest_mdr", "issuer_preferred",
    "high_value_route", "retry_after_fail", "geo_optimised",
]

# state machine per attempt outcome
_EVENT_TYPES_SUCCESS_PATH = ["initiated", "authorized", "captured", "settled"]
_EVENT_TYPES_FAILED_PATH  = ["initiated", "failed"]
_EVENT_TYPES_PENDING_PATH = ["initiated", "authorized"]

_METHOD_TYPES         = ["card", "upi", "netbanking", "wallet"]
_METHOD_TYPE_WEIGHTS  = [22, 60, 10, 8]

_CARD_NETWORKS        = ["Visa", "MasterCard", "RuPay", "Amex"]
_CARD_NETWORK_WEIGHTS = [40, 30, 27, 3]

_CARD_ISSUERS = [
    "HDFC Bank", "ICICI Bank", "SBI", "Axis Bank",
    "Kotak Mahindra", "Yes Bank", "IndusInd Bank", "IDFC First",
]
_UPI_ISSUERS        = ["PhonePe", "Google Pay", "Paytm", "BHIM", "Amazon Pay", "CRED"]
_NETBANKING_ISSUERS = [
    "HDFC Bank", "ICICI Bank", "SBI", "Axis Bank",
    "Kotak Mahindra", "PNB", "Bank of Baroda",
]
_WALLET_ISSUERS  = ["Paytm Wallet", "Amazon Pay Balance", "Mobikwik", "Freecharge"]
_UPI_HANDLES     = ["@ybl", "@okhdfcbank", "@paytm", "@apl", "@axl", "@oksbi", "@ibl"]

_REFUND_REASONS        = ["product_return", "duplicate_charge", "fraud", "customer_request"]
_REFUND_REASON_WEIGHTS = [45, 15, 5, 35]

_CHARGEBACK_REASONS        = ["unauthorized", "item_not_received", "duplicate", "other"]
_CHARGEBACK_REASON_WEIGHTS = [40, 30, 15, 15]

_EVIDENCE_DOC_TYPES = [
    "invoice", "delivery_proof", "communication_log",
    "tracking_document", "signed_receipt", "id_verification",
]

_BATCH_STATUS         = ["pending", "settled", "failed"]
_BATCH_STATUS_WEIGHTS = [15, 80, 5]

_DISPUTE_RESOLUTIONS         = ["merchant_won", "customer_won", "pending"]
_DISPUTE_RESOLUTION_WEIGHTS  = [35, 30, 35]

_DISPUTE_TIMELINE_ACTORS = ["merchant", "customer", "arbitrator", "acquirer", "issuer"]

_MCC_CODES = [
    "5411", "5812", "5732", "5921", "5311", "4111",
    "7011", "5691", "4816", "5967", "8299",
]

_GATEWAYS = ["razorpay", "cashfree", "juspay", "billdesk", "payu"]
_DECLINE_CODES = ["05", "12", "51", "91", "N7", "14", "54"]
_DECLINE_MSGS = [
    "do_not_honor", "invalid_txn", "insufficient_funds",
    "issuer_timeout", "auth_declined", "card_expired", "3ds_failed",
]

# pool caps (internal, per-domain state)
_MAX_ATTEMPT_POOL     = 20_000
_MAX_PAYMENT_POOL     = 20_000
_MAX_CHARGEBACK_POOL  = 5_000


class PaymentsGenerator(BaseGenerator):
    domain = "payments"
    tables = [
        "payment_attempts", "payment_events", "payment_methods",
        "refunds", "chargebacks", "settlement_batches", "disputes",
    ]

    # ---- pool helpers ----

    def _cap_pool(self, name: str, cap: int) -> None:
        pool = self.state.pool(name)
        if len(pool) > cap:
            self.state.entity_pools[name] = pool[-cap:]

    def _append_pool(self, name: str, value) -> None:
        self.state.pool(name).append(value)

    # ---- nested-payload builders ----

    def _risk_checks(self) -> list:
        n = self.rng.choices([2, 3, 4, 5], weights=[15, 40, 30, 15], k=1)[0]
        names = self.rng.sample(_RISK_CHECK_NAMES, k=min(n, len(_RISK_CHECK_NAMES)))
        return [
            {
                "check_name": name,
                "result":     self.pick_weighted(_RISK_RESULTS, _RISK_RESULT_WEIGHTS),
                "score":      round(self.rng.uniform(0.0, 1.0), 4),
            }
            for name in names
        ]

    def _routing_decision(self, rail: str) -> dict:
        candidates = ["UPI", "DC", "CC", "NB", "WALLET", "IMPS", "NEFT"]
        alt = [r for r in candidates if r != rail] or candidates
        return {
            "primary_rail":    rail,
            "fallback_rail":   self.rng.choice(alt),
            "decision_reason": self.rng.choice(_ROUTING_REASONS),
            "latency_ms":      self.rng.randint(15, 850),
        }

    def _rail_ref(self, rail: str) -> str:
        prefix = {
            "UPI": "upi", "DC": "dc", "CC": "cc", "NB": "nb",
            "WALLET": "wlt", "IMPS": "imps", "NEFT": "neft",
        }.get(rail, "gen")
        return f"{prefix}_{self.rng.getrandbits(48):012x}"

    def _event_attributes(self, rail: str) -> dict:
        return {
            "gateway_ref": f"gw_{self.rng.getrandbits(64):016x}",
            "rail_ref":    self._rail_ref(rail),
            "terminal_id": f"tm_{self.rng.randint(10000, 99999)}",
            "mcc_code":    self.rng.choice(_MCC_CODES),
        }

    def _processor_response(self, rail: str, event_type: str) -> str:
        is_ok = event_type != "failed"
        payload = {
            "gateway":    self.rng.choice(_GATEWAYS),
            "rail":       rail,
            "event":      event_type,
            "code":       "00" if is_ok else self.rng.choice(_DECLINE_CODES),
            "message":    "OK" if is_ok else self.rng.choice(_DECLINE_MSGS),
            "risk_score": round(self.rng.random(), 3),
            "trace_id":   f"tr_{self.rng.getrandbits(80):020x}",
            "retry":      (not is_ok) and self.rng.random() < 0.4,
        }
        return json.dumps(payload)

    def _method_details(self, method_type: str) -> dict:
        """Polymorphic — only fields relevant to method_type non-null; rest null."""
        details = {
            "upi_vpa":       None,
            "card_bin":      None,
            "card_network":  None,
            "wallet_handle": None,
            "expiry_month":  None,
            "expiry_year":   None,
        }
        if method_type == "card":
            details["card_bin"]     = f"{self.rng.randint(400000, 699999):06d}"
            details["card_network"] = self.pick_weighted(_CARD_NETWORKS, _CARD_NETWORK_WEIGHTS)
            details["expiry_month"] = self.rng.randint(1, 12)
            details["expiry_year"]  = self.rng.randint(2026, 2032)
        elif method_type == "upi":
            handle = self.rng.choice(_UPI_HANDLES)
            details["upi_vpa"] = f"{self.faker.user_name()}{handle}"
        elif method_type == "wallet":
            details["wallet_handle"] = f"+91{self.rng.randint(6000000000, 9999999999)}"
        # netbanking → issuer alone carries the identity; details stay null
        return details

    def _issuer_for(self, method_type: str) -> str:
        if method_type == "card":       return self.rng.choice(_CARD_ISSUERS)
        if method_type == "upi":        return self.rng.choice(_UPI_ISSUERS)
        if method_type == "netbanking": return self.rng.choice(_NETBANKING_ISSUERS)
        if method_type == "wallet":     return self.rng.choice(_WALLET_ISSUERS)
        return "unknown"

    def _evidence_docs(self, filed_at: datetime) -> list:
        n = self.rng.choices([1, 2, 3, 4], weights=[20, 40, 30, 10], k=1)[0]
        docs = []
        for _ in range(n):
            doc_type = self.rng.choice(_EVIDENCE_DOC_TYPES)
            offset = self.rng.randint(60, 3600 * 24 * 3)  # 1min – 3d after filing
            docs.append({
                "doc_type":    doc_type,
                "filename":    f"{doc_type}_{self.rng.getrandbits(32):08x}.pdf",
                "uploaded_at": filed_at + timedelta(seconds=offset),
                "verified":    self.rng.random() < 0.75,
            })
        return docs

    def _batch_breakdown(self, gross: float, mdr: float) -> list:
        """Split a batch across 1-4 rails; last row absorbs rounding slack."""
        rails = list(self.cfg.get("rails.weights", {}).keys()) or ["UPI", "DC", "CC"]
        k = self.rng.randint(1, min(4, len(rails)))
        picked = self.rng.sample(rails, k=k)
        weights = [self.rng.random() + 0.1 for _ in picked]
        total_w = sum(weights)
        rows = []
        remaining_gross = gross
        remaining_mdr   = mdr
        for i, (r, w) in enumerate(zip(picked, weights)):
            if i == len(picked) - 1:
                g = round(remaining_gross, 2)
                m = round(remaining_mdr, 2)
            else:
                frac = w / total_w
                g = round(gross * frac, 2)
                m = round(mdr * frac, 2)
                remaining_gross -= g
                remaining_mdr   -= m
            rows.append({
                "rail_type": r,
                "count":     self.rng.randint(1, 250),
                "gross":     g,
                "mdr":       m,
                "net":       round(g - m, 2),
            })
        return rows

    def _dispute_timeline(self, evidence_at: datetime,
                          resolved_at: Optional[datetime]) -> list:
        events = ["filed", "evidence_requested", "evidence_submitted"]
        if resolved_at is not None:
            events += ["review", "resolved"]
        else:
            events += self.rng.choice([["review"], ["review", "arbitration"]])
        step = timedelta(hours=self.rng.randint(6, 72))
        # Walk from before evidence_submitted forward through the sequence
        cursor = evidence_at - step * (len(events) - 1)
        rows = []
        for evt in events:
            rows.append({
                "event_type": evt,
                "event_time": cursor,
                "actor":      self.rng.choice(_DISPUTE_TIMELINE_ACTORS),
            })
            cursor = cursor + step
        return rows

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        n_attempts = self.rate("payment_attempts")
        n_events   = self.rate("payment_events")
        n_methods  = self.rate("payment_methods")
        n_refunds  = self.rate("refunds")
        n_cb       = self.rate("chargebacks")
        n_batches  = self.rate("settlement_batches")
        n_disp     = self.rate("disputes")
        now = self.now()

        order_pool    = self.state.pool("order_ids")
        user_pool     = self.state.pool("user_ids")
        merchant_pool = self.state.pool("merchant_ids")

        # -------- payment_attempts --------
        attempts_rows: List[dict] = []
        if order_pool and user_pool and merchant_pool:
            for _ in range(n_attempts):
                aid = f"pay_{self.state.next_id('attempt'):012d}"
                rail = self.pick_rail()
                # Log-normal INR ticket sizes, median ~₹450, tail to ~₹50k (§6.11)
                amount = round(max(20.0, self.rng.lognormvariate(6.1, 1.15)), 2)
                attempted_at = self.event_ts(spread_seconds=3600)
                status = self.pick_weighted(_STATUS_ATTEMPT, _STATUS_ATTEMPT_WEIGHTS)

                attempts_rows.append({
                    "attempt_id":       aid,
                    "order_id":         self.rng.choice(order_pool),
                    "user_id":          self.rng.choice(user_pool),
                    "merchant_id":      self.rng.choice(merchant_pool),
                    "amount":           amount,
                    "rail_type":        rail,
                    "attempted_at":     attempted_at,
                    "status":           status,
                    "risk_checks":      self._risk_checks(),
                    "routing_decision": self._routing_decision(rail),
                    "_event_ts":        self.maybe_late(attempted_at),
                    "_ingest_ts":       now,
                })

                # Internal pool of recent attempts (for events emission)
                self._append_pool("attempt_ids", aid)
                # Only successful attempts count as "payments" that can be
                # refunded / charged back
                if status == "success":
                    self._append_pool("payment_ids", aid)

        self._cap_pool("attempt_ids", _MAX_ATTEMPT_POOL)
        self._cap_pool("payment_ids", _MAX_PAYMENT_POOL)

        # -------- payment_events --------
        # Multi-event state-machine flow per attempt. Draws from recent
        # attempts (this tick + carryover from prior ticks).
        events_rows: List[dict] = []
        attempt_pool = self.state.pool("attempt_ids")
        rail_by_attempt   = {r["attempt_id"]: r["rail_type"] for r in attempts_rows}
        status_by_attempt = {r["attempt_id"]: r["status"]    for r in attempts_rows}

        emitted = 0
        while emitted < n_events and attempt_pool:
            aid = self.rng.choice(attempt_pool)
            rail = rail_by_attempt.get(aid) or self.pick_rail()
            status = status_by_attempt.get(aid) or self.pick_weighted(
                _STATUS_ATTEMPT, _STATUS_ATTEMPT_WEIGHTS)
            if status == "success":
                path = _EVENT_TYPES_SUCCESS_PATH
            elif status == "failed":
                path = _EVENT_TYPES_FAILED_PATH
            else:
                path = _EVENT_TYPES_PENDING_PATH
            # Clip to remaining budget so we don't overshoot n_events
            path = path[: min(len(path), n_events - emitted)]
            base_time = self.event_ts(spread_seconds=3600)
            step = timedelta(milliseconds=self.rng.randint(80, 900))
            for i, evt_type in enumerate(path):
                event_time = base_time + step * i
                events_rows.append({
                    "event_id":           f"pev_{self.state.next_id('event'):014d}",
                    "attempt_id":         aid,
                    "event_type":         evt_type,
                    "event_time":         event_time,
                    "event_attributes":   self._event_attributes(rail),
                    "processor_response": self._processor_response(rail, evt_type),
                    "_event_ts":          self.maybe_late(event_time),
                    "_ingest_ts":         now,
                })
                emitted += 1

        # -------- payment_methods --------
        methods_rows: List[dict] = []
        if user_pool:
            for _ in range(n_methods):
                method_type = self.pick_weighted(_METHOD_TYPES, _METHOD_TYPE_WEIGHTS)
                added_at = self.event_ts(spread_seconds=3600)
                methods_rows.append({
                    "method_id":      f"pm_{self.state.next_id('method'):012d}",
                    "user_id":        self.rng.choice(user_pool),
                    "method_type":    method_type,
                    "issuer":         self._issuer_for(method_type),
                    "added_at":       added_at,
                    "method_details": self._method_details(method_type),
                    "_event_ts":      self.maybe_late(added_at),
                    "_ingest_ts":     now,
                })

        # -------- refunds --------
        refunds_rows: List[dict] = []
        payment_pool = self.state.pool("payment_ids")
        if payment_pool:
            for _ in range(n_refunds):
                pid = self.rng.choice(payment_pool)
                refund_time = self.event_ts(spread_seconds=3600)
                refund_amount = round(self.rng.uniform(20.0, 15_000.0), 2)
                reason = self.pick_weighted(_REFUND_REASONS, _REFUND_REASON_WEIGHTS)
                metadata = json.dumps({
                    "requested_by": self.rng.choice(["customer", "merchant", "system"]),
                    "channel":      self.rng.choice(["app", "web", "support_ticket", "auto"]),
                    "sla_hours":    self.rng.choice([24, 48, 72, 168]),
                    "notes":        "" if self.rng.random() < 0.7 else self.faker.sentence(nb_words=6),
                    "partial":      self.rng.random() < 0.35,
                })
                refunds_rows.append({
                    "refund_id":           f"rfn_{self.state.next_id('refund'):012d}",
                    "original_payment_id": pid,
                    "refund_amount":       refund_amount,
                    "refund_time":         refund_time,
                    "reason":              reason,
                    "reason_metadata":     metadata,
                    "_event_ts":           self.maybe_late(refund_time),
                    "_ingest_ts":          now,
                })

        # -------- chargebacks --------
        chargebacks_rows: List[dict] = []
        if payment_pool:
            for _ in range(n_cb):
                pid = self.rng.choice(payment_pool)
                filed_at = self.event_ts(spread_seconds=3600)
                # ~55% resolved by ingest time; rest still open
                if self.rng.random() < 0.55:
                    resolved_at = filed_at + timedelta(days=self.rng.randint(1, 30))
                else:
                    resolved_at = None
                cid = f"cbk_{self.state.next_id('chargeback'):012d}"
                chargebacks_rows.append({
                    "chargeback_id":       cid,
                    "original_payment_id": pid,
                    "dispute_reason":      self.pick_weighted(
                        _CHARGEBACK_REASONS, _CHARGEBACK_REASON_WEIGHTS),
                    "filed_at":            filed_at,
                    "resolved_at":         resolved_at,
                    "evidence_docs":       self._evidence_docs(filed_at),
                    "_event_ts":           self.maybe_late(filed_at),
                    "_ingest_ts":          now,
                })
                self._append_pool("chargeback_ids", cid)

        self._cap_pool("chargeback_ids", _MAX_CHARGEBACK_POOL)

        # -------- settlement_batches --------
        batches_rows: List[dict] = []
        if merchant_pool:
            for _ in range(n_batches):
                mid = self.rng.choice(merchant_pool)
                batch_when = self.event_ts(spread_seconds=3600)
                batch_dt = batch_when.date()
                gross = round(self.rng.uniform(1_000.0, 500_000.0), 2)
                mdr_rate = self.rng.uniform(0.005, 0.03)   # 50–300 bps
                mdr = round(gross * mdr_rate, 2)
                net = round(gross - mdr, 2)
                batches_rows.append({
                    "batch_id":     f"stb_{self.state.next_id('batch'):012d}",
                    "merchant_id":  mid,
                    "batch_date":   batch_dt,
                    "gross_amount": gross,
                    "mdr_amount":   mdr,
                    "net_amount":   net,
                    "status":       self.pick_weighted(
                        _BATCH_STATUS, _BATCH_STATUS_WEIGHTS),
                    "breakdown":    self._batch_breakdown(gross, mdr),
                    "_event_ts":    self.maybe_late(batch_when),
                    "_ingest_ts":   now,
                })

        # -------- disputes --------
        disputes_rows: List[dict] = []
        cb_pool = self.state.pool("chargeback_ids")
        if cb_pool:
            for _ in range(n_disp):
                cid = self.rng.choice(cb_pool)
                evidence_at = self.event_ts(spread_seconds=3600)
                resolution = self.pick_weighted(
                    _DISPUTE_RESOLUTIONS, _DISPUTE_RESOLUTION_WEIGHTS)
                if resolution == "pending":
                    resolved_at = None
                else:
                    resolved_at = evidence_at + timedelta(days=self.rng.randint(3, 45))
                disputes_rows.append({
                    "dispute_id":            f"dsp_{self.state.next_id('dispute'):012d}",
                    "chargeback_id":         cid,
                    "evidence_submitted_at": evidence_at,
                    "resolution":            resolution,
                    "resolved_at":           resolved_at,
                    "timeline":              self._dispute_timeline(evidence_at, resolved_at),
                    "_event_ts":             self.maybe_late(evidence_at),
                    "_ingest_ts":            now,
                })

        # -------- assemble --------
        return {
            "payment_attempts":   pa.Table.from_pylist(attempts_rows,    schema=_ATTEMPTS_SCHEMA),
            "payment_events":     pa.Table.from_pylist(events_rows,      schema=_EVENTS_SCHEMA),
            "payment_methods":    pa.Table.from_pylist(methods_rows,     schema=_METHODS_SCHEMA),
            "refunds":            pa.Table.from_pylist(refunds_rows,     schema=_REFUNDS_SCHEMA),
            "chargebacks":        pa.Table.from_pylist(chargebacks_rows, schema=_CHARGEBACKS_SCHEMA),
            "settlement_batches": pa.Table.from_pylist(batches_rows,     schema=_BATCHES_SCHEMA),
            "disputes":           pa.Table.from_pylist(disputes_rows,    schema=_DISPUTES_SCHEMA),
        }
