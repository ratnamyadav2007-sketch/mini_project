const demoMemberData = [
  { id: "maya", name: "Maya Patel", relation: "Child", initials: "MP", color: "#456c58", wash: "#e5ede5" },
  { id: "arun", name: "Arun Patel", relation: "Partner", initials: "AP", color: "#416c78", wash: "#e3edef" },
  { id: "riya", name: "Riya Patel", relation: "You", initials: "RP", color: "#806016", wash: "#f5edcf" },
];
let memberData = demoMemberData;

const routeInfo = {
  "/home": { label: "Home", icon: "home" },
  "/timeline": { label: "Timeline", icon: "clock" },
  "/family": { label: "Family", icon: "users" },
  "/appointments": { label: "Appointments", icon: "calendar" },
  "/emergency-contacts": { label: "Emergency contacts", icon: "phone" },
  "/diet": { label: "Diet", icon: "apple" },
  "/insights": { label: "Insights", icon: "activity" },
  "/chat": { label: "Chat", icon: "clipboard" },
  "/sos": { label: "Urgent help", icon: "alert-triangle" },
  "/settings": { label: "Settings", icon: "settings" },
  "/onboarding": { label: "New profile", icon: "user-plus" },
};

let activeMember = memberData[2];
let isOnline = navigator.onLine;
let activeRoute = "/home";
const routeContent = document.querySelector("#route-content");
const breadcrumbs = document.querySelector("#breadcrumbs");
const toastRegion = document.querySelector("#shell-toasts");
const PREVIEW_MODE = new URLSearchParams(window.location.search).get("preview") === "1";
const MOCK_MODE = new URLSearchParams(window.location.search).get("mock") === "1";
let appReady = false;
const reminderNotifications = [];
const seenReminderNotifications = new Set();
let reminderPollCursor = new Date(Date.now() - 60_000);
let unreadReminderCount = 0;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function avatarMarkup(member, extraClass = "") {
  return `<span class="${extraClass} ${extraClass ? "" : "person-avatar"}" style="--member-color:${member.color};--member-wash:${member.wash}" aria-hidden="true">${escapeHtml(member.initials)}</span>`;
}

function recordRow(icon, title, detail, badge = "") {
  return `<li class="record-row"><span class="record-icon"><svg class="icon" aria-hidden="true"><use href="icons.svg#${icon}"></use></svg></span><span class="record-copy"><strong>${title}</strong><small>${detail}</small></span>${badge ? `<span class="status-pill ${badge === "Review" ? "warning" : ""}">${badge}</span>` : ""}<svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-right"></use></svg></li>`;
}

function pageHeader(kicker, title, lede = "") {
  return `<header class="page-header"><p class="page-kicker">${kicker}</p><h1 class="page-title">${title}</h1>${lede ? `<p class="page-lede">${lede}</p>` : ""}</header>`;
}

function homePage() {
  if (!activeMember) {
    return `${pageHeader("A first page in the journal", "Begin with one profile.", "Add a person to create a private lifetime health timeline.")}<a class="primary-action" href="#/onboarding">Create a profile</a>`;
  }
  const memberRows = memberData.map(member => memberRow(member)).join("");
  return `${pageHeader("Your care journal", `Good morning, ${escapeHtml(activeMember.name.split(" ")[0])}.`, "A clear place to pick up where your family's care left off.")}
    <div class="welcome-strip"><div><strong>Small notes add up.</strong><p>Keep today's details close; share them when they are useful.</p></div><button class="primary-action" type="button" data-demo-action="Note saved as a draft."><svg class="icon" aria-hidden="true"><use href="icons.svg#plus"></use></svg>Add a note</button></div>
    <div class="content-grid" style="margin-top:28px"><section class="content-column"><h2 class="section-label">Recent notes <a href="#/timeline">View timeline</a></h2><div class="surface surface-pad"><ul class="record-list">${recordRow("clipboard", "Clinic visit", "Maya / Yesterday, 2:40 pm", "Shared")}${recordRow("pill", "Medication list updated", "Arun / Monday", "Review")}${recordRow("heart", "Sleep routine", "Maya / Sunday")}</ul></div></section>
    <aside class="content-column"><h2 class="section-label">Your family <a href="#/family">Manage</a></h2><div class="surface surface-pad"><div class="member-list">${memberRows || '<p class="empty-copy">No members yet. Add a profile to begin.</p>'}</div></div></aside></div>`;
}

