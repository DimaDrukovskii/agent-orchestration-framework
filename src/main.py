import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.api import routes
from src.api import hermes_routes
from src.api import hermes_async_routes
from src.api import tts_routes
from src.api import llm_routes
from src.api import stt_routes
from src.api import tenant_routes
from src.worker import run_worker
from src.config import non_chat_model, settings
from src.utils import logger

if settings.llm_provider == "polza" and non_chat_model(settings.polza_model):
    logger.error(
        f"POLZA_MODEL={settings.polza_model} это модель распознавания или озвучки, а не чата: "
        "ответы агента будут падать с 400. Укажите чат-модель (например anthropic/claude-haiku-5.5) и "
        "пересоздайте контейнер: docker compose --env-file .env -f docker/docker-compose.yml up -d"
    )

app = FastAPI(
    title="MTS Call Processing Agent",
    version="1.1.0-hermes",
    description="AI Agent for processing MTS incoming calls — Hermes (tool-using, natural dialog)"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(routes.router, prefix="/api")
app.include_router(hermes_routes.router, prefix="/api")
app.include_router(hermes_async_routes.router, prefix="/api")
app.include_router(tts_routes.router, prefix="/api")
app.include_router(llm_routes.router, prefix="/api")
app.include_router(stt_routes.router, prefix="/api")
app.include_router(tenant_routes.router, prefix="/api")

# Демо-страницы (чат, подключение, история) — тот же origin, что и /api,
# иначе fetch('/api/...') из demo/*.html упирается в CORS/file:// при
# открытии файла напрямую. html=True отдаёт index.html на /demo/.
app.mount("/demo", StaticFiles(directory="demo", html=True), name="demo")

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.1.0-hermes"}

@app.on_event("startup")
async def start_background_worker():
    # Скелет под нагрузку МТС (docs/mts/SCALING_PLAN.md §4.1) — фоновая задача
    # в этом же процессе, не отдельный воркер-процесс. Этого достаточно,
    # чтобы показать структуру; реальное горизонтальное масштабирование
    # воркера — когда очередь переедет на Redis/SQS.
    asyncio.create_task(run_worker())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
