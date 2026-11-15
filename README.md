# ORBIT AI — Semaine 3

Socle de la plateforme ORBIT AI, limité aux tâches prévues pour la **semaine 3** du cahier des charges :

| Tâche (cahier des charges) | Réalisation |
|---|---|
| Initialiser le dépôt Git | Projet prêt à versionner (`.gitignore` fourni). Le dépôt est à créer par vous (voir la fin du document). |
| Configurer Docker Compose | `docker-compose.yml` : PostgreSQL 16, MinIO, API FastAPI, interface Next.js |
| Créer le squelette FastAPI et Next.js | `backend/` (FastAPI, API versionnée `/api/v1`, doc OpenAPI) et `frontend/` (Next.js 15, React 19, TypeScript, Tailwind) |
| Mettre en place PostgreSQL et les migrations | SQLAlchemy 2 + Alembic (`backend/alembic/versions/0001_initial_schema.py`), appliquées au démarrage |
| Implémenter l'authentification JWT | `POST /api/v1/auth/login` → jeton JWT (HS256), mots de passe bcrypt, blocage après 5 échecs |
| Gérer les 3 rôles | Administrateur, Data Engineer, Analyste (`backend/app/core/permissions.py`) |
| Importer des fichiers (CSV, Excel, JSON) | CSV / TSV, Excel `.xlsx`, JSON et JSON Lines, jusqu'à 100 Mo |
| Découvrir le schéma automatiquement | Types, valeurs vides, valeurs distinctes, min / max, clé probable, avertissements, aperçu |
| Stocker la couche Bronze dans MinIO | Fichier d'origine conservé + version Parquet immuable par chargement, catalogue dans PostgreSQL |

Les pipelines ETL, Silver / Gold, la qualité, les connecteurs base de données / API / Kafka, SQL Studio, le ML et l'analyste IA
sont prévus pour les semaines 4 à 6 et ne font **pas** partie de ce dossier.

---

## 1. Prérequis

- **Docker Desktop** (démarré) — c'est tout ce qu'il faut pour la méthode recommandée.
- Pour lancer sans Docker ou exécuter les tests : **Python 3.11** et **Node.js 20+** (testé avec Node 22).

## 2. Lancement avec Docker (méthode recommandée)

Toutes les commandes se lancent depuis le dossier `orbit_ai_semaine3`.

**Étape 1 — créer le fichier `.env`**

```powershell
copy .env.example .env
```

Ouvrir `.env` et remplacer **chaque** `CHANGE_ME` :

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

Le premier lancement prend quelques minutes (téléchargement des images, installation des dépendances).
Au démarrage, l'API applique les migrations Alembic puis crée les comptes de démo (les deux opérations sont sans effet si déjà faites).

**Étape 3 — vérifier**

```powershell
docker compose ps
```

Les 4 services (`postgres`, `minio`, `api`, `frontend`) doivent être `healthy`.

**Étape 4 — ouvrir l'application**

| Service | Adresse |
|---|---|
| Interface web | http://localhost:3000 |
| Documentation de l'API (Swagger) | http://localhost:8000/docs |
| Santé de l'API | http://localhost:8000/api/v1/health |
| Console MinIO (fichiers du lakehouse) | http://localhost:9001 — identifiant `orbitminio`, mot de passe = `MINIO_ROOT_PASSWORD` |

**Arrêter**

```powershell
docker compose down        # arrête, en GARDANT les données
docker compose down -v     # arrête et SUPPRIME toutes les données (base + fichiers MinIO)
```

> Si un port est déjà pris (par exemple par le projet ORBIT AI complet), changez `FRONTEND_HOST_PORT`, `API_HOST_PORT`,
> `POSTGRES_HOST_PORT` ou `MINIO_*_HOST_PORT` dans `.env`. Si vous changez le port de l'API ou de l'interface,
> mettez aussi à jour `NEXT_PUBLIC_API_URL` et `ORBIT_CORS_ORIGINS`, puis relancez avec `--build`.

**Dépannage**

