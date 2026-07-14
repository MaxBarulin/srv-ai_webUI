"""Инструменты LLM (§7): заметки, календарь, текущая дата/время.

Исполнение — всегда от имени текущего пользователя (те же права, что в UI:
чужие личные данные недоступны). Каждый выполненный вызов пишется в audit_log
(только факт: кто, какой инструмент, какой объект — без содержания).

Деструктивные действия (удаление, перезапись текста заметки) при
TOOLS_CONFIRM_DESTRUCTIVE=true не исполняются сразу: регистрируется отложенное
действие, пользователю в UI показывается кнопка подтверждения, а модель
получает ответ «требуется подтверждение».
"""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime

from app.audit import utcnow_iso, write_audit
from app.config import settings
from app.db import get_connection
from app.llm import APP_TZ, _WEEKDAYS_RU

MAX_TOOL_ITERATIONS = 6
SEARCH_LIMIT = 20

_SCOPE_PROP = {"type": "string", "enum": ["personal", "shared"],
               "description": "personal — личная, shared — общая"}
_SCOPE_FILTER_PROP = {"type": "string", "enum": ["personal", "shared", "all"],
                      "description": "Фильтр по области; all — все доступные"}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS_SPEC: list[dict] = [
    _tool("notes_search",
          "Поиск заметок пользователя по подстроке и тэгам. Возвращает список без текста заметок.",
          {
              "query": {"type": "string", "description": "Подстрока в заголовке или тексте"},
              "scope": _SCOPE_FILTER_PROP,
              "tags": {"type": "array", "items": {"type": "string"},
                       "description": "Заметка должна содержать все указанные тэги"},
          }, []),
    _tool("notes_get", "Получить заметку целиком (с текстом) по id.",
          {"id": {"type": "integer"}}, ["id"]),
    _tool("notes_create", "Создать заметку.",
          {
              "title": {"type": "string", "description": "Заголовок"},
              "text": {"type": "string", "description": "Текст заметки (markdown)"},
              "scope": _SCOPE_PROP,
              "tags": {"type": "array", "items": {"type": "string"}},
          }, ["title"]),
    _tool("notes_update",
          "Изменить заметку. Передавай только изменяемые поля. "
          "Поле text полностью заменяет прежний текст заметки.",
          {
              "id": {"type": "integer"},
              "title": {"type": "string"},
              "text": {"type": "string"},
              "scope": _SCOPE_PROP,
              "tags": {"type": "array", "items": {"type": "string"}},
          }, ["id"]),
    _tool("notes_delete", "Удалить заметку по id.", {"id": {"type": "integer"}}, ["id"]),
    _tool("calendar_list",
          "Список событий календаря за период. Даты — ISO 8601 (YYYY-MM-DD или с временем).",
          {
              "date_from": {"type": "string", "description": "Начало периода, ISO 8601"},
              "date_to": {"type": "string", "description": "Конец периода, ISO 8601"},
              "scope": _SCOPE_FILTER_PROP,
          }, []),
    _tool("calendar_create",
          "Создать событие календаря. Время — ISO 8601 со смещением, часовой пояс Europe/Moscow "
          "(+03:00), например 2026-07-15T10:00:00+03:00.",
          {
              "title": {"type": "string"},
              "starts_at": {"type": "string", "description": "Начало, ISO 8601"},
              "ends_at": {"type": "string", "description": "Окончание, ISO 8601"},
              "description": {"type": "string"},
              "location": {"type": "string", "description": "Место проведения"},
              "all_day": {"type": "boolean", "description": "Событие на весь день"},
              "scope": _SCOPE_PROP,
          }, ["title", "starts_at", "ends_at"]),
    _tool("calendar_update",
          "Изменить событие календаря (например, перенести). Передавай только изменяемые поля.",
          {
              "id": {"type": "integer"},
              "title": {"type": "string"},
              "starts_at": {"type": "string"},
              "ends_at": {"type": "string"},
              "description": {"type": "string"},
              "location": {"type": "string"},
              "all_day": {"type": "boolean"},
              "scope": _SCOPE_PROP,
          }, ["id"]),
    _tool("calendar_delete", "Удалить событие календаря по id.",
          {"id": {"type": "integer"}}, ["id"]),
    _tool("get_current_datetime", "Текущие дата, время и день недели (Europe/Moscow).", {}, []),
]


class ToolError(Exception):
    """Ошибка исполнения инструмента — текст возвращается модели."""


