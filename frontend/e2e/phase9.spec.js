import { expect, test } from "@playwright/test";

const password = "Playwright-Sample-Passphrase-53!";

test("compresses page HTML and gives fingerprinted frontend assets immutable caching", async ({ request, baseURL }) => {
  const response = await request.get(`${baseURL}/landing.html`, {
    headers: { "Accept-Encoding": "gzip" },
  });
  expect(response.status()).toBe(200);
  expect(response.headers()["content-encoding"]).toBe("gzip");
  expect(response.headers()["cache-control"]).toBe("no-cache");
  const html = await response.text();
  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+\.[a-f0-9]{8,}\.(?:js|css))"/g)];
  expect(assets.length).toBeGreaterThan(0);
  for (const [, assetPath] of assets) {
    const asset = await request.get(`${baseURL}${assetPath}`);
    expect(asset.status(), assetPath).toBe(200);
    expect(asset.headers()["cache-control"], assetPath).toBe("public, max-age=31536000, immutable");
  }
});

async function registerAndFinishOnboarding(page, name, email) {
  await page.goto("/register.html");
  await page.getByLabel("Your name").fill(name);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Create password").fill(password);
  const registerResponse = page.waitForResponse(response =>
    response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/auth/register"),
  );
  await page.getByRole("button", { name: "Create local account" }).click();
  const registrationCookieHeader = await (await registerResponse).headerValue("set-cookie");
  await expect(page.locator("#recovery-panel")).toBeVisible();
  await page.locator("#recovery-saved").check();
  await page.getByRole("button", { name: /I've saved it/ }).click();

  await expect(page).toHaveURL(/app-shell\.html/);
  await expect(page.locator("[data-onboarding-form]")).toBeVisible();
  await page.getByLabel("Profile name").fill(name);
  await page.getByRole("button", { name: /Continue/ }).click();
  await page.getByLabel("Date of birth").fill("1990-05-16");
  await page.getByRole("button", { name: /Continue/ }).click();
  await page.getByLabel("Allergies").fill("peanuts");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.locator("[data-onboarding-form]")).toHaveCount(0);
  return registrationCookieHeader;
}

async function openFamily(page, hasFamily = false) {
  const alreadyOnFamily = new URL(page.url()).hash.startsWith("#/family");
  await page.goto("/app-shell.html#/family");
  if (alreadyOnFamily) await page.reload();
  await expect(page.locator("#family-dashboard")).toHaveAttribute("data-family-ready", "true");
  if (hasFamily) {
    await expect(page.getByRole("heading", { name: "Your care circle" })).toBeVisible();
  } else {
    await expect(page.locator("[data-family-create]")).toBeVisible();
  }
}

async function addRecord(page, title, visibility, expectedEmail) {
  await page.goto("/app-shell.html#/timeline");
  const identity = await page.evaluate(async () => {
    const response = await fetch("/api/v1/auth/me");
    return response.json();
  });
  expect(identity.email).toBe(expectedEmail);
  await page.getByRole("button", { name: /Add record/ }).first().click();
  await expect(page.locator("#record-form")).toBeVisible();
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Visit summary").fill(`Browser verification record: ${title}`);
  await page.getByLabel("Who can see this").selectOption(visibility);
  await page.getByRole("button", { name: "Add to timeline" }).click();
  await expect(page.locator("#health-record-drawer")).not.toHaveAttribute("open", "");
}

test("signup through shared care, SOS, and logout uses the live API", async ({ browser, baseURL }) => {
  const ownerContext = await browser.newContext();
  const owner = await ownerContext.newPage();
  const ownerEmail = `owner-${Date.now()}@example.com`;
  const errors = [];
  owner.on("pageerror", error => errors.push(error.message));

  const registrationCookieHeader = await registerAndFinishOnboarding(owner, "Riley Owner", ownerEmail);
  expect(registrationCookieHeader).toMatch(/httponly/i);
  expect(registrationCookieHeader).toMatch(/samesite=lax/i);
  expect(await owner.evaluate(() => localStorage.length)).toBe(0);
  await owner.goto("/app-shell.html#/home");
  const eagerFeatureAssets = await owner.evaluate(() =>
    performance.getEntriesByType("resource")
      .map(entry => entry.name)
      .filter(name => /insights-chat-pages|chart\.umd/.test(name)),
  );
  expect(eagerFeatureAssets).toEqual([]);
  await owner.goto("/app-shell.html#/insights");
  await expect(owner.locator("#insights-chart")).toBeVisible();
  expect(errors).toEqual([]);

  const csrfRejection = await owner.evaluate(async () => {
    const response = await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    return response.status;
  });
  expect(csrfRejection).toBe(403);

  const cookies = await ownerContext.cookies();
  const sessionCookie = cookies.find(cookie => cookie.name.toLowerCase().includes("session"));
  expect(sessionCookie).toBeTruthy();
  expect(sessionCookie.httpOnly).toBe(true);

  await owner.goto("/app-shell.html#/timeline");
  await owner.getByRole("button", { name: /Add record/ }).first().click();
  await owner.locator("#record-files").setInputFiles({
    name: "active-content.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from('<svg onload="window.__uploadXss=true"></svg>'),
  });
  await expect(owner.locator("#record-form-error")).toContainText("Use a PDF, PNG, or JPEG");
  await owner.getByRole("button", { name: "Cancel" }).click();
  expect(await owner.evaluate(() => window.__uploadXss || false)).toBe(false);

  await openFamily(owner);
  const familyName = `End-to-end care circle ${Date.now()}`;
  const familyNameInput = owner.getByLabel("Family name");
  await familyNameInput.fill(familyName);
  await expect(familyNameInput).toHaveValue(familyName);
  await owner.getByRole("button", { name: "Create family" }).click();
  await expect(owner.getByRole("heading", { name: "Your care circle" })).toBeVisible();
  const familyId = await owner.evaluate(async name => {
    const response = await fetch("/api/v1/families");
    const families = await response.json();
    return families.find(family => family.name === name).id;
  }, familyName);

  await owner.locator("#invite-role").selectOption("viewer");
  await owner.getByRole("button", { name: "Create invitation link" }).click();
  const inviteURL = await owner.getByLabel("One-time family invitation link").inputValue();
  const inviteCode = new URL(inviteURL, baseURL).hash.match(/[?&]invite=([^&]+)/)?.[1];
  expect(inviteCode).toBeTruthy();

  const memberContext = await browser.newContext();
  const member = await memberContext.newPage();
  const memberEmail = `viewer-${Date.now()}@example.com`;
  await registerAndFinishOnboarding(member, "Riley Viewer", memberEmail);
  const memberIdentity = await member.evaluate(async () => {
    const response = await fetch("/api/v1/auth/me");
    return response.json();
  });
  expect(memberIdentity.email).toBe(memberEmail);
  await member.goto(`/app-shell.html#/family?invite=${inviteCode}`);
  await member.getByRole("button", { name: "Accept invitation" }).click();
  await expect(member.getByText(/2 members \/ roles and consent stay visible here\./)).toBeVisible();
  const joinedIdentity = await member.evaluate(async () => {
    const response = await fetch("/api/v1/auth/me");
    return response.json();
  });
  expect(joinedIdentity.email).toBe(memberEmail);
  const dashboardMembers = () => owner.evaluate(async id => {
    const response = await fetch(`/api/v1/families/${id}/dashboard`);
    const dashboard = await response.json();
    return dashboard.members.length;
  }, familyId);
  expect(await dashboardMembers()).toBe(2);
  const xssTitle = '<img src=x onerror="window.__xssRan=true">';
  await addRecord(member, xssTitle, "family", memberEmail);
  await expect(member.getByText(xssTitle, { exact: true })).toBeVisible();
  expect(await member.locator("img[onerror]").count()).toBe(0);
  expect(await member.evaluate(() => window.__xssRan || false)).toBe(false);
  await addRecord(member, "Private viewer note", "private", memberEmail);
  expect(await dashboardMembers()).toBe(2);

  await openFamily(owner, true);
  await expect(owner.locator("[data-consent-toggle]").first()).toBeVisible();
  await expect(owner.getByRole("combobox", { name: "Role for Riley Viewer" })).toHaveValue("viewer");
  for (const role of ["adult", "guardian", "dependent", "viewer"]) {
    const roleUpdate = owner.waitForResponse(response =>
      response.request().method() === "PATCH"
      && new URL(response.url()).pathname.startsWith(`/api/v1/families/${familyId}/members/`),
    );
    await owner.getByRole("combobox", { name: "Role for Riley Viewer" }).selectOption(role);
    expect((await roleUpdate).status()).toBe(200);
    await expect(owner.getByRole("combobox", { name: "Role for Riley Viewer" })).toHaveValue(role);
  }
  const ownerProfileId = await owner.evaluate(() => {
    const consent = document.querySelector("[data-consent-toggle]");
    return consent?.dataset.sourceProfile;
  });
  expect(ownerProfileId).toBeTruthy();

  await openFamily(member, true);
  const consent = member.locator(
    `[data-consent-toggle][data-recipient-profile="${ownerProfileId}"][data-consent-resource="health_records"]`,
  ).first();
  await expect(consent).toBeVisible();
  if (!(await consent.isChecked())) await consent.check({ force: true });
  await expect(member.getByText("Consent shared.")).toBeVisible();

  await openFamily(owner, true);
  await expect(owner.getByText("Visit note", { exact: true })).toBeVisible();
  await expect(owner.getByText("Riley Viewer", { exact: true })).toBeVisible();
  await expect(owner.getByText("Private viewer note", { exact: true })).toHaveCount(0);
  expect(await owner.locator("img[onerror]").count()).toBe(0);
  expect(await owner.evaluate(() => window.__xssRan || false)).toBe(false);

  await owner.goto("/app-shell.html#/sos");
  await owner.locator("[data-sos-start]").click();
  await expect(owner.locator("[data-cancel-sos]")).toBeVisible();
  await expect(owner.getByText(/SOS alert sent|Alert sent|Delivery status/i)).toBeVisible({ timeout: 15_000 });

  expect(errors).toEqual([]);
  await owner.getByRole("button", { name: "Switch family member" }).click();
  const logout = owner.getByRole("button", { name: /Log out|Sign out/ });
  await expect(logout).toBeVisible();
  await logout.click();
  await expect(owner).toHaveURL(/login\.html/);

  await memberContext.close();
  await ownerContext.close();
});

test("mobile viewport exposes the primary navigation without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/landing.html");
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Sign in" })).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasHorizontalOverflow).toBe(false);
});
