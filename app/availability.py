"""Cálculo de horarios disponibles a partir del horario laboral y los intervalos ocupados."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .config import Settings

Interval = tuple[datetime, datetime]


def overlaps(start: datetime, end: datetime, intervals: Iterable[Interval]) -> bool:
    return any(busy_start < end and start < busy_end for busy_start, busy_end in intervals)


def generate_slots(
    settings: Settings,
    range_start: datetime,
    range_end: datetime,
    busy: list[Interval],
    now: datetime,
) -> list[datetime]:
    """Devuelve los inicios (en UTC) de los horarios libres dentro de [range_start, range_end)."""
    tz = settings.tz
    duration = timedelta(minutes=settings.duration_minutes)
    step = timedelta(minutes=settings.slot_interval_minutes)

    earliest = max(range_start, now + timedelta(hours=settings.min_notice_hours))
    latest = min(range_end, now + timedelta(days=settings.booking_window_days))
    if earliest >= latest:
        return []

    slots: list[datetime] = []
    day = earliest.astimezone(tz).date()
    last_day = latest.astimezone(tz).date()
    while day <= last_day:
        if day.isoweekday() in settings.work_days and day not in settings.blocked_dates:
            for block_start, block_end in settings.work_hours:
                cursor = datetime.combine(day, block_start, tzinfo=tz)
                block_limit = datetime.combine(day, block_end, tzinfo=tz)
                while cursor + duration <= block_limit:
                    start = cursor.astimezone(timezone.utc)
                    end = start + duration
                    if earliest <= start < latest and not overlaps(start, end, busy):
                        slots.append(start)
                    cursor += step
        day += timedelta(days=1)
    return slots
