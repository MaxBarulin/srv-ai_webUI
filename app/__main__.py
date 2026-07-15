"""Запуск приложения: `python -m app`.

Хост и порт берутся из конфигурации (.env: APP_HOST, APP_PORT), поэтому в
systemd-юните не нужно подставлять переменные окружения в ExecStart.
"""
from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
