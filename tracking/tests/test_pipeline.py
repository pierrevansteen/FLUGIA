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
    beh = snap["comportement"]
    assert beh["branche"] and beh["jours_mesures"] == 7
    rows = {r["indicateur"]: r for r in beh["lignes"]}
    assert rows["Sessions"]["cur"] > 1000 and not rows["Sessions avec rage clicks"]["hausse_bien"]
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


def test_read_only_db_does_not_create_tables(tmp_path):
    path = tmp_path / "ro.db"
    DB(f"sqlite:///{path}").close()  # crée le schéma
    ro = DB(f"sqlite:///{path}", read_only=True)
    assert ro.query("SELECT COUNT(*) AS n FROM metrics_daily")[0]["n"] == 0
    empty = DB(f"sqlite:///{tmp_path / 'vide.db'}", read_only=True)
    assert empty.query("SELECT name FROM sqlite_master WHERE type='table'") == []


def test_objectives_saved_and_used_by_alerts(tmp_path):
    from flugia_tracking import objectives

    db = DB(f"sqlite:///{tmp_path / 'o.db'}")
    as_of = seed(db)
    assert objectives.load(db) == objectives.defaults()  # rien d'enregistré : valeurs de config/alerts.yaml
    objectives.save(db, {"sessions": 1_000_000, "signups": None, "budget_pub": 1})
    saved = objectives.load(db)
    assert saved == {"sessions": 1_000_000, "budget_pub": 1}
    al = alerts.evaluate(kpis.compute(db, as_of), saved)
    keys = [a.key for a in al]
    assert any(k.startswith("objectif:sessions:") for k in keys)
    assert any(k.startswith("budget:") for k in keys)
    assert not any(k.startswith("objectif:signups:") for k in keys)
    stages = {objectives.stage_of(a.__dict__) for a in al}
    assert stages <= set(objectives.STAGES) | {"Système"}
