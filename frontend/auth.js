import { apiGet, apiPost, isMockMode, setCsrfToken } from "./js/api.js";
import { setState } from "./js/store.js";
import { fieldErrorsFromApiError, mapFieldErrors } from "./js/ui.js";

const AUTH_ORIGIN = window.location.hostname;
const IS_LOCALHOST = AUTH_ORIGIN === "localhost" || AUTH_ORIGIN === "127.0.0.1";
const AUTH_PAGE = document.body.dataset.authPage;
const INVITE_CODE = new URLSearchParams(window.location.search).get("invite");
const authForm = document.querySelector("[data-auth-form]");
const formMessage = document.querySelector("#form-message");
const recoveryMessage = document.querySelector("#recovery-message");
const passwordInput = document.querySelector("#password");
const submitButton = authForm?.querySelector('[type="submit"]');

function setFormMessage(message, kind = "error") {
  if (!formMessage) return;
  formMessage.textContent = message;
  formMessage.dataset.kind = kind;
  formMessage.setAttribute("role", kind === "error" ? "alert" : "status");
}

function setFieldError(fieldName, message = "") {
  const fieldIds = {
    email: ["email", "email-error"],
    password: ["password", "password-error"],
    display_name: ["display_name", "display-name-error"],
    displayName: ["display_name", "display-name-error"],
    recoveryCode: ["recoveryCode", "recovery-error"],
    confirmPassword: ["confirmPassword", "confirm-error"],
  };
  const [inputName, errorId] = fieldIds[fieldName] || [];
  const input = authForm?.elements.namedItem(inputName);
  const error = errorId ? document.getElementById(errorId) : null;
  if (!input || !error) return;
  error.textContent = message;
  error.hidden = !message;
  if (message) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
}

function clearFieldErrors() {
  for (const name of ["email", "password", "display_name", "recoveryCode", "confirmPassword"]) {
    setFieldError(name);
  }
}

function validateForm() {
  clearFieldErrors();
  setFormMessage("");
  const values = new FormData(authForm);
  const email = String(values.get("email") || "").trim();
  const password = String(values.get("password") || "");
  let valid = true;

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
    setFieldError("email", "Enter a valid email address.");
    valid = false;
  }

  if (AUTH_PAGE === "login") {
    if (!password) {
      setFieldError("password", "Enter your password.");
      valid = false;
    }
  } else if (password.length < 12 || password.length > 128) {
    setFieldError("password", "Use at least 12 characters and no more than 128.");
    valid = false;
  }

  if (AUTH_PAGE === "register") {
    const displayName = String(values.get("display_name") || "").trim();
    if (!displayName || displayName.length > 120) {
      setFieldError("display_name", "Enter a name of 1–120 characters.");
      valid = false;
    }
  }

  if (AUTH_PAGE === "reset") {
    const recoveryCode = String(values.get("recoveryCode") || "").replace(/[\s-]/g, "");
    const confirmation = String(values.get("confirmPassword") || "");
    if (recoveryCode.length < 24 || recoveryCode.length > 40) {
      setFieldError("recoveryCode", "Enter one complete recovery code.");
      valid = false;
    }
    if (!confirmation) {
      setFieldError("confirmPassword", "Confirm your new password.");
      valid = false;
    } else if (confirmation !== password) {
      setFieldError("confirmPassword", "These passwords do not match.");
      valid = false;
    }
  }
  return valid;
}

function updatePasswordMeter() {
  const meter = document.querySelector("[data-password-meter]");
  const copy = document.querySelector("[data-password-copy]");
  if (!meter || !copy || !passwordInput) return;
  const password = passwordInput.value;
  let level = 0;
  if (password.length >= 12) level = 1;
  if (password.length >= 12 && /[a-z]/.test(password) && /[A-Z]/.test(password)) level = 2;
  if (level >= 2 && /\d/.test(password)) level = 3;
  if (level >= 3 && /[^a-zA-Z0-9]/.test(password)) level = 4;
  const labels = ["Not entered", "Getting there / add variety", "Fair / mix letter cases", "Good / add a number", "Strong / long and varied"];
  meter.dataset.level = String(level);
  meter.setAttribute("aria-valuenow", String(level));
  meter.setAttribute("aria-valuetext", labels[level]);
  copy.textContent = password.length === 0 ? "Strength appears as you type." : labels[level];
}

