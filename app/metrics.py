"""In-memory метрики (§13, п. 3.7 Положения) — без содержания запросов.

Счётчики считаются с момента старта процесса (сбрасываются при перезапуске).
Хранятся только числа: количество запросов, успешных/неуспешных, суммарные
токены и время генерации для средней скорости, а также счётчики срабатываний
PII-фильтра по типам (без значений).
"""
from __future__ import annotations

from collections import Counter


class Metrics:
    def __init__(self) -> None:
        self.requests_total = 0
        self.requests_success = 0
        self.requests_failed = 0
        self._gen_tokens = 0
        self._gen_seconds = 0.0
        self.pii_counts: Counter[str] = Counter()

    def record_request(self, *, success: bool, tokens: int = 0, seconds: float = 0.0) -> None:
        self.requests_total += 1
        if success:
            self.requests_success += 1
        else:
            self.requests_failed += 1
        if tokens > 0 and seconds > 0:
            self._gen_tokens += tokens
            self._gen_seconds += seconds

    def record_pii(self, counts: dict[str, int]) -> None:
        for pii_type, n in counts.items():
            if n:
                self.pii_counts[pii_type] += n

    def snapshot(self) -> dict:
        avg_speed = round(self._gen_tokens / self._gen_seconds, 1) if self._gen_seconds else 0.0
        return {
            "requests_total": self.requests_total,
            "requests_success": self.requests_success,
            "requests_failed": self.requests_failed,
            "avg_tokens_per_sec": avg_speed,
            "pii_masked_by_type": dict(self.pii_counts),
        }


metrics = Metrics()
