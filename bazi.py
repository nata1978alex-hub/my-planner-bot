"""Day pillar for Moscow civil dates and solar-month based day officers."""
from datetime import timedelta, timezone

from lunar_python import Solar

# lunar_python's solar-term clock is China Standard Time (UTC+08:00).
CHINESE_CALENDAR_TIME = timezone(timedelta(hours=8))
OFFICERS = (
    ("建", "Установление"), ("除", "Устранение"), ("滿", "Наполнение"),
    ("平", "Равновесие"), ("定", "Определение"), ("執", "Удержание"),
    ("破", "Разрушение"), ("危", "Опасность"), ("成", "Успех"),
    ("收", "Принятие"), ("開", "Открытие"), ("閉", "Закрытие"),
)


def day_details(local_time):
    """Use the caller's Moscow date for the pillar and the instant for the month."""
    civil_day = local_time.date()
    day = Solar.fromYmd(civil_day.year, civil_day.month, civil_day.day).getLunar()
    term_time = local_time.astimezone(CHINESE_CALENDAR_TIME)
    month = Solar.fromYmdHms(
        term_time.year, term_time.month, term_time.day,
        term_time.hour, term_time.minute, term_time.second,
    ).getLunar()
    index = (day.getDayZhiIndex() - month.getMonthZhiIndexExact()) % len(OFFICERS)
    return day.getDayInGanZhi(), *OFFICERS[index]
