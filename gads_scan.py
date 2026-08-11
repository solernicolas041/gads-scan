#!/usr/bin/env python3
"""gads_scan.py — read-only Google Ads account scanner.

One command prints a markdown report of where an account is leaking money, and
whether its numbers can be trusted in the first place.

    python gads_scan.py --customer-id 123-456-7890 --days 30
    python gads_scan.py --customer-id 123-456-7890 --section waste
    python gads_scan.py --customer-id 123-456-7890 --month 2026-07
    python gads_scan.py --all --out ./scans/

This tool never writes to an account. There is no mutation code in this file.

Credentials: google-ads.yaml next to this script (see google-ads.yaml.example).
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
import time
from datetime import date
from pathlib import Path

YAML_PATH = Path(__file__).parent / "google-ads.yaml"

# Google Ads API supported DURING literals. Anything else is rejected by the API
# with INVALID_VALUE_WITH_DURING_OPERATOR, so we validate before spending a call.
VALID_DURING = {
    "TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
    "LAST_BUSINESS_WEEK", "THIS_WEEK_SUN_TODAY", "THIS_WEEK_MON_TODAY",
    "LAST_WEEK_SUN_SAT", "LAST_WEEK_MON_SUN", "THIS_MONTH", "LAST_MONTH",
    "ALL_TIME",
}

DAYS_TO_DURING = {7: "LAST_7_DAYS", 14: "LAST_14_DAYS", 30: "LAST_30_DAYS"}

MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")

QUERY_MAX_ATTEMPTS = 4
QUERY_BACKOFF_S = 2

_TRANSIENT_HTTP = {429, 500, 502, 503, 504}
_TRANSIENT_GRPC = {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL",
                   "RESOURCE_EXHAUSTED", "ABORTED"}
_TRANSIENT_NAMES = {"ServiceUnavailable", "DeadlineExceeded", "InternalServerError",
                    "TooManyRequests", "BadGateway", "Aborted", "RetryError"}


def warn(msg: str) -> None:
    """Never fail silently: a degraded section says so, on stderr."""
    print(f"WARNING: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Date handling
# ---------------------------------------------------------------------------
def month_bounds(month: str) -> tuple[str, str]:
    """'2026-07' -> ('2026-07-01', '2026-07-31'), clamped to today for the
    current month. A client report needs a calendar month; a rolling 30-day
    window quietly straddles two of them and cannot be reconciled with an invoice.
    """
    m = MONTH_RE.match(month or "")
    if not m:
        raise ValueError(f"--month expects YYYY-MM, got {month!r}")
    year, mon = int(m.group(1)), int(m.group(2))
    if not 1 <= mon <= 12:
        raise ValueError(f"--month expects a month between 01 and 12, got {month!r}")
    last = calendar.monthrange(year, mon)[1]
    end = date(year, mon, last)
    today = date.today()
    if end > today:
        if date(year, mon, 1) > today:
            raise ValueError(f"--month {month} is in the future")
        end = today
    return f"{year:04d}-{mon:02d}-01", end.isoformat()


def date_clause(spec: str) -> str:
    """Build the WHERE fragment for a date range.

    spec is either a DURING literal ('LAST_30_DAYS') or a month ('2026-07').
    """
    if MONTH_RE.match(spec or ""):
        start, end = month_bounds(spec)
        return f"segments.date BETWEEN '{start}' AND '{end}'"
    token = (spec or "").upper()
    if token not in VALID_DURING:
        raise ValueError(
            f"invalid date range {spec!r}. Use one of: {', '.join(sorted(VALID_DURING))} "
            f"or a calendar month as YYYY-MM."
        )
    return f"segments.date DURING {token}"


def range_label(spec: str) -> str:
    if MONTH_RE.match(spec or ""):
        start, end = month_bounds(spec)
        return f"{start} to {end}"
    return (spec or "").upper()


# ---------------------------------------------------------------------------
# API plumbing
# ---------------------------------------------------------------------------
def get_client():
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        sys.exit("ERROR: google-ads is not installed. Run: pip install -r requirements.txt")
    if not YAML_PATH.exists():
        sys.exit(f"ERROR: {YAML_PATH.name} not found. Copy google-ads.yaml.example and fill it in.")
    return GoogleAdsClient.load_from_storage(str(YAML_PATH))


def is_transient(exc: BaseException) -> bool:
    """True for a transport blip (DNS, 503, timeout, throttle) — safe to retry.

    A real API error (bad query, bad auth) is never retried: hiding it is worse
    than failing loudly.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _TRANSIENT_HTTP:
        return True
    grpc_code = getattr(exc, "grpc_status_code", None)
    if grpc_code is None and callable(code):
        try:
            grpc_code = code()
        except Exception:  # noqa: BLE001 — a code() that fails tells us nothing
            grpc_code = None
    if getattr(grpc_code, "name", None) in _TRANSIENT_GRPC:
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


