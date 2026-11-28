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


def test_crunchbase_watch_and_alerts(tmp_path):
    from datetime import date as d_, datetime as dt, timezone as tz

    from flugia_tracking import crunchbase

    home = ('<title>AI co-workers | FLUGIA</title><meta name="description" content="Agents IA pour PME">'
            '<h1>Put AI to work</h1><a href="/ai-sales-manager/">x</a><a href="https://www.linkedin.com/company/flugia_ai">in</a>')
    pages = {"/": home, "/pricing-flugia/": "<p>From 59 € / month</p><p>999 €</p>"}
    fetch = lambda url: pages[url.replace(crunchbase.SITE, "")]  # noqa: E731
    db = DB(f"sqlite:///{tmp_path / 'c.db'}")
    as_of = seed(db)
    assert crunchbase.watch_site(db, fetch) == []  # premier relevé : référence
    assert crunchbase.snapshot_site(fetch)["tarifs.prix"] == "59€, 999€"
    pages["/"] = home.replace('/ai-sales-manager/">x</a>', '/ai-sales-manager/">x</a><a href="/ai-legal-manager/">y</a>')
    changes = crunchbase.watch_site(db, fetch, now=dt.combine(as_of, dt.min.time(), tz.utc))
    assert [c["champ"] for c in changes] == ["offre.co_workers"]

    crunchbase.add_event(db, "Levée de fonds", "Seed de 1 M€")
    snap = kpis.compute(db, as_of)
    keys = [a.key for a in alerts.evaluate(snap, None, db)]
    assert any(k.startswith("crunchbase_revue:") for k in keys)  # date jamais renseignée
    assert any(k.startswith("crunchbase_site:offre.co_workers:") for k in keys)
    assert any(k.startswith("crunchbase_evenement:") for k in keys)

    crunchbase.set_param(db, "crunchbase_maj", d_.today().isoformat())
    for e in crunchbase.events(db):
        crunchbase.mark_event(db, e["id"])
    keys = [a.key for a in alerts.evaluate(snap, None, db)]
    assert not any(k.startswith(("crunchbase_revue:", "crunchbase_evenement:")) for k in keys)


def test_crunchbase_missing_profile_fields(tmp_path):
    from flugia_tracking import crunchbase

    db = DB(f"sqlite:///{tmp_path / 'f.db'}")
    as_of = seed(db)
    snap = kpis.compute(db, as_of)
    crunchbase.set_param(db, "crunchbase_url", "https://www.crunchbase.com/organization/flugia")
    keys = [a.key for a in alerts.evaluate(snap, None, db)]
    assert "crunchbase_fiche:liste" in keys  # liste jamais remplie
    crunchbase.set_param(db, "crunchbase_fiche", ",".join(k for k in crunchbase.PROFILE_FIELDS if k not in ("logo", "x")))
    keys = [a.key for a in alerts.evaluate(snap, None, db) if a.key.startswith("crunchbase_fiche:")]
    assert sorted(keys) == ["crunchbase_fiche:logo", "crunchbase_fiche:x"]
    crunchbase.mark_field(db, "logo")
    keys = [a.key for a in alerts.evaluate(snap, None, db) if a.key.startswith("crunchbase_fiche:")]
    assert keys == ["crunchbase_fiche:x"]


def test_alternativeto_alerts(tmp_path):
    from flugia_tracking import crunchbase

    db = DB(f"sqlite:///{tmp_path / 'a.db'}")
    as_of = seed(db)
    snap = kpis.compute(db, as_of)
    keys = [a.key for a in alerts.evaluate(snap, None, db) if a.key.startswith("alternativeto_")]
    assert keys == []  # fiche non déclarée : aucune alerte AlternativeTo
    crunchbase.set_param(db, "alternativeto_url", "https://alternativeto.net/software/flugia/about/")
    crunchbase.set_param(db, "alternativeto_maj", as_of.isoformat())
    crunchbase.set_param(db, "alternativeto_fiche", ",".join(k for k in crunchbase.AT_FIELDS if k not in ("captures", "alternatives")))
    crunchbase.add_event(db, "Nouvelle offre ou nouveau produit", "Co-Worker juridique")
    crunchbase.add_event(db, "Levée de fonds", "Seed")
    al = {a.key: a for a in alerts.evaluate(snap, None, db) if a.key.startswith("alternativeto_")}
    assert {k.split(":")[0] + ":" + k.split(":")[1] for k in al if "fiche" in k} == {"alternativeto_fiche:captures", "alternativeto_fiche:alternatives"}
    assert "Sintra" in al["alternativeto_fiche:alternatives"].action
    assert sum(k.startswith("alternativeto_evenement:") for k in al) == 1  # la levée de fonds ne concerne pas AlternativeTo
    assert not any(k.startswith("alternativeto_revue:") for k in al)
