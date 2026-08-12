"""Query bank — parameterised templates across 4 archetypes.

Each template is a callable that returns (template_id, sql, param_values_dict).
Templates use $-substitution with pool draws from BankContext.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, List


@dataclass
class BankContext:
    rng: random.Random
    merchant_ids:  list[str]
    user_ids:      list[str]
    order_ids:     list[str]
    campaign_ids:  list[str]
    loan_ids:      list[str]
    today:         date = field(default_factory=date.today)

    def pick_merchant(self) -> str:  return self.rng.choice(self.merchant_ids) if self.merchant_ids else "mch_00001"
    def pick_user(self)     -> str:  return self.rng.choice(self.user_ids)     if self.user_ids     else "usr_0000000001"
    def pick_order(self)    -> str:  return self.rng.choice(self.order_ids)    if self.order_ids    else "ord_0000000001"
    def pick_campaign(self) -> str:  return self.rng.choice(self.campaign_ids) if self.campaign_ids else "cmp_00001"
    def pick_days_ago(self, min_d=1, max_d=90) -> date:  return self.today - timedelta(days=self.rng.randint(min_d, max_d))


# ---------- BI archetype (dashboard-style, gold-heavy, cheap, repetitive) ----------

def bi_merchant_scorecard(ctx: BankContext) -> tuple[str, str]:
    merchant = ctx.pick_merchant()
    start = ctx.pick_days_ago(30, 60)
    end   = ctx.pick_days_ago(1, 7)
    sql = f"""
        SELECT day, orders, gmv, payment_success_rate, settlement_net
        FROM read_parquet('s3://lake/fintech/gold/g_merchant_daily/**/*.parquet', hive_partitioning=1)
        WHERE merchant_id = '{merchant}'
          AND day BETWEEN DATE '{start}' AND DATE '{end}'
        ORDER BY day
    """
    return ("bi.merchant_scorecard.v1", sql)

def bi_payment_success_by_rail(ctx: BankContext) -> tuple[str, str]:
    d = ctx.pick_days_ago(1, 30)
    sql = f"""
        SELECT rail_type, attempts, successes, success_rate, avg_ticket
        FROM read_parquet('s3://lake/fintech/gold/g_payments_daily/**/*.parquet', hive_partitioning=1)
        WHERE day = DATE '{d}'
        ORDER BY attempts DESC
    """
    return ("bi.payment_success_by_rail.v1", sql)

def bi_orders_dashboard(ctx: BankContext) -> tuple[str, str]:
    merchant = ctx.pick_merchant()
    start = ctx.pick_days_ago(14, 30)
    sql = f"""
        SELECT day, orders, cancellation_rate, abandonment_rate, checkout_sessions
        FROM read_parquet('s3://lake/fintech/gold/g_orders_daily/**/*.parquet', hive_partitioning=1)
        WHERE merchant_id = '{merchant}' AND day >= DATE '{start}'
        ORDER BY day
    """
    return ("bi.orders_dashboard.v1", sql)

def bi_top_merchants(ctx: BankContext) -> tuple[str, str]:
    d = ctx.pick_days_ago(1, 7)
    sql = f"""
        SELECT merchant_id, tier, gmv, orders, payment_success_rate
        FROM read_parquet('s3://lake/fintech/gold/g_merchant_daily/**/*.parquet', hive_partitioning=1)
        WHERE day = DATE '{d}'
        ORDER BY gmv DESC
        LIMIT 20
    """
    return ("bi.top_merchants.v1", sql)


# ---------- Ops archetype (real-time, small windows, must be fast) ----------

def ops_recent_failed_payments(ctx: BankContext) -> tuple[str, str]:
    sql = """
        SELECT attempt_id, order_id, merchant_id, amount, rail_type, attempted_at, status_final
        FROM read_parquet('s3://lake/fintech/silver/s_payments/**/*.parquet', hive_partitioning=1)
        WHERE status_final = 'failed'
        ORDER BY attempted_at DESC
        LIMIT 100
    """
    return ("ops.recent_failed_payments.v1", sql)

def ops_orders_awaiting_shipment(ctx: BankContext) -> tuple[str, str]:
    sql = """
        SELECT order_id, merchant_id, order_time, order_amount
        FROM read_parquet('s3://lake/fintech/silver/s_orders/**/*.parquet', hive_partitioning=1)
        WHERE status_current = 'placed'
        ORDER BY order_time DESC
        LIMIT 200
    """
    return ("ops.orders_awaiting_shipment.v1", sql)

def ops_high_risk_events_today(ctx: BankContext) -> tuple[str, str]:
    d = ctx.today
    sql = f"""
        SELECT risk_event_id, entity_type, entity_id, top_rule_name, top_rule_score, terminal_action
        FROM read_parquet('s3://lake/fintech/silver/s_risk_events/**/*.parquet', hive_partitioning=1)
        WHERE event_time::DATE = DATE '{d}' AND severity IN ('high','critical')
        ORDER BY top_rule_score DESC
        LIMIT 50
    """
    return ("ops.high_risk_events_today.v1", sql)


# ---------- Analyst archetype (silver+gold, exploratory) ----------

def analyst_cohort_conversion(ctx: BankContext) -> tuple[str, str]:
    start = ctx.pick_days_ago(30, 60)
    sql = f"""
        SELECT date_trunc('month', signup_time)::DATE AS cohort_month,
               count(*)                                                        AS users,
               count_if(kyc_status_current = 'approved')                       AS kyc_approved
        FROM read_parquet('s3://lake/fintech/silver/s_users/**/*.parquet', hive_partitioning=1)
        WHERE signup_time >= DATE '{start}'
        GROUP BY 1
        ORDER BY 1
    """
    return ("analyst.cohort_conversion.v1", sql)

def analyst_rail_x_category(ctx: BankContext) -> tuple[str, str]:
    start = ctx.pick_days_ago(7, 14)
    sql = f"""
        SELECT p.rail_type, m.category_group,
               count(*)             AS attempts,
               count_if(status_final IN ('captured','settled')) AS successes,
               count_if(status_final IN ('captured','settled'))::DOUBLE / NULLIF(count(*), 0) AS success_rate
        FROM read_parquet('s3://lake/fintech/silver/s_payments/**/*.parquet', hive_partitioning=1) p
        LEFT JOIN read_parquet('s3://lake/fintech/silver/s_merchants/**/*.parquet', hive_partitioning=1) m
               ON m.merchant_id = p.merchant_id
        WHERE p.attempted_at >= DATE '{start}'
        GROUP BY 1, 2
        ORDER BY attempts DESC
    """
    return ("analyst.rail_x_category.v1", sql)

def analyst_checkout_dropoff(ctx: BankContext) -> tuple[str, str]:
    sql = """
        SELECT last_step, count(*) AS abandonments
        FROM read_parquet('s3://lake/fintech/raw/checkouts/checkout_abandonments/**/*.parquet', hive_partitioning=1)
        GROUP BY last_step
        ORDER BY abandonments DESC
    """
    return ("analyst.checkout_dropoff.v1", sql)


# ---------- RCA archetype (expensive cross-domain, raw+silver, DISCOVERY targets) ----------
# These templates DELIBERATELY simulate the query patterns the modeller must find and propose
# materialisations for. Each corresponds to one §8B discovery target.

def rca_lending_90day_emi(ctx: BankContext) -> tuple[str, str]:
    """DISCOVERY TARGET: 90-day EMI payment lookback by vintage_month × snapshot_date.
    Modeller should propose g_lending_90d_health_daily to materialise this."""
    snap = ctx.pick_days_ago(1, 60)
    sql = f"""
        WITH win AS (
            SELECT l.vintage_month,
                   count(*)                                          AS installments_due,
                   count_if(r.on_time)                               AS installments_on_time,
                   sum(s.emi_amount)                                 AS emi_due_90d,
                   sum(COALESCE(r.paid_amount, 0.0))                 AS emi_paid_90d
            FROM read_parquet('s3://lake/fintech/raw/lending/repayment_schedule/**/*.parquet', hive_partitioning=1) s
            LEFT JOIN read_parquet('s3://lake/fintech/silver/s_repayments/**/*.parquet', hive_partitioning=1) r
                   ON r.schedule_id = s.schedule_id
            LEFT JOIN read_parquet('s3://lake/fintech/silver/s_loans/**/*.parquet', hive_partitioning=1) l
                   ON l.loan_id = s.loan_id
            WHERE s.due_date BETWEEN DATE '{snap}' - INTERVAL 90 DAY AND DATE '{snap}'
            GROUP BY 1
        )
        SELECT vintage_month, installments_due, installments_on_time,
               emi_due_90d, emi_paid_90d,
               (emi_paid_90d / NULLIF(emi_due_90d, 0)) AS pay_ratio_90d,
               (installments_on_time::DOUBLE / NULLIF(installments_due, 0)) AS on_time_pct_90d
        FROM win
        ORDER BY vintage_month
    """
    return ("rca.lending_90day_emi.v1", sql)

def rca_attribution_roas(ctx: BankContext) -> tuple[str, str]:
    """DISCOVERY TARGET: multi-touch attribution + ROAS by campaign × week."""
    start = ctx.pick_days_ago(21, 28)
    sql = f"""
        WITH tp AS (
            SELECT campaign_id, date_trunc('week', touched_at)::DATE AS week,
                   count(*) AS touchpoints, sum(weight) AS weighted_touch
            FROM read_parquet('s3://lake/fintech/silver/s_attribution/**/*.parquet', hive_partitioning=1)
            WHERE touched_at >= DATE '{start}'
            GROUP BY 1, 2
        ),
        sp AS (
            SELECT campaign_id, date_trunc('week', spend_date)::DATE AS week,
                   sum(spend_amount) AS spend
            FROM read_parquet('s3://lake/fintech/silver/s_ad_spend/**/*.parquet', hive_partitioning=1)
            WHERE spend_date >= DATE '{start}'
            GROUP BY 1, 2
        )
        SELECT tp.campaign_id, tp.week,
               tp.touchpoints, tp.weighted_touch,
               sp.spend,
               (tp.weighted_touch::DOUBLE / NULLIF(sp.spend, 0)) AS weighted_touches_per_rupee
        FROM tp LEFT JOIN sp USING (campaign_id, week)
        ORDER BY tp.week DESC, sp.spend DESC NULLS LAST
    """
    return ("rca.attribution_roas.v1", sql)

def rca_user_cohort_ltv(ctx: BankContext) -> tuple[str, str]:
    """DISCOVERY TARGET: cohort × month retention & LTV."""
    sql = """
        WITH cohorts AS (
            SELECT user_id, date_trunc('month', signup_time)::DATE AS cohort_month
            FROM read_parquet('s3://lake/fintech/silver/s_users/**/*.parquet', hive_partitioning=1)
        ),
        activity AS (
            SELECT user_id, date_trunc('month', activity_date)::DATE AS activity_month,
                   sum(gmv) AS monthly_gmv, sum(order_count) AS monthly_orders
            FROM read_parquet('s3://lake/fintech/silver/s_user_activity/**/*.parquet', hive_partitioning=1)
            GROUP BY 1, 2
        )
        SELECT c.cohort_month, a.activity_month,
               date_diff('month', c.cohort_month, a.activity_month) AS months_since_signup,
               count(DISTINCT c.user_id) AS active_users,
               sum(a.monthly_gmv)        AS cohort_gmv
        FROM cohorts c
        JOIN activity a USING (user_id)
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    return ("rca.user_cohort_ltv.v1", sql)

