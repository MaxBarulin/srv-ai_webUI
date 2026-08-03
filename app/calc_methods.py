"""Справочник методик расчёта (§17): разбор, проверка, каталог, исполнение.

Методика — это именованный расчёт с параметрами и шагами, который заводит
администратор. Модель не сочиняет формулу, а подставляет числа в готовую:
арифметику вычислитель и так считает точно, но выбор формулы — как раз то
место, где модель ошибается. Методика этот выбор закрывает.

Параметры и шаги администратор пишет построчно, а не в JSON: инженеру так
быстрее, а нам не нужен сложный редактор с вложенными строками.

    параметры:  D | мм | диаметр заготовки
    шаги:       v = pi * D * n / 1000 | м/мин | скорость резания

Разбор и проверка идут здесь, на сервере: он же источник ошибок с номером
строки, которые видит администратор.
"""
from __future__ import annotations

import json

import aiosqlite

from app.calc import CalcError, check_name, run_steps, validate

MAX_METHOD_NAME = 120
MAX_DESCRIPTION = 500
MAX_PARAMS = 20
MAX_METHOD_STEPS = 30
# Каталог уходит в системный промпт при каждом запросе с включёнными
# расчётами, поэтому его размер ограничен: методик на пару десятков хватает,
# а на сотнях промпт распухнет и придётся заводить поиск отдельным вызовом.
MAX_CATALOGUE_METHODS = 40


def _split_cells(line: str) -> list[str]:
    return [part.strip() for part in line.split("|")]


def parse_params(text: str) -> list[dict]:
    """«D | мм | диаметр заготовки» построчно → список параметров."""
    params: list[dict] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = _split_cells(line)
        try:
            name = check_name(cells[0])
        except CalcError as exc:
            raise CalcError(f"Параметры, строка {number}: {exc}")
        if name in seen:
            raise CalcError(f"Параметры, строка {number}: «{name}» уже описан")
        seen.add(name)
        params.append({
            "name": name,
            "unit": cells[1] if len(cells) > 1 else "",
            "description": cells[2] if len(cells) > 2 else "",
        })
        if len(params) > MAX_PARAMS:
            raise CalcError(f"Не больше {MAX_PARAMS} параметров")
    return params


def parse_steps(text: str, param_names: set[str]) -> list[dict]:
    """«v = pi * D * n / 1000 | м/мин | комментарий» построчно → список шагов.

    Каждая формула проверяется сразу: известны ли все имена, нет ли
    запрещённых конструкций. Имена, определённые предыдущими шагами,
    доступны следующим.
    """
    steps: list[dict] = []
    known = set(param_names)
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = _split_cells(line)
        head, sep, expr = cells[0].partition("=")
        if not sep:
            raise CalcError(
                f"Шаги, строка {number}: нужен знак «=», например «v = pi * D * n / 1000»")
        try:
            name = check_name(head)
        except CalcError as exc:
            raise CalcError(f"Шаги, строка {number}: {exc}")
        if name in known:
            raise CalcError(f"Шаги, строка {number}: «{name}» уже определён выше")
        try:
            validate(expr, known)
        except CalcError as exc:
            raise CalcError(f"Шаги, строка {number} ({name}): {exc}")
        known.add(name)
        steps.append({
            "name": name,
            "expr": expr.strip(),
            "unit": cells[1] if len(cells) > 1 else "",
            "comment": cells[2] if len(cells) > 2 else "",
        })
        if len(steps) > MAX_METHOD_STEPS:
            raise CalcError(f"Не больше {MAX_METHOD_STEPS} шагов")
    if not steps:
        raise CalcError("Нужен хотя бы один шаг расчёта")
    return steps


def params_to_text(params: list[dict]) -> str:
    return "\n".join(
        " | ".join([p["name"], p.get("unit", ""), p.get("description", "")]).rstrip(" |")
        for p in params)


