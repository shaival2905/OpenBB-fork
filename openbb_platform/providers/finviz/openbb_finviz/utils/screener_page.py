"""Read the Finviz screener from the page's embedded JSON.

Finviz moved the screener to ``/screener/stocks`` and stopped rendering the result as an HTML ``<table>``: the rows now
arrive as JSON in a ``data-ssr-props`` script island. ``finvizfinance`` still looks for the old table, finds none and
reports "No ticker found", so every screen came back empty. The library is still used to build the filter parameters
(``set_filter``); this module fetches the pages and turns the island into the same columns, in the same units, the old
table produced, so the data model above it does not change.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from time import sleep
from typing import Any

from openbb_core.app.model.abstract.error import OpenBBError

PCT = 0.01  # the page gives whole percents; the model holds ratios (the frontend multiplies by 100)
MILLIONS = 1_000_000.0  # market cap and share counts arrive in millions

_ISLAND = re.compile(r'<script[^>]*data-ssr-props="ssr-island-1"[^>]*>(.*?)</script>', re.S)

TEXT_COLUMNS = {
    "ticker": "Ticker",
    "company": "Company",
    "sector": "Sector",
    "industry": "Industry",
    "country": "Country",
}

_TAIL = {"price": ("Price", 1.0), "change": ("Change", PCT), "volume": ("Volume", 1.0)}

# page key -> (column the data model reads, multiplier), per screener view
NUMBER_COLUMNS: dict[str, dict[str, tuple[str, float]]] = {
    "overview": {"marketCap": ("Market Cap", MILLIONS), "PE": ("P/E", 1.0)},
    "valuation": {
        "marketCap": ("Market Cap", MILLIONS),
        "PE": ("P/E", 1.0),
        "forwardPE": ("Fwd P/E", 1.0),
        "PEG": ("PEG", 1.0),
        "PS": ("P/S", 1.0),
        "PB": ("P/B", 1.0),
        "PC": ("P/C", 1.0),
        "PFCF": ("P/FCF", 1.0),
        "epsYoY": ("EPS This Y", PCT),
        "epsYoY1": ("EPS Next Y", PCT),
        "eps5Years": ("EPS Past 5Y", PCT),
        "estLTGrowth": ("EPS Next 5Y", PCT),
        "sales5Years": ("Sales Past 5Y", PCT),
    },
    "financial": {
        "marketCap": ("Market Cap", MILLIONS),
        "dividendYield": ("Dividend", PCT),
        "roa": ("ROA", PCT),
        "roe": ("ROE", PCT),
        "roi": ("ROI", PCT),
        "curRatio": ("Curr R", 1.0),
        "quickRatio": ("Quick R", 1.0),
        "ltdebtEq": ("LTDebt/Eq", 1.0),
        "debtEq": ("Debt/Eq", 1.0),
        "grossMargin": ("Gross M", PCT),
        "operMargin": ("Oper M", PCT),
        "netMargin": ("Profit M", PCT),
    },
    "ownership": {
        "marketCap": ("Market Cap", MILLIONS),
        "sharesOutstanding2": ("Outstanding", MILLIONS),
        "sharesFloat": ("Float", MILLIONS),
        "insiderOwn": ("Insider Own", PCT),
        "insiderTrans": ("Insider Trans", PCT),
        "instOwn": ("Inst Own", PCT),
        "instTrans": ("Inst Trans", PCT),
        "shortInterestShare": ("Float Short", PCT),
        "shortInterestRatio": ("Short Ratio", 1.0),
        "averageVolume": ("Avg Volume", 1.0),
    },
    "performance": {
        "marketCap": ("Market Cap", MILLIONS),
        "perf1w": ("Perf Week", PCT),
        "perf4w": ("Perf Month", PCT),
        "perf13w": ("Perf Quart", PCT),
        "perf26w": ("Perf Half", PCT),
        "perf52w": ("Perf Year", PCT),
        "perfYtd": ("Perf YTD", PCT),
        "volatility1w": ("Volatility W", PCT),
        "volatility4w": ("Volatility M", PCT),
        "averageVolume": ("Avg Volume", 1.0),
        "relativeVolume": ("Rel Volume", 1.0),
    },
    "technical": {
        "marketCap": ("Market Cap", MILLIONS),
        "beta": ("Beta", 1.0),
        "averageTrueRange": ("ATR", 1.0),
        "sma20": ("SMA20", PCT),
        "sma50": ("SMA50", PCT),
        "sma200": ("SMA200", PCT),
        "high52w": ("52W High", PCT),
        "low52w": ("52W Low", PCT),
        "rsi": ("RSI", 1.0),
        "changeopen": ("from Open", PCT),
        "gap": ("Gap", PCT),
    },
}

VIEW_CODES = {111: "overview", 121: "valuation", 161: "financial", 131: "ownership", 141: "performance", 171: "technical"}


def _earnings(value: Any) -> str | None:
    """'2026-10-29T16:30:00' as the old table wrote it: 'Oct 29/a' (after the close), '/b' (before the open), or just the day."""
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    day = f"{at:%b} {at.day}"
    minutes = at.hour * 60 + at.minute
    if minutes >= 16 * 60:
        return f"{day}/a"
    if minutes < 9 * 60 + 30:
        return f"{day}/b"
    return day


def parse_page(html: str) -> dict[str, Any]:
    """The screener island of one page: ``rows``, ``totalCount``, ``firstRow`` and ``pageSize``."""
    found = _ISLAND.search(html)
    if found is None:
        raise OpenBBError("Finviz returned a screener page without its data; the site layout may have changed again.")
    try:
        data = json.loads(found.group(1)).get("data")
    except ValueError as e:
        raise OpenBBError(f"Finviz screener data could not be read: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise OpenBBError("Finviz screener data has no rows; the site layout may have changed again.")
    return data


def rows_to_records(rows: list[dict], view: str) -> list[dict]:
    """Page rows as records keyed by the column names (and units) the data model reads."""
    numbers = {**NUMBER_COLUMNS[view], **_TAIL}
    out: list[dict] = []
    for row in rows:
        text, nums = row.get("text") or {}, row.get("numbers") or {}
        rec: dict[str, Any] = {col: text.get(key) for key, col in TEXT_COLUMNS.items() if text.get(key) is not None}
        if view == "financial" and text.get("earningsDate"):
            rec["Earnings"] = _earnings(text["earningsDate"])
        for key, (col, scale) in numbers.items():
            val = nums.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                rec[col] = val * scale if scale != 1.0 else val
        out.append(rec)
    return out


def fetch_screener(screen: Any, order: str, ascend: bool, limit: int, sleep_sec: float = 0.1) -> list[dict]:
    """Every page of the screen `screen` (a finvizfinance screener with its filters set), up to `limit` rows.

    An empty result is an empty list, never an error: Finviz answers a screen nothing matches with a normal page that has
    no rows. A page that is not a screener page at all raises, so a layout change is not mistaken for "no matches"."""
    from finvizfinance import util
    from finvizfinance.constants import order_dict

    view = VIEW_CODES.get(getattr(screen, "v_page", None))
    if view is None:
        raise OpenBBError(f"Unsupported Finviz screener view: {getattr(screen, 'v_page', None)}")
    if order not in order_dict:
        raise OpenBBError(f"Invalid screener order '{order}'.")
    params = dict(screen.request_params)
    params["o"] = ("" if ascend else "-") + order_dict[order]
    records: list[dict] = []
    first = 1
    while True:
        html = util._request(screen.url, params={**params, "r": first}).text  # pylint: disable=protected-access
        data = parse_page(html)
        page = rows_to_records(data["rows"], view)
        records.extend(page)
        size = int(data.get("pageSize") or len(page) or 1)
        total = int(data.get("totalCount") or 0)
        first += size
        if not page or len(records) >= limit or first > total:
            break
        sleep(sleep_sec)
    return records[:limit]
