"""Autoriza la app a usar tu Google Calendar y guarda el refresh token en .env.

Uso:
    1. Completa GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en el archivo .env.
    2. Ejecuta:  python scripts/google_auth.py
    3. Inicia sesión con la cuenta de Google cuyo calendario recibirá las reservas.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, settings  # noqa: E402

# Debe coincidir exactamente con un "URI de redireccionamiento autorizado" del cliente OAuth.
# Usa un puerto libre: si coincide con el de la app, detén el servidor mientras autorizas.
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip() or "http://localhost:8085/"
PORT = urllib.parse.urlparse(REDIRECT_URI).port or 80
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",  # crear eventos con Meet e invitar al cliente
    "https://www.googleapis.com/auth/calendar.freebusy",  # leer huecos ocupados (sin ver detalles)
]


def update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending:
            lines[index] = f"{key}={pending.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if not settings.google_client_id or not settings.google_client_secret:
        sys.exit("Falta GOOGLE_CLIENT_ID o GOOGLE_CLIENT_SECRET en el archivo .env.")

    state = secrets.token_urlsafe(16)
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )

    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in query or "error" in query:
                if query.get("state", [""])[0] != state:
                    result["error"] = "El parámetro state no coincide."
                elif "error" in query:
                    result["error"] = query["error"][0]
                else:
                    result["code"] = query["code"][0]
                message = "Listo. Ya puedes cerrar esta pestaña y volver a la terminal."
            else:
                message = "Esperando la autorización de Google…"
            body = f"<html><body style='font-family:sans-serif'><p>{message}</p></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", PORT), CallbackHandler)
    print("Abriendo el navegador para autorizar el acceso a Google Calendar…")
    print(f"Si no se abre solo, copia esta URL:\n\n{auth_url}\n")
    webbrowser.open(auth_url)
    while not result:
        server.handle_request()
    server.server_close()

    if "error" in result:
        sys.exit(f"Autorización cancelada o fallida: {result['error']}")

    data = urllib.parse.urlencode(
        {
            "code": result["code"],
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    try:
        with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data, timeout=15) as response:
            tokens = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"Google rechazó el intercambio del código: {exc.read().decode('utf-8', 'replace')}")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        sys.exit(
            "Google no devolvió un refresh token. Revoca el acceso de la app en "
            "https://myaccount.google.com/permissions y vuelve a ejecutar este script."
        )

    update_env_file(BASE_DIR / ".env", {"GOOGLE_REFRESH_TOKEN": refresh_token, "GOOGLE_CALENDAR_ENABLED": "true"})
    print("Refresh token guardado en .env y GOOGLE_CALENDAR_ENABLED=true.")
    print("Reinicia el servidor (python server.py) para empezar a sincronizar las reservas.")


if __name__ == "__main__":
    main()
