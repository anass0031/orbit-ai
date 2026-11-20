# Scénarios de traitement des données

Chaque scénario correspond à un test automatique du fichier `backend/tests/test_data_scenarios.py`, numéroté de la même façon.

```bash
cd backend
pytest tests/test_data_scenarios.py -q
```

**Légende :** ✅ traité automatiquement par la plateforme · 🛠️ traité avec une étape de pipeline à configurer · 🔎 détecté et signalé, la décision reste humaine.

## 1. Entrée et ingestion

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 1 | Un fichier, un jeu de données | ✅ | Import, découverte du schéma, écriture Bronze |
| 2 | Un fichier, plusieurs entités | ✅ | `POST /imports/batch` crée un dataset Bronze par feuille Excel ou par collection JSON, XML, YAML |
| 3 | Plusieurs fichiers, jeux différents | ✅ | Un dataset Bronze par fichier |
| 4 | Plusieurs fichiers du même type | ✅ | Option « Combine into one dataset » : les lignes sont ajoutées |
| 5 | Plusieurs fichiers liés | ✅ | Relations mesurées par le conseiller Gold |
| 6 | Sources indépendantes | ✅ | Modèles Gold séparés |
| 7 | Plusieurs formats dans un lot | ✅ | CSV, Excel, JSON, XML, YAML et Parquet |
| 8 | Fichier vide ou en-tête seul | ✅ | Refus avec un message clair |
| 9 | Fichier corrompu | ✅ | Import en échec, motif conservé |
| 10 | Gros fichier | ✅ | Testé sur 150 000 lignes, dans la limite `ORBIT_UPLOAD_MAX_MB` |
| 11 | Même fichier chargé deux fois | ✅ | Ignoré dans un lot grâce à l'empreinte SHA-256, avertissement sinon |
| 12 | Ingestion incrémentale | ✅ | Mode `append` et nœud `incremental_input` |

## 2. Schéma et structure

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 13 | Schémas identiques | ✅ | Ajout direct |
| 14 | Schémas compatibles | ✅ | Type élargi, par exemple entier vers décimal |
| 15 | Schémas incompatibles | ✅ | Refus `schema_incompatible`, version précédente intacte |
| 16 | Colonnes manquantes | ✅ | Valeurs vides sur les nouvelles lignes |
| 17 | Colonnes supplémentaires | ✅ | Colonne ajoutée, vide sur les anciennes lignes |
| 18 | Noms différents, même information | 🛠️ | Étape **Select / cast** pour renommer, puis **Union** |
| 19 | Types différents, même champ | 🛠️ | Étape **Select / cast** ; une valeur non convertible devient vide |
| 20 | Objets JSON imbriqués | ✅ | Colonnes aplaties `parent_enfant` |
| 21 | Tableaux JSON d'objets | ✅ | Table enfant avec la clé du parent |
| 22 | JSON mixte | ✅ | Union des champs, tableaux en table enfant |
| 23 | XML imbriqué et répété | ✅ | Tables reliées ; `DOCTYPE` et entités refusés par sécurité |
| 24 | YAML imbriqué | ✅ | Tables reliées, lecture sûre |
| 25 | Schéma modifié entre deux lots | ✅ | Changement enregistré sur la version |

## 3. Nettoyage

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 26 | Valeurs NULL | ✅ | **Fill missing values** dans le modèle Bronze → Silver |
| 27 | Chaînes vides ou espaces | ✅ | Converties en valeur vide |
| 28 | Représentations du manquant | ✅ | `-`, `n/a`, `null`, `?`, etc. |
| 29 | Doublons exacts | ✅ | **Deduplicate** |
| 30 | Doublons partiels | 🛠️ | **Deduplicate** avec une colonne d'ordre : la ligne la plus récente est gardée |
| 31 | Identifiants métier en double | ✅ | Règle `unique` bloquante, rien n'est écrit |
| 32 | Types invalides | ✅ | Conversion sûre : valeur vide au lieu d'une erreur |
| 33 | Formats de dates | ✅ | Format unique |
| 34 | Séparateurs décimaux | ✅ | `1 234,50`, `1,234.50` et `1234.50` donnent le même nombre |
| 35 | Casse incohérente | 🛠️ | Option de casse de **Clean** |
| 36 | Espaces en trop | ✅ | Supprimés |
| 37 | Valeurs mal formées | ✅ | Lignes mises en quarantaine à l'import |
| 38 | Valeurs aberrantes | 🛠️ | **Anomaly detection** (z-score ou IQR) |
| 39 | Unités et devises | 🛠️ | **Derived column** : le taux de conversion est une règle métier à fournir |
| 40 | Catégories invalides | 🛠️ | Règle `allowed_values` avec quarantaine |

