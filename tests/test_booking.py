"""Tests de disponibilidad y reservas. Ejecutar: python -m unittest discover tests"""
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.availability import generate_slots
from app.booking_service import BookingService, SlotUnavailableError, ValidationError
from app.config import settings as base_settings
from app.db import BookingStore
from app.google_calendar import GoogleCalendarClient

UTC = timezone.utc
# Lunes 12/10/2026 08:00 en Buenos Aires (UTC-3) = 11:00 UTC.
NOW = datetime(2026, 10, 12, 11, 0, tzinfo=UTC)


def make_settings(**overrides):
    defaults = dict(
        timezone_name="America/Argentina/Buenos_Aires",
        work_days=frozenset({1, 2, 3, 4, 5}),
        work_hours=((datetime.strptime("09:00", "%H:%M").time(), datetime.strptime("12:00", "%H:%M").time()),),
        duration_minutes=30,
        slot_interval_minutes=30,
        min_notice_hours=2,
        booking_window_days=60,
        blocked_dates=frozenset(),
        google_enabled=False,
    )
    defaults.update(overrides)
    return replace(base_settings, **defaults)


class AvailabilityTests(unittest.TestCase):
    def test_respects_work_hours_and_min_notice(self):
        s = make_settings()
        slots = generate_slots(s, NOW, NOW + timedelta(days=1), [], NOW)
        # 09:00–12:00 local = 6 horarios; con 2 h de antelación (desde 10:00) quedan 10:00…11:30.
        local = [slot.astimezone(s.tz).strftime("%H:%M") for slot in slots]
        self.assertEqual(local, ["10:00", "10:30", "11:00", "11:30"])

    def test_skips_weekends_and_blocked_dates(self):
        s = make_settings(blocked_dates=frozenset({date(2026, 10, 13)}))
        slots = generate_slots(s, NOW, NOW + timedelta(days=7), [], NOW)
        days = {slot.astimezone(s.tz).date() for slot in slots}
        self.assertNotIn(date(2026, 10, 13), days)  # bloqueado
        self.assertNotIn(date(2026, 10, 17), days)  # sábado
        self.assertNotIn(date(2026, 10, 18), days)  # domingo
        self.assertIn(date(2026, 10, 14), days)

    def test_busy_intervals_remove_overlapping_slots(self):
        s = make_settings(min_notice_hours=0)
        busy_start = datetime(2026, 10, 12, 13, 15, tzinfo=UTC)  # 10:15 local
        busy = [(busy_start, busy_start + timedelta(minutes=30))]
        slots = generate_slots(s, NOW, NOW + timedelta(days=1), busy, NOW)
        local = [slot.astimezone(s.tz).strftime("%H:%M") for slot in slots]
        self.assertEqual(local, ["09:00", "09:30", "11:00", "11:30"])


class BookingServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = make_settings(min_notice_hours=0, booking_window_days=3650)
        store = BookingStore(Path(self.tmp.name) / "test.db")
        self.service = BookingService(self.settings, store, GoogleCalendarClient(self.settings))
        future = datetime.now(UTC) + timedelta(days=7)
        self.slot = self.service.available_slots(future, future + timedelta(days=7))[0]

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, slot=None, email="ana@example.com"):
        return self.service.book(name="Ana Pérez", email=email, start=slot or self.slot, invitee_timezone="Europe/Madrid")

    def test_booking_removes_slot(self):
        booking = self.book()
        self.assertEqual(booking.end - booking.start, timedelta(minutes=30))
        remaining = self.service.available_slots(self.slot - timedelta(hours=1), self.slot + timedelta(hours=1))
        self.assertNotIn(self.slot, remaining)

    def test_duplicate_booking_is_rejected(self):
        self.book()
        with self.assertRaises(SlotUnavailableError):
            self.book(email="otro@example.com")

    def test_concurrent_bookings_only_one_wins(self):
        results = []

        def attempt(i):
            try:
                self.book(email=f"user{i}@example.com")
                results.append("ok")
            except SlotUnavailableError:
                results.append("taken")

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1)

    def test_rejects_time_outside_schedule(self):
        with self.assertRaises(SlotUnavailableError):
            self.book(slot=self.slot + timedelta(minutes=10))

    def test_validates_input(self):
        with self.assertRaises(ValidationError):
            self.service.book(name="A", email="ana@example.com", start=self.slot, invitee_timezone=None)
        with self.assertRaises(ValidationError):
            self.service.book(name="Ana", email="no-es-email", start=self.slot, invitee_timezone=None)


if __name__ == "__main__":
    unittest.main()
