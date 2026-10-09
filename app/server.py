"""Servidor HTTP: API JSON + archivos estáticos del frontend."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .booking_service import BookingService, SlotUnavailableError, ValidationError
from .config import BASE_DIR, Settings, settings
from .db import BookingStore, to_iso
from .google_calendar import GoogleCalendarClient, GoogleCalendarError

log = logging.getLogger("reservas")

STATIC_DIR = BASE_DIR / "static"
MAX_BODY_BYTES = 10_000
MAX_SLOT_RANGE = timedelta(days=45)
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_datetime(value: str | None, field: str) -> datetime:
    if not value:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Falta el parámetro '{field}'.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Fecha inválida en '{field}'.") from None
    if parsed.tzinfo is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"'{field}' debe incluir zona horaria (ej. ...Z).")
    return parsed.astimezone(timezone.utc)


def make_handler(service: BookingService, settings: Settings, calendar: GoogleCalendarClient):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Reservas/1.0"

        # --- Rutas de la API ---------------------------------------------------

        def get_config(self, query):
            return HTTPStatus.OK, {**settings.public_info(), "googleCalendar": calendar.enabled}

        def get_slots(self, query):
            start = parse_datetime(query.get("from", [None])[0], "from")
            end = parse_datetime(query.get("to", [None])[0], "to")
            if end <= start or end - start > MAX_SLOT_RANGE:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Rango de fechas inválido (máximo 45 días).")
            slots = service.available_slots(start, end)
            return HTTPStatus.OK, {"slots": [to_iso(slot) for slot in slots]}

        def post_booking(self, query):
            data = self._read_json()
            booking = service.book(
                name=data.get("name", ""),
                email=data.get("email", ""),
                start=parse_datetime(data.get("start"), "start"),
                invitee_timezone=data.get("timezone"),
            )
            log.info("Reserva %s creada para %s", booking.id, to_iso(booking.start))
            return HTTPStatus.CREATED, {"booking": booking.to_public()}

        routes = {
            ("GET", "/api/config"): get_config,
            ("GET", "/api/slots"): get_slots,
            ("POST", "/api/bookings"): post_booking,
        }

        # --- Infraestructura ---------------------------------------------------

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method: str):
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                if method == "GET":
                    self._serve_static(url.path)
                else:
                    self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Método no permitido."})
                return

            route = self.routes.get((method, url.path))
            try:
                if route is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
                status, payload = route(self, parse_qs(url.query))
            except ApiError as exc:
                status, payload = exc.status, {"error": exc.message}
            except ValidationError as exc:
                status, payload = HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            except SlotUnavailableError:
                status, payload = HTTPStatus.CONFLICT, {
                    "error": "Ese horario ya no está disponible. Por favor, elige otro."
                }
            except GoogleCalendarError as exc:
                log.error("Error de Google Calendar: %s", exc)
                status, payload = HTTPStatus.BAD_GATEWAY, {
                    "error": "No pudimos conectar con el calendario. Intenta de nuevo en unos minutos."
                }
            except Exception:
                log.exception("Error inesperado en %s %s", method, url.path)
                status, payload = HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Error interno del servidor."}
            self._send_json(status, payload)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Cuerpo de la petición inválido.")
            try:
                data = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON inválido.") from None
            if not isinstance(data, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON inválido.")
            return data

        def _send_json(self, status: HTTPStatus, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_static(self, path: str):
            relative = "index.html" if path in ("", "/") else path.removeprefix("/static/").lstrip("/")
            file_path = (STATIC_DIR / relative).resolve()
            if not file_path.is_relative_to(STATIC_DIR.resolve()) or not file_path.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "No encontrado."})
                return
            body = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            log.debug("%s - %s", self.address_string(), format % args)

    return Handler


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = BookingStore(settings.database_path)
    calendar = GoogleCalendarClient(settings)

    if settings.google_enabled and not settings.google_configured:
        log.warning(
            "GOOGLE_CALENDAR_ENABLED=true pero faltan GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET "
            "o GOOGLE_REFRESH_TOKEN. Las reservas se guardarán solo en local."
        )
    elif calendar.enabled:
        try:
            calendar.verify()
            log.info("Google Calendar conectado (calendario: %s).", settings.google_calendar_id)
        except GoogleCalendarError as exc:
            log.error("No se pudo validar la conexión con Google Calendar: %s", exc)
    else:
        log.info("Google Calendar desactivado: las reservas se guardan solo en la base de datos local.")

    service = BookingService(settings, store, calendar)
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(service, settings, calendar))
    log.info("Aplicación disponible en http://%s:%s", settings.host, settings.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Servidor detenido.")
    finally:
        httpd.server_close()