function memberRow(member) {
  return `<button class="member-row" type="button" data-select-member="${escapeHtml(member.id)}">${avatarMarkup(member)}<span class="person-meta"><span class="person-name">${escapeHtml(member.name)}</span><span class="person-subtitle">${escapeHtml(member.relation)} / View health notes</span></span><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-right"></use></svg></button>`;
}

function timelinePage() {
  return `${pageHeader("Family record / ${activeMember.name}", "Timeline", "Appointments, observations, and updates in date order.")}
    <div class="content-grid"><section class="content-column"><h2 class="section-label">October 2026 <span class="status-pill info">This month</span></h2><div class="surface surface-pad"><ol class="timeline"><li><span class="date-stamp">Today / 9:14 am</span><strong>Routine check-in</strong><p>A note was added by ${activeMember.name.split(" ")[0]}.</p></li><li><span class="date-stamp">Oct 06 / 3:30 pm</span><strong>Medication list updated</strong><p>Details are ready for the next care visit.</p></li><li><span class="date-stamp">Oct 02 / 11:00 am</span><strong>Clinic visit</strong><p>Visit summary shared with the family care circle.</p></li></ol></div></section><aside class="content-column"><h2 class="section-label">Filters</h2><div class="surface surface-pad"><p style="margin:0 0 12px">Choose a record type to narrow this view.</p><button class="status-pill" type="button" aria-pressed="true">All notes</button> <button class="status-pill info" type="button" aria-pressed="false">Visits</button> <button class="status-pill warning" type="button" aria-pressed="false">Medication</button></div></aside></div>`;
}

function familyPage() {
  return `${pageHeader("People / Care circle", "Family", "The people and details that help you care for one another.")}
    <div class="content-grid"><section class="content-column"><h2 class="section-label">Family members <button class="secondary-action" type="button" data-demo-action="Invite flow is ready to connect."><svg class="icon" aria-hidden="true"><use href="icons.svg#user-plus"></use></svg>Invite someone</button></h2><div class="surface surface-pad"><div class="member-list">${memberData.map(member => memberRow(member)).join("")}</div></div></section><aside class="content-column"><h2 class="section-label">Emergency contacts</h2><div class="surface surface-pad"><div class="record-copy"><strong>Setup needed</strong><small>Add a trusted contact before relying on urgent help.</small></div><a class="secondary-action" style="margin-top:15px" href="#/settings">Review emergency setup</a></div></aside></div>`;
}

function dietPage() {
  return `${pageHeader("Notes / Food &amp; wellbeing", "Diet", "Notice patterns that feel useful. No scores, streaks, or good and bad labels.")}
    <div class="content-grid"><section class="content-column"><h2 class="section-label">Recent food notes <button class="secondary-action" type="button" data-demo-action="Food note added to the draft."><svg class="icon" aria-hidden="true"><use href="icons.svg#plus"></use></svg>Add a note</button></h2><div class="surface surface-pad"><div class="food-entry"><span><strong>Lunch / Rice and lentils</strong><small>Today / Maya</small></span><span class="status-pill info">Meal note</span></div><div class="food-entry"><span><strong>More energy after breakfast</strong><small>Yesterday / Riya</small></span><span class="status-pill">Observation</span></div><div class="food-entry"><span><strong>Water reminder</strong><small>Oct 04 / Arun</small></span><span class="status-pill warning">Review</span></div></div></section><aside class="content-column"><h2 class="section-label">A gentle reminder</h2><div class="surface surface-pad"><p style="margin:0">Food notes are personal context for a conversation with your care team, not a nutrition grade.</p></div></aside></div>`;
}