def is_destructive(name: str, args: dict) -> bool:
    if name in ("notes_delete", "calendar_delete"):
        return True
    return name == "notes_update" and "text" in args


# --- Отложенные действия (подтверждение деструктивных) ---

_PENDING: dict[str, dict] = {}
PENDING_TTL = 600  # секунд


def _cleanup_pending() -> None:
    now = time.monotonic()
    for token in [t for t, p in _PENDING.items() if p["expires"] < now]:
        del _PENDING[token]


def register_pending(user: dict, name: str, args: dict, label: str) -> str:
    _cleanup_pending()
    token = secrets.token_urlsafe(16)
    _PENDING[token] = {
        "user_id": user["id"],
        "user": user,
        "name": name,
        "args": args,
        "label": label,
        "expires": time.monotonic() + PENDING_TTL,
    }
    return token


def pop_pending(token: str, user_id: int) -> dict | None:
    _cleanup_pending()
    pending = _PENDING.get(token)
    if pending is None or pending["user_id"] != user_id:
        return None
    del _PENDING[token]
    return pending


# --- Валидация аргументов ---

def _req_int(args: dict, key: str) -> int:
    value = args.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolError(f"Параметр {key} должен быть целым числом")
    return value


def _str(args: dict, key: str, default: str = "") -> str:
    value = args.get(key, default)
    if not isinstance(value, str):
        raise ToolError(f"Параметр {key} должен быть строкой")
    return value


def _tags(args: dict) -> list[str] | None:
    if "tags" not in args:
        return None
    value = args["tags"]
    if isinstance(value, str):  # модели часто передают строку вместо массива
        value = [t for t in value.split(",")]
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ToolError("Параметр tags должен быть массивом строк")
    return value


def _scope(args: dict, default: str, allow_all: bool = False) -> str:
    value = args.get("scope", default)
    allowed = ("personal", "shared", "all") if allow_all else ("personal", "shared")
    if value not in allowed:
        raise ToolError(f"Параметр scope должен быть одним из: {', '.join(allowed)}")
    return value


