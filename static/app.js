// Reserva de citas estilo Calendly — lógica del cliente.

const DAY_MS = 24 * 60 * 60 * 1000;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const $ = (id) => document.getElementById(id);

const el = {
  card: $("card"),
  hostAvatar: $("host-avatar"),
  hostName: $("host-name"),
  serviceName: $("service-name"),
  serviceDuration: $("service-duration"),
  serviceLocation: $("service-location"),
  serviceDescription: $("service-description"),
  hostTimezone: $("host-timezone"),
  selectionSummary: $("selection-summary"),
  selectionText: $("selection-text"),
  steps: { select: $("step-select"), form: $("step-form"), done: $("step-done") },
  monthLabel: $("month-label"),
  prevMonth: $("prev-month"),
  nextMonth: $("next-month"),
  grid: $("calendar-grid"),
  timezoneSelect: $("timezone-select"),
  slotsDate: $("slots-date"),
  slotsNotice: $("slots-notice"),
  slotsList: $("slots-list"),
  backButton: $("back-button"),
  form: $("booking-form"),
  nameInput: $("name-input"),
  emailInput: $("email-input"),
  formError: $("form-error"),
  submitButton: $("submit-button"),
  doneMessage: $("done-message"),
  doneService: $("done-service"),
  doneProfessional: $("done-professional"),
  doneDatetime: $("done-datetime"),
  doneLocation: $("done-location"),
  addToCalendar: $("add-to-calendar"),
  newBooking: $("new-booking"),
};

const state = {
  config: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  viewYear: 0,
  viewMonth: 0, // 0 = enero
  rawSlots: [],
  slotsByDay: new Map(),
  selectedDay: null, // "AAAA-MM-DD" en la zona horaria elegida
  selectedSlot: null, // ISO UTC
  loadToken: 0,
};

// ---------- API ----------

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "Ocurrió un error inesperado. Intenta de nuevo.");
    error.status = response.status;
    throw error;
  }
  return data;
}

// ---------- Fechas ----------

function zonedParts(date, timeZone = state.timezone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone, year: "numeric", month: "numeric", day: "numeric",
  }).formatToParts(date);
  const get = (type) => Number(parts.find((p) => p.type === type).value);
  return { year: get("year"), month: get("month") - 1, day: get("day") };
}

