const params = new URLSearchParams(location.search);
const profileId = params.get("profileId");
const status = document.querySelector("#card-status");
document.querySelector("#print-card").addEventListener("click", () => window.print());

function renderCard(data) {
  const { contacts, medications, generated_at: generatedAt } = data;
  document.querySelector("#card-name").textContent = data.name;
  document.querySelector("#card-updated").textContent = `Last updated ${new Date(generatedAt).toLocaleString()}`;
  document.querySelector("#card-birth").textContent = data.date_of_birth ? new Date(`${data.date_of_birth}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" }) : "Not recorded";
  document.querySelector("#card-blood").textContent = data.blood_group || "Not recorded";
  document.querySelector("#card-allergies").textContent = data.allergies.length
    ? data.allergies.map(item => [item.name, item.details?.severity, item.details?.reaction].filter(Boolean).join(" / ")).join(", ")
    : "None recorded";
  document.querySelector("#card-conditions").textContent = data.conditions.length
    ? data.conditions.map(item => [item.name, item.details?.status].filter(Boolean).join(" / ")).join(", ")
    : "None recorded";
  document.querySelector("#card-medications").replaceChildren(...medications.map(medication => {
    const item = document.createElement("li");
    item.textContent = [medication.name, medication.details?.dosage, medication.details?.instructions].filter(Boolean).join(" / ");
    return item;
  }));
  document.querySelector("#card-medications-section").hidden = medications.length === 0;
  const contactList = document.querySelector("#card-contacts");
  contactList.replaceChildren(...contacts.map(contact => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.className = "contact-name";
    name.textContent = contact.name;
    const phone = document.createElement("a");
    phone.className = "contact-phone";
    phone.href = `tel:${contact.phone_number}`;
    phone.textContent = contact.phone_number;
    const relationship = document.createElement("span");
    relationship.className = "contact-meta";
    relationship.textContent = contact.relationship;
    item.append(name, phone, relationship);
    return item;
  }));
  document.querySelector("#card-no-contacts").hidden = contacts.length > 0;
  document.querySelector("#offline-state").textContent = navigator.onLine ? "ONLINE COPY" : "SAVED OFFLINE";
  document.querySelector("#emergency-card").hidden = false;
  status.hidden = true;
}

async function loadCard() {
  if (!profileId) throw new Error("Open this page from a health profile to show its card.");
  if (serviceWorkerRegistration) {
    const registration = await serviceWorkerRegistration;
    if (registration) await navigator.serviceWorker.ready;
  }
  const { apiGet } = await import("./js/api.js");
  const data = await apiGet(`/profiles/${encodeURIComponent(profileId)}/emergency-card?profile_id=${encodeURIComponent(profileId)}`);
  renderCard(data);
}

const serviceWorkerRegistration = "serviceWorker" in navigator
  ? navigator.serviceWorker.register("/service-worker.js").catch(error => {
    status.textContent = `Offline card storage is unavailable: ${error.message}`;
    return null;
  })
  : null;

window.addEventListener("online", () => { status.hidden = false; status.textContent = "Connection restored / updating card..."; void loadCard().catch(error => { status.textContent = error.message; }); });
window.addEventListener("offline", () => { document.querySelector("#offline-state").textContent = "SAVED OFFLINE"; });
void loadCard().catch(error => { status.textContent = error.message; });
