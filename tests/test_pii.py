"""PII filter tests (§13): регулярки, Луна, белый список, NER, интеграция."""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import httpx
import pytest

from app import llm as llm_module
from app import pii as pii_module
from app.config import settings
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app
from tests.test_chat import _parse_sse

PASS = "pii-user-pass-01"


def _clear_wl_cache():
    clear = getattr(pii_module._whitelist, "cache_clear", None)
    if clear:
        clear()


@pytest.fixture(autouse=True)
def enable_pii(monkeypatch):
    monkeypatch.setattr(pii_module, "settings",
                        replace(settings, pii_filter=True, pii_whitelist_file=""))
    _clear_wl_cache()
    yield
    _clear_wl_cache()


def mask(text):
    return pii_module.mask_text(text)


def test_filter_disabled_passes_through(monkeypatch):
    monkeypatch.setattr(pii_module, "settings", replace(settings, pii_filter=False))
    r = mask("email: test@example.com")
    assert r.text == "email: test@example.com"
    assert r.total == 0


def test_mask_email():
    r = mask("Пишите на ivanov@zavod.ru пожалуйста")
    assert "[EMAIL]" in r.text
    assert "ivanov@zavod.ru" not in r.text
    assert r.counts["EMAIL"] == 1


def test_mask_phone():
    for phone in ("+7 900 123-45-67", "8(900)123-45-67", "89001234567"):
        r = mask(f"звоните {phone}")
        assert "[ТЕЛЕФОН]" in r.text, phone


def test_mask_snils():
    r = mask("СНИЛС 112-233-445 95")
    assert "[СНИЛС]" in r.text


def test_mask_card_luhn_only():
    # Валидная по Луну карта маскируется
    r = mask("карта 4111 1111 1111 1111 оплата")
    assert "[КАРТА]" in r.text
    # Невалидная по Луну последовательность не считается картой
    r2 = mask("номер 1234 5678 9012 3456 просто")
    assert "[КАРТА]" not in r2.text


def test_mask_account_20_digits():
    r = mask("счёт 40702810900000012345 в банке")
    assert "[СЧЁТ]" in r.text


def test_mask_inn():
    r = mask("ИНН 7707083893 организации")
    assert "[ИНН]" in r.text


def test_whitelist_excludes(monkeypatch, tmp_path):
    wl = tmp_path / "wl.txt"
    wl.write_text("Иванов\n# комментарий\nzavod.ru\n", encoding="utf-8")
    monkeypatch.setattr(pii_module, "settings",
                        replace(settings, pii_filter=True, pii_whitelist_file=str(wl)))
    pii_module._whitelist.cache_clear()
    # email в белом списке по домену не сработает как точное совпадение — проверим ФИО ниже
    assert "zavod.ru" in pii_module._whitelist()


def test_name_masks_full_fio():
    r = mask("Приказ подписал Иванов Иван Иванович, начальник цеха.")
    assert "[ФИО]" in r.text
    assert "Иванов" not in r.text
    assert r.counts.get("ФИО", 0) == 1


def test_name_masks_initials():
    r = mask("Ответственный Петров П.П., согласовано с Сидоровой А.И.")
    assert r.counts.get("ФИО", 0) == 2
    assert "Петров" not in r.text and "Сидоровой" not in r.text


def test_name_masks_first_plus_surname():
    r = mask("Сергей Кузнецов и Мария Петрова провели контроль.")
    assert r.counts.get("ФИО", 0) == 2


def test_name_masks_patronymic_pair():
    r = mask("Бригадир Ольга Васильевна утвердила график.")
    assert "[ФИО]" in r.text
    assert "Ольга" not in r.text


@pytest.mark.parametrize("text", [
    "Поручить Иванову Ивану Ивановичу подготовку.",   # дательный
    "Согласовано с Петровым Петром Петровичем.",       # творительный
    "у Сидоровой Анны Ивановны есть замечания",        # родительный, женское
    "передать Марии Сергеевне документы",              # дательный, женское
    "Анна Никитична утвердила график.",                # отчество на -ична (список)
])
def test_name_masks_declensions(text):
    assert mask(text).counts.get("ФИО", 0) == 1


@pytest.mark.parametrize("text", [
    "Симметрична относительно оси заготовка.",
    "Аналогична предыдущей методике конструкция.",
    "Идентична по составу партия.",
    "Практична и надёжна оснастка.",
    "Типична для серии деталь.",
    "Виновна ли сторона — решает суд.",
])
def test_no_false_positive_on_short_adjectives(text):
    # заглавные краткие прилагательные на -ична/-овна — не ФИО
    assert mask(text).counts.get("ФИО", 0) == 0


