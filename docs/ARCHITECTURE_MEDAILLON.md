# Architecture médaillon

## 1. Les couches

| Couche | Contenu | Écrite par |
|---|---|---|
| **Bronze** | Données brutes, versionnées, avec colonnes techniques | Imports de fichiers |
| **Silver** | Données nettoyées : dédoublonnées et contrôlées | Pipelines |
| **Gold** | Données métier : indicateurs et star schema | Pipelines |

Chaque écriture crée une nouvelle version Parquet immuable dans MinIO.

## 2. Règles appliquées par la plateforme

| Règle | Comportement |
|---|---|
| **R1. Gold passe par Silver** | Une sortie Gold ne peut pas recevoir de donnée issue de Bronze ou d'une source brute. Le nœud `date_dimension` est autorisé |
| **R2. Silver doit être nettoyé** | Tout chemin d'une donnée brute vers une sortie Silver passe par `dedupe` **et** `quality_check` |
| **R3. Pas de retour en arrière** | Une sortie Silver ne peut pas recevoir de donnée issue de Gold |

R1 à R3 sont contrôlées à la validation, à la publication et juste avant chaque exécution (`backend/app/processing/medallion.py`). Le parcours suit toutes les branches, y compris les deux ports de `branch` et les sous-pipelines. Un sous-pipeline modifié après la publication du pipeline parent est donc détecté au lancement : l'exécution échoue avec `medallion_rule_violation` et rien n'est écrit.

## 3. Modèle Bronze → Silver

`POST /api/v1/pipelines/templates/bronze-to-silver` crée un pipeline modifiable : `dataset_input` → `clean` → `dedupe` → `fill_missing` → `quality_check` → `output` Silver.

| Exigence Silver | Étape du modèle |
|---|---|
| Standardisation des formats | `clean` : espaces, valeurs factices (`-`, `n/a`…), nombres et dates dans un format unique |
| Correction des types | `clean` : une colonne texte devient nombre ou date si au moins 80 % de ses valeurs correspondent |
| Suppression des doublons | `dedupe` sur les clés choisies |
| Gestion des valeurs nulles | `fill_missing` : texte → `Unknown`, nombre → médiane |
| Validation de la qualité | `quality_check` : clés non nulles et uniques (bloquant), colonnes complétées (avertissement) |

Les clés, identifiants, codes, dates et booléens ne reçoivent jamais de valeur inventée : ils restent vides si la donnée manque.

## 4. Lignage

Chaque version Silver ou Gold enregistre les datasets et versions lus par les étapes situées en amont de sa sortie. `GET /api/v1/datasets/{id}/lineage` renvoie ses sources et les datasets produits à partir de lui.

## 5. Star schema Gold

| Élément | Fonctionnement |
|---|---|
| **Rôle** | Une sortie Gold peut être `fact`, `dimension` ou `mart`, avec sa clé primaire et ses relations (colonne → dimension) |
| **Clé de substitution** | Le nœud `surrogate_key` calcule un entier stable depuis la clé métier : la même valeur donne la même clé à chaque exécution. Une clé vide donne `-1` |
| **Membre Unknown** | Chaque dimension contient une ligne de clé `-1` |
| **Clés étrangères** | Une valeur sans correspondance prend `-1` : aucune ligne de fait n'est perdue |
| **Intégrité** | Clé primaire unique et non nulle, clés étrangères présentes dans leur dimension. Un échec bloque toutes les écritures de l'exécution |

Ordre d'écriture : dimensions, puis faits, puis les autres sorties (`backend/app/processing/star.py`).
