"""Рабочий календарь и горизонт планирования (BR-2, BR-3)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

HORIZON_WEEKS = 8
EXTRA_DAYS_AFTER_LAST_DUE = 14
GUARD_DAYS = 3650


def build_work_calendar(
    today: date,
    due_dates: Iterable[Optional[date]] = (),
    holidays: Iterable[date] = (),
    work_weekdays: tuple = (0, 1, 2, 3, 4),   # Пн–Пт (date.weekday())
    horizon_weeks: int = HORIZON_WEEKS,
) -> tuple:
    """Рабочие дни от `today` до max(последний срок + 14 дней, today + 8 недель).

    Если `today` — выходной или праздник, первым днём становится ближайший рабочий.
    """
    hol = set(holidays)
    max_due = today
    for d in due_dates:
        if d is not None and d > max_due:
            max_due = d
    safety = max_due + timedelta(days=EXTRA_DAYS_AFTER_LAST_DUE)
    min_end = today + timedelta(days=horizon_weeks * 7)
    end = safety if safety > min_end else min_end

    days = []
    cursor = today
    guard = 0
    while cursor <= end and guard < GUARD_DAYS:
        if cursor.weekday() in work_weekdays and cursor not in hol:
            days.append(cursor)
        cursor += timedelta(days=1)
        guard += 1
    return tuple(days)