function sosPage() {
  return `<div class="sos-page">${pageHeader("Urgent help / Always nearby", "What do you need right now?", "Keep the next step visible. Choose a trusted contact or open the essential health details.")}
    <div class="sos-banner"><span class="sos-mark"><svg class="icon" aria-hidden="true"><use href="icons.svg#alert-triangle"></use></svg></span><div><h2>For immediate danger, contact local emergency services.</h2><p>Emergency numbers vary by region. Set your local number in settings before using this action.</p></div></div>
    <div class="sos-actions"><button class="sos-action" type="button" data-demo-action="Set your local emergency number in Settings before calling from Fieldnote."><svg class="icon" aria-hidden="true"><use href="icons.svg#phone"></use></svg><span><strong>Call local emergency services</strong><small>Number not configured</small></span></button><a class="sos-action" href="#/family"><svg class="icon" aria-hidden="true"><use href="icons.svg#users"></use></svg><span><strong>Choose an emergency contact</strong><small>Review your care circle</small></span></a><button class="sos-action" type="button" data-demo-action="Essential health details are ready to connect to a member record."><svg class="icon" aria-hidden="true"><use href="icons.svg#briefcase-medical"></use></svg><span><strong>Show key health details</strong><small>Medication and care notes</small></span></button><a class="sos-action" href="#/settings"><svg class="icon" aria-hidden="true"><use href="icons.svg#settings"></use></svg><span><strong>Set up urgent help</strong><small>Add a local number and contacts</small></span></a></div>
    <p class="sos-footnote">Prototype: calls are not connected. Configure and verify regional emergency details before launch.</p></div>`;
}

function settingsPage() {
  return `${pageHeader("Preferences / Safety", "Settings", "Keep your care circle and urgent-help details up to date.")}
    <div class="content-grid"><section class="content-column"><h2 class="section-label">Urgent help</h2><div class="surface surface-pad"><div class="record-row"><span class="record-icon" style="background:var(--coral-wash);color:var(--coral)"><svg class="icon" aria-hidden="true"><use href="icons.svg#phone"></use></svg></span><span class="record-copy"><strong>Local emergency number</strong><small>Not configured / set before launch</small></span><span class="status-pill coral">Needs setup</span></div><div class="record-row"><span class="record-icon"><svg class="icon" aria-hidden="true"><use href="icons.svg#users"></use></svg></span><span class="record-copy"><strong>Emergency contact</strong><small>No trusted contact selected</small></span><a href="#/family">Add</a></div></div></section></div>`;
}

function offlinePage() {
  return `<div class="empty-page"><span class="empty-page-mark"><svg class="icon" aria-hidden="true"><use href="icons.svg#cloud-off"></use></svg></span><p class="page-kicker">Connection paused</p><h1 class="page-title">Your notes are safe here.</h1><p>You're offline, so shared records may be out of date. Reconnect to refresh your care circle and sync changes.</p><div class="offline-banner"><svg class="icon" aria-hidden="true"><use href="icons.svg#info"></use></svg><p>Emergency help may need a phone connection. Use your device to contact local services if someone is in immediate danger.</p></div><button class="primary-action" style="margin-top:18px" type="button" data-retry-online><svg class="icon" aria-hidden="true"><use href="icons.svg#refresh"></use></svg>Try again</button></div>`;
}

function notFoundPage() {
  return `<div class="empty-page"><span class="empty-page-mark error"><svg class="icon" aria-hidden="true"><use href="icons.svg#search"></use></svg></span><p class="page-kicker">Page not found / 404</p><h1 class="page-title">That page isn't in the notebook.</h1><p>The address may have changed, or the page may have been moved. Head back to your family home to find your way.</p><a class="primary-action" href="#/home"><svg class="icon" aria-hidden="true"><use href="icons.svg#home"></use></svg>Go to home</a></div>`;
}

const pageRenderers = {
  "/home": window.HealthPages.renderHome,
  "/timeline": window.HealthPages.renderTimeline,
  "/family": window.HealthPages.renderFamily,
  "/onboarding": window.HealthPages.renderOnboarding,
  "/diet": window.WellnessPages.renderDiet,
  "/insights": () => window.InsightsChatPages.renderInsights(),
  "/chat": () => window.InsightsChatPages.renderChat(),
  "/sos": () => window.EmergencyPages.renderPage("/sos"),
  "/appointments": () => window.EmergencyPages.renderPage("/appointments"),
  "/emergency-contacts": () => window.EmergencyPages.renderPage("/emergency-contacts"),
  "/settings": settingsPage,
};

