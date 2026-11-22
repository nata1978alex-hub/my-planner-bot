"""Effective-dated affirmation history with owner-confirmed Telegram edits."""
import copy
import hashlib
import json
import re
import secrets
from datetime import date, datetime

from reminder_flow import pause_diary, resume_buttons


def entries(history):
    return history if isinstance(history, list) else []


def effective_date(entry):
    if not isinstance(entry, dict) or entry.get("status", "active") != "active":
        return None
    if not isinstance(entry.get("text"), str) or not entry["text"].strip():
        return None
    try:
        return date.fromisoformat(entry["effective_from"])
    except (KeyError, TypeError, ValueError):
        return None


def active_entry(history, day):
    selected, latest = None, None
    for entry in entries(history):
        begins = effective_date(entry)
        if begins is not None and begins <= day and (latest is None or begins >= latest):
            selected, latest = entry, begins
    return selected


def entry_key(entry):
    encoded = json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def find_future(history, key, day):
    for entry in entries(history):
        begins = effective_date(entry)
        if begins is not None and begins > day and entry_key(entry) == key:
            return entry
    return None


def respond(text, buttons):
    # User-editable legacy texts can be longer than a Telegram message.
    pages = [text[i:i + 3500] for i in range(0, len(text), 3500)] or [""]
    output = [("sendMessage", {"text": page}) for page in pages]
    output[-1][1]["reply_markup"] = {"inline_keyboard": buttons}
    return output


