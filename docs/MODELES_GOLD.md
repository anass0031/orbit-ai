# Modèles Gold

L'écran **Gold Models** analyse toutes les tables Silver et propose les modèles dimensionnels que les données permettent. Aucun nom de table n'est prévu à l'avance.

## Pourquoi les graphiques affichaient des identifiants

Une table de faits contient des clés de substitution, par exemple `client_key = 545187411974233900`. L'aperçu et le profil lisaient cette table seule : ils montraient donc ces nombres, avec une moyenne et un histogramme sans signification.

Désormais, l'aperçu, le profil et l'onglet **Charts** suivent les relations du modèle et affichent la colonne descriptive de chaque dimension. Le bouton **Technical keys** de l'aperçu montre toujours les clés d'origine.

## Utilisation

1. Ouvrir **Gold Models**.
2. Choisir une proposition en étoile ou en flocon.
3. Garder les faits, les dimensions, les mesures et les attributs utiles.
4. Vérifier les jointures et choisir le nom affiché dans les graphiques.
5. Cliquer sur **Preview tables** : rien n'est écrit.
6. Cliquer sur **Generate Gold model** : un pipeline modifiable est créé puis exécuté.

## Règles d'analyse

| Élément | Règle |
|---|---|
| Clé primaire | Colonne unique et non vide, sinon combinaison de 2 ou 3 colonnes |
| Clé étrangère | Au moins 50 % des valeurs trouvées si les noms correspondent, 95 % sinon |
| Fait | Mesures à un grain répété, avec des clés, des dates ou des dimensions intégrées |
| Fait sans mesure | Événements reliés à plusieurs dimensions |
| Dimension | Table référencée qui décrit une entité |
| Table de liaison | Uniquement des clés étrangères : relation N:M |
| Dimension intégrée | Colonne texte répétée d'une table plate |
| Hiérarchie | Dépendance fonctionnelle mesurée sur des membres répétés |
| Temps | `dim_time` partagée, clé `AAAAMMJJ` |
| Modèles indépendants | Faits sans dimension commune hors calendrier |

## Propositions

- **Étoile** : les attributs parents sont repliés dans la dimension.
- **Flocon** : proposé quand une hiérarchie existe, avec des dimensions parentes reliées.
- **Constellation** : plusieurs faits partagent des dimensions.
- **Tables KPI** : `mart_<fait>_kpis`, par mois et par dimension principale, avec les noms métier.

Une ligne de détail hérite des dimensions et des dates de son en-tête. Par exemple, `fact_order_items` reçoit le client et la date de `orders`.

## API

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/v1/gold-models/analysis` | Analyse de Silver et propositions |
| POST | `/api/v1/gold-models/preview` | Tables, colonnes et exemples, sans écriture |
| POST | `/api/v1/gold-models/generate` | Création du pipeline et exécution |
| GET | `/api/v1/datasets/{id}/charts` | Graphiques avec noms métier |
| GET | `/api/v1/datasets/{id}/preview?view=business` | Aperçu avec noms métier |

## Limites

- L'analyse lit au plus 200 000 lignes par table Silver. Le pipeline généré traite toutes les lignes.
- En étoile, un seul niveau de table parente est replié dans une dimension.
- Un fait lit au plus 4 tables Silver, soit 3 jointures. Les liens au-delà sont ignorés.
- Les conflits entre sources et les doublons d'entités sont signalés, jamais fusionnés automatiquement.
