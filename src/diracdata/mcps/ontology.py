"""Ontology + channel map helpers for catalog/schema fabric."""
from __future__ import annotations

from typing import Any, Optional


def empty_ontology() -> dict[str, Any]:
    return {
        "entities": {},       # canonical -> {synonyms: [], dbs: []}
        "hierarchies": {},    # name -> [child, ...]
        "notes": [],
    }


def empty_channel_maps() -> dict[str, Any]:
    return {
        "campaign_channel_to_utm_source": {
            # campaign touchpoint channel -> list of utm sources
            "email": ["email"],
            "search": ["google"],
            "social": ["meta", "tiktok"],
            # display/affiliate intentionally unmapped until tagged
        },
        "utm_source_to_campaign_channel": {
            "email": "email",
            "google": "search",
            "meta": "social",
            "tiktok": "social",
            "direct": "organic_direct",
        },
        "notes": [
            "display and affiliate have no UTM counterpart until utm_source=display / utm_medium=affiliate exist."
        ],
    }


def map_utm_to_channel(channel_maps: dict[str, Any], utm_source: str) -> Optional[str]:
    m = (channel_maps or {}).get("utm_source_to_campaign_channel") or {}
    return m.get((utm_source or "").lower().strip())


def map_channel_to_utms(channel_maps: dict[str, Any], campaign_channel: str) -> list[str]:
    m = (channel_maps or {}).get("campaign_channel_to_utm_source") or {}
    vals = m.get((campaign_channel or "").lower().strip()) or []
    return list(vals) if isinstance(vals, list) else [vals]
