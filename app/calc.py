"""Безопасный вычислитель формул для инструмента расчётов (§17).

Разбираем выражение в AST и обходим дерево по белому списку узлов. Всё, чего
в списке нет, — отказ. eval() и exec() здесь не используются ни в каком виде:
строка от модели или из справочника методик никогда не исполняется как код.

Опасность вычислителя — не столько инъекция (её закрывает белый список),
сколько исчерпание ресурсов: одно выражение вида 9**9**9 уводит процесс в
своп, а uvicorn у нас один на всех пользователей. Поэтому пределы на степень,
длину, глубину и величину чисел проверяются ДО вычисления.

Зависимостей нет — только ast и math из стандартной библиотеки.
"""
from __future__ import annotations

import ast
import math
import re

MAX_EXPR_LENGTH = 500        # символов в одной формуле
MAX_AST_DEPTH = 25           # глубина вложенности выражения
MAX_STEPS = 30               # шагов в одной цепочке расчёта
MAX_EXPONENT = 64            # показатель степени: 2**64 ещё считается, 9**9**9 — нет
MAX_ABS_VALUE = 1e100        # предел промежуточного результата
MAX_NAME_LENGTH = 40

# Имя переменной: буквы (в том числе кириллица), цифры, подчёркивание;
# начинается не с цифры. Технологу удобнее писать T_о и S_мин, чем t1 и s2.
_NAME_RE = re.compile(r"^(?!\d)\w+$", re.UNICODE)


class CalcError(ValueError):
    """Ошибка разбора или вычисления — показывается пользователю и модели."""


# --- Разрешённые функции и константы ---
#
# Только чистая математика без ввода-вывода и без доступа к объектам Python.
# round2 добавлен отдельно: округление «как в школе» (0.5 вверх), потому что
# встроенный round() округляет к чётному и на нормах времени это удивляет.

def _round_half_up(value: float, digits: int = 0) -> float:
    factor = 10 ** int(digits)
    return math.floor(abs(value) * factor + 0.5) / factor * (1 if value >= 0 else -1)


FUNCTIONS: dict[str, object] = {
    "abs": abs, "min": min, "max": max, "sum": lambda *a: sum(a),
    "round": round, "round_half_up": _round_half_up,
    "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc,
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log,
    "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "radians": math.radians, "degrees": math.degrees,
    "hypot": math.hypot, "fabs": math.fabs, "fmod": math.fmod,
}

CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}


def _depth(node: ast.AST, level: int = 0) -> int:
    if level > MAX_AST_DEPTH:
        raise CalcError("Выражение слишком глубоко вложено")
    children = list(ast.iter_child_nodes(node))
    return level if not children else max(_depth(c, level + 1) for c in children)


