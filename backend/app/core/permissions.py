"""Role → permission matrix for the three roles of the cahier des charges.

- admin          : manages the whole platform, the users and reads the audit log.
- data_engineer  : imports files, builds and runs pipelines (Silver / Gold), defines quality rules.
- analyst        : reads the catalogue, pipelines, runs and quality results, handles quality anomalies and
                   documents datasets (glossary, column descriptions).
"""

ROLES = ("admin", "data_engineer", "analyst")

ROLE_LABELS = {
    "admin": "Administrator",
    "data_engineer": "Data Engineer",
    "analyst": "Analyst",
}

_READ = {"imports.read", "datasets.read", "pipelines.read", "runs.read", "quality.read", "glossary.read"}
_ENGINEER = _READ | {"imports.write", "pipelines.write", "pipelines.run", "quality.write", "quality.run", "quality.triage",
                    "catalog.document"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": _ENGINEER | {"users.manage", "audit.read"},
    "data_engineer": _ENGINEER,
    "analyst": _READ | {"quality.triage", "catalog.document"},
}


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def permissions_for(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role, set()))
