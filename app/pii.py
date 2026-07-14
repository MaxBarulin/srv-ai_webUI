"""Фильтр персональных данных (§13, реализация п. 4.5 Положения).

Двухуровневая предобработка без дополнительного LLM-инференса:
  1. Регулярные выражения для структурированных ПДн (телефоны, e-mail, СНИЛС,
     ИНН, паспорт, карты с проверкой по Луну, счета) → типизированные плейсхолдеры.
  2. NER для ФИО (Natasha/Slovnet, CPU, офлайн) — сущности PER → [ФИО].

Маскирование выполняется ДО отправки в LLM и ДО записи в историю (§13).
Белый список (PII_WHITELIST_FILE) исключает термины/фамилии из маскирования.
Изображения фильтром не обрабатываются (известное исключение vision-пути).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import settings

# --- Регулярные выражения структурированных ПДн ---

# Телефон РФ: +7/8 и 10 цифр с любыми разделителями
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# СНИЛС: 11 цифр в формате XXX-XXX-XXX YY
_SNILS = re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s\-]\d{2}(?!\d)")
# Паспорт РФ: серия 2+2 цифры + номер 6 цифр, с разделителями (иначе неотличим
# от 10-значного ИНН — контигуальные 10 цифр относим к ИНН)
_PASSPORT = re.compile(r"(?<!\d)\d{2}\s\d{2}\s\d{6}(?!\d)")
# Кандидат в номер карты/счёта: последовательности из 13-20 цифр (с разделителями)
_DIGITS_SEQ = re.compile(r"(?<!\d)(?:\d[\s\-]?){13,20}\d(?!\d)")
# ИНН: 10 (юр) или 12 (физ) цифр
_INN = re.compile(r"(?<!\d)\d{10}(?:\d{2})?(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class PIIResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@lru_cache(maxsize=1)
def _whitelist() -> set[str]:
    path = settings.pii_whitelist_file
    if not path:
        return set()
    p = Path(path)
    if not p.is_absolute():
        from app.config import BASE_DIR
        p = BASE_DIR / p
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")}


# --- NER (ленивая загрузка, graceful degradation) ---

_ner_state: dict = {"loaded": False, "ok": False, "seg": None, "tagger": None}


def _load_ner() -> bool:
    if _ner_state["loaded"]:
        return _ner_state["ok"]
    _ner_state["loaded"] = True
    try:
        from natasha import NewsEmbedding, NewsNERTagger, Segmenter
        _ner_state["seg"] = Segmenter()
        _ner_state["tagger"] = NewsNERTagger(NewsEmbedding())
        _ner_state["ok"] = True
    except Exception:  # noqa: BLE001 — библиотека/модель недоступна → работаем без NER
        _ner_state["ok"] = False
    return _ner_state["ok"]


def _mask_names(text: str, counts: dict[str, int]) -> str:
    if not _load_ner():
        return text
    from natasha import Doc

    doc = Doc(text)
    doc.segment(_ner_state["seg"])
    doc.tag_ner(_ner_state["tagger"])
    whitelist = _whitelist()
    # Заменяем справа налево, чтобы не сбить смещения
    spans = sorted((s for s in doc.spans if s.type == "PER"),
                   key=lambda s: s.start, reverse=True)
    for span in spans:
        if span.text.lower() in whitelist:
            continue
        text = text[:span.start] + "[ФИО]" + text[span.stop:]
        counts["ФИО"] = counts.get("ФИО", 0) + 1
    return text


# --- Основной проход ---

def _sub_count(pattern: re.Pattern, placeholder: str, label: str,
               text: str, counts: dict[str, int], *, validate=None) -> str:
    def repl(m: re.Match) -> str:
        raw = m.group(0)
        if validate is not None:
            digits = re.sub(r"\D", "", raw)
            if not validate(digits):
                return raw
        if raw.strip().lower() in _whitelist():
            return raw
        counts[label] = counts.get(label, 0) + 1
        return placeholder
    return pattern.sub(repl, text)


def mask_text(text: str) -> PIIResult:
    """Замаскировать ПДн в тексте. Возвращает очищенный текст и счётчики по типам."""
    if not text or not settings.pii_filter:
        return PIIResult(text=text, counts={})

    counts: dict[str, int] = {}
    # Порядок важен: длинные/специфичные паттерны раньше общих числовых
    text = _sub_count(_EMAIL, "[EMAIL]", "EMAIL", text, counts)
    text = _sub_count(_PHONE, "[ТЕЛЕФОН]", "ТЕЛЕФОН", text, counts)
    text = _sub_count(_SNILS, "[СНИЛС]", "СНИЛС", text, counts)
    # Карты и счета: длинные последовательности; карту подтверждаем по Луну
    text = _sub_count(_DIGITS_SEQ, "[КАРТА]", "КАРТА", text, counts,
                      validate=lambda d: 13 <= len(d) <= 19 and _luhn_ok(d))
    text = _sub_count(_DIGITS_SEQ, "[СЧЁТ]", "СЧЁТ", text, counts,
                      validate=lambda d: len(d) in (20, 22, 25))
    text = _sub_count(_PASSPORT, "[ПАСПОРТ]", "ПАСПОРТ", text, counts)
    text = _sub_count(_INN, "[ИНН]", "ИНН", text, counts)
    text = _mask_names(text, counts)
    return PIIResult(text=text, counts=counts)
