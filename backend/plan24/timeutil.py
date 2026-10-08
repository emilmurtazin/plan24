from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config


def today_in(tz_name: str) -> date:
    """«Сегодня» в часовом поясе организации. В dev можно зафиксировать PLAN24_TODAY."""
    if config.env() == "dev" and os.environ.get("PLAN24_TODAY"):
        return date.fromisoformat(os.environ["PLAN24_TODAY"])
    return datetime.now(ZoneInfo(tz_name)).date()
