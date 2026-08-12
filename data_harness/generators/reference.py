"""5 static reference tables — idempotent seed. Small enough to keep flat parquet."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Dict
import pyarrow as pa
from data_harness.common.config import Config
from data_harness.common.logging import log
from data_harness.writers.parquet_writer import write_reference


# --- Static reference data (India-focused) --------------------------------

_STATES = [
    ("MH", "Maharashtra", "West"),   ("KA", "Karnataka", "South"),
    ("TN", "Tamil Nadu", "South"),   ("DL", "Delhi", "North"),
    ("GJ", "Gujarat", "West"),       ("UP", "Uttar Pradesh", "North"),
    ("WB", "West Bengal", "East"),   ("TG", "Telangana", "South"),
    ("AP", "Andhra Pradesh", "South"), ("KL", "Kerala", "South"),
    ("RJ", "Rajasthan", "North"),    ("PB", "Punjab", "North"),
    ("HR", "Haryana", "North"),      ("MP", "Madhya Pradesh", "Central"),
    ("BR", "Bihar", "East"),         ("OR", "Odisha", "East"),
    ("AS", "Assam", "Northeast"),    ("JH", "Jharkhand", "East"),
    ("CT", "Chhattisgarh", "Central"), ("UT", "Uttarakhand", "North"),
]

_CURRENCIES = [
    ("INR", "Indian Rupee", 2), ("USD", "US Dollar", 2), ("EUR", "Euro", 2),
    ("GBP", "Pound Sterling", 2), ("SGD", "Singapore Dollar", 2),
    ("AED", "UAE Dirham", 2), ("JPY", "Japanese Yen", 0),
]

# Realistic MCC subset (top 30 categories relevant to Indian fintech)
_MCCS = [
    ("5411", "Grocery Stores, Supermarkets", "Retail"),
    ("5812", "Eating Places, Restaurants", "Food"),
    ("5814", "Fast Food Restaurants", "Food"),
    ("5541", "Service Stations", "Fuel"),
    ("5311", "Department Stores", "Retail"),
    ("5691", "Men's & Women's Clothing", "Apparel"),
    ("5651", "Family Clothing Stores", "Apparel"),
    ("5732", "Electronics Stores", "Electronics"),
    ("4111", "Local & Suburban Transport", "Transport"),
    ("4121", "Taxicabs & Limousines", "Transport"),
    ("4131", "Bus Lines", "Transport"),
    ("4511", "Airlines & Air Carriers", "Travel"),
    ("7011", "Lodging - Hotels & Motels", "Travel"),
    ("5912", "Drug Stores & Pharmacies", "Health"),
    ("8011", "Doctors & Physicians", "Health"),
    ("8021", "Dentists & Orthodontists", "Health"),
    ("8062", "Hospitals", "Health"),
    ("8299", "Schools & Educational Services", "Education"),
    ("8211", "Elementary & Secondary Schools", "Education"),
    ("4812", "Telecommunication Services", "Telecom"),
    ("4900", "Utilities", "Utilities"),
    ("6011", "Financial Institutions - Manual Cash", "Financial"),
    ("6012", "Financial Institutions - Merchandise", "Financial"),
    ("7995", "Betting/Casino Gambling", "Entertainment"),
    ("7832", "Motion Picture Theaters", "Entertainment"),
    ("5942", "Book Stores", "Retail"),
    ("5945", "Hobby, Toy & Game Shops", "Retail"),
    ("5399", "Miscellaneous General Merchandise", "Retail"),
    ("7372", "Computer Programming, Data Processing", "Services"),
    ("7399", "Business Services, Not Elsewhere Classified", "Services"),
]

# Indian national holidays (2026 subset)
_HOLIDAYS = [
    ("IN", "2026-01-26", "Republic Day"),
    ("IN", "2026-03-14", "Holi"),
    ("IN", "2026-04-14", "Ambedkar Jayanti"),
    ("IN", "2026-05-01", "Labour Day"),
    ("IN", "2026-08-15", "Independence Day"),
    ("IN", "2026-10-02", "Gandhi Jayanti"),
    ("IN", "2026-10-19", "Diwali"),
    ("IN", "2026-11-04", "Bhai Dooj"),
    ("IN", "2026-12-25", "Christmas"),
]


def _fx_rates_for(days: int = 90) -> list[tuple]:
    """Simple FX rate table: (from, to, date, rate). INR base."""
    out = []
    today = date.today()
    # rough anchor rates
    anchors = {("USD", "INR"): 83.5, ("EUR", "INR"): 90.2, ("GBP", "INR"): 105.4,
               ("SGD", "INR"): 62.1, ("AED", "INR"): 22.7, ("JPY", "INR"): 0.56}
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        for (a, b), rate in anchors.items():
            # tiny walk to simulate daily variation (deterministic-ish)
            drift = ((hash((a, b, d)) & 0xFFFF) / 0xFFFF - 0.5) * 0.02  # ±1%
            out.append((a, b, d, round(rate * (1 + drift), 6)))
            out.append((b, a, d, round(1.0 / (rate * (1 + drift)), 8)))
    return out


# --- Build pyarrow tables -------------------------------------------------

def _countries() -> pa.Table:
    # 25 core countries — India + neighbours + trading partners
    data = [
        ("IN", "India", "SA"),  ("US", "United States", "NA"),
        ("GB", "United Kingdom", "EU"), ("SG", "Singapore", "AS"),
        ("AE", "United Arab Emirates", "ME"), ("DE", "Germany", "EU"),
        ("FR", "France", "EU"), ("AU", "Australia", "OC"),
        ("CA", "Canada", "NA"), ("JP", "Japan", "AS"),
        ("CN", "China", "AS"), ("BD", "Bangladesh", "SA"),
        ("LK", "Sri Lanka", "SA"), ("NP", "Nepal", "SA"),
        ("PK", "Pakistan", "SA"), ("BT", "Bhutan", "SA"),
        ("MV", "Maldives", "SA"), ("MY", "Malaysia", "AS"),
        ("TH", "Thailand", "AS"), ("ID", "Indonesia", "AS"),
        ("VN", "Vietnam", "AS"), ("PH", "Philippines", "AS"),
        ("SA", "Saudi Arabia", "ME"), ("QA", "Qatar", "ME"),
        ("OM", "Oman", "ME"),
    ]
    return pa.table({
        "country_code": [d[0] for d in data],
        "country_name": [d[1] for d in data],
        "region":       [d[2] for d in data],
    })


def _currencies() -> pa.Table:
    return pa.table({
        "currency_code":  [c[0] for c in _CURRENCIES],
        "currency_name":  [c[1] for c in _CURRENCIES],
        "decimal_digits": [c[2] for c in _CURRENCIES],
    })


def _mcc_codes() -> pa.Table:
    return pa.table({
        "mcc_code":       [m[0] for m in _MCCS],
        "category_name":  [m[1] for m in _MCCS],
        "category_group": [m[2] for m in _MCCS],
    })


def _holidays() -> pa.Table:
    dates = [date.fromisoformat(h[1]) for h in _HOLIDAYS]
    return pa.table({
        "country_code": [h[0] for h in _HOLIDAYS],
        "holiday_date": pa.array(dates, type=pa.date32()),
        "holiday_name": [h[2] for h in _HOLIDAYS],
    })


def _states_lookup() -> pa.Table:
    return pa.table({
        "state_code": [s[0] for s in _STATES],
        "state_name": [s[1] for s in _STATES],
        "region":     [s[2] for s in _STATES],
    })


def _fx() -> pa.Table:
    fx = _fx_rates_for(90)
    dates = [date.fromisoformat(r[2]) for r in fx]
    return pa.table({
        "from_currency": [r[0] for r in fx],
        "to_currency":   [r[1] for r in fx],
        "rate_date":     pa.array(dates, type=pa.date32()),
        "rate":          [r[3] for r in fx],
    })


def seed_reference(cfg: Config, s3) -> Dict[str, Dict]:
    """Idempotent — writes all 6 reference tables. Returns per-table stats."""
    out = {}
    for name, tbl in (
        ("countries",     _countries()),
        ("currencies",    _currencies()),
        ("mcc_codes",     _mcc_codes()),
        ("holidays",      _holidays()),
        ("states_in",     _states_lookup()),
        ("fx_rates",      _fx()),
    ):
        stats = write_reference(s3, cfg, name, tbl)
        out[name] = stats
        log.info("reference.write", table=name, rows=stats["rows"], bytes=stats["bytes"])
    return out
