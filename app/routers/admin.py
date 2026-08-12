"""Admin endpoints: user management, specializations, chat examples, feedback export."""
from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.appsettings import (
    CALC_ACCESS,
    CALC_ACCESS_VALUES,
    LLM_API_KEY,
    LLM_API_KEY_CLEARED,
    LLM_BASE_URL,
    LLM_CONTEXT_SIZE,
    LLM_KEYS,
    LLM_MODEL,
    get_setting,
    load_llm_connection,
    set_setting,
)
from app.audit import utcnow_iso, write_audit
from app.auth import client_ip, hash_password, require_admin, validate_password
from app.calc import CalcError
from app.calc_methods import method_dict, normalize_method, run_method
from app.db import get_db
from app.groups import ensure_group_exists, ensure_name_free, normalize_group_name
from app.metrics import metrics

router = APIRouter(prefix="/api/admin", tags=["admin"])

SELECT_USER = """
SELECT u.id, u.login, u.display_name, u.role, u.is_active, u.created_at,
       u.group_id, g.name AS group_name
FROM users u LEFT JOIN groups g ON g.id = u.group_id
"""


class CreateUserRequest(BaseModel):
    login: str
    password: str
    display_name: str = ""
    role: str = "user"
    group_id: int | None = None


class SetActiveRequest(BaseModel):
    is_active: bool


class SetGroupRequest(BaseModel):
    group_id: int | None = None  # None — без группы: видит все общие записи


class ResetPasswordRequest(BaseModel):
    new_password: str


class GroupRequest(BaseModel):
    name: str


