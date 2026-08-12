"""adtech domain generator — 6 tables with rich nested types (§6.7).

Tables:
  ad_campaigns    (grain campaign_id; targeting STRUCT; budget_pacing LIST<STRUCT> — deeply nested)
  ad_creatives    (grain creative_id; creative_meta JSON)
  ad_impressions  (grain impression_id; placement STRUCT — highest-volume table)
  ad_clicks       (grain click_id; click_context STRUCT — ~2% CTR)
  attribution     (grain attribution_id; touchpoints LIST<STRUCT>, weights MAP<VARCHAR,DOUBLE>)
  ad_spend_daily  (grain campaign_id x spend_date; breakdown MAP<VARCHAR,DOUBLE>)
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pyarrow as pa
from data_harness.generators.base import BaseGenerator
from data_harness.common.paths import utc_now


# ---- schemas (explicit — nested types matter for the modeller) ----

_TARGETING_STRUCT = pa.struct([
    ("age_min",                       pa.int32()),
    ("age_max",                       pa.int32()),
    ("states",                        pa.list_(pa.string())),
    ("interests",                     pa.list_(pa.string())),
    ("exclude_users_lookback_days",   pa.int32()),
])

_BUDGET_PACING_STRUCT = pa.struct([
    ("pace_date",     pa.timestamp("us", tz="UTC")),
    ("planned_spend", pa.float64()),
    ("actual_spend",  pa.float64()),
    ("updated_at",    pa.timestamp("us", tz="UTC")),
])

_PLACEMENT_STRUCT = pa.struct([
    ("surface",                pa.string()),
    ("position_index",         pa.int32()),
    ("viewport_pct_visible",   pa.float64()),
    ("duration_ms",            pa.int64()),
])

_CLICK_CONTEXT_STRUCT = pa.struct([
    ("click_x_pct",                 pa.float64()),
    ("click_y_pct",                 pa.float64()),
    ("time_since_impression_ms",    pa.int64()),
    ("device_orientation",          pa.string()),
])

_TOUCHPOINT_STRUCT = pa.struct([
    ("channel",     pa.string()),
    ("campaign_id", pa.string()),
    ("touched_at",  pa.timestamp("us", tz="UTC")),
    ("weight",      pa.float64()),
])


_CAMPAIGNS_SCHEMA = pa.schema([
    ("campaign_id",    pa.string()),
    ("campaign_name",  pa.string()),
    ("channel",        pa.string()),
    ("budget",         pa.float64()),
    ("started_at",     pa.timestamp("us", tz="UTC")),
    ("ended_at",       pa.timestamp("us", tz="UTC")),  # nullable
    ("targeting",      _TARGETING_STRUCT),
    ("budget_pacing",  pa.list_(_BUDGET_PACING_STRUCT)),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_CREATIVES_SCHEMA = pa.schema([
    ("creative_id",    pa.string()),
    ("campaign_id",    pa.string()),
    ("creative_type",  pa.string()),
    ("creative_url",   pa.string()),
    ("creative_meta",  pa.string()),  # JSON as string
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_IMPRESSIONS_SCHEMA = pa.schema([
    ("impression_id",  pa.string()),
    ("campaign_id",    pa.string()),
    ("creative_id",    pa.string()),
    ("user_id",        pa.string()),
    ("shown_at",       pa.timestamp("us", tz="UTC")),
    ("platform",       pa.string()),
    ("placement",      _PLACEMENT_STRUCT),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_CLICKS_SCHEMA = pa.schema([
    ("click_id",       pa.string()),
    ("impression_id",  pa.string()),
    ("clicked_at",     pa.timestamp("us", tz="UTC")),
    ("click_context",  _CLICK_CONTEXT_STRUCT),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_ATTRIBUTION_SCHEMA = pa.schema([
    ("attribution_id", pa.string()),
    ("order_id",       pa.string()),
    ("touchpoints",    pa.list_(_TOUCHPOINT_STRUCT)),
    ("model",          pa.string()),
    ("attributed_at",  pa.timestamp("us", tz="UTC")),
    ("weights",        pa.map_(pa.string(), pa.float64())),
    ("_event_ts",      pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",     pa.timestamp("us", tz="UTC")),
])

_SPEND_DAILY_SCHEMA = pa.schema([
    ("campaign_id",       pa.string()),
    ("spend_date",        pa.date32()),
    ("spend_amount",      pa.float64()),
    ("impressions_count", pa.int64()),
    ("clicks_count",      pa.int64()),
    ("breakdown",         pa.map_(pa.string(), pa.float64())),
    ("_event_ts",         pa.timestamp("us", tz="UTC")),
    ("_ingest_ts",        pa.timestamp("us", tz="UTC")),
])


_CHANNELS = ["google", "meta", "instagram", "youtube", "tiktok", "native"]
_CHANNEL_WEIGHTS = [30, 25, 18, 12, 10, 5]
_CREATIVE_TYPES = ["image", "video", "carousel", "story"]
_CREATIVE_TYPE_WEIGHTS = [45, 25, 20, 10]
_PLATFORMS = ["android", "ios", "web"]
_PLATFORM_WEIGHTS = [65, 25, 10]
_SURFACES = ["feed", "story", "search", "reels", "sidebar", "in_stream"]
_ORIENTATIONS = ["portrait", "landscape"]
_ATTRIBUTION_MODELS = ["last_click", "first_click", "linear", "time_decay", "data_driven"]
_ATTRIBUTION_MODEL_WEIGHTS = [45, 15, 15, 15, 10]
_INTERESTS = [
    "shopping", "gaming", "travel", "food", "fitness", "finance",
    "technology", "fashion", "music", "sports", "education", "beauty",
]


class AdtechGenerator(BaseGenerator):
    domain = "adtech"
    tables = [
        "ad_campaigns", "ad_creatives", "ad_impressions",
        "ad_clicks", "attribution", "ad_spend_daily",
    ]

    # ---- helpers ----

    def _new_targeting(self) -> dict:
        n_states = self.rng.randint(1, 5)
        states = self.rng.sample([s["code"] for s in self.cfg.get("geo.states", [])] or ["MH"],
                                 k=min(n_states, len(self.cfg.get("geo.states", []) or ["MH"])))
        n_interests = self.rng.randint(1, 4)
        interests = self.rng.sample(_INTERESTS, k=n_interests)
        age_min = self.rng.choice([13, 18, 21, 25, 30])
        age_max = age_min + self.rng.choice([10, 15, 20, 30, 45])
        return {
            "age_min":                     age_min,
            "age_max":                     age_max,
            "states":                      states,
            "interests":                   interests,
            "exclude_users_lookback_days": self.rng.choice([0, 7, 30, 90]),
        }

    def _new_budget_pacing(self, start: datetime, budget: float, days: int) -> list:
        planned_per_day = budget / max(days, 1)
        entries = []
        for i in range(days):
            pace_day = start + timedelta(days=i)
            actual = planned_per_day * self.rng.uniform(0.6, 1.3)
            entries.append({
                "pace_date":     pace_day,
                "planned_spend": round(planned_per_day, 2),
                "actual_spend":  round(actual, 2),
                "updated_at":    pace_day + timedelta(hours=23),
            })
        return entries

    def _creative_meta_json(self, ctype: str) -> str:
        meta = {
            "width":       self.rng.choice([320, 480, 720, 1080, 1920]),
            "height":      self.rng.choice([320, 480, 720, 1080, 1920]),
            "file_size_kb": self.rng.randint(30, 4096),
            "language":    self.pick_language(),
            "cta":         self.rng.choice(["shop_now", "learn_more", "install", "sign_up", "watch"]),
        }
        if ctype == "video":
            meta["duration_sec"] = self.rng.randint(6, 60)
            meta["autoplay"]     = self.rng.random() < 0.8
        elif ctype == "carousel":
            meta["n_frames"] = self.rng.randint(2, 8)
        return json.dumps(meta)

    def _new_placement(self) -> dict:
        return {
            "surface":              self.rng.choice(_SURFACES),
            "position_index":       self.rng.randint(1, 20),
            "viewport_pct_visible": round(self.rng.uniform(0.2, 1.0), 3),
            "duration_ms":          self.rng.randint(100, 15_000),
        }

    def _new_click_context(self) -> dict:
        return {
            "click_x_pct":              round(self.rng.random(), 4),
            "click_y_pct":              round(self.rng.random(), 4),
            "time_since_impression_ms": self.rng.randint(200, 30_000),
            "device_orientation":       self.rng.choice(_ORIENTATIONS),
        }

    # ---- emit ----

    def emit_tick(self) -> Dict[str, pa.Table]:
        n_campaigns   = self.rate("ad_campaigns")
        n_creatives   = self.rate("ad_creatives")
        n_impressions = self.rate("ad_impressions")
        n_clicks      = self.rate("ad_clicks")
        n_attribution = self.rate("attribution")
        n_spend       = self.rate("ad_spend_daily")
        now = self.now()

        # ---- ad_campaigns (respect cap of 200) ----
        campaigns_rows: List[dict] = []
        max_campaigns = int(self.cfg.get("entity_caps.campaigns", 200))
        current_campaigns = len(self.state.pool("campaign_ids"))
        slots = max(0, max_campaigns - current_campaigns)
        to_create = min(n_campaigns, slots)
        for _ in range(to_create):
            cid = f"cmp_{self.state.next_id('campaign'):06d}"
            self.state.pool("campaign_ids").append(cid)

            channel = self.pick_weighted(_CHANNELS, _CHANNEL_WEIGHTS)
            budget = round(self.rng.uniform(50_000, 5_000_000), 2)
            duration_days = self.rng.choice([7, 14, 30, 60, 90])
            started = self.event_ts(spread_seconds=86_400 * 30)
            # ~30% still running (no ended_at)
            if self.rng.random() < 0.30:
                ended = None
            else:
                ended = started + timedelta(days=duration_days)
            pacing_days = min(duration_days, self.rng.randint(3, 14))
            pacing = self._new_budget_pacing(started, budget, pacing_days)

            campaigns_rows.append({
                "campaign_id":   cid,
                "campaign_name": f"{channel}_{self.faker.color_name().lower()}_{self.rng.randint(1000, 9999)}",
                "channel":       channel,
                "budget":        budget,
                "started_at":    started,
                "ended_at":      ended,
                "targeting":     self._new_targeting(),
                "budget_pacing": pacing,
                "_event_ts":     self.maybe_late(started),
                "_ingest_ts":    now,
            })

        # ---- ad_creatives (many per campaign) ----
        creatives_rows: List[dict] = []
        campaign_pool = self.state.pool("campaign_ids")
        for _ in range(n_creatives):
            if not campaign_pool:
                break
            cid = self.rng.choice(campaign_pool)
            crid = f"crv_{self.state.next_id('creative'):08d}"
            self.state.pool("creative_ids").append(crid)
            # cap creative pool to avoid unbounded state
            max_crv_pool = 20_000
            if len(self.state.pool("creative_ids")) > max_crv_pool:
                self.state.entity_pools["creative_ids"] = self.state.pool("creative_ids")[-max_crv_pool:]

            ctype = self.pick_weighted(_CREATIVE_TYPES, _CREATIVE_TYPE_WEIGHTS)
            when = self.event_ts(spread_seconds=3600)
            creatives_rows.append({
                "creative_id":   crid,
                "campaign_id":   cid,
                "creative_type": ctype,
                "creative_url":  f"https://cdn.example.in/ads/{cid}/{crid}.{'mp4' if ctype == 'video' else 'jpg'}",
                "creative_meta": self._creative_meta_json(ctype),
                "_event_ts":     self.maybe_late(when),
                "_ingest_ts":    now,
            })

        # ---- ad_impressions (highest volume; needs users + creative pools) ----
        impressions_rows: List[dict] = []
        user_pool = self.state.pool("user_ids")
        creative_pool = self.state.pool("creative_ids")
        if user_pool and creative_pool and campaign_pool:
            for _ in range(n_impressions):
                iid = f"imp_{self.state.next_id('impression'):012d}"
                self.state.pool("impression_ids").append(iid)
                # cap impression pool aggressively — very high volume
                max_imp_pool = 50_000
                if len(self.state.pool("impression_ids")) > max_imp_pool:
                    self.state.entity_pools["impression_ids"] = self.state.pool("impression_ids")[-max_imp_pool:]

                # bias creative→campaign by picking a creative first, but campaign is a
                # separate pool sample here (intentional flat draw — analyst discovers join)
                crid = self.rng.choice(creative_pool)
                cid = self.rng.choice(campaign_pool)
                uid = self.rng.choice(user_pool)
                shown = self.event_ts(spread_seconds=3600)
                impressions_rows.append({
                    "impression_id": iid,
                    "campaign_id":   cid,
                    "creative_id":   crid,
                    "user_id":       uid,
                    "shown_at":      shown,
                    "platform":      self.pick_weighted(_PLATFORMS, _PLATFORM_WEIGHTS),
                    "placement":     self._new_placement(),
                    "_event_ts":     self.maybe_late(shown),
                    "_ingest_ts":    now,
                })

        # ---- ad_clicks (~2% CTR — draw from impression pool) ----
        clicks_rows: List[dict] = []
        impression_pool = self.state.pool("impression_ids")
        if impression_pool:
            for _ in range(n_clicks):
                iid = self.rng.choice(impression_pool)
                clicked = self.event_ts(spread_seconds=1800)
                clicks_rows.append({
                    "click_id":      f"clk_{self.state.next_id('click'):012d}",
                    "impression_id": iid,
                    "clicked_at":    clicked,
                    "click_context": self._new_click_context(),
                    "_event_ts":     self.maybe_late(clicked),
                    "_ingest_ts":    now,
                })

        # ---- attribution (multi-touch, references order pool) ----
        attribution_rows: List[dict] = []
        order_pool = self.state.pool("order_ids")
        if order_pool and campaign_pool:
            for _ in range(n_attribution):
                oid = self.rng.choice(order_pool)
                attributed = self.event_ts(spread_seconds=3600)
                n_touches = self.rng.randint(1, 5)
                touchpoints = []
                channels_seen = []
                for t in range(n_touches):
                    ch = self.pick_weighted(_CHANNELS, _CHANNEL_WEIGHTS)
                    channels_seen.append(ch)
                    touched = attributed - timedelta(minutes=self.rng.randint(5, 60 * 24 * 30))
                    touchpoints.append({
                        "channel":     ch,
                        "campaign_id": self.rng.choice(campaign_pool),
                        "touched_at":  touched,
                        "weight":      round(1.0 / n_touches, 4),
                    })
                model = self.pick_weighted(_ATTRIBUTION_MODELS, _ATTRIBUTION_MODEL_WEIGHTS)
                # weights per channel — for models that split, allocate; otherwise 1.0 to picked
                if model == "last_click":
                    weights_map = {channels_seen[-1]: 1.0}
                elif model == "first_click":
                    weights_map = {channels_seen[0]: 1.0}
                elif model == "linear":
                    share = round(1.0 / len(channels_seen), 4)
                    weights_map = {ch: share for ch in set(channels_seen)}
                else:  # time_decay | data_driven — random split summing ~1.0
                    raw = [self.rng.random() for _ in channels_seen]
                    tot = sum(raw) or 1.0
                    weights_map = {}
                    for ch, w in zip(channels_seen, raw):
                        weights_map[ch] = round(weights_map.get(ch, 0.0) + w / tot, 4)

                attribution_rows.append({
                    "attribution_id": f"att_{self.state.next_id('attribution'):010d}",
                    "order_id":       oid,
                    "touchpoints":    touchpoints,
                    "model":          model,
                    "attributed_at":  attributed,
                    "weights":        weights_map,
                    "_event_ts":      self.maybe_late(attributed),
                    "_ingest_ts":     now,
                })

        # ---- ad_spend_daily (per campaign per day rollup) ----
        spend_rows: List[dict] = []
        if campaign_pool:
            for _ in range(n_spend):
                cid = self.rng.choice(campaign_pool)
                # random day in last 30
                spend_day = (now - timedelta(days=self.rng.randint(0, 30))).date()
                spend_amount = round(self.rng.uniform(500, 250_000), 2)
                # platform breakdown sums to spend_amount
                weights = [self.rng.random() for _ in _PLATFORMS]
                tot = sum(weights) or 1.0
                breakdown = {p: round(spend_amount * w / tot, 2) for p, w in zip(_PLATFORMS, weights)}
                impressions_count = self.rng.randint(1_000, 200_000)
                clicks_count = int(impressions_count * self.rng.uniform(0.005, 0.05))
                spend_rows.append({
                    "campaign_id":       cid,
                    "spend_date":        spend_day,
                    "spend_amount":      spend_amount,
                    "impressions_count": impressions_count,
                    "clicks_count":      clicks_count,
                    "breakdown":         breakdown,
                    "_event_ts":         self.maybe_late(now),
                    "_ingest_ts":        now,
                })

        # ---- build pa.Tables with explicit schemas ----
        return {
            "ad_campaigns":   pa.Table.from_pylist(campaigns_rows,   schema=_CAMPAIGNS_SCHEMA)   if campaigns_rows   else pa.Table.from_pylist([], schema=_CAMPAIGNS_SCHEMA),
            "ad_creatives":   pa.Table.from_pylist(creatives_rows,   schema=_CREATIVES_SCHEMA)   if creatives_rows   else pa.Table.from_pylist([], schema=_CREATIVES_SCHEMA),
            "ad_impressions": pa.Table.from_pylist(impressions_rows, schema=_IMPRESSIONS_SCHEMA) if impressions_rows else pa.Table.from_pylist([], schema=_IMPRESSIONS_SCHEMA),
            "ad_clicks":      pa.Table.from_pylist(clicks_rows,      schema=_CLICKS_SCHEMA)      if clicks_rows      else pa.Table.from_pylist([], schema=_CLICKS_SCHEMA),
            "attribution":    pa.Table.from_pylist(attribution_rows, schema=_ATTRIBUTION_SCHEMA) if attribution_rows else pa.Table.from_pylist([], schema=_ATTRIBUTION_SCHEMA),
            "ad_spend_daily": pa.Table.from_pylist(spend_rows,       schema=_SPEND_DAILY_SCHEMA) if spend_rows       else pa.Table.from_pylist([], schema=_SPEND_DAILY_SCHEMA),
        }
