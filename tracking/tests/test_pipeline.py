from flugia_tracking import alerts, dashboard, kpis
from flugia_tracking.db import DB
from flugia_tracking.demo import seed


def test_demo_pipeline(tmp_path):
    db = DB(f"sqlite:///{tmp_path / 't.db'}")
    as_of = seed(db)
    snap = kpis.compute(db, as_of)
    assert snap["visibilite"]["total"]["cur"] > 0
    assert snap["acquisition"]["branche"] and snap["acquisition"]["taux_conversion"]["cur"] is not None
    al = alerts.evaluate(snap)
    assert any(a.key.startswith("visibilite_baisse:meta_ads") for a in al)  # chute Meta simulée
    first = alerts.only_new(db, al)
    assert first and alerts.only_new(db, al) == []  # pas de doublon le lendemain
    out = dashboard.build(snap, al, output=tmp_path / "index.html")
    assert "Pilotage commercial Flugia" in out.read_text()


def test_attribution_utm():
    assert kpis.family_from_utm("chatgpt.com", "referral") == "GEO"
    assert kpis.family_from_utm("google", "cpc") == "Google Ads"
    assert kpis.family_from_utm("google", "organic") == "SEO"
    assert kpis.family_from_utm("partenaire-ucm", "referral") == "Referral"
    assert kpis.family_from_utm(None, None) == "Direct / autre"
