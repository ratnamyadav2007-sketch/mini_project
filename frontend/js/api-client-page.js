import { apiGet } from "./api.js";
import { clearFieldErrors, mapFieldErrors, showLoadingSkeleton, showToast } from "./ui.js";

const result = document.querySelector("#health-result");
const modeStatus = document.querySelector("#mode-status");
const form = document.querySelector("#field-error-form");
const mockMode = new URLSearchParams(window.location.search).get("mock") === "1";

modeStatus.textContent = mockMode
  ? "Mock mode is active. Responses are fixtures; no API request is sent."
  : "Live mode is active. Requests go to the local API.";

function showJson(value) {
  const output = document.createElement("pre");
  output.textContent = JSON.stringify(value, null, 2);
  result.replaceChildren(output);
}

async function runScenario(scenario) {
  if (scenario === "loading") {
    showLoadingSkeleton(result, { rows: 4, label: "Loading test data" });
    await new Promise(resolve => window.setTimeout(resolve, 1000));
    showJson({ state: "loading complete" });
    return;
  }
  if (scenario === "fields") {
    mapFieldErrors(form, { email: "Enter a valid email address.", display_name: "This field is required." });
    showToast("Server field errors were mapped to the form.", "info");
    return;
  }
  if (scenario === "health") {
    showLoadingSkeleton(result, { rows: 2, label: "Checking health endpoint" });
    showJson(await apiGet("/health"));
    return;
  }
  await apiGet("/health", { mockScenario: scenario });
}

document.querySelectorAll("[data-run]").forEach(button => {
  button.addEventListener("click", async () => {
    try {
      await runScenario(button.dataset.run);
    } catch (error) {
      if (![0, 401, 403, 429].includes(error.status)) {
        showJson({ error: error.message, code: error.code });
      }
    }
  });
});

form.addEventListener("submit", event => {
  event.preventDefault();
  clearFieldErrors(form);
  showToast("Sample form errors cleared.", "success");
});
