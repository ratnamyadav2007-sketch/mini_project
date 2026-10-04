import { showOfflineBanner, showToast } from "./ui.js";
import { getState } from "./store.js";

const DEFAULT_TIMEOUT_MS = 10000;
const API_BASE_URL = (window.FIELDNOTE_API_BASE_URL || "/api/v1").replace(/\/$/, "");
let sessionCsrfToken = "";
let csrfChallengeToken = "";
let csrfChallengeRequest;
const mockHealthRecords = [];
const mockDietPlans = new Map();
const mockGoals = new Map();
const mockGoalLogs = new Map();
const GET_CACHE_TTL_MS = 10000;
const MAX_CACHED_GETS = 100;
const getResponseCache = new Map();
const getRequestsInFlight = new Map();
let cacheGeneration = 0;

function clonePayload(payload) {
  return payload == null ? payload : structuredClone(payload);
}

export function clearRequestCache() {
  cacheGeneration += 1;
  getResponseCache.clear();
  getRequestsInFlight.clear();
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = "network_error", details, fields, cause } = {}) {
    super(message, { cause });
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.fields = fields;
  }
}

export function isMockMode() {
  return new URLSearchParams(window.location.search).get("mock") === "1";
}

function withActiveProfile(path) {
  if (/^\/auth(?:\/|$)/.test(path)) return path;
  const activeProfileId = getState().activeMember?.id;
  if (!activeProfileId) return path;
  const url = new URL(path, window.location.origin);
  if (!url.searchParams.has("profile_id")) url.searchParams.set("profile_id", activeProfileId);
  return `${url.pathname}${url.search}${url.hash}`;
}