function currentPath() {
  const raw = window.location.hash.replace(/^#/, "").split("?", 1)[0];
  return raw.startsWith("/") ? raw : "/home";
}

function renderBreadcrumbs(path) {
  const items = [{ label: "Home", href: "#/home" }];
  if (path !== "/home") items.push({ label: routeInfo[path]?.label || "Not found" });
  breadcrumbs.innerHTML = items.map((item, index) => {
    const content = item.href && index < items.length - 1
      ? `<a href="${item.href}">${item.label}</a>`
      : `<span aria-current="page">${item.label}</span>`;
    return `${index ? '<li aria-hidden="true"><svg class="icon"><use href="icons.svg#chevron-right"></use></svg></li>' : ""}<li>${content}</li>`;
  }).join("");
}

async function ensureRouteModule(path) {
  if (path !== "/insights" && path !== "/chat") return;
  if (!window.Chart) {
    const chartModule = await import("./chart.umd.js");
    window.Chart = chartModule.default || window.Chart;
  }
  if (!window.InsightsChatPages) await import("./insights-chat-pages.js");
}

async function renderRoute(animate = true) {
  const requestedRoute = currentPath();
  try {
    await ensureRouteModule(requestedRoute);
  } catch (error) {
    routeContent.innerHTML = `<div class="empty-page" role="alert"><p class="page-kicker">Page unavailable</p><h1 class="page-title">This screen could not be loaded.</h1><p>${escapeHtml(error.message || "Reload the page and try again.")}</p></div>`;
    return;
  }
  if (requestedRoute !== currentPath()) return;
  activeRoute = requestedRoute;
  renderBreadcrumbs(activeRoute);
  const isKnownRoute = Boolean(pageRenderers[activeRoute]);
  const routeRenderer = !isOnline ? offlinePage : isKnownRoute ? pageRenderers[activeRoute] : notFoundPage;
  routeContent.innerHTML = `<div class="page-view${animate ? " is-entering" : ""}">${routeRenderer()}</div>`;
  routeContent.classList.remove("is-entering");
  if (animate && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    requestAnimationFrame(() => routeContent.firstElementChild?.classList.add("is-entering"));
  }
  updateNavigation(isKnownRoute && isOnline ? activeRoute : "");
  routeContent.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "instant" });
  void window.HealthPages?.afterRender(activeRoute);
  void window.EmergencyPages?.afterRender(activeRoute);
  void window.WellnessPages?.afterRender(activeRoute);
  void window.InsightsChatPages?.afterRender(activeRoute);
}

window.renderRoute = renderRoute;

