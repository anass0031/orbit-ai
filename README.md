# ORBIT AI — Semaine 4

Plateforme ORBIT AI à la fin de la **semaine 4** du cahier des charges : tout le socle de la semaine 3, plus la transformation des
données (pipelines ETL visuels, moteur DuckDB, couches Silver / Gold), la qualité des données et le suivi des exécutions.

**Livrable de la semaine (cahier des charges)** : *démo intermédiaire — pipeline exécuté de bout en bout avec contrôles qualité et
suivi des exécutions* → voir le scénario de démonstration (section 4).

| Tâche semaine 4 | Réalisation |
|---|---|
| Développer l'éditeur visuel de pipelines | Éditeur glisser-déposer (React Flow) : 34 types de nœuds, validation en direct, aperçu des données à chaque étape, brouillon sauvegardé automatiquement, versions publiées immuables |
| Intégrer le moteur d'exécution DuckDB | Chaque nœud est une table DuckDB ; les entrées sont lues depuis les fichiers Parquet de MinIO ; le SQL utilisateur est isolé (pas d'accès fichiers / réseau) |
| Produire les couches Silver et Gold | Les nœuds « Dataset output » écrivent une nouvelle version Parquet de `silver.*` ou `gold.*` à chaque exécution |
| Définir les règles de qualité | 8 types : non nul, unique, plage, motif (regex), valeurs autorisées, fraîcheur, nombre de lignes, SQL personnalisé ; sévérité ; action en cas d'échec (avertir / mettre en quarantaine / bloquer) |
| Calculer les scores et suivre les anomalies | Score par règle, par dataset, par dimension (complétude, validité, unicité, cohérence, fraîcheur) et global ; anomalies ouvertes / prises en charge / résolues (fermeture automatique quand la règle repasse) |
| Suivre les exécutions | Exécution en arrière-plan (Redis + worker Celery), statut de chaque étape, journaux en direct, relance, annulation, historique filtrable |
| Construire le tableau de bord | Indicateurs : couches Bronze / Silver / Gold, exécutions par jour, taux de succès, durées, score qualité, anomalies, points d'attention |

**Repris de la semaine 3** (toujours présent) : Docker Compose, FastAPI + Next.js, PostgreSQL + migrations Alembic, authentification JWT,
3 rôles, import de fichiers CSV / Excel / JSON / Parquet, découverte automatique du schéma, couche Bronze dans MinIO.



## Nouveautés de cette version

> **Base créée avant cette version :** la chaîne de migrations a changé. Recréez la base avec `docker compose down -v`
> (**cela efface les données de cette semaine**), puis `docker compose up -d --build`.

- **Parquet :** la page *File Import* accepte `.parquet` et conserve les types natifs (entier, décimal, date, timestamp, booléen). Exemple : `samples/ventes.parquet`.
- **Règles médaillon :** Gold passe par Silver (R1), Silver est dédoublonné et contrôlé (R2), pas de retour de Gold vers Silver (R3). Elles sont vérifiées à la validation, à la publication et juste avant chaque exécution.
- **Modèle Bronze → Silver en un clic :** bouton *Clean to Silver* sur un dataset Bronze, ou *New pipeline* à partir d'un dataset Bronze.
- **Lignage :** onglet *Lineage* de la page d'un dataset.
- **Star schema :** rôle `fact` / `dimension` / `mart` sur les sorties Gold, nœud *Surrogate key*, membre *Unknown* (`-1`), contrôle d'intégrité, onglet *Data model*.
- **Outils de pipeline :** 34 types de nœuds, paramètres `{{…}}`, planification, déclencheurs, relances, notifications, SLA, backfill, *Run Calendar* (voir `docs/NOUVEAUX_OUTILS.md`).
- **Catalogue :** profil des colonnes, comparaison de versions, documentation, tags, glossaire métier (voir `docs/ESPACE_ANALYSTE.md`).
- **E-mails de démonstration :** Mailpit, boîte de réception sur http://localhost:8025.

### Démonstration du star schema

La pile étant démarrée, depuis le dossier de cette semaine (avec `httpx` installé, par exemple dans le venv du backend) :

```powershell
python samples/demo_star_schema.py --url http://localhost:8000 --password <ORBIT_DEMO_PASSWORD>
```

Le script charge `clients.json` et `commandes.csv` en Bronze, les nettoie en Silver, puis construit `gold.dim_client`, `gold.dim_date` et `gold.fact_commandes`.
Les commandes dont le client est absent de `clients.json` pointent vers le membre *Unknown* (`-1`).

