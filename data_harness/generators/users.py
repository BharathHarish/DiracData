"""users domain generator — 4 tables with rich nested types (§6.1).

Tables:
  users              (base user; nested: devices LIST<STRUCT>, preferences JSON,
                     kyc_documents LIST<STRUCT>, feature_flags MAP)
  user_devices       (flat child; STRUCT fingerprint)  -- intentional redundancy
  user_kyc_events    (append-only; JSON verification_payload)
  user_sessions      (per user × session; LIST<STRUCT> event_stream)
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- schemas (explicit — nested types matter for the modeller) ----

_DEVICE_STRUCT = pa.struct([
    ("device_id",  pa.string()),
    ("os",         pa.string()),
    ("model",      pa.string()),
    ("trusted",    pa.bool_()),
    ("last_seen",  pa.timestamp("us", tz="UTC")),
])

_KYC_DOC_STRUCT = pa.struct([
    ("doc_type",     pa.string()),
    ("uploaded_at",  pa.timestamp("us", tz="UTC")),
    ("status",       pa.string()),
])

_FINGERPRINT_STRUCT = pa.struct([
    ("screen_resolution", pa.string()),
    ("timezone",          pa.string()),
    ("language",          pa.string()),
    ("hash",              pa.string()),
])

_SESSION_EVENT_STRUCT = pa.struct([
    ("event_type", pa.string()),
    ("event_time", pa.timestamp("us", tz="UTC")),
    ("target",     pa.string()),
])


_USERS_SCHEMA = pa.schema([
    ("user_id",         pa.string()),
    ("user_type",       pa.string()),
    ("signup_time",     pa.timestamp("us", tz="UTC")),
    ("state_code",      pa.string()),
    ("city",            pa.string()),
    ("language",        pa.string()),
    ("kyc_status",      pa.string()),
    ("devices",         pa.list_(_DEVICE_STRUCT)),
    ("preferences",     pa.string()),  # JSON as string
    ("kyc_documents",   pa.list_(_KYC_DOC_STRUCT)),
    ("feature_flags",   pa.map_(pa.string(), pa.bool_())),
    ("_event_ts",       pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",      pa.timestamp("us", tz="UTC")),
])

_DEVICES_SCHEMA = pa.schema([
    ("device_id",   pa.string()),
    ("user_id",     pa.string()),
    ("os",          pa.string()),
    ("model",       pa.string()),
    ("first_seen",  pa.timestamp("us", tz="UTC")),
    ("trusted",     pa.bool_()),
    ("fingerprint", _FINGERPRINT_STRUCT),
    ("_event_ts",   pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",  pa.timestamp("us", tz="UTC")),
])

_KYC_SCHEMA = pa.schema([
    ("kyc_event_id",         pa.string()),
    ("user_id",              pa.string()),
    ("event_type",           pa.string()),  # submitted | approved | rejected | resubmitted
    ("event_time",           pa.timestamp("us", tz="UTC")),
    ("doc_type",             pa.string()),
    ("verification_payload", pa.string()),  # JSON
    ("_event_ts",            pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",           pa.timestamp("us", tz="UTC")),
])

_SESSIONS_SCHEMA = pa.schema([
    ("session_id",     pa.string()),
    ("user_id",        pa.string()),
    ("session_start",  pa.timestamp("us", tz="UTC")),
    ("session_end",    pa.timestamp("us", tz="UTC")),
    ("platform",       pa.string()),
    ("event_stream",   pa.list_(_SESSION_EVENT_STRUCT)),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])


_USER_TYPES = ["personal", "merchant", "business"]
_USER_TYPE_WEIGHTS = [85, 12, 3]
_KYC_STATES = ["pending", "in_review", "approved", "rejected", "resubmit"]
_KYC_WEIGHTS = [10, 15, 65, 5, 5]
_DEVICE_OS = ["Android", "iOS", "Web"]
_DEVICE_OS_WEIGHTS = [72, 25, 3]
_ANDROID_MODELS = ["Xiaomi Redmi", "Samsung Galaxy", "OnePlus", "Realme", "Vivo", "Oppo"]
_IOS_MODELS = ["iPhone 12", "iPhone 13", "iPhone 14", "iPhone 15", "iPhone SE"]
_PLATFORMS = ["android_app", "ios_app", "web"]
_PLATFORM_WEIGHTS = [65, 25, 10]


class UsersGenerator(BaseGenerator):
    domain = "users"
    tables = ["users", "user_devices", "user_kyc_events", "user_sessions"]

    def _pick_model(self, os: str) -> str:
        if os == "Android": return self.rng.choice(_ANDROID_MODELS)
        if os == "iOS":     return self.rng.choice(_IOS_MODELS)
        return "web_browser"

    def _new_device(self, user_id: str, when: datetime) -> dict:
        os = self.pick_weighted(_DEVICE_OS, _DEVICE_OS_WEIGHTS)
        return {
            "device_id":  f"dev_{self.state.next_id('device'):010d}",
            "user_id":    user_id,
            "os":         os,
            "model":      self._pick_model(os),
            "trusted":    self.rng.random() < 0.85,
            "last_seen":  when,
        }

    def _new_kyc_doc(self, when: datetime) -> dict:
        return {
            "doc_type":    self.rng.choice(["aadhaar", "pan", "passport", "voter_id", "driving_license"]),
            "uploaded_at": when,
            "status":      self.pick_weighted(["submitted", "verified", "rejected"], [30, 65, 5]),
        }

    def _preferences_json(self) -> str:
        return json.dumps({
            "notifications": {
                "email":  self.rng.random() < 0.7,
                "sms":    self.rng.random() < 0.85,
                "push":   self.rng.random() < 0.9,
                "whatsapp": self.rng.random() < 0.6,
            },
            "theme":         self.rng.choice(["light", "dark", "system"]),
            "currency":      "INR",
            "biometric_login": self.rng.random() < 0.55,
        })

    def _feature_flags(self) -> dict:
        return {
            "upi_autopay":      self.rng.random() < 0.4,
            "credit_upsell":    self.rng.random() < 0.25,
            "beta_lending":     self.rng.random() < 0.15,
            "new_home_screen":  self.rng.random() < 0.5,
        }

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        n_users     = self.rate("users")
        n_devices   = self.rate("user_devices")
        n_kyc       = self.rate("user_kyc_events")
        n_sessions  = self.rate("user_sessions")
        now = self.now()

        # ---- users ----
        users_rows: List[dict] = []
        for _ in range(n_users):
            uid = f"usr_{self.state.next_id('user'):010d}"
            self.state.pool("user_ids").append(uid)
            # Cap the pool so JSON state doesn't grow unboundedly
            max_pool = int(self.cfg.get("entity_caps.users", 50_000))
            if len(self.state.pool("user_ids")) > max_pool:
                self.state.entity_pools["user_ids"] = self.state.pool("user_ids")[-max_pool:]

            state = self.pick_state()
            signup_ts = self.event_ts(spread_seconds=3600)
            n_dev = self.rng.choices([1, 2, 3], weights=[70, 25, 5], k=1)[0]
            devices = [self._new_device(uid, signup_ts) for _ in range(n_dev)]
            n_docs = self.rng.choices([0, 1, 2], weights=[10, 70, 20], k=1)[0]
            docs = [self._new_kyc_doc(signup_ts) for _ in range(n_docs)]

            users_rows.append({
                "user_id":       uid,
                "user_type":     self.pick_weighted(_USER_TYPES, _USER_TYPE_WEIGHTS),
                "signup_time":   signup_ts,
                "state_code":    state["code"],
                "city":          self.faker.city(),
                "language":      self.pick_language(),
                "kyc_status":    self.pick_weighted(_KYC_STATES, _KYC_WEIGHTS),
                "devices":       devices,
                "preferences":   self._preferences_json(),
                "kyc_documents": docs,
                "feature_flags": self._feature_flags(),
                "_event_ts":     self.maybe_late(signup_ts),
                "_ingest_ts":    now,
            })

        # ---- user_devices (redundant flat child) ----
        # Some devices come from newly-created users' devices; some fresh
        devices_rows: List[dict] = []
        for _ in range(n_devices):
            # Pick an existing user (if any) or synth a new device tied to a fresh user_id in pool
            pool = self.state.pool("user_ids")
            if not pool:
                break
            uid = self.rng.choice(pool)
            os = self.pick_weighted(_DEVICE_OS, _DEVICE_OS_WEIGHTS)
            when = self.event_ts(spread_seconds=3600)
            devices_rows.append({
                "device_id":   f"dev_{self.state.next_id('device'):010d}",
                "user_id":     uid,
                "os":          os,
                "model":       self._pick_model(os),
                "first_seen":  when,
                "trusted":     self.rng.random() < 0.85,
                "fingerprint": {
                    "screen_resolution": self.rng.choice(["360x800", "412x915", "390x844", "1920x1080"]),
                    "timezone":          "Asia/Kolkata",
                    "language":          self.pick_language(),
                    "hash":              f"fp_{self.rng.getrandbits(64):016x}",
                },
                "_event_ts":  self.maybe_late(when),
                "_ingest_ts": now,
            })

        # ---- user_kyc_events ----
        kyc_rows: List[dict] = []
        pool = self.state.pool("user_ids")
        for _ in range(n_kyc):
            if not pool: break
            uid = self.rng.choice(pool)
            when = self.event_ts(spread_seconds=3600)
            evt_type = self.pick_weighted(
                ["submitted", "approved", "rejected", "resubmitted"], [30, 55, 8, 7])
            kyc_rows.append({
                "kyc_event_id": f"kyc_{self.state.next_id('kyc'):012d}",
                "user_id":      uid,
                "event_type":   evt_type,
                "event_time":   when,
                "doc_type":     self.rng.choice(["aadhaar", "pan", "passport", "voter_id"]),
                "verification_payload": json.dumps({
                    "reviewer":   f"agent_{self.rng.randint(1, 50)}",
                    "score":      round(self.rng.random(), 3),
                    "ocr_conf":   round(self.rng.uniform(0.7, 1.0), 3),
                    "flags":      [] if self.rng.random() < 0.9 else ["low_light", "blurry"],
                }),
                "_event_ts":  self.maybe_late(when),
                "_ingest_ts": now,
            })

        # ---- user_sessions ----
        sessions_rows: List[dict] = []
        for _ in range(n_sessions):
            if not pool: break
            uid = self.rng.choice(pool)
            start = self.event_ts(spread_seconds=3600)
            duration = self.rng.randint(30, 1800)  # 30s - 30min
            end = start + timedelta(seconds=duration)
            n_events = self.rng.randint(2, 12)
            event_stream = []
            for _ in range(n_events):
                offset = self.rng.randint(0, duration)
                event_stream.append({
                    "event_type": self.rng.choice(
                        ["page_view", "click", "swipe", "form_input", "back", "submit"]),
                    "event_time": start + timedelta(seconds=offset),
                    "target":     self.rng.choice(
                        ["home", "checkout", "profile", "search", "payment", "history"]),
                })
            sessions_rows.append({
                "session_id":    f"ses_{self.state.next_id('session'):012d}",
                "user_id":       uid,
                "session_start": start,
                "session_end":   end,
                "platform":      self.pick_weighted(_PLATFORMS, _PLATFORM_WEIGHTS),
                "event_stream":  event_stream,
                "_event_ts":     self.maybe_late(start),
                "_ingest_ts":    now,
            })

        # ---- build pa.Tables with explicit schemas ----
        return {
            "users":           pa.Table.from_pylist(users_rows,    schema=_USERS_SCHEMA)    if users_rows    else pa.Table.from_pylist([], schema=_USERS_SCHEMA),
            "user_devices":    pa.Table.from_pylist(devices_rows,  schema=_DEVICES_SCHEMA)  if devices_rows  else pa.Table.from_pylist([], schema=_DEVICES_SCHEMA),
            "user_kyc_events": pa.Table.from_pylist(kyc_rows,      schema=_KYC_SCHEMA)      if kyc_rows      else pa.Table.from_pylist([], schema=_KYC_SCHEMA),
            "user_sessions":   pa.Table.from_pylist(sessions_rows, schema=_SESSIONS_SCHEMA) if sessions_rows else pa.Table.from_pylist([], schema=_SESSIONS_SCHEMA),
        }
