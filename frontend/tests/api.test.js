import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

let search = "";
let assignedUrl = "";
globalThis.window = {
  FIELDNOTE_API_BASE_URL: "",
  location: {
    origin: "http://localhost",
    pathname: "/api-client-test.html",
    hash: "",
    assign(url) { assignedUrl = url; },
    get search() { return search; },
    set search(value) { search = value; },
  },
  setTimeout,
  clearTimeout,
  dispatchEvent() {},
};

const {
  ApiError,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiUpload,
  clearRequestCache,
  handleGlobalApiError,
  setCsrfToken,
} = await import("../js/api.js");
const { resetState, setState } = await import("../js/store.js");
const originalFetch = globalThis.fetch;
const originalXhr = globalThis.XMLHttpRequest;

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.XMLHttpRequest = originalXhr;
  search = "";
  assignedUrl = "";
  setCsrfToken("");
  clearRequestCache();
  resetState();
});

test("uses same-origin credentials and decodes JSON success", async () => {
  let requestOptions;
  globalThis.fetch = async (_url, options) => {
    requestOptions = options;
    return new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  assert.deepEqual(await apiGet("/health"), { status: "ok" });
  assert.equal(requestOptions.credentials, "same-origin");
  assert.equal(requestOptions.headers.get("Accept"), "application/json");
});

test("requests a CSRF challenge and includes it on mutations", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), options });
    const payload = String(url).endsWith("/auth/csrf")
      ? { csrf_token: "csrf-test-token" }
      : { status: "created" };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  assert.deepEqual(await apiPost("/profiles", { display_name: "Demo" }), { status: "created" });
  assert.equal(requests[0].url, "/api/v1/auth/csrf");
  assert.equal(requests[0].options.method, "GET");
  assert.equal(requests[1].options.headers.get("X-CSRF-Token"), "csrf-test-token");
  assert.equal(requests[1].options.headers.get("Content-Type"), "application/json");
  assert.deepEqual(JSON.parse(requests[1].options.body), { display_name: "Demo" });
});

test("uses the authenticated CSRF token in memory for later requests", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), options });
    const payload = String(url).endsWith("/auth/csrf")
      ? { csrf_token: "one-time-challenge" }
      : String(url).endsWith("/auth/login")
        ? { csrf_token: "session-csrf-token" }
        : { status: "logged_out" };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await apiPost("/auth/login", { email: "demo@example.test", password: "example-password" });
  await apiPost("/auth/logout");

  assert.equal(requests[2].options.headers.get("X-CSRF-Token"), "session-csrf-token");
});

test("adds the active profile id to non-auth API requests", async () => {
  let requestedUrl = "";
  setState({ activeMember: { id: "profile-123" } });
  globalThis.fetch = async url => {
    requestedUrl = String(url);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await apiGet("/profiles/profile-123/timeline?type=allergy");

  const url = new URL(requestedUrl, window.location.origin);
  assert.equal(url.searchParams.get("profile_id"), "profile-123");
  assert.equal(url.searchParams.get("type"), "allergy");
});

test("caches successful GET responses briefly and returns independent payloads", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ values: ["server"] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const first = await apiGet("/records");
  first.values.push("caller mutation");
  assert.deepEqual(await apiGet("/records"), { values: ["server"] });
  assert.equal(calls, 1);
});

test("deduplicates concurrent GET requests", async () => {
  let calls = 0;
  let resolveResponse;
  globalThis.fetch = () => {
    calls += 1;
    return new Promise(resolve => {
      resolveResponse = resolve;
    });
  };

  const first = apiGet("/records");
  const second = apiGet("/records");
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls, 1);
  resolveResponse(new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  assert.deepEqual(await Promise.all([first, second]), [{ ok: true }, { ok: true }]);
});

test("scopes cached GET responses by active profile", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  setState({ activeMember: { id: "profile-one" } });
  await apiGet("/records");
  await apiGet("/records");
  setState({ activeMember: { id: "profile-two" } });
  await apiGet("/records");
  await apiGet("/records");
  assert.equal(calls, 2);
});

test("invalidates cached GET responses after writes", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), method: options.method });
    const payload = String(url).endsWith("/auth/csrf") ? { csrf_token: "challenge" } : { ok: true };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await apiGet("/records");
  await apiGet("/records");
  await apiPost("/records", { title: "New record" });
  await apiGet("/records");
  assert.equal(requests.filter(request => request.url.endsWith("/records")).length, 3);
});

test("does not cache auth, due-reminder, or SOS polling responses", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await apiGet("/auth/me");
  await apiGet("/auth/me");
  await apiGet("/reminders/due");
  await apiGet("/reminders/due");
  await apiGet("/sos/event-123");
  await apiGet("/sos/event-123");
  assert.equal(calls, 6);
});

test("preserves server error status, code, details, and fields", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    error: {
      code: "validation_error",
      message: "Invalid input",
      fields: { email: "Enter a valid address." },
      details: [{ location: ["body", "email"], message: "Invalid email", type: "value_error" }],
    },
  }), {
    status: 422,
    headers: { "Content-Type": "application/json" },
  });

  await assert.rejects(apiGet("/profiles"), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.code, "validation_error");
    assert.equal(error.fields.email, "Enter a valid address.");
    assert.equal(error.details[0].type, "value_error");
    return true;
  });
});

test("mock mode returns health fixtures without calling fetch", async () => {
  search = "?mock=1";
  globalThis.fetch = async () => {
    throw new Error("fetch should not run in mock mode");
  };

  assert.deepEqual(await apiGet("/health"), { status: "ok", mock: true });
});