def run_query(client, customer_id: str, query: str) -> list:
    service = client.get_service("GoogleAdsService")
    last_error = None
    for attempt in range(1, QUERY_MAX_ATTEMPTS + 1):
        try:
            rows = []
            for batch in service.search_stream(customer_id=customer_id, query=query):
                rows.extend(batch.results)
            return rows
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if not is_transient(exc) or attempt == QUERY_MAX_ATTEMPTS:
                raise
            warn(f"transient API error ({type(exc).__name__}), retry {attempt}/{QUERY_MAX_ATTEMPTS}")
            time.sleep(QUERY_BACKOFF_S * attempt)
    raise last_error  # unreachable, kept for clarity


def enum_name(client, value, enum_type: str | None) -> str:
    """proto-plus enums expose .name; a raw int needs the wrapper lookup."""
    if hasattr(value, "name"):
        return value.name
    if not enum_type:
        return str(value)
    try:
        wrapper = getattr(client.enums, enum_type)
        inner = getattr(wrapper, enum_type[:-4] if enum_type.endswith("Enum") else enum_type)
        return inner.Name(int(value))
    except Exception:  # noqa: BLE001 — degrade to the raw value, never crash a read
        return str(value)


def normalize_customer_id(raw: str) -> str:
    cid = re.sub(r"[^0-9]", "", raw or "")
    if len(cid) != 10:
        raise ValueError(f"--customer-id expects 10 digits, got {raw!r}")
    return cid


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------
def table(headers: list[str], rows: list[list[str]], empty: str = "_(nothing to report)_") -> list[str]:
    if not rows:
        return [empty]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def money(value: float) -> str:
    return f"${value:,.2f}"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def section_conversions(client, cid: str, spec: str) -> list[str]:
    """Section 1 — the foundation. Wrong here, wrong everywhere below."""
    meta = run_query(client, cid, """
        SELECT conversion_action.name, conversion_action.type,
               conversion_action.category, conversion_action.primary_for_goal,
               conversion_action.counting_type
        FROM conversion_action WHERE conversion_action.status != 'REMOVED'""")
    counts: dict[str, float] = {}
    try:
        for r in run_query(client, cid, f"""
                SELECT segments.conversion_action_name, metrics.conversions
                FROM customer WHERE {date_clause(spec)}"""):
            counts[r.segments.conversion_action_name] = r.metrics.conversions
    except Exception as exc:  # noqa: BLE001
        warn(f"conversion counts unavailable: {exc}")

    rows, flagged = [], 0
    for r in meta:
        c = r.conversion_action
        counting = enum_name(client, c.counting_type, "ConversionActionCountingTypeEnum")
        category = enum_name(client, c.category, "ConversionActionCategoryEnum")
        is_lead = category in {"CONTACT", "PHONE_CALL_LEAD", "SUBMIT_LEAD_FORM",
                               "BOOK_APPOINTMENT", "REQUEST_QUOTE", "SIGNUP", "DEFAULT"}
        flag = ""
        if counting == "MANY_PER_CLICK" and is_lead:
            flag, flagged = " (!)", flagged + 1
        rows.append([c.name, enum_name(client, c.type_, "ConversionActionTypeEnum"),
                     category, c.primary_for_goal, counting + flag,
                     f"{counts.get(c.name, 0.0):.1f}"])

    out = ["## 1. Conversion integrity — the foundation"]
    out += table(["Action", "Type", "Category", "Primary", "Counting", "Conversions"], rows,
                 "_(no conversion actions on this account)_")
    if flagged:
        out.append(f"> (!) {flagged} lead action(s) counting MANY_PER_CLICK. "
                   f"That inflates the conversion count and the ROAS built on it. "
                   f"Check these before trusting any number below.")
    else:
        out.append("> No lead action on MANY_PER_CLICK. The counts below can be read at face value.")
    return out


