"""PDF, ожидающие растеризации до отправки сообщения (§16).

Растеризация страниц в картинки — самое долгое, что делает разбор: двадцать
страниц это полминуты, сотня — минуты. Держать на этом закрепление файла с
заблокированной кнопкой «Отправить» нельзя, поэтому при закреплении PDF только
проверяется и считаются страницы, а рисуется он уже внутри хода, после
«Отправить» — там видно, чем занят сервер, и работает «Остановить».

Между этими двумя моментами файл надо где-то держать. Держим в памяти процесса
по токену: гонять мегабайты обратно в браузер и потом снова на сервер вдвое
дороже, а писать на диск — заводить уборку файлов и новый путь утечки. Процесс
у нас один, так что словаря достаточно.

Срок жизни короткий: не отправил за полчаса — забыли. При перезапуске службы
незавершённые вложения теряются, это ожидаемо.
"""
from __future__ import annotations

import secrets
import time

TTL_SECONDS = 1800          # полчаса на «приложил и передумал»
MAX_TOTAL_BYTES = 256 * 1024 * 1024   # потолок на всё хранилище разом

# token -> (user_id, filename, данные, срок)
_PENDING: dict[str, tuple[int, str, bytes, float]] = {}


def _evict() -> None:
    now = time.monotonic()
    for token in [t for t, (_, _, _, exp) in _PENDING.items() if exp < now]:
        del _PENDING[token]


def _total_bytes() -> int:
    return sum(len(data) for _, _, data, _ in _PENDING.values())


def put(user_id: int, filename: str, data: bytes) -> str:
    _evict()
    # Переполнение вытесняет самые старые: лучше потерять давно забытый файл,
    # чем отказать тому, кто прикладывает прямо сейчас.
    while _total_bytes() + len(data) > MAX_TOTAL_BYTES and _PENDING:
        oldest = min(_PENDING, key=lambda t: _PENDING[t][3])
        del _PENDING[oldest]
    token = secrets.token_urlsafe(16)
    _PENDING[token] = (user_id, filename, data, time.monotonic() + TTL_SECONDS)
    return token


def take(token: str, user_id: int) -> bytes | None:
    """Забрать файл (одноразово). Чужой токен — None, как и просроченный."""
    _evict()
    entry = _PENDING.get(token)
    if entry is None or entry[0] != user_id:
        return None
    del _PENDING[token]
    return entry[2]
