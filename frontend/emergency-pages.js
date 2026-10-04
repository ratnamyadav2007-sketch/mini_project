(() => {
  const state = {
    preview: false,
    contacts: [],
    appointments: [],
    reminders: [],
    calendarView: "month",
    calendarDate: new Date(),
    countdownTimer: 0,
    countdown: 5,
    holdArmed: false,
    location: null,
    locationText: "Location has not been requested.",
    deliveryStatus: "",
    sosPollTimer: 0,
    sosEventId: "",
    publicCardUrl: "",
    sosPollAttempts: 0,
    editingContactId: "",
    familyRecipients: [],
  };
  const demoContacts = [
    { id: "demo-contact-1", profileId: "preview-maya", name: "Riya Patel", relationship: "Parent", phone: "+1 555 010 2741", priority: 1, visibility: "family" },
    { id: "demo-contact-2", profileId: "preview-maya", name: "Dr. Lee", relationship: "Family clinic", phone: "+1 555 010 9012", priority: 2, visibility: "family" },
  ];
  const demoAppointments = [
    { id: "demo-appt-1", profileId: "preview-maya", title: "School health review", date: "2026-10-12", details: { provider: "School clinic", location: "Room 4", time: "09:30", reminderMinutes: "30", reminderStatus: "pending" }, visibility: "family", profileName: "Maya Patel" },
    { id: "demo-appt-2", profileId: "preview-riya", title: "Annual visit", date: "2026-10-20", details: { provider: "Dr. Lee", time: "14:00", reminderMinutes: "60", reminderStatus: "pending" }, visibility: "family", profileName: "Riya Patel" },
  ];

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  async function request(path, options = {}) {
    const { apiRequest } = await import("./js/api.js");
    return apiRequest(path, options);
  }

  async function refreshEmergencyCardCache(profileId) {
    if (!profileId || state.preview) return;
    await request(`/profiles/${encodeURIComponent(profileId)}/emergency-card`);
  }

  function activeProfile() {
    return window.HealthPages.activeProfile();
  }

  function pageHeader(kicker, title, copy = "") {
    return `<header class="page-header"><p class="page-kicker">${kicker}</p><h1 class="page-title">${title}</h1>${copy ? `<p class="page-lede">${copy}</p>` : ""}</header>`;
  }

  function showToast(message, kind = "success") {
    const region = document.querySelector("#health-toasts");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `shell-toast${kind === "error" ? " error" : ""}`;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");
    toast.textContent = message;
    region.append(toast);
    window.setTimeout(() => toast.remove(), 4000);
  }

  function formatDate(date) {
    return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  async function bootstrap(preview) {
    state.preview = preview;
  }

  function renderContacts() {
    const profile = activeProfile();
    if (!profile) return `${pageHeader("Safety / Trusted contacts", "Add a health profile first.", "Emergency contacts belong to a family health profile.")}<a class="primary-action" href="#/onboarding">Add profile</a>`;
    const cards = state.contacts.map((contact, index) => `<li class="emergency-contact-row"><span class="contact-priority">${index + 1}</span><span class="contact-avatar">${escapeHtml(contact.name.split(/\s+/).map(part => part[0]).slice(0, 2).join("").toUpperCase())}</span><div class="contact-identity"><strong>${escapeHtml(contact.name)}</strong><small>${escapeHtml(contact.relationship)} / ${escapeHtml(contact.phone)}</small></div><span class="status-pill visibility-${escapeHtml(contact.visibility || "private")}">${escapeHtml(contact.visibility || "private")}</span><div class="contact-actions"><button class="icon-button" type="button" aria-label="Move ${escapeHtml(contact.name)} up" data-contact-move="up" data-contact-id="${escapeHtml(contact.id)}" ${index === 0 ? "disabled" : ""}><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-up"></use></svg></button><button class="icon-button" type="button" aria-label="Move ${escapeHtml(contact.name)} down" data-contact-move="down" data-contact-id="${escapeHtml(contact.id)}" ${index === state.contacts.length - 1 ? "disabled" : ""}><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-down"></use></svg></button><button class="icon-button contact-remove" type="button" aria-label="Remove ${escapeHtml(contact.name)}" data-contact-remove="${escapeHtml(contact.id)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#trash"></use></svg></button></div></li>`).join("");
    const members = (state.familyRecipients || []).filter(member => member.email !== state.currentEmail);
    return `${pageHeader("Safety / Trusted people", "Emergency contacts", `Put the most helpful person first for ${escapeHtml(profile.name)}. Changes remain on this local account.`)}
      <div class="emergency-contact-layout"><section class="safety-panel"><div class="safety-panel-heading"><div><h2>Contact priority</h2><p>Top to bottom is the order shown in the emergency flow.</p></div><span class="status-pill">${state.contacts.length} saved</span></div><ol class="emergency-contact-list">${cards || '<li class="contact-empty">No emergency contacts yet. Add someone you trust.</li>'}</ol></section>
      <section class="safety-panel"><div class="safety-panel-heading"><div><h2>Add a contact</h2><p>Use a number that can receive calls in your region.</p></div></div><form class="health-form contact-form" data-contact-form><input type="hidden" name="profileId" value="${escapeHtml(profile.id)}"><div class="health-field"><label for="contact-name">Full name</label><input class="health-input" id="contact-name" name="name" required maxlength="100"></div><div class="health-field"><label for="contact-relationship">Relationship</label><input class="health-input" id="contact-relationship" name="relationship" required maxlength="60" placeholder="Parent, neighbour, clinician"></div><div class="health-field"><label for="contact-phone">Phone number</label><input class="health-input" id="contact-phone" name="phone" type="tel" autocomplete="tel" required placeholder="+1 555 010 1234"></div><div class="health-field"><label for="contact-visibility">Who can see this</label><select class="health-input" id="contact-visibility" name="visibility"><option value="private">Private / only me</option><option value="selected">Selected family members</option><option value="family">Family care circle</option></select></div><fieldset class="contact-share-field" id="contact-share-field" hidden><legend>Share with</legend>${members.map(member => `<label class="check-label"><input type="checkbox" name="selectedMemberEmails" value="${escapeHtml(member.email)}"><span>${escapeHtml(member.displayName)}</span></label>`).join("") || '<span class="field-hint">Invite another account first.</span>'}</fieldset><p class="health-error" id="contact-form-error" role="alert"></p><button class="primary-action" type="submit">Save contact</button></form></section></div>`;
  }

  function monthGrid(date) {
    const monthStart = new Date(date.getFullYear(), date.getMonth(), 1);
    const mondayOffset = (monthStart.getDay() + 6) % 7;
    const start = new Date(date.getFullYear(), date.getMonth(), 1 - mondayOffset);
    const days = Array.from({ length: 42 }, (_, index) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + index));
    return `<div class="calendar-weekdays" aria-hidden="true">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => `<span>${day}</span>`).join("")}</div><div class="calendar-grid month-grid">${days.map(day => dayCell(day, date)).join("")}</div>`;
  }

  function weekGrid(date) {
    const offset = (date.getDay() + 6) % 7;
    const start = new Date(date.getFullYear(), date.getMonth(), date.getDate() - offset);
    const days = Array.from({ length: 7 }, (_, index) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + index));
    return `<div class="calendar-weekdays" aria-hidden="true">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => `<span>${day}</span>`).join("")}</div><div class="calendar-grid week-grid">${days.map(day => dayCell(day, date)).join("")}</div>`;
  }

  function dayCell(day, viewingDate) {
    const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    const items = state.appointments.filter(appointment => appointment.date === key);
    const sameMonth = day.getMonth() === viewingDate.getMonth();
    const dateLabel = day.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
    return `<button type="button" class="calendar-day ${sameMonth ? "" : "outside-month"} ${key === new Date().toISOString().slice(0, 10) ? "today" : ""}" data-calendar-date="${key}" aria-label="${dateLabel}${items.length ? `, ${items.length} appointments` : ""}"><span class="calendar-day-number">${day.getDate()}</span>${items.slice(0, 2).map(item => `<span class="calendar-event">${escapeHtml(item.details?.time || "")} ${escapeHtml(item.title)}</span>`).join("")}${items.length > 2 ? `<span class="calendar-more">+${items.length - 2} more</span>` : ""}</button>`;
  }

  function appointmentStatus(appointment) {
    const status = appointment.details?.reminderStatus || "scheduled";
    return status === "completed" ? "Completed" : status === "cancelled" ? "Cancelled" : status === "snoozed" ? "Snoozed" : status === "sent" ? "Reminder sent" : "Scheduled";
  }

  function appointmentList() {
    return state.appointments.map(item => {
      const reminderId = item.details?.reminderId || "";
      const startsAt = item.starts_at ? new Date(item.starts_at) : new Date(`${item.date}T${item.details?.time || "12:00"}`);
      const localStartsAt = new Date(startsAt.getTime() - startsAt.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      const startsAtValue = item.starts_at || startsAt.toISOString();
      return `<li class="appointment-row"><div class="appointment-when"><time datetime="${escapeHtml(startsAtValue)}">${formatDate(item.date)} / ${escapeHtml(item.details?.time || "")}</time><span class="status-pill ${item.details?.reminderStatus === "completed" ? "neutral" : item.details?.reminderStatus === "snoozed" ? "warning" : "info"}">${appointmentStatus(item)}</span></div><div class="appointment-info"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.profileName || activeProfile()?.name || "Family")}${item.details?.location ? ` / ${escapeHtml(item.details.location)}` : ""}</small></div><div class="appointment-actions">${reminderId && ["pending", "sent"].includes(item.details?.reminderStatus) ? `<button type="button" class="secondary-action" data-reminder-action="snooze" data-reminder-id="${escapeHtml(reminderId)}">Snooze</button><button type="button" class="secondary-action" data-reminder-action="complete" data-reminder-id="${escapeHtml(reminderId)}">Complete reminder</button>` : ""}<details class="appointment-edit"><summary class="secondary-action">Edit</summary><form class="health-form" data-appointment-edit-form data-appointment-id="${escapeHtml(item.id)}"><div class="health-field"><label>Appointment</label><input class="health-input" name="title" required maxlength="160" value="${escapeHtml(item.title)}"></div><div class="health-field"><label>Date and time</label><input class="health-input" name="starts_at" type="datetime-local" required value="${localStartsAt}"></div><div class="health-field"><label>Location</label><input class="health-input" name="location" maxlength="255" value="${escapeHtml(item.location || item.details?.location || "")}"></div><div class="health-field"><label>Notes</label><input class="health-input" name="notes" maxlength="4000" value="${escapeHtml(item.notes || "")}"></div><button class="primary-action" type="submit">Save changes</button></form></details><button type="button" class="secondary-action" data-appointment-delete="${escapeHtml(item.id)}">Delete</button></div></li>`;
    }).join("");
  }

  function reminderList() {
    return state.reminders.map(reminder => {
      const localDue = new Date(new Date(reminder.due_at).getTime() - new Date(reminder.due_at).getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      return `<li class="appointment-row"><div class="appointment-when"><time datetime="${escapeHtml(reminder.due_at)}">${escapeHtml(new Date(reminder.due_at).toLocaleString())}</time><span class="status-pill ${reminder.status === "completed" ? "neutral" : reminder.snoozed_until ? "warning" : "info"}">${escapeHtml(reminder.status)}</span></div><div class="appointment-info"><strong>${escapeHtml(reminder.title)}</strong><small>${escapeHtml(reminder.recurrence === "none" ? "One-time reminder" : `Repeats ${reminder.recurrence}`)}</small></div><div class="appointment-actions">${["pending", "sent"].includes(reminder.status) ? `<button type="button" class="secondary-action" data-reminder-action="snooze" data-reminder-id="${escapeHtml(reminder.id)}">Snooze</button><button type="button" class="secondary-action" data-reminder-action="complete" data-reminder-id="${escapeHtml(reminder.id)}">Complete</button>` : ""}<details class="appointment-edit"><summary class="secondary-action">Edit</summary><form class="health-form" data-reminder-edit-form data-reminder-id="${escapeHtml(reminder.id)}"><div class="health-field"><label>Reminder</label><input class="health-input" name="title" required maxlength="160" value="${escapeHtml(reminder.title)}"></div><div class="health-field"><label>Due date and time</label><input class="health-input" name="due_at" type="datetime-local" required value="${localDue}"></div><div class="health-field"><label>Repeat</label><select class="health-input" name="recurrence"><option value="none" ${reminder.recurrence === "none" ? "selected" : ""}>Does not repeat</option><option value="daily" ${reminder.recurrence === "daily" ? "selected" : ""}>Daily</option><option value="weekly" ${reminder.recurrence === "weekly" ? "selected" : ""}>Weekly</option><option value="monthly" ${reminder.recurrence === "monthly" ? "selected" : ""}>Monthly</option></select></div><button class="primary-action" type="submit">Save changes</button></form></details><button type="button" class="secondary-action" data-reminder-delete="${escapeHtml(reminder.id)}">Delete</button></div></li>`;
    }).join("");
  }

  function renderAppointments() {
    const profile = activeProfile();
    if (!profile) return `${pageHeader("Care planning / Appointments", "Add a health profile first.", "Appointments are attached to a family profile.")}<a class="primary-action" href="#/onboarding">Add profile</a>`;
    const monthName = state.calendarDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    return `<div class="appointments-page">${pageHeader("Care planning / Reminders", "Appointments", "Put the next visit in the calendar. Set a reminder, snooze it, or mark it done.")}
      <div class="appointments-toolbar"><div class="calendar-profile"><span class="page-kicker">Calendar for</span><strong>${escapeHtml(profile.name)}</strong></div><div class="calendar-view-switch" role="group" aria-label="Calendar view"><button class="zoom-button" type="button" data-calendar-view="month" aria-pressed="${state.calendarView === "month"}">Month</button><button class="zoom-button" type="button" data-calendar-view="week" aria-pressed="${state.calendarView === "week"}">Week</button></div><a class="secondary-action" href="/api/v1/profiles/${encodeURIComponent(profile.id)}/appointments.ics?profile_id=${encodeURIComponent(profile.id)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#download"></use></svg>Export .ics</a></div>
      <section class="safety-panel calendar-panel"><div class="calendar-heading"><button type="button" class="icon-button" data-calendar-move="previous" aria-label="Previous ${state.calendarView}"><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-left"></use></svg></button><h2>${monthName}</h2><button type="button" class="icon-button" data-calendar-move="next" aria-label="Next ${state.calendarView}"><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-right"></use></svg></button><button type="button" class="secondary-action" data-calendar-today>Today</button></div>${state.calendarView === "month" ? monthGrid(state.calendarDate) : weekGrid(state.calendarDate)}</section>
      <section class="safety-panel appointment-list-panel"><div class="safety-panel-heading"><div><h2>Appointments</h2><p>${state.appointments.length} on this profile</p></div><a class="secondary-action" href="#health-appointment-form">Add appointment</a></div><ul class="appointment-list">${appointmentList() || '<li class="contact-empty">No appointments yet. Add one to start planning.</li>'}</ul></section>
      <section class="safety-panel appointment-list-panel"><div class="safety-panel-heading"><div><h2>Reminders</h2><p>${state.reminders.length} reminders on this profile</p></div></div><ul class="appointment-list">${reminderList() || '<li class="contact-empty">No reminders yet.</li>'}</ul></section>
      <details class="appointment-create" id="health-appointment-form"><summary><svg class="icon" aria-hidden="true"><use href="icons.svg#plus"></use></svg>Create appointment</summary><form class="health-form appointment-form" data-appointment-form><div class="appointment-form-grid"><div class="health-field"><label for="appointment-title">Appointment</label><input class="health-input" id="appointment-title" name="title" required maxlength="160" placeholder="Annual visit"></div><div class="health-field"><label for="appointment-date">Date</label><input class="health-input" id="appointment-date" name="date" type="date" required></div><div class="health-field"><label for="appointment-time">Time</label><input class="health-input" id="appointment-time" name="time" type="time" required></div><div class="health-field"><label for="appointment-location">Location <span class="optional-label">Optional</span></label><input class="health-input" id="appointment-location" name="location" maxlength="255"></div><div class="health-field"><label for="appointment-reminder">Reminder</label><select class="health-input" id="appointment-reminder" name="reminderMinutes"><option value="0">No reminder</option><option value="5">5 minutes before</option><option value="10">10 minutes before</option><option value="15">15 minutes before</option><option value="30" selected>30 minutes before</option><option value="60">1 hour before</option><option value="1440">1 day before</option></select></div><div class="health-field"><label for="appointment-visibility">Who can see this</label><select class="health-input" id="appointment-visibility" name="visibility"><option value="family">Family care circle</option><option value="private">Private / only me</option></select></div><div class="health-field"><label for="appointment-note">Note <span class="optional-label">Optional</span></label><input class="health-input" id="appointment-note" name="notes" maxlength="4000"></div></div><p class="health-error" id="appointment-error" role="alert"></p><button class="primary-action" type="submit">Save appointment</button></form></details>
      <details class="appointment-create"><summary><svg class="icon" aria-hidden="true"><use href="icons.svg#plus"></use></svg>Create standalone reminder</summary><form class="health-form" data-reminder-form><div class="health-field"><label for="reminder-title">Reminder</label><input class="health-input" id="reminder-title" name="title" required maxlength="160"></div><div class="health-field"><label for="reminder-due">Due date and time</label><input class="health-input" id="reminder-due" name="due_at" type="datetime-local" required></div><div class="health-field"><label for="reminder-recurrence">Repeat</label><select class="health-input" id="reminder-recurrence" name="recurrence"><option value="none">Does not repeat</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></div><p class="health-error" id="reminder-error" role="alert"></p><button class="primary-action" type="submit">Save reminder</button></form></details></div>`;
  }

  function renderEmergencyContacts() {
    const profile = activeProfile();
    if (!profile) return `${pageHeader("Safety / Trusted contacts", "Add a profile first.", "Emergency contacts are attached to a family profile.")}<a class="primary-action" href="#/onboarding">Add profile</a>`;
    const contacts = state.contacts.map((contact, index) => `<li class="emergency-contact-row"><span class="contact-priority">${index + 1}</span><span class="contact-avatar">${escapeHtml(contact.name.split(/\s+/).map(part => part[0]).slice(0, 2).join("").toUpperCase())}</span><div class="contact-identity"><strong>${escapeHtml(contact.name)}</strong><small>${escapeHtml(contact.relationship)} / ${escapeHtml(contact.phone)}</small></div><a class="contact-call" href="tel:${encodeURIComponent(contact.phone)}" aria-label="Call ${escapeHtml(contact.name)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#phone"></use></svg></a><div class="contact-actions"><button class="icon-button" type="button" aria-label="Edit ${escapeHtml(contact.name)}" data-contact-edit="${escapeHtml(contact.id)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#edit"></use></svg></button><button class="icon-button" type="button" aria-label="Move ${escapeHtml(contact.name)} up" data-contact-move="up" data-contact-id="${escapeHtml(contact.id)}" ${index === 0 ? "disabled" : ""}><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-up"></use></svg></button><button class="icon-button" type="button" aria-label="Move ${escapeHtml(contact.name)} down" data-contact-move="down" data-contact-id="${escapeHtml(contact.id)}" ${index === state.contacts.length - 1 ? "disabled" : ""}><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-down"></use></svg></button><button class="icon-button contact-remove" type="button" aria-label="Remove ${escapeHtml(contact.name)}" data-contact-remove="${escapeHtml(contact.id)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#trash"></use></svg></button></div></li>`).join("");
    return `<div class="emergency-contacts-page">${pageHeader("Safety / Trusted people", "Emergency contacts", `Put the most helpful person first for ${escapeHtml(profile.name)}. Changes remain on this local account.`)}
      <div class="emergency-contact-layout"><section class="safety-panel"><div class="safety-panel-heading"><div><h2>Contact priority</h2><p>Top to bottom is the order shown in the emergency flow.</p></div><span class="status-pill">${state.contacts.length} saved</span></div><ol class="emergency-contact-list">${contacts || '<li class="contact-empty">No emergency contacts yet. Add someone you trust.</li>'}</ol></section>
      <section class="safety-panel"><div class="safety-panel-heading"><div><h2>${state.editingContactId ? "Edit contact" : "Add a contact"}</h2><p>Use a number that can receive calls in your region.</p></div></div><form class="health-form contact-form" data-contact-form><input type="hidden" name="id" value="${escapeHtml(state.editingContactId)}"><input type="hidden" name="profileId" value="${escapeHtml(profile.id)}"><div class="health-field"><label for="contact-name">Full name</label><input class="health-input" id="contact-name" name="name" required maxlength="120" value="${escapeHtml(state.contacts.find(item => item.id === state.editingContactId)?.name || "")}"></div><div class="health-field"><label for="contact-relationship">Relationship</label><input class="health-input" id="contact-relationship" name="relationship" required maxlength="64" placeholder="Parent, neighbour, clinician" value="${escapeHtml(state.contacts.find(item => item.id === state.editingContactId)?.relationship || "")}"></div><div class="health-field"><label for="contact-phone">Phone number</label><input class="health-input" id="contact-phone" name="phone_number" type="tel" autocomplete="tel" required maxlength="32" placeholder="+1 555 010 1234" value="${escapeHtml(state.contacts.find(item => item.id === state.editingContactId)?.phone || "")}"></div><div class="health-field"><label for="contact-email">Email <span class="optional-label">Optional</span></label><input class="health-input" id="contact-email" name="email" type="email" maxlength="320" value="${escapeHtml(state.contacts.find(item => item.id === state.editingContactId)?.email || "")}"></div><p class="health-error" id="contact-form-error" role="alert"></p><button class="primary-action" type="submit">${state.editingContactId ? "Update contact" : "Save contact"}</button>${state.editingContactId ? '<button class="secondary-action" type="button" data-contact-edit-cancel>Cancel</button>' : ""}</form></section></div></div>`;
  }

  function renderSos() {
    const profile = activeProfile();
    if (!profile) return `${pageHeader("Urgent help", "Add a profile to set up emergency help.", "Add a family health profile to continue.")}<a class="primary-action" href="#/onboarding">Add profile</a>`;
    const contacts = state.contacts.map((contact, index) => `<li class="sos-contact-row"><span class="sos-contact-rank">${index + 1}</span><span><strong>${escapeHtml(contact.name)}</strong><small>${escapeHtml(contact.relationship)}</small></span><a class="sos-call-link" href="tel:${encodeURIComponent(contact.phone)}" aria-label="Call ${escapeHtml(contact.name)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#phone"></use></svg>Call</a></li>`).join("");
    const members = window.HealthPages.memberProfiles();
    return `<div class="sos-page">${pageHeader(`Emergency / ${escapeHtml(profile.name)}`, "What do you need right now?", "For immediate danger, call your local emergency number. This prototype does not dispatch emergency services.")}
      <div class="sos-banner"><span class="sos-mark"><svg class="icon" aria-hidden="true"><use href="icons.svg#alert-triangle"></use></svg></span><div><h2>Hold to start the emergency demo.</h2><p>A five-second cancel window follows. Location sharing is optional and requires your permission.</p></div></div>
      <div class="sos-demo-panel"><button class="sos-hold-button" type="button" data-sos-start aria-describedby="sos-hold-help"><svg class="icon" aria-hidden="true"><use href="icons.svg#phone"></use></svg><span>Start urgent-help alert</span></button><p id="sos-hold-help">A five-second cancel window starts before the alert is sent. Demo notifications are logged locally.</p><div class="sos-live-status" id="sos-feedback" role="status" aria-live="polite">Ready / No alert has been sent.</div></div>
      <section class="sos-panel"><div class="safety-panel-heading"><div><h2>Priority contacts</h2><p>${state.contacts.length ? "Call a trusted person directly." : "Add and order trusted contacts first."}</p></div><a class="secondary-action" href="#/emergency-contacts">Manage contacts</a></div><ol class="sos-contact-list">${contacts || '<li class="contact-empty">No priority contacts yet.</li>'}</ol></section>
      <div class="sos-card-link"><span><strong>Emergency health card</strong><small>High-contrast, printable, and available offline after first load.</small></span><a class="secondary-action" href="/health-card.html?profileId=${encodeURIComponent(profile.id)}">Open card <svg class="icon" aria-hidden="true"><use href="icons.svg#arrow-right"></use></svg></a></div>
      <div class="sos-target-profile"><label for="sos-profile-select">Emergency card profile</label><select id="sos-profile-select" class="health-input" aria-label="Emergency card profile">${members.map(member => `<option value="${escapeHtml(member.id)}" ${member.id === profile.id ? "selected" : ""}>${escapeHtml(member.name)}</option>`).join("")}</select></div></div>`;
  }

  function renderPage(route) {
    if (route === "/sos") return `<div id="emergency-root">${renderSos()}</div>`;
    if (route === "/emergency-contacts") return `<div id="emergency-root">${renderEmergencyContacts()}</div>`;
    if (route === "/appointments") return `<div id="appointments-root">${renderAppointments()}</div>`;
    return `${pageHeader("Safety", "Page not found.")}`;
  }

  async function refreshContacts() {
    if (state.preview) {
      state.contacts = demoContacts.filter(contact => contact.profileId === activeProfile()?.id).sort((left, right) => left.priority - right.priority);
      return;
    }
    const profile = activeProfile();
    if (!profile) return;
    const data = await request(`/profiles/${encodeURIComponent(profile.id)}/emergency-contacts`);
    state.contacts = data.map(contact => ({
      ...contact,
      phone: contact.phone_number,
    }));
  }

  async function refreshAppointments() {
    if (state.preview) {
      state.appointments = demoAppointments.filter(item => item.profileId === activeProfile()?.id || item.visibility === "family");
      return;
    }
    const profile = activeProfile();
    if (!profile) return;
    const first = state.calendarView === "month"
      ? new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth(), 1 - ((new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth(), 1).getDay() + 6) % 7))
      : new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth(), state.calendarDate.getDate() - ((state.calendarDate.getDay() + 6) % 7));
    const last = state.calendarView === "month"
      ? new Date(first.getFullYear(), first.getMonth(), first.getDate() + 42)
      : new Date(first.getFullYear(), first.getMonth(), first.getDate() + 7);
    const result = await request(`/appointments?profile_id=${encodeURIComponent(profile.id)}&from=${encodeURIComponent(first.toISOString())}&to=${encodeURIComponent(last.toISOString())}`);
    const reminders = await request(`/profiles/${encodeURIComponent(profile.id)}/reminders`);
    state.reminders = reminders;
    state.appointments = result.map(item => {
      const startsAt = new Date(item.starts_at);
      const reminder = reminders.find(candidate => candidate.appointment_id === item.id);
      return {
        ...item,
        date: `${startsAt.getFullYear()}-${String(startsAt.getMonth() + 1).padStart(2, "0")}-${String(startsAt.getDate()).padStart(2, "0")}`,
        details: {
          time: startsAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          location: item.location,
          reminderStatus: reminder?.snoozed_until ? "snoozed" : reminder?.status || item.status,
          reminderId: reminder?.id,
        },
        profileName: profile.name,
      };
    });
  }

  async function refreshRoute(route) {
    if (!["/sos", "/emergency-contacts", "/appointments"].includes(route)) return;
    const container = route === "/appointments" ? document.querySelector("#appointments-root") : document.querySelector("#emergency-root");
    if (!container || state.preview) {
      if (state.preview) container.innerHTML = renderPage(route);
      return;
    }
    try {
      if (route === "/appointments") await refreshAppointments();
      else await refreshContacts();
      container.innerHTML = renderPage(route);
    } catch (error) {
      container.innerHTML = `<div class="timeline-empty"><h2>Safety information could not load.</h2><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  function setSosStatus(text, kind = "") {
    const status = document.querySelector("#sos-feedback");
    if (!status) return;
    status.className = `sos-live-status${kind ? ` ${kind}` : ""}`;
    status.textContent = text;
  }

  function clearSosCountdown() {
    window.clearInterval(state.countdownTimer);
    state.countdownTimer = 0;
  }

  function cancelSosDemo() {
    clearSosCountdown();
    state.holdArmed = false;
    state.location = null;
    state.deliveryStatus = "";
    state.locationText = "No location was shared.";
    const page = document.querySelector("#emergency-root");
    if (page) page.innerHTML = renderSos();
    setSosStatus("Cancelled. No alert was sent.", "cancelled");
  }

  function renderSosDelivery(event, eventStatus, kind = "delivered") {
    const deliveries = event.notifications.map(item => `${item.recipient}: ${item.status}`).join("; ");
    const status = document.querySelector("#sos-feedback");
    if (!status) return;
    status.className = `sos-live-status ${kind}`;
    status.innerHTML = `<strong>${escapeHtml(eventStatus)}</strong><span>${escapeHtml(deliveries || "No notification recipients are configured.")}</span><span>${escapeHtml(state.locationText)}</span><a class="secondary-action" target="_blank" rel="noreferrer" href="${escapeHtml(state.publicCardUrl)}">Open public emergency card</a><button type="button" class="secondary-action" data-reset-sos>Done</button>`;
  }

  async function pollSosStatus() {
    if (!state.sosEventId) return;
    try {
      const event = await request(`/sos/${encodeURIComponent(state.sosEventId)}`);
      state.sosPollAttempts += 1;
      const complete = event.notifications.every(item => item.status !== "pending");
      if (complete) {
        window.clearInterval(state.sosPollTimer);
        state.sosPollTimer = 0;
        state.deliveryStatus = "delivered";
        renderSosDelivery(event, event.notifications.every(item => item.status === "delivered")
          ? "SOS alert sent."
          : "Some notifications were not delivered.");
      } else {
        if (state.sosPollAttempts >= 30) {
          window.clearInterval(state.sosPollTimer);
          state.sosPollTimer = 0;
          state.deliveryStatus = "pending";
          renderSosDelivery(event, "Alert created; notification delivery is still pending.", "warning");
        } else {
          setSosStatus("Alert created. Waiting for notification delivery...");
        }
      }
    } catch (error) {
      window.clearInterval(state.sosPollTimer);
      state.sosPollTimer = 0;
      const status = document.querySelector("#sos-feedback");
      if (status) {
        status.className = "sos-live-status error";
        status.innerHTML = `<strong>Alert created, but delivery status could not be loaded: ${escapeHtml(error.message)}</strong><a class="secondary-action" target="_blank" rel="noreferrer" href="${escapeHtml(state.publicCardUrl)}">Open public emergency card</a><button type="button" class="secondary-action" data-reset-sos>Done</button>`;
      }
    }
  }

  async function sendSos() {
    clearSosCountdown();
    document.querySelector("[data-cancel-sos]")?.remove();
    const countdownCopy = document.querySelector(".sos-countdown-copy");
    if (countdownCopy) countdownCopy.textContent = "Sending the alert...";
    if (state.preview) {
      state.holdArmed = false;
      setSosStatus("Preview only: no alert or notification was sent.", "cancelled");
      document.querySelector("#sos-feedback")?.insertAdjacentHTML("beforeend", '<button type="button" class="secondary-action" data-reset-sos>Reset preview</button>');
      return;
    }
    setSosStatus("Creating the emergency alert...");
    try {
      const payload = { profile_id: activeProfile().id };
      if (state.location) payload.location = state.location;
      const event = await request("/sos", { method: "POST", body: payload });
      state.sosEventId = event.id;
      state.publicCardUrl = event.public_page_url;
      state.sosPollAttempts = 0;
      const hasPending = event.notifications.some(item => item.status === "pending");
      if (!hasPending) {
        state.deliveryStatus = "delivered";
        renderSosDelivery(event, "SOS alert sent.");
        return;
      }
      setSosStatus("Alert created. Waiting for notification delivery...");
      await pollSosStatus();
      if (!state.deliveryStatus) state.sosPollTimer = window.setInterval(() => void pollSosStatus(), 2000);
    } catch (error) {
      state.holdArmed = false;
      const status = document.querySelector("#sos-feedback");
      if (status) {
        status.className = "sos-live-status error";
        status.innerHTML = `<strong>The alert could not be sent: ${escapeHtml(error.message)}</strong><button type="button" class="secondary-action" data-reset-sos>Try again</button>`;
      }
    }
  }

  function requestLocation() {
    state.locationText = "Location permission not requested.";
    state.location = null;
    if (!navigator.geolocation) {
      state.locationText = "Location is not available in this browser.";
      return;
    }
    state.locationText = "Waiting for location permission...";
    navigator.geolocation.getCurrentPosition(position => {
      if (!state.holdArmed) return;
      state.location = { latitude: position.coords.latitude, longitude: position.coords.longitude };
      state.locationText = "Location will be included with this alert.";
      if (state.holdArmed) setSosStatus(`Cancel in ${state.countdown} seconds. ${state.locationText}`);
    }, error => {
      if (!state.holdArmed) return;
      state.locationText = error.code === error.PERMISSION_DENIED ? "Location was not shared. You can continue without it." : "Location is unavailable. You can continue without it.";
      if (state.holdArmed) setSosStatus(`Cancel in ${state.countdown} seconds. ${state.locationText}`);
    }, { enableHighAccuracy: false, timeout: 4500, maximumAge: 60000 });
  }

  function beginSosCountdown() {
    if (state.holdArmed) return;
    state.holdArmed = true;
    state.countdown = 5;
    const root = document.querySelector("#emergency-root");
    const demoPanel = root?.querySelector(".sos-demo-panel");
    if (demoPanel) demoPanel.innerHTML = `<div class="sos-countdown-number" aria-label="5 seconds">5</div><p class="sos-countdown-copy">Alert will send in five seconds.</p><button class="button danger" type="button" data-cancel-sos>Cancel alert</button><div class="sos-live-status" id="sos-feedback" role="status" aria-live="polite">Cancel in 5 seconds.</div>`;
    requestLocation();
    window.clearInterval(state.countdownTimer);
    state.countdownTimer = window.setInterval(() => {
      state.countdown -= 1;
      const number = document.querySelector(".sos-countdown-number");
      const status = document.querySelector("#sos-feedback");
      if (number) {
        number.textContent = String(Math.max(0, state.countdown));
        number.setAttribute("aria-label", `${Math.max(0, state.countdown)} seconds`);
      }
      if (state.countdown <= 0) {
        state.holdArmed = false;
        void sendSos();
      }
      else if (status) status.firstChild.textContent = `Cancel in ${state.countdown} seconds. `;
    }, 1000);
  }

  async function loadHealthCard() {
    const profileId = new URLSearchParams(window.location.search).get("profileId");
    if (!profileId) throw new Error("Open this page from a health profile to show its card.");
    return request(`/profiles/${encodeURIComponent(profileId)}/emergency-card`);
  }

  document.addEventListener("submit", event => {
    const contactForm = event.target.closest("[data-contact-form]");
    if (contactForm) {
      event.preventDefault();
      const values = new FormData(contactForm);
      const error = contactForm.querySelector("#contact-form-error");
      error.textContent = "";
      const submit = contactForm.querySelector('[type="submit"]');
      submit.disabled = true;
      const profileId = activeProfile().id;
      const contactId = String(values.get("id") || "");
      const body = Object.fromEntries(values);
      delete body.id;
      delete body.profileId;
      if (!body.email) delete body.email;
      if (state.preview) {
        const contact = {
          id: contactId || crypto.randomUUID(),
          profileId,
          name: String(body.name),
          relationship: String(body.relationship),
          phone: String(body.phone_number),
          email: String(body.email || ""),
          priority: contactId ? state.contacts.findIndex(item => item.id === contactId) + 1 : state.contacts.length + 1,
        };
        const existing = demoContacts.findIndex(item => item.id === contactId);
        if (existing >= 0) demoContacts[existing] = contact;
        else demoContacts.push(contact);
        state.contacts = demoContacts.filter(item => item.profileId === profileId);
        state.editingContactId = "";
        document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
        showToast(contactId ? "Preview contact updated." : "Preview contact saved.");
        submit.disabled = false;
        return;
      }
      void request(contactId ? `/emergency-contacts/${encodeURIComponent(contactId)}` : `/profiles/${encodeURIComponent(profileId)}/emergency-contacts`, {
        method: contactId ? "PATCH" : "POST",
        body: contactId ? body : { ...body, priority: state.contacts.length + 1 },
      }).then(async () => {
        state.editingContactId = "";
        await refreshContacts();
        document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
        showToast(contactId ? "Emergency contact updated." : "Emergency contact saved.");
        try {
          await refreshEmergencyCardCache(profileId);
        } catch (cacheError) {
          showToast(`The contact was saved, but the offline emergency card could not be refreshed: ${cacheError.message}`, "error");
        }
      }).catch(requestError => { error.textContent = requestError.message; })
        .finally(() => { submit.disabled = false; });
      return;
    }
    const appointmentForm = event.target.closest("[data-appointment-form]");
    if (appointmentForm) {
      event.preventDefault();
      const values = new FormData(appointmentForm);
      const error = appointmentForm.querySelector("#appointment-error");
      error.textContent = "";
      const submit = appointmentForm.querySelector('[type="submit"]');
      submit.disabled = true;
      const profile = activeProfile();
      const startsAt = new Date(`${values.get("date")}T${values.get("time")}`);
      const reminderMinutes = Number(values.get("reminderMinutes"));
      if (state.preview) {
        const appointment = {
          id: crypto.randomUUID(),
          profileId: profile.id,
          title: String(values.get("title")),
          date: String(values.get("date")),
          starts_at: startsAt.toISOString(),
          location: String(values.get("location") || ""),
          notes: String(values.get("notes") || ""),
          details: {
            time: String(values.get("time")),
            location: String(values.get("location") || ""),
            reminderStatus: reminderMinutes ? "pending" : "scheduled",
          },
          profileName: profile.name,
          visibility: String(values.get("visibility")),
        };
        demoAppointments.push(appointment);
        if (reminderMinutes) {
          const dueAt = new Date(startsAt.getTime() - reminderMinutes * 60_000);
          state.reminders.push({
            id: crypto.randomUUID(),
            profile_id: profile.id,
            appointment_id: appointment.id,
            title: appointment.title,
            due_at: dueAt.toISOString(),
            status: "pending",
            recurrence: "none",
            recurrence_interval: 1,
            visibility: appointment.visibility,
          });
        }
        state.appointments = demoAppointments.filter(item => item.profileId === profile.id);
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Preview appointment saved.");
        submit.disabled = false;
        return;
      }
      void request(`/profiles/${encodeURIComponent(profile.id)}/appointments`, {
        method: "POST",
        body: {
          title: values.get("title"),
          starts_at: startsAt.toISOString(),
          location: values.get("location") || null,
          notes: values.get("notes") || null,
          visibility: values.get("visibility"),
        },
      }).then(async appointment => {
        if (reminderMinutes) {
          const dueAt = new Date(startsAt.getTime() - reminderMinutes * 60_000);
          try {
            await request(`/profiles/${encodeURIComponent(profile.id)}/reminders`, {
              method: "POST",
              body: {
                appointment_id: appointment.id,
                title: appointment.title,
                due_at: dueAt.toISOString(),
                visibility: values.get("visibility"),
              },
            });
          } catch (reminderError) {
            await refreshAppointments();
            document.querySelector("#appointments-root").innerHTML = renderAppointments();
            showToast(`Appointment saved, but the reminder could not be created: ${reminderError.message}`, "error");
            return;
          }
        }
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast(reminderMinutes ? "Appointment and reminder saved." : "Appointment saved.");
      }).catch(requestError => {
        error.textContent = requestError.message;
        if (requestError.status && requestError.status >= 500) showToast(requestError.message, "error");
      }).finally(() => { submit.disabled = false; });
      return;
    }
    const editForm = event.target.closest("[data-appointment-edit-form]");
    if (editForm) {
      event.preventDefault();
      const values = new FormData(editForm);
      const startsAt = new Date(String(values.get("starts_at")));
      if (state.preview) {
        const appointment = demoAppointments.find(item => item.id === editForm.dataset.appointmentId);
        if (appointment) {
          appointment.title = String(values.get("title"));
          appointment.date = `${startsAt.getFullYear()}-${String(startsAt.getMonth() + 1).padStart(2, "0")}-${String(startsAt.getDate()).padStart(2, "0")}`;
          appointment.starts_at = startsAt.toISOString();
          appointment.location = String(values.get("location") || "");
          appointment.notes = String(values.get("notes") || "");
        }
        state.appointments = demoAppointments.filter(item => item.profileId === activeProfile()?.id);
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Preview appointment updated.");
        return;
      }
      void request(`/appointments/${encodeURIComponent(editForm.dataset.appointmentId)}`, {
        method: "PATCH",
        body: {
          title: values.get("title"),
          starts_at: startsAt.toISOString(),
          location: values.get("location") || null,
          notes: values.get("notes") || null,
        },
      }).then(async () => {
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Appointment updated.");
      }).catch(error => showToast(error.message, "error"));
      return;
    }
    const reminderEditForm = event.target.closest("[data-reminder-edit-form]");
    if (reminderEditForm) {
      event.preventDefault();
      const values = new FormData(reminderEditForm);
      if (state.preview) {
        const reminder = state.reminders.find(item => item.id === reminderEditForm.dataset.reminderId);
        if (reminder) {
          reminder.title = String(values.get("title"));
          reminder.due_at = new Date(String(values.get("due_at"))).toISOString();
          reminder.recurrence = String(values.get("recurrence"));
        }
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Preview reminder updated.");
        return;
      }
      void request(`/reminders/${encodeURIComponent(reminderEditForm.dataset.reminderId)}`, {
        method: "PATCH",
        body: {
          title: values.get("title"),
          due_at: new Date(String(values.get("due_at"))).toISOString(),
          recurrence: values.get("recurrence"),
        },
      }).then(async () => {
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Reminder updated.");
      }).catch(error => showToast(error.message, "error"));
      return;
    }
    const reminderForm = event.target.closest("[data-reminder-form]");
    if (reminderForm) {
      event.preventDefault();
      const values = new FormData(reminderForm);
      const error = reminderForm.querySelector("#reminder-error");
      error.textContent = "";
      const dueAt = new Date(String(values.get("due_at")));
      const profile = activeProfile();
      if (state.preview) {
        const dueAt = new Date(String(values.get("due_at")));
        state.reminders.push({
          id: crypto.randomUUID(),
          profile_id: profile.id,
          appointment_id: null,
          title: String(values.get("title")),
          due_at: dueAt.toISOString(),
          status: "pending",
          recurrence: String(values.get("recurrence")),
          recurrence_interval: 1,
          visibility: "private",
        });
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Preview reminder saved.");
        return;
      }
      void request(`/profiles/${encodeURIComponent(profile.id)}/reminders`, {
        method: "POST",
        body: {
          title: values.get("title"),
          due_at: dueAt.toISOString(),
          recurrence: values.get("recurrence"),
        },
      }).then(async () => {
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Reminder saved.");
      }).catch(requestError => { error.textContent = requestError.message; });
    }
  });

  document.addEventListener("click", event => {
    const appointmentCreateLink = event.target.closest('a[href="#health-appointment-form"]');
    if (appointmentCreateLink) {
      event.preventDefault();
      const form = document.querySelector(".appointment-create");
      if (form) {
        form.open = true;
        form.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      return;
    }
    if (event.target.closest("[data-sos-start]")) { beginSosCountdown(); return; }
    if (event.target.closest("[data-cancel-sos]")) { cancelSosDemo(); return; }
    if (event.target.closest("[data-reset-sos]")) {
      window.clearInterval(state.sosPollTimer);
      state.sosPollTimer = 0;
      state.holdArmed = false;
      state.deliveryStatus = "";
      state.sosEventId = "";
      state.publicCardUrl = "";
      document.querySelector("#emergency-root").innerHTML = renderSos();
      return;
    }
    const contactEdit = event.target.closest("[data-contact-edit]");
    if (contactEdit) {
      state.editingContactId = contactEdit.dataset.contactEdit;
      document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
      return;
    }
    if (event.target.closest("[data-contact-edit-cancel]")) {
      state.editingContactId = "";
      document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
      return;
    }
    if (event.target.closest("[data-contact-remove]")) {
      const contactId = event.target.closest("[data-contact-remove]").dataset.contactRemove;
      const profileId = activeProfile().id;
      if (state.preview) {
        const index = demoContacts.findIndex(contact => contact.id === contactId);
        if (index >= 0) demoContacts.splice(index, 1);
        state.contacts = demoContacts.filter(contact => contact.profileId === profileId);
        document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
        showToast("Preview contact removed.");
        return;
      }
      void request(`/emergency-contacts/${encodeURIComponent(contactId)}`, { method: "DELETE" }).then(async () => {
        await refreshContacts();
        document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
        showToast("Emergency contact removed.");
        try {
          await refreshEmergencyCardCache(profileId);
        } catch (cacheError) {
          showToast(`The contact was removed, but the offline emergency card could not be refreshed: ${cacheError.message}`, "error");
        }
      }).catch(error => showToast(error.message, "error"));
      return;
    }
    const move = event.target.closest("[data-contact-move]");
    if (move) {
      const id = move.dataset.contactId;
      const from = state.contacts.findIndex(contact => contact.id === id);
      const to = from + (move.dataset.contactMove === "up" ? -1 : 1);
      if (to < 0 || to >= state.contacts.length) return;
      const ordered = [...state.contacts];
      [ordered[from], ordered[to]] = [ordered[to], ordered[from]];
      if (state.preview) {
        ordered.forEach((contact, index) => {
          const fixture = demoContacts.find(item => item.id === contact.id);
          if (fixture) fixture.priority = index + 1;
        });
        state.contacts = ordered.map((contact, index) => ({ ...contact, priority: index + 1 }));
        document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
        return;
      }
      void Promise.all(ordered.map((contact, index) =>
        request(`/emergency-contacts/${encodeURIComponent(contact.id)}`, {
          method: "PATCH",
          body: { priority: index + 1 },
        }),
      )).then(async () => {
        await refreshContacts();
        document.querySelector("#emergency-root").innerHTML = renderEmergencyContacts();
        try {
          await refreshEmergencyCardCache(activeProfile().id);
        } catch (cacheError) {
          showToast(`Priority was updated, but the offline emergency card could not be refreshed: ${cacheError.message}`, "error");
        }
      }).catch(error => showToast(error.message, "error"));
      return;
    }
    const view = event.target.closest("[data-calendar-view]");
    if (view) {
      state.calendarView = view.dataset.calendarView;
      void refreshAppointments().then(() => { document.querySelector("#appointments-root").innerHTML = renderAppointments(); }).catch(error => showToast(error.message, "error"));
      return;
    }
    const monthMove = event.target.closest("[data-calendar-move]");
    if (monthMove) {
      const amount = monthMove.dataset.calendarMove === "next" ? 1 : -1;
      state.calendarDate = state.calendarView === "month" ? new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth() + amount, 1) : new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth(), state.calendarDate.getDate() + amount * 7);
      void refreshAppointments().then(() => { document.querySelector("#appointments-root").innerHTML = renderAppointments(); }).catch(error => showToast(error.message, "error"));
      return;
    }
    if (event.target.closest("[data-calendar-today]")) {
      state.calendarDate = new Date();
      void refreshAppointments().then(() => { document.querySelector("#appointments-root").innerHTML = renderAppointments(); }).catch(error => showToast(error.message, "error"));
      return;
    }
    const reminder = event.target.closest("[data-reminder-action]");
    if (reminder) {
      const action = reminder.dataset.reminderAction;
      const reminderId = reminder.dataset.reminderId;
      if (state.preview) {
        const target = state.reminders.find(item => item.id === reminderId);
        if (target) {
          if (action === "complete") target.status = "completed";
          else target.snoozed_until = new Date(Date.now() + 10 * 60_000).toISOString();
          if (target.appointment_id) {
            const appointment = demoAppointments.find(item => item.id === target.appointment_id);
            if (appointment) appointment.details.reminderStatus = action === "complete" ? "completed" : "snoozed";
          }
        }
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast(action === "complete" ? "Preview reminder completed." : "Preview reminder snoozed.");
        return;
      }
      const path = action === "snooze"
        ? `/reminders/${encodeURIComponent(reminderId)}/snooze`
        : `/reminders/${encodeURIComponent(reminderId)}/complete`;
      void request(path, {
        method: "POST",
        ...(action === "snooze" ? { body: { minutes: 10 } } : {}),
      }).then(async () => {
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast(action === "complete" ? "Reminder completed." : "Reminder snoozed for ten minutes.");
      }).catch(error => showToast(error.message, "error"));
      return;
    }
    const deleteAppointment = event.target.closest("[data-appointment-delete]");
    if (deleteAppointment) {
      if (!window.confirm("Delete this appointment?")) return;
      if (state.preview) {
        const id = deleteAppointment.dataset.appointmentDelete;
        const index = demoAppointments.findIndex(item => item.id === id);
        if (index >= 0) demoAppointments.splice(index, 1);
        state.appointments = demoAppointments.filter(item => item.profileId === activeProfile()?.id);
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Preview appointment deleted.");
        return;
      }
      void request(`/appointments/${encodeURIComponent(deleteAppointment.dataset.appointmentDelete)}`, { method: "DELETE" }).then(async () => {
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Appointment deleted.");
      }).catch(error => showToast(error.message, "error"));
      return;
    }
    const deleteReminder = event.target.closest("[data-reminder-delete]");
    if (deleteReminder) {
      if (!window.confirm("Delete this reminder?")) return;
      if (state.preview) {
        state.reminders = state.reminders.filter(item => item.id !== deleteReminder.dataset.reminderDelete);
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Preview reminder deleted.");
        return;
      }
      void request(`/reminders/${encodeURIComponent(deleteReminder.dataset.reminderDelete)}`, { method: "DELETE" }).then(async () => {
        await refreshAppointments();
        document.querySelector("#appointments-root").innerHTML = renderAppointments();
        showToast("Reminder deleted.");
      }).catch(error => showToast(error.message, "error"));
    }
  });

  document.addEventListener("change", event => {
    if (event.target.matches("#sos-profile-select")) {
      const member = window.HealthPages.memberProfiles().find(item => item.id === event.target.value);
      if (member) window.location.href = `/health-card.html?profileId=${encodeURIComponent(member.id)}`;
    }
  });

  async function afterRender(route) {
    if (["/sos", "/emergency-contacts", "/appointments"].includes(route)) await refreshRoute(route);
  }

  window.EmergencyPages = { bootstrap, renderPage, afterRender, loadHealthCard };
})();
