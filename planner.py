"""Planner rules. The web service supplies storage and Telegram delivery."""
import calendar
import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bazi import day_details
from reminder_flow import handle_reminder_flow, resume_buttons

MOSCOW = ZoneInfo("Europe/Moscow")
HELP = """Мой планировщик
/add Текст — добавить задачу
/today — план на сегодня
/remind 15м Текст — напомнить через 15 минут
/remind 3д Текст — напомнить через 3 дня
/remind 1мес Текст — напомнить через месяц
/remind 2026-12-01 18:30 Текст — напомнить в указанное время
/reminders — список напоминаний
/day 2026-12-01 甲子 День грабителя — описание дня и два иероглифа
/affirm Текст — утренняя аффирмация
/evening — вечерний итог
/report 2026-12 — отчёт за месяц"""
MENU = [
    [{"text": "Сегодня", "callback_data": "today"},
     {"text": "Добавить задачу", "callback_data": "add"}],
    [{"text": "Вечерний итог", "callback_data": "evening"},
     {"text": "Напоминания", "callback_data": "reminders"}],
]


def new_state():
    return {"tasks": {}, "reminders": {}, "days": {}, "journal": {},
            "affirmation": "", "pending": None, "next_id": 1, "sent": {}}


def message(text, buttons=None):
    payload = {"text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return ("sendMessage", payload)


def chunks(lines, limit=3500):
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            yield current
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        yield current


def task_list(state):
    active = sorted(state["tasks"].items(), key=lambda pair: int(pair[0]))
    buttons = [[{"text": f"✓ {title[:45]}", "callback_data": f"done:{key}"}]
               for key, title in active[:30]]
    lines = [f"• {title}" for _, title in active] or ["Открытых задач нет."]
    pages = list(chunks(lines))
    return [message(page, buttons + MENU if index == 0 else None)
            for index, page in enumerate(pages)]


def today(state, day):
    description = state["days"].get(day, "")
    affirmation = state["affirmation"]
    return [message(f"План на {day}" + (f"\n{description}" if description else "")
                    + (f"\n\n{affirmation}" if affirmation else "")), *task_list(state)]


WEEKDAYS = ("понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье")


def morning_affirmation(history, day, fallback):
    latest = None
    selected = fallback
    for entry in history if isinstance(history, list) else ():
        if not isinstance(entry, dict) or entry.get("status", "active") != "active":
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            effective = date.fromisoformat(entry["effective_from"])
        except (KeyError, TypeError, ValueError):
            continue
        if effective <= day and (latest is None or effective >= latest):
            latest, selected = effective, text
    return selected


def morning_plan(state, day, bazi_profile, affirmations_history):
    lines = ["🌅 Доброе утро!", f"📅 {day:%d.%m.%Y}, {WEEKDAYS[day.weekday()]}"]
    try:
        pillar, officer, default_name = day_details(datetime.combine(day, time(5), MOSCOW))
    except Exception:
        # Keep the morning plan and due reminders available if calendar data fails.
        logging.error("Could not calculate the morning calendar")
        lines.extend(["", "🐉 Календарь Ба-цзы временно недоступен."])
    else:
        profile = bazi_profile.get(officer) if isinstance(bazi_profile, dict) else None
        name = profile.get("name") if isinstance(profile, dict) else None
        description = profile.get("text") if isinstance(profile, dict) else None
        if not isinstance(name, str) or not name.strip():
            name = default_name
        lines.extend(["", "🐉 Календарь Ба-цзы", f"Иероглифы дня: {pillar}",
                      f"Управитель: {name} ({officer})"])
        if isinstance(description, str) and description.strip():
            lines.extend(["", "🌿 Характеристика дня", description,
                          "Традиционная трактовка. Не откладывай необходимое лечение из-за календаря."])

    note = state["days"].get(day.isoformat())
    if note:
        lines.extend(["", "✨ Особенности сегодняшнего дня", note])
    affirmation = morning_affirmation(affirmations_history, day, state["affirmation"])
    if affirmation:
        lines.extend(["", "☀️ Моя аффирмация", affirmation])

    upcoming = sorted(
        (datetime.fromisoformat(item["due"]).astimezone(MOSCOW), item["text"])
        for item in state["reminders"].values()
    )
    reminders = [f"• {due:%H:%M} — {title}" for due, title in upcoming if due.date() == day]
    lines.extend(["", "🔔 Напоминания на сегодня", *(reminders or ["Напоминаний нет."]),
                  "", "📋 Задачи на сегодня"])

    tasks = task_list(state)
    heading = "\n".join(lines)
    _, first_task = tasks[0]
    combined = f'{heading}\n{first_task["text"]}'
    if len(combined) <= 3500:
        return [("sendMessage", {**first_task, "text": combined}), *tasks[1:]]

    # Editable descriptions can be long. Keep all content and existing task buttons.
    blocks = (line[offset:offset + 3500] for line in lines
              for offset in range(0, max(1, len(line)), 3500))
    return [
        *(message(page) for page in chunks(blocks) if page.strip()),
        *tasks,
    ]


def reminder_list(state):
    upcoming = sorted(state["reminders"].items(), key=lambda pair: pair[1]["due"])
    lines = [f'{datetime.fromisoformat(item["due"]):%d.%m %H:%M} — {item["text"]}'
             for _, item in upcoming] or ["Активных напоминаний нет."]
    buttons = [[{"text": f'Отменить · {item["text"][:35]}',
                 "callback_data": f"cancel:{key}"}] for key, item in upcoming[:30]]
    return [message(page, buttons + (resume_buttons(state) or []) + MENU if index == 0 else None)
            for index, page in enumerate(chunks(lines))]


def parse_reminder(value, now):
    relative = re.fullmatch(r"(\d{1,4})\s*(мес|м|ч|д)\s+(.+)", value, re.S)
    if relative:
        count, unit, title = relative.groups()
        count = int(count)
        if unit == "мес":
            year, month_index = divmod(now.year * 12 + now.month - 1 + count, 12)
            month = month_index + 1
            due = now.replace(year=year, month=month,
                              day=min(now.day, calendar.monthrange(year, month)[1]))
        else:
            interval = {"м": "minutes", "ч": "hours", "д": "days"}[unit]
            due = now + timedelta(**{interval: count})
    else:
        stamp, clock, title = value.split(maxsplit=2)
        due = datetime.strptime(f"{stamp} {clock}", "%Y-%m-%d %H:%M").replace(tzinfo=MOSCOW)
    if due <= now or not title.strip():
        raise ValueError("Reminder must have a future time and text")
    return due, title.strip()


def evening(day):
    return message(f"Как прошёл день {day}? Поставь оценку.",
                   [[{"text": str(score), "callback_data": f"score:{day}:{score}"}
                     for score in range(1, 6)]])


def report(state, month):
    records = sorted((day, entry) for day, entry in state["journal"].items()
                     if day.startswith(f"{month}-"))
    lines = [f"# Мой планировщик · {month}"]
    scored = [(day, entry["score"]) for day, entry in records if "score" in entry]
    if scored:
        low = min(score for _, score in scored)
        high = max(score for _, score in scored)
        for label, score in [("Самые позитивные дни", high), ("Самые трудные дни", low)]:
            dates = ", ".join(day for day, value in scored if value == score)
            lines.append(f"\n{label} ({score}/5) — {dates}")
    for day, entry in records:
        lines.append(f"\n## {day} · {entry.get('score', '—')}/5")
        lines.append(entry.get("notes") or "Записи нет.")
        lines.append(f"\nБлагодарности — {entry.get('thanks') or 'без записи'}")
    if not records:
        lines.append("\nЗаписей за месяц пока нет.")
    return ("sendDocument", {"filename": f"planner-{month}.md", "content": "\n".join(lines)})


def add_item(state, field, item):
    key = str(state["next_id"])
    state["next_id"] += 1
    state[field][key] = item


def handle(state, now, text="", action=""):
    flow = handle_reminder_flow(state, now, text, action)
    if flow is not None:
        if text.strip().split(" ", 1)[0].split("@", 1)[0].lower() == "/cancel":
            flow[0][1]["reply_markup"] = {"inline_keyboard": (resume_buttons(state) or []) + MENU}
        return flow
    day = now.date().isoformat()
    if action:
        kind, _, value = action.partition(":")
        if kind == "done":
            title = state["tasks"].pop(value, None)
            return [message(f"Готово · {title}" if title else "Эта задача уже закрыта."),
                    *task_list(state)]
        if kind == "cancel":
            state["reminders"].pop(value, None)
            return reminder_list(state)
        if kind == "score":
            target, _, score = value.partition(":")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target) or score not in "12345" or len(score) != 1:
                return []
            entry = state["journal"].setdefault(target, {})
            entry["score"] = int(score)
            state["pending"] = ["notes", target]
            return [message("Напиши пару строк о дне. Если хочешь пропустить, отправь «—».")]
        text = f"/{kind}"
    if not text:
        return [message("В этой версии можно вводить записи текстом.", MENU)]
    text = text.strip()
    if text.startswith("/"):
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        if command in ("/start", "/help"):
            return [message(HELP, MENU)]
        if command == "/today":
            return today(state, day)
        if command == "/add":
            if not argument:
                state["pending"] = ["task", day]
                return [message("Напиши текст задачи.")]
            add_item(state, "tasks", argument[:400])
            state["pending"] = None
            return [message("Задача добавлена.", MENU)]
        if command == "/remind":
            try:
                due, title = parse_reminder(argument, now)
            except (ValueError, OverflowError):
                return [message("Пример: /remind 15м Позвонить или /remind 2026-12-01 18:30 Позвонить.")]
            add_item(state, "reminders", {"due": due.isoformat(), "text": title[:400]})
            return [message(f"Напомню {due:%d.%m.%Y в %H:%M} по Москве.", MENU)]
        if command == "/reminders":
            return reminder_list(state)
        if command == "/day":
            try:
                stamp, description = argument.split(maxsplit=1)
                stamp = date.fromisoformat(stamp).isoformat()
            except ValueError:
                return [message("Пример: /day 2026-12-01 甲子 День грабителя")]
            state["days"][stamp] = description[:750]
            return [message(f"Описание сохранено на {stamp}.")]
        if command == "/affirm":
            if not argument:
                return [message("Отправь /affirm и текст новой аффирмации.")]
            state["affirmation"] = argument[:750]
            return [message("Аффирмация сохранена.")]
        if command == "/evening":
            return [*task_list(state), evening(day)]
        if command == "/report":
            month = argument or day[:7]
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                return [message("Пример: /report 2026-12")]
            return [report(state, month)]
        if command == "/cancel":
            state["pending"] = None
            return [message("Ввод отменён.", MENU)]
        return [message("Команды доступны через /help.", MENU)]
    pending = state["pending"]
    if not pending:
        return [message("Для новой задачи нажми «Добавить задачу».", MENU)]
    field, target = pending
    if field == "task":
        return handle(state, now, f"/add {text}")
    entry = state["journal"][target]
    entry[field] = "" if text == "—" else text[:3500]
    if field == "notes":
        state["pending"] = ["thanks", target]
        return [message("За что ты сегодня благодарна? Можно отправить «—».")]
    entry["complete"] = True
    state["pending"] = None
    return [message("Вечерний итог сохранён.", MENU)]


def scheduled(state, now, *, bazi_profile=None, affirmations_history=None):
    day = now.date()
    key = day.isoformat()
    previous = (day - timedelta(days=1)).isoformat()
    sent = state["sent"]
    output = []
    for name, hours, end in [("morning", 5, 21), ("evening", 21, 24)]:
        if not hours <= now.hour < end or sent.get(name) == key:
            continue
        sent[name] = key
        if name == "morning":
            output.extend(morning_plan(state, day, bazi_profile, affirmations_history))
            if not state["journal"].get(previous, {}).get("complete") and sent.get("evening") == previous:
                output.append(message("Вчерашний итог ещё можно заполнить."))
                output.append(evening(previous))
            previous_month = previous[:7]
            if previous_month != key[:7] and sent.get("report") != previous_month:
                sent["report"] = previous_month
                output.append(report(state, previous_month))
        else:
            output.extend([*task_list(state), evening(key)])
    for reminder_id, item in list(state["reminders"].items()):
        if datetime.fromisoformat(item["due"]) <= now:
            output.append(message(f'Напоминание · {item["text"]}', MENU))
            del state["reminders"][reminder_id]
    return output
