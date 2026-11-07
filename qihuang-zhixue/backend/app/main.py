from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api.auth import router as auth_router
from app.capabilities.http import router as capabilities_router
from app.api.chat import router as chat_router
from app.api.learning import router as learning_router
from app.api.immersive import router as immersive_router
from app.api.path import router as path_router
from app.api.profile import router as profile_router
from app.api.resources import router as resources_router
from app.api.graph import router as graph_router
from app.api.kg import router as kg_router
from app.api.mistakes import router as mistakes_router
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
app.include_router(kg_router)
app.include_router(immersive_router)
app.include_router(quiz_router)
app.include_router(mistakes_router)
app.include_router(capabilities_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查接口，供前端和部署探针使用。"""

    return {"status": "ok", "service": settings.app_name}


#: P4 教材抽取的人工复核单页（`scripts.textbook.review_sheet` 生成）。
REVIEW_SHEET = Path(__file__).resolve().parents[2] / "deliverables" / "review_sheet.html"


@app.get("/review", response_class=HTMLResponse)
async def review_sheet() -> HTMLResponse:
    """直接吐复核单页的静态 HTML。

    **故意不走前端 SPA。** 那套页面要先登录、要过 onboarding 引导，
    而复核是一个内部工具，打开就该看到 442 条药，不该先填一遍画像。

    也**故意不做成只有 JS 能渲染的页面**：如果浏览器或预览面板禁了脚本，
    页面会显式提示怎么打开，而不是白屏——白屏和「没问题」是同一种骗人。
    """

    if not REVIEW_SHEET.exists():
        return HTMLResponse(
            "<meta charset='utf-8'><h1>复核页还没生成</h1>"
            "<p>先跑：<code>python -m scripts.textbook.review_sheet</code></p>",
            status_code=404,
        )
    return HTMLResponse(REVIEW_SHEET.read_text(encoding="utf-8"))