def _check_iso(value: str, field: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(f"Некорректная дата/время в поле {field}: {value!r}")
    return value


def _tags_to_str(tags: list[str]) -> str:
    cleaned = [t.strip() for t in tags if t.strip()]
    return ",".join(dict.fromkeys(cleaned))


# --- Исполнение ---

NOTE_VISIBLE = "(scope = 'shared' OR owner_id = ?)"
EVENT_VISIBLE = NOTE_VISIBLE


async def _get_note(db, note_id: int, user_id: int):
    cursor = await db.execute(
        f"SELECT * FROM notes WHERE id = ? AND {NOTE_VISIBLE}", (note_id, user_id))
    row = await cursor.fetchone()
    if row is None:
        raise ToolError(f"Заметка id={note_id} не найдена или недоступна")
    return row


async def _get_event(db, event_id: int, user_id: int):
    cursor = await db.execute(
        f"SELECT * FROM events WHERE id = ? AND {EVENT_VISIBLE}", (event_id, user_id))
    row = await cursor.fetchone()
    if row is None:
        raise ToolError(f"Событие id={event_id} не найдено или недоступно")
    return row


def _note_brief(row) -> dict:
    return {
        "id": row["id"], "title": row["title"], "scope": row["scope"],
        "tags": [t for t in row["tags"].split(",") if t],
        "updated_at": row["updated_at"],
    }


def _event_brief(row) -> dict:
    return {
        "id": row["id"], "title": row["title"], "scope": row["scope"],
        "starts_at": row["starts_at"], "ends_at": row["ends_at"],
        "all_day": bool(row["all_day"]), "location": row["location"],
    }


def _fmt_event_date(starts_at: str) -> str:
    try:
        return datetime.fromisoformat(starts_at).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return starts_at


async def preview_destructive(user: dict, name: str, args: dict) -> str:
    """Проверить объект и вернуть описание действия для кнопки подтверждения."""
    async with get_connection() as db:
        if name == "notes_delete":
            row = await _get_note(db, _req_int(args, "id"), user["id"])
            return f"удалить заметку «{row['title']}»"
        if name == "notes_update":
            row = await _get_note(db, _req_int(args, "id"), user["id"])
            return f"перезаписать заметку «{row['title']}»"
        if name == "calendar_delete":
            row = await _get_event(db, _req_int(args, "id"), user["id"])
            return f"удалить событие «{row['title']}»"
    raise ToolError(f"Неизвестное деструктивное действие: {name}")


async def execute_tool(user: dict, name: str, args: dict, ip: str | None) -> tuple[dict, str]:
    """Выполнить инструмент, вернуть (результат для модели, подпись плашки для UI)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Неизвестный инструмент: {name}")
    if not isinstance(args, dict):
        raise ToolError("Аргументы инструмента должны быть объектом JSON")

    async with get_connection() as db:
        result, label, object_type, object_id = await handler(db, user, args)
        await write_audit(db, user_id=user["id"], action="llm_tool_call",
                          object_type=object_type, object_id=object_id,
                          details=f"tool={name}", ip=ip)
    return result, label


async def _notes_search(db, user, args):
    conditions = [NOTE_VISIBLE]
    params: list = [user["id"]]
    scope = _scope(args, "all", allow_all=True)
    if scope != "all":
        conditions.append("scope = ?")
        params.append(scope)
    query = _str(args, "query").strip()
    if query:
        conditions.append("(title LIKE ? OR body LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    for tag in (_tags(args) or []):
        if tag.strip():
            conditions.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag.strip()},%")
    cursor = await db.execute(
        "SELECT * FROM notes WHERE " + " AND ".join(conditions) +
        f" ORDER BY updated_at DESC LIMIT {SEARCH_LIMIT + 1}", params)
    rows = await cursor.fetchall()
    truncated = len(rows) > SEARCH_LIMIT
    rows = rows[:SEARCH_LIMIT]
    result = {"notes": [_note_brief(r) for r in rows]}
    if truncated:
        result["note"] = f"Показаны первые {SEARCH_LIMIT}, уточните запрос"
    return result, f"поиск по заметкам: найдено {len(rows)}", "note", None


async def _notes_get(db, user, args):
    row = await _get_note(db, _req_int(args, "id"), user["id"])
    result = _note_brief(row)
    result["text"] = row["body"]
    return result, f"прочитана заметка «{row['title']}»", "note", str(row["id"])


async def _notes_create(db, user, args):
    title = _str(args, "title").strip()
    if not title:
        raise ToolError("Заголовок не может быть пустым")
    now = utcnow_iso()
    cursor = await db.execute(
        "INSERT INTO notes (owner_id, scope, title, body, tags, created_at, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], _scope(args, "personal"), title, _str(args, "text"),
         _tags_to_str(_tags(args) or []), now, now, user["id"]),
    )
    await db.commit()
    return ({"id": cursor.lastrowid, "title": title, "status": "created"},
            f"создана заметка «{title}»", "note", str(cursor.lastrowid))


async def _notes_update(db, user, args):
    note_id = _req_int(args, "id")
    row = await _get_note(db, note_id, user["id"])
    scope = args.get("scope")
    if scope is not None and scope != row["scope"] and row["owner_id"] != user["id"]:
        raise ToolError("Менять область может только автор заметки")
    title = row["title"] if "title" not in args else _str(args, "title").strip()
    if not title:
        raise ToolError("Заголовок не может быть пустым")
    tags = _tags(args)
    await db.execute(
        "UPDATE notes SET title = ?, body = ?, tags = ?, scope = ?, updated_at = ?, updated_by = ? "
        "WHERE id = ?",
        (title,
         row["body"] if "text" not in args else _str(args, "text"),
         row["tags"] if tags is None else _tags_to_str(tags),
         row["scope"] if scope is None else _scope(args, row["scope"]),
         utcnow_iso(), user["id"], note_id),
    )
    await db.commit()
    return ({"id": note_id, "title": title, "status": "updated"},
            f"обновлена заметка «{title}»", "note", str(note_id))


async def _notes_delete(db, user, args):
    note_id = _req_int(args, "id")
    row = await _get_note(db, note_id, user["id"])
    await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    await db.commit()
    return ({"id": note_id, "status": "deleted"},
            f"удалена заметка «{row['title']}»", "note", str(note_id))


async def _calendar_list(db, user, args):
    conditions = [EVENT_VISIBLE]
    params: list = [user["id"]]
    scope = _scope(args, "all", allow_all=True)
    if scope != "all":
        conditions.append("scope = ?")
        params.append(scope)
    date_from = _str(args, "date_from").strip()
    if date_from:
        conditions.append("ends_at >= ?")
        params.append(_check_iso(date_from, "date_from"))
    date_to = _str(args, "date_to").strip()
    if date_to:
        conditions.append("starts_at <= ?")
        params.append(_check_iso(date_to, "date_to"))
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + " AND ".join(conditions) + " ORDER BY starts_at", params)
    rows = await cursor.fetchall()
    return ({"events": [_event_brief(r) for r in rows]},
            f"просмотр календаря: событий {len(rows)}", "event", None)


async def _calendar_create(db, user, args):
    title = _str(args, "title").strip()
    if not title:
        raise ToolError("Название не может быть пустым")
    starts_at = _check_iso(_str(args, "starts_at"), "starts_at")
    ends_at = _check_iso(_str(args, "ends_at"), "ends_at")
    if datetime.fromisoformat(ends_at) < datetime.fromisoformat(starts_at):
        raise ToolError("Окончание раньше начала")
    now = utcnow_iso()
    cursor = await db.execute(
        "INSERT INTO events (owner_id, scope, title, description, location, "
        "starts_at, ends_at, all_day, created_at, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], _scope(args, "personal"), title, _str(args, "description"),
         _str(args, "location"), starts_at, ends_at, int(bool(args.get("all_day"))),
         now, now, user["id"]),
    )
    await db.commit()
    return ({"id": cursor.lastrowid, "title": title, "status": "created"},
            f"создано событие «{title}» ({_fmt_event_date(starts_at)})",
            "event", str(cursor.lastrowid))


async def _calendar_update(db, user, args):
    event_id = _req_int(args, "id")
    row = await _get_event(db, event_id, user["id"])
    scope = args.get("scope")
    if scope is not None and scope != row["scope"] and row["owner_id"] != user["id"]:
        raise ToolError("Менять область может только автор события")
    title = row["title"] if "title" not in args else _str(args, "title").strip()
    if not title:
        raise ToolError("Название не может быть пустым")
    starts_at = row["starts_at"] if "starts_at" not in args \
        else _check_iso(_str(args, "starts_at"), "starts_at")
    ends_at = row["ends_at"] if "ends_at" not in args \
        else _check_iso(_str(args, "ends_at"), "ends_at")
    if datetime.fromisoformat(ends_at) < datetime.fromisoformat(starts_at):
        raise ToolError("Окончание раньше начала")
    await db.execute(
        "UPDATE events SET title = ?, description = ?, location = ?, starts_at = ?, "
        "ends_at = ?, all_day = ?, scope = ?, updated_at = ?, updated_by = ? WHERE id = ?",
        (title,
         row["description"] if "description" not in args else _str(args, "description"),
         row["location"] if "location" not in args else _str(args, "location"),
         starts_at, ends_at,
         row["all_day"] if "all_day" not in args else int(bool(args["all_day"])),
         row["scope"] if scope is None else _scope(args, row["scope"]),
         utcnow_iso(), user["id"], event_id),
    )
    await db.commit()
    return ({"id": event_id, "title": title, "status": "updated"},
            f"обновлено событие «{title}» ({_fmt_event_date(starts_at)})",
            "event", str(event_id))


async def _calendar_delete(db, user, args):
    event_id = _req_int(args, "id")
    row = await _get_event(db, event_id, user["id"])
    await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    await db.commit()
    return ({"id": event_id, "status": "deleted"},
            f"удалено событие «{row['title']}»", "event", str(event_id))


async def _get_current_datetime(db, user, args):
    now = datetime.now(APP_TZ)
    result = {
        "datetime": now.isoformat(timespec="seconds"),
        "weekday": _WEEKDAYS_RU[now.weekday()],
        "timezone": "Europe/Moscow",
    }
    return result, "запрошены текущие дата и время", None, None


_HANDLERS = {
    "notes_search": _notes_search,
    "notes_get": _notes_get,
    "notes_create": _notes_create,
    "notes_update": _notes_update,
    "notes_delete": _notes_delete,
    "calendar_list": _calendar_list,
    "calendar_create": _calendar_create,
    "calendar_update": _calendar_update,
    "calendar_delete": _calendar_delete,
    "get_current_datetime": _get_current_datetime,
}


# --- Fallback: JSON-блок вместо структурных tool_calls (§7) ---

_FALLBACK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```|(\{.*\})", re.DOTALL)


def parse_fallback_tool_calls(text: str) -> list[dict] | None:
    """Достать вызов инструмента из текста ответа (устойчивость к старым сборкам llama.cpp).

    Возвращает список в формате OpenAI tool_calls или None.
    """
    match = _FALLBACK_RE.search(text.strip())
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("tool")
    args = data.get("arguments") or data.get("parameters") or {}
    if not isinstance(name, str) or name not in _HANDLERS or not isinstance(args, dict):
        return None
    return [{
        "id": f"fallback_{secrets.token_hex(4)}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }]
