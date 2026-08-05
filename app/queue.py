"""Серверная очередь запросов к LLM (§15).

Модель на CPU обрабатывает запросы последовательно, поэтому у бэкенда — одна
общая FIFO-очередь. Ожидающий клиент получает честный статус «В очереди: N»
до начала стриминга. Таймаут ожидания — из конфига.

Использование в SSE-обработчике:

    ticket = llm_queue.enqueue()
    try:
        async for position in ticket.wait_turn():
            yield sse("queued", {"position": position})   # пока ждём
        ...  # наша очередь — работаем с LLM
    finally:
        ticket.release()
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

POLL_INTERVAL = 0.3       # как часто проверять/обновлять позицию, сек
# Максимальное ожидание места. Ориентир — не «сколько не жалко ждать», а
# сколько может длиться ОДИН ответ: на CPU с длинным размышлением это 3–4
# минуты. Прежние 300 с означали, что третий в очереди получал «превышено
# время ожидания» просто потому, что впереди два обычных длинных ответа.
QUEUE_WAIT_TIMEOUT = 900
# Как часто повторять позицию, даже если она не изменилась. Без этого поток
# молчит всё время ожидания, и обратный прокси вправе закрыть его как простой.
HEARTBEAT_EVERY = 15.0    # сек


class QueueTimeout(Exception):
    """Не дождались своей очереди за отведённое время."""


class Ticket:
    __slots__ = ("_queue", "_started", "_released")

    def __init__(self, queue: "LLMQueue") -> None:
        self._queue = queue
        self._started = False
        self._released = False

    async def wait_turn(self, timeout: float = QUEUE_WAIT_TIMEOUT) -> AsyncIterator[int]:
        """Ждать своей очереди, отдавая позицию (число впереди) при каждом опросе.

        Ничего не отдаёт, если очередь свободна и запрос обрабатывается сразу.
        """
        elapsed = 0.0
        since_beat = 0.0
        last_reported: int | None = None
        while not self._queue._can_start(self):
            position = self._queue._position(self)
            if position != last_reported or since_beat >= HEARTBEAT_EVERY:
                yield position
                last_reported = position
                since_beat = 0.0
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            since_beat += POLL_INTERVAL
            if elapsed >= timeout:
                raise QueueTimeout(
                    "Превышено время ожидания в очереди к модели, попробуйте позже")
        self._queue._start(self)
        self._started = True

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._queue._remove(self)


class LLMQueue:
    def __init__(self) -> None:
        self._waiters: deque[Ticket] = deque()
        self._active: Ticket | None = None

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    def enqueue(self) -> Ticket:
        ticket = Ticket(self)
        self._waiters.append(ticket)
        return ticket

    def _can_start(self, ticket: Ticket) -> bool:
        return self._active is None and bool(self._waiters) and self._waiters[0] is ticket

    def _position(self, ticket: Ticket) -> int:
        # Сколько запросов впереди: ждущие раньше + текущий обрабатываемый
        try:
            idx = self._waiters.index(ticket)
        except ValueError:
            return 0
        return idx + (1 if self._active is not None else 0)

    def _start(self, ticket: Ticket) -> None:
        # Вызывается только когда _can_start вернул True (тикет — во главе очереди)
        self._waiters.popleft()
        self._active = ticket

    def _remove(self, ticket: Ticket) -> None:
        if self._active is ticket:
            self._active = None
        else:
            try:
                self._waiters.remove(ticket)
            except ValueError:
                pass


llm_queue = LLMQueue()
