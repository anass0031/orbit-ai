# Catalogue pour l'analyste

## 1. Exploration d'un dataset

Page d'un dataset, onglets :

- **Profile :** valeurs vides, valeurs distinctes, minimum, maximum, moyenne, histogramme et valeurs fréquentes.
- **Compare versions :** colonnes modifiées, lignes ajoutées, supprimées ou changées.
- **Documentation :** description, tags et description de chaque colonne.
- **Lineage :** datasets sources et datasets produits.
- **Data model :** place d'une table Gold dans le star schema.

## 2. Glossaire métier

Le menu *Glossary* regroupe les définitions partagées (nom, définition, synonymes, liens vers des datasets et des colonnes).

## 3. Recherche

La recherche du catalogue porte sur les noms, descriptions, tags, colonnes, descriptions de colonnes et termes du glossaire.

## 4. Permissions

| Permission | Rôles |
|---|---|
| `glossary.read` | Tous les rôles |
| `catalog.document` | Administrateur, data engineer, analyste |
