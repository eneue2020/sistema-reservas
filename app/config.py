"""Configuración de la aplicación, leída de variables de entorno y del archivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Carga un archivo .env sencillo (CLAVE=valor). Las variables ya definidas tienen prioridad."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "si", "sí", "on"}


def _parse_time(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


def _parse_work_hours(value: str) -> tuple[tuple[time, time], ...]:
    """Convierte "09:00-13:00,14:00-18:00" en bloques (inicio, fin)."""
    blocks = []
    for chunk in value.split(","):
        if not chunk.strip():
            continue
        start_raw, end_raw = chunk.split("-")
        start, end = _parse_time(start_raw), _parse_time(end_raw)
        if end <= start:
            raise ValueError(f"Bloque horario inválido en WORK_HOURS: {chunk!r}")
        blocks.append((start, end))
    return tuple(blocks)


def _parse_weekdays(value: str) -> frozenset[int]:
    """Días ISO: 1 = lunes … 7 = domingo."""
    return frozenset(int(day) for day in value.split(",") if day.strip())


def _parse_dates(value: str) -> frozenset[date]:
    return frozenset(date.fromisoformat(item.strip()) for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # Servidor
    host: str
    port: int
    database_path: Path

    # Profesional y servicio
    professional_name: str
    professional_avatar_url: str
    service_name: str
    service_description: str
    duration_minutes: int
    location: str

    # Disponibilidad
    timezone_name: str
    work_days: frozenset[int]
    work_hours: tuple[tuple[time, time], ...]
    slot_interval_minutes: int
    min_notice_hours: int
    booking_window_days: int
    blocked_dates: frozenset[date]

    # Google Calendar
    google_enabled: bool
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    google_calendar_id: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)

    def public_info(self) -> dict:
        """Datos seguros para enviar al navegador."""
        return {
            "professional": {
                "name": self.professional_name,
                "avatarUrl": self.professional_avatar_url or None,
            },
            "service": {
                "name": self.service_name,
                "description": self.service_description,
                "durationMinutes": self.duration_minutes,
                "location": self.location,
            },
            "timezone": self.timezone_name,
            "bookingWindowDays": self.booking_window_days,
        }


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    database_path = Path(_env("DATABASE_PATH", "data/bookings.db"))
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path

    settings = Settings(
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8000),
        database_path=database_path,
        professional_name=_env("PROFESSIONAL_NAME", "Estela Neue"),
        professional_avatar_url=_env("PROFESSIONAL_AVATAR_URL"),
        service_name=_env("SERVICE_NAME", "Asesoría IA"),
        service_description=_env(
            "SERVICE_DESCRIPTION",
            "Sesión individual para analizar tu caso y descubrir cómo aplicar la "
            "inteligencia artificial en tu negocio o proyecto.",
        ),
        duration_minutes=_env_int("SERVICE_DURATION_MINUTES", 30),
        location=_env("SERVICE_LOCATION", "Google Meet"),
        timezone_name=_env("TIMEZONE", "America/Argentina/Buenos_Aires"),
        work_days=_parse_weekdays(_env("WORK_DAYS", "1,2,3,4,5")),
        work_hours=_parse_work_hours(_env("WORK_HOURS", "09:00-13:00,14:00-18:00")),
        slot_interval_minutes=_env_int("SLOT_INTERVAL_MINUTES", 30),
        min_notice_hours=_env_int("MIN_NOTICE_HOURS", 2),
        booking_window_days=_env_int("BOOKING_WINDOW_DAYS", 60),
        blocked_dates=_parse_dates(_env("BLOCKED_DATES")),
        google_enabled=_env_bool("GOOGLE_CALENDAR_ENABLED"),
        google_client_id=_env("GOOGLE_CLIENT_ID"),
        google_client_secret=_env("GOOGLE_CLIENT_SECRET"),
        google_refresh_token=_env("GOOGLE_REFRESH_TOKEN"),
        google_calendar_id=_env("GOOGLE_CALENDAR_ID", "primary"),
    )
    settings.tz  # Falla al arrancar si la zona horaria no existe.
    return settings


settings = load_settings()
