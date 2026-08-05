"""Вычислитель формул (§17): белый список и пределы расхода ресурсов.

Формулы приходят от модели, а её текст пишет в том числе пользователь — то
есть это недоверенный ввод, исполняемый на сервере. Инъекцию закрывает белый
список узлов AST, а вот исчерпание ресурсов страшнее: uvicorn у нас один на
всех, и одно выражение, считающееся минуту, останавливает завод.
"""
from __future__ import annotations

import time

import pytest

from app.calc import CalcError, evaluate, run_steps


# --- Белый список: ничего, кроме чисел и разрешённых функций ---

@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "(1).__class__.__mro__",
    "lambda: 1",
    "[1, 2, 3]",
    "{'a': 1}",
    "'строка'",
    "x if x else y",
    "1 < 2",
    "a and b",
])
def test_rejects_everything_but_arithmetic(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


def test_rejects_unknown_names_and_functions():
    with pytest.raises(CalcError, match="Неизвестная величина"):
        evaluate("нет_такой + 1")
    with pytest.raises(CalcError, match="Неизвестная функция"):
        evaluate("eval(1)")


def test_arithmetic_still_works():
    assert evaluate("(2 + 3) * 4 / 2") == 10
    assert evaluate("sqrt(16) + log(e)") == 5
    assert evaluate("round_half_up(2.5, 0)") == 3      # «как в школе», не к чётному
    assert evaluate("T * 1.15", {"T": 2}) == pytest.approx(2.3)


# --- Расход ресурсов ---

def _under(expr: str, seconds: float) -> None:
    start = time.monotonic()
    with pytest.raises(CalcError):
        evaluate(expr)
    spent = time.monotonic() - start
    assert spent < seconds, f"{expr}: считалось {spent:.1f}с"


def test_exponent_tower_rejected():
    _under("9**9**9", 1.0)


def test_nested_powers_rejected_before_they_grow():
    """Ограничения одного показателя мало: в каждой скобке показатель равен 64
    и проверку проходит, а основание тем временем растёт до миллионов знаков.
    Раньше четыре скобки считались 14 секунд, пять — не считались вовсе."""
    _under("(((10**64)**64)**64)**64", 1.0)
    _under("((((10**64)**64)**64)**64)**64", 1.0)


def test_huge_rounding_precision_rejected():
    """round_half_up(1, 100000000) — это 10**100000000, стомиллионзначное
    число: белый список тут ни при чём, взрыв внутри разрешённой функции."""
    _under("round_half_up(1, 100000000)", 1.0)


def test_intermediate_overflow_named_clearly():
    with pytest.raises(CalcError, match="промежуточный результат"):
        evaluate("(10**64)**64")


def test_long_expression_rejected():
    with pytest.raises(CalcError, match="длиннее"):
        evaluate("1+" * 300 + "1")


# --- Цепочка шагов ---

def test_run_steps_chains_values():
    trace = run_steps([
        {"name": "T_осн", "expr": "L / v", "unit": "мин"},
        {"name": "T_шт", "expr": "T_осн * 1.15", "unit": "мин"},
    ], given={"L": 100, "v": 50})
    assert [s["name"] for s in trace] == ["T_осн", "T_шт"]
    assert trace[-1]["value"] == pytest.approx(2.3)


def test_run_steps_rejects_redefinition_and_overrun():
    with pytest.raises(CalcError, match="уже определена"):
        run_steps([{"name": "a", "expr": "1"}, {"name": "a", "expr": "2"}])
    with pytest.raises(CalcError, match="шагов"):
        run_steps([{"name": f"s{i}", "expr": "1"} for i in range(50)])