function updateNavigation(path) {
  for (const link of document.querySelectorAll("[data-route-link]")) {
    if (link.getAttribute("href") === `#${path}` && path) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function renderMemberControls() {
  if (activeMember) {
    window.dispatchEvent(new CustomEvent("fieldnote:active-member", { detail: activeMember }));
  }
  const cluster = document.querySelector("#member-cluster");
  cluster.innerHTML = activeMember
    ? memberData.map(member => avatarMarkup(member, `member-avatar${member.id === activeMember.id ? " active" : ""}`)).join("")
    : "";
  document.querySelector("#member-label").textContent = activeMember?.name.split(" ")[0] || "No profile";
  document.querySelector("#member-menu").innerHTML = `${memberData.length
    ? `<p class="popover-heading">Switch family member</p>${memberData.map(member => `<button class="member-option" type="button" data-member-id="${escapeHtml(member.id)}" aria-current="${member.id === activeMember?.id}">${avatarMarkup(member)}<span><strong>${escapeHtml(member.name)}</strong><small>${escapeHtml(member.relation)}</small></span>${member.id === activeMember?.id ? '<svg class="icon" aria-label="Selected"><use href="icons.svg#check"></use></svg>' : ""}</button>`).join("")}`
    : '<p class="popover-heading">No profiles are available yet.</p><a class="member-option" href="#/onboarding">Create a profile</a>'}<hr class="popover-divider"><button class="member-option sign-out-option" type="button" data-sign-out><svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg><span><strong>Sign out</strong><small>End this local session</small></span></button>`;
  document.documentElement.style.setProperty("--active-member", activeMember?.color || "#456c58");
  document.documentElement.style.setProperty("--active-member-wash", activeMember?.wash || "#e5ede5");
}

function showToast(message, kind = "success") {
  const toast = document.createElement("div");
  toast.className = `shell-toast${kind === "error" ? " error" : ""}`;
  toast.setAttribute("role", kind === "error" ? "alert" : "status");
  const icon = kind === "error" ? "alert-circle" : "check-circle";
  toast.innerHTML = `<svg class="icon" aria-hidden="true"><use href="icons.svg#${icon}"></use></svg><p>${escapeHtml(message)}</p>`;
  toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 3600);
}

function renderReminderNotifications() {
  const list = document.querySelector("#reminder-notices");
  const badge = document.querySelector("#notification-badge");
  const button = document.querySelector("[data-notifications]");
  if (!list || !badge || !button) return;
  badge.textContent = unreadReminderCount > 99 ? "99+" : String(unreadReminderCount);
  badge.hidden = unreadReminderCount === 0;
  button.setAttribute("aria-label", unreadReminderCount
    ? `Notifications, ${unreadReminderCount} unread reminder${unreadReminderCount === 1 ? "" : "s"}`
    : "Notifications");
  list.innerHTML = reminderNotifications.length
    ? reminderNotifications.slice(0, 10).map(item => `<div class="notice-option"><svg class="icon" aria-hidden="true"><use href="icons.svg#bell"></use></svg><span>${escapeHtml(item.title)}<small>${escapeHtml(new Date(item.delivered_at || item.due_at).toLocaleString())}</small></span></div>`).join("")
    : '<p class="notice-option">No due reminders.</p>';
}

async function pollDueReminders() {
  if (!appReady || document.visibilityState === "hidden") return;
  try {
    const { apiGet } = await import("./js/api.js");
    const items = await apiGet(`/reminders/due?since=${encodeURIComponent(reminderPollCursor.toISOString())}`);
    reminderPollCursor = new Date(Date.now() - 2_000);
    for (const item of items) {
      const key = item.notification_id || `${item.reminder_id}:${item.due_at}`;
      if (seenReminderNotifications.has(key)) continue;
      seenReminderNotifications.add(key);
      reminderNotifications.unshift(item);
      unreadReminderCount += 1;
      showToast(item.message || item.title);
    }
    renderReminderNotifications();
  } catch (error) {
    if (error.code !== "network_error") {
      showToast(`Due reminders could not be refreshed: ${error.message}`, "error");
    }
  }
}

async function signOut() {
  closePopovers();
  navigator.serviceWorker?.controller?.postMessage({ type: "PURGE_PRIVATE_CARD" });
  try {
    const { apiPost } = await import("./js/api.js");
    await apiPost("/auth/logout");
    const channel = new BroadcastChannel("fieldnote-auth");
    channel.postMessage({ type: "logout" });
    channel.close();
  } catch (error) {
    showToast(error.message || "The session could not be ended cleanly.", "error");
    return;
  }
  window.location.replace("/login.html?reason=signed-out");
}

async function startShell() {
  if (!PREVIEW_MODE && !MOCK_MODE) {
    try {
      const { apiGet } = await import("./js/api.js");
      const user = await apiGet("/auth/me");
      window.dispatchEvent(new CustomEvent("fieldnote:authenticated", { detail: user }));
      await apiGet("/auth/csrf/session");
    } catch (error) {
      routeContent.innerHTML = `<div class="empty-page" role="alert"><p class="page-kicker">Local service unavailable</p><h1 class="page-title">Your care space could not connect.</h1><p>${escapeHtml(error.message || "Start the local service again, then reload this page.")}</p><a class="primary-action" href="/login.html">Return to sign in</a></div>`;
      return;
    }
  }

  let profiles;
  routeContent.innerHTML = '<div class="profile-loading" role="status">Loading your profiles...</div>';
  try {
    profiles = await window.HealthPages.bootstrap(PREVIEW_MODE || MOCK_MODE);
    await window.EmergencyPages.bootstrap(PREVIEW_MODE || MOCK_MODE);
    await window.WellnessPages.bootstrap(PREVIEW_MODE || MOCK_MODE);
  } catch (error) {
    routeContent.innerHTML = `<div class="empty-page" role="alert"><p class="page-kicker">Profiles unavailable</p><h1 class="page-title">Your members could not load.</h1><p>${escapeHtml(error.message || "Try again when the local service is available.")}</p><button class="secondary-action" type="button" data-retry-profiles>Try again</button></div>`;
    return;
  }
  memberData = window.HealthPages.memberProfiles();
  if (memberData.length === 0 && (PREVIEW_MODE || MOCK_MODE)) memberData = demoMemberData;
  activeMember = memberData.find(member => member.id === activeMember?.id) || memberData[0] || null;
  if (activeMember) window.HealthPages.setActiveProfile(activeMember.id);
  window.setActiveHealthProfile = profileId => {
    window.HealthPages.setActiveProfile(profileId);
    activeMember = memberData.find(member => member.id === profileId) || activeMember;
    renderMemberControls();
  };
  if (!PREVIEW_MODE && !MOCK_MODE && "serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  renderMemberControls();
  appReady = true;
  const hasProfile = profiles.some(profile => profile.onboardingComplete);
  if (!PREVIEW_MODE && !MOCK_MODE && !hasProfile && currentPath() !== "/onboarding") window.location.hash = "/onboarding";
  else if (!window.location.hash) window.location.hash = hasProfile || PREVIEW_MODE || MOCK_MODE ? "/home" : "/onboarding";
  else renderRoute(false);

  if (!PREVIEW_MODE && !MOCK_MODE) {
    await pollDueReminders();
    window.setInterval(() => void pollDueReminders(), 30_000);
    window.setInterval(async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const { apiGet } = await import("./js/api.js");
        await apiGet("/auth/me");
      } catch (error) {
        if (error.code !== "network_error") {
          showToast(error.message || "The local session could not be checked.", "error");
        }
      }
    }, 15000);
  }
}

