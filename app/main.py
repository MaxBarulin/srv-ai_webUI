"""FastAPI application: routing, static files, security middleware."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.db import init_db
from app.routers import admin as admin_router
from app.routers import auth as auth_router

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="srv-ai webUI", lifespan=lifespan)


@app.middleware("http")
async def security_headers_and_csrf(request: Request, call_next):
    if request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if origin is not None and origin.split("://", 1)[-1] != host:
            return JSONResponse({"detail": "Invalid Origin"}, status_code=403)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router.router)
app.include_router(admin_router.router)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
