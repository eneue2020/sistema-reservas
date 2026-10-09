"""Cliente mínimo de Google Calendar (OAuth 2.0 con refresh token + API REST), sin dependencias.

Se activa con GOOGLE_CALENDAR_ENABLED=true y las credenciales GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET y GOOGLE_REFRESH_TOKEN (ver README y scripts/google_auth.py).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .config import Settings
from .db import Booking, to_iso

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarError(Exception):
    """Fallo al comunicarse con Google Calendar."""


class GoogleCalendarClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._access_token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.settings.google_enabled and self.settings.google_configured

    # --- HTTP -----------------------------------------------------------------

    @staticmethod
    def _http(method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
        request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise GoogleCalendarError(f"Google respondió {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GoogleCalendarError(f"No se pudo conectar con Google: {exc.reason}") from exc

    def _get_access_token(self) -> str:
        with self._token_lock:
            if self._access_token and time.time() < self._token_expiry - 60:
                return self._access_token
            data = urllib.parse.urlencode(
                {
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "refresh_token": self.settings.google_refresh_token,
                    "grant_type": "refresh_token",
                }
            ).encode()
            payload = self._http(
                "POST", TOKEN_URL, data, {"Content-Type": "application/x-www-form-urlencoded"}
            )
            self._access_token = payload["access_token"]
            self._token_expiry = time.time() + int(payload.get("expires_in", 3600))
            return self._access_token

    def _api(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        return self._http(method, url, data, headers)

    @property
    def _calendar_path(self) -> str:
        return "/calendars/" + urllib.parse.quote(self.settings.google_calendar_id, safe="")

    # --- Operaciones ------------------------------------------------------------

    def verify(self) -> None:
        """Comprueba que las credenciales son válidas (se usa al arrancar)."""
        self._get_access_token()

    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Bloques ocupados del calendario del profesional (eventos propios y externos)."""
        calendar_id = self.settings.google_calendar_id
        response = self._api(
            "POST",
            "/freeBusy",
            body={"timeMin": to_iso(start), "timeMax": to_iso(end), "items": [{"id": calendar_id}]},
        )
        calendar = response.get("calendars", {}).get(calendar_id, {})
        if calendar.get("errors"):
            raise GoogleCalendarError(f"Error en freeBusy: {calendar['errors']}")
        return [
            (datetime.fromisoformat(block["start"]), datetime.fromisoformat(block["end"]))
            for block in calendar.get("busy", [])
        ]

    def create_event(self, booking: Booking) -> tuple[str, str | None]:
        """Crea el evento con enlace de Google Meet e invita al cliente. Devuelve (event_id, meet_link)."""
        s = self.settings
        body = {
            # Usar el id de la reserva hace que un reintento no duplique el evento.
            "id": booking.id,
            "summary": f"{s.service_name} con {booking.name}",
            "description": (
                f"Reserva realizada desde la web.\n\n"
                f"Cliente: {booking.name}\n"
                f"Email: {booking.email}\n"
                f"Zona horaria del cliente: {booking.invitee_timezone or 'no indicada'}"
            ),
            "start": {"dateTime": to_iso(booking.start), "timeZone": s.timezone_name},
            "end": {"dateTime": to_iso(booking.end), "timeZone": s.timezone_name},
            "attendees": [{"email": booking.email, "displayName": booking.name}],
            "conferenceData": {
                "createRequest": {
                    "requestId": booking.id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
            "reminders": {"useDefault": True},
        }
        event = self._api(
            "POST",
            f"{self._calendar_path}/events",
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            body=body,
        )
        meet_link = event.get("hangoutLink")
        if not meet_link:
            for entry in event.get("conferenceData", {}).get("entryPoints", []):
                if entry.get("entryPointType") == "video":
                    meet_link = entry.get("uri")
                    break
        return event["id"], meet_link
