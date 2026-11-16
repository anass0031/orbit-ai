"""Role → permission matrix for the three roles of the cahier des charges.

- admin          : manages the whole platform, the users and reads the audit log.
- data_engineer  : imports files (schema discovery + Bronze ingestion) and reads the catalogue.
- analyst        : reads the catalogue (datasets, schemas, previews) and the import history.

Later weeks add permissions (pipelines, quality, SQL, ML, AI) to this same table.
"""

ROLES = ("admin", "data_engineer", "analyst")

ROLE_LABELS = {
    "admin": "Administrator",
    "data_engineer": "Data Engineer",
    "analyst": "Analyst",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "imports.read", "imports.write",
        "datasets.read",
        "users.manage", "audit.read",
    },
    "data_engineer": {
        "imports.read", "imports.write",
        "datasets.read",
    },
    "analyst": {
        "imports.read",
        "datasets.read",
    },
}


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def permissions_for(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role, set()))
