"""Загрузка и парсинг вложений (§16).

Endpoint без сохранения состояния: файл парсится во временной памяти, клиенту
возвращается извлечённый текст и/или изображения (data-URL). Исходный файл не
хранится. Разобранное содержимое клиент отправляет вместе со следующим
сообщением чата (см. chat.send_message).

Здесь только разбор — модель не задействована. Расшифровка картинок (§16)
делается позже, при отправке сообщения (app/routers/chat.py): она стоит
полноценной генерации, и место ей внутри хода, где видно, чем занята модель,
и где её обрывает кнопка «Остановить».
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.config import settings
from app.documents import DocumentError, parse_upload

router = APIRouter(prefix="/api", tags=["attachments"])


@router.post("/attachments")
async def upload_attachment(
    file: UploadFile = File(...),
    pdf_mode: str = Form("vision"),  # 'vision' | 'text' | 'auto' — только для PDF
    user: dict = Depends(get_current_user),
) -> dict:
    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"Файл больше {settings.max_upload_mb} МБ")
    if pdf_mode not in ("vision", "text", "auto"):
        pdf_mode = "vision"
    # Разбор — чистый счёт: Pillow, растеризация PDF, распаковка zip. В event
    # loop он вставал колом на секунды, и всё это время сервер не отвечал
    # НИКОМУ — процесс-то один на весь завод. Уводим в поток.
    try:
        doc = await run_in_threadpool(
            parse_upload, file.filename or "file", file.content_type or "", data,
            pdf_mode=pdf_mode)
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "filename": doc.filename,
        "text": doc.text,
        "images": doc.images,
        "warnings": doc.warnings,
        "token_estimate": doc.token_estimate,
    }
