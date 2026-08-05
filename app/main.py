"""FastAPI application: routing, static files, security middleware."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.db import init_db
from app.routers import admin as admin_router
from app.routers import attachments as attachments_router
from app.routers import auth as auth_router
from app.routers import calendar as calendar_router
from app.routers import chat as chat_router
from app.routers import meta as meta_router
from app.routers import notes as notes_router
from app.routers import tools as tools_router

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Пределы размера тела запроса (отсекаем ДО парсинга — защита от исчерпания
# памяти и диска):
#   • загрузка файла — строго по MAX_UPLOAD_MB (+запас на multipart-обёртку);
#   • остальные запросы — MAX_BODY_MB: сюда попадает отправка сообщения с PDF
#     в режиме «как картинку», где КАЖДАЯ страница уходит отдельной base64-
#     картинкой (документ на 50+ листов — это десятки МБ, и это штатный режим).
MAX_UPLOAD_BYTES = (settings.max_upload_mb + 2) * 1024 * 1024
MAX_BODY_BYTES = max(settings.max_body_mb * 1024 * 1024, MAX_UPLOAD_BYTES)
UPLOAD_PATH_PREFIX = "/api/attachments"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Политика хранения (CHAT_RETENTION_DAYS): чистим при старте и раз в сутки
    from app.retention import retention_loop
    task = asyncio.create_task(retention_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="srv-ai webUI", lifespan=lifespan)


@app.middleware("http")
async def security_headers_and_csrf(request: Request, call_next):
    if request.method in MUTATING_METHODS:
        # Отсекаем слишком большое тело до парсинга (OOM/переполнение диска)
        content_length = request.headers.get("content-length")
        if content_length is not None:
            limit = (MAX_UPLOAD_BYTES
                     if request.url.path.startswith(UPLOAD_PATH_PREFIX)
                     else MAX_BODY_BYTES)
            try:
                oversized = int(content_length) > limit
            except ValueError:
                return JSONResponse({"detail": "Некорректный Content-Length"}, status_code=400)
            if oversized:
                return JSONResponse({"detail": "Тело запроса слишком большое"}, status_code=413)

        # CSRF: на мутациях Origin обязателен и должен совпадать с хостом.
        # SameSite=Lax уже отсекает межсайтовые запросы; это — второй рубеж
        # (в т.ч. на случай запросов вовсе без Origin).
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if origin is None or origin.split("://", 1)[-1] != host:
            return JSONResponse({"detail": "Invalid Origin"}, status_code=403)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # style-src 'unsafe-inline' — только для инлайн-стилей KaTeX (формулы);
    # скрипты по-прежнему строго 'self', пользовательский HTML экранируется.
    # base-uri, form-action, object-src и frame-ancestors из default-src НЕ
    # выводятся — их приходится задавать отдельно, иначе они не ограничены
    # вовсе: <base href> увёл бы все относительные ссылки на чужой хост.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "base-uri 'self'; form-action 'self'; object-src 'none'; "
        "frame-ancestors 'none'")
    return response


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(chat_router.router)
app.include_router(notes_router.router)
app.include_router(calendar_router.router)
app.include_router(tools_router.router)
app.include_router(meta_router.router)
app.include_router(attachments_router.router)

STATIC_DIR = BASE_DIR / "static"


@app.get("/", include_in_schema=False)
async def index_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
async def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
