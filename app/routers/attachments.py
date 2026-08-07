"""Загрузка и парсинг вложений (§16).

Endpoint без сохранения состояния: файл парсится во временной памяти, клиенту
возвращается извлечённый текст и/или изображения (data-URL). Исходный файл не
хранится. Разобранное содержимое клиент отправляет вместе со следующим
сообщением чата (см. chat.send_message).

Здесь только быстрый разбор. На отправку сообщения отложено всё долгое
(app/routers/chat.py): растеризация страниц PDF в картинки и их расшифровка.
Место такому ожиданию внутри хода, где видно, чем занят сервер, и где
работает «Остановить», — а не на закреплении файла с заблокированной кнопкой.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.config import settings
from app.documents import DocumentError, parse_upload
from app.pending_pdf import put as put_pending_pdf

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
    body = {
        "filename": doc.filename,
        "text": doc.text,
        "images": doc.images,
        "warnings": doc.warnings,
        "token_estimate": doc.token_estimate,
    }
    if doc.pdf_pages:
        # Страницы нарисуются при отправке; файл ждёт своей очереди в памяти
        body["pdf_pages"] = doc.pdf_pages
        body["pdf_token"] = put_pending_pdf(user["id"], doc.filename, data)
    return body