function currentReturnPath() {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function isAuthPage() {
  return /\/(?:login|register|reset)\.html$/.test(window.location.pathname);
}

function redirectToLogin() {
  if (isAuthPage()) return;
  const target = new URL("/login.html", window.location.origin);
  target.searchParams.set("returnTo", currentReturnPath());
  target.searchParams.set("reason", "expired");
  window.location.assign(`${target.pathname}${target.search}`);
}

export function handleGlobalApiError(error) {
  if (!(error instanceof ApiError) || error.globalHandled) return;
  error.globalHandled = true;
  if (error.status === 401) {
    redirectToLogin();
  } else if (error.status === 403) {
    showToast("You are not allowed to do that.", "error");
  } else if (error.status === 429) {
    showToast("Slow down a moment, then try again.", "warning");
  } else if (error.code === "network_error") {
    showOfflineBanner(true);
    showToast("The service could not be reached. Check your connection.", "error");
  }
}

function mockResponse(path, options) {
  const search = new URLSearchParams(window.location.search);
  const scenario = options.mockScenario || search.get("mock_error");
  if (scenario === "offline") {
    throw new ApiError("Mock network failure", { code: "network_error" });
  }
  if (scenario === "401" || scenario === "403" || scenario === "429") {
    const status = Number(scenario);
    throw new ApiError(
      status === 401 ? "Authentication required" :
        status === 403 ? "Access is not allowed" : "Too many requests",
      { status, code: status === 429 ? "rate_limited" : "http_error" },
    );
  }

  const url = new URL(path, window.location.origin);
  if (url.pathname.endsWith("/health")) return { status: "ok", mock: true };
  if (url.pathname.endsWith("/auth/csrf")) return { csrf_token: "mock-csrf-token" };
  if (url.pathname.endsWith("/auth/register") || url.pathname.endsWith("/auth/login")) {
    return {
      user: { id: "mock-user", email: "demo@example.test", display_name: "Demo User" },
      csrf_token: "mock-csrf-token",
      recovery_code: "MOCK-RECOVERY-CODE-DO-NOT-USE",
    };
  }
  if (url.pathname.endsWith("/auth/reset-password")) return { status: "password_reset" };
  if (url.pathname.endsWith("/auth/me")) {
    return { id: "mock-user", email: "demo@example.test", display_name: "Demo User" };
  }
  const createRecord = url.pathname.match(/\/profiles\/([^/]+)\/records$/);
  if (createRecord && options.method === "POST") {
    const record = {
      id: crypto.randomUUID(),
      profile_id: decodeURIComponent(createRecord[1]),
      type: options.body.type,
      recorded_at: options.body.recorded_at,
      visibility: options.body.visibility || "private",
      data: options.body.data,
      attachments: [],
    };
    mockHealthRecords.unshift(record);
    return record;
  }
  const recordAction = url.pathname.match(/\/records\/([^/]+)(?:\/(share))?$/);
  if (recordAction && options.method === "PATCH") {
    const record = mockHealthRecords.find(item => item.id === decodeURIComponent(recordAction[1]));
    if (!record) return { ...options.body, id: recordAction[1], profile_id: "mock-profile-user", attachments: [] };
    Object.assign(record, options.body);
    return record;
  }
  if (recordAction && recordAction[2] === "share" && options.method === "POST") {
    const record = mockHealthRecords.find(item => item.id === decodeURIComponent(recordAction[1]));
    if (record) record.visibility = "selected";
    return { ok: true };
  }
  if (recordAction && !recordAction[2] && options.method === "DELETE") {
    const index = mockHealthRecords.findIndex(item => item.id === decodeURIComponent(recordAction[1]));
    if (index >= 0) mockHealthRecords.splice(index, 1);
    return null;
  }
  if (url.pathname === `${API_BASE_URL}/meta/record-types`) {
    return [
      ["condition", "Condition", "heart", "name"],
      ["medication", "Medication", "pill", "name"],
      ["allergy", "Allergy", "alert-triangle", "name"],
      ["vaccination", "Vaccination", "shield", "name"],
      ["lab", "Laboratory", "activity", "name"],
      ["vital", "Vital", "activity", "name"],
      ["surgery", "Surgery", "activity", "name"],
      ["visit_note", "Visit note", "clipboard", "summary"],
      ["blood_group", "Blood group", "heart", "group"],
      ["systolic_blood_pressure", "Systolic blood pressure", "activity", "value"],
      ["body_temperature", "Body temperature", "activity", "value"],
      ["blood_glucose", "Blood glucose", "activity", "value"],
    ].map(([code, display_name, icon, requiredField]) => ({
      code,
      display_name,
      icon,
      fields: [{ name: requiredField, label: display_name, type: "text", required: true }],
    }));
  }
  if (url.pathname === `${API_BASE_URL}/profiles` && options.method === "GET") {
    return [{
      id: "mock-profile-user",
      user_id: "mock-user",
      display_name: "Demo Member",
      date_of_birth: "2000-01-01",
      sex: null,
      timezone: "UTC",
      relationship: "you",
      avatar_color: "#456c58",
      avatar_wash: "#e5ede5",
      blood_group: null,
      allergies: [],
      conditions: [],
      onboarding_step: 2,
      onboarding_complete: true,
    }];
  }
  if (url.pathname === `${API_BASE_URL}/families` && options.method === "GET") return [];
  if (url.pathname === `${API_BASE_URL}/families` && options.method === "POST") {
    return { id: "mock-family", name: options.body?.name || "Demo family" };
  }
  if (url.pathname.endsWith("/insights")) {
    const profileId = getState().activeMember?.id || "mock-profile-user";
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - 29);
    const day = value => value.toISOString().slice(0, 10);
    return {
      metric: url.searchParams.get("metric") || "systolic_blood_pressure",
      start_date: day(start),
      end_date: day(end),
      series: (url.searchParams.get("members") || profileId).split(",").map((id, index) => ({
        profile_id: id,
        member_label: id === profileId ? "You" : `Family member ${index}`,
        locked: id !== profileId,
        points: id === profileId ? [
          { recorded_at: `${day(start)}T09:00:00Z`, value: 145, unit: "mmHg", range_flag: "above", reference_population: "Demo reference interval", reference_note: null, reference_lower: 90, reference_upper: 120 },
          { recorded_at: `${day(end)}T09:00:00Z`, value: 122, unit: "mmHg", range_flag: "above", reference_population: "Demo reference interval", reference_note: null, reference_lower: 90, reference_upper: 120 },
        ] : [],
        trend: "decreasing",
        improvement_delta: 0.5,
        improvement: "improved",
      })),
      reference_ranges: [{ unit: "mmHg", population: "Demo reference interval", lower: 90, upper: 120 }],
      disclaimer: "Mock data only; reference ranges are context, not diagnoses.",
    };
  }
  if (url.pathname.endsWith("/chat/messages") && options.method === "POST") {
    const emergency = /\b(chest pain|can't breathe|cannot breathe|emergency|stroke|overdose)\b/i.test(options.body?.message || "");
    return {
      intent: emergency ? "emergency" : "clarification",
      answer: emergency
        ? "If someone is in immediate danger, contact emergency services now. This chat cannot contact emergency services."
        : "I can summarize an authorized reading, list pending reminders, or share general wellness education.",
      source: "rule_based",
      emergency_guidance: emergency,
      actions: emergency
        ? [{ type: "navigation", label: "Open SOS", href: "#/sos", description: "Trigger SOS yourself." }]
        : [],
    };
  }
  const plansPath = url.pathname.match(/\/profiles\/([^/]+)\/diet\/plans$/);
  if (plansPath && options.method === "GET") {
    return { items: mockDietPlans.get(decodeURIComponent(plansPath[1])) || [], disclaimer: "Mock nutrition estimates only." };
  }
  if (url.pathname.endsWith("/diet/plans/generate") && options.method === "POST") {
    const start = new Date(`${options.body.starts_on}T00:00:00`);
    const formatDate = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const profileId = url.searchParams.get("profile_id") || getState().activeMember?.id || "mock-profile-user";
    const meals = Array.from({ length: 7 }, (_, index) => {
      const scheduled = new Date(start);
      scheduled.setDate(scheduled.getDate() + index);
      return {
        id: `mock-meal-${index}`,
        name: ["Oats and fruit", "Lentil vegetable bowl", "Rice and greens"][index % 3],
        meal_type: ["breakfast", "lunch", "dinner"][index % 3],
        scheduled_on: formatDate(scheduled),
        sort_order: 0,
        serving_size_grams: 250,
        allergens: [],
        allergen_warnings: [],
        nutrients: { energy_kcal: 420, protein_g: 20, carbohydrate_g: 55, fat_g: 12, fiber_g: 8 },
      };
    });
    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    const plan = {
      id: "mock-plan", profile_id: profileId, name: "Mock weekly plan",
      starts_on: formatDate(start), ends_on: formatDate(end),
      target_calories: 2000, macro_targets: { protein_g: 68, carbohydrate_g: 250, fat_g: 67 },
      safety_notes: [], excluded_foods: [], meals, daily_totals: {},
      weekly_totals: { energy_kcal: 2940, protein_g: 140, carbohydrate_g: 385, fat_g: 84, fiber_g: 56 },
      disclaimer: "Mock nutrition estimates only; not medical advice.",
    };
    mockDietPlans.set(profileId, [plan]);
    return plan;
  }
  const movePath = url.pathname.match(/\/diet\/plans\/([^/]+)\/meals\/([^/]+)$/);
  if (movePath && options.method === "PATCH") {
    const profileId = url.searchParams.get("profile_id") || getState().activeMember?.id || "mock-profile-user";
    const plan = (mockDietPlans.get(profileId) || []).find(item => item.id === decodeURIComponent(movePath[1]));
    const meal = plan?.meals.find(item => item.id === decodeURIComponent(movePath[2]));
    if (meal) Object.assign(meal, options.body);
    return plan || { ok: true };
  }
  const goalsPath = url.pathname.match(/\/profiles\/([^/]+)\/goals$/);
  if (goalsPath && options.method === "GET") return mockGoals.get(decodeURIComponent(goalsPath[1])) || [];
  if (goalsPath && options.method === "POST") {
    const profileId = decodeURIComponent(goalsPath[1]);
    const goals = mockGoals.get(profileId) || [];
    const goal = {
      id: crypto.randomUUID(), profile_id: profileId, kind: options.body.kind,
      title: options.body.title, target_value: options.body.target_value,
      unit: ({ steps: "steps", water: "ml", sleep: "hours", weight: "kg" })[options.body.kind],
      status: "active", current_streak: 0,
    };
    goals.push(goal);
    mockGoals.set(profileId, goals);
    return goal;
  }
  const badgePath = url.pathname.match(/\/profiles\/([^/]+)\/badges$/);
  if (badgePath && options.method === "GET") return [];
  const logsPath = url.pathname.match(/\/goals\/([^/]+)\/logs$/);
  if (logsPath && options.method === "GET") return mockGoalLogs.get(decodeURIComponent(logsPath[1])) || [];
  if (logsPath && options.method === "POST") {
    const goalId = decodeURIComponent(logsPath[1]);
    const goal = [...mockGoals.values()].flat().find(item => item.id === goalId);
    const log = {
      logged_on: options.body.logged_on,
      value: options.body.value,
      target_met: Number(options.body.value) >= Number(goal?.target_value || Infinity),
      awarded_badges: [],
    };
    const logs = mockGoalLogs.get(goalId) || [];
    const index = logs.findIndex(item => item.logged_on === log.logged_on);
    if (index < 0) logs.unshift(log);
    else logs[index] = log;
    mockGoalLogs.set(goalId, logs);
    return { goal, ...log };
  }
  if (url.pathname === `${API_BASE_URL}/profiles` && options.method === "POST") {
    return { id: "mock-dependent", user_id: null, ...options.body };
  }
  if (/\/profiles\/[^/]+$/.test(url.pathname) && options.method === "PATCH") {
    return {
      id: url.pathname.split("/").at(-1),
      user_id: "mock-user",
      timezone: "UTC",
      ...options.body,
    };
  }
  return { ok: true, mock: true, data: options.body ?? null };
}

function normalizeErrorPayload(payload, status) {
  const error = payload?.error;
  const details = error?.details ?? payload?.details;
  const fields = error?.fields ?? payload?.fields;
  const message = error?.message ?? payload?.message ?? `Request failed with status ${status}`;
  const code = error?.code ?? payload?.code ?? "http_error";
  return new ApiError(message, { status, code, details, fields });
}

async function readResponse(response, { sessionCsrf = false } = {}) {
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("json")) {
    const text = await response.text();
    if (!response.ok) throw normalizeErrorPayload({ message: text || response.statusText }, response.status);
    return text || null;
  }
  let payload;
  try {
    payload = await response.json();
  } catch (cause) {
    if (response.ok) throw new ApiError("The server returned invalid JSON.", {
      status: response.status,
      code: "invalid_response",
      cause,
    });
    payload = {};
  }
  if (!response.ok) throw normalizeErrorPayload(payload, response.status);
  if (sessionCsrf && typeof payload?.csrf_token === "string") {
    setCsrfToken(payload.csrf_token);
  }
  return payload;
}

