"""Reglas de negocio: horarios disponibles y creación de reservas."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .availability import generate_slots
from .config import Settings
from .db import Booking, BookingStore, SlotTakenError
from .google_calendar import GoogleCalendarClient

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(Exception):
    """Datos de entrada inválidos."""


class SlotUnavailableError(Exception):
    """El horario elegido ya no está disponible."""


class BookingService:
    def __init__(self, settings: Settings, store: BookingStore, calendar: GoogleCalendarClient):
        self.settings = settings
        self.store = store
        self.calendar = calendar

    def _busy(self, start: datetime, end: datetime):
        # Se amplía el rango para detectar reservas que empiezan antes y terminan dentro.
        margin = timedelta(minutes=self.settings.duration_minutes)
        busy = self.store.busy_intervals(start - margin, end + margin)
        if self.calendar.enabled:
            busy += self.calendar.busy_intervals(start - margin, end + margin)
        return busy

    def available_slots(self, start: datetime, end: datetime, now: datetime | None = None) -> list[datetime]:
        now = now or datetime.now(timezone.utc)
        return generate_slots(self.settings, start, end, self._busy(start, end), now)

    def book(self, *, name: str, email: str, start: datetime, invitee_timezone: str | None) -> Booking:
        name = " ".join(str(name or "").split())
        email = str(email or "").strip().lower()
        if not 2 <= len(name) <= 100:
            raise ValidationError("Ingresa tu nombre (entre 2 y 100 caracteres).")
        if len(email) > 254 or not EMAIL_RE.match(email):
            raise ValidationError("Ingresa un email válido.")
        invitee_timezone = _valid_timezone(invitee_timezone)

        start = start.astimezone(timezone.utc).replace(microsecond=0)
        if start not in self.available_slots(start, start + timedelta(minutes=1)):
            raise SlotUnavailableError()

        try:
            booking = self.store.create(
                name=name,
                email=email,
                start=start,
                end=start + timedelta(minutes=self.settings.duration_minutes),
                invitee_timezone=invitee_timezone,
            )
        except SlotTakenError as exc:
            raise SlotUnavailableError() from exc

        if self.calendar.enabled:
            try:
                event_id, meet_link = self.calendar.create_event(booking)
            except Exception:
                # Si no se pudo agendar en Google, se libera el horario para no dejar una reserva fantasma.
                self.store.delete(booking.id)
                raise
            booking = self.store.attach_google_event(booking.id, event_id, meet_link)
        return booking


def _valid_timezone(value: str | None) -> str | None:
    if not value or not isinstance(value, str) or len(value) > 64:
        return None
    try:
        ZoneInfo(value)
    except Exception:
        return None
    return value
