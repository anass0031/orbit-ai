from fastapi import APIRouter

from app.api.v1 import auth, datasets, health, imports, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(imports.router)
api_router.include_router(datasets.router)