## 4. Relations et intégration

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 41 | Clé primaire | ✅ | Simple ou composite, mesurée sur les données |
| 42 | Clé étrangère | ✅ | Recouvrement des valeurs et proximité des noms |
| 43 | Relation 1:1 | ✅ | Détectée |
| 44 | Relation 1:N | ✅ | Détectée |
| 45 | Relation N:M | ✅ | Table de liaison reconnue |
| 46 | Enregistrements orphelins | ✅ | Comptés, rattachés au membre `Unknown` (-1), aucune mesure perdue |
| 47 | Identifiant partagé | ✅ | Détecté entre plusieurs tables |
| 48 | Conflits entre sources | 🔎 | Signalés, sans choix automatique de la bonne valeur |
| 49 | Identifiants différents, même entité | 🔎 | Signalés, sans fusion automatique |
| 50 | Dépendances fonctionnelles | ✅ | Hiérarchies comme ville → pays |

## 5. Bronze → Silver

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 51 | Un Bronze → un Silver | ✅ | Modèle en un clic |
| 52 | Un Bronze → plusieurs Silver | 🛠️ | Plusieurs branches, chacune nettoyée |
| 53 | Plusieurs Bronze → un Silver | 🛠️ | **Union** ou **Join** |
| 54 | Plusieurs Bronze → plusieurs Silver | 🛠️ | Branches indépendantes |
| 55 | Données imbriquées → tables relationnelles | ✅ | Entités parent et enfant |
| 56 | Aplatissement avec relations | ✅ | La clé du parent est conservée |
| 57 | Jeux sans relation | ✅ | Restent séparés |
| 58 | Lignes partiellement valides | ✅ | Les lignes valides continuent |
| 59 | Quarantaine | ✅ | Fichier dédié, chemin dans les journaux |
| 60 | Métadonnées et lignage | ✅ | `_source_file`, `_import_id`, API de lignage |

## 6. Silver → Gold

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 61 | Un Silver → un Gold | ✅ | Pipeline simple |
| 62 | Plusieurs Silver → un Gold | 🛠️ | **Join** ou **SQL transform** |
| 63 | Plusieurs Silver → plusieurs Gold | ✅ | Écran **Gold Models** |
| 64 | Identification des faits | ✅ | Mesures, dates, clés |
| 65 | Identification des dimensions | ✅ | Tables référencées et colonnes descriptives |
| 66 | Schéma en étoile | ✅ | Hiérarchies repliées dans la dimension |
| 67 | Schéma en flocon | ✅ | Dimensions parentes reliées |
| 68 | Dimensions partagées | ✅ | Dimensions conformes entre faits |
| 69 | Grain du fait | ✅ | Affiché et vérifié avant écriture |
| 70 | Agrégations et KPI | ✅ | Tables `mart_*_kpis` |
| 71 | Pas de modélisation nécessaire | ✅ | Table listée avec le motif |

## 7. Traitement et fiabilité

| N° | Scénario | État | Comportement |
|---|---|---|---|
| 72 | Évolution du schéma | ✅ | Propagée et tracée |
| 73 | Mises à jour incrémentales | ✅ | Mode `upsert` |
| 74 | Nouvelles lignes et lignes modifiées | ✅ | Mode `upsert` sur la clé |
| 75 | Traitement idempotent | ✅ | Même résultat à chaque relance |
| 76 | Échec et reprise | ✅ | La dernière version valide reste en place |
| 77 | Échec partiel d'un lot | ✅ | Les autres fichiers sont chargés |
| 78 | Suivi des rejets | ✅ | Nombre, motif et emplacement conservés |
| 79 | Qualité avant et après | ✅ | Résultat de chaque contrôle sur l'exécution |
| 80 | Lignage et historique | ✅ | Versions, exécutions et tables sources |