def overview(state, history, day, page=0):
    active = active_entry(history, day)
    text = "☀️ Аффирмации\n\nТекущая аффирмация\n"
    if active:
        begins = date.fromisoformat(active["effective_from"])
        text += f"С {begins:%d.%m.%Y}\n{active['text']}"
    elif state.get("affirmation"):
        text += f"{state['affirmation']}\nДата начала в истории не указана."
    else:
        text += "Аффирмация пока не задана."
    future = sorted(
        (entry for entry in entries(history) if effective_date(entry) is not None
         and effective_date(entry) > day), key=lambda entry: entry["effective_from"],
    )
    page = min(max(0, page), max(0, (len(future) - 1) // 5))
    buttons = [[{"text": "Новая аффирмация", "callback_data": "af:new"}]]
    text += "\n\nЗапланировано"
    for entry in future[page * 5:(page + 1) * 5]:
        begins = date.fromisoformat(entry["effective_from"])
        key = entry_key(entry)
        text += f"\n\nС {begins:%d.%m.%Y}\n{entry['text']}"
        buttons.append([
            {"text": f"Изменить {begins:%d.%m}", "callback_data": f"af:edit:{key}"},
            {"text": f"Отменить {begins:%d.%m}", "callback_data": f"af:remove:{key}"},
        ])
    if not future:
        text += "\nБудущих аффирмаций нет."
    navigation = []
    if page:
        navigation.append({"text": "Назад", "callback_data": f"af:list:{page - 1}"})
    if (page + 1) * 5 < len(future):
        navigation.append({"text": "Далее", "callback_data": f"af:list:{page + 1}"})
    if navigation:
        buttons.append(navigation)
    return respond(text, buttons + (resume_buttons(state) or []))


def draft_buttons(draft):
    token = draft["token"]
    return [[{"text": "Подтвердить", "callback_data": f"af:confirm:{token}"},
             {"text": "Исправить", "callback_data": f"af:correct:{token}"}],
            [{"text": "Отменить", "callback_data": f"af:cancel:{token}"}]]


def preview(draft, history):
    begins = date.fromisoformat(draft["effective_from"])
    if draft.get("operation") == "remove":
        text = f"Отменить запланированную аффирмацию с {begins:%d.%m.%Y}?\n{draft['text']}"
        buttons = draft_buttons(draft)
        buttons[0] = buttons[0][:1]
        return respond(text, buttons)
    text = f"Сохранить аффирмацию с {begins:%d.%m.%Y} по Москве?\n\n{draft['text']}"
    same_day = any(effective_date(entry) == begins for entry in entries(history))
    if same_day:
        text += "\n\nЗапись на эту дату будет заменена. Её прежняя версия сохранится в истории."
    later = [effective_date(entry) for entry in entries(history)
             if effective_date(entry) is not None and effective_date(entry) > begins
             and entry_key(entry) != draft.get("target")]
    if later:
        next_date = min(later)
        text += f"\n\nСледующая запланированная смена {next_date:%d.%m.%Y} сохранится."
    return respond(text, draft_buttons(draft))


def parse_date(text, today):
    if text.strip().lower() in {"сегодня", "с сегодняшнего дня"}:
        return today
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        try:
            return datetime.strptime(text.strip(), "%d.%m.%Y").date()
        except ValueError:
            raise ValueError("Введи дату в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД, либо «сегодня».") from None


def confirm(state, history, draft, day):
    if history is not None and not isinstance(history, list):
        return respond("Не удалось прочитать формат истории. Изменение не сохранено.",
                       [[{"text": "Аффирмации", "callback_data": "af:list:0"}]])
    target = None
    if draft.get("target"):
        target = find_future(history, draft["target"], day)
        if target is None:
            state.pop("affirmation_draft", None)
            return respond("Запись уже изменена или дата её начала наступила. Открой список заново.",
                           [[{"text": "Аффирмации", "callback_data": "af:list:0"}]])
    begins = date.fromisoformat(draft["effective_from"])
    if begins < day:
        draft.update(stage="date", token=secrets.token_hex(8))
        return respond("Эта дата уже прошла. Введи сегодняшнюю или будущую дату.",
                       [[{"text": "Отменить", "callback_data": f"af:cancel:{draft['token']}"}]])
    rows = copy.deepcopy(entries(history))
    # Retain the legacy fallback as a dated historical record on the first write.
    if active_entry(rows, day) is None and state.get("affirmation"):
        rows.append({"effective_from": day.isoformat(), "text": state["affirmation"],
                     "source": "legacy", "recorded_on": day.isoformat()})
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        if target is not None and entry_key(entry) == draft["target"]:
            entry.update(status="cancelled" if draft["operation"] == "remove" else "superseded",
                         changed_on=day.isoformat())
        elif draft["operation"] != "remove" and effective_date(entry) == begins:
            entry.update(status="superseded", changed_on=day.isoformat())
    if draft["operation"] != "remove":
        rows.append({"id": secrets.token_hex(12), "effective_from": begins.isoformat(),
                     "text": draft["text"], "status": "active", "recorded_on": day.isoformat()})
    # Modify the list supplied by the caller. app.py persists it in the same row transaction.
    history[:] = rows
    state.pop("affirmation_draft", None)
    result = "Запланированная аффирмация отменена." if draft["operation"] == "remove" else "Аффирмация сохранена."
    return respond(result, [[{"text": "Аффирмации", "callback_data": "af:list:0"}]])


def handle_affirmation_flow(state, history, now, text, action):
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    command = command if command.startswith("/") else ""
    day = now.date()
    draft = state.get("affirmation_draft")
    switching = (action in {"reminders", "add", "evening"}
                 or command in {"/remind", "/reminders", "/add", "/evening"}
                 or bool(re.match(r"^напомни\b", text.strip(), re.I)))
    if switching or command == "/cancel":
        state.pop("affirmation_draft", None)
        return None
    if action.startswith("af:") or command in {"/affirm", "/affirmations"}:
        pause_diary(state)
        state.pop("reminder_draft", None)
    parts = action.split(":", 2)
    operation = parts[1] if len(parts) > 1 and parts[0] == "af" else ""
    value = parts[2] if len(parts) > 2 else ""
    if command == "/affirmations" or operation == "list" or (command == "/affirm" and not argument):
        state.pop("affirmation_draft", None)
        page = int(value) if value.isdigit() else 0
        return overview(state, history, day, page)
    if operation in {"confirm", "correct", "cancel"}:
        if not draft or value != draft["token"]:
            return respond("Этот запрос уже обработан или заменён новым.",
                           [[{"text": "Аффирмации", "callback_data": "af:list:0"}]])
        if operation == "cancel":
            state.pop("affirmation_draft", None)
            return overview(state, history, day)
        if operation == "correct":
            draft.update(stage="text", token=secrets.token_hex(8))
            return respond("Введи исправленный текст аффирмации. Затем выберем дату.",
                           [[{"text": "Отменить", "callback_data": f"af:cancel:{draft['token']}"}]])
        if draft.get("stage") != "confirm":
            return respond("Сначала введи текст и дату начала.",
                           [[{"text": "Аффирмации", "callback_data": "af:list:0"}]])
        return confirm(state, history, draft, day)
    if operation in {"new", "edit", "remove"} or command == "/affirm":
        if history is None:
            return respond("История аффирмаций недоступна. Изменение не сохранено.",
                           [[{"text": "Аффирмации", "callback_data": "af:list:0"}]])
        draft = {"token": secrets.token_hex(8), "stage": "text", "operation": "save"}
        if operation in {"edit", "remove"}:
            entry = find_future(history, value, day)
            if entry is None:
                return overview(state, history, day)
            draft.update(target=value, text=entry["text"], effective_from=entry["effective_from"])
        if operation == "remove":
            draft.update(operation="remove", stage="confirm")
        elif command == "/affirm":
            draft.update(text=argument.strip(), stage="date")
            if not 1 <= len(draft["text"]) <= 750:
                draft["stage"] = "text"
        state["affirmation_draft"] = draft
        if operation == "remove":
            return preview(draft, history)
        if draft["stage"] == "date":
            return respond("С какой даты применять? Введи ДД.ММ.ГГГГ или «сегодня».",
                           [[{"text": "Отменить", "callback_data": f"af:cancel:{draft['token']}"}]])
        return respond("Введи текст аффирмации (до 750 символов). Затем выберем дату.",
                       [[{"text": "Отменить", "callback_data": f"af:cancel:{draft['token']}"}]])
    if draft and text.strip() and not command and not action:
        if draft["stage"] == "text":
            if not 1 <= len(text.strip()) <= 750:
                return respond("Введи от 1 до 750 символов.", draft_buttons(draft)[1:])
            draft.update(text=text.strip(), stage="date")
            return respond("С какой даты применять? Введи ДД.ММ.ГГГГ или «сегодня».",
                           draft_buttons(draft)[1:])
        if draft["stage"] == "date":
            try:
                begins = parse_date(text, day)
                if begins < day:
                    raise ValueError("Укажи сегодняшнюю или будущую дату.")
            except ValueError as error:
                return respond(str(error), draft_buttons(draft)[1:])
            draft.update(effective_from=begins.isoformat(), stage="confirm", token=secrets.token_hex(8))
        return preview(draft, history)
    return None
