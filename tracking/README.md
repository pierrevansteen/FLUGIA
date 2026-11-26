# Tracking marketing et commercial Flugia

Plateforme automatisée, sans abonnement à des outils tiers. Chaque nuit :

1. **Des collecteurs** (un par source) récupèrent les chiffres via les API officielles.
2. Les chiffres sont stockés dans **une base** (Postgres/Supabase gratuit, ou SQLite).
3. Les **KPIs** sont calculés par le code : Visibilité → Intérêt → Acquisition → Rétention.
4. **Le moteur d'alertes** applique les règles de `config/alerts.yaml` et envoie les nouvelles alertes sur Slack ou par e-mail.
5. **Le tableau de bord** (une page HTML) est régénéré.
6. Le lundi, **un agent IA (Claude)** rédige la synthèse de la semaine et les 3 priorités.

Les chiffres ne passent jamais par l'IA : ils sont calculés par le code, donc exacts et reproductibles. L'IA ne fait que commenter des agrégats, sans nom ni e-mail de client.

## Ce que mesure chaque étape

| Étape | Indicateurs | Sources |
|---|---|---|
| **Visibilité** | Vues par canal + total, recherches « flugia », taux de citation par les IA (GEO), trafic venant des IA, dépenses, coût pour 1000 impressions | Google Ads, Meta, LinkedIn, Search Console, GA4, agent GEO, saisie manuelle |
| **Intérêt** | Inscriptions (essais), démos réservées et tenues, visites par canal (dont Referral), taux visite → inscription | GA4 (puis base de l'app), Google Agenda |
| **Acquisition** | Conversion essai → payant (28 j), nouveaux clients, taux d'activation, inscriptions, clients et coût par client **par canal** | Base de l'app / Stripe |
| **Rétention** | Clients payants, MRR, churn 30 j, part des clients qui prolongent après 1 et 3 mois, paiements échoués | Base de l'app / Stripe |

Listes d'actions générées chaque jour : **essais chauds** à appeler (jour ≥ 10 et usage fort), **essais bloqués** (rien activé après 3 jours), **clients à risque** (usage −50 %) et **renouvellements** à venir.

## Démarrage rapide (en local)

```bash
cd tracking
pip install -r requirements.txt
python -m flugia_tracking demo        # données fictives → site/demo.html
cp .env.example .env                  # puis remplir les accès
python -m flugia_tracking status      # quelles sources sont prêtes
python -m flugia_tracking run --days 90 --no-notify   # 1re collecte avec 90 jours d'historique
```

Le tableau de bord est écrit dans `site/index.html`, avec les chiffres bruts dans `site/snapshot.json`.

## Mise en production (GitHub Actions, gratuit)

Le workflow `.github/workflows/tracking.yml` tourne chaque nuit vers 6h20 (heure de Bruxelles). On peut aussi le lancer à la main depuis l'onglet **Actions** (bouton « Run workflow », avec `days = 90` la première fois).

Dans **Settings → Secrets and variables → Actions** :
- **Secrets** : les jetons et mots de passe (`GOOGLE_SERVICE_ACCOUNT_JSON`, `META_ACCESS_TOKEN`, `LINKEDIN_ACCESS_TOKEN`, `GOOGLE_ADS_*`, `ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`, `DATABASE_URL`…).
- **Variables** : les identifiants non secrets (`GA4_PROPERTY_ID`, `SEARCH_CONSOLE_SITE`, `GOOGLE_CALENDAR_ID`, `META_AD_ACCOUNT_ID`…).

La liste complète est dans `.env.example`. **Une source sans accès est simplement ignorée** : on peut brancher les sources une par une.

**Base de données :**
- Recommandé : un projet **Supabase gratuit** (Postgres), dont l'URL va dans le secret `DATABASE_URL`.
- Sans `DATABASE_URL`, une base SQLite est conservée dans le cache GitHub. C'est suffisant pour démarrer, mais un cache inutilisé pendant 7 jours est effacé.

## Brancher chaque source

| Source | Ce qu'il faut faire | Délai |
|---|---|---|
| **GA4, Search Console, Google Agenda** | 1 compte de service Google Cloud (API « Google Analytics Data », « Search Console », « Calendar » activées). L'ajouter comme *Lecteur* dans GA4 et comme *utilisateur* dans Search Console. Partager l'agenda des démos avec son adresse (« voir tous les détails »). | 30 min |
| **Google Agenda (démos)** | Les rendez-vous sont reconnus par un mot dans le titre (`DEMO_KEYWORDS`, par défaut « démo »). Si vous utilisez les *agendas de réservation* Google, mettez « Démo » dans le nom du créneau. | — |
| **Google Ads** | Demander un *developer token* (Centre API du compte administrateur), créer un client OAuth et générer un *refresh token*. | Quelques jours (validation Google) |
| **Meta** | App Meta for Developers + *utilisateur système* dans Business Manager avec `ads_read` et `instagram_basic` / `instagram_manage_insights`. Le jeton n'expire pas. | 1 h |
| **LinkedIn** | App LinkedIn avec accès *Advertising API* et *Community Management API* : **à demander en premier**, la validation peut prendre plusieurs semaines. Le jeton expire après 60 jours ; le tableau de bord signale l'échec. | Semaines |
| **GEO** | Clé API Anthropic. Les questions testées sont dans `config/geo_prompts.yaml` (FR, EN, NL). Mesure hebdomadaire. | 5 min |
| **Referral** | Automatique via GA4 (canal « Referral »). Pour un programme de parrainage, saisir les chiffres dans `data/manual_visibility.csv` ou faire passer `utm_medium=referral` dans les liens. | — |
| **Salons, presse** | Saisie manuelle dans `data/manual_visibility.csv`. | — |
| **Base de l'app** *(plus tard)* | Créer un utilisateur **en lecture seule**, adapter la requête de `config/app_queries.yaml`, renseigner `APP_DATABASE_URL`. | 1 h |
| **Facturation** *(plus tard)* | Stripe : clé restreinte en lecture seule. Pour un autre outil, ajouter un collecteur sur le même modèle. | 30 min |

### Indispensable pour l'attribution : enregistrer la source à l'inscription

Pour savoir quel canal amène des **clients payants** (et pas seulement des visites), l'app doit enregistrer au moment de l'inscription sur `app.flugia.com/register` :
- `utm_source`, `utm_medium`, `utm_campaign`, et le *referrer* si pas d'UTM ;
- le tout dans des colonnes du compte, et dans les *metadata* du client Stripe.

Il faut aussi :
- **mettre des UTM sur tous les liens** (annonces, posts, signatures, QR codes de salon) ;
- **déclencher l'événement GA4 `sign_up`** à la fin de l'inscription. En attendant le branchement de la base de l'app, c'est lui qui alimente les inscriptions par canal.

## Régler les alertes

Tout se règle dans `config/alerts.yaml` :
- les seuils de variation ;
- les objectifs hebdomadaires (démos, inscriptions, conversions) ;
- la définition d'un essai chaud ou d'un client à risque.

Une même alerte n'est renvoyée qu'au bout de 7 jours.

## Ajouter une source

Créer `flugia_tracking/collectors/ma_source.py` avec une classe qui hérite de `Collector`, puis l'ajouter à la liste de `collectors/__init__.py`. Elle renvoie des lignes `(jour, canal, métrique, valeur)`.

## Tests

```bash
python -m pytest -q tests
```

Les tests simulent les réponses de chaque API : ils tournent sans aucun accès.