def test_name_masks_weak_patronymic_with_first_name():
    r = mask("Мастер Иван Ильич принял смену.")
    assert "[ФИО]" in r.text
    assert "Ильич" not in r.text


def test_name_masks_hyphenated_surname_initials():
    r = mask("Согласовал Римский-Корсаков Н.А.")
    assert "[ФИО]" in r.text
    assert "Корсаков" not in r.text


# --- Отсутствие ложных срабатываний на технических терминах ---

@pytest.mark.parametrize("text", [
    "Марка стали 09Г2С по ГОСТ 19281 применяется для корпусов.",
    "Токарный станок с ЧПУ, режим Черновая обработка.",
    "Раздел Механообработка. Пункт Сварка. Контроль по Шаблону.",
    "Приёмка партии Проката. Аттестация Сварщиков в четверг.",
    "Расчёт по методике Стьюдента, критерий Фишера.",
    "Совещание по Нормированию. Повестка: План на квартал.",
])
def test_no_false_positive_on_terms(text):
    r = mask(text)
    assert r.counts.get("ФИО", 0) == 0
    assert "[ФИО]" not in r.text


@pytest.mark.parametrize("text", [
    "Начальник участка Николай проверил партию.",   # одиночное имя без фамилии
    "Инженер Роман составил отчёт.",                 # имя-омоним без фамилии
])
def test_lone_first_name_not_masked(text):
    # одиночное имя без второго сигнала не маскируем (защита от омонимов)
    assert mask(text).counts.get("ФИО", 0) == 0


@pytest.mark.parametrize("text", [
    "Подпись ______________ И.О. Фамилия",   # строка подписи бланка
    "(подпись)          Фамилия И.О.",
])
def test_form_placeholder_not_masked(text):
    # шаблон бланка «И.О. Фамилия» — не ПДн, маскировать не нужно
    assert mask(text).counts.get("ФИО", 0) == 0


def test_real_document_names_and_no_false_positives():
    # Фрагмент реального стандарта: аббревиатуры/заголовки не маскируются, ФИО — да
    text = ("УТВЕРЖДАЮ Врио генерального директора А.В. Быстров. "
            "Нормы устанавливаются УТПП и УОТиСР по ГОСТ 3.1109 в ИИС «Адмирал». "
            "Согласовал начальник ОНУиАТПКиС Р.С. Аверкиев.")
    r = mask(text)
    assert r.counts.get("ФИО", 0) == 2               # Быстров, Аверкиев
    assert "Быстров" not in r.text and "Аверкиев" not in r.text
    for term in ("УТПП", "УОТиСР", "ГОСТ", "Адмирал", "ОНУиАТПКиС", "Врио"):
        assert term in r.text                        # термины не задеты


def test_name_whitelist_keeps_name(monkeypatch):
    import app.pii as p
    monkeypatch.setattr(p, "_whitelist", lambda: {"иванов иван иванович"})
    r = mask("Документ утвердил Иванов Иван Иванович.")
    assert "Иванов Иван Иванович" in r.text
    assert "[ФИО]" not in r.text


# --- Интеграция с чатом ---

@pytest.fixture()
def pii_chat(client, make_user, monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))
    monkeypatch.setattr("app.routers.chat.mask_text", pii_module.mask_text)
    make_user("pii-user", PASS)
    login_as(client, "pii-user", PASS)
    yield
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM chats")
        conn.commit()
    finally:
        conn.close()


def test_chat_masks_before_llm_and_storage(client, pii_chat, monkeypatch):
    captured = []
    orig = llm_module.stream_chat

    def spy(messages, tools=None, **kwargs):
        captured.append(messages)
        return orig(messages, tools=tools)

    monkeypatch.setattr("app.routers.chat.stream_chat", spy)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/messages", json={
        "content": "Мой email ivanov@zavod.ru и телефон +7 900 123-45-67",
        "use_tools": False,
    })
    events = _parse_sse(r.text)

    # Плашка ПДн отдана в UI
    masked = [d for e, d in events if e == "pii_masked"]
    assert masked and masked[0]["count"] >= 2

    # В LLM ушёл очищенный текст
    user_msg = captured[0][-1]["content"]
    assert "ivanov@zavod.ru" not in user_msg
    assert "[EMAIL]" in user_msg

    # В историю сохранён очищенный текст (исходные ПДн не попадают в БД)
    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    stored = [m["content"] for m in msgs if m["role"] == "user"][0]
    assert "ivanov@zavod.ru" not in stored
    assert "+7 900 123-45-67" not in stored