test("mock wellness APIs match the diet, insights, and chat contracts", async () => {
  search = "?mock=1";
  setState({ activeMember: { id: "profile-mock" } });
  globalThis.fetch = async () => {
    throw new Error("fetch should not run in mock mode");
  };

  const plan = await apiPost("/diet/plans/generate?profile_id=profile-mock", {
    weight_kg: 68,
    height_cm: 165,
    activity_level: "light",
    starts_on: "2026-10-05",
  });
  assert.equal(plan.starts_on, "2026-10-05");
  assert.equal(plan.meals[0].allergen_warnings.length, 0);
  const moved = await apiPatch(`/diet/plans/${plan.id}/meals/${plan.meals[0].id}`, {
    scheduled_on: "2026-10-06",
    meal_type: "lunch",
    sort_order: 0,
  });
  assert.equal(moved.meals[0].scheduled_on, "2026-10-06");
  assert.equal(moved.meals[0].meal_type, "lunch");

  const insights = await apiGet("/insights?metric=systolic_blood_pressure&range=30d&members=profile-mock");
  assert.equal(insights.series[0].locked, false);
  assert.equal(insights.series[0].points[0].reference_lower, 90);

  const chat = await apiPost("/chat/messages", {
    profile_id: "profile-mock",
    message: "I have chest pain",
  });
  assert.equal(chat.intent, "emergency");
  assert.equal(chat.actions[0].href, "#/sos");
});

test("mock record operations return the record contract and support sharing and delete", async () => {
  search = "?mock=1";
  const created = await apiPost("/profiles/profile-1/records", {
    type: "condition",
    recorded_at: "2026-09-30T00:00:00.000Z",
    visibility: "private",
    data: { title: "Asthma", name: "Asthma" },
  });

  assert.equal(created.profile_id, "profile-1");
  assert.equal(created.data.title, "Asthma");
  assert.equal(created.attachments.length, 0);
  assert.equal((await apiPost(`/records/${created.id}/share`, { email: "viewer@example.test" })).ok, true);
  assert.equal(await apiDelete(`/records/${created.id}`), null);
});

test("mock uploads report completion and return attachment metadata", async () => {
  search = "?mock=1";
  const formData = new FormData();
  formData.append("file", new Blob(["%PDF-1.7"], { type: "application/pdf" }), "visit.pdf");
  formData.append("record_id", "record-1");
  const progress = [];

  const attachment = await apiUpload("/profiles/profile-1/attachments", formData, {
    onProgress: value => progress.push(value),
  });

  assert.equal(attachment.original_filename, "visit.pdf");
  assert.equal(attachment.content_type, "application/pdf");
  assert.equal(attachment.record_id, "record-1");
  assert.deepEqual(progress, [1]);
});

test("multipart uploads send the active profile and CSRF token while reporting progress", async () => {
  setState({ activeMember: { id: "profile-upload" } });
  const requests = [];
  globalThis.fetch = async () => new Response(JSON.stringify({ csrf_token: "upload-csrf" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
  class TestXhr {
    constructor() {
      this.headers = new Map();
      this.listeners = new Map();
      this.upload = { addEventListener: (name, listener) => this.listeners.set(`upload:${name}`, listener) };
    }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers.set(name, value); }
    addEventListener(name, listener) { this.listeners.set(name, listener); }
    send(body) {
      requests.push({ xhr: this, body });
      this.listeners.get("upload:progress")({ lengthComputable: true, loaded: 5, total: 10 });
      this.status = 201;
      this.responseText = JSON.stringify({ id: "attachment-1" });
      this.listeners.get("load")();
    }
  }
  globalThis.XMLHttpRequest = TestXhr;
  const formData = new FormData();
  formData.append("file", new Blob(["%PDF-1.7"], { type: "application/pdf" }), "visit.pdf");
  const progress = [];

  const result = await apiUpload("/profiles/profile-upload/attachments", formData, {
    onProgress: value => progress.push(value),
  });

  assert.deepEqual(result, { id: "attachment-1" });
  assert.equal(requests[0].xhr.method, "POST");
  assert.equal(new URL(requests[0].xhr.url, window.location.origin).searchParams.get("profile_id"), "profile-upload");
  assert.equal(requests[0].xhr.withCredentials, true);
  assert.equal(requests[0].xhr.headers.get("X-CSRF-Token"), "upload-csrf");
  assert.equal(requests[0].body, formData);
  assert.deepEqual(progress, [0.5]);
});

test("aborts requests at the configured timeout", async () => {
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => {
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });

  await assert.rejects(apiGet("/slow", { timeout: 10 }), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.code, "timeout");
    return true;
  });
});

test("401 global handler redirects to sign-in and remembers the current page", () => {
  window.location.pathname = "/app-shell.html";
  window.location.hash = "#/timeline";
  window.location.search = "?filter=lab";

  handleGlobalApiError(new ApiError("Authentication required", { status: 401 }));

  assert.ok(assignedUrl.startsWith("/login.html?"));
  const redirect = new URL(assignedUrl, window.location.origin);
  assert.equal(redirect.searchParams.get("returnTo"), "/app-shell.html?filter=lab#/timeline");
  assert.equal(redirect.searchParams.get("reason"), "expired");

  window.location.pathname = "/api-client-test.html";
  window.location.hash = "";
  window.location.search = "";
});