async def _get_user_or_404(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row:
    cursor = await db.execute(SELECT_USER + "WHERE u.id = ?", (user_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return row


@router.get("/users")
async def list_users(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(SELECT_USER + "ORDER BY u.login")
    return [dict(row) for row in await cursor.fetchall()]


@router.post("/users", status_code=201)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    login = payload.login.strip()
    if not login:
        raise HTTPException(status_code=400, detail="Логин не может быть пустым")
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    validate_password(payload.password)

    cursor = await db.execute("SELECT id FROM users WHERE login = ?", (login,))
    if await cursor.fetchone() is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким логином уже существует")
    await ensure_group_exists(db, payload.group_id)

    cursor = await db.execute(
        "INSERT INTO users (login, pass_hash, display_name, role, is_active, group_id, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?, ?)",
        (login, hash_password(payload.password), payload.display_name.strip() or login,
         payload.role, payload.group_id, utcnow_iso()),
    )
    await db.commit()
    user_id = cursor.lastrowid
    await write_audit(db, user_id=admin["id"], action="user_created",
                      object_type="user", object_id=str(user_id),
                      details=f"login={login}, role={payload.role}", ip=client_ip(request))
    return dict(await _get_user_or_404(db, user_id))


@router.post("/users/{user_id}/active")
async def set_user_active(
    user_id: int,
    payload: SetActiveRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    if user_id == admin["id"] and not payload.is_active:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")

    await db.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(payload.is_active), user_id))
    if not payload.is_active:
        # kill active sessions of the blocked user
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.commit()
    await write_audit(db, user_id=admin["id"],
                      action="user_unblocked" if payload.is_active else "user_blocked",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}", ip=client_ip(request))
    return dict(await _get_user_or_404(db, user_id))


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    if row["role"] == "admin":
        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?",
            (user_id,))
        if (await cursor.fetchone())[0] == 0:
            raise HTTPException(status_code=400,
                                detail="Нельзя удалить последнего активного администратора")

    # Каскад: всё, что владелец создал в системе. audit_log сохраняем, но
    # обнуляем ссылку — история действий не теряется.
    # Порядок важен: сначала зависимые таблицы (FK enforcement).
    await db.execute(
        "DELETE FROM feedback WHERE user_id = ? "
        "OR chat_id IN (SELECT id FROM chats WHERE user_id = ?) "
        "OR message_id IN (SELECT m.id FROM messages m "
        "  JOIN chats c ON c.id = m.chat_id WHERE c.user_id = ?)",
        (user_id, user_id, user_id))
    await db.execute(
        "DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE user_id = ?)",
        (user_id,))
    await db.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
    await db.execute(
        "DELETE FROM note_prefs WHERE user_id = ? OR note_id IN "
        "(SELECT id FROM notes WHERE owner_id = ?)", (user_id, user_id))
    await db.execute("DELETE FROM notes WHERE owner_id = ?", (user_id,))
    await db.execute("DELETE FROM events WHERE owner_id = ?", (user_id,))
    await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.execute("UPDATE notes SET updated_by = NULL WHERE updated_by = ?", (user_id,))
    await db.execute("UPDATE events SET updated_by = NULL WHERE updated_by = ?", (user_id,))
    await db.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()

    await write_audit(db, user_id=admin["id"], action="user_deleted",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}, role={row['role']}",
                      ip=client_ip(request))
    return {"ok": True}


@router.post("/users/{user_id}/password")
async def reset_user_password(
    user_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    validate_password(payload.new_password)
    await db.execute(
        "UPDATE users SET pass_hash = ? WHERE id = ?",
        (hash_password(payload.new_password), user_id),
    )
    # force re-login with the new password everywhere
    await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.commit()
    await write_audit(db, user_id=admin["id"], action="user_password_reset",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}", ip=client_ip(request))
    return {"ok": True}


# ===== Группы пользователей (отделы, бюро) =====
#
# Группа определяет, кто видит общие заметки и события. Пользователь без
# группы видит все общие записи; пользователь в группе — только общие своей
# группы и адресованные всем. Подробности правила — в app/scoping.py.

@router.get("/groups")
async def list_groups(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    # Счётчики нужны интерфейсу, чтобы предупредить о последствиях удаления
    cursor = await db.execute(
        "SELECT g.id, g.name, g.created_at, "
        "  (SELECT COUNT(*) FROM users WHERE group_id = g.id) AS user_count, "
        "  (SELECT COUNT(*) FROM notes WHERE group_id = g.id) AS note_count, "
        "  (SELECT COUNT(*) FROM events WHERE group_id = g.id) AS event_count "
        "FROM groups g ORDER BY g.name")
    return [dict(row) for row in await cursor.fetchall()]


@router.post("/groups", status_code=201)
async def create_group(
    payload: GroupRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    name = normalize_group_name(payload.name)
    await ensure_name_free(db, name)
    cursor = await db.execute(
        "INSERT INTO groups (name, created_at) VALUES (?, ?)", (name, utcnow_iso()))
    await db.commit()
    group_id = cursor.lastrowid
    await write_audit(db, user_id=admin["id"], action="group_created",
                      object_type="group", object_id=str(group_id),
                      details=f"name={name}", ip=client_ip(request))
    return {"id": group_id, "name": name}


@router.put("/groups/{group_id}")
async def rename_group(
    group_id: int,
    payload: GroupRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    name = normalize_group_name(payload.name)
    await ensure_name_free(db, name, exclude_id=group_id)
    cursor = await db.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    await write_audit(db, user_id=admin["id"], action="group_renamed",
                      object_type="group", object_id=str(group_id),
                      details=f"name={name}", ip=client_ip(request))
    return {"ok": True, "id": group_id, "name": name}


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cursor = await db.execute("SELECT name FROM groups WHERE id = ?", (group_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    # Удаление группы РАСШИРЯЕТ видимость, а не сужает: её участники остаются
    # без группы и начинают видеть все общие записи, а адресованные ей заметки
    # и события становятся общими для всех. Интерфейс предупреждает об этом
    # заранее числами из list_groups.
    await db.execute("UPDATE users SET group_id = NULL WHERE group_id = ?", (group_id,))
    await db.execute("UPDATE notes SET group_id = NULL WHERE group_id = ?", (group_id,))
    await db.execute("UPDATE events SET group_id = NULL WHERE group_id = ?", (group_id,))
    await db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    await db.commit()
    await write_audit(db, user_id=admin["id"], action="group_deleted",
                      object_type="group", object_id=str(group_id),
                      details=f"name={row['name']}", ip=client_ip(request))
    return {"ok": True}


@router.post("/users/{user_id}/group")
async def set_user_group(
    user_id: int,
    payload: SetGroupRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    await ensure_group_exists(db, payload.group_id)
    await db.execute("UPDATE users SET group_id = ? WHERE id = ?", (payload.group_id, user_id))
    await db.commit()
    updated = await _get_user_or_404(db, user_id)
    await write_audit(db, user_id=admin["id"], action="user_group_changed",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}, group="
                              f"{updated['group_name'] or 'без группы'}",
                      ip=client_ip(request))
    return dict(updated)


# ===== Подключение к модели =====
#
# .env задаёт исходное подключение, администратор может увести всех на другой
# сервер или внешнего провайдера, не перезапуская сервис. Ключ хранится в БД
# открытым текстом (шифровать его на изолированной машине, где ключ шифрования
# лежит рядом, — самообман) и НИКОГДА не возвращается наружу: интерфейс видит
# только «задан / не задан», аудит — только адрес и модель.

_ALLOWED_SCHEMES = ("http://", "https://")


class LLMConnRequest(BaseModel):
    base_url: str
    model: str
    context_size: int = 0
    # None — ключ не трогаем, "" — очистить, строка — задать новый
    api_key: str | None = None
    # Кто работает через это подключение. None — список не трогаем (например,
    # правим только адрес); список — он становится полным составом.
    user_ids: list[int] | None = None


def _validate_conn(payload: LLMConnRequest) -> tuple[str, str]:
    base_url = payload.base_url.strip()
    model = payload.model.strip()
    if not base_url.startswith(_ALLOWED_SCHEMES):
        raise HTTPException(status_code=400, detail="Адрес должен начинаться с http:// или https://")
    if not model:
        raise HTTPException(status_code=400, detail="Не указана модель")
    if payload.context_size < 0:
        raise HTTPException(status_code=400, detail="Размер контекста не может быть отрицательным")
    # Ключ уходит в HTTP-заголовок, а тот обязан быть в ASCII: с кириллицей
    # (например, скопированной вместе с подсказкой) запрос падал бы уже в
    # httpx, и администратор видел бы 500 вместо внятного объяснения.
    key = (payload.api_key or "").strip()
    if key and not key.isascii():
        raise HTTPException(status_code=400,
                            detail="Ключ доступа должен быть без кириллицы и других "
                                   "нелатинских символов")
    return base_url, model


async def _conn_view(db: aiosqlite.Connection) -> dict:
    """Состояние подключения для интерфейса — без ключа, но со списком людей."""
    from app.config import settings
    from app.llm import custom_connection

    conn = custom_connection()
    cursor = await db.execute(
        "SELECT id, login, display_name, llm_custom FROM users "
        "WHERE is_active = 1 ORDER BY display_name COLLATE NOCASE, login")
    users = [{"id": r["id"], "login": r["login"], "display_name": r["display_name"],
              "enabled": bool(r["llm_custom"])} for r in await cursor.fetchall()]
    return {
        "base_url": conn["base_url"],
        "model": conn["model"],
        "context_size": conn["context_size"],
        "has_key": bool(conn["api_key"]),
        "custom": conn["custom"],
        "users": users,
        "enabled_count": sum(1 for u in users if u["enabled"]),
        # Чтобы администратор видел, на чём остаются все остальные
        "env": {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "context_size": settings.llm_context_size,
            "has_key": bool(settings.llm_api_key),
        },
    }


async def _set_conn_users(db: aiosqlite.Connection, user_ids: list[int]) -> None:
    """Полный состав тех, кто работает через дополнительное подключение."""
    await db.execute("UPDATE users SET llm_custom = 0")
    for user_id in user_ids:
        await db.execute("UPDATE users SET llm_custom = 1 WHERE id = ?", (user_id,))


@router.get("/llm")
async def get_llm_connection(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    return await _conn_view(db)


@router.put("/llm")
async def update_llm_connection(
    payload: LLMConnRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    base_url, model = _validate_conn(payload)
    await set_setting(db, LLM_BASE_URL, base_url)
    await set_setting(db, LLM_MODEL, model)
    await set_setting(db, LLM_CONTEXT_SIZE, str(payload.context_size or ""))
    if payload.api_key is not None:
        await set_setting(db, LLM_API_KEY, payload.api_key.strip())
        # Пустая строка в настройках значит «как в .env», поэтому явную
        # очистку ключа отмечаем отдельно.
        await set_setting(db, LLM_API_KEY_CLEARED, "" if payload.api_key.strip() else "1")
    if payload.user_ids is not None:
        await _set_conn_users(db, payload.user_ids)
    await db.commit()
    await load_llm_connection(db)
    view = await _conn_view(db)
    # В аудите — состав поимённо: это и есть ответ на вопрос «чьи данные ушли
    # на этот сервер и с какого момента».
    enabled = ", ".join(u["login"] for u in view["users"] if u["enabled"]) or "никто"
    await write_audit(db, user_id=admin["id"], action="llm_connection_changed",
                      object_type="setting", object_id="llm",
                      details=f"base_url={base_url}, model={model}, включено: {enabled}",
                      ip=client_ip(request))
    await db.commit()
    return view


@router.post("/llm/reset")
async def reset_llm_connection(
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    for key in LLM_KEYS:
        await set_setting(db, key, "")
    # Сброс снимает и отметки: иначе настройки вернулись бы к .env, а список
    # людей остался бы — и при следующей правке адреса они молча уехали бы
    # на новый сервер, о чём администратор уже не помнит.
    await _set_conn_users(db, [])
    await db.commit()
    await load_llm_connection(db)
    await write_audit(db, user_id=admin["id"], action="llm_connection_reset",
                      object_type="setting", object_id="llm",
                      details="настройки очищены, отметки сняты со всех",
                      ip=client_ip(request))
    await db.commit()
    return await _conn_view(db)


@router.post("/llm/test")
async def test_llm_connection(
    payload: LLMConnRequest,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Проверить подключение ДО сохранения — теми значениями, что в форме.

    Спрашиваем /v1/models: единственный метод, который есть и у llama.cpp, и у
    OpenAI-совместимых провайдеров, и который ничего не стоит по токенам.
    """
    import httpx

    from app import llm as llm_module

    base_url, model = _validate_conn(payload)
    key = payload.api_key
    if key is None:                      # ключ не меняли — берём сохранённый
        key = llm_module.custom_connection()["api_key"]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers,
                                     timeout=httpx.Timeout(10.0, connect=5.0),
                                     transport=llm_module._transport) as client:
            r = await client.get("/models")
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Соединение не установлено: {exc.__class__.__name__}"}
    if r.status_code == 401 or r.status_code == 403:
        return {"ok": False, "error": "Сервер отклонил ключ доступа"}
    if r.status_code != 200:
        return {"ok": False, "error": f"Сервер ответил {r.status_code}: "
                                      f"{r.text[:200]}"}
    try:
        names = [m.get("id", "") for m in (r.json().get("data") or []) if m.get("id")]
    except (ValueError, AttributeError):
        names = []
    return {"ok": True, "models": names[:50],
            "model_found": (not names) or model in names}


# ===== Расчёты (§17): кому доступен инструмент вычислений =====
#
# Держим в БД, а не в .env, чтобы открыть функцию на всех сотрудников или
# аварийно её погасить можно было галочкой в интерфейсе — без выкатки новой
# версии на закрытый контур.

class CalcSettingsRequest(BaseModel):
    access: str


@router.get("/calc/settings")
async def get_calc_settings(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    return {"access": await get_setting(db, CALC_ACCESS)}


@router.put("/calc/settings")
async def update_calc_settings(
    payload: CalcSettingsRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    if payload.access not in CALC_ACCESS_VALUES:
        raise HTTPException(status_code=400, detail="Недопустимый уровень доступа")
    await set_setting(db, CALC_ACCESS, payload.access)
    await db.commit()
    await write_audit(db, user_id=admin["id"], action="calc_access_changed",
                      object_type="setting", object_id=CALC_ACCESS,
                      details=f"access={payload.access}", ip=client_ip(request))
    return {"ok": True, "access": payload.access}


# ===== Расчёты (§17): справочник методик =====
#
# Формулы проверяются здесь, при сохранении: опечатка администратора должна
# всплыть сразу и с номером строки, а не при первом обращении модели.

class MethodRequest(BaseModel):
    name: str
    description: str = ""
    params_text: str = ""
    steps_text: str = ""
    is_active: bool = True
    sort_order: int = 0


SELECT_METHOD = ("SELECT id, name, description, params_json, steps_json, "
                 "is_active, sort_order FROM calc_methods ")


@router.get("/calc/methods")
async def list_calc_methods(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(SELECT_METHOD + "ORDER BY sort_order, id")
    return [method_dict(row) for row in await cursor.fetchall()]


async def _method_or_404(db: aiosqlite.Connection, method_id: int) -> aiosqlite.Row:
    cursor = await db.execute(SELECT_METHOD + "WHERE id = ?", (method_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Методика не найдена")
    return row


def _normalized(payload: MethodRequest) -> dict:
    try:
        return normalize_method(payload.name, payload.description,
                                payload.params_text, payload.steps_text)
    except CalcError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/calc/methods", status_code=201)
async def create_calc_method(
    payload: MethodRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    data = _normalized(payload)
    now = utcnow_iso()
    cursor = await db.execute(
        "INSERT INTO calc_methods (name, description, params_json, steps_json, "
        "is_active, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (data["name"], data["description"],
         json.dumps(data["params"], ensure_ascii=False),
         json.dumps(data["steps"], ensure_ascii=False),
         int(payload.is_active), payload.sort_order, now, now))
    await db.commit()
    method_id = cursor.lastrowid
    await write_audit(db, user_id=admin["id"], action="calc_method_created",
                      object_type="calc_method", object_id=str(method_id),
                      details=f"name={data['name']}", ip=client_ip(request))
    return method_dict(await _method_or_404(db, method_id))


@router.put("/calc/methods/{method_id}")
async def update_calc_method(
    method_id: int,
    payload: MethodRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    await _method_or_404(db, method_id)
    data = _normalized(payload)
    await db.execute(
        "UPDATE calc_methods SET name = ?, description = ?, params_json = ?, "
        "steps_json = ?, is_active = ?, sort_order = ?, updated_at = ? WHERE id = ?",
        (data["name"], data["description"],
         json.dumps(data["params"], ensure_ascii=False),
         json.dumps(data["steps"], ensure_ascii=False),
         int(payload.is_active), payload.sort_order, utcnow_iso(), method_id))
    await db.commit()
    await write_audit(db, user_id=admin["id"], action="calc_method_updated",
                      object_type="calc_method", object_id=str(method_id),
                      details=f"name={data['name']}", ip=client_ip(request))
    return method_dict(await _method_or_404(db, method_id))


@router.delete("/calc/methods/{method_id}")
async def delete_calc_method(
    method_id: int,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _method_or_404(db, method_id)
    await db.execute("DELETE FROM calc_methods WHERE id = ?", (method_id,))
    await db.commit()
    # Прежние ответы не пострадают: формулы сохранены в трассе сообщения
    await write_audit(db, user_id=admin["id"], action="calc_method_deleted",
                      object_type="calc_method", object_id=str(method_id),
                      details=f"name={row['name']}", ip=client_ip(request))
    return {"ok": True}


class MethodTryRequest(BaseModel):
    params: dict[str, float] = {}


@router.post("/calc/methods/{method_id}/try")
async def try_calc_method(
    method_id: int,
    payload: MethodTryRequest,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Прогнать методику на пробных числах — проверить её до выдачи людям."""
    try:
        out = await run_method(db, method_id, dict(payload.params))
    except CalcError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"steps": out["trace"], "result": out["trace"][-1]["value"]}


# ===== Специализации (§15) =====

class SpecializationBody(BaseModel):
    name: str
    system_prompt: str = ""
    is_active: bool = True
    sort_order: int = 0


@router.get("/specializations")
async def admin_list_specializations(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, name, system_prompt, is_active, sort_order, created_at "
        "FROM specializations ORDER BY sort_order, id")
    return [dict(row) for row in await cursor.fetchall()]


@router.post("/specializations", status_code=201)
async def admin_create_specialization(
    payload: SpecializationBody,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    cursor = await db.execute(
        "INSERT INTO specializations (name, system_prompt, is_active, sort_order, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, payload.system_prompt, int(payload.is_active), payload.sort_order, utcnow_iso()))
    await db.commit()
    return {"id": cursor.lastrowid}


@router.put("/specializations/{spec_id}")
async def admin_update_specialization(
    spec_id: int,
    payload: SpecializationBody,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    cursor = await db.execute(
        "UPDATE specializations SET name = ?, system_prompt = ?, is_active = ?, sort_order = ? "
        "WHERE id = ?",
        (name, payload.system_prompt, int(payload.is_active), payload.sort_order, spec_id))
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Специализация не найдена")
    return {"ok": True}


@router.delete("/specializations/{spec_id}")
async def admin_delete_specialization(
    spec_id: int,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    # Чаты, ссылавшиеся на специализацию, останутся с NULL (общий режим)
    await db.execute("UPDATE chats SET specialization_id = NULL WHERE specialization_id = ?",
                     (spec_id,))
    cursor = await db.execute("DELETE FROM specializations WHERE id = ?", (spec_id,))
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Специализация не найдена")
    return {"ok": True}


# ===== Примеры запросов в пустом чате (§15) =====

class ExamplesBody(BaseModel):
    items: list[str]


@router.get("/examples")
async def admin_list_examples(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute("SELECT id, text FROM chat_examples ORDER BY sort_order, id")
    return [dict(row) for row in await cursor.fetchall()]


@router.put("/examples")
async def admin_set_examples(
    payload: ExamplesBody,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    items = [t.strip() for t in payload.items if t.strip()]
    await db.execute("DELETE FROM chat_examples")
    for order, text in enumerate(items):
        await db.execute("INSERT INTO chat_examples (text, sort_order) VALUES (?, ?)", (text, order))
    await db.commit()
    return {"ok": True, "count": len(items)}


# ===== Выгрузка обратной связи в JSONL (§15) =====

@router.get("/feedback/export")
async def export_feedback(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> StreamingResponse:
    cursor = await db.execute(
        "SELECT f.id, f.message_id, f.chat_id, f.rating, f.comment, f.specialization, "
        "       f.created_at, u.login AS user_login, "
        "       q.content AS prompt, m.content AS answer "
        "FROM feedback f "
        "JOIN messages m ON m.id = f.message_id "
        "JOIN users u ON u.id = f.user_id "
        "LEFT JOIN messages q ON q.id = ("
        "    SELECT MAX(id) FROM messages WHERE chat_id = f.chat_id "
        "    AND id < f.message_id AND role = 'user') "
        "ORDER BY f.id")
    rows = await cursor.fetchall()

    def generate():
        for row in rows:
            yield json.dumps(dict(row), ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=feedback.jsonl"},
    )


# ===== Аудит-лог (§10, §13) — только просмотр админом =====

@router.get("/audit")
async def list_audit(
    limit: int = 100,
    offset: int = 0,
    action: str = "",
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    limit = max(1, min(limit, 500))
    conditions = []
    params: list = []
    if action.strip():
        conditions.append("a.action = ?")
        params.append(action.strip())
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_cur = await db.execute(f"SELECT COUNT(*) FROM audit_log a {where}", params)
    total = (await count_cur.fetchone())[0]

    cursor = await db.execute(
        "SELECT a.id, a.action, a.object_type, a.object_id, a.details, a.created_at, a.ip, "
        "       u.login AS user_login "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
        f"{where} ORDER BY a.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset])
    items = [dict(row) for row in await cursor.fetchall()]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ===== Метрики (§13, п. 3.7) — без содержания запросов =====

@router.get("/metrics")
async def get_metrics(admin: dict = Depends(require_admin)) -> dict:
    return metrics.snapshot()