| Problème | Cause et solution |
|---|---|
| `npm ci` échoue pendant le build du `frontend` (« package.json and package-lock.json … are in sync », souvent avec `picomatch`) | `frontend/package-lock.json` a été régénéré avec une autre version de npm. Restaurer la version du dépôt, ou le régénérer avec l'image Docker : `docker run --rm -v "${PWD}rontend:/app" -w /app node:22-alpine npm install --package-lock-only`, puis relancer `docker compose up -d --build`. |
| L'API ne démarre pas : erreur d'authentification PostgreSQL | PostgreSQL ne fixe son mot de passe qu'au **premier** démarrage. Si `POSTGRES_PASSWORD` a changé ensuite dans `.env`, faire `docker compose down -v` (supprime les données) puis `docker compose up -d`. |
| L'API refuse de démarrer (« Unsafe configuration ») | Une valeur `CHANGE_ME` est restée dans `.env`. |

## 3. Comptes de démonstration

Mot de passe = valeur de `ORBIT_DEMO_PASSWORD` dans `.env`.

| Email | Rôle | Ce qu'il peut faire |
|---|---|---|
| `admin@orbit.local` | Administrateur | tout, plus gestion des utilisateurs / rôles et journal d'audit |
| `engineer@orbit.local` | Data Engineer | importer des fichiers, découvrir le schéma, charger en Bronze, consulter le catalogue |
| `analyst@orbit.local` | Analyste | consulter le catalogue Bronze, les schémas, les aperçus et l'historique des imports |

Les droits sont vérifiés par l'API à chaque requête (le menu de l'interface ne fait que masquer ce qui n'est pas autorisé).
Un changement de rôle s'applique immédiatement, même pour un utilisateur déjà connecté.

## 4. Scénario de démonstration

Des fichiers d'exemple sont fournis dans `samples/` :

| Fichier | Ce qu'il montre |
|---|---|
| `commandes.csv` | séparateur `;` détecté, virgule décimale, dates JJ/MM/AAAA, codes postaux à zéro initial gardés en texte, ligne mal formée mise en quarantaine |
| `produits.xlsx` | classeur à 2 feuilles : choisir la feuille « Produits » puis relancer la découverte |
| `clients.json` | JSON imbriqué aplati (`adresse_ville`, `adresse_pays`), tableau gardé en texte JSON, clé `id` détectée |
| `evenements.jsonl` | JSON Lines avec une ligne invalide mise en quarantaine, horodatages en UTC |

