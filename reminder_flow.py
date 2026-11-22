"""Russian reminder drafts and explicit resumption of interrupted diary input."""
import re
import secrets
from datetime import date, datetime, time, timedelta


EXAMPLES = (
    "Напиши, например: «Напомни завтра в 10:00 позвонить врачу» "
    "или «Напомни через 30 минут сделать перерыв»."
)


def parse_natural_reminder(text, now):
    value = re.sub(r"^напомни(?:\s+мне)?\s+", "", text.strip(), flags=re.I)
    relative = re.fullmatch(
        r"через\s+(\d{1,4})\s+(минут(?:у|ы)?|час(?:а|ов)?|д(?:ень|ня|ней))\s+(.+)",
        value, re.I | re.S,
    )
    if relative:
        amount, unit, title = relative.groups()
        unit = unit.lower()
        if unit.startswith("минут"):
            duration = "minutes"
        elif unit.startswith("час"):
            duration = "hours"
        else:
            duration = "days"
        due = now + timedelta(**{duration: int(amount)})
    else:
        absolute = re.fullmatch(
            r"(сегодня|завтра|послезавтра|\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})"
            r"\s+в\s+(\d{1,2}):(\d{2})\s+(.+)", value, re.I | re.S,
        )
        if not absolute:
            raise ValueError("Укажи дату, точное время и текст напоминания. " + EXAMPLES)
        stamp, hour, minute, title = absolute.groups()
        stamp = stamp.lower()
        offsets = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
        try:
            if stamp in offsets:
                target = now.date() + timedelta(days=offsets[stamp])
            elif "." in stamp:
                target = datetime.strptime(stamp, "%d.%m.%Y").date()
            else:
                target = date.fromisoformat(stamp)
            due = datetime.combine(target, time(int(hour), int(minute)), now.tzinfo)
        except ValueError:
            raise ValueError("Проверь дату и время. Часы от 0 до 23, минуты от 00 до 59.") from None
    title = title.strip()
    if due <= now:
        raise ValueError("Это время уже прошло. Укажи будущие дату и время.")
    if not title or len(title) > 400:
        raise ValueError("Текст напоминания должен содержать от 1 до 400 символов.")
    return due, title


def response(text, buttons=None):
    payload = {"text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return [("sendMessage", payload)]


def pause_diary(state):
    pending = state.get("pending")
    if pending and pending[0] in {"notes", "thanks"}:
        state.pop("diary_resumed_date", None)
        state.pop("diary_resumed_on", None)
        state["paused_diary"] = list(pending)
        state["pending"] = None


def resume_buttons(state):
    paused = state.get("paused_diary")
    if not paused:
        return None
    return [[{"text": f"Продолжить итог за {paused[1]}", "callback_data": f"diary_resume:{paused[1]}"}]]


def preview(draft):
    due = datetime.fromisoformat(draft["due"])
    token = draft["token"]
    return response(
        f"Создать напоминание?\n{due:%d.%m.%Y в %H:%M} по Москве\n{draft['text']}",
        [[{"text": "Подтвердить", "callback_data": f"reminder_confirm:{token}"},
          {"text": "Исправить", "callback_data": f"reminder_edit:{token}"}],
         [{"text": "Отменить", "callback_data": f"reminder_cancel:{token}"}]],
    )


def handle_reminder_flow(state, now, text, action):
    """Return None to continue the existing planner handler."""
    text = text.strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    kind, _, token = action.partition(":")
    draft = state.get("reminder_draft")

    if kind in {"reminder_confirm", "reminder_edit", "reminder_cancel"}:
        if not draft or token != draft["token"]:
            return response("Этот запрос уже обработан или заменён новым.")
        if kind == "reminder_cancel":
            state.pop("reminder_draft", None)
            return response("Создание напоминания отменено.", resume_buttons(state))
        if kind == "reminder_edit":
            draft["editing"] = True
            # Invalidate the previously displayed confirmation immediately.
            draft["token"] = secrets.token_hex(8)
            return response("Введи исправленное напоминание целиком. " + EXAMPLES)
        if draft.get("editing"):
            return response("Сначала введи исправленное напоминание.")
        due = datetime.fromisoformat(draft["due"])
        if due <= now:
            draft["editing"] = True
            draft["token"] = secrets.token_hex(8)
            return response("Время уже прошло. Введи напоминание с новым временем. " + EXAMPLES)
        key = str(state["next_id"])
        state["next_id"] += 1
        state["reminders"][key] = {"due": draft["due"], "text": draft["text"]}
        state.pop("reminder_draft", None)
        return response(f"Напомню {due:%d.%m.%Y в %H:%M} по Москве.", resume_buttons(state))

    if kind == "diary_resume":
        paused = state.get("paused_diary")
        if not paused or token != paused[1]:
            return response("Этот итог уже продолжен или заменён другим.")
        if draft:
            return response("Сначала подтверди или отмени напоминание.")
        state["pending"] = state.pop("paused_diary")
        state["diary_resumed_date"] = token
        state["diary_resumed_on"] = now.date().isoformat()
        field, day = state["pending"]
        prompt = "Напиши пару строк о дне." if field == "notes" else "За что ты благодарна?"
        return response(f"Продолжаем итог за {day}. {prompt} Для пропуска отправь «—».")

    if command == "/cancel":
        state.pop("reminder_draft", None)
        state["pending"] = None
        return response("Ввод отменён.", resume_buttons(state))

    if action == "reminders" or command in {"/reminders", "/remind"}:
        pause_diary(state)
        if command == "/remind":
            state.pop("reminder_draft", None)
        return None

    natural = bool(re.match(r"^напомни\b", text, re.I))
    if natural or (draft and draft.get("editing") and text and not text.startswith("/")):
        pause_diary(state)
        try:
            due, title = parse_natural_reminder(text, now)
        except (ValueError, OverflowError) as error:
            state["reminder_draft"] = {"token": secrets.token_hex(8), "editing": True}
            prompt = str(error) if isinstance(error, ValueError) else "Слишком большой срок. Укажи другую дату."
            return response(prompt, [[{"text": "Отменить", "callback_data":
                                      f"reminder_cancel:{state['reminder_draft']['token']}"}]])
        state["reminder_draft"] = {"token": secrets.token_hex(8), "due": due.isoformat(),
                                   "text": title, "editing": False}
        return preview(state["reminder_draft"])

    if draft and not action and text and not text.startswith("/"):
        return preview(draft)

    pending = state.get("pending")
    if (not action and text and not text.startswith("/") and pending
            and pending[0] in {"notes", "thanks"} and pending[1] != now.date().isoformat()
            and (state.get("diary_resumed_date") != pending[1]
                 or state.get("diary_resumed_on") != now.date().isoformat())):
        pause_diary(state)
        return response("Есть незавершённый итог прошлого дня. Нажми кнопку, чтобы продолжить его. "
                        "Для напоминания можно написать «Напомни завтра в 10:00 позвонить».",
                        resume_buttons(state))
    return None