def section_bidding(client, cid: str, spec: str) -> list[str]:
    rows_raw = run_query(client, cid, f"""
        SELECT campaign.name, campaign.bidding_strategy_type,
               campaign.maximize_conversions.target_cpa_micros,
               campaign.maximize_conversion_value.target_roas,
               campaign.target_cpa.target_cpa_micros, campaign.target_roas.target_roas,
               campaign_budget.amount_micros
        FROM campaign WHERE {date_clause(spec)} AND campaign.status = 'ENABLED'""")
    rows, seen = [], set()
    for r in rows_raw:
        c = r.campaign
        if c.name in seen:
            continue
        seen.add(c.name)
        target = "—"
        if c.maximize_conversions.target_cpa_micros:
            target = f"tCPA ${c.maximize_conversions.target_cpa_micros / 1e6:.0f}"
        elif c.target_cpa.target_cpa_micros:
            target = f"tCPA ${c.target_cpa.target_cpa_micros / 1e6:.0f}"
        elif c.maximize_conversion_value.target_roas:
            target = f"tROAS {c.maximize_conversion_value.target_roas * 100:.0f}%"
        elif c.target_roas.target_roas:
            target = f"tROAS {c.target_roas.target_roas * 100:.0f}%"
        rows.append([c.name, enum_name(client, c.bidding_strategy_type, "BiddingStrategyTypeEnum"),
                     target, money(r.campaign_budget.amount_micros / 1e6)])
    return ["## 2. Bidding strategy and budget"] + table(
        ["Campaign", "Bid strategy", "Target", "Budget/day"], rows,
        "_(no enabled campaign in this window)_")


def _segment_table(client, cid: str, spec: str, field: str, enum_type: str | None,
                   title: str, note: str = "") -> list[str]:
    rows_raw = run_query(client, cid, f"""
        SELECT {field}, metrics.cost_micros, metrics.conversions, metrics.conversions_value
        FROM campaign WHERE {date_clause(spec)}
        AND campaign.advertising_channel_type = 'SEARCH' AND campaign.status = 'ENABLED'""")
    agg: dict[str, list[float]] = {}
    key = field.split(".")[-1]
    for r in rows_raw:
        label = enum_name(client, getattr(r.segments, key), enum_type)
        bucket = agg.setdefault(label, [0.0, 0.0, 0.0])
        bucket[0] += r.metrics.cost_micros / 1e6
        bucket[1] += r.metrics.conversions
        bucket[2] += r.metrics.conversions_value
    rows = []
    for label, (cost, conv, value) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        rows.append([label, money(cost), f"{conv:.1f}",
                     money(cost / conv) if conv else "—",
                     f"{value / cost * 100:.0f}%" if cost else "—"])
    out = [f"## {title}"] + table(["Segment", "Spend", "Conv", "CPA", "ROAS"], rows,
                                  "_(no search spend in this window)_")
    if note:
        out.append(f"> {note}")
    return out


def section_device(client, cid: str, spec: str) -> list[str]:
    return _segment_table(client, cid, spec, "segments.device", "DeviceEnum",
                          "3. Device", "One device at twice the CPA of another is a bid adjustment waiting to happen.")


def section_dayofweek(client, cid: str, spec: str) -> list[str]:
    return _segment_table(client, cid, spec, "segments.day_of_week", "DayOfWeekEnum",
                          "4a. Day of week")


def section_hour(client, cid: str, spec: str) -> list[str]:
    return _segment_table(client, cid, spec, "segments.hour", None, "4b. Hour of day",
                          "Judge an hour on 90 days, not 30. A single quiet week is not a pattern.")