async function requestCsrfChallenge(signal) {
  if (csrfChallengeToken) return csrfChallengeToken;
  if (!csrfChallengeRequest) {
    csrfChallengeRequest = (async () => {
      const response = await fetch(`${API_BASE_URL}/auth/csrf`, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal,
      });
      const challenge = await readResponse(response);
      if (typeof challenge?.csrf_token !== "string") {
        throw new ApiError("The server did not issue a CSRF token.", {
          status: response.status,
          code: "invalid_response",
        });
      }
      csrfChallengeToken = challenge.csrf_token;
      return csrfChallengeToken;
    })().finally(() => {
      csrfChallengeRequest = undefined;
    });
  }
  return csrfChallengeRequest;
}

async function apiRequestUncached(path, options = {}) {
  const {
    method = "GET",
    body,
    headers = {},
    timeout = DEFAULT_TIMEOUT_MS,
    mockScenario,
    ...fetchOptions
  } = options;
  const normalizedMethod = method.toUpperCase();
  const isMutation = !["GET", "HEAD", "OPTIONS"].includes(normalizedMethod);
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const isPreAuthCsrfRequest = normalizedMethod === "POST"
    && ["/auth/login", "/auth/register", "/auth/reset-password"].includes(normalizedPath);
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort("Request timed out"), timeout);
  let usedChallengeToken = false;

  try {
    if (isMockMode()) {
      await new Promise(resolve => window.setTimeout(resolve, 120));
      return mockResponse(path, { ...options, mockScenario });
    }

    const requestHeaders = new Headers(headers);
    if (!requestHeaders.has("Accept")) requestHeaders.set("Accept", "application/json");
    let requestBody = body;
    if (body !== undefined && !(body instanceof FormData) && typeof body !== "string") {
      requestBody = JSON.stringify(body);
      if (!requestHeaders.has("Content-Type")) requestHeaders.set("Content-Type", "application/json");
    }
    if (isMutation && !requestHeaders.has("X-CSRF-Token")) {
      const shouldUseChallenge = isPreAuthCsrfRequest || !sessionCsrfToken;
      const token = shouldUseChallenge
        ? await requestCsrfChallenge(controller.signal)
        : sessionCsrfToken;
      usedChallengeToken = shouldUseChallenge;
      if (token) requestHeaders.set("X-CSRF-Token", token);
    }

    const response = await fetch(`${API_BASE_URL}${withActiveProfile(normalizedPath)}`, {
      ...fetchOptions,
      method: normalizedMethod,
      credentials: "same-origin",
      cache: fetchOptions.cache || "no-store",
      headers: requestHeaders,
      body: requestBody,
      signal: controller.signal,
    });
    const payload = await readResponse(response, { sessionCsrf: isPreAuthCsrfRequest });
    if (normalizedPath === "/auth/csrf/session" && typeof payload?.csrf_token === "string") {
      setCsrfToken(payload.csrf_token);
    }
    if (normalizedPath === "/auth/reset-password" || normalizedPath === "/auth/logout") {
      setCsrfToken("");
    }
    if (payload?.user && typeof payload.user === "object") {
      window.dispatchEvent(new CustomEvent("fieldnote:authenticated", { detail: payload.user }));
    }
    return payload;
  } catch (error) {
    let apiError;
    if (error instanceof ApiError) {
      apiError = error;
    } else if (error?.name === "AbortError") {
      apiError = new ApiError("The request timed out. Please try again.", {
        code: "timeout",
        cause: error,
      });
    } else {
      apiError = new ApiError("The service could not be reached. Check your connection.", {
        code: "network_error",
        cause: error,
      });
    }
    handleGlobalApiError(apiError);
    throw apiError;
  } finally {
    if (usedChallengeToken) csrfChallengeToken = "";
    window.clearTimeout(timeoutId);
  }
}

