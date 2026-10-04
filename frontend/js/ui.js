const TOAST_DURATION_MS = 5000;
let toastRegion;
let offlineBanner;

function ensureToastRegion() {
  if (toastRegion?.isConnected) return toastRegion;
  toastRegion = document.querySelector("[data-toast-region]");
  if (!toastRegion) {
    toastRegion = document.createElement("div");
    toastRegion.dataset.toastRegion = "";
    toastRegion.className = "global-toast-region";
    toastRegion.setAttribute("aria-live", "polite");
    toastRegion.setAttribute("aria-relevant", "additions text");
    document.body.append(toastRegion);
  }
  return toastRegion;
}

export function showToast(message, kind = "info", duration = TOAST_DURATION_MS) {
  const toast = document.createElement("div");
  toast.className = `global-toast global-toast--${kind}`;
  toast.setAttribute("role", kind === "error" ? "alert" : "status");
  toast.textContent = String(message);
  ensureToastRegion().append(toast);
  if (duration > 0) window.setTimeout(() => toast.remove(), duration);
  return toast;
}

export function showOfflineBanner(isOffline = true) {
  if (isOffline) {
    if (!offlineBanner?.isConnected) {
      offlineBanner = document.querySelector("[data-offline-banner]");
      if (!offlineBanner) {
        offlineBanner = document.createElement("div");
        offlineBanner.dataset.offlineBanner = "";
        offlineBanner.className = "global-offline-banner";
        offlineBanner.setAttribute("role", "status");
        offlineBanner.textContent = "You appear to be offline. Changes may not reach the server.";
        document.body.prepend(offlineBanner);
      }
    }
    offlineBanner.hidden = false;
  } else if (offlineBanner?.isConnected) {
    offlineBanner.hidden = true;
  }
}

export function showLoadingSkeleton(container, { rows = 3, label = "Loading" } = {}) {
  if (!(container instanceof Element)) throw new TypeError("A loading container element is required");
  const skeleton = document.createElement("div");
  skeleton.className = "loading-skeleton";
  skeleton.setAttribute("role", "status");
  skeleton.setAttribute("aria-label", label);
  skeleton.setAttribute("aria-busy", "true");
  for (let index = 0; index < rows; index += 1) {
    const line = document.createElement("span");
    line.className = "loading-skeleton__line";
    skeleton.append(line);
  }
  container.replaceChildren(skeleton);
  return skeleton;
}

function fieldElement(form, fieldName) {
  const candidates = [
    fieldName,
    fieldName.replace(/_([a-z])/g, (_match, letter) => letter.toUpperCase()),
  ];
  for (const name of candidates) {
    const input = form.elements.namedItem(name);
    if (input instanceof HTMLElement) return input;
  }
  return form.querySelector(`[name="${CSS.escape(fieldName)}"]`);
}

function fieldErrorElement(form, input) {
  const describedBy = input.getAttribute("aria-describedby")?.split(/\s+/) || [];
  for (const id of describedBy) {
    const target = form.ownerDocument.getElementById(id);
    if (target?.matches("[data-field-error], .field-error")) return target;
  }
  let error = form.querySelector(`[data-error-for="${CSS.escape(input.name)}"]`);
  if (!error) {
    error = document.createElement("p");
    error.dataset.errorFor = input.name;
    error.dataset.fieldError = "";
    error.className = "field-error";
    error.id = `field-error-${input.name.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    input.insertAdjacentElement("afterend", error);
    input.setAttribute("aria-describedby", [
      ...describedBy,
      error.id,
    ].filter(Boolean).join(" "));
  }
  return error;
}

export function mapFieldErrors(form, errors) {
  if (!(form instanceof HTMLFormElement)) throw new TypeError("A form element is required");
  const fields = errors && typeof errors === "object" ? errors : {};
  let firstInvalid;
  for (const [name, rawMessage] of Object.entries(fields)) {
    const input = fieldElement(form, name);
    if (!input) continue;
    const message = Array.isArray(rawMessage) ? rawMessage.join(" ") : String(rawMessage);
    const error = fieldErrorElement(form, input);
    error.textContent = message;
    error.hidden = !message;
    input.setAttribute("aria-invalid", "true");
    firstInvalid ||= input;
  }
  firstInvalid?.focus();
  return Boolean(firstInvalid);
}

export function fieldErrorsFromApiError(error) {
  if (error?.fields && typeof error.fields === "object") return error.fields;
  const details = Array.isArray(error?.details) ? error.details : [];
  return Object.fromEntries(
    details
      .filter(item => Array.isArray(item.location) && item.location.length > 1)
      .map(item => [String(item.location.at(-1)), item.message || "Invalid value"]),
  );
}

export function clearFieldErrors(form) {
  for (const input of form.querySelectorAll("[aria-invalid='true']")) {
    input.removeAttribute("aria-invalid");
  }
  for (const error of form.querySelectorAll("[data-field-error], .field-error")) {
    error.textContent = "";
    error.hidden = true;
  }
}