const dayKeyFromParts = (year, month, day) =>
  `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;

function dayKey(date) {
  const { year, month, day } = zonedParts(date);
  return dayKeyFromParts(year, month, day);
}

const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function formatTime(date) {
  return new Intl.DateTimeFormat("es", {
    timeZone: state.timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date);
}

function formatDayKey(key, { capital = true } = {}) {
  const [year, month, day] = key.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  const text = new Intl.DateTimeFormat("es", {
    timeZone: "UTC", weekday: "long", day: "numeric", month: "long", year: "numeric",
  }).format(date);
  return capital ? capitalize(text) : text;
}

function formatRange(startIso) {
  const start = new Date(startIso);
  const end = new Date(start.getTime() + state.config.service.durationMinutes * 60000);
  return `${formatTime(start)} – ${formatTime(end)}, ${formatDayKey(dayKey(start), { capital: false })}`;
}

function timezoneLabel(timeZone) {
  let offset = "";
  try {
    offset = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "shortOffset" })
      .formatToParts(new Date())
      .find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch { /* navegador sin soporte de shortOffset */ }
  return `${timeZone.replaceAll("_", " ")}${offset ? ` (${offset})` : ""}`;
}

// ---------- Panel del profesional ----------

function renderHost() {
  const { professional, service } = state.config;
  const initials = professional.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  if (professional.avatarUrl) {
    const img = document.createElement("img");
    img.src = professional.avatarUrl;
    img.alt = "";
    el.hostAvatar.replaceChildren(img);
  } else {
    el.hostAvatar.textContent = initials;
  }
  el.hostName.textContent = professional.name;
  el.serviceName.textContent = service.name;
  el.serviceDuration.textContent = `${service.durationMinutes} min`;
  el.serviceLocation.textContent = service.location;
  el.serviceDescription.textContent = service.description;
  el.hostTimezone.textContent = timezoneLabel(state.timezone);
  document.title = `Reservar · ${service.name} con ${professional.name}`;
}

function renderTimezoneOptions() {
  let zones = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch { /* navegador antiguo */ }
  const all = new Set([...zones, state.timezone, state.config.timezone]);
  const fragment = document.createDocumentFragment();
  [...all].sort().forEach((zone) => {
    const option = new Option(timezoneLabel(zone), zone, false, zone === state.timezone);
    fragment.append(option);
  });
  el.timezoneSelect.replaceChildren(fragment);
}

// ---------- Calendario ----------

function monthIndex(year, month) { return year * 12 + month; }

function currentMonthIndex() {
  const { year, month } = zonedParts(new Date());
  return monthIndex(year, month);
}

function lastMonthIndex() {
  const limit = new Date(Date.now() + state.config.bookingWindowDays * DAY_MS);
  const { year, month } = zonedParts(limit);
  return monthIndex(year, month);
}

function availableDaysInView() {
  const prefix = dayKeyFromParts(state.viewYear, state.viewMonth, 1).slice(0, 8);
  return [...state.slotsByDay.keys()].filter((key) => key.startsWith(prefix));
}

function renderCalendar() {
  const { viewYear: year, viewMonth: month } = state;
  el.monthLabel.textContent = capitalize(new Intl.DateTimeFormat("es", {
    timeZone: "UTC", month: "long", year: "numeric",
  }).format(new Date(Date.UTC(year, month, 1))));

  const current = monthIndex(year, month);
  el.prevMonth.disabled = current <= currentMonthIndex();
  el.nextMonth.disabled = current >= lastMonthIndex();

  const firstWeekday = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7; // lunes = 0
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const todayKey = dayKey(new Date());
  const fragment = document.createDocumentFragment();

  for (let i = 0; i < firstWeekday; i++) fragment.append(document.createElement("span"));

  for (let day = 1; day <= daysInMonth; day++) {
    const key = dayKeyFromParts(year, month, day);
    const available = state.slotsByDay.has(key);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "day";
    button.textContent = day;
    button.disabled = !available;
    button.classList.toggle("is-available", available);
    button.classList.toggle("is-selected", key === state.selectedDay);
    button.classList.toggle("is-today", key === todayKey);
    button.setAttribute("aria-label", `${formatDayKey(key)}${available ? ", hay horarios disponibles" : ", sin disponibilidad"}`);
    if (key === state.selectedDay) button.setAttribute("aria-pressed", "true");
    button.addEventListener("click", () => selectDay(key));
    fragment.append(button);
  }
  el.grid.replaceChildren(fragment);
}

function groupSlots() {
  state.slotsByDay = new Map();
  for (const iso of state.rawSlots) {
    const key = dayKey(new Date(iso));
    if (!state.slotsByDay.has(key)) state.slotsByDay.set(key, []);
    state.slotsByDay.get(key).push(iso);
  }
}

async function loadMonth() {
  const token = ++state.loadToken;
  // Se pide un margen de 2 días a cada lado para cubrir cualquier zona horaria.
  const from = new Date(Date.UTC(state.viewYear, state.viewMonth, 1) - 2 * DAY_MS);
  const to = new Date(Date.UTC(state.viewYear, state.viewMonth + 1, 1) + 2 * DAY_MS);

  renderCalendar();
  el.grid.classList.add("is-loading");
  try {
    const query = new URLSearchParams({ from: from.toISOString(), to: to.toISOString() });
    const { slots } = await request(`/api/slots?${query}`);
    if (token !== state.loadToken) return;
    state.rawSlots = slots;
  } catch (error) {
    if (token !== state.loadToken) return;
    state.rawSlots = [];
    showSlotsNotice(error.message, true);
  }
  el.grid.classList.remove("is-loading");
  groupSlots();
  if (state.selectedDay && !state.slotsByDay.has(state.selectedDay)) {
    state.selectedDay = null;
    state.selectedSlot = null;
  }
  renderCalendar();
  renderSlots();
}

function changeMonth(delta) {
  const index = monthIndex(state.viewYear, state.viewMonth) + delta;
  state.viewYear = Math.floor(index / 12);
  state.viewMonth = index % 12;
  state.selectedDay = null;
  state.selectedSlot = null;
  hideSlotsNotice();
  return loadMonth();
}

function selectDay(key) {
  state.selectedDay = key;
  state.selectedSlot = null;
  hideSlotsNotice();
  renderCalendar();
  renderSlots();
  if (window.matchMedia("(max-width: 820px)").matches) {
    el.slotsDate.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ---------- Horarios ----------

function renderSlots() {
  if (!state.selectedDay) {
    el.slotsDate.textContent = "Elige un día para ver los horarios disponibles.";
    el.slotsDate.classList.add("is-placeholder");
    el.slotsList.replaceChildren();
    return;
  }

  el.slotsDate.textContent = formatDayKey(state.selectedDay);
  el.slotsDate.classList.remove("is-placeholder");
  const slots = state.slotsByDay.get(state.selectedDay) ?? [];

  if (!slots.length) {
    const empty = document.createElement("p");
    empty.className = "slots__empty";
    empty.textContent = "No quedan horarios disponibles para este día.";
    el.slotsList.replaceChildren(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  let nextButton = null;
  for (const iso of slots) {
    const row = document.createElement("div");
    row.className = "slot";
    const isSelected = iso === state.selectedSlot;
    row.classList.toggle("is-selected", isSelected);

    const time = document.createElement("button");
    time.type = "button";
    time.className = "slot__time";
    time.textContent = formatTime(new Date(iso));
    time.addEventListener("click", () => selectSlot(iso));
    row.append(time);

    if (isSelected) {
      time.setAttribute("aria-pressed", "true");
      nextButton = document.createElement("button");
      nextButton.type = "button";
      nextButton.className = "slot__next";
      nextButton.textContent = "Siguiente";
      nextButton.setAttribute("aria-label", `Continuar con el horario de las ${time.textContent}`);
      nextButton.addEventListener("click", goToForm);
      row.append(nextButton);
    }
    fragment.append(row);
  }
  el.slotsList.replaceChildren(fragment);
  nextButton?.focus();
}

function selectSlot(iso) {
  state.selectedSlot = state.selectedSlot === iso ? null : iso;
  renderSlots();
}

function showSlotsNotice(message, isError = false) {
  el.slotsNotice.textContent = message;
  el.slotsNotice.classList.toggle("notice--error", isError);
  el.slotsNotice.hidden = false;
}

function hideSlotsNotice() { el.slotsNotice.hidden = true; }

// ---------- Pasos ----------

function setStep(step) {
  el.card.dataset.step = step;
  for (const [name, section] of Object.entries(el.steps)) section.hidden = name !== step;
  el.selectionSummary.hidden = step !== "form";
  window.scrollTo({ top: 0 });
}

function goToForm() {
  el.selectionText.textContent = formatRange(state.selectedSlot);
  hideFormError();
  setStep("form");
  el.nameInput.focus();
}

function showFormError(message, field) {
  el.formError.textContent = message;
  el.formError.hidden = false;
  [el.nameInput, el.emailInput].forEach((input) => input.removeAttribute("aria-invalid"));
  if (field) {
    field.setAttribute("aria-invalid", "true");
    field.focus();
  }
}

function hideFormError() {
  el.formError.hidden = true;
  [el.nameInput, el.emailInput].forEach((input) => input.removeAttribute("aria-invalid"));
}

async function submitBooking(event) {
  event.preventDefault();
  const name = el.nameInput.value.trim().replace(/\s+/g, " ");
  const email = el.emailInput.value.trim();

  if (name.length < 2) return showFormError("Ingresa tu nombre.", el.nameInput);
  if (!EMAIL_RE.test(email)) return showFormError("Ingresa un email válido.", el.emailInput);
  hideFormError();

  el.submitButton.disabled = true;
  el.submitButton.textContent = "Confirmando…";
  try {
    const { booking } = await request("/api/bookings", {
      method: "POST",
      body: JSON.stringify({ name, email, start: state.selectedSlot, timezone: state.timezone }),
    });
    showConfirmation(booking);
  } catch (error) {
    if (error.status === 409) {
      // Alguien reservó ese horario mientras tanto: volver al calendario con datos frescos.
      state.selectedSlot = null;
      setStep("select");
      await loadMonth();
      showSlotsNotice(error.message, true);
    } else {
      showFormError(error.message);
    }
  } finally {
    el.submitButton.disabled = false;
    el.submitButton.textContent = "Confirmar reserva";
  }
}

// ---------- Confirmación ----------

function googleCalendarLink(booking) {
  const { professional, service } = state.config;
  const compact = (iso) => iso.replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: `${service.name} con ${professional.name}`,
    dates: `${compact(booking.start)}/${compact(booking.end)}`,
    details: `Reserva confirmada: ${service.name} (${service.durationMinutes} min).`,
    location: booking.meetLink || service.location,
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}

function showConfirmation(booking) {
  const { professional, service } = state.config;
  el.doneService.textContent = `${service.name} · ${service.durationMinutes} min`;
  el.doneProfessional.textContent = professional.name;
  el.doneDatetime.textContent = `${formatRange(booking.start)} · ${timezoneLabel(state.timezone)}`;

  if (booking.meetLink) {
    const link = document.createElement("a");
    link.href = booking.meetLink;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = booking.meetLink;
    el.doneLocation.replaceChildren(`${service.location}: `, link);
  } else {
    el.doneLocation.textContent = service.location;
  }

  el.doneMessage.textContent = booking.googleSynced
    ? `Te enviamos una invitación de calendario a ${booking.email} con el enlace de ${service.location}.`
    : `Tu cita quedó registrada. ${professional.name} te enviará el enlace de ${service.location} a ${booking.email}.`;

  // Si la reserva ya está en Google Calendar el cliente recibe la invitación; si no, se ofrece añadirla.
  el.addToCalendar.hidden = booking.googleSynced;
  el.addToCalendar.href = googleCalendarLink(booking);

  setStep("done");
  $("done-title").focus();
}

function startOver() {
  el.form.reset();
  state.selectedDay = null;
  state.selectedSlot = null;
  hideSlotsNotice();
  setStep("select");
  loadMonth();
}

// ---------- Inicio ----------

async function init() {
  try {
    state.config = await request("/api/config");
  } catch (error) {
    showSlotsNotice("No se pudo cargar la información del servicio. Recarga la página.", true);
    return;
  }

  renderHost();
  renderTimezoneOptions();

  const { year, month } = zonedParts(new Date());
  state.viewYear = year;
  state.viewMonth = month;
  await loadMonth();
  // Si al mes actual ya no le quedan horarios, se salta al siguiente.
  if (!availableDaysInView().length && !el.nextMonth.disabled) await changeMonth(1);
}

el.prevMonth.addEventListener("click", () => changeMonth(-1));
el.nextMonth.addEventListener("click", () => changeMonth(1));
el.timezoneSelect.addEventListener("change", () => {
  state.timezone = el.timezoneSelect.value;
  state.selectedDay = null;
  state.selectedSlot = null;
  el.hostTimezone.textContent = timezoneLabel(state.timezone);
  groupSlots();
  renderCalendar();
  renderSlots();
});
el.backButton.addEventListener("click", () => setStep("select"));
el.form.addEventListener("submit", submitBooking);
el.newBooking.addEventListener("click", startOver);

init();
