"""Marketing dashboard: ad spend against the leads, qualified leads and deals it produced.

- Spend comes from ``ad_insights_daily`` (daily sync), per ad, in the ad account's
  currency, and is converted into the deal currency with ``settings.currency_rates``
  (AED → USD by default, since the AED is pegged).
- Leads are CRM leads from lead ads (``source = facebook``) created in the period;
  qualified / won follow the status flags, like the pipeline dashboard.
- Cost per lead = spend ÷ CRM leads; ROAS = won deal value ÷ spend.
"""

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csv_export import to_csv
from app.models.ads import Ad, AdCampaign, AdInsightDaily, AdSet, Integration
from app.models.configuration import LeadStatus
from app.models.leads import Campaign, Lead
from app.schemas.reports import (
    AdRow,
    MarketingDay,
    MarketingKpis,
    MarketingReport,
    Metric,
)
from app.services.access import CurrentUser, visible_user_ids
from app.services.report_service import (
    ReportFilters,
    _campaign_filter,
    _fill_days,
    _local_day,
    _org,
    _people_filter,
    _window,
)

MENU = "dashboard.marketing"
AD_SOURCES = ("facebook",)  # TikTok, LinkedIn and Snapchat join when their connectors do


def _rates(settings: dict[str, Any], deal_currency: str) -> dict[str, Decimal]:
    rates = {k.upper(): Decimal(str(v)) for k, v in (settings.get("currency_rates") or {}).items()}
    rates[deal_currency.upper()] = Decimal(1)
    return rates


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def _metric(now: float | None, before: float | None) -> Metric:
    return Metric(value=float(now or 0), previous=float(before or 0))


