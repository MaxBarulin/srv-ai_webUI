#!/usr/bin/env bash
# Резервное копирование srv-ai webUI (§12): консистентная копия SQLite + .env.
# Использование: ./backup.sh [каталог_назначения]
# По умолчанию бэкапы кладутся в $DATA_DIR/backups.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

# Каталог данных из .env (по умолчанию ./data)
DATA_DIR="$(grep -E '^DATA_DIR=' .env 2>/dev/null | cut -d= -f2- | tr -d '"'"'"'' || true)"
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
DB_FILE="$DATA_DIR/srv-ai-ui.db"

DEST="${1:-$DATA_DIR/backups}"
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Консистентный снимок БД (безопасно при работающем сервисе, WAL).
# Используем venv Python (модуль sqlite3 из stdlib) — не зависим от наличия
# консольной утилиты sqlite3. Если venv нет — пробуем sqlite3, затем python3.
PYTHON="$APP_DIR/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"

if [ -f "$DB_FILE" ]; then
    if [ -n "$PYTHON" ]; then
        "$PYTHON" - "$DB_FILE" "$TMP/srv-ai-ui.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
PY
    elif command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$DB_FILE" ".backup '$TMP/srv-ai-ui.db'"
    else
        echo "ОШИБКА: не найден ни python3, ни sqlite3 для снимка БД" >&2
        exit 1
    fi
else
    echo "ВНИМАНИЕ: файл БД не найден: $DB_FILE" >&2
fi

# .env — конфигурация (содержит настройки, храните бэкапы в защищённом месте)
[ -f "$APP_DIR/.env" ] && cp "$APP_DIR/.env" "$TMP/.env"

ARCHIVE="$DEST/srv-ai-ui-backup-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$TMP" .
echo "Бэкап создан: $ARCHIVE"

# Ротация: оставить последние 14 архивов
ls -1t "$DEST"/srv-ai-ui-backup-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
