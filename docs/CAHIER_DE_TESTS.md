# Cahier de tests — ORBIT AI, semaine 4

## Résultats de l'intégration (18/11/2026)

| Contrôle | Résultat |
|---|---|
| `pytest` avec les conteneurs de démonstration | **98 réussis, 0 ignoré(s), 0 échec** |
| `ruff check .`, `tsc --noEmit`, `next lint`, `next build` | Sans erreur |
| Migrations : `upgrade head`, `check`, `downgrade -1`, `upgrade head` | Réussies sur SQLite et PostgreSQL |
| Pile Docker complète sur les ports de test | Tous les services `healthy` |
| Scénario réel via l'API | **40 contrôles sur 40** (fichier exporté, e-mail dans Mailpit, données REST reçues, Parquet en Bronze, star schema de `samples/`) |

Limite connue : les pages répondent 200, mais l'interface n'a pas été parcourue dans un vrai navigateur.

Fichiers de tests de cette semaine : authentification, import, découverte du schéma, pipelines, qualité, outils de pipeline, destinations, automatisation, catalogue, Parquet, règles médaillon, lignage, star schema et démonstration `samples/`.