async def marketing(
    session: AsyncSession, current: CurrentUser, f: ReportFilters
) -> MarketingReport:
    """Spend, cost per lead, cost per MQL and ROAS, with the ad drill-down."""
    org = await _org(session, current)
    w = _window(f, org.timezone or "Asia/Dubai")
    deal_currency = ((org.settings or {}).get("deal_value") or {}).get(
        "currency"
    ) or org.default_currency
    rates = _rates(org.settings or {}, deal_currency)
    owners = await visible_user_ids(session, current, MENU)

    # Which ad campaigns are in view: campaign / programme / market filters act on the
    # CRM campaign each ad campaign feeds.
    crm_filter = _campaign_filter(f)
    ad_campaign_scope = [AdCampaign.organization_id == current.organization_id]
    crm_conditions = _crm_campaign_conditions(f)
    if crm_conditions:
        ad_campaign_scope.append(
            AdCampaign.crm_campaign_id.in_(
                select(Campaign.id).where(
                    Campaign.organization_id == current.organization_id, *crm_conditions
                )
            )
        )

    async def spend_rows(start: date, end: date) -> list[Any]:
        return list(
            (
                await session.execute(
                    select(
                        AdCampaign.id,
                        AdSet.id,
                        Ad.id,
                        AdInsightDaily.day,
                        AdInsightDaily.currency,
                        AdInsightDaily.spend,
                        AdInsightDaily.impressions,
                        AdInsightDaily.clicks,
                        AdInsightDaily.platform_leads,
                    )
                    .join(AdSet, AdSet.ad_campaign_id == AdCampaign.id)
                    .join(Ad, Ad.ad_set_id == AdSet.id)
                    .join(AdInsightDaily, AdInsightDaily.ad_id == Ad.id)
                    .where(
                        *ad_campaign_scope, AdInsightDaily.day >= start, AdInsightDaily.day <= end
                    )
                )
            ).all()
        )

    def convert(amount: Decimal, currency: str) -> float:
        return float(amount * rates.get(currency.upper(), Decimal(1)))

    now_spend = await spend_rows(w.period.date_from, w.period.date_to)
    prev_spend = await spend_rows(w.period.previous_from, w.period.previous_to)
    currencies = sorted({r[4] for r in now_spend + prev_spend})
    missing = [c for c in currencies if c.upper() not in rates]

    lead_base = [
        Lead.organization_id == current.organization_id,
        Lead.deleted_at.is_(None),
        Lead.source.in_(AD_SOURCES),
        *_people_filter(Lead.owner_user_id, owners, f),
        *crm_filter,
    ]
    is_mql = LeadStatus.is_mql.is_(True)
    is_won = LeadStatus.is_won.is_(True)

    async def lead_totals(start: Any, end: Any) -> Any:
        return (
            await session.execute(
                select(
                    func.count(),
                    func.count(case((is_mql, 1))),
                    func.count(case((is_won, 1))),
                    func.coalesce(func.sum(case((is_won, Lead.deal_value))), 0),
                )
                .select_from(Lead)
                .join(LeadStatus, LeadStatus.id == Lead.status_id)
                .where(*lead_base, Lead.created_at >= start, Lead.created_at < end)
            )
        ).one()

    now_l = await lead_totals(w.start, w.end)
    prev_l = await lead_totals(w.prev_start, w.prev_end)
    spend_now = sum(convert(r[5], r[4]) for r in now_spend)
    spend_prev = sum(convert(r[5], r[4]) for r in prev_spend)

    def per(total: float, count: float) -> float:
        return total / count if count else 0.0

    kpis = MarketingKpis(
        spend=_metric(spend_now, spend_prev),
        leads=_metric(now_l[0], prev_l[0]),
        cost_per_lead=_metric(per(spend_now, now_l[0]), per(spend_prev, prev_l[0])),
        qualified=_metric(now_l[1], prev_l[1]),
        cost_per_qualified=_metric(per(spend_now, now_l[1]), per(spend_prev, prev_l[1])),
        won=_metric(now_l[2], prev_l[2]),
        won_value=_metric(now_l[3], prev_l[3]),
        roas=_metric(per(float(now_l[3] or 0), spend_now), per(float(prev_l[3] or 0), spend_prev)),
        impressions=sum(r[6] for r in now_spend),
        clicks=sum(r[7] for r in now_spend),
        platform_leads=sum(r[8] for r in now_spend),
    )

    spend_by_day: dict[date, float] = defaultdict(float)
    for r in now_spend:
        spend_by_day[r[3]] += convert(r[5], r[4])
    day = _local_day(Lead.created_at, w.tz)
    leads_by_day = dict(
        (
            await session.execute(
                select(day, func.count())
                .where(*lead_base, Lead.created_at >= w.start, Lead.created_at < w.end)
                .group_by(day)
            )
        ).all()
    )
    daily = [
        MarketingDay(day=d, spend=round(spend_by_day.get(d, 0.0), 2), leads=count)
        for d, count in _fill_days(leads_by_day, f, 0)
    ]

    # Drill-down: spend per ad (rolled up) and CRM results per ad (leads carry ad_id).
    totals: dict[tuple[str, uuid.UUID], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    parents: dict[uuid.UUID, uuid.UUID | None] = {}
    for camp_id, set_id, ad_id, _, currency, spend, impressions, clicks, platform in now_spend:
        parents[set_id], parents[ad_id], parents[camp_id] = camp_id, set_id, None
        for key in (("campaign", camp_id), ("adset", set_id), ("ad", ad_id)):
            bucket = totals[key]
            bucket["spend"] += convert(spend, currency)
            bucket["impressions"] += impressions
            bucket["clicks"] += clicks
            bucket["platform_leads"] += platform
    lead_rows = (
        await session.execute(
            select(
                AdCampaign.id,
                AdSet.id,
                Ad.id,
                func.count(),
                func.count(case((is_mql, 1))),
                func.count(case((is_won, 1))),
                func.coalesce(func.sum(case((is_won, Lead.deal_value))), 0),
            )
            .select_from(Lead)
            .join(LeadStatus, LeadStatus.id == Lead.status_id)
            .join(Ad, Ad.id == Lead.ad_id)
            .join(AdSet, AdSet.id == Ad.ad_set_id)
            .join(AdCampaign, and_(AdCampaign.id == AdSet.ad_campaign_id, *ad_campaign_scope))
            .where(*lead_base, Lead.created_at >= w.start, Lead.created_at < w.end)
            .group_by(AdCampaign.id, AdSet.id, Ad.id)
        )
    ).all()
    for camp_id, set_id, ad_id, leads, mql, won, value in lead_rows:
        parents[set_id], parents[ad_id], parents[camp_id] = camp_id, set_id, None
        for key in (("campaign", camp_id), ("adset", set_id), ("ad", ad_id)):
            bucket = totals[key]
            bucket["leads"] += leads
            bucket["qualified"] += mql
            bucket["won"] += won
            bucket["won_value"] += float(value or 0)

    names: dict[uuid.UUID, tuple[str, uuid.UUID | None]] = {}
    ids = {key[1] for key in totals}
    if ids:
        for model in (AdCampaign, AdSet, Ad):
            columns = [model.id, model.name] + (
                [model.crm_campaign_id] if model is AdCampaign else []
            )
            for row in (await session.execute(select(*columns).where(model.id.in_(ids)))).all():
                names[row[0]] = (row[1], row[2] if model is AdCampaign else None)
    rows = [
        AdRow(
            level=level,
            id=item_id,
            parent_id=parents.get(item_id),
            name=names.get(item_id, ("(unknown)", None))[0],
            crm_campaign_id=names.get(item_id, ("", None))[1],
            spend=round(b["spend"], 2),
            impressions=int(b["impressions"]),
            clicks=int(b["clicks"]),
            platform_leads=int(b["platform_leads"]),
            leads=int(b["leads"]),
            qualified=int(b["qualified"]),
            won=int(b["won"]),
            won_value=round(b["won_value"], 2),
            cost_per_lead=_ratio(b["spend"], b["leads"]),
            roas=_ratio(b["won_value"], b["spend"]),
        )
        for (level, item_id), b in totals.items()
    ]
    order = {"campaign": 0, "adset": 1, "ad": 2}
    rows.sort(key=lambda r: (order[r.level], -r.spend, -r.leads))

    integration = await session.scalar(
        select(Integration).where(
            Integration.organization_id == current.organization_id, Integration.provider == "meta"
        )
    )
    return MarketingReport(
        period=w.period,
        currency=deal_currency,
        spend_currencies=currencies,
        missing_rates=missing,
        connected=bool(integration and integration.status == "connected"),
        last_spend_sync_at=integration.last_spend_sync_at if integration else None,
        kpis=kpis,
        daily=daily,
        rows=rows,
    )


def _crm_campaign_conditions(f: ReportFilters) -> list[Any]:
    conditions: list[Any] = []
    if f.campaign_ids:
        conditions.append(Campaign.id.in_(f.campaign_ids))
    if f.program_ids:
        conditions.append(Campaign.program_id.in_(f.program_ids))
    if f.market_ids:
        conditions.append(Campaign.market_id.in_(f.market_ids))
    return conditions


def marketing_csv(report: MarketingReport) -> str:
    """The drill-down as CSV."""
    c = report.currency
    return to_csv(
        [
            "Level",
            "Name",
            f"Spend ({c})",
            "Impressions",
            "Clicks",
            "Meta leads",
            "CRM leads",
            "MQL",
            "Won",
            f"Won value ({c})",
            f"Cost per lead ({c})",
            "ROAS",
        ],  # fmt: skip
        (
            [
                {"campaign": "Campaign", "adset": "Ad set", "ad": "Ad"}[r.level],
                r.name,
                r.spend,
                r.impressions,
                r.clicks,
                r.platform_leads,
                r.leads,
                r.qualified,
                r.won,
                r.won_value,
                round(r.cost_per_lead, 2) if r.cost_per_lead is not None else None,
                round(r.roas, 2) if r.roas is not None else None,
            ]
            for r in report.rows
        ),
    )
