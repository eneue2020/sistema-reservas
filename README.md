# Sistema de reservas — Asesoría IA con Estela Neue

Aplicación web de reservas al estilo Calendly. El cliente elige un día en el calendario mensual, después una hora libre, completa su nombre y email y confirma la cita.

- **Backend:** Python 3.11 o superior, solo con la biblioteca estándar (no hay que instalar nada).
- **Base de datos:** SQLite (`data/bookings.db`, se crea sola).
- **Frontend:** HTML, CSS y JavaScript sin frameworks.
- **Google Calendar:** la integración ya está escrita y se activa desde `.env`.

## Ejecutar en local

```bash
python server.py
```

Abre <http://127.0.0.1:8000>.

Para cambiar el horario, la duración, el texto del servicio, etc.:

```bash
copy .env.example .env
```

Edita `.env` y reinicia el servidor. Todas las opciones están explicadas dentro del archivo.

Tests:

```bash
python -m unittest discover tests
```

## Funcionalidades

| Funcionalidad | Dónde está |
|---|---|
| Calendario mensual con los días disponibles resaltados | `static/app.js` → `renderCalendar` |
| Horas libres del día elegido, en la zona horaria del cliente (se puede cambiar) | `renderSlots`, `GET /api/slots` |
| Formulario con nombre y email, validado en el cliente y en el servidor | `submitBooking`, `BookingService.book` |
| Pantalla de confirmación con el enlace para añadir la cita a Google Calendar | `showConfirmation` |
| **Bloqueo de reservas duplicadas** | ver más abajo |
| Guardar la reserva en Google Calendar con un enlace de Meet e invitar al cliente | `app/google_calendar.py` |
| Diseño responsive: tres columnas en escritorio, una columna en móvil, modo oscuro automático | `static/styles.css` |

### Cómo se evitan las reservas duplicadas

1. El servidor vuelve a calcular la disponibilidad antes de aceptar una reserva. Así rechaza horarios fuera de la agenda o que ya están ocupados.
2. La reserva se inserta en una transacción `BEGIN IMMEDIATE` que comprueba solapamientos. Si dos personas confirman el mismo horario a la vez, solo una de las dos reservas se guarda (hay un test que lo prueba con 8 hilos).
3. Un índice `UNIQUE` en SQLite actúa como última red de seguridad.
4. Con Google Calendar activado, los eventos que Ivan ya tenga en su calendario también bloquean horarios (consulta *freeBusy*).
5. Si alguien pierde el horario en el último momento, la interfaz se lo avisa y recarga la disponibilidad.

## Estructura

```
server.py                 Punto de entrada
app/
  config.py               Configuración (.env)
  availability.py         Cálculo de horarios libres
  booking_service.py      Reglas de negocio y validación
  db.py                   Persistencia SQLite
  google_calendar.py      Cliente de Google Calendar (OAuth + REST)
  server.py               Servidor HTTP y API JSON
static/                   Frontend (index.html, styles.css, app.js)
scripts/google_auth.py    Asistente para autorizar Google Calendar
tests/                    Tests automatizados
```

### API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/config` | Datos del profesional y del servicio |
| GET | `/api/slots?from=ISO&to=ISO` | Horarios libres en UTC (rango máximo de 45 días) |
| POST | `/api/bookings` | `{name, email, start, timezone}` → `201`. Devuelve `409` si el horario ya está ocupado |

## Conectar con Google Calendar

La app usa OAuth 2.0 con la cuenta de Google de Ivan. Así puede crear eventos con enlace de Google Meet y enviar la invitación al cliente. Una cuenta de servicio no sirve para esto en cuentas personales de Gmail.

### 1. Crear las credenciales en Google Cloud (unos 10 minutos)

1. Entra en <https://console.cloud.google.com/> con la cuenta del calendario y crea un proyecto, por ejemplo "Reservas".
2. En **APIs y servicios → Biblioteca**, busca **Google Calendar API** y pulsa **Habilitar**.
3. En **APIs y servicios → Pantalla de consentimiento de OAuth** (o "Google Auth Platform"):
   - Tipo de usuario: **Externo**. Pon un nombre de app y tu email.
   - En **Usuarios de prueba**, agrega el email de la cuenta del calendario.
4. En **Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **App de escritorio**.
   - Copia el **ID de cliente** y el **Secreto del cliente**.

### 2. Configurar la app

1. Crea el archivo `.env` (`copy .env.example .env`) y pega estos dos valores:
   ```
   GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=xxxxxxxx
   ```
2. Ejecuta el asistente:
   ```bash
   python scripts/google_auth.py
   ```
   Se abre el navegador. Inicia sesión con la cuenta del calendario y acepta los permisos. Google puede avisar de que la app no está verificada; es normal porque la app es tuya: pulsa *Avanzado → Ir a…*. El script guarda `GOOGLE_REFRESH_TOKEN` en `.env` y pone `GOOGLE_CALENDAR_ENABLED=true`.
3. Reinicia el servidor. En la consola debería aparecer `Google Calendar conectado`.

**Permisos que se piden:** `calendar.events` (crear los eventos de las citas) y `calendar.freebusy` (ver qué horas están ocupadas sin leer el contenido de tus eventos).

> **Importante:** mientras la app esté en modo *Prueba* en la pantalla de consentimiento, Google hace caducar el refresh token a los 7 días. Para que funcione de forma permanente, entra en la pantalla de consentimiento y pulsa **Publicar app** (pasa a *En producción*), y después vuelve a ejecutar `scripts/google_auth.py`. Para uso propio no hace falta pasar la verificación de Google.

Para usar un calendario distinto del principal, pon su ID en `GOOGLE_CALENDAR_ID`. Lo encuentras en la configuración del calendario, en "Integrar el calendario".

### Comportamiento con Google activado

- Los horarios ocupados en el Google Calendar de Ivan dejan de mostrarse.
- Cada reserva crea un evento con un enlace de Meet y envía la invitación por email al cliente.
- Si Google falla al crear el evento, la reserva se anula y el cliente recibe un aviso para que lo intente de nuevo, así no quedan citas "fantasma".

## Notas para producción

- No subas el archivo `.env` a ningún repositorio (ya está en `.gitignore`).
- Pon la app detrás de un proxy con HTTPS (Nginx, Caddy, etc.) y cambia `HOST=0.0.0.0`.
- Para ver las reservas guardadas: abre `data/bookings.db` con cualquier visor de SQLite (por ejemplo DB Browser for SQLite).
