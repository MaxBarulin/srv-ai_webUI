"""Видимость общих заметок и событий с учётом групп (отделов/бюро).

Правило одно на весь проект — REST-роутеры и инструменты LLM обязаны
пользоваться этими функциями, чтобы фильтр нельзя было забыть в одном
из мест:

* личная запись (scope = 'personal') — только автору;
* общая запись (scope = 'shared'):
    - пользователь без группы видит все общие записи;
    - пользователь в группе видит общие своей группы и общие «для всех»
      (group_id IS NULL) — так объявление администратора доходит до каждого.

Группа записи проставляется сервером при записи и не принимается от
пользователя, состоящего в группе: он всегда публикует в свою группу.
"""
from __future__ import annotations


def visible_sql(alias: str = "") -> str:
    """Условие WHERE для выборки видимых записей.

    Ожидает три плейсхолдера подряд — их даёт visible_params().

    alias подставляется в SQL как есть, поэтому допустимы только литералы из
    кода («n», «e»); пользовательские данные сюда попадать не должны.
    """
    p = f"{alias}." if alias else ""
    return (
        f"({p}owner_id = ? OR ({p}scope = 'shared' AND "
        f"(? IS NULL OR {p}group_id IS NULL OR {p}group_id = ?)))"
    )


def visible_params(user: dict) -> list:
    # Именно user["group_id"], а не .get(): отсутствие ключа означало бы
    # «без группы», то есть доступ ко всем общим записям. Такая ошибка должна
    # шуметь KeyError, а не тихо открывать чужие отделы.
    group_id = user["group_id"]
    return [user["id"], group_id, group_id]


def may_choose_group(user: dict) -> bool:
    """Кто вправе сам выбрать адресата общей записи.

    Администратор — потому что раскладывать общую базу по отделам может
    только он; и любой сотрудник без группы, которому и так видно всё.
    Остальные публикуют исключительно в свою группу.
    """
    return user["role"] == "admin" or user["group_id"] is None


def may_retarget(user: dict, owner_id: int) -> bool:
    """Кто вправе сменить адресата уже опубликованной общей записи.

    Автор — своей; администратор — любой, включая заметки, созданные до
    появления групп. Удалять чужое и переводить его в личные по-прежнему
    может только автор — это правило не трогаем.
    """
    return user["id"] == owner_id or user["role"] == "admin"


def target_group_id(user: dict, scope: str, requested: int | None) -> int | None:
    """Какой группе адресована записываемая заметка/событие.

    Сотрудник в группе публикует только в свою — переданный им group_id
    игнорируется. Тот, кому выбор разрешён (см. may_choose_group), адресует
    запись названной группе либо оставляет её общей для всех.
    """
    if scope != "shared":
        return None  # у личной записи группы нет
    if may_choose_group(user):
        return requested
    return user["group_id"]  # см. visible_params: молчаливый .get() опасен


def new_group_id(user: dict, scope: str, requested: int | None, explicit: bool) -> int | None:
    """Группа создаваемой записи.

    Поле group_id по умолчанию равно None, поэтому «не передал» и «передал
    null — для всех» различаются только по explicit. Без явного выбора запись
    уходит в группу автора: иначе администратор, состоящий в отделе, разослал
    бы её всему заводу, сам того не заметив.
    """
    return target_group_id(user, scope, requested if explicit else user["group_id"])


def update_group_id(
    user: dict,
    current_scope: str,
    current_group_id: int | None,
    new_scope: str,
    requested: int | None,
    explicit: bool,
) -> int | None:
    """Группа записи после редактирования.

    Пересчитываем только тогда, когда запись становится общей или автор
    явно переадресовал её другой группе. Иначе группа сохраняется: правка
    чужой общей заметки коллегой не должна менять круг её читателей — и не
    должна утаскивать её в группу редактора.
    """
    if new_scope != "shared":
        return None
    if current_scope != "shared" or explicit:
        return new_group_id(user, "shared", requested, explicit)
    return current_group_id
