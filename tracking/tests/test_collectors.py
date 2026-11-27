"""Tests hors ligne : on simule les réponses des API pour vérifier la lecture des données."""
from datetime import date
from types import SimpleNamespace

import pytest

from flugia_tracking.collectors import base, ga4, geo, google_ads, google_calendar, linkedin, meta, search_console, stripe_billing

START, END = date(2026, 11, 1), date(2026, 11, 2)


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    for k in ["GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
              "GOOGLE_ADS_CUSTOMER_ID", "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_IG_USER_ID", "LINKEDIN_ACCESS_TOKEN",
              "LINKEDIN_AD_ACCOUNT_ID", "LINKEDIN_ORGANIZATION_ID", "GA4_PROPERTY_ID", "SEARCH_CONSOLE_SITE",
              "GOOGLE_CALENDAR_ID", "STRIPE_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "META_PAGE_ID"]:
        monkeypatch.setenv(k, "x")
    for mod in (ga4, search_console, google_calendar):
        monkeypatch.setattr(mod, "google_access_token", lambda *a, **k: "tok")


def by(result):
    return {(m.day, m.channel, m.metric): m.value for m in result.metrics}


def test_google_ads(monkeypatch):
    c = google_ads.GoogleAdsCollector()
    monkeypatch.setattr(c, "_access_token", lambda: "tok")
    monkeypatch.setattr(c, "http", lambda *a, **k: [{"results": [
        {"segments": {"date": "2026-11-01"}, "metrics": {"impressions": "1000", "clicks": "30", "costMicros": "25500000"}}]}])
    r = by(c.collect(START, END))
    assert r[("2026-11-01", "google_ads", "impressions")] == 1000
    assert r[("2026-11-01", "google_ads", "spend")] == 25.5


def test_google_ads_compte_de_service(monkeypatch):
    for k in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN"):
        monkeypatch.delenv(k)
    monkeypatch.setattr(google_ads, "google_access_token", lambda scopes: "sa-tok")
    c = google_ads.GoogleAdsCollector()
    assert c.enabled()
    seen = {}
    def http(method, url, headers=None, **k):
        seen.update(headers=headers, url=url)
        return [{"results": [{"segments": {"date": "2026-11-01"}, "metrics": {"clicks": "7"}}]}]
    monkeypatch.setattr(c, "http", http)
    r = by(c.collect(START, END))
    assert r[("2026-11-01", "google_ads", "clicks")] == 7
    assert seen["headers"] == {"Authorization": "Bearer sa-tok"}  # pas de jeton développeur
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    assert not c.enabled()


def test_meta(monkeypatch):
    c = meta.MetaCollector()
    def http(method, url, **k):
        if "insights" in url and "act_" in url:
            return {"data": [{"date_start": "2026-11-01", "impressions": "500", "reach": "400", "clicks": "5", "spend": "12.3"}]}
        if url.endswith("/x"):  # jeton de page
            return {"access_token": "page-tok"}
        if k["params"].get("metric") == "page_media_view":
            raise base.CollectorError("métrique retirée")
        if k["params"].get("metric", "").startswith("page_"):
            assert k["params"]["access_token"] == "page-tok"
            return {"data": [{"values": [{"value": 90, "end_time": "2026-11-02T08:00:00+0000"}]}]}
        return {"data": [{"values": [{"value": 77, "end_time": "2026-11-02T08:00:00+0000"}]}]}
    monkeypatch.setattr(c, "http", http)
    res = c.collect(START, END)
    r = by(res)
    assert r[("2026-11-01", "meta_ads", "spend")] == 12.3
    assert r[("2026-11-02", "instagram", "reach")] == 77
    assert r[("2026-11-01", "facebook", "impressions")] == 90  # repli sur page_impressions
    assert r[("2026-11-01", "facebook", "engagement")] == 90
    assert not res.warnings


def test_meta_instagram_decoupe_30_jours(monkeypatch):
    from datetime import timedelta
    c = meta.MetaCollector()
    monkeypatch.delenv("META_AD_ACCOUNT_ID")
    monkeypatch.delenv("META_PAGE_ID")
    calls = []
    def http(method, url, params=None, **k):
        assert params["until"] - params["since"] <= 30 * 86400
        calls.append(params["since"])
        return {"data": []}
    monkeypatch.setattr(c, "http", http)
    res = c.collect(END - timedelta(days=89), END)
    assert len(calls) == 3 and not res.warnings


def test_linkedin(monkeypatch):
    c = linkedin.LinkedInCollector()
    def http(method, url, **k):
        if "adAnalytics" in url:
            assert "accounts=List(urn%3Ali%3AsponsoredAccount%3Ax)" in url
            return {"elements": [{"impressions": 300, "clicks": 3, "costInLocalCurrency": "9.5",
                                  "dateRange": {"start": {"year": 2026, "month": 11, "day": 1}}}]}
        return {"elements": [{"timeRange": {"start": 1793491200000}, "totalShareStatistics": {"impressionCount": 800, "clickCount": 8}}]}
    monkeypatch.setattr(c, "http", http)
    r = by(c.collect(START, END))
    assert r[("2026-11-01", "linkedin_ads", "spend")] == 9.5
    assert r[("2026-11-01", "linkedin_organique", "impressions")] == 800


def test_ga4(monkeypatch):
    c = ga4.GA4Collector()
    def http(method, url, json=None, **k):
        dims = [d["name"] for d in json["dimensions"]]
        if dims == ["date", "sessionDefaultChannelGroup"]:
            return {"rows": [{"dimensionValues": [{"value": "20261101"}, {"value": "Referral"}], "metricValues": [{"value": "40"}, {"value": "30"}]}]}
        if dims == ["date", "sessionSource"] and "orGroup" in json["dimensionFilter"]:
            return {"rows": [{"dimensionValues": [{"value": "20261101"}, {"value": "chatgpt.com"}], "metricValues": [{"value": "4"}]}]}
        if dims == ["date", "sessionSource"] and "andGroup" in json["dimensionFilter"]:
            return {"rows": [{"dimensionValues": [{"value": "20261101"}, {"value": "www.lecho.be"}], "metricValues": [{"value": "1"}]}]}
        if dims == ["date", "sessionSource"]:
            return {"rows": [{"dimensionValues": [{"value": "20261101"}, {"value": "www.lecho.be"}], "metricValues": [{"value": "12"}]}]}
        return {"rows": [{"dimensionValues": [{"value": "20261101"}, {"value": "Organic Search"}, {"value": "sign_up"}], "metricValues": [{"value": "2"}]}]}
    monkeypatch.setattr(c, "http", http)
    r = by(c.collect(START, END))
    assert r[("2026-11-01", "site:referral", "sessions")] == 40
    assert r[("2026-11-01", "geo", "ai_sessions")] == 4
    assert r[("2026-11-01", "site:seo", "web_signups")] == 2
    assert r[("2026-11-01", "referral:lecho.be", "referral_sessions")] == 12
    assert r[("2026-11-01", "referral:lecho.be", "referral_signups")] == 1


def test_search_console(monkeypatch):
    c = search_console.SearchConsoleCollector()
    def http(method, url, json=None, **k):
        if json["dimensions"] == ["date"]:
            return {"rows": [{"keys": ["2026-11-01"], "impressions": 900, "clicks": 20}]}
        return {"rows": [{"keys": ["2026-11-01", "flugia avis"], "impressions": 30, "clicks": 10},
                         {"keys": ["2026-11-01", "agent ia pme"], "impressions": 50, "clicks": 1}]}
    monkeypatch.setattr(c, "http", http)
    r = by(c.collect(START, END))
    assert r[("2026-11-01", "seo", "impressions")] == 900
    assert r[("2026-11-01", "seo_marque", "branded_searches")] == 30


def test_google_calendar(monkeypatch):
    c = google_calendar.GoogleCalendarCollector()
    items = [
        {"summary": "Démo Flugia - Garage X", "created": "2026-11-01T09:00:00Z", "start": {"dateTime": "2026-11-02T10:00:00+01:00"}},
        {"summary": "Déjeuner", "created": "2026-11-01T09:00:00Z", "start": {"dateTime": "2026-11-02T12:00:00+01:00"}},
        {"summary": "Demo Flugia", "status": "cancelled", "created": "2026-11-01T09:00:00Z", "start": {"dateTime": "2026-11-01T15:00:00Z"}},
    ]
    monkeypatch.setattr(c, "http", lambda *a, **k: {"items": items})
    r = by(c.collect(START, END))
    assert r[("2026-11-01", "demos", "demos_booked")] == 1
    assert r[("2026-11-02", "demos", "demos_held")] == 1
    assert r[("2026-11-01", "demos", "demos_cancelled")] == 1


def test_stripe(monkeypatch):
    c = stripe_billing.StripeCollector()
    sub = {"id": "sub_1", "status": "active", "customer": {"id": "cus_1", "name": "Salon A", "metadata": {"utm_source": "linkedin"}},
           "trial_start": 1760000000, "trial_end": 1761296000, "start_date": 1760000000, "created": 1760000000,
           "current_period_end": 1765000000, "items": {"data": [{"quantity": 1, "price": {"unit_amount": 4900, "nickname": "standard", "recurring": {"interval": "month", "interval_count": 1}}}]}}
    monkeypatch.setattr(c, "http", lambda m, url, **k: {"data": [sub] if "subscriptions" in url else [], "has_more": False})
    res = c.collect(START, END)
    assert any(m.metric == "mrr" and m.value == 49 for m in res.metrics)
    assert res.accounts[0]["converted_at"] and res.accounts[0]["first_touch_source"] == "linkedin"


def test_geo(monkeypatch):
    c = geo.GeoCollector()
    c.forced = True
    resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Essayez Flugia ou Sintra.")])
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **k: resp)))
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    res = c.collect(START, END)
    totals = {(m.channel, m.metric): m.value for m in res.metrics}
    assert totals[("geo", "ai_mention_rate")] == 100.0
    assert {m.day for m in res.metrics} == {str(END)}  # daté comme les autres sources
    assert ("geo_concurrent:Sintra", "ai_mentions") in totals
