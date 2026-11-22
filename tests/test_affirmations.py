import copy
import unittest
from datetime import date, datetime

from affirmation_flow import entry_key
from planner import MENU, MOSCOW, handle, morning_affirmation, morning_plan, new_state, scheduled


NOW = datetime(2026, 11, 22, 23, 55, tzinfo=MOSCOW)


class AffirmationTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.state["affirmation"] = "Старое поле"
        self.history = [
            {"effective_from": "2026-11-16", "text": "Текущая"},
            {"effective_from": "2026-12-01", "text": "Декабрьская"},
        ]

    def send(self, text="", action="", now=NOW):
        return handle(self.state, now, text, action, affirmations_history=self.history)

    def draft(self, text="Новый текст", begins="25.11.2026"):
        self.send("/affirm " + text)
        return self.send(begins)

    def confirm(self):
        return self.send(action="af:confirm:" + self.state["affirmation_draft"]["token"])

    def test_overview_active_date_future_and_button(self):
        result = self.send("/affirmations")
        text = "".join(payload["text"] for _, payload in result)
        self.assertIn("Текущая", text)
        self.assertIn("16.11.2026", text)
        self.assertIn("Декабрьская", text)
        self.assertIn("01.12.2026", text)
        self.assertIn([{"text": "Аффирмации", "callback_data": "af:list:0"}], MENU)
        self.assertNotIn("affirmation_draft", self.state)

    def test_draft_does_not_save_before_confirmation(self):
        before = copy.deepcopy(self.history)
        result = self.draft()
        self.assertEqual(self.history, before)
        self.assertIn("25.11.2026", result[0][1]["text"])
        labels = [button["text"] for row in result[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(labels, ["Подтвердить", "Исправить", "Отменить"])
        self.confirm()
        self.assertEqual(morning_affirmation(self.history, date(2026, 11, 25), ""), "Новый текст")

    def test_earlier_change_preserves_december_and_history(self):
        before = copy.deepcopy(self.history)
        preview = self.draft(begins="сегодня")
        self.assertIn("01.12.2026", preview[0][1]["text"])
        self.confirm()
        self.assertEqual(self.history[:2], before)
        for stamp, text in (("2026-11-21", "Текущая"), ("2026-11-22", "Новый текст"),
                            ("2026-11-30", "Новый текст"), ("2026-12-01", "Декабрьская")):
            self.assertEqual(morning_affirmation(self.history, date.fromisoformat(stamp), ""), text)

    def test_same_day_replacement_archives_prior_version(self):
        self.draft("Замена декабря", "2026-12-01")
        self.confirm()
        self.assertEqual(self.history[1]["text"], "Декабрьская")
        self.assertEqual(self.history[1]["status"], "superseded")
        self.assertEqual(morning_affirmation(self.history, date(2026, 12, 1), ""), "Замена декабря")

    def test_correction_rejects_old_confirmation(self):
        self.draft()
        token = self.state["affirmation_draft"]["token"]
        self.send(action="af:correct:" + token)
        self.send(action="af:confirm:" + token)
        self.assertEqual(len(self.history), 2)
        self.send("Исправленный текст")
        self.send("28.11.2026")
        self.confirm()
        self.assertEqual(morning_affirmation(self.history, date(2026, 11, 28), ""), "Исправленный текст")

    def test_cancel_and_slash_cancel_keep_history(self):
        before = copy.deepcopy(self.history)
        self.draft()
        self.send(action="af:cancel:" + self.state["affirmation_draft"]["token"])
        self.assertEqual(self.history, before)
        self.draft()
        self.send("/cancel")
        self.assertNotIn("affirmation_draft", self.state)
        self.assertEqual(self.history, before)

    def test_double_confirmation_is_idempotent(self):
        self.draft()
        action = "af:confirm:" + self.state["affirmation_draft"]["token"]
        self.send(action=action)
        saved = copy.deepcopy(self.history)
        self.send(action=action)
        self.assertEqual(self.history, saved)

    def test_future_edit_changes_date_and_retains_old_version(self):
        self.send(action="af:edit:" + entry_key(self.history[1]))
        self.send("Текст на 27 ноября")
        self.send("27.11.2026")
        self.confirm()
        self.assertEqual(self.history[1]["status"], "superseded")
        self.assertEqual(morning_affirmation(self.history, date(2026, 12, 1), ""), "Текст на 27 ноября")

    def test_future_cancellation_requires_confirmation_and_retains_record(self):
        self.send(action="af:remove:" + entry_key(self.history[1]))
        self.assertEqual(len(self.history), 2)
        self.assertNotIn("status", self.history[1])
        self.confirm()
        self.assertEqual(self.history[1]["status"], "cancelled")
        self.assertEqual(self.history[1]["text"], "Декабрьская")
        self.assertEqual(morning_affirmation(self.history, date(2026, 12, 1), ""), "Текущая")

    def test_concurrent_edit_conflict_and_past_date_are_rejected(self):
        self.send(action="af:edit:" + entry_key(self.history[1]))
        self.send("Замена")
        self.send("30.11.2026")
        self.history[1]["text"] = "Изменено другим запросом"
        self.confirm()
        self.assertEqual(len(self.history), 2)
        self.draft(begins="21.11.2026")
        self.assertEqual(self.state["affirmation_draft"]["stage"], "date")
        self.send("31.11.2026")
        self.assertEqual(self.state["affirmation_draft"]["stage"], "date")

    def test_expired_confirmation_requires_a_new_date(self):
        self.draft(begins="сегодня")
        action = "af:confirm:" + self.state["affirmation_draft"]["token"]
        self.send(action=action, now=NOW.replace(day=23))
        self.assertEqual(len(self.history), 2)
        self.assertEqual(self.state["affirmation_draft"]["stage"], "date")

    def test_legacy_fallback_and_null_history(self):
        self.history = []
        self.draft("Первый план", "01.12.2026")
        self.confirm()
        self.assertEqual(morning_affirmation(self.history, date(2026, 11, 30), ""), "Старое поле")
        self.assertEqual(morning_affirmation(self.history, date(2026, 12, 1), ""), "Первый план")
        self.assertEqual(self.state["affirmation"], "Старое поле")

    def test_diary_pause_and_switch_to_reminders(self):
        self.state["journal"]["2026-11-22"] = {"score": 4, "notes": "Запись"}
        self.state["pending"] = ["thanks", "2026-11-22"]
        before = copy.deepcopy(self.state["journal"])
        self.draft()
        self.assertIsNone(self.state["pending"])
        self.send("Напомни завтра в 15:00 Позвонить")
        self.assertNotIn("affirmation_draft", self.state)
        self.assertIn("reminder_draft", self.state)
        self.assertEqual(self.state["journal"], before)

    def test_morning_on_effective_date_and_deduplication(self):
        self.draft("Ноябрьская", "24.11.2026")
        self.confirm()
        for day, expected in ((23, "Текущая"), (24, "Ноябрьская")):
            state = new_state()
            instant = datetime(2026, 11, day, 5, tzinfo=MOSCOW)
            output = scheduled(state, instant, affirmations_history=self.history)
            self.assertIn(expected, output[0][1]["text"])
            self.assertEqual(scheduled(state, instant, affirmations_history=self.history), [])

    def test_malformed_history_is_not_overwritten(self):
        malformed = {"unexpected": "format"}
        handle(self.state, NOW, "/affirm Текст", affirmations_history=malformed)
        handle(self.state, NOW, "сегодня", affirmations_history=malformed)
        action = "af:confirm:" + self.state["affirmation_draft"]["token"]
        handle(self.state, NOW, action=action, affirmations_history=malformed)
        self.assertEqual(malformed, {"unexpected": "format"})