def section_waste(client, cid: str, spec: str) -> list[str]:
    rows_raw = run_query(client, cid, f"""
        SELECT ad_group_criterion.keyword.text, campaign.name,
               metrics.cost_micros, metrics.clicks
        FROM keyword_view WHERE {date_clause(spec)}
        AND metrics.cost_micros > 0 AND metrics.conversions = 0
        ORDER BY metrics.cost_micros DESC LIMIT 25""")
    rows, total = [], 0.0
    for r in rows_raw:
        cost = r.metrics.cost_micros / 1e6
        total += cost
        rows.append([r.ad_group_criterion.keyword.text, r.campaign.name,
                     money(cost), r.metrics.clicks, 0])
    out = ["## 5. Wasted spend — keywords with 0 conversions"]
    out += table(["Keyword", "Campaign", "Spend", "Clicks", "Conv"], rows,
                 "_(every keyword that spent also converted)_")
    if rows:
        out.append(f"> {money(total)} spent on zero conversions in this window. "
                   f"Cut only the ones with enough clicks to judge — a keyword with "
                   f"4 clicks has not failed yet, it has not been tested.")
    return out


def section_harvest(client, cid: str, spec: str) -> list[str]:
    rows_raw = run_query(client, cid, f"""
        SELECT search_term_view.search_term, metrics.conversions, metrics.cost_micros
        FROM search_term_view WHERE {date_clause(spec)} AND metrics.conversions > 0
        ORDER BY metrics.conversions DESC LIMIT 20""")
    rows = [[r.search_term_view.search_term, f"{r.metrics.conversions:.1f}",
             money(r.metrics.cost_micros / 1e6)] for r in rows_raw]
    return ["## 6. Converting search terms — what to harvest"] + table(
        ["Search term", "Conv", "Spend"], rows,
        "_(no converting search term in this window)_") + [
        "> Check which of these are not exact keywords yet."]


def section_assets(client, cid: str, spec: str) -> list[str]:
    # campaign.status must be SELECTed to be filtered on in this resource.
    rows_raw = run_query(client, cid, """
        SELECT campaign.name, campaign.status, campaign_asset.field_type
        FROM campaign_asset WHERE campaign.status = 'ENABLED'""")
    per: dict[str, set] = {}
    for r in rows_raw:
        per.setdefault(r.campaign.name, set()).add(
            enum_name(client, r.campaign_asset.field_type, "AssetFieldTypeEnum"))
    expected = {"SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL"}
    rows = [[name, ", ".join(sorted(types & expected)) or "—",
             ", ".join(sorted(expected - types)) or "complete"]
            for name, types in sorted(per.items())]
    return ["## 7. Assets — missing ones cost CTR, and CTR costs Quality Score"] + table(
        ["Campaign", "Present", "Missing"], rows,
        "_(no campaign asset found — worth checking in the UI)_")


def section_trend(client, cid: str, spec: str) -> list[str]:
    def kpi(window: str) -> tuple[float, float, float]:
        rows = run_query(client, cid, f"""
            SELECT metrics.cost_micros, metrics.conversions, metrics.conversions_value
            FROM customer WHERE {date_clause(window)}""")
        if not rows:
            return (0.0, 0.0, 0.0)
        m = rows[0].metrics
        return (m.cost_micros / 1e6, m.conversions, m.conversions_value)

    this_cost, this_conv, this_val = kpi("THIS_MONTH")
    last_cost, last_conv, last_val = kpi("LAST_MONTH")
    arrow = lambda now, prev: "up" if now > prev else ("down" if now < prev else "flat")
    rows = [
        ["Spend", money(last_cost), money(this_cost), arrow(this_cost, last_cost)],
        ["Conversions", f"{last_conv:.1f}", f"{this_conv:.1f}", arrow(this_conv, last_conv)],
        ["Conv. value", money(last_val), money(this_val), arrow(this_val, last_val)],
    ]
    return ["## 8. Month over month"] + table(
        ["Metric", "Last month", "This month (MTD)", ""], rows) + [
        "> This month is partial. Compare the shape, not the totals."]