export async function apiRequest(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (method !== "GET") {
    clearRequestCache();
    return apiRequestUncached(path, options);
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (
    isMockMode()
    || options.cache === "no-store"
    || /^\/auth(?:\/|$)/.test(normalizedPath)
    || normalizedPath.startsWith("/reminders/")
    || normalizedPath.startsWith("/sos/")
  ) {
    return apiRequestUncached(path, options);
  }

  const key = withActiveProfile(normalizedPath);
  const cached = getResponseCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return clonePayload(cached.payload);
  if (cached) getResponseCache.delete(key);
  const pending = getRequestsInFlight.get(key);
  if (pending) return clonePayload(await pending);

  const generation = cacheGeneration;
  let request;
  request = apiRequestUncached(path, options)
    .then(payload => {
      if (generation === cacheGeneration) {
        getResponseCache.delete(key);
        getResponseCache.set(key, { payload: clonePayload(payload), expiresAt: Date.now() + GET_CACHE_TTL_MS });
        while (getResponseCache.size > MAX_CACHED_GETS) {
          getResponseCache.delete(getResponseCache.keys().next().value);
        }
      }
      return payload;
    })
    .finally(() => {
      if (getRequestsInFlight.get(key) === request) getRequestsInFlight.delete(key);
    });
  getRequestsInFlight.set(key, request);
  return clonePayload(await request);
}

export const apiGet = (path, options = {}) => apiRequest(path, { ...options, method: "GET" });
export const apiPost = (path, body, options = {}) => apiRequest(path, { ...options, method: "POST", body });
export const apiPut = (path, body, options = {}) => apiRequest(path, { ...options, method: "PUT", body });
export const apiPatch = (path, body, options = {}) => apiRequest(path, { ...options, method: "PATCH", body });
export const apiDelete = (path, options = {}) => apiRequest(path, { ...options, method: "DELETE" });

export async function apiUpload(path, formData, { onProgress, timeout = DEFAULT_TIMEOUT_MS } = {}) {
  clearRequestCache();
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort("Request timed out"), timeout);
  let usedChallengeToken = false;
  try {
    if (isMockMode()) {
      await new Promise(resolve => window.setTimeout(resolve, 120));
      onProgress?.(1);
      const file = formData.get("file");
      const attachment = {
        id: crypto.randomUUID(),
        original_filename: file?.name || "attachment",
        content_type: file?.type || "application/octet-stream",
        file_size_bytes: file?.size || 0,
        record_id: formData.get("record_id") || null,
      };
      const record = mockHealthRecords.find(item => item.id === attachment.record_id);
      record?.attachments.push(attachment);
      return attachment;
    }
    const token = sessionCsrfToken || await requestCsrfChallenge(controller.signal);
    usedChallengeToken = !sessionCsrfToken;
    return await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE_URL}${withActiveProfile(path)}`);
      xhr.withCredentials = true;
      xhr.timeout = timeout;
      xhr.setRequestHeader("Accept", "application/json");
      if (token) xhr.setRequestHeader("X-CSRF-Token", token);
      xhr.upload.addEventListener("progress", event => {
        if (event.lengthComputable) onProgress?.(event.loaded / event.total);
      });
      xhr.addEventListener("load", () => {
        let payload;
        try {
          payload = xhr.responseType === "json" && xhr.response
            ? xhr.response
            : JSON.parse(xhr.responseText || "null");
        } catch (cause) {
          reject(new ApiError("The server returned invalid JSON.", {
            status: xhr.status,
            code: "invalid_response",
            cause,
          }));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(normalizeErrorPayload(payload || {}, xhr.status));
          return;
        }
        resolve(payload);
      });
      xhr.addEventListener("error", () => reject(new ApiError(
        "The service could not be reached. Check your connection.",
        { code: "network_error" },
      )));
      xhr.addEventListener("timeout", () => reject(new ApiError(
        "The request timed out. Please try again.",
        { code: "timeout" },
      )));
      xhr.addEventListener("abort", () => reject(new ApiError(
        "The request timed out. Please try again.",
        { code: "timeout" },
      )));
      controller.signal.addEventListener("abort", () => xhr.abort(), { once: true });
      xhr.send(formData);
    });
  } catch (error) {
    const apiError = error instanceof ApiError
      ? error
      : new ApiError("The upload could not be completed.", { code: "network_error", cause: error });
    handleGlobalApiError(apiError);
    throw apiError;
  } finally {
    if (usedChallengeToken) csrfChallengeToken = "";
    window.clearTimeout(timeoutId);
  }
}

export function setCsrfToken(token) {
  sessionCsrfToken = typeof token === "string" ? token : "";
}