def steps_to_text(steps: list[dict]) -> str:
    return "\n".join(
        " | ".join([f"{s['name']} = {s['expr']}", s.get("unit", ""), s.get("comment", "")])
        .rstrip(" |")
        for s in steps)


def normalize_method(name: str, description: str, params_text: str, steps_text: str) -> dict:
    """Проверить методику целиком и вернуть готовое к записи представление."""
    clean_name = " ".join(name.split())
    if not clean_name:
        raise CalcError("Название методики не может быть пустым")
    if len(clean_name) > MAX_METHOD_NAME:
        raise CalcError(f"Название длиннее {MAX_METHOD_NAME} символов")
    clean_desc = description.strip()
    if len(clean_desc) > MAX_DESCRIPTION:
        raise CalcError(f"Описание длиннее {MAX_DESCRIPTION} символов")
    params = parse_params(params_text)
    steps = parse_steps(steps_text, {p["name"] for p in params})
    return {"name": clean_name, "description": clean_desc, "params": params, "steps": steps}


def method_dict(row: aiosqlite.Row) -> dict:
    params = json.loads(row["params_json"])
    steps = json.loads(row["steps_json"])
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "params": params,
        "steps": steps,
        "params_text": params_to_text(params),
        "steps_text": steps_to_text(steps),
        "is_active": bool(row["is_active"]),
        "sort_order": row["sort_order"],
    }


async def active_methods(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, name, description, params_json, steps_json, is_active, sort_order "
        "FROM calc_methods WHERE is_active = 1 ORDER BY sort_order, id "
        f"LIMIT {MAX_CATALOGUE_METHODS}")
    return [method_dict(row) for row in await cursor.fetchall()]


def build_catalogue(methods: list[dict]) -> str:
    """Список методик для системного промпта.

    Кладём каталог прямо в промпт, а не заводим отдельный инструмент поиска:
    поиск стоил бы лишнего круга через модель, а круг — это секунды в очереди.
    """
    if not methods:
        return ""
    lines = ["Готовые методики расчёта — вызывай их инструментом calc_method, "
             "не сочиняя формулы самостоятельно:"]
    for m in methods:
        params = ", ".join(
            f"{p['name']}" + (f" ({p['unit']})" if p["unit"] else "") for p in m["params"])
        line = f"  #{m['id']} «{m['name']}»"
        if m["description"]:
            line += f" — {m['description']}"
        line += f"; параметры: {params or 'нет'}"
        lines.append(line)
    lines.append("Если подходящей методики нет, считай через calc_run, "
                 "явно предупредив, что методика не из справочника.")
    return "\n".join(lines)


async def run_method(db: aiosqlite.Connection, method_id: int, given: dict) -> dict:
    """Выполнить методику по идентификатору с переданными параметрами."""
    cursor = await db.execute(
        "SELECT id, name, description, params_json, steps_json, is_active, sort_order "
        "FROM calc_methods WHERE id = ? AND is_active = 1", (method_id,))
    row = await cursor.fetchone()
    if row is None:
        raise CalcError(f"Методика #{method_id} не найдена или выключена")
    method = method_dict(row)

    numbers: dict[str, float] = {}
    missing: list[str] = []
    for p in method["params"]:
        if p["name"] not in given:
            missing.append(p["name"] + (f" ({p['unit']})" if p["unit"] else ""))
            continue
        value = given[p["name"]]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalcError(f"Параметр «{p['name']}» должен быть числом")
        numbers[p["name"]] = float(value)
    if missing:
        raise CalcError(f"Не переданы параметры методики «{method['name']}»: "
                        + ", ".join(missing))
    extra = set(given) - {p["name"] for p in method["params"]}
    if extra:
        raise CalcError(f"Методика «{method['name']}» не принимает: {', '.join(sorted(extra))}")

    trace = run_steps(method["steps"], numbers)
    return {"method": method, "trace": trace}
