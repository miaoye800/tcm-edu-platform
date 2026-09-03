from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.learning import router as learning_router
from app.api.immersive import router as immersive_router
from app.api.path import router as path_router
from app.api.profile import router as profile_router
from app.api.resources import router as resources_router
from app.api.graph import router as graph_router
from app.api.quiz import router as quiz_router
from app.config import get_settings, warn_if_default_auth_secret

settings = get_settings()
warn_if_default_auth_secret(settings)

app = FastAPI(title="岐黄智学 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(profile_router)
app.include_router(learning_router)
app.include_router(path_router)
app.include_router(resources_router)
app.include_router(graph_router)
app.include_router(immersive_router)
app.include_router(quiz_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查接口，供前端和部署探针使用。"""

    return {"status": "ok", "service": settings.app_name}
