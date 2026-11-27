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
    refs = {r["site"]: r for r in snap["interet"]["sites_referents"]}
    assert refs["lecho.be"]["nouveau"] and not refs["ucm.be"]["nouveau"]
    assert any(a.key == "nouveau_referent:lecho.be" for a in al)  # article de presse simulé
    # les visites par site ne doivent pas gonfler le total des visites
    assert snap["interet"]["visites"]["cur"] == sum(c["cur"] for c in snap["interet"]["visites_par_canal"])
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


def test_replace_metrics_drops_renamed_channels(tmp_path):
    db = DB(f"sqlite:///{tmp_path / 'r.db'}")
    db.upsert_metrics([("2026-11-01", "ga4", "site:ai_assistant", "sessions", 3, "t")])
    db.replace_metrics("ga4", [("2026-11-01", "ga4", "site:geo_ia", "sessions", 3, "t")])
    db.commit()
    rows = db.query("SELECT channel FROM metrics_daily")
    assert [r["channel"] for r in rows] == ["site:geo_ia"]


def test_only_major_changes_are_urgent():
    from flugia_tracking.alerts import Alert, _severity, urgent

    rule = {"variation": -30, "severite": "moyenne"}
    ncfg = {"escalade_variation": 2, "volume_minimum_urgent": 100}
    assert _severity(rule, -35, 500, ncfg) == "moyenne"     # baisse notable : rapport du lundi
    assert _severity(rule, -65, 500, ncfg) == "haute"       # chute majeure : alerte immédiate
    assert _severity(rule, -90, 40, ncfg) == "moyenne"      # petit volume : jamais urgent
    assert _severity({"variation": -30, "severite": "haute"}, -40, 40, ncfg) == "moyenne"
    al = [Alert("a", "Intérêt", "haute", "t", "d", "x"), Alert("b", "Intérêt", "moyenne", "t", "d", "x")]
    assert [a.key for a in urgent(al)] == ["a"]
