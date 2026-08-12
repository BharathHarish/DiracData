"""lending domain generator — 7 tables, the modeller's marquee domain (§6.6).

Tables:
  loan_applications    (STRUCT applicant_snapshot, JSON bureau_report)
  loans                (STRUCT risk_snapshot, LIST<STRUCT> installments — denormalised)
  loan_disbursements   (STRUCT payout_details)
  repayment_schedule   (flat — same data as loans.installments, intentional
                       redundancy per §6.10)
  repayments_actual    (STRUCT allocation — principal/interest/penalty split)
  delinquencies        (STRUCT aging_buckets — daily snapshot per active loan)
  credit_bureau_pulls  (JSON report_payload)

Reads:  user_ids pool (cross-domain — tick emits nothing here if empty).
Writes: loan_ids, schedule_ids pools (for cross-referencing downstream).

Time convention: application/disbursement/pull timestamps are backfilled across
the past year so the schedule pool immediately spans past-due and future EMIs;
this gives the modeller real 90-day EMI lookback signal from tick 1 rather
than waiting months of wall-clock time.

Payment realism: a weighted mix of on-time, late (up to 60d), and very-late
(60-120d) repayments with per-behavior paid_amount / allocation shape so the
delinquency / DPD queries the modeller runs have training signal.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- STRUCT + LIST types (explicit — nested shapes matter for the modeller) ----

_APPLICANT_SNAPSHOT = pa.struct([
    ("monthly_income",       pa.float64()),
    ("employment_type",      pa.string()),
    ("current_credit_score", pa.int32()),
    ("existing_emi_burden",  pa.float64()),
])

_RISK_SNAPSHOT = pa.struct([
    ("score_at_origination", pa.int32()),
    ("dpd_current",          pa.int32()),
    ("dpd_max_ever",         pa.int32()),
    ("model_version",        pa.string()),
])

_INSTALLMENT_STRUCT = pa.struct([
    ("installment_no",  pa.int32()),
    ("due_date",        pa.timestamp("us", tz="UTC")),
    ("principal_due",   pa.float64()),
    ("interest_due",    pa.float64()),
    ("emi_amount",      pa.float64()),
])

_PAYOUT_DETAILS = pa.struct([
    ("bank_account",     pa.string()),
    ("ifsc",             pa.string()),
    ("upi_vpa",          pa.string()),
    ("confirmation_ref", pa.string()),
])

_ALLOCATION = pa.struct([
    ("principal_paid", pa.float64()),
    ("interest_paid",  pa.float64()),
    ("penalty_paid",   pa.float64()),
])

_AGING_BUCKETS = pa.struct([
    ("b_0_30",    pa.int32()),
    ("b_30_60",   pa.int32()),
    ("b_60_90",   pa.int32()),
    ("b_90_plus", pa.int32()),
])


# ---- table schemas ----

_LOAN_APPS_SCHEMA = pa.schema([
    ("app_id",             pa.string()),
    ("user_id",            pa.string()),
    ("requested_amount",   pa.float64()),
    ("tenure_months",      pa.int32()),
    ("applied_at",         pa.timestamp("us", tz="UTC")),
    ("status",             pa.string()),
    ("applicant_snapshot", _APPLICANT_SNAPSHOT),
    ("bureau_report",      pa.string()),  # JSON as string
    ("_event_ts",          pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",         pa.timestamp("us", tz="UTC")),
])

_LOANS_SCHEMA = pa.schema([
    ("loan_id",       pa.string()),
    ("app_id",        pa.string()),
    ("user_id",       pa.string()),
    ("principal",     pa.float64()),
    ("interest_rate", pa.float64()),
    ("tenure_months", pa.int32()),
    ("disbursed_at",  pa.timestamp("us", tz="UTC")),
    ("status",        pa.string()),
    ("risk_snapshot", _RISK_SNAPSHOT),
    ("installments",  pa.list_(_INSTALLMENT_STRUCT)),
    ("_event_ts",     pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",    pa.timestamp("us", tz="UTC")),
])

_DISBURSEMENTS_SCHEMA = pa.schema([
    ("disbursement_id",     pa.string()),
    ("loan_id",             pa.string()),
    ("disbursed_at",        pa.timestamp("us", tz="UTC")),
    ("amount",              pa.float64()),
    ("disbursement_method", pa.string()),
    ("payout_details",      _PAYOUT_DETAILS),
    ("_event_ts",           pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",          pa.timestamp("us", tz="UTC")),
])

_SCHEDULE_SCHEMA = pa.schema([
    ("schedule_id",    pa.string()),
    ("loan_id",        pa.string()),
    ("installment_no", pa.int32()),
    ("due_date",       pa.timestamp("us", tz="UTC")),
    ("principal_due",  pa.float64()),
    ("interest_due",   pa.float64()),
    ("emi_amount",     pa.float64()),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_REPAYMENTS_SCHEMA = pa.schema([
    ("repayment_id",   pa.string()),
    ("loan_id",        pa.string()),
    ("schedule_id",    pa.string()),
    ("paid_at",        pa.timestamp("us", tz="UTC")),
    ("paid_amount",    pa.float64()),
    ("payment_method", pa.string()),
    ("allocation",     _ALLOCATION),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_DELINQUENCIES_SCHEMA = pa.schema([
    ("delinquency_id", pa.string()),
    ("loan_id",        pa.string()),
    ("dpd_bucket",     pa.string()),
    ("snapshot_date",  pa.timestamp("us", tz="UTC")),
    ("outstanding",    pa.float64()),
    ("aging_buckets",  _AGING_BUCKETS),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_BUREAU_PULLS_SCHEMA = pa.schema([
    ("pull_id",        pa.string()),
    ("user_id",        pa.string()),
    ("pulled_at",      pa.timestamp("us", tz="UTC")),
    ("credit_score",   pa.int32()),
    ("bureau",         pa.string()),
    ("report_payload", pa.string()),  # JSON
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])


_APP_STATUSES        = ["submitted", "approved", "rejected", "withdrawn"]
_APP_STATUS_WEIGHTS  = [20, 60, 15, 5]
_LOAN_STATUSES       = ["active", "closed", "written_off"]
_LOAN_STATUS_WEIGHTS = [85, 12, 3]
_DISBURSE_METHODS    = ["bank_transfer", "wallet", "check"]
_DISBURSE_WEIGHTS    = [80, 15, 5]
_PAYMENT_METHODS     = ["auto_debit", "manual_upi", "manual_netbanking"]
_PAYMENT_WEIGHTS     = [60, 30, 10]
_EMPLOYMENT_TYPES    = ["salaried", "self_employed", "business_owner", "freelancer", "student"]
_EMPLOYMENT_WEIGHTS  = [55, 20, 12, 10, 3]
_BUREAUS             = ["cibil", "experian", "equifax", "crif"]
_BUREAU_WEIGHTS      = [50, 25, 15, 10]
_DPD_BUCKETS         = ["0-30", "30-60", "60-90", "90+"]
_DPD_BUCKET_WEIGHTS  = [70, 15, 10, 5]


def _emi(principal: float, annual_rate: float, tenure_months: int) -> float:
    """Standard EMI: P * r * (1+r)^n / ((1+r)^n - 1)."""
    r = annual_rate / 12.0
    if r <= 0 or tenure_months <= 0:
        return principal / max(1, tenure_months)
    factor = math.pow(1.0 + r, tenure_months)
    return principal * r * factor / (factor - 1.0)


class LendingGenerator(BaseGenerator):
    domain = "lending"
    tables = [
        "loan_applications",
        "loans",
        "loan_disbursements",
        "repayment_schedule",
        "repayments_actual",
        "delinquencies",
        "credit_bureau_pulls",
    ]

    _LOAN_POOL_CAP     = 5_000
    _SCHEDULE_POOL_CAP = 50_000

    # ---- helpers ----

    def _requested_amount(self) -> float:
        """log-normal INR, median ~₹50k, tail to ~₹5L."""
        v = self.rng.lognormvariate(math.log(50_000), 0.9)
        return round(min(v, 5_000_000.0), 2)

    def _credit_score(self) -> int:
        s = int(self.rng.gauss(720, 80))
        return max(300, min(900, s))

    def _tenure(self) -> int:
        return self.pick_weighted([6, 12, 18, 24, 36], [10, 30, 25, 25, 10])

    def _applied_at(self) -> datetime:
        """Backfill: spread apps across the past year so schedules/repayments
        immediately have real distribution (past-due + future EMIs)."""
        days = self.rng.randint(0, 365)
        secs = self.rng.randint(0, 86_400)
        return self.now() - timedelta(days=days, seconds=secs)

    def _bureau_report_short(self, score: int) -> str:
        return json.dumps({
            "score":           score,
            "bureau":          self.rng.choice(_BUREAUS),
            "trades_active":   self.rng.randint(0, 8),
            "trades_closed":   self.rng.randint(0, 20),
            "enquiries_6m":    self.rng.randint(0, 10),
            "utilization_pct": round(self.rng.uniform(0.0, 1.2), 3),
            "delinq_flags":    [] if self.rng.random() < 0.85 else ["dpd30_last_12m"],
        })

    def _bureau_report_full(self, score: int) -> str:
        return json.dumps({
            "score":       score,
            "reported_at": utc_now().isoformat(),
            "trade_lines": [
                {
                    "type":        self.rng.choice(["cc", "personal_loan", "home_loan", "auto_loan"]),
                    "opened_year": self.rng.randint(2015, 2026),
                    "balance":     round(self.rng.uniform(0, 500_000), 2),
                    "status":      self.rng.choice(["current", "closed", "dpd30", "dpd60"]),
                }
                for _ in range(self.rng.randint(0, 5))
            ],
            "enquiries_6m":  self.rng.randint(0, 12),
            "score_reasons": self.rng.sample(
                ["high_util", "new_credit", "short_history", "late_pay", "thin_file", "stable"],
                k=self.rng.randint(1, 3)),
        })

    def _applicant_snapshot(self) -> dict:
        return {
            "monthly_income":       round(self.rng.lognormvariate(math.log(60_000), 0.7), 2),
            "employment_type":      self.pick_weighted(_EMPLOYMENT_TYPES, _EMPLOYMENT_WEIGHTS),
            "current_credit_score": self._credit_score(),
            "existing_emi_burden":  round(self.rng.uniform(0, 40_000), 2),
        }

    def _payout_details(self) -> dict:
        return {
            "bank_account":     f"{self.rng.randint(10_000_000, 99_999_999_999)}",
            "ifsc":             f"HDFC0{self.rng.randint(100000, 999999)}",
            "upi_vpa":          f"user{self.rng.randint(1000, 999_999)}@upi",
            "confirmation_ref": f"REF{self.rng.getrandbits(48):012x}".upper(),
        }

    def _build_installments(self, loan_id: str, principal: float,
                            annual_rate: float, tenure: int,
                            disbursed_at: datetime) -> tuple[list[dict], list[dict]]:
        """Amortize a loan into `tenure` monthly installments.

        Returns (inline_structs, flat_rows) — same content in two shapes:
          inline_structs -> loans.installments (LIST<STRUCT>)
          flat_rows      -> repayment_schedule (per-row) with schedule_id assigned
        """
        emi = round(_emi(principal, annual_rate, tenure), 2)
        r = annual_rate / 12.0
        balance = principal
        inline: List[dict] = []
        flat:   List[dict] = []
        for i in range(1, tenure + 1):
            interest = round(balance * r, 2)
            principal_due = round(emi - interest, 2)
            balance = max(0.0, balance - principal_due)
            due = disbursed_at + timedelta(days=30 * i)
            inline.append({
                "installment_no": i,
                "due_date":       due,
                "principal_due":  principal_due,
                "interest_due":   interest,
                "emi_amount":     emi,
            })
            sid = f"sch_{self.state.next_id('lending_sch'):012d}"
            flat.append({
                "schedule_id":    sid,
                "loan_id":        loan_id,
                "installment_no": i,
                "due_date":       due,
                "principal_due":  principal_due,
                "interest_due":   interest,
                "emi_amount":     emi,
            })
        return inline, flat

    def _register_loan(self, loan_id: str, disbursed_at: datetime,
                       tenure: int, principal: float, emi: float,
                       interest_rate: float) -> None:
        pool_ids  = self.state.pool("loan_ids")
        pool_meta = self.state.pool("_loan_meta")
        pool_ids.append(loan_id)
        pool_meta.append({
            "loan_id":       loan_id,
            "disbursed_at":  disbursed_at.isoformat(),
            "tenure_months": tenure,
            "principal":     principal,
            "emi_amount":    emi,
            "interest_rate": interest_rate,
        })
        cap = self._LOAN_POOL_CAP
        if len(pool_ids) > cap:
            self.state.entity_pools["loan_ids"]   = pool_ids[-cap:]
            self.state.entity_pools["_loan_meta"] = pool_meta[-cap:]

    def _register_schedule(self, flat_rows: List[dict]) -> None:
        pool_ids  = self.state.pool("schedule_ids")
        pool_meta = self.state.pool("_schedule_meta")
        for row in flat_rows:
            pool_ids.append(row["schedule_id"])
            pool_meta.append({
                "schedule_id":   row["schedule_id"],
                "loan_id":       row["loan_id"],
                "due_date":      row["due_date"].isoformat(),
                "emi_amount":    row["emi_amount"],
                "principal_due": row["principal_due"],
                "interest_due":  row["interest_due"],
            })
        cap = self._SCHEDULE_POOL_CAP
        if len(pool_ids) > cap:
            self.state.entity_pools["schedule_ids"]   = pool_ids[-cap:]
            self.state.entity_pools["_schedule_meta"] = pool_meta[-cap:]

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        now        = self.now()
        n_apps     = self.rate("loan_applications")
        n_loans    = self.rate("loans")
        n_disburse = self.rate("loan_disbursements")
        n_repay    = self.rate("repayments_actual")
        n_delinq   = self.rate("delinquencies")
        n_pulls    = self.rate("credit_bureau_pulls")

        user_pool = self.state.pool("user_ids")

        # ---- loan_applications ----
        apps_rows: List[dict] = []
        approved_this_tick: List[dict] = []
        for _ in range(n_apps):
            if not user_pool:
                break
            uid       = self.rng.choice(user_pool)
            requested = self._requested_amount()
            tenure    = self._tenure()
            applied   = self._applied_at()
            status    = self.pick_weighted(_APP_STATUSES, _APP_STATUS_WEIGHTS)
            snap      = self._applicant_snapshot()
            app_id    = f"app_{self.state.next_id('lending_app'):010d}"
            apps_rows.append({
                "app_id":             app_id,
                "user_id":            uid,
                "requested_amount":   requested,
                "tenure_months":      tenure,
                "applied_at":         applied,
                "status":             status,
                "applicant_snapshot": snap,
                "bureau_report":      self._bureau_report_short(snap["current_credit_score"]),
                "_event_ts":          self.maybe_late(applied),
                "_ingest_ts":         now,
            })
            if status == "approved":
                approved_this_tick.append({
                    "app_id":       app_id,
                    "user_id":      uid,
                    "requested":    requested,
                    "tenure":       tenure,
                    "applied_at":   applied,
                    "credit_score": snap["current_credit_score"],
                })

        # ---- loans (only for approved apps this tick — see spec §6.6) ----
        loans_rows:    List[dict] = []
        schedule_rows: List[dict] = []
        new_disb_meta: List[dict] = []
        n_new_loans = min(n_loans, len(approved_this_tick))
        for i in range(n_new_loans):
            app           = approved_this_tick[i]
            loan_id       = f"loan_{self.state.next_id('lending_loan'):010d}"
            principal     = round(app["requested"] * self.rng.uniform(0.85, 1.0), 2)
            interest_rate = round(self.rng.uniform(0.10, 0.24), 4)
            tenure        = app["tenure"]
            disbursed_at  = app["applied_at"] + timedelta(
                minutes=self.rng.randint(30, 3 * 24 * 60))
            emi = round(_emi(principal, interest_rate, tenure), 2)
            inline, flat = self._build_installments(
                loan_id, principal, interest_rate, tenure, disbursed_at)
            status = self.pick_weighted(_LOAN_STATUSES, _LOAN_STATUS_WEIGHTS)
            if status == "written_off":
                dpd_current = self.rng.randint(90, 180)
                dpd_max     = max(dpd_current, self.rng.randint(90, 240))
            elif status == "closed":
                dpd_current = 0
                dpd_max     = self.rng.randint(0, 60)
            else:  # active
                dpd_current = self.rng.choices([0, self.rng.randint(1, 30)], weights=[85, 15])[0]
                dpd_max     = max(dpd_current, self.rng.randint(0, 60))
            loans_rows.append({
                "loan_id":       loan_id,
                "app_id":        app["app_id"],
                "user_id":       app["user_id"],
                "principal":     principal,
                "interest_rate": interest_rate,
                "tenure_months": tenure,
                "disbursed_at":  disbursed_at,
                "status":        status,
                "risk_snapshot": {
                    "score_at_origination": app["credit_score"],
                    "dpd_current":          dpd_current,
                    "dpd_max_ever":         dpd_max,
                    "model_version":        self.rng.choice(["credit_v1", "credit_v2", "credit_v3"]),
                },
                "installments":  inline,
                "_event_ts":     self.maybe_late(disbursed_at),
                "_ingest_ts":    now,
            })
            for row in flat:
                schedule_rows.append({
                    **row,
                    "_event_ts":  self.maybe_late(row["due_date"]),
                    "_ingest_ts": now,
                })
            self._register_loan(loan_id, disbursed_at, tenure, principal, emi, interest_rate)
            self._register_schedule(flat)
            new_disb_meta.append({
                "loan_id":      loan_id,
                "disbursed_at": disbursed_at,
                "amount":       principal,
            })

        # ---- loan_disbursements (~1:1 with new loans this tick) ----
        disburse_rows: List[dict] = []
        for i in range(min(n_disburse, len(new_disb_meta))):
            meta = new_disb_meta[i]
            disburse_rows.append({
                "disbursement_id":     f"dsb_{self.state.next_id('lending_disb'):010d}",
                "loan_id":             meta["loan_id"],
                "disbursed_at":        meta["disbursed_at"],
                "amount":              meta["amount"],
                "disbursement_method": self.pick_weighted(_DISBURSE_METHODS, _DISBURSE_WEIGHTS),
                "payout_details":      self._payout_details(),
                "_event_ts":           self.maybe_late(meta["disbursed_at"]),
                "_ingest_ts":          now,
            })

        # ---- repayments_actual ----
        # Pick from schedule pool; only accept past-due schedules so paid_at is
        # realistic (<= now). Behavior mix drives days-late distribution — this
        # is the training signal for the 90-day EMI lookback pattern.
        repay_rows: List[dict] = []
        sched_pool = self.state.pool("_schedule_meta")
        past_due = [e for e in sched_pool
                    if datetime.fromisoformat(e["due_date"]) <= now]
        for _ in range(n_repay):
            if not past_due:
                break
            entry         = self.rng.choice(past_due)
            due_dt        = datetime.fromisoformat(entry["due_date"])
            emi           = float(entry["emi_amount"])
            principal_due = float(entry["principal_due"])
            interest_due  = float(entry["interest_due"])

            behavior = self.pick_weighted(["on_time", "late", "very_late"], [70, 20, 10])
            if behavior == "on_time":
                offset  = timedelta(days=self.rng.randint(-3, 1))
                paid    = round(emi * self.rng.uniform(0.98, 1.02), 2)
                penalty = 0.0
                p_paid  = round(min(principal_due, paid), 2)
                i_paid  = round(max(0.0, paid - p_paid), 2)
            elif behavior == "late":
                offset  = timedelta(days=self.rng.randint(5, 60))
                paid    = round(emi * self.rng.uniform(0.95, 1.05), 2)
                penalty = round(emi * self.rng.uniform(0.02, 0.06), 2)
                p_paid  = round(min(principal_due, max(0.0, paid - penalty)), 2)
                i_paid  = round(max(0.0, paid - penalty - p_paid), 2)
            else:  # very_late / partial
                offset  = timedelta(days=self.rng.randint(60, 120))
                paid    = round(emi * self.rng.uniform(0.3, 0.9), 2)
                penalty = round(emi * self.rng.uniform(0.05, 0.10), 2)
                i_paid  = round(min(interest_due, paid), 2)
                p_paid  = round(max(0.0, paid - i_paid - penalty), 2)

            paid_at = due_dt + offset
            # Clamp: keep event ts realistic (<= now).
            if paid_at > now:
                paid_at = now - timedelta(minutes=self.rng.randint(1, 60))

            repay_rows.append({
                "repayment_id":   f"rpy_{self.state.next_id('lending_rpy'):012d}",
                "loan_id":        entry["loan_id"],
                "schedule_id":    entry["schedule_id"],
                "paid_at":        paid_at,
                "paid_amount":    round(paid, 2),
                "payment_method": self.pick_weighted(_PAYMENT_METHODS, _PAYMENT_WEIGHTS),
                "allocation": {
                    "principal_paid": p_paid,
                    "interest_paid":  i_paid,
                    "penalty_paid":   penalty,
                },
                "_event_ts":  self.maybe_late(paid_at),
                "_ingest_ts": now,
            })

        # ---- delinquencies (daily snapshot per active loan) ----
        delinq_rows: List[dict] = []
        loan_meta_pool = self.state.pool("_loan_meta")
        for _ in range(n_delinq):
            if not loan_meta_pool:
                break
            lm = self.rng.choice(loan_meta_pool)
            # Spread snapshot_date across last 30 days for a real history.
            snap_days = self.rng.randint(0, 30)
            snap_ts = (now - timedelta(days=snap_days)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            outstanding = round(float(lm["principal"]) * self.rng.uniform(0.05, 0.95), 2)
            bucket = self.pick_weighted(_DPD_BUCKETS, _DPD_BUCKET_WEIGHTS)
            # Weight aging bucket counts by the dominant bucket for internal
            # consistency with dpd_bucket.
            b_0  = self.rng.randint(1, 5) if bucket == "0-30"  else self.rng.randint(0, 1)
            b_30 = self.rng.randint(1, 3) if bucket == "30-60" else self.rng.randint(0, 1)
            b_60 = self.rng.randint(1, 3) if bucket == "60-90" else self.rng.randint(0, 1)
            b_90 = self.rng.randint(1, 5) if bucket == "90+"   else 0
            delinq_rows.append({
                "delinquency_id": f"del_{self.state.next_id('lending_del'):012d}",
                "loan_id":        lm["loan_id"],
                "dpd_bucket":     bucket,
                "snapshot_date":  snap_ts,
                "outstanding":    outstanding,
                "aging_buckets": {
                    "b_0_30":    b_0,
                    "b_30_60":   b_30,
                    "b_60_90":   b_60,
                    "b_90_plus": b_90,
                },
                "_event_ts":  self.maybe_late(snap_ts),
                "_ingest_ts": now,
            })

        # ---- credit_bureau_pulls ----
        pulls_rows: List[dict] = []
        for _ in range(n_pulls):
            if not user_pool:
                break
            uid       = self.rng.choice(user_pool)
            pulled_at = self._applied_at()  # spread over past year
            score     = self._credit_score()
            pulls_rows.append({
                "pull_id":        f"pul_{self.state.next_id('lending_pul'):012d}",
                "user_id":        uid,
                "pulled_at":      pulled_at,
                "credit_score":   score,
                "bureau":         self.pick_weighted(_BUREAUS, _BUREAU_WEIGHTS),
                "report_payload": self._bureau_report_full(score),
                "_event_ts":      self.maybe_late(pulled_at),
                "_ingest_ts":     now,
            })

        # ---- build pa.Tables with explicit schemas ----
        return {
            "loan_applications":   pa.Table.from_pylist(apps_rows,     schema=_LOAN_APPS_SCHEMA),
            "loans":               pa.Table.from_pylist(loans_rows,    schema=_LOANS_SCHEMA),
            "loan_disbursements":  pa.Table.from_pylist(disburse_rows, schema=_DISBURSEMENTS_SCHEMA),
            "repayment_schedule":  pa.Table.from_pylist(schedule_rows, schema=_SCHEDULE_SCHEMA),
            "repayments_actual":   pa.Table.from_pylist(repay_rows,    schema=_REPAYMENTS_SCHEMA),
            "delinquencies":       pa.Table.from_pylist(delinq_rows,   schema=_DELINQUENCIES_SCHEMA),
            "credit_bureau_pulls": pa.Table.from_pylist(pulls_rows,    schema=_BUREAU_PULLS_SCHEMA),
        }
