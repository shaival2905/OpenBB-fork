"""The Finviz screener page reader: units, missing fields, paging and a layout it cannot read."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_finviz.utils import screener_page as sp


def page(rows, total=None, first=0, size=20):
    data = {"data": {"rows": rows, "totalCount": len(rows) if total is None else total, "firstRow": first, "pageSize": size}}
    return f'<html><script type="application/json" data-ssr-props="ssr-island-1">{json.dumps(data)}</script></html>'


def row(ticker, **numbers):
    return {"text": {"ticker": ticker, "company": f"{ticker} Inc", "industry": "Software", "country": "USA"},
            "numbers": {"price": 10.0, "change": 12.5, "volume": 1000, **numbers}}


def test_units_match_the_data_model():
    rec = sp.rows_to_records([row("AAA", marketCap=26.08, roi=-58.81, debtEq=74.75, grossMargin=12.59)], "financial")[0]
    assert rec["Ticker"] == "AAA" and rec["Company"] == "AAA Inc"
    assert rec["Market Cap"] == pytest.approx(26_080_000)  # the page gives millions
    assert rec["Change"] == pytest.approx(0.125) and rec["ROI"] == pytest.approx(-0.5881)  # whole percents become ratios
    assert rec["Debt/Eq"] == 74.75 and rec["Gross M"] == pytest.approx(0.1259)  # a ratio stays a ratio
    assert rec["Price"] == 10.0 and rec["Volume"] == 1000


def test_a_field_the_page_leaves_out_is_left_out():
    rec = sp.rows_to_records([row("AAA")], "valuation")[0]
    assert "P/E" not in rec and "Fwd P/E" not in rec and rec["Price"] == 10.0


def test_every_view_has_columns():
    for view in sp.VIEW_CODES.values():
        rec = sp.rows_to_records([row("AAA", marketCap=1.0)], view)[0]
        assert rec["Market Cap"] == 1_000_000 and rec["Change"] == pytest.approx(0.125)


@pytest.mark.parametrize("iso, shown", [("2026-10-29T16:30:00", "Oct 29/a"), ("2026-10-29T06:00:00", "Oct 29/b"),
                                          ("2026-10-29T12:00:00", "Oct 29")])
def test_earnings_are_written_as_the_old_table_wrote_them(iso, shown):
    r = row("AAA")
    r["text"]["earningsDate"] = iso
    assert sp.rows_to_records([r], "financial")[0]["Earnings"] == shown


def screen(view=111):
    return SimpleNamespace(v_page=view, url="https://finviz.com/screener.ashx", request_params={"v": view, "s": "ta_topgainers"})


def test_pages_are_followed_until_the_limit_or_the_end():
    pages = {1: page([row("A"), row("B")], total=5, size=2), 3: page([row("C"), row("D")], total=5, size=2),
             5: page([row("E")], total=5, size=2)}
    seen = []

    def fake(url, params=None, stream=False):
        seen.append((params["r"], params["o"]))
        return SimpleNamespace(text=pages[params["r"]])

    with patch("finvizfinance.util._request", fake):
        got = sp.fetch_screener(screen(), "Change", False, 100, sleep_sec=0)
        assert [r["Ticker"] for r in got] == list("ABCDE") and seen == [(1, "-change"), (3, "-change"), (5, "-change")]
        seen.clear()
        assert [r["Ticker"] for r in sp.fetch_screener(screen(), "Change", True, 3, sleep_sec=0)] == list("ABC")
        assert seen[0] == (1, "change")  # ascending has no sign


def test_a_screen_nothing_matches_is_empty_not_an_error():
    with patch("finvizfinance.util._request", lambda url, params=None, stream=False: SimpleNamespace(text=page([], total=0))):
        assert sp.fetch_screener(screen(), "Change", False, 10, sleep_sec=0) == []


def test_a_page_without_the_data_raises_instead_of_looking_empty():
    with pytest.raises(OpenBBError, match="layout"):
        sp.parse_page("<html><table></table></html>")
    with patch("finvizfinance.util._request", lambda url, params=None, stream=False: SimpleNamespace(text="<html></html>")):
        with pytest.raises(OpenBBError):
            sp.fetch_screener(screen(), "Change", False, 10, sleep_sec=0)


def test_an_unknown_view_or_order_is_refused():
    with pytest.raises(OpenBBError):
        sp.fetch_screener(screen(999), "Change", False, 10)
    with pytest.raises(OpenBBError):
        sp.fetch_screener(screen(), "not an order", False, 10)
