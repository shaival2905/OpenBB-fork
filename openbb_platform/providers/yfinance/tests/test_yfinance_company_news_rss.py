"""YFinance company news falls back to Yahoo's RSS headline feed when Ticker.get_news() returns nothing."""

import asyncio

from openbb_yfinance.models import company_news

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo! Finance: RY.TO News</title>
<item><title>Royal Bank Of Canada And 2 More Canadian Dividend Stocks</title>
<link>https://finance.yahoo.com/news/royal-bank-171011.html</link>
<description>Three dividend payers worth a look.</description>
<guid isPermaLink="false">abc-123</guid>
<pubDate>Fri, 02 Oct 2026 17:10:11 +0000</pubDate></item>
<item><title>No link item</title><pubDate>Fri, 02 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""


class _EmptyTicker:
    def __init__(self, sym):
        self.sym = sym

    def get_news(self, *a, **k):
        return []  # Yahoo retired the /xhr/ncp endpoint yfinance uses; it now returns 404 and yfinance returns []


def test_rss_fallback_when_get_news_is_empty(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", _EmptyTicker)
    seen = []

    def fake_rss(sym):
        seen.append(sym)
        return RSS

    monkeypatch.setattr(company_news, "_yahoo_rss", fake_rss)
    query = company_news.YFinanceCompanyNewsFetcher.transform_query({"symbol": "RY.TO"})
    rows = asyncio.run(company_news.YFinanceCompanyNewsFetcher.aextract_data(query, None))
    assert seen == ["RY.TO"]
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "RY.TO" and r["title"].startswith("Royal Bank") and r["url"].endswith("royal-bank-171011.html")
    assert r["summary"] == "Three dividend payers worth a look." and r["id"] == "abc-123"
    data = company_news.YFinanceCompanyNewsFetcher.transform_data(query, rows)
    assert data[0].date.year == 2026 and data[0].date.hour == 17
