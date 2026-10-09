"""Persistencia de reservas en SQLite."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    email            TEXT NOT NULL,
    start_utc        TEXT NOT NULL,
    end_utc          TEXT NOT NULL,
    invitee_timezone TEXT,
    status           TEXT NOT NULL DEFAULT 'confirmed',
    google_event_id  TEXT,
    meet_link        TEXT,
    created_at       TEXT NOT NULL
);
-- Red de seguridad: nunca dos reservas confirmadas que empiecen a la misma hora.
CREATE UNIQUE INDEX IF NOT EXISTS ux_bookings_confirmed_start
    ON bookings (start_utc) WHERE status = 'confirmed';
"""


def to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(ISO_FORMAT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=timezone.utc)


class SlotTakenError(Exception):
    """El horario se solapa con otra reserva confirmada."""


@dataclass
class Booking:
    id: str
    name: str
    email: str
    start: datetime
    end: datetime
    invitee_timezone: str | None
    status: str
    google_event_id: str | None
    meet_link: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Booking":
        return cls(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            start=from_iso(row["start_utc"]),
            end=from_iso(row["end_utc"]),
            invitee_timezone=row["invitee_timezone"],
            status=row["status"],
            google_event_id=row["google_event_id"],
            meet_link=row["meet_link"],
            created_at=from_iso(row["created_at"]),
        )

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "start": to_iso(self.start),
            "end": to_iso(self.end),
            "meetLink": self.meet_link,
            "googleSynced": self.google_event_id is not None,
        }


class BookingStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._write_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: las transacciones se controlan a mano con BEGIN/COMMIT.
        conn = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Reservas confirmadas que se solapan con [start, end)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT start_utc, end_utc FROM bookings "
                "WHERE status = 'confirmed' AND start_utc < ? AND end_utc > ?",
                (to_iso(end), to_iso(start)),
            ).fetchall()
        return [(from_iso(r["start_utc"]), from_iso(r["end_utc"])) for r in rows]

    def create(
        self, *, name: str, email: str, start: datetime, end: datetime, invitee_timezone: str | None
    ) -> Booking:
        """Crea la reserva de forma atómica; lanza SlotTakenError si el horario ya está ocupado."""
        booking = Booking(
            # uuid4().hex solo usa 0-9a-f, válido también como id de evento de Google.
            id=uuid.uuid4().hex,
            name=name,
            email=email,
            start=start,
            end=end,
            invitee_timezone=invitee_timezone,
            status="confirmed",
            google_event_id=None,
            meet_link=None,
            created_at=datetime.now(timezone.utc),
        )
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                clash = conn.execute(
                    "SELECT 1 FROM bookings "
                    "WHERE status = 'confirmed' AND start_utc < ? AND end_utc > ? LIMIT 1",
                    (to_iso(end), to_iso(start)),
                ).fetchone()
                if clash:
                    raise SlotTakenError()
                conn.execute(
                    "INSERT INTO bookings (id, name, email, start_utc, end_utc, invitee_timezone, "
                    "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        booking.id,
                        booking.name,
                        booking.email,
                        to_iso(booking.start),
                        to_iso(booking.end),
                        booking.invitee_timezone,
                        booking.status,
                        to_iso(booking.created_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise SlotTakenError() from exc
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return booking

    def attach_google_event(self, booking_id: str, event_id: str, meet_link: str | None) -> Booking:
        with self._connect() as conn:
            conn.execute(
                "UPDATE bookings SET google_event_id = ?, meet_link = ? WHERE id = ?",
                (event_id, meet_link, booking_id),
            )
            row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return Booking.from_row(row)

    def delete(self, booking_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