1. Se connecter avec `engineer@orbit.local`.
2. **File Import** → déposer `samples/commandes.csv` → **Upload & discover schema**.
3. Lire le schéma découvert (types, vides, min / max, clé probable 🔑), les avertissements et l'aperçu.
4. Laisser le nom proposé `commandes` → **Load into Bronze**.
5. **Bronze Catalog** → ouvrir `bronze.commandes` : schéma, versions, aperçu relu depuis le fichier Parquet de MinIO.
6. Réimporter un fichier dans le même dataset : une **version 2** est créée, la version 1 reste intacte et consultable.
7. Se connecter avec `analyst@orbit.local` : le catalogue est visible, l'import est refusé.
8. Se connecter avec `admin@orbit.local` → **Users & Audit** : créer un utilisateur, changer un rôle, lire le journal d'audit.
9. Dans la console MinIO (http://localhost:9001), bucket `orbit-lakehouse` :
   - `landing/<id_import>/` : fichier d'origine, inchangé (+ `quarantine.jsonl` si des lignes ont été rejetées) ;
   - `bronze/<dataset>/v<N>/<id_version>.parquet` : données typées de chaque version.

## 5. Lancement sans Docker pour l'application 

Utile pour développer avec rechargement automatique. PostgreSQL et MinIO tournent quand même dans Docker.

```powershell
# 1. Infrastructure seulement
docker compose up -d postgres minio

# 2. Backend (terminal 1)
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload --port 8000

# 3. Frontend (terminal 2)
cd frontend
npm ci        # installe exactement les versions de package-lock.json (ne le modifie pas)
npm run dev
```

Le backend lit le fichier `.env` à la racine (`ORBIT_DATABASE_URL`, `ORBIT_S3_*`… doivent contenir les vrais mots de passe).
Sans MinIO, il est possible de stocker le lakehouse dans un dossier local : `ORBIT_STORAGE_BACKEND=local`
(les fichiers vont alors dans `data/runtime/objects/`).

## 6. Tests et qualité

Les tests n'ont besoin ni de Docker ni de PostgreSQL / MinIO : ils utilisent une base SQLite et un dossier temporaire.

```powershell
cd backend
.venv\Scripts\activate
pytest            # 32 tests : authentification, 3 rôles, import CSV / Excel / JSON, découverte du schéma, Bronze
ruff check .      # analyse statique Python

cd ..\frontend
npm run typecheck # TypeScript
npm run lint      # ESLint
npm run build     # build de production
```

## 7. API (résumé)

Documentation complète et interactive : http://localhost:8000/docs

| Méthode et route | Rôle requis | Description |
|---|---|---|
| `GET /api/v1/health` | public | état de PostgreSQL et de MinIO |
| `POST /api/v1/auth/login` | public | connexion → jeton JWT |
| `GET /api/v1/auth/me` | connecté | utilisateur, rôle, permissions |
| `GET /api/v1/overview` | connecté | compteurs du tableau de bord |
| `POST /api/v1/imports` | admin, data engineer | envoi d'un fichier (multipart) + découverte du schéma |
| `GET /api/v1/imports`, `GET /api/v1/imports/{id}` | tous | historique et détail (schéma, avertissements, aperçu) |
| `POST /api/v1/imports/{id}/discover` | admin, data engineer | relancer la découverte (séparateur, feuille, encodage, en-tête) |
| `POST /api/v1/imports/{id}/ingest` | admin, data engineer | écrire une nouvelle version Parquet en Bronze |
| `GET /api/v1/datasets`, `GET /api/v1/datasets/{id}` | tous | catalogue, schéma, versions |
| `GET /api/v1/datasets/{id}/preview?version=N` | tous | premières lignes lues depuis le Parquet |
| `GET/POST /api/v1/users`, `PATCH /api/v1/users/{id}` | admin | gestion des utilisateurs et des rôles |
| `GET /api/v1/audit` | admin | journal d'audit |

Les erreurs ont toujours la même forme : `{"error": {"code", "message", "details", "correlation_id"}}`.

## 8. Comment fonctionne la découverte du schéma

1. Le fichier est lu **en texte** (rien n'est converti à l'aveugle par le lecteur CSV / Excel / JSON).
2. Les noms de colonnes sont normalisés en `snake_case` (accents retirés, doublons numérotés) ; le nom d'origine est conservé.
3. Pour chaque colonne, le type est déduit de **toutes** les valeurs, dans cet ordre : booléen → entier → décimal (point ou virgule)
   → date (`AAAA-MM-JJ` ou `JJ/MM/AAAA`) → horodatage ISO 8601 → texte. Un type n'est retenu que si **toutes** les valeurs non vides
   le respectent ; sinon la colonne reste en texte et un avertissement explique pourquoi (ex. « 90 % numérique mais contient 'N/D' »).
4. Les nombres à zéro initial (`01000`) restent en texte : ce sont des codes, pas des quantités.
5. Les lignes mal formées (trop de champs, JSON invalide) ne sont pas perdues en silence : elles sont comptées et enregistrées dans
   `landing/<id>/quarantine.jsonl`.
6. Au chargement en Bronze, trois colonnes techniques sont ajoutées : `_ingested_at`, `_source_file`, `_import_id`.
   Chaque chargement crée une **nouvelle version** ; une version existante n'est jamais modifiée.

## 9. Structure du projet

```
orbit_ai_semaine3/
├── docker-compose.yml          PostgreSQL, MinIO, API, interface
├── .env.example                modèle de configuration (copier en .env)
├── samples/                    fichiers de démonstration
├── backend/
│   ├── alembic/                migrations de la base
│   ├── app/
│   │   ├── api/                routes REST (auth, users, imports, datasets, health) + contrôle des rôles
│   │   ├── core/               configuration, JWT, permissions des 3 rôles, erreurs, logs
│   │   ├── db/                 session SQLAlchemy, comptes de démo
│   │   ├── ingestion/          lecture CSV / Excel / JSON et découverte du schéma
│   │   ├── lakehouse/          stockage MinIO et catalogue versionné (Bronze)
│   │   ├── models/             tables : users, audit_events, file_imports, datasets, dataset_versions
│   │   └── services/           import de fichiers, audit
│   └── tests/                  tests automatisés (pytest)
└── frontend/
    ├── app/                    pages : login, dashboard, imports, datasets, admin
    ├── components/             composants d'interface
    └── lib/                    client API, authentification, types
```