function setBusy(isBusy) {
  if (!submitButton) return;
  if (isBusy) {
    submitButton.dataset.originalLabel = submitButton.textContent;
    submitButton.disabled = true;
    submitButton.setAttribute("aria-busy", "true");
    submitButton.innerHTML = '<span class="spinner" aria-hidden="true"></span><span>Checking locally...</span>';
  } else {
    submitButton.disabled = false;
    submitButton.removeAttribute("aria-busy");
    submitButton.textContent = submitButton.dataset.originalLabel || "Continue";
  }
}

async function sendAuthRequest(endpoint, payload) {
  const routes = {
    register: "/auth/register",
    login: "/auth/login",
    reset: "/auth/reset-password",
  };
  const body = endpoint === "reset"
      ? {
          email: payload.email,
          recovery_code: payload.recoveryCode,
          new_password: payload.password,
        }
      : payload;
  return apiPost(routes[endpoint], body);
}

function showRecoveryCodes(codes) {
  const panel = document.querySelector("#recovery-panel");
  const list = document.querySelector("#recovery-codes");
  if (!panel || !list) return;
  list.replaceChildren(...codes.map(code => {
    const item = document.createElement("li");
    item.textContent = code;
    return item;
  }));
  authForm.hidden = true;
  panel.hidden = false;
  panel.querySelector("[data-confirm-recovery]")?.focus();
}

if (!IS_LOCALHOST) {
  setFormMessage("This prototype is available only through localhost on this computer.");
  if (submitButton) submitButton.disabled = true;
}

if (AUTH_PAGE === "register" && INVITE_CODE) {
  const note = document.querySelector(".local-only-note");
  if (note) note.textContent = "You were invited to a family circle. Create this local account with the invited email to join.";
  for (const link of document.querySelectorAll('a[href="/login.html"]')) link.href = `/login.html?invite=${encodeURIComponent(INVITE_CODE)}`;
}

if (formMessage) {
  const params = new URLSearchParams(window.location.search);
  if (params.get("reason") === "expired") setFormMessage("Your local session ended. Sign in again to continue.", "success");
  if (params.get("reason") === "server") setFormMessage("The local service is unavailable. Start it with npm start, then try again.");
  if (params.get("reason") === "signed-out") setFormMessage("You have signed out.", "success");
  if (params.get("reset") === "success") setFormMessage("Password updated. Sign in with your new password.", "success");
}

for (const toggle of document.querySelectorAll("[data-toggle-password]")) {
  toggle.addEventListener("click", () => {
    const input = document.getElementById(toggle.dataset.togglePassword);
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    toggle.textContent = reveal ? "Hide" : "Show";
    toggle.setAttribute("aria-label", `${reveal ? "Hide" : "Show"} password`);
    input.focus();
  });
}

passwordInput?.addEventListener("input", updatePasswordMeter);

authForm?.addEventListener("input", event => {
  const field = event.target;
  if (field.name) setFieldError(field.name);
  if (field.name === "confirmPassword") setFieldError("confirmPassword");
  if (field.name === "email" || field.name === "password") setFormMessage("");
});