def rca_unit_economics(ctx: BankContext) -> tuple[str, str]:
    """DISCOVERY TARGET: cross-domain unit economics — orders + payments + refunds + ad_spend."""
    start = ctx.pick_days_ago(7, 14)
    sql = f"""
        WITH gmv AS (
            SELECT merchant_id, order_time::DATE AS day, sum(order_amount) AS gmv
            FROM read_parquet('s3://lake/fintech/silver/s_orders/**/*.parquet', hive_partitioning=1)
            WHERE order_time >= DATE '{start}'
            GROUP BY 1, 2
        ),
        pay AS (
            SELECT merchant_id, attempted_at::DATE AS day, sum(refund_total) AS refunds
            FROM read_parquet('s3://lake/fintech/silver/s_payments/**/*.parquet', hive_partitioning=1)
            WHERE attempted_at >= DATE '{start}'
            GROUP BY 1, 2
        ),
        st AS (
            SELECT merchant_id, batch_date AS day, sum(mdr_amount) AS mdr,
                   sum(chargeback_offset) AS chargeback
            FROM read_parquet('s3://lake/fintech/silver/s_settlement/**/*.parquet', hive_partitioning=1)
            WHERE batch_date >= DATE '{start}'
            GROUP BY 1, 2
        )
        SELECT gmv.merchant_id, gmv.day,
               gmv.gmv,
               COALESCE(pay.refunds,     0.0) AS refunds,
               COALESCE(st.mdr,          0.0) AS mdr,
               COALESCE(st.chargeback,   0.0) AS chargeback,
               gmv.gmv - COALESCE(pay.refunds,0) - COALESCE(st.mdr,0) - COALESCE(st.chargeback,0)
                   AS contribution_margin
        FROM gmv
        LEFT JOIN pay USING (merchant_id, day)
        LEFT JOIN st  USING (merchant_id, day)
        ORDER BY contribution_margin DESC
    """
    return ("rca.unit_economics.v1", sql)


# ---------- registry ----------

TEMPLATES: dict[str, list[Callable[[BankContext], tuple[str, str]]]] = {
    "bi":      [bi_merchant_scorecard, bi_payment_success_by_rail, bi_orders_dashboard, bi_top_merchants],
    "ops":     [ops_recent_failed_payments, ops_orders_awaiting_shipment, ops_high_risk_events_today],
    "analyst": [analyst_cohort_conversion, analyst_rail_x_category, analyst_checkout_dropoff],
    # RCA templates each hit ONE §8B discovery target. All 4 must be present.
    "rca":     [rca_lending_90day_emi, rca_attribution_roas, rca_user_cohort_ltv, rca_unit_economics],
}


def all_template_ids() -> list[str]:
    """Every registered template_id — used to verify discovery-target coverage."""
    ids = []
    for arch in TEMPLATES.values():
        ctx = BankContext(rng=random.Random(0), merchant_ids=[], user_ids=[],
                          order_ids=[], campaign_ids=[], loan_ids=[])
        for t in arch:
            try:
                ids.append(t(ctx)[0])
            except Exception:
                pass
    return ids
