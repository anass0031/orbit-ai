# Outils des pipelines ETL

Ce dossier propose 34 types de nœuds. Chaque nœud peut utiliser des paramètres `{{run_date}}`, `{{start_date}}`, `{{end_date}}` et ceux déclarés dans *Automation*.

## 1. Sources

| Nœud | Rôle |
|---|---|
| Dataset input | Lit une version d'un dataset |
| Incremental input | Lit seulement les lignes ajoutées depuis la dernière exécution réussie |
| File drop folder | Lit chaque nouveau fichier déposé (CSV, Excel, JSON, Parquet) |
| Date dimension | Génère un calendrier avec jours fériés |
| Sub-pipeline | Réutilise une étape d'un autre pipeline publié |

## 2. Transformations

Filter, Select / cast, SQL, Join, Union, Aggregate, Deduplicate, Clean, Fill missing values, Derived column, Split column, Merge columns, Lookup, Sort / Top N, Pivot, Unpivot, Window functions, History (SCD2), **Surrogate key** et Python transform.

## 3. Qualité

Data quality, Schema check, Row count comparison, Anomaly detection et Branch (ports `true` et `false`).

## 4. Destinations

| Nœud | Rôle |
|---|---|
| Dataset output | Écrit Silver ou Gold : `overwrite`, `append` ou `upsert`. En Gold : rôle `fact`, `dimension` ou `mart` |
| Export file | Fichier CSV, Excel, JSON ou Parquet avec lien de téléchargement |
| REST API output | Envoie les lignes vers une URL |
| Email report | Envoie un tableau ou un CSV par e-mail |

## 5. Automatisation

Panneau *Automation* d'un pipeline : planification (intervalle, jour, semaine, mois, cron, en UTC), nouvelles données ou fichiers déposés, après un autre pipeline, déclencheur API à jeton, conditions, relances, temps limite, notifications (application, e-mail, webhook), SLA et backfill.

Pages liées : *Run Calendar*, *Notifications*, section *Deliveries* d'une exécution.

## 6. Règles médaillon

Les pipelines respectent les règles R1 à R3 décrites dans `ARCHITECTURE_MEDAILLON.md`.