authForm?.addEventListener("submit", async event => {
  event.preventDefault();
  if (!IS_LOCALHOST || !validateForm()) {
    authForm.querySelector('[aria-invalid="true"]')?.focus();
    return;
  }

  const values = new FormData(authForm);
  const payload = {
    email: String(values.get("email") || "").trim(),
    password: String(values.get("password") || ""),
  };
  if (AUTH_PAGE === "register") {
    payload.display_name = String(values.get("display_name") || "").trim();
  }
  if (AUTH_PAGE === "register" && INVITE_CODE) payload.inviteCode = INVITE_CODE;
  if (AUTH_PAGE === "reset") {
    payload.recoveryCode = String(values.get("recoveryCode") || "");
  }

  setBusy(true);
  try {
    if (AUTH_PAGE === "register") {
      const result = await sendAuthRequest("register", payload);
      setFormMessage("Your local account is ready.", "success");
      setState({ currentUser: result.user || null });
      setCsrfToken(result.csrf_token);
      showRecoveryCodes(result.recovery_code ? [result.recovery_code] : []);
      return;
    }
    if (AUTH_PAGE === "login") {
      const result = await sendAuthRequest("login", payload);
      setState({ currentUser: result.user || null });
      const returnTo = new URLSearchParams(window.location.search).get("returnTo");
      const target = returnTo ? new URL(returnTo, window.location.origin) : null;
      const safeReturnTo = target?.origin === window.location.origin
        && !/^\/(?:login|register|reset)\.html$/.test(target.pathname)
        ? `${target.pathname}${target.search}${target.hash}`
        : isMockMode() ? "/app-shell.html?preview=1&mock=1" : "/app-shell.html";
      window.location.assign(safeReturnTo);
      return;
    }
    const confirmation = String(values.get("confirmPassword") || "");
    if (confirmation !== payload.password) {
      setFieldError("confirmPassword", "These passwords do not match.");
      authForm.elements.namedItem("confirmPassword").focus();
      return;
    }
    await sendAuthRequest("reset", payload);
    window.location.assign("/login.html?reset=success");
  } catch (error) {
    const hasFieldErrors = mapFieldErrors(authForm, fieldErrorsFromApiError(error));
    const message = error.status === 429
      ? "Too many sign-in attempts. Please wait before trying again."
      : error.status === 401 && AUTH_PAGE === "login"
        ? "Email or password is incorrect."
        : error.message;
    setFormMessage(
      hasFieldErrors
        ? "Check the highlighted fields and try again."
        : message || "The local service could not complete that request.",
    );
  } finally {
    setBusy(false);
  }
});

document.querySelector("[data-confirm-recovery]")?.addEventListener("click", () => {
  const confirmed = document.querySelector("#recovery-saved");
  if (!confirmed?.checked) {
    if (recoveryMessage) {
      recoveryMessage.textContent = "Confirm that you saved the recovery code before continuing.";
    }
    confirmed?.focus();
    return;
  }
  document.querySelector("#recovery-codes")?.replaceChildren();
  window.location.assign(isMockMode() ? "/app-shell.html?preview=1&mock=1" : "/app-shell.html");
});

document.querySelector("#recovery-saved")?.addEventListener("change", event => {
  const continueButton = document.querySelector("[data-confirm-recovery]");
  if (continueButton) continueButton.disabled = !event.target.checked;
  if (recoveryMessage) recoveryMessage.textContent = "";
});

document.querySelector("[data-copy-recovery]")?.addEventListener("click", async () => {
  const codes = [...document.querySelectorAll("#recovery-codes li")].map(item => item.textContent);
  try {
    await navigator.clipboard.writeText(codes.join("\n"));
    if (recoveryMessage) recoveryMessage.textContent = "Recovery code copied. Store it somewhere private.";
  } catch {
    if (recoveryMessage) {
      recoveryMessage.textContent = "Clipboard access is unavailable. Select and copy the code above.";
    }
  }
});

if (IS_LOCALHOST && AUTH_PAGE !== "reset" && !isMockMode()) {
  apiGet("/auth/me")
    .then(async user => {
      setState({ currentUser: user });
      if (INVITE_CODE) {
        try {
          await sendAuthRequest("family/invites/accept", { code: INVITE_CODE });
        } catch (error) {
          setFormMessage(error.message || "This invitation could not be accepted.");
          return;
        }
      }
      window.location.replace("/app-shell.html");
    })
    .catch(error => {
      if (error.status !== 401) setFormMessage(error.message || "The local service could not complete that request.");
    });
}