def _check_number(value, where: str):
    """Числовой результат в разумных пределах — иначе понятная ошибка."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalcError(f"{where}: результат не число")
    if isinstance(value, float):
        if math.isnan(value):
            raise CalcError(f"{where}: результат не определён (NaN)")
        if math.isinf(value):
            raise CalcError(f"{where}: переполнение — слишком большое число")
    if abs(value) > MAX_ABS_VALUE:
        raise CalcError(f"{where}: результат вне допустимого диапазона")
    return value


def _eval_node(node: ast.AST, names: dict[str, float]):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("Допустимы только числа")
        return node.value

    if isinstance(node, ast.Name):
        key = node.id
        if key in names:
            return names[key]
        if key in CONSTANTS:
            return CONSTANTS[key]
        raise CalcError(f"Неизвестная величина «{key}»")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_eval_node(node.operand, names)
        if isinstance(node.op, ast.UAdd):
            return +_eval_node(node.operand, names)
        raise CalcError("Недопустимая унарная операция")

    if isinstance(node, ast.BinOp):
        handler = _BIN_OPS.get(type(node.op))
        if handler is None:
            raise CalcError("Недопустимая операция")
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        if isinstance(node.op, ast.Pow):
            # Главная защита от исчерпания памяти: 9**9**9 не должен родиться
            if abs(right) > MAX_EXPONENT:
                raise CalcError(f"Показатель степени больше {MAX_EXPONENT} не допускается")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise CalcError("Деление на ноль")
        try:
            return handler(left, right)
        except OverflowError:
            raise CalcError("Переполнение при вычислении")
        except ValueError as exc:
            raise CalcError(f"Недопустимая операция: {exc}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalcError("Вызов допустим только по имени функции")
        fn = FUNCTIONS.get(node.func.id)
        if fn is None:
            raise CalcError(f"Неизвестная функция «{node.func.id}»")
        if node.keywords:
            raise CalcError("Именованные аргументы не поддерживаются")
        args = [_eval_node(a, names) for a in node.args]
        try:
            return fn(*args)
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            raise CalcError(f"Ошибка в функции {node.func.id}: {exc}")

    raise CalcError("Недопустимая конструкция в выражении")


def evaluate(expr: str, names: dict[str, float] | None = None) -> float:
    """Вычислить одно выражение. Бросает CalcError с понятным текстом."""
    if not isinstance(expr, str):
        raise CalcError("Формула должна быть строкой")
    expr = expr.strip()
    if not expr:
        raise CalcError("Пустая формула")
    if len(expr) > MAX_EXPR_LENGTH:
        raise CalcError(f"Формула длиннее {MAX_EXPR_LENGTH} символов")
    # Запятая как десятичный разделитель встречается в наших документах,
    # но в Python это кортеж — поэтому явный отказ с подсказкой.
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"Не удалось разобрать формулу: {exc.msg}")
    except (RecursionError, MemoryError, ValueError) as exc:
        # Глубокую вложенность CPython отбивает сам («too many nested
        # parentheses»), но полагаться на текст чужого сообщения не стоит:
        # любой отказ парсера — понятная ошибка, а не 500 от сервера.
        raise CalcError(f"Формула слишком сложна для разбора: {type(exc).__name__}")
    if isinstance(tree.body, ast.Tuple):
        raise CalcError("Запятая как десятичный разделитель не поддерживается — пишите точку")
    _depth(tree)
    return _check_number(_eval_node(tree.body, names or {}), "результат")


def validate(expr: str, known: set[str] | None = None) -> None:
    """Проверить формулу без вычисления: синтаксис, состав, известность имён.

    Нужна при сохранении методики администратором — опечатку надо ловить
    сразу, а не при первом обращении модели.
    """
    probe = {name: 1.0 for name in (known or set())}
    try:
        evaluate(expr, probe)
    except CalcError as exc:
        msg = str(exc)
        # Деление на ноль и подобное на пробных единицах — не ошибка формулы
        if msg.startswith(("Деление на ноль", "результат", "Ошибка в функции")):
            return
        raise


def check_name(name: str) -> str:
    """Имя промежуточной величины: то, на что можно сослаться дальше."""
    if not isinstance(name, str):
        raise CalcError("Имя величины должно быть строкой")
    name = name.strip()
    if not name:
        raise CalcError("Пустое имя величины")
    if len(name) > MAX_NAME_LENGTH:
        raise CalcError(f"Имя длиннее {MAX_NAME_LENGTH} символов")
    if not _NAME_RE.match(name):
        raise CalcError(f"Недопустимое имя «{name}»: только буквы, цифры и подчёркивание")
    if name in FUNCTIONS:
        raise CalcError(f"Имя «{name}» занято функцией")
    return name


def format_value(value: float) -> str:
    """Число для текста, который читает МОДЕЛЬ.

    Дробная часть — через точку, а не запятую как в интерфейсе: если модель
    перенесёт это число в формулу, точка разберётся, а запятая упрётся в
    отказ «пишите точку» и будет стоить лишней итерации. До десяти значащих
    цифр без хвоста нулей: округлять историю нельзя, иначе следующий расчёт
    пойдёт от огрублённого значения.
    """
    if isinstance(value, int) or value == int(value):
        if abs(value) < 1e15:
            return str(int(value))
    return f"{value:.10g}"


def trace_to_text(trace: list[dict]) -> str:
    """Трасса расчёта для истории диалога.

    Сам расчёт хранится при сообщении ради интерфейса, но модель на следующем
    ходу видит только текст своего ответа. Если она ответила скупо, числа для
    неё пропадают — этот блок возвращает их обратно.
    """
    lines = []
    for step in trace:
        if not isinstance(step, dict) or not step.get("name"):
            continue
        value = step.get("value")
        has_value = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not step.get("expr") and not has_value:
            continue  # битая запись: без формулы и без числа строка бессмысленна
        line = f"  {step['name']} = {step.get('expr', '')}"
        if has_value:
            line += f" → {format_value(value)}"
            if step.get("unit"):
                line += f" {step['unit']}"
        if step.get("comment"):
            line += f"  ({step['comment']})"
        lines.append(line)
    return "[расчёт]\n" + "\n".join(lines) if lines else ""


def run_steps(steps: list[dict], given: dict[str, float] | None = None) -> list[dict]:
    """Посчитать цепочку шагов: каждый может ссылаться на предыдущие.

    Возвращает трассу — по строке на шаг с формулой, значением и единицей.
    Трасса важнее самого ответа: нормировщик должен видеть, из чего получена
    цифра, а сохранённая формула оставляет ответ воспроизводимым даже после
    правки методики.
    """
    if not isinstance(steps, list) or not steps:
        raise CalcError("Нужен хотя бы один шаг расчёта")
    if len(steps) > MAX_STEPS:
        raise CalcError(f"Больше {MAX_STEPS} шагов за раз не считаем")

    names: dict[str, float] = {}
    for key, value in (given or {}).items():
        names[check_name(key)] = _check_number(value, f"исходная величина «{key}»")

    trace: list[dict] = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise CalcError(f"Шаг {index}: ожидается объект с полями name и expr")
        name = check_name(step.get("name") or f"шаг{index}")
        if name in names:
            raise CalcError(f"Шаг {index}: величина «{name}» уже определена")
        expr = step.get("expr") or step.get("formula") or ""
        try:
            value = evaluate(expr, names)
        except CalcError as exc:
            raise CalcError(f"Шаг {index} ({name}): {exc}")
        names[name] = value
        trace.append({
            "name": name,
            "expr": expr.strip(),
            "value": value,
            "unit": str(step.get("unit") or "").strip(),
            "comment": str(step.get("comment") or "").strip(),
        })
    return trace