L'architecture est décrite dans `docs/ARCHITECTURE_MEDAILLON.md`.

---

## 1. Prérequis

- **Docker Desktop** (démarré) — c'est tout ce qu'il faut pour la méthode recommandée.
- Pour lancer sans Docker ou exécuter les tests : **Python 3.11** et **Node.js 20+** (testé avec Node 22).

## 2. Lancement avec Docker (méthode recommandée)

Toutes les commandes se lancent depuis le dossier `orbit_ai_semaine4`.

**Étape 1 — le fichier `.env`**

Un `.env` déjà rempli est fourni (copié de la semaine 3). Pour repartir de zéro :

```powershell
copy .env.example .env
```

puis remplacer **chaque** `CHANGE_ME` :

| Variable | Valeur à mettre |
|---|---|
| `ORBIT_JWT_SECRET` | une longue chaîne aléatoire : `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `ORBIT_DEMO_PASSWORD` | le mot de passe des 3 comptes de démo (ex. `orbit-demo-2026`) |
| `POSTGRES_PASSWORD` | un mot de passe pour PostgreSQL |
| `ORBIT_DATABASE_URL` | remplacer `CHANGE_ME` par le **même** mot de passe PostgreSQL (sert seulement hors Docker) |
| `MINIO_ROOT_PASSWORD` | un mot de passe d'au moins 8 caractères pour MinIO |
| `ORBIT_S3_SECRET_KEY` | le **même** mot de passe que `MINIO_ROOT_PASSWORD` (sert seulement hors Docker) |

L'API refuse de démarrer si une valeur `CHANGE_ME` est restée.


**Étape 2 — construire et démarrer**

```powershell
docker compose up -d --build
```

Le premier lancement prend quelques minutes. Au démarrage, l'API applique les migrations Alembic (`0001` puis `0002`) et crée les
comptes de démo ; le worker démarre ensuite.

**Étape 3 — vérifier**

```powershell
docker compose ps
```

Les 6 services (`postgres`, `minio`, `redis`, `api`, `worker`, `frontend`) doivent être `healthy`.

**Étape 5 — ouvrir l'application**

| Service | Adresse |
|---|---|
| Interface web | http://localhost:3000 |
| Documentation de l'API (Swagger) | http://localhost:8000/docs |
| Santé (base, stockage, file de tâches) | http://localhost:8000/api/v1/health |
| Console MinIO (fichiers du lakehouse) | http://localhost:9001 — identifiant `orbitminio`, mot de passe = `MINIO_ROOT_PASSWORD` |

**Arrêter**

```powershell
docker compose down        # arrête, en GARDANT les données
docker compose down -v     # arrête et SUPPRIME toutes les données (base, fichiers MinIO, file Redis)
```

> Si un port est déjà pris, changez `FRONTEND_HOST_PORT`, `API_HOST_PORT`, `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT` ou
> `MINIO_*_HOST_PORT` dans `.env`. Si vous changez le port de l'API ou de l'interface, mettez aussi à jour `NEXT_PUBLIC_API_URL`
> et `ORBIT_CORS_ORIGINS`, puis relancez avec `--build`.

**Dépannage**

| Problème | Cause et solution |
|---|---|
| Une exécution reste « queued » / message « The job queue (Redis) is unreachable » | Le worker ou Redis ne tourne pas : `docker compose ps`, puis `docker compose logs worker`. Le tableau de bord signale une exécution en attente depuis plus de 5 minutes. |
| `npm ci` échoue pendant le build du `frontend` (« package.json and package-lock.json … are in sync », souvent avec `picomatch`) | `frontend/package-lock.json` a été régénéré avec une autre version de npm. Le régénérer avec l'image Docker : `docker run --rm -v "${PWD}\frontend:/app" -w /app node:22-alpine npm install --package-lock-only`, puis relancer `docker compose up -d --build`. |
| L'API ne démarre pas : erreur d'authentification PostgreSQL | PostgreSQL ne fixe son mot de passe qu'au **premier** démarrage. Si `POSTGRES_PASSWORD` a changé ensuite dans `.env`, faire `docker compose down -v` (supprime les données) puis `docker compose up -d`. |
| L'API refuse de démarrer (« Unsafe configuration ») | Une valeur `CHANGE_ME` est restée dans `.env`. |
| « Cannot reach the ORBIT API » dans le navigateur | Ouvrir l'application sur `http://localhost:3000` ou `http://127.0.0.1:3000` (adresses autorisées par `ORBIT_CORS_ORIGINS`), sans transfert de port VS Code. |

## 3. Comptes de démonstration et rôles