SECTIONS = {
    "conversions": section_conversions,
    "bidding": section_bidding,
    "device": section_device,
    "dayofweek": section_dayofweek,
    "hour": section_hour,
    "waste": section_waste,
    "harvest": section_harvest,
    "assets": section_assets,
    "trend": section_trend,
}


def run_section(fn, client, cid: str, spec: str) -> list[str]:
    """One broken query degrades its own section and nothing else."""
    try:
        return fn(client, cid, spec)
    except Exception as exc:  # noqa: BLE001
        name = fn.__name__.replace("section_", "")
        warn(f"section {name} failed: {exc}")
        return [f"## {name} — DEGRADED", f"> This section failed: `{exc}`. "
                f"The rest of the scan is unaffected."]


def scan(customer_id: str, spec: str = "LAST_30_DAYS",
         only: str | None = None, client=None) -> str:
    client = client or get_client()
    wanted = [only] if only else list(SECTIONS)
    out = [f"# Google Ads scan — {customer_id} ({range_label(spec)})", ""]
    if not only:
        out += ["Read section 1 before believing sections 2 to 8.", ""]
    for name in wanted:
        out += run_section(SECTIONS[name], client, customer_id, spec) + [""]
    return "\n".join(out).rstrip() + "\n"


def list_accounts(client) -> list[tuple[str, str]]:
    """Every non-manager account under the login customer id in google-ads.yaml."""
    login_cid = str(client.login_customer_id)
    rows = run_query(client, login_cid, """
        SELECT customer_client.id, customer_client.descriptive_name,
               customer_client.manager, customer_client.status
        FROM customer_client WHERE customer_client.status = 'ENABLED'""")
    return [(str(r.customer_client.id), r.customer_client.descriptive_name)
            for r in rows if not r.customer_client.manager]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gads_scan.py",
        description="Read-only Google Ads account scanner. Prints markdown; never writes.")
    p.add_argument("--customer-id", help="Account to scan, e.g. 123-456-7890")
    p.add_argument("--all", action="store_true",
                   help="Scan every account under the MCC in google-ads.yaml")
    p.add_argument("--days", type=int, default=30, choices=sorted(DAYS_TO_DURING),
                   help="Rolling window in days (default: 30)")
    p.add_argument("--month", help="Exact calendar month as YYYY-MM (use this for client reports)")
    p.add_argument("--range", dest="during", help=f"A DURING literal: {', '.join(sorted(VALID_DURING))}")
    p.add_argument("--section", choices=sorted(SECTIONS), help="Print one section only")
    p.add_argument("--out", help="Directory to write one .md file per account")
    p.add_argument("--list-accounts", action="store_true", help="List accounts under the MCC and exit")
    return p


def resolve_spec(args) -> str:
    if args.month:
        return args.month
    if args.during:
        return args.during.upper()
    return DAYS_TO_DURING[args.days]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not (args.customer_id or args.all or args.list_accounts):
        parser.error("give --customer-id, --all, or --list-accounts")

    try:
        spec = resolve_spec(args)
        date_clause(spec)  # fail before spending an API call
    except ValueError as exc:
        parser.error(str(exc))

    client = get_client()

    if args.list_accounts:
        for cid, name in list_accounts(client):
            print(f"{cid}\t{name}")
        return 0

    targets: list[tuple[str, str]]
    if args.all:
        targets = list_accounts(client)
    else:
        try:
            targets = [(normalize_customer_id(args.customer_id), "")]
        except ValueError as exc:
            parser.error(str(exc))

    out_dir = Path(args.out).expanduser() if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for cid, name in targets:
        try:
            report = scan(cid, spec, only=args.section, client=client)
        except Exception as exc:  # noqa: BLE001 — one bad account must not stop a sweep
            failures += 1
            warn(f"account {cid} ({name or 'unnamed'}) failed entirely: {exc}")
            continue
        if out_dir:
            path = out_dir / f"{cid}.md"
            path.write_text(report, encoding="utf-8")
            print(f"wrote {path}")
        else:
            print(report)

    return 1 if failures and failures == len(targets) else 0


if __name__ == "__main__":
    sys.exit(main())
