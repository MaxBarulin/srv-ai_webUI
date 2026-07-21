"""SQLite access layer — aiosqlite, WAL mode, schema per TZ §10."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import aiosqlite

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT NOT NULL UNIQUE,
    pass_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    reasoning TEXT,
    tool_calls_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    scope TEXT NOT NULL CHECK (scope IN ('personal', 'shared')),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes(owner_id);
CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes(scope);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    scope TEXT NOT NULL CHECK (scope IN ('personal', 'shared')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_events_owner ON events(owner_id);
CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- §15: библиотека специализаций (системные промпты, редактируются админом)
CREATE TABLE IF NOT EXISTS specializations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- §15: обратная связь по ответам модели (задел под датасет LoRA)
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    rating INTEGER NOT NULL,
    comment TEXT,
    specialization TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);

-- §15: кликабельные примеры запросов в пустом чате (редактируются админом)
CREATE TABLE IF NOT EXISTS chat_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
"""

# Миграции для существующих БД: добавление колонок (ALTER не поддерживает IF NOT EXISTS).
_COLUMN_MIGRATIONS = [
    ("chats", "specialization_id", "INTEGER"),
    ("chats", "custom_prompt", "TEXT NOT NULL DEFAULT ''"),
    ("users", "font_scale", "INTEGER NOT NULL DEFAULT 1"),
    # Пер-чатовые тумблеры «Заметки/Календарь» и «Размышления» (§15)
    ("chats", "use_tools", "INTEGER NOT NULL DEFAULT 1"),
    ("chats", "enable_thinking", "INTEGER NOT NULL DEFAULT 1"),
    # Статистика генерации ответа (токены, скорость, контекст) — под ответом
    ("messages", "stats_json", "TEXT"),
]

DEFAULT_SPECIALIZATIONS = [
    ("Общий", ""),
    ("Механообработка",
     "Ты — инженер-технолог по механообработке. Помогай с режимами резания, "
     "нормированием операций, выбором инструмента и приспособлений."),
    ("Сварка",
     "Ты — инженер-технолог по сварочному производству. Помогай с выбором способов "
     "сварки, режимов, контроля качества сварных соединений и нормативной документации."),
    ("Литьё",
     "Ты — инженер-технолог литейного производства. Помогай с технологией литья, "
     "выбором сплавов, расчётом литниковых систем и устранением дефектов отливок."),
]

DEFAULT_EXAMPLES = [
    "Создай на завтра на 10:00 совещание по нормированию и заметку с повесткой",
    "Что у меня запланировано на этой неделе?",
    "Составь чек-лист входного контроля материалов",
    "Найди мои заметки по теме сварки",
]


async def _run_column_migrations(db: aiosqlite.Connection) -> None:
    for table, column, decl in _COLUMN_MIGRATIONS:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def _seed_defaults(db: aiosqlite.Connection) -> None:
    from app.audit import utcnow_iso
    cursor = await db.execute("SELECT COUNT(*) FROM specializations")
    if (await cursor.fetchone())[0] == 0:
        now = utcnow_iso()
        for order, (name, prompt) in enumerate(DEFAULT_SPECIALIZATIONS):
            await db.execute(
                "INSERT INTO specializations (name, system_prompt, is_active, sort_order, created_at) "
                "VALUES (?, ?, 1, ?, ?)", (name, prompt, order, now))
    cursor = await db.execute("SELECT COUNT(*) FROM chat_examples")
    if (await cursor.fetchone())[0] == 0:
        for order, text in enumerate(DEFAULT_EXAMPLES):
            await db.execute(
                "INSERT INTO chat_examples (text, sort_order) VALUES (?, ?)", (text, order))


# --- Опциональное шифрование БД (SQLCipher, ключ DB_KEY в .env) ---
# Стандартный sqlite3 не шифрует: PRAGMA key он молча игнорирует, и файл
# остался бы открытым. Поэтому при заданном DB_KEY подменяем драйвер aiosqlite
# на sqlcipher3 (пакет sqlcipher3-binary) и падаем с понятной ошибкой, если
# он не установлен, — тихая деградация до нешифрованной БД недопустима.
_cipher_ready = False


def _ensure_cipher_driver() -> None:
    global _cipher_ready
    if _cipher_ready or not settings.db_key:
        return
    try:
        import sqlcipher3  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "DB_KEY задан, но пакет sqlcipher3 не установлен — БД не будет "
            "зашифрована. Установите sqlcipher3-binary (pip install "
            "sqlcipher3-binary) или уберите DB_KEY из .env.") from exc
    import aiosqlite.core
    aiosqlite.core.sqlite3 = sqlcipher3
    _cipher_ready = True


async def _apply_key(db: aiosqlite.Connection) -> None:
    if settings.db_key:
        escaped = settings.db_key.replace("'", "''")
        await db.execute(f"PRAGMA key = '{escaped}'")


async def init_db() -> None:
    _ensure_cipher_driver()
    async with aiosqlite.connect(settings.db_path) as db:
        await _apply_key(db)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        await _run_column_migrations(db)
        await _seed_defaults(db)
        await db.commit()


@contextlib.asynccontextmanager
async def get_connection() -> AsyncIterator[aiosqlite.Connection]:
    _ensure_cipher_driver()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        await _apply_key(db)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 5000")
        yield db


async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    async with get_connection() as db:
        yield db
