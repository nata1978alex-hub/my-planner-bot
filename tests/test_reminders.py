import copy
import unittest
from datetime import datetime

from planner import MENU, MOSCOW, handle, new_state, report, scheduled
from reminder_flow import parse_natural_reminder


NOW = datetime(2026, 11, 22, 11, 0, tzinfo=MOSCOW)


def confirmation(state):
    return "reminder_confirm:" + state["reminder_draft"]["token"]


class ReminderTests(unittest.TestCase):
    def test_examples_dates_and_moscow_offset(self):
        examples = (
            ("Напомни мне сегодня в 15:00 выпить чай", "2026-11-22T15:00:00+03:00", "выпить чай"),
            ("Напомни завтра в 10:00 позвонить врачу", "2026-11-23T10:00:00+03:00", "позвонить врачу"),
            ("Напомни через 30 минут сделать перерыв", "2026-11-22T11:30:00+03:00", "сделать перерыв"),
            ("Напомни через 3 дня проверить заказ", "2026-11-25T11:00:00+03:00", "проверить заказ"),
            ("Напомни 01.12.2026 в 9:05 задача", "2026-12-01T09:05:00+03:00", "задача"),
        )
        for text, stamp, title in examples:
            with self.subTest(text=text):
                due, actual = parse_natural_reminder(text, NOW)
                self.assertEqual((due.isoformat(), actual), (stamp, title))

    def test_midnight_month_year_and_leap_day(self):
        examples = (
            (datetime(2026, 12, 31, 23, 50, tzinfo=MOSCOW),
             "Напомни через 30 минут отдохнуть", "2027-01-01T00:20:00+03:00"),
            (datetime(2028, 2, 28, 11, 0, tzinfo=MOSCOW),
             "Напомни завтра в 10:00 позвонить", "2028-02-29T10:00:00+03:00"),
            (datetime(2026, 11, 30, 23, 59, tzinfo=MOSCOW),
             "Напомни завтра в 0:05 чай", "2026-12-01T00:05:00+03:00"),
        )
        for now, text, expected in examples:
            self.assertEqual(parse_natural_reminder(text, now)[0].isoformat(), expected)

    def test_ambiguous_invalid_and_past_input_never_saves(self):
        for text in ("Напомни завтра позвонить", "Напомни в три позвонить",
                     "Напомни сегодня в 10:00 чай", "Напомни завтра в 25:00 чай",
                     "Напомни 2026-02-30 в 15:00 чай", "Напомни через 0 минут чай"):
            state = new_state()
            response = handle(state, NOW, text)
            self.assertTrue(response)
            self.assertEqual(state["reminders"], {})
            self.assertTrue(state["reminder_draft"]["editing"])

    def test_confirmation_correction_cancellation_and_double_click(self):
        state = new_state()
        handle(state, NOW, "Напомни сегодня в 15:00 чай")
        original = confirmation(state)
        self.assertEqual(state["reminders"], {})
        handle(state, NOW, action=original.replace("confirm", "edit"))
        handle(state, NOW, action=original)
        self.assertEqual(state["reminders"], {})
        handle(state, NOW, "Напомни завтра в 10:00 кофе")
        corrected = confirmation(state)
        handle(state, NOW, action=corrected)
        handle(state, NOW, action=corrected)
        self.assertEqual(len(state["reminders"]), 1)
        self.assertEqual(state["reminders"]["1"]["text"], "кофе")
        handle(state, NOW, "Напомни завтра в 12:00 чай")
        cancel = confirmation(state).replace("confirm", "cancel")
        handle(state, NOW, action=cancel)
        self.assertNotIn("reminder_draft", state)
        self.assertEqual(len(state["reminders"]), 1)

    def test_expired_preview_requires_new_time(self):
        state = new_state()
        handle(state, NOW, "Напомни через 1 минуту чай")
        handle(state, NOW.replace(minute=2), action=confirmation(state))
        self.assertEqual(state["reminders"], {})
        self.assertTrue(state["reminder_draft"]["editing"])

    def test_diary_paused_by_button_and_resumed_without_losing_text(self):
        state = new_state()
        day = "2026-11-21"
        state["journal"][day] = {"score": 4, "notes": "Хороший день"}
        state["pending"] = ["thanks", day]
        before = copy.deepcopy(state["journal"])
        handle(state, NOW, action="reminders")
        self.assertIsNone(state["pending"])
        handle(state, NOW, "Напомни сегодня в 15:00 чай")
        handle(state, NOW, action=confirmation(state))
        self.assertEqual(state["journal"], before)
        handle(state, NOW, action="diary_resume:" + day)
        handle(state, NOW, "Спасибо за прогулку")
        self.assertEqual(state["journal"][day]["thanks"], "Спасибо за прогулку")
        self.assertTrue(state["journal"][day]["complete"])

    def test_plain_text_cannot_complete_stale_diary(self):
        state = new_state()
        day = "2026-11-21"
        state["journal"][day] = {"score": 3, "notes": "Запись"}
        state["pending"] = ["thanks", day]
        journal = copy.deepcopy(state["journal"])
        handle(state, NOW, "Просто сообщение")
        self.assertEqual(state["journal"], journal)
        self.assertIsNone(state["pending"])
        self.assertEqual(state["paused_diary"], ["thanks", day])

    def test_resumed_diary_needs_fresh_consent_after_another_day(self):
        state = new_state()
        state["journal"]["2026-11-21"] = {"score": 4}
        state["paused_diary"] = ["notes", "2026-11-21"]
        handle(state, NOW, action="diary_resume:2026-11-21")
        handle(state, NOW.replace(day=23), "Обычное сообщение")
        self.assertEqual(state["journal"]["2026-11-21"], {"score": 4})
        self.assertIsNone(state["pending"])

    def test_natural_request_before_diary_and_invalid_correction(self):
        state = new_state()
        state["journal"]["2026-11-22"] = {"notes": "Сохранено"}
        state["pending"] = ["thanks", "2026-11-22"]
        handle(state, NOW, "Напомни завтра позвонить")
        self.assertNotIn("thanks", state["journal"]["2026-11-22"])
        handle(state, NOW, "Напомни завтра в 15:00 позвонить")
        self.assertEqual(state["reminders"], {})
        handle(state, NOW, "/cancel")
        self.assertNotIn("reminder_draft", state)
        self.assertEqual(state["journal"]["2026-11-22"], {"notes": "Сохранено"})

    def test_legacy_remind_cancel_and_reminder_delivery(self):
        state = new_state()
        for command in ("/remind 1м Чай", "/remind 2026-11-23 10:00 Врач"):
            handle(state, NOW, command)
        self.assertEqual(len(state["reminders"]), 2)
        handle(state, NOW, "/cancel")
        self.assertEqual(len(state["reminders"]), 2)
        state["sent"] = {"morning": "2026-11-22"}
        self.assertEqual(scheduled(state, NOW), [])
        due = scheduled(state, NOW.replace(minute=10))
        self.assertEqual([p["text"] for _, p in due], ["Напоминание · Чай"])
        self.assertEqual(scheduled(state, NOW.replace(minute=20)), [])

    def test_tasks_evening_journal_report_and_buttons(self):
        state = new_state()
        handle(state, NOW, "/add Первая")
        handle(state, NOW, action="add")
        handle(state, NOW, "Вторая")
        self.assertEqual(len(state["tasks"]), 2)
        tomorrow = NOW.replace(day=23)
        self.assertIn("Первая", handle(state, tomorrow, "/today")[1][1]["text"])
        handle(state, NOW, action="done:1")
        self.assertEqual(list(state["tasks"].values()), ["Вторая"])
        handle(state, NOW, action="score:2026-11-22:5")
        handle(state, NOW, "Хороший день")
        handle(state, NOW, "—")
        self.assertTrue(state["journal"]["2026-11-22"]["complete"])
        self.assertIn("Хороший день", report(state, "2026-11")[1]["content"])
        self.assertEqual(handle(state, NOW, "/cancel")[0][1]["reply_markup"]["inline_keyboard"], MENU)
        self.assertEqual(handle(state, NOW, "/reminders")[0][1]["reply_markup"]["inline_keyboard"], MENU)

    def test_unfinished_evening_notified_once_next_morning(self):
        state = new_state()
        state["sent"]["evening"] = "2026-11-21"
        result = scheduled(state, NOW.replace(hour=5))
        self.assertEqual(sum("Вчерашний итог" in p.get("text", "") for _, p in result), 1)
        self.assertEqual(scheduled(state, NOW.replace(hour=5, minute=10)), [])
