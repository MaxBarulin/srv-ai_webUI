"""Загрузка и парсинг вложений (§16).

Endpoint без сохранения состояния: файл парсится во временной памяти, клиенту
возвращается извлечённый текст и/или изображения (data-URL). Исходный файл не
хранится. Разобранное содержимое клиент отправляет вместе со следующим
сообщением чата (см. chat.send_message).

Картинки здесь же расшифровываются (app.transcribe): в переписке они не
сохраняются, и без пересказа от них на следующем ходу остаётся одно имя файла.
Из-за этого ответ на загрузку картинки идёт десятки секунд — это плата
осознанная, ждать приходится, пока пользователь дописывает вопрос.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.config import settings
from app.documents import DocumentError, parse_upload
from app.transcribe import TranscribeBusy, transcribe_images

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
    try:
        transcript, warning = await transcribe_images(user["id"], doc.filename, doc.images)
    except TranscribeBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    warnings = list(doc.warnings)
    if warning:
        warnings.append(warning)
    return {
        "filename": doc.filename,
        "text": doc.text,
        "images": doc.images,
        "transcript": transcript,
        "warnings": warnings,
        "token_estimate": doc.token_estimate,
    }
