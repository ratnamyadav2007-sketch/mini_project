const token = new URLSearchParams(window.location.search).get("token");
const status = document.querySelector("#card-status");
document.querySelector("#print-card").addEventListener("click", () => window.print());

function displayEntries(entries, target, emptyCopy = "None recorded") {
  target.textContent = entries.length
    ? entries.map(item => [item.name, item.details?.severity, item.details?.reaction, item.details?.status]
      .filter(Boolean).join(" / ")).join(", ")
    : emptyCopy;
}

function renderCard(card) {
  document.querySelector("#card-name").textContent = card.name;
  document.querySelector("#card-birth").textContent = card.date_of_birth
    ? new Date(`${card.date_of_birth}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" })
    : "Not recorded";
  document.querySelector("#card-blood").textContent = card.blood_group || "Not recorded";
  displayEntries(card.allergies, document.querySelector("#card-allergies"));
  displayEntries(card.conditions, document.querySelector("#card-conditions"));
  const medications = document.querySelector("#card-medications");
  medications.replaceChildren(...card.medications.map(medication => {
    const item = document.createElement("li");
    item.textContent = [medication.name, medication.details?.dosage, medication.details?.instructions]
      .filter(Boolean).join(" / ");
    return item;
  }));
  document.querySelector("#card-medications-section").hidden = card.medications.length === 0;
  const contacts = document.querySelector("#card-contacts");
  contacts.replaceChildren(...card.contacts.map(contact => {
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
  document.querySelector("#card-no-contacts").hidden = card.contacts.length > 0;
  document.querySelector("#emergency-card").hidden = false;
  status.hidden = true;
}

async function loadCard() {
  if (!token) throw new Error("This emergency card link is incomplete.");
  const response = await fetch(`/api/v1/sos/${encodeURIComponent(token)}`, {
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
    headers: { Accept: "application/json" },
  });
  if (response.status === 404) throw new Error("This emergency card link has expired or was revoked.");
  if (response.status === 429) throw new Error("Too many requests. Please wait before trying again.");
  if (!response.ok) throw new Error("The emergency card could not be loaded.");
  renderCard(await response.json());
}

void loadCard().catch(error => {
  status.textContent = error.message;
});