Mot de passe = valeur de `ORBIT_DEMO_PASSWORD` dans `.env`.

| Email | Rôle | Ce qu'il peut faire |
|---|---|---|
| `admin@orbit.local` | Administrateur | tout, plus gestion des utilisateurs / rôles et journal d'audit |
| `engineer@orbit.local` | Data Engineer | importer des fichiers, créer / publier / exécuter des pipelines, relancer / annuler des exécutions, définir et lancer les règles de qualité, traiter les anomalies |
| `analyst@orbit.local` | Analyste | consulter le catalogue, les pipelines, les exécutions et la qualité ; **traiter les anomalies** (prendre en charge, résoudre) |

Les droits sont vérifiés par l'API à chaque requête (le menu ne fait que masquer ce qui n'est pas autorisé).

## 4. Scénario de démonstration 

Connecté avec `engineer@orbit.local` :

**A. Bronze**
1. **File Import** → déposer `samples/commandes.csv` → **Upload & discover schema** → nom `commandes` → **Load into Bronze**.

**B. Pipeline Bronze → Silver → Gold**
2. **ETL Pipelines** → **New pipeline** → nom « Commandes medallion », *Start from a dataset* = `bronze.commandes` → **Create & open editor**.
   L'éditeur s'ouvre avec un nœud d'entrée relié à une sortie `silver.commandes_clean`.