function closePopovers() {
  document.querySelectorAll("[data-popover]").forEach(popover => { popover.hidden = true; });
  document.querySelectorAll("[aria-controls][aria-expanded]").forEach(button => button.setAttribute("aria-expanded", "false"));
}

function togglePopover(button) {
  const target = document.getElementById(button.getAttribute("aria-controls"));
  const shouldOpen = target.hidden;
  closePopovers();
  target.hidden = !shouldOpen;
  button.setAttribute("aria-expanded", String(shouldOpen));
}

document.addEventListener("click", event => {
  if (event.target.closest("[data-retry-profiles]")) {
    window.location.reload();
    return;
  }
  if (event.target.closest("[data-sign-out]")) {
    void signOut();
    return;
  }
  const switcher = event.target.closest("[data-member-switcher]");
  if (switcher) {
    togglePopover(switcher);
    return;
  }
  const notifications = event.target.closest("[data-notifications]");
  if (notifications) {
    togglePopover(notifications);
    unreadReminderCount = 0;
    renderReminderNotifications();
    return;
  }
  const memberButton = event.target.closest("[data-member-id]");
  if (memberButton) {
    activeMember = memberData.find(member => member.id === memberButton.dataset.memberId) || activeMember;
    window.HealthPages.setActiveProfile(activeMember.id);
    renderMemberControls();
    closePopovers();
    renderRoute();
    showToast(`${activeMember.name} is selected.`);
    return;
  }
  const memberRowButton = event.target.closest("[data-select-member]");
  if (memberRowButton) {
    activeMember = memberData.find(member => member.id === memberRowButton.dataset.selectMember) || activeMember;
    if (activeMember) window.HealthPages.setActiveProfile(activeMember.id);
    renderMemberControls();
    renderRoute();
    showToast(`${activeMember.name} is selected.`);
    return;
  }
  const actionButton = event.target.closest("[data-demo-action]");
  if (actionButton) {
    showToast(actionButton.dataset.demoAction, actionButton.hasAttribute("data-error") ? "error" : "success");
    return;
  }
  if (event.target.closest("[data-retry-online]")) {
    if (navigator.onLine) {
      isOnline = true;
      renderRoute();
    } else {
      showToast("Still offline. Your device connection has not returned.", "error");
    }
    return;
  }
  if (!event.target.closest(".popover")) closePopovers();
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") closePopovers();
});

window.addEventListener("hashchange", () => { if (appReady) renderRoute(); });
window.addEventListener("healthprofileschange", event => {
  memberData = event.detail;
  activeMember = memberData.find(member => member.id === window.HealthPages.activeProfile()?.id) || memberData[0] || demoMemberData[2];
  window.HealthPages.setActiveProfile(activeMember.id);
  renderMemberControls();
});
window.addEventListener("offline", () => {
  isOnline = false;
  if (appReady) renderRoute();
});
window.addEventListener("online", () => {
  isOnline = true;
  if (appReady) {
    renderRoute();
    showToast("You're back online.");
  }
});

void startShell();
