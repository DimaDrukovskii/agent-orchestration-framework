import asyncio
from contextlib import suppress
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.api import hermes_routes
from src.api import tts_routes
from src.api import llm_routes
from src.api import stt_routes
from src.api import tenant_routes
from src.api import analytics_routes, callback_routes, simulator_routes
from src.api import ready_routes
from src.telephony import asterisk, routes as telephony_routes
from src.config import non_chat_model, settings
from src.utils import logger

if settings.llm_provider == "polza" and non_chat_model(settings.polza_model):
    logger.error(
        f"POLZA_MODEL={settings.polza_model} это модель распознавания или озвучки, а не чата: "
        "ответы агента будут падать с 400. Укажите чат-модель (например anthropic/claude-haiku-5.5) и "
        "пересоздайте контейнер: docker compose --env-file .env -f docker/docker-compose.yml up -d"
    )

app = FastAPI(
    title="Voice Agent",
    version="1.1.0-hermes",
    description="Configurable voice assistant powered by Hermes"
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
app.include_router(hermes_routes.router, prefix="/api")
app.include_router(tts_routes.router, prefix="/api")
app.include_router(llm_routes.router, prefix="/api")
app.include_router(stt_routes.router, prefix="/api")
app.include_router(tenant_routes.router, prefix="/api")
app.include_router(analytics_routes.router, prefix="/api")
app.include_router(callback_routes.router, prefix="/api")
app.include_router(simulator_routes.router, prefix="/api")
app.include_router(ready_routes.router)  # GET /ready: что настроено, чего не хватает
app.include_router(telephony_routes.router)  # Twilio: свои адреса /telephony/twilio/*, авторизация по подписи Twilio
app.include_router(asterisk.router)

# Демо-страницы (виджет, лендинг, служебные в demo/tools) — тот же origin, что и /api,
# иначе fetch('/api/...') из demo/*.html упирается в CORS/file:// при
# открытии файла напрямую. html=True отдаёт index.html на /demo/.
app.mount("/demo", StaticFiles(directory="demo", html=True), name="demo")

@app.on_event("startup")
async def warm_telephony():
    if settings.telephony_enabled:
        asyncio.create_task(telephony_routes.prewarm())


@app.on_event("startup")
async def start_callbacks():
    if settings.callback_simulator_enabled:
        app.state.callback_worker = asyncio.create_task(callback_routes.run_worker())


@app.on_event("shutdown")
async def stop_callbacks():
    task = getattr(app.state, "callback_worker", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@app.get("/health")
async def health_check(response: Response):
    worker = getattr(app.state, "callback_worker", None)
    if settings.callback_simulator_enabled and (worker is None or worker.done()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "component": "callback_worker"}
    return {"status": "ok", "version": "1.1.0-hermes"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