3. Cliquer le nœud d'entrée, puis ajouter dans l'ordre (chaque nouveau nœud est relié au nœud sélectionné) :
   - **Filter** — condition `montant > 0` ;
   - **Deduplicate** — clé `order_id` ;
   - **Data quality** — assertion `not_null` sur `code_postal`, action « quarantine rows ».
   Relier ce dernier nœud à la sortie Silver (glisser du port droit vers le port gauche ; supprimer l'ancien lien avec la touche Suppr).
4. Depuis le nœud *Data quality*, ajouter **Aggregate** (grouper par `client`, `sum(montant)` alias `chiffre_affaires`) puis
   **Dataset output** (couche Gold, nom `ca_par_client`).
5. Le bandeau indique **✓ valid** (la validation s'exécute en direct dans DuckDB) ; chaque nœud affiche son schéma de sortie.
   Sélectionner un nœud → **Preview data at this node** pour voir les lignes à cette étape.
6. **Run pipeline** : les nœuds passent en *running* puis *succeeded* avec leur nombre de lignes ; le panneau de journaux s'ouvre.
   La ligne sans code postal est mise en quarantaine (fichier JSON Lines dans MinIO, `quarantine/…`).

**C. Contrôles qualité**
7. **Lakehouse Catalog** → `gold.ca_par_client` → **Quality rules** → **New rule** : *Range* sur `chiffre_affaires`, min `10`,
   sévérité *High*, action **Block**.
8. Relancer le pipeline : l'exécution **échoue** avec `quality_gate_failed` — rien n'est écrit, `gold.ca_par_client` reste en v1.
   Une anomalie *high* apparaît dans **Data Quality → Anomalies** avec un échantillon des lignes fautives.
9. **Data Quality → Rules** : décocher **enabled** sur la règle (l'anomalie est fermée avec la note « rule disabled »), puis
   **Run Monitoring** → ouvrir l'exécution échouée → **Retry (same version)** : succès, Gold passe en v2.
   (Une anomalie se ferme aussi **automatiquement** quand sa règle repasse lors d'un contrôle ou d'une exécution suivante.)
10. **Data Quality → Scores** : score global, score par dimension, score par dataset ; **Run checks** relance les règles d'un dataset.

**D. Suivi et tableau de bord**
11. **Run Monitoring** : indicateurs (taux de succès, durées, lignes écrites), graphique par jour, historique filtrable ; le détail
    d'une exécution montre chaque étape, les datasets lus / écrits, les journaux, et permet relance / annulation.
12. **Dashboard** : couches Bronze / Silver / Gold, exécutions par jour, qualité, anomalies récentes, points d'attention
    (pipeline en échec répété, exécution bloquée, dataset sous 80 % de qualité), état de PostgreSQL / MinIO / Redis.
13. Se connecter avec `analyst@orbit.local` : tout est consultable, l'analyste peut traiter une anomalie mais ne peut ni modifier
    ni exécuter un pipeline.

Fichiers visibles dans la console MinIO (bucket `orbit-lakehouse`) : `landing/` (fichiers d'origine), `bronze/`, `silver/`, `gold/`
(une version Parquet par écriture) et `quarantine/` (lignes mises de côté).

## 5. Lancement sans Docker pour l'application

Utile pour développer avec rechargement automatique. PostgreSQL, MinIO et Redis tournent quand même dans Docker.

```powershell
# 1. Infrastructure seulement
docker compose up -d postgres minio redis

# 2. Backend — API (terminal 1)
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload --port 8000

# 3. Backend — worker (terminal 2, même venv). --pool=solo est nécessaire sous Windows.
cd backend
.venv\Scripts\activate
celery -A app.worker.celery_app worker -l info --pool=solo

# 4. Frontend (terminal 3)
cd frontend
npm ci        # installe exactement les versions de package-lock.json (ne le modifie pas)
npm run dev
```

Sans worker, mettre `ORBIT_TASKS_EAGER=true` dans `.env` : les pipelines s'exécutent alors dans le processus de l'API
(la requête « Run » attend la fin de l'exécution).

## 6. Tests et qualité du code

Les tests n'ont besoin ni de Docker, ni de PostgreSQL / MinIO / Redis : SQLite, dossier temporaire et exécution dans le processus.

```powershell
cd backend
.venv\Scripts\activate
pytest            # 187 tests
ruff check .      # analyse statique Python

cd ..\frontend
npm run typecheck # TypeScript
npm run lint      # ESLint
npm run build     # build de production
```

| Fichier de tests | Ce qui est vérifié |
|---|---|
| `test_auth_roles.py` | JWT, blocage après échecs, 3 rôles, gestion des utilisateurs, santé, CORS (toutes les méthodes utilisées par l'interface) |
| `test_imports.py`, `test_schema_discovery.py` | import CSV / Excel / JSON / Parquet, inférence des types, Bronze versionné |
| `test_pipelines.py` | validation, aperçu, exécution Bronze → Silver → Gold, quarantaine, blocage qualité + relance, versions publiées, SQL isolé, étape en échec, rôles, tableau de bord |
| `test_quality.py` | 7 types de règles, scores (règle / dataset / dimension), anomalies (ouverture, mise à jour, résolution), validation des règles, protection des imports Bronze |

## 7. API (résumé)

Documentation complète et interactive : http://localhost:8000/docs

| Méthode et route | Rôle requis | Description |
|---|---|---|
| `GET /api/v1/health` | public | état de PostgreSQL, MinIO et de la file Redis |
| `POST /api/v1/auth/login`, `GET /api/v1/auth/me` | public / connecté | connexion JWT, utilisateur et permissions |
| `GET /api/v1/dashboard` | connecté | indicateurs du tableau de bord |
| `POST /api/v1/imports`, `…/{id}/discover`, `…/{id}/ingest` | admin, data engineer | import, découverte du schéma, chargement Bronze |
| `GET /api/v1/datasets`, `…/{id}`, `…/{id}/preview` | tous | catalogue Bronze / Silver / Gold, versions, aperçu |
| `GET /api/v1/pipelines`, `GET …/{id}`, `GET …/{id}/versions`, `GET …/node-types` | tous | pipelines, graphe, versions publiées |
| `POST /api/v1/pipelines`, `PUT …/{id}`, `DELETE …/{id}`, `POST …/{id}/publish` | admin, data engineer | créer, enregistrer le brouillon, supprimer, publier |
| `POST /api/v1/pipelines/validate`, `POST /api/v1/pipelines/preview` | tous | validation (exécution à blanc DuckDB), aperçu d'un nœud |
| `POST /api/v1/pipelines/{id}/run` | admin, data engineer | lancer une exécution (HTTP 202) |
| `GET /api/v1/runs`, `GET /api/v1/runs/stats`, `GET /api/v1/runs/{id}` | tous | historique, indicateurs, détail (étapes, journaux) |
| `POST /api/v1/runs/{id}/retry`, `POST /api/v1/runs/{id}/cancel` | admin, data engineer | relancer (même version), annuler |
| `GET /api/v1/quality/overview`, `…/rules`, `…/results`, `…/issues` | tous | scores, règles, résultats, anomalies |
| `POST/PATCH/DELETE /api/v1/quality/rules…`, `POST /api/v1/quality/datasets/{id}/run` | admin, data engineer | gérer les règles, lancer les contrôles |
| `PATCH /api/v1/quality/issues/{id}` | admin, data engineer, analyste | prendre en charge / résoudre / rouvrir une anomalie |
| `GET/POST /api/v1/users`, `PATCH /api/v1/users/{id}`, `GET /api/v1/audit` | admin | utilisateurs, rôles, journal d'audit |

Les erreurs ont toujours la même forme : `{"error": {"code", "message", "details", "correlation_id"}}`.

## 8. Fonctionnement

### Pipelines et DuckDB

- Le graphe dessiné dans l'éditeur est enregistré en JSON (nœuds + liens). Le **brouillon** est sauvegardé automatiquement ;
  **Publier** le valide et le fige en **version N** (une modification de position seule ne crée pas de nouvelle version).
- Une exécution prend un **instantané** de la version publiée : une relance rejoue exactement la même version.
- Exécution : l'API crée l'exécution (statut `queued`) et envoie son identifiant dans Redis ; le **worker Celery** l'exécute.
  Les nœuds sont matérialisés en tables DuckDB dans l'ordre des dépendances. Les entrées sont lues depuis les fichiers Parquet
  de MinIO, puis l'accès aux fichiers / au réseau et la configuration de DuckDB sont verrouillés : le SQL saisi dans un nœud ne
  voit que les tables du pipeline. Les nœuds SQL n'acceptent qu'un seul `SELECT` (vérifié avec sqlglot).
- Types de nœuds : entrée dataset (version fixe ou dernière), filtre, sélection / renommage / conversion, SQL, jointure, union,
  agrégation, déduplication, contrôle qualité intégré, sortie Silver / Gold.
- Statut, nombre de lignes et message de chaque étape sont enregistrés pendant l'exécution ; les journaux sont visibles en direct.
  L'annulation est vérifiée avant chaque étape. Le nombre de tentatives est limité (`ORBIT_MAX_RUN_ATTEMPTS`, 3 par défaut).

### Qualité

- Les règles d'un dataset sont évaluées **avant** toute écriture (pipeline ou import Bronze) et à la demande :
  - **warn** : l'anomalie est enregistrée, les données sont écrites ;
  - **quarantine** : les lignes fautives sont écartées et stockées dans `quarantine/`, le reste est écrit ;
  - **fail** : **rien n'est écrit**, le dataset garde sa version précédente, l'exécution échoue (`quality_gate_failed`).
- Score d'une règle = 100 × lignes valides / lignes évaluées (100 ou 0 pour les contrôles globaux : fraîcheur, nombre de lignes).
  Score d'un dataset = moyenne du dernier score de ses règles actives. Les scores ne sont jamais estimés : « — » tant qu'aucune
  règle n'a tourné.
- Une **anomalie** est ouverte au premier échec d'une règle, mise à jour (compteur d'occurrences, échantillon) tant qu'elle échoue,
  et résolue automatiquement quand la règle repasse. Elle peut être prise en charge ou résolue manuellement avec une note.

## 9. Structure du projet

```
orbit_ai_semaine4/
├── docker-compose.yml          PostgreSQL, MinIO, Redis, API, worker, interface
├── .env.example                modèle de configuration (copier en .env)
├── samples/                    fichiers de démonstration
├── backend/
│   ├── alembic/versions/       0001 (semaine 3) + 0002 (pipelines, exécutions, qualité)
│   ├── app/
│   │   ├── api/v1/             routes : auth, users, imports, datasets, pipelines, runs (+ dashboard), quality, health
│   │   ├── core/               configuration, JWT, permissions des 3 rôles, erreurs, logs
│   │   ├── ingestion/          lecture CSV / Excel / JSON / Parquet et découverte du schéma
│   │   ├── lakehouse/          stockage MinIO et catalogue versionné
│   │   ├── processing/         spécification du graphe et moteur DuckDB
│   │   ├── services/           imports, pipelines, exécutions, qualité, suivi / tableau de bord, audit
│   │   ├── worker/             application Celery et envoi des tâches
│   │   └── models/             tables SQLAlchemy
│   └── tests/                  187 tests automatisés (pytest)
└── frontend/
    ├── app/(app)/              pages : dashboard, imports, datasets, pipelines (+ éditeur), runs, quality, admin
    ├── components/pipeline/    nœuds et panneaux de configuration de l'éditeur
    ├── components/data/        tableaux de schéma, journaux, scores, graphique
    └── lib/                    client API, authentification, types
```

## Modèles Gold et scénarios de données

- L'écran **Gold Models** analyse Silver et génère des schémas en étoile ou en flocon : voir [docs/MODELES_GOLD.md](docs/MODELES_GOLD.md).
- Les aperçus, profils et graphiques Gold affichent les noms métier au lieu des clés techniques.
- Les 80 scénarios de traitement sont décrits dans [docs/SCENARIOS_DONNEES.md](docs/SCENARIOS_DONNEES.md) et vérifiés par `backend/tests/test_data_scenarios.py`.
