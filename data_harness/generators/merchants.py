"""merchants domain generator — 5 tables (§6.2).

Tables:
  merchants                  (500 cap; nested: contact_persons LIST<STRUCT>,
                              business_registration STRUCT)
  merchant_kyc               (append-only; STRUCT verification_result with flags LIST)
  merchant_settlement_config (SCD-ish; effective_from per merchant)
  merchant_pricing_plans     (per-plan; MAP<VARCHAR,DOUBLE> rail_overrides)
  merchant_category_map      (STATIC seed on first tick only, ~30 rows)
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- schemas (explicit — nested types matter for the modeller) ----

_CONTACT_PERSON_STRUCT = pa.struct([
    ("name",  pa.string()),
    ("role",  pa.string()),
    ("email", pa.string()),
    ("phone", pa.string()),
])

_BUSINESS_REG_STRUCT = pa.struct([
    ("reg_type",      pa.string()),
    ("reg_number",    pa.string()),
    ("registered_at", pa.timestamp("us", tz="UTC")),
    ("address",       pa.string()),
])

_VERIFICATION_RESULT_STRUCT = pa.struct([
    ("reviewer",       pa.string()),
    ("score",          pa.float64()),
    ("ocr_confidence", pa.float64()),
    ("flags",          pa.list_(pa.string())),
])


_MERCHANTS_SCHEMA = pa.schema([
    ("merchant_id",           pa.string()),
    ("business_name",         pa.string()),
    ("mcc_code",              pa.string()),
    ("onboarded_at",          pa.timestamp("us", tz="UTC")),
    ("status",                pa.string()),
    ("tier",                  pa.string()),
    ("state_code",            pa.string()),
    ("city",                  pa.string()),
    ("gstin",                 pa.string()),
    ("pan",                   pa.string()),
    ("contact_persons",       pa.list_(_CONTACT_PERSON_STRUCT)),
    ("business_registration", _BUSINESS_REG_STRUCT),
    ("_event_ts",             pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",            pa.timestamp("us", tz="UTC")),
])

_MERCHANT_KYC_SCHEMA = pa.schema([
    ("kyc_event_id",        pa.string()),
    ("merchant_id",         pa.string()),
    ("event_type",          pa.string()),  # submitted | approved | rejected | resubmitted
    ("event_time",          pa.timestamp("us", tz="UTC")),
    ("doc_type",            pa.string()),
    ("verification_result", _VERIFICATION_RESULT_STRUCT),
])

_MERCHANT_SETTLEMENT_SCHEMA = pa.schema([
    ("merchant_id",          pa.string()),
    ("settlement_speed",     pa.string()),  # T+0 | T+1 | T+2
    ("settlement_bank_ref",  pa.string()),
    ("effective_from",       pa.timestamp("us", tz="UTC")),
    ("_event_ts",            pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",           pa.timestamp("us", tz="UTC")),
])

_MERCHANT_PRICING_SCHEMA = pa.schema([
    ("plan_id",         pa.string()),
    ("merchant_id",     pa.string()),
    ("plan_name",       pa.string()),
    ("mdr_bps",         pa.int32()),
    ("effective_from",  pa.timestamp("us", tz="UTC")),
    ("effective_to",    pa.timestamp("us", tz="UTC")),  # nullable
    ("rail_overrides",  pa.map_(pa.string(), pa.float64())),
    ("_event_ts",       pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",      pa.timestamp("us", tz="UTC")),
])

_MERCHANT_CATEGORY_MAP_SCHEMA = pa.schema([
    ("mcc_code",       pa.string()),
    ("category_name",  pa.string()),
    ("category_group", pa.string()),
])


_STATUSES = ["active", "dormant", "churned"]
_STATUS_WEIGHTS = [80, 15, 5]
_TIERS = ["small", "mid", "large", "enterprise"]
_TIER_WEIGHTS = [60, 25, 12, 3]
_SETTLEMENT_SPEEDS = ["T+0", "T+1", "T+2"]
_SETTLEMENT_SPEED_WEIGHTS = [10, 70, 20]
_PLAN_NAMES = ["starter", "growth", "scale", "enterprise", "custom"]
_KYC_EVENT_TYPES = ["submitted", "approved", "rejected", "resubmitted"]
_KYC_EVENT_WEIGHTS = [30, 55, 8, 7]
_KYC_DOC_TYPES = ["gstin_certificate", "pan_card", "bank_proof",
                  "shop_establishment", "moa", "aoa"]
_REG_TYPES = ["private_limited", "llp", "sole_proprietorship",
              "partnership", "public_limited"]
_REG_TYPE_WEIGHTS = [35, 20, 30, 10, 5]
_CONTACT_ROLES = ["founder", "director", "cfo", "operations_head",
                  "accountant", "compliance"]
_VERIFICATION_FLAGS = ["low_resolution", "signature_mismatch",
                       "address_mismatch", "expired_doc", "watermark_missing"]
_RAILS = ["UPI", "DC", "CC", "NB", "WALLET", "IMPS", "NEFT"]

_MCC_CATALOG = [
    ("5411", "Grocery Stores",         "Retail"),
    ("5541", "Service Stations",       "Fuel"),
    ("5812", "Restaurants",            "Food"),
    ("5814", "Fast Food",              "Food"),
    ("5462", "Bakeries",               "Food"),
    ("5813", "Bars",                   "Food"),
    ("5311", "Department Stores",      "Retail"),
    ("5691", "Apparel Stores",         "Retail"),
    ("5651", "Family Clothing",        "Retail"),
    ("5732", "Electronics",            "Retail"),
    ("5945", "Toy Stores",             "Retail"),
    ("5947", "Gifts",                  "Retail"),
    ("5192", "Books",                  "Retail"),
    ("5964", "Direct Marketing",       "Retail"),
    ("5999", "Miscellaneous Retail",   "Retail"),
    ("5921", "Package Stores",         "Retail"),
    ("4511", "Airlines",               "Travel"),
    ("7011", "Hotels",                 "Travel"),
    ("4121", "Taxis",                  "Travel"),
    ("4111", "Local Transit",          "Travel"),
    ("8062", "Hospitals",              "Healthcare"),
    ("8021", "Dentists",               "Healthcare"),
    ("8011", "Doctors",                "Healthcare"),
    ("5912", "Pharmacies",             "Healthcare"),
    ("8299", "Schools",                "Education"),
    ("7832", "Motion Pictures",        "Entertainment"),
    ("7995", "Betting",                "Entertainment"),
    ("7999", "Recreation",             "Entertainment"),
    ("4816", "Digital Goods",          "Digital"),
    ("4899", "Cable Streaming",        "Digital"),
]
_MCC_CODES = [row[0] for row in _MCC_CATALOG]


class MerchantsGenerator(BaseGenerator):
    domain = "merchants"
    tables = ["merchants", "merchant_kyc", "merchant_settlement_config",
              "merchant_pricing_plans", "merchant_category_map"]

    _LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    _DIGITS = "0123456789"

    def _gen_pan(self) -> str:
        alpha5 = "".join(self.rng.choice(self._LETTERS) for _ in range(5))
        num4   = "".join(self.rng.choice(self._DIGITS)  for _ in range(4))
        last   = self.rng.choice(self._LETTERS)
        return f"{alpha5}{num4}{last}"

    def _gen_gstin(self, pan: str) -> str:
        state_num = f"{self.rng.randint(1, 37):02d}"
        entity    = str(self.rng.randint(1, 9))
        check     = self.rng.choice(self._LETTERS + self._DIGITS)
        return f"{state_num}{pan}{entity}Z{check}"

    def _pick_contact(self) -> dict:
        return {
            "name":  self.faker.name(),
            "role":  self.rng.choice(_CONTACT_ROLES),
            "email": self.faker.company_email(),
            "phone": self.faker.msisdn(),
        }

    def _pick_registration(self, when: datetime) -> dict:
        reg_type = self.pick_weighted(_REG_TYPES, _REG_TYPE_WEIGHTS)
        reg_num = f"REG-{self.state.next_id('reg_number'):010d}"
        # registered anywhere from 30 days to 5 years before onboarding
        days_back = self.rng.randint(30, 5 * 365)
        return {
            "reg_type":      reg_type,
            "reg_number":    reg_num,
            "registered_at": when - timedelta(days=days_back),
            "address":       self.faker.address().replace("\n", ", "),
        }

    def _pick_verification_result(self) -> dict:
        n_flags = self.rng.choices([0, 1, 2], weights=[80, 15, 5], k=1)[0]
        flags = self.rng.sample(_VERIFICATION_FLAGS, k=n_flags) if n_flags else []
        return {
            "reviewer":       f"kyc_agent_{self.rng.randint(1, 50)}",
            "score":          round(self.rng.random(), 3),
            "ocr_confidence": round(self.rng.uniform(0.7, 1.0), 3),
            "flags":          flags,
        }

    def _pick_rail_overrides(self) -> dict:
        n = self.rng.choices([0, 1, 2, 3], weights=[40, 30, 20, 10], k=1)[0]
        rails = self.rng.sample(_RAILS, k=n) if n else []
        return {rail: round(self.rng.uniform(80.0, 400.0), 2) for rail in rails}

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        cap        = int(self.cfg.get("entity_caps.merchants", 500))
        pool       = self.state.pool("merchant_ids")
        n_want     = self.rate("merchants")
        n_new      = max(0, min(n_want, cap - len(pool)))
        n_kyc      = self.rate("merchant_kyc")
        n_plans    = self.rate("merchant_pricing_plans")
        now        = self.now()

        # ---- merchants ----
        merchants_rows: List[dict] = []
        for _ in range(n_new):
            mid = f"mer_{self.state.next_id('merchant'):08d}"
            pool.append(mid)
            state    = self.pick_state()
            onboard  = self.event_ts(spread_seconds=3600)
            pan      = self._gen_pan()
            gstin    = self._gen_gstin(pan)
            n_contacts = self.rng.choices([1, 2, 3], weights=[55, 35, 10], k=1)[0]
            contacts = [self._pick_contact() for _ in range(n_contacts)]

            merchants_rows.append({
                "merchant_id":           mid,
                "business_name":         self.faker.company(),
                "mcc_code":              self.rng.choice(_MCC_CODES),
                "onboarded_at":          onboard,
                "status":                self.pick_weighted(_STATUSES, _STATUS_WEIGHTS),
                "tier":                  self.pick_weighted(_TIERS, _TIER_WEIGHTS),
                "state_code":            state["code"],
                "city":                  self.faker.city(),
                "gstin":                 gstin,
                "pan":                   pan,
                "contact_persons":       contacts,
                "business_registration": self._pick_registration(onboard),
                "_event_ts":             self.maybe_late(onboard),
                "_ingest_ts":            now,
            })

        # ---- merchant_kyc ----
        kyc_rows: List[dict] = []
        for _ in range(n_kyc):
            if not pool:
                break
            mid = self.rng.choice(pool)
            when = self.event_ts(spread_seconds=3600)
            kyc_rows.append({
                "kyc_event_id":        f"mkyc_{self.state.next_id('merchant_kyc'):012d}",
                "merchant_id":         mid,
                "event_type":          self.pick_weighted(_KYC_EVENT_TYPES, _KYC_EVENT_WEIGHTS),
                "event_time":          when,
                "doc_type":            self.rng.choice(_KYC_DOC_TYPES),
                "verification_result": self._pick_verification_result(),
            })

        # ---- merchant_settlement_config ----
        # Emit config for newly onboarded merchants + occasional updates to existing.
        settlement_rows: List[dict] = []
        for row in merchants_rows:
            settlement_rows.append({
                "merchant_id":         row["merchant_id"],
                "settlement_speed":    self.pick_weighted(_SETTLEMENT_SPEEDS, _SETTLEMENT_SPEED_WEIGHTS),
                "settlement_bank_ref": f"BANK{self.rng.randint(1000, 9999)}-{self.rng.randint(100000, 999999)}",
                "effective_from":      row["onboarded_at"],
                "_event_ts":           row["_event_ts"],
                "_ingest_ts":          now,
            })
        n_updates = self.rng.randint(0, 2)
        for _ in range(n_updates):
            if not pool:
                break
            mid = self.rng.choice(pool)
            when = self.event_ts(spread_seconds=3600)
            settlement_rows.append({
                "merchant_id":         mid,
                "settlement_speed":    self.pick_weighted(_SETTLEMENT_SPEEDS, _SETTLEMENT_SPEED_WEIGHTS),
                "settlement_bank_ref": f"BANK{self.rng.randint(1000, 9999)}-{self.rng.randint(100000, 999999)}",
                "effective_from":      when,
                "_event_ts":           self.maybe_late(when),
                "_ingest_ts":          now,
            })

        # ---- merchant_pricing_plans ----
        pricing_rows: List[dict] = []
        for _ in range(n_plans):
            if not pool:
                break
            mid = self.rng.choice(pool)
            when = self.event_ts(spread_seconds=3600)
            has_end = self.rng.random() < 0.15
            end_ts = when + timedelta(days=self.rng.randint(30, 365)) if has_end else None
            pricing_rows.append({
                "plan_id":         f"plan_{self.state.next_id('pricing_plan'):010d}",
                "merchant_id":     mid,
                "plan_name":       self.rng.choice(_PLAN_NAMES),
                "mdr_bps":         self.rng.randint(150, 350),
                "effective_from":  when,
                "effective_to":    end_ts,
                "rail_overrides":  self._pick_rail_overrides(),
                "_event_ts":       self.maybe_late(when),
                "_ingest_ts":      now,
            })

        # ---- merchant_category_map (STATIC — first tick only) ----
        category_rows: List[dict] = []
        if not self.state.counters.get("mcc_seeded"):
            for code, name, group in _MCC_CATALOG:
                category_rows.append({
                    "mcc_code":       code,
                    "category_name":  name,
                    "category_group": group,
                })
            self.state.counters["mcc_seeded"] = 1

        # ---- build pa.Tables with explicit schemas ----
        return {
            "merchants":                  pa.Table.from_pylist(merchants_rows,  schema=_MERCHANTS_SCHEMA)              if merchants_rows  else pa.Table.from_pylist([], schema=_MERCHANTS_SCHEMA),
            "merchant_kyc":               pa.Table.from_pylist(kyc_rows,        schema=_MERCHANT_KYC_SCHEMA)           if kyc_rows        else pa.Table.from_pylist([], schema=_MERCHANT_KYC_SCHEMA),
            "merchant_settlement_config": pa.Table.from_pylist(settlement_rows, schema=_MERCHANT_SETTLEMENT_SCHEMA)    if settlement_rows else pa.Table.from_pylist([], schema=_MERCHANT_SETTLEMENT_SCHEMA),
            "merchant_pricing_plans":     pa.Table.from_pylist(pricing_rows,    schema=_MERCHANT_PRICING_SCHEMA)       if pricing_rows    else pa.Table.from_pylist([], schema=_MERCHANT_PRICING_SCHEMA),
            "merchant_category_map":      pa.Table.from_pylist(category_rows,   schema=_MERCHANT_CATEGORY_MAP_SCHEMA)  if category_rows   else pa.Table.from_pylist([], schema=_MERCHANT_CATEGORY_MAP_SCHEMA),
        }
