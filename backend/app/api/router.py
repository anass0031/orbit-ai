from fastapi import APIRouter

from app.api.v1 import analyst, auth, datasets, gold_models, health, imports, notifications, pipelines, quality, runs, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(imports.router)
api_router.include_router(datasets.router)
api_router.include_router(gold_models.router)
api_router.include_router(pipelines.router)
api_router.include_router(runs.router)
api_router.include_router(quality.router)
api_router.include_router(notifications.router)
api_router.include_router(analyst.router)
