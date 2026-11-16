import copy
import unittest
from datetime import date, datetime, timedelta

from bazi import day_details
from planner import MOSCOW, morning_affirmation, morning_plan, new_state, scheduled, today


def moscow(year, month, day, hour=5, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=MOSCOW)


class MorningTests(unittest.TestCase):
    def test_independent_almanac_covers_all_officers(self):
        # https://fengshuicalculator.com/date-selection/2026-12/
        expected = (
            ("甲子", "建"), ("乙丑", "除"), ("丙寅", "滿"), ("丁卯", "平"),
            ("戊辰", "定"), ("己巳", "執"), ("庚午", "破"), ("辛未", "危"),
            ("壬申", "成"), ("癸酉", "收"), ("甲戌", "開"), ("乙亥", "閉"),
        )
        for day, value in enumerate(expected, 16):
            with self.subTest(day=day):
                self.assertEqual(day_details(moscow(2026, 12, day))[:2], value)

    def test_solar_month_changes_at_the_instant_not_midnight(self):
        # Dec 7 solar term around 05:53 Moscow and Feb 4 around 04:46 Moscow.
        # Almanacs give the after-transition officer for the whole Chinese date.
        fixtures = (
            (moscow(2026, 12, 6), ("甲寅", "平")),
            (moscow(2026, 12, 7, 5), ("乙卯", "定")),
            (moscow(2026, 12, 7, 5, 55), ("乙卯", "平")),
            (moscow(2026, 12, 8), ("丙辰", "定")),
            (moscow(2027, 2, 3), ("癸丑", "建")),
            (moscow(2027, 2, 4, 4, 44), ("甲寅", "除")),
            (moscow(2027, 2, 4, 5), ("甲寅", "建")),
            (moscow(2027, 2, 5), ("乙卯", "除")),
        )
        for instant, expected in fixtures:
            with self.subTest(time=instant):
                self.assertEqual(day_details(instant)[:2], expected)

    def test_moscow_midnight_and_sixty_day_cycle(self):
        late = moscow(2026, 12, 7, 23, 59)
        self.assertEqual(day_details(late)[0], "乙卯")
        self.assertEqual(day_details(late + timedelta(minutes=1))[0], "丙辰")
        self.assertEqual(day_details(late + timedelta(days=60))[0], "乙卯")

    def test_affirmation_effective_date_and_legacy_fallback(self):
        history = [
            {"effective_from": "2026-12-01", "text": "Новая"},
            {"effective_from": "2026-11-16", "text": "Текущая"},
            {"effective_from": "invalid", "text": "Ошибочная"},
        ]
        self.assertEqual(morning_affirmation(history, date(2026, 11, 30), "Старая"), "Текущая")
        self.assertEqual(morning_affirmation(history, date(2026, 12, 1), "Старая"), "Новая")
        self.assertEqual(morning_affirmation(None, date(2026, 12, 1), "Старая"), "Старая")

    def test_morning_content_follows_the_planned_time_and_keeps_buttons(self):
        state = new_state()
        state["tasks"] = {"1": "Подготовить выкройку"}
        state["days"] = {"2026-12-07": "День Грабителя"}
        state["reminders"] = {
            "2": {"due": "2026-12-07T11:00:00+00:00", "text": "Купить фурнитуру"},
            "3": {"due": "2026-12-07T22:00:00+00:00", "text": "Напоминание завтра"},
        }
        profile = {"定": {"name": "Определение", "text": "Пользовательское описание."}}
        history = [{"effective_from": "2026-11-16", "text": "Моя аффирмация"}]
        before = copy.deepcopy(state)
        _, morning = morning_plan(state, date(2026, 12, 7), profile, history)[0]
        self.assertEqual(state, before)
        for text in ("понедельник", "乙卯", "Определение (定)", "Пользовательское описание.",
                     "День Грабителя", "Моя аффирмация", "Подготовить выкройку",
                     "14:00 — Купить фурнитуру"):
            self.assertIn(text, morning["text"])
        self.assertNotIn("Напоминание завтра", morning["text"])
        self.assertEqual(morning["reply_markup"], today(state, "2026-12-07")[1][1]["reply_markup"])
        output = scheduled(state, moscow(2026, 12, 7, 6), bazi_profile=profile,
                           affirmations_history=history)
        self.assertIn("Определение (定)", output[0][1]["text"])
        self.assertEqual(scheduled(state, moscow(2026, 12, 7, 6, 10)), [])
        self.assertIn("Напоминание · Купить фурнитуру",
                      scheduled(state, moscow(2026, 12, 7, 14))[0][1]["text"])
        evening = scheduled(state, moscow(2026, 12, 7, 21))
        self.assertIn("Поставь оценку", evening[-1][1]["text"])

    def test_long_description_preserves_all_content_and_task_buttons(self):
        state = new_state()
        state["tasks"] = {"1": "Задача"}
        description = "я" * 8_000
        profile = {"定": {"text": description}}
        output = morning_plan(state, date(2026, 12, 7), profile, None)
        text = "".join(payload["text"] for _, payload in output)
        self.assertIn(description, text)
        self.assertTrue(all(len(payload["text"]) <= 3500 for _, payload in output))
        self.assertEqual(output[-1][1]["reply_markup"], today(state, "2026-12-07")[1][1]["reply_markup"])
