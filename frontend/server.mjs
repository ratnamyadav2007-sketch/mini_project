import { createServer } from "node:http";
import { createHash, randomBytes, randomUUID, scrypt as scryptCallback, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import QRCode from "qrcode";

const scrypt = promisify(scryptCallback);
const ROOT = fileURLToPath(new URL(".", import.meta.url));
const HOST = "127.0.0.1";
const PORT = Number(process.env.PORT || 4173);
const requestedSessionTtl = Number(process.env.SESSION_TTL_SECONDS || 1800);
const SESSION_TTL_SECONDS = Number.isFinite(requestedSessionTtl) ? Math.max(1, requestedSessionTtl) : 1800;
const DATA_DIR = process.env.AUTH_DATA_DIR || join(ROOT, ".local-auth");
const DATABASE_PATH = join(DATA_DIR, "users.json");
const PASSWORD_BYTES = 64;
const MAX_BODY_BYTES = 10 * 1024 * 1024;
const MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_ATTACHMENT_BYTES = 6 * 1024 * 1024;
const RECORD_TYPES = new Set(["appointment", "medication", "symptom", "allergy", "condition", "milestone", "measurement"]);
const VISIBILITIES = new Set(["private", "selected", "family"]);
const sessions = new Map();
const attempts = new Map();
const staticFiles = new Map([
  ["/landing.html", "landing.html"],
  ["/login.html", "login.html"],
  ["/register.html", "register.html"],
  ["/reset.html", "reset.html"],
  ["/auth.css", "auth.css"],
  ["/auth.js", "auth.js"],
  ["/app-shell.html", "app-shell.html"],
  ["/app-shell.css", "app-shell.css"],
  ["/app-shell.js", "app-shell.js"],
  ["/health-pages.css", "health-pages.css"],
  ["/health-pages.js", "health-pages.js"],
  ["/emergency-pages.css", "emergency-pages.css"],
  ["/emergency-pages.js", "emergency-pages.js"],
  ["/health-card.html", "health-card.html"],
  ["/health-card.css", "health-card.css"],
  ["/health-card.js", "health-card.js"],
  ["/wellness-pages.css", "wellness-pages.css"],
  ["/wellness-pages.js", "wellness-pages.js"],
  ["/wellness-api-pages.js", "wellness-api-pages.js"],
  ["/insights-chat-pages.js", "insights-chat-pages.js"],
  ["/chart.umd.js", "chart.umd.js"],
  ["/js/api.js", "js/api.js"],
  ["/js/bootstrap.js", "js/bootstrap.js"],
  ["/js/store.js", "js/store.js"],
  ["/js/ui.js", "js/ui.js"],
  ["/js/ui.css", "js/ui.css"],
  ["/service-worker.js", "service-worker.js"],
  ["/emergency-pages.css", "emergency-pages.css"],
  ["/emergency-pages.js", "emergency-pages.js"],
  ["/health-card.html", "health-card.html"],
  ["/health-card.css", "health-card.css"],
  ["/health-card.js", "health-card.js"],
  ["/service-worker.js", "service-worker.js"],
  ["/tokens.css", "tokens.css"],
  ["/icons.svg", "icons.svg"],
  ["/design-direction.html", "design-direction.html"],
  ["/styleguide.html", "styleguide.html"],
]);

let users = [];
let families = [];
let writeQueue = Promise.resolve();

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function constantTimeHexEqual(left, right) {
  const leftBuffer = Buffer.from(left, "hex");
  const rightBuffer = Buffer.from(right, "hex");
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

function normaliseEmail(value) {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function validEmail(value) {
  return value.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function validPassword(value) {
  return typeof value === "string" && value.length >= 12 && value.length <= 128;
}

function normaliseRecoveryCode(value) {
  return typeof value === "string" ? value.replace(/[\s-]/g, "").toUpperCase() : "";
}

function isLoopbackRequest(request) {
  const remote = request.socket.remoteAddress;
  const localAddress = remote === "127.0.0.1" || remote === "::1" || remote === "::ffff:127.0.0.1";
  if (!localAddress) return false;

  try {
    const host = new URL(`http://${request.headers.host || ""}`);
    if (!new Set(["localhost", "127.0.0.1"]).has(host.hostname)) return false;
    if (Number(host.port || 80) !== PORT) return false;
    return true;
  } catch {
    return false;
  }
}

function originIsLocal(request) {
  const origin = request.headers.origin;
  if (!origin) return true;
  try {
    const url = new URL(origin);
    return url.protocol === "http:" &&
      new Set(["localhost", "127.0.0.1"]).has(url.hostname) &&
      Number(url.port || 80) === PORT;
  } catch {
    return false;
  }
}

function sendJson(response, status, data, headers = {}) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    ...headers,
  });
  response.end(JSON.stringify(data));
}

function sessionCookie(token) {
  return `fieldnote_session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_TTL_SECONDS}`;
}

const expiredCookie = "fieldnote_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0";

function readCookie(request, key) {
  const cookies = request.headers.cookie || "";
  for (const item of cookies.split(";")) {
    const separator = item.indexOf("=");
    if (separator < 0) continue;
    if (item.slice(0, separator).trim() === key) return item.slice(separator + 1).trim();
  }
  return "";
}

function getSession(request) {
  const token = readCookie(request, "fieldnote_session");
  const session = sessions.get(token);
  if (!session) return null;
  if (session.expiresAt <= Date.now()) {
    sessions.delete(token);
    return null;
  }
  return { ...session, token };
}

function createSession(email) {
  const token = randomBytes(32).toString("base64url");
  const expiresAt = Date.now() + SESSION_TTL_SECONDS * 1000;
  sessions.set(token, { email, expiresAt });
  return { token, expiresAt };
}

async function derivePassword(password, salt) {
  return scrypt(password, salt, PASSWORD_BYTES, { N: 1 << 15, r: 8, p: 1, maxmem: 64 * 1024 * 1024 });
}

function roleConsent(role) {
  return {
    canViewTimeline: ["owner", "guardian", "caregiver"].includes(role),
    canViewHealthDetails: ["owner", "guardian", "caregiver"].includes(role),
    canViewAttachments: ["owner", "guardian"].includes(role),
    canEditRecords: ["owner", "guardian"].includes(role),
  };
}

function createFamily(owner, id = randomUUID()) {
  return {
    id,
    name: `${owner.email.split("@")[0]}'s family`,
    members: [{ email: owner.email, role: "owner", consent: roleConsent("owner"), joinedAt: new Date().toISOString() }],
    invites: [],
    goals: [],
  };
}

async function readUsers() {
  try {
    const parsed = JSON.parse(await readFile(DATABASE_PATH, "utf8"));
    if (!Array.isArray(parsed.users)) throw new Error("Invalid local auth database.");
    users = parsed.users;
    families = Array.isArray(parsed.families) ? parsed.families : [];
    let migrated = !Array.isArray(parsed.families);
    for (const user of users) {
      user.profiles ||= [];
      user.records ||= [];
      let family = families.find(item => item.id === user.familyId);
      if (!family) {
        family = createFamily(user, user.familyId || randomUUID());
        user.familyId = family.id;
        families.push(family);
        migrated = true;
      }
      family.members ||= [];
      family.invites ||= [];
      family.goals ||= [];
      if (!family.members.some(member => member.email === user.email)) {
        family.members.push({ email: user.email, role: "owner", consent: roleConsent("owner"), joinedAt: user.createdAt || new Date().toISOString() });
        migrated = true;
      }
    }
    if (migrated) await persistUsers(users, families);
    return users;
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

async function persistUsers(nextUsers, nextFamilies = families) {
  await mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
  const temporaryPath = `${DATABASE_PATH}.${process.pid}.tmp`;
  await writeFile(temporaryPath, JSON.stringify({ version: 2, users: nextUsers, families: nextFamilies }, null, 2), { mode: 0o600 });
  await rename(temporaryPath, DATABASE_PATH);
  users = nextUsers;
  families = nextFamilies;
}

function serialiseMutation(work) {
  const next = writeQueue.then(work, work);
  writeQueue = next.catch(() => {});
  return next;
}

async function readJsonBody(request) {
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new HttpError(413, "That request is too large.");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new HttpError(400, "Enter the requested details and try again.");
  }
}

function allowAttempt(request) {
  const key = request.socket.remoteAddress || "local";
  const now = Date.now();
  const active = (attempts.get(key) || []).filter(time => now - time < 15 * 60 * 1000);
  if (active.length >= 12) {
    attempts.set(key, active);
    return false;
  }
  active.push(now);
  attempts.set(key, active);
  return true;
}

function clearAttempts(request) {
  attempts.delete(request.socket.remoteAddress || "local");
}

function generateRecoveryCodes() {
  return Array.from({ length: 8 }, () => {
    const raw = randomBytes(16).toString("hex").toUpperCase();
    return raw.match(/.{1,8}/g).join("-");
  });
}

function cleanText(value, maximum = 500) {
  return typeof value === "string" ? value.trim().replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "").slice(0, maximum) : "";
}

function cleanStringList(value, maximumItems = 30) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(item => cleanText(item, 120)).filter(Boolean))].slice(0, maximumItems);
}

function validDate(value) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00.000Z`));
}

function profilePayload(body) {
  const name = cleanText(body.name, 100);
  const relationship = cleanText(body.relationship, 30);
  const birthDate = cleanText(body.birthDate, 10);
  const bloodGroup = cleanText(body.bloodGroup, 4).toUpperCase();
  if (!name) throw new HttpError(400, "Add a name for this profile.");
  if (!new Set(["you", "child", "partner", "parent", "family member"]).has(relationship)) {
    throw new HttpError(400, "Choose a relationship for this profile.");
  }
  if (birthDate && !validDate(birthDate)) throw new HttpError(400, "Enter a valid birth date.");
  if (bloodGroup && !new Set(["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]).has(bloodGroup)) {
    throw new HttpError(400, "Choose a valid blood group or leave it blank.");
  }
  const allowedFields = cleanStringList(body.allergies);
  const conditions = cleanStringList(body.conditions);
  return { name, relationship, birthDate, bloodGroup, allergies: allowedFields, conditions };
}

function attachmentPayload(value) {
  if (!Array.isArray(value)) return [];
  if (value.length > 4) throw new HttpError(400, "Attach up to four files to one record.");
  let totalBytes = 0;
  return value.map(file => {
    const name = cleanText(file?.name, 140);
    const type = cleanText(file?.type, 100).toLowerCase();
    const dataUrl = typeof file?.dataUrl === "string" ? file.dataUrl : "";
    const match = /^data:(application\/pdf|image\/(?:png|jpeg|webp)|text\/plain);base64,([A-Za-z0-9+/]+={0,2})$/.exec(dataUrl);
    if (!name || !match || match[1] !== type) throw new HttpError(400, "Use a PDF, PNG, JPEG, WebP, or plain-text attachment.");
    const bytes = Buffer.from(match[2], "base64");
    if (bytes.length > MAX_ATTACHMENT_BYTES) throw new HttpError(400, "Each attachment must be 2 MB or smaller.");
    totalBytes += bytes.length;
    if (totalBytes > MAX_TOTAL_ATTACHMENT_BYTES) throw new HttpError(400, "Attachments must total 6 MB or less.");
    return { id: randomUUID(), name, type, size: bytes.length, dataUrl };
  });
}

function recordPayload(body) {
  const type = cleanText(body.type, 30);
  const title = cleanText(body.title, 140);
  const recordDate = cleanText(body.date, 10);
  const visibility = cleanText(body.visibility, 20);
  const selectedProfileIds = cleanStringList(body.selectedProfileIds, 50);
  const selectedMemberEmails = cleanStringList(body.selectedMemberEmails, 12).map(normaliseEmail);
  if (!RECORD_TYPES.has(type)) throw new HttpError(400, "Choose a record type.");
  if (!title) throw new HttpError(400, "Add a title for this record.");
  if (!validDate(recordDate)) throw new HttpError(400, "Enter a valid record date.");
  if (!VISIBILITIES.has(visibility)) throw new HttpError(400, "Choose who can see this record.");
  if (visibility === "selected" && selectedProfileIds.length + selectedMemberEmails.length === 0) throw new HttpError(400, "Choose at least one person for selected visibility.");
  if (selectedMemberEmails.some(email => !validEmail(email))) throw new HttpError(400, "Selected family members must have valid account emails.");
  const details = {};
  for (const [key, maximum] of Object.entries({ provider: 160, location: 180, medication: 140, dosage: 100, frequency: 120, time: 5, reminderMinutes: 4, reminderStatus: 20, snoozedUntil: 32, symptom: 160, severity: 20, substance: 140, reaction: 180, condition: 160, status: 40, measurement: 120, value: 60, unit: 40, milestone: 180, note: 4000 })) {
    const value = cleanText(body.details?.[key], maximum);
    if (value) details[key] = value;
  }
  const requiredByType = {
    appointment: ["provider"],
    medication: ["medication"],
    symptom: ["symptom"],
    allergy: ["substance"],
    condition: ["condition"],
    milestone: [],
    measurement: ["measurement", "value"],
  };
  for (const key of requiredByType[type]) {
    if (!details[key]) throw new HttpError(400, `Add ${key === "provider" ? "a provider or place" : key === "medication" ? "a medication name" : key === "symptom" ? "a symptom" : key === "substance" ? "an allergen" : key === "condition" ? "a condition" : `the ${key}`} for this record.`);
  }
  if (visibility !== "selected" && (selectedProfileIds.length || selectedMemberEmails.length)) throw new HttpError(400, "Selected visibility needs selected family members.");
  return { type, title, date: recordDate, visibility, selectedProfileIds, selectedMemberEmails, details, attachments: attachmentPayload(body.attachments) };
}

async function persistAccountUsers(nextUsers) {
  await persistUsers(nextUsers);
  users = nextUsers;
}

function familyForUser(user) {
  return families.find(family => family.id === user.familyId) || null;
}

function familyMember(family, email) {
  return family?.members.find(member => member.email === email) || null;
}

function familyProfileName(user, profileId) {
  return user.profiles?.find(profile => profile.id === profileId)?.name || "Family member";
}

function recordForViewer(record, owner, viewer, family) {
  const isOwner = owner.email === viewer.email;
  const viewerMembership = familyMember(family, viewer.email);
  if (!isOwner) {
    const consent = viewerMembership?.consent || roleConsent(viewerMembership?.role || "viewer");
    const sharedWithViewer = record.visibility === "family" || (record.visibility === "selected" && (record.selectedMemberEmails || []).includes(viewer.email));
    if (!consent.canViewTimeline || !sharedWithViewer) return null;
    const redactDetails = !consent.canViewHealthDetails;
    return {
      ...record,
      ownerEmail: owner.email,
      ownerName: owner.profiles?.[0]?.name || owner.email,
      profileName: familyProfileName(owner, record.profileId),
      canEdit: Boolean(consent.canEditRecords),
      redacted: redactDetails,
      details: redactDetails ? {} : record.details,
      attachments: redactDetails || !consent.canViewAttachments ? [] : record.attachments,
      attachmentsHidden: !redactDetails && !consent.canViewAttachments && Boolean(record.attachments?.length),
      title: redactDetails ? `${record.type[0].toUpperCase()}${record.type.slice(1)} record` : record.title,
    };
  }
  return {
    ...record,
    ownerEmail: owner.email,
    ownerName: owner.profiles?.[0]?.name || owner.email,
    profileName: familyProfileName(owner, record.profileId),
    canEdit: true,
    redacted: false,
    attachmentsHidden: false,
  };
}

function recordsVisibleTo(user, family, profileId = "") {
  const visible = [];
  for (const owner of users) {
    if (owner.familyId !== family?.id) continue;
    for (const record of owner.records || []) {
      const view = recordForViewer(record, owner, user, family);
      if (!view) continue;
      if (owner.email === user.email && profileId) {
        const visibleToProfile = record.visibility === "family" || record.profileId === profileId || (record.visibility === "selected" && record.selectedProfileIds.includes(profileId));
        if (!visibleToProfile) continue;
      }
      visible.push(view);
    }
  }
  return visible;
}

function publicFamilyMember(member, account, viewerEmail, viewerConsent) {
  const isSelf = member.email === viewerEmail;
  const consent = member.consent || roleConsent(member.role);
  const revealProfiles = isSelf || Boolean(viewerConsent?.canViewHealthDetails);
  const profiles = revealProfiles ? (account.profiles || []).map(profile => ({
    id: profile.id,
    name: profile.name,
    relationship: profile.relationship,
    birthDate: profile.birthDate,
    bloodGroup: profile.bloodGroup,
    allergies: profile.allergies,
    conditions: profile.conditions,
    onboardingComplete: profile.onboardingComplete,
  })) : [];
  return {
    email: member.email,
    displayName: profiles[0]?.name || (isSelf ? member.email : member.email),
    role: member.role,
    consent,
    joinedAt: member.joinedAt,
    isSelf,
    profiles,
  };
}

function inviteByCode(code) {
  const codeHash = digest(cleanText(code, 120));
  for (const family of families) {
    const invite = family.invites?.find(item => constantTimeHexEqual(item.codeHash, codeHash));
    if (invite) return { family, invite };
  }
  return null;
}

function publicInvite(invite) {
  return { id: invite.id, email: invite.email, role: invite.role, invitedBy: invite.invitedBy, createdAt: invite.createdAt, expiresAt: invite.expiresAt };
}

function requireFamilyManager(member) {
  if (!member || !["owner", "guardian"].includes(member.role)) throw new HttpError(403, "Only a family owner or guardian can manage this setting.");
}

async function acceptFamilyInvite(userIndex, code) {
  return serialiseMutation(async () => {
    const user = users[userIndex];
    const match = inviteByCode(code);
    if (!match || match.invite.expiresAt <= Date.now()) throw new HttpError(400, "That invitation is invalid or has expired.");
    if (match.invite.email !== user.email) throw new HttpError(403, "Sign in with the email address this invitation was sent to.");
    if (match.family.members.some(member => member.email === user.email)) throw new HttpError(409, "This account is already in the family circle.");
    if (match.family.members.length >= 12) throw new HttpError(400, "This family circle has reached its member limit.");

    let nextFamilies = families.filter(family => family.id !== match.family.id);
    if (user.familyId && user.familyId !== match.family.id) {
      const previousFamily = families.find(family => family.id === user.familyId);
      if (previousFamily && previousFamily.members.some(member => member.email !== user.email)) {
        throw new HttpError(409, "This account already belongs to another family circle. Use a separate local account to join this invitation.");
      }
      nextFamilies = nextFamilies.filter(family => family.id !== previousFamily?.id);
    }

    const updatedFamily = {
      ...match.family,
      members: [...match.family.members, { email: user.email, role: match.invite.role, consent: roleConsent(match.invite.role), joinedAt: new Date().toISOString() }],
      invites: match.family.invites.filter(invite => invite.id !== match.invite.id),
    };
    nextFamilies.push(updatedFamily);
    const nextUsers = users.map((item, index) => index === userIndex ? { ...item, familyId: updatedFamily.id } : item);
    await persistUsers(nextUsers, nextFamilies);
    return { familyId: updatedFamily.id, role: match.invite.role };
  });
}

async function handleFamilyApi(request, response, url) {
  const session = getSession(request);
  if (!session) {
    sendJson(response, 401, { error: "Your session has expired." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const userIndex = users.findIndex(user => user.email === session.email);
  if (userIndex < 0) {
    sendJson(response, 401, { error: "Your account is no longer available." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const user = users[userIndex];
  const family = familyForUser(user);
  const actor = familyMember(family, user.email);
  if (!family || !actor) throw new HttpError(403, "This account is not part of a family circle.");

  if (url.pathname === "/api/family" && request.method === "GET") {
    const accounts = family.members.map(member => users.find(account => account.email === member.email)).filter(Boolean);
    const visibleRecords = recordsVisibleTo(user, family).sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
    const today = new Date().toISOString().slice(0, 10);
    const feed = visibleRecords.slice(0, 12).map(record => ({ ...record, attachments: (record.attachments || []).map(({ id, name, type, size }) => ({ id, name, type, size })) }));
    const appointments = visibleRecords.filter(record => record.type === "appointment" && record.date >= today).sort((left, right) => left.date.localeCompare(right.date)).slice(0, 8);
    sendJson(response, 200, {
      family: { id: family.id, name: family.name },
      currentEmail: user.email,
      currentRole: actor.role,
      members: family.members.map(member => {
        const account = accounts.find(item => item.email === member.email);
        return publicFamilyMember(member, account, user.email, actor.consent);
      }),
      invites: ["owner", "guardian"].includes(actor.role) ? family.invites.filter(invite => invite.expiresAt > Date.now()).map(publicInvite) : [],
      updates: feed,
      upcomingAppointments: appointments,
      goals: family.goals || [],
    });
    return;
  }

  if (url.pathname === "/api/family/invites" && request.method === "POST") {
    requireFamilyManager(actor);
    const body = await readJsonBody(request);
    const email = normaliseEmail(body.email);
    const role = cleanText(body.role, 20);
    if (!validEmail(email)) throw new HttpError(400, "Enter a valid email address for the invitation.");
    if (!new Set(["guardian", "caregiver", "viewer"]).has(role)) throw new HttpError(400, "Choose a valid family role.");
    if (email === user.email || family.members.some(member => member.email === email)) throw new HttpError(409, "This person is already in the family circle.");
    if (family.members.length + family.invites.length >= 12) throw new HttpError(400, "This family circle has reached its member limit.");
    const inviteCode = randomBytes(24).toString("base64url");
    const invite = { id: randomUUID(), email, role, codeHash: digest(inviteCode), invitedBy: user.email, createdAt: new Date().toISOString(), expiresAt: Date.now() + 7 * 24 * 60 * 60 * 1000 };
    const nextFamilies = families.map(item => item.id === family.id ? { ...item, invites: [...item.invites, invite] } : item);
    await persistUsers(users, nextFamilies);
    sendJson(response, 201, { invite: publicInvite(invite), inviteCode, inviteUrl: `http://${HOST}:${PORT}/register.html?invite=${encodeURIComponent(inviteCode)}` });
    return;
  }

  if (url.pathname === "/api/family/invites/accept" && request.method === "POST") {
    const body = await readJsonBody(request);
    const accepted = await acceptFamilyInvite(userIndex, body.code);
    sendJson(response, 200, { accepted: true, role: accepted.role, familyId: accepted.familyId });
    return;
  }

  const roleMatch = /^\/api\/family\/members\/([^/]+)\/role$/.exec(url.pathname);
  if (roleMatch && request.method === "PATCH") {
    if (actor.role !== "owner") throw new HttpError(403, "Only the family owner can change roles.");
    const email = normaliseEmail(decodeURIComponent(roleMatch[1]));
    const body = await readJsonBody(request);
    const role = cleanText(body.role, 20);
    if (!new Set(["guardian", "caregiver", "viewer"]).has(role)) throw new HttpError(400, "Choose a valid family role.");
    if (email === user.email) throw new HttpError(400, "The family owner role cannot be changed.");
    const target = familyMember(family, email);
    if (!target) throw new HttpError(404, "That family member could not be found.");
    const nextFamilies = families.map(item => item.id === family.id ? { ...item, members: item.members.map(member => member.email === email ? { ...member, role, consent: roleConsent(role) } : member) } : item);
    await persistUsers(users, nextFamilies);
    sendJson(response, 200, { member: publicFamilyMember(familyMember(nextFamilies.find(item => item.id === family.id), email), users.find(item => item.email === email), user.email, actor.consent) });
    return;
  }

  const consentMatch = /^\/api\/family\/members\/([^/]+)\/consent$/.exec(url.pathname);
  if (consentMatch && request.method === "PUT") {
    const email = normaliseEmail(decodeURIComponent(consentMatch[1]));
    if (email !== user.email) requireFamilyManager(actor);
    const body = await readJsonBody(request);
    const target = familyMember(family, email);
    if (!target) throw new HttpError(404, "That family member could not be found.");
    const consent = Object.fromEntries(["canViewTimeline", "canViewHealthDetails", "canViewAttachments", "canEditRecords"].map(key => [key, Boolean(body[key])]));
    if (!consent.canViewTimeline) {
      consent.canViewHealthDetails = false;
      consent.canViewAttachments = false;
      consent.canEditRecords = false;
    }
    if (!consent.canViewHealthDetails) {
      consent.canViewAttachments = false;
      consent.canEditRecords = false;
    }
    if (!new Set(["owner", "guardian"]).has(target.role)) consent.canEditRecords = false;
    const nextFamilies = families.map(item => item.id === family.id ? { ...item, members: item.members.map(member => member.email === email ? { ...member, consent } : member) } : item);
    await persistUsers(users, nextFamilies);
    sendJson(response, 200, { consent });
    return;
  }

  if (url.pathname === "/api/family/goals" && request.method === "POST") {
    requireFamilyManager(actor);
    const body = await readJsonBody(request);
    const title = cleanText(body.title, 120);
    const targetDate = cleanText(body.targetDate, 10);
    if (!title) throw new HttpError(400, "Add a name for this family goal.");
    if (targetDate && !validDate(targetDate)) throw new HttpError(400, "Choose a valid goal date.");
    const goal = { id: randomUUID(), title, targetDate, completed: false, createdAt: new Date().toISOString(), createdBy: user.email };
    const nextFamilies = families.map(item => item.id === family.id ? { ...item, goals: [...(item.goals || []), goal] } : item);
    await persistUsers(users, nextFamilies);
    sendJson(response, 201, { goal });
    return;
  }

  const goalMatch = /^\/api\/family\/goals\/([^/]+)$/.exec(url.pathname);
  if (goalMatch && request.method === "PATCH") {
    requireFamilyManager(actor);
    const goalId = decodeURIComponent(goalMatch[1]);
    const body = await readJsonBody(request);
    const updatedGoal = (family.goals || []).find(goal => goal.id === goalId);
    if (!updatedGoal) throw new HttpError(404, "That family goal could not be found.");
    const nextFamilies = families.map(item => item.id === family.id ? { ...item, goals: item.goals.map(goal => goal.id === goalId ? { ...goal, completed: Boolean(body.completed), updatedAt: new Date().toISOString() } : goal) } : item);
    await persistUsers(users, nextFamilies);
    sendJson(response, 200, { goal: nextFamilies.find(item => item.id === family.id).goals.find(goal => goal.id === goalId) });
    return;
  }

  sendJson(response, 404, { error: "That family endpoint was not found." });
}

function validPhone(value) {
  const phone = cleanText(value, 40);
  return /^[+]?[-()\s\d.]{7,24}$/.test(phone) && (phone.match(/\d/g) || []).length >= 7;
}

function icsEscape(value) {
  return String(value || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/,/g, "\\,").replace(/;/g, "\\;");
}

function findVisibleProfile(profileId, viewer, family) {
  for (const account of users) {
    const profile = account.profiles?.find(item => item.id === profileId);
    if (!profile || account.familyId !== family?.id) continue;
    if (account.email !== viewer.email) {
      const consent = familyMember(family, viewer.email)?.consent || {};
      if (!consent.canViewTimeline || !consent.canViewHealthDetails) throw new HttpError(403, "This profile is not shared with your account.");
    }
    return { account, profile };
  }
  throw new HttpError(404, "That health profile could not be found.");
}

async function handleSafetyApi(request, response, url) {
  const session = getSession(request);
  if (!session) {
    sendJson(response, 401, { error: "Your session has expired." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const userIndex = users.findIndex(user => user.email === session.email);
  if (userIndex < 0) {
    sendJson(response, 401, { error: "Your account is no longer available." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const user = users[userIndex];
  const family = familyForUser(user);
  const actor = familyMember(family, user.email);
  const profileId = cleanText(url.searchParams.get("profileId"), 80);
  const contactMatch = /^\/api\/emergency\/contacts\/([^/]+)$/.exec(url.pathname);
  const appointmentMatch = /^\/api\/appointments\/([^/]+)$/.exec(url.pathname);

  if (url.pathname === "/api/emergency/contacts" && request.method === "GET") {
    const target = findVisibleProfile(profileId, user, family);
    const source = target.account.emergencyContacts || [];
    const contacts = source.filter(contact => target.account.email === user.email || contact.visibility === "family" || (contact.visibility === "selected" && contact.selectedMemberEmails?.includes(user.email)));
    sendJson(response, 200, { contacts: contacts.slice().sort((left, right) => left.priority - right.priority) });
    return;
  }

  if (url.pathname === "/api/emergency/contacts" && request.method === "POST") {
    const body = await readJsonBody(request);
    if (!users[userIndex].profiles.some(profile => profile.id === body.profileId)) throw new HttpError(400, "Choose one of your profiles for this contact.");
    const name = cleanText(body.name, 100);
    const relationship = cleanText(body.relationship, 60);
    const phone = cleanText(body.phone, 40);
    const visibility = cleanText(body.visibility || "private", 20);
    if (!name || !relationship) throw new HttpError(400, "Add the contact's name and relationship.");
    if (!validPhone(phone)) throw new HttpError(400, "Enter a phone number with at least seven digits.");
    if (!new Set(["private", "selected", "family"]).has(visibility)) throw new HttpError(400, "Choose who can see this contact.");
    const selectedMemberEmails = cleanStringList(body.selectedMemberEmails, 12).map(normaliseEmail);
    if (visibility === "selected" && !selectedMemberEmails.length) throw new HttpError(400, "Choose at least one family member.");
    if (selectedMemberEmails.some(email => !family.members.some(member => member.email === email))) throw new HttpError(400, "Choose members of this family circle.");
    const contacts = users[userIndex].emergencyContacts || [];
    const contact = { id: randomUUID(), profileId: body.profileId, name, relationship, phone, visibility, selectedMemberEmails, priority: contacts.filter(item => item.profileId === body.profileId).length + 1, createdAt: new Date().toISOString() };
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...users[userIndex], emergencyContacts: [...contacts, contact] };
    await persistAccountUsers(nextUsers);
    sendJson(response, 201, { contact });
    return;
  }

  if (url.pathname === "/api/emergency/contacts/order" && request.method === "PUT") {
    const body = await readJsonBody(request);
    if (!users[userIndex].profiles.some(profile => profile.id === body.profileId)) throw new HttpError(400, "Choose one of your profiles.");
    const ownContacts = (users[userIndex].emergencyContacts || []).filter(contact => contact.profileId === body.profileId);
    const orderedIds = cleanStringList(body.orderedIds, 30);
    if (orderedIds.length !== ownContacts.length || new Set(orderedIds).size !== ownContacts.length || ownContacts.some(contact => !orderedIds.includes(contact.id))) {
      throw new HttpError(400, "The new order must include every contact exactly once.");
    }
    const priorities = new Map(orderedIds.map((id, index) => [id, index + 1]));
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...users[userIndex], emergencyContacts: users[userIndex].emergencyContacts.map(contact => priorities.has(contact.id) ? { ...contact, priority: priorities.get(contact.id) } : contact) };
    await persistAccountUsers(nextUsers);
    sendJson(response, 200, { contacts: nextUsers[userIndex].emergencyContacts.filter(contact => contact.profileId === body.profileId).sort((left, right) => left.priority - right.priority) });
    return;
  }

  if (contactMatch && request.method === "DELETE") {
    const contactId = decodeURIComponent(contactMatch[1]);
    const contacts = users[userIndex].emergencyContacts || [];
    if (!contacts.some(contact => contact.id === contactId)) throw new HttpError(404, "That emergency contact could not be found.");
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...users[userIndex], emergencyContacts: contacts.filter(contact => contact.id !== contactId) };
    await persistAccountUsers(nextUsers);
    sendJson(response, 200, { removed: true });
    return;
  }

  if (url.pathname === "/api/emergency/card" && request.method === "GET") {
    const target = findVisibleProfile(profileId, user, family);
    const sourceContacts = target.account.emergencyContacts || [];
    const contacts = sourceContacts.filter(contact => target.account.email === user.email || contact.visibility === "family" || (contact.visibility === "selected" && contact.selectedMemberEmails?.includes(user.email))).sort((left, right) => left.priority - right.priority);
    const medicationRecords = recordsVisibleTo(user, family, profileId).filter(record => record.type === "medication" && record.profileId === profileId);
    sendJson(response, 200, {
      profile: { id: target.profile.id, name: target.profile.name, birthDate: target.profile.birthDate, bloodGroup: target.profile.bloodGroup, allergies: target.profile.allergies || [], conditions: target.profile.conditions || [] },
      contacts,
      medications: medicationRecords.map(record => ({ title: record.title, medication: record.details?.medication, dosage: record.details?.dosage, frequency: record.details?.frequency })).filter(record => record.medication),
      generatedAt: new Date().toISOString(),
    }, { "Cache-Control": "private, no-store" });
    return;
  }

  if (url.pathname === "/api/emergency/card/qr" && request.method === "GET") {
    findVisibleProfile(profileId, user, family);
    const cardUrl = new URL(`/health-card.html?profileId=${encodeURIComponent(profileId)}`, `http://${request.headers.host}`).href;
    const qrDataUrl = await QRCode.toDataURL(cardUrl, { errorCorrectionLevel: "M", margin: 1, width: 280, color: { dark: "#24342e", light: "#fffefa" } });
    sendJson(response, 200, { cardUrl, qrDataUrl }, { "Cache-Control": "private, no-store" });
    return;
  }

  if (url.pathname === "/api/appointments" && request.method === "GET") {
    const search = cleanText(url.searchParams.get("q"), 120).toLowerCase();
    const type = cleanText(url.searchParams.get("view"), 10);
    const records = recordsVisibleTo(user, family, profileId).filter(record => record.type === "appointment" && (!search || `${record.title} ${record.details?.provider || ""} ${record.details?.location || ""}`.toLowerCase().includes(search))).sort((left, right) => left.date.localeCompare(right.date) || String(left.details?.time || "").localeCompare(String(right.details?.time || "")));
    sendJson(response, 200, { appointments: records, view: type || "list" });
    return;
  }

  if (url.pathname === "/api/appointments" && request.method === "POST") {
    const body = await readJsonBody(request);
    if (!users[userIndex].profiles.some(profile => profile.id === body.profileId)) throw new HttpError(400, "Choose one of your profiles for this appointment.");
    if (!actor?.consent?.canEditRecords && actor?.role !== "owner") throw new HttpError(403, "Your family role does not allow creating appointments.");
    const reminderMinutes = Number(body.reminderMinutes || 30);
    if (![0, 5, 10, 15, 30, 60, 1440].includes(reminderMinutes)) throw new HttpError(400, "Choose a valid reminder interval.");
    const payload = recordPayload({ profileId: body.profileId, type: "appointment", title: body.title, date: body.date, visibility: body.visibility || "family", selectedProfileIds: body.selectedProfileIds || [], selectedMemberEmails: body.selectedMemberEmails || [], details: { provider: body.provider, location: body.location, time: cleanText(body.time, 5), reminderMinutes: String(reminderMinutes), reminderStatus: "pending", note: body.note } });
    const timestamp = new Date().toISOString();
    const appointment = { id: randomUUID(), profileId: body.profileId, ...payload, createdAt: timestamp, updatedAt: timestamp, audit: [{ action: "created", at: timestamp, actor: user.email }] };
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...user, records: [...(user.records || []), appointment] };
    await persistAccountUsers(nextUsers);
    sendJson(response, 201, { appointment });
    return;
  }

  if (url.pathname === "/api/appointments/export.ics" && request.method === "GET") {
    const appointments = recordsVisibleTo(user, family, profileId).filter(record => record.type === "appointment");
    const events = appointments.map(record => {
      const date = record.date.replaceAll("-", "");
      const time = /^\d{2}:\d{2}$/.test(record.details?.time || "") ? `T${record.details.time.replace(":", "")}00` : "";
      const end = time ? `DTEND:${date}T${String((Number(record.details.time.slice(0, 2)) + 1) % 24).padStart(2, "0")}${record.details.time.slice(3, 5)}00` : "";
      return ["BEGIN:VEVENT", `UID:${record.id}@fieldnote.local`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "")}`, time ? `DTSTART:${date}${time}` : `DTSTART;VALUE=DATE:${date}`, end, `SUMMARY:${icsEscape(record.title)}`, `DESCRIPTION:${icsEscape([record.details?.provider, record.details?.location, record.details?.note].filter(Boolean).join(" / "))}`, "END:VEVENT"].filter(Boolean).join("\r\n");
    });
    const calendar = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fieldnote Care//Appointments//EN", "CALSCALE:GREGORIAN", ...events, "END:VCALENDAR"].join("\r\n");
    response.writeHead(200, { "Content-Type": "text/calendar; charset=utf-8", "Content-Disposition": "attachment; filename=fieldnote-appointments.ics", "Cache-Control": "no-store" });
    response.end(calendar);
    return;
  }

  if (appointmentMatch && request.method === "PATCH") {
    const appointmentId = decodeURIComponent(appointmentMatch[1]);
    const body = await readJsonBody(request);
    const ownerIndex = users.findIndex(account => account.familyId === family.id && account.records?.some(record => record.id === appointmentId && record.type === "appointment"));
    const existing = users[ownerIndex]?.records.find(record => record.id === appointmentId);
    if (!existing) throw new HttpError(404, "That appointment could not be found.");
    if (ownerIndex !== userIndex && !actor?.consent?.canEditRecords) throw new HttpError(403, "Your family role does not allow changing this reminder.");
    if (!new Set(["snooze", "complete", "reopen"]).has(body.action)) throw new HttpError(400, "Choose snooze, complete, or reopen.");
    const timestamp = new Date().toISOString();
    const details = { ...existing.details };
    if (body.action === "snooze") {
      const minutes = Number(body.minutes || 10);
      if (![5, 10, 15, 30, 60].includes(minutes)) throw new HttpError(400, "Choose a valid snooze interval.");
      details.snoozedUntil = new Date(Date.now() + minutes * 60 * 1000).toISOString();
      details.reminderStatus = "snoozed";
    } else {
      details.reminderStatus = body.action === "complete" ? "completed" : "pending";
      delete details.snoozedUntil;
    }
    const updated = { ...existing, details, updatedAt: timestamp, audit: [...existing.audit, { action: body.action === "snooze" ? "reminder-snoozed" : body.action === "complete" ? "reminder-completed" : "reminder-reopened", at: timestamp, actor: user.email }] };
    const nextUsers = [...users];
    nextUsers[ownerIndex] = { ...users[ownerIndex], records: users[ownerIndex].records.map(record => record.id === appointmentId ? updated : record) };
    await persistAccountUsers(nextUsers);
    sendJson(response, 200, { appointment: updated });
    return;
  }

  sendJson(response, 404, { error: "That emergency or appointment endpoint was not found." });
}

const PLAN_DAYS = new Set(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]);
const MEAL_SLOTS = new Set(["breakfast", "lunch", "dinner", "snack"]);
const WELLNESS_TYPES = new Set(["steps", "water", "sleep", "weight"]);

function weeklyMealPayload(value) {
  if (!Array.isArray(value) || value.length > 56) throw new HttpError(400, "A weekly plan can contain up to 56 meals.");
  return value.map(meal => {
    const day = cleanText(meal.day, 12);
    const slot = cleanText(meal.slot, 12);
    const name = cleanText(meal.name, 120);
    if (!PLAN_DAYS.has(day) || !MEAL_SLOTS.has(slot) || !name) throw new HttpError(400, "Each meal needs a valid day, meal slot, and name.");
    const nutrients = {};
    for (const [key, maximum] of Object.entries({ calories: 5000, protein: 500, carbs: 1000, fat: 500, fiber: 200 })) {
      const number = Number(meal.nutrients?.[key] || 0);
      if (!Number.isFinite(number) || number < 0 || number > maximum) throw new HttpError(400, `Enter a valid ${key} estimate for ${name}.`);
      nutrients[key] = Math.round(number * 10) / 10;
    }
    return { id: cleanText(meal.id, 80) || randomUUID(), day, slot, name, ingredients: cleanStringList(meal.ingredients, 20), allergens: cleanStringList(meal.allergens, 20), nutrients };
  });
}

function wellnessGoalPayload(body) {
  const type = cleanText(body.type, 20);
  const title = cleanText(body.title, 100);
  const target = Number(body.target);
  if (!WELLNESS_TYPES.has(type)) throw new HttpError(400, "Choose steps, water, sleep, or weight.");
  if (!title) throw new HttpError(400, "Add a name for this wellness goal.");
  const limits = { steps: [500, 50000], water: [250, 10000], sleep: [60, 1440], weight: [2, 400] };
  if (!Number.isFinite(target) || target < limits[type][0] || target > limits[type][1]) throw new HttpError(400, `Choose a valid ${type} target.`);
  return { type, title, target: Math.round(target * 10) / 10 };
}

async function handleDietApi(request, response, url) {
  const session = getSession(request);
  if (!session) {
    sendJson(response, 401, { error: "Your session has expired." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const userIndex = users.findIndex(user => user.email === session.email);
  if (userIndex < 0) {
    sendJson(response, 401, { error: "Your account is no longer available." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const user = users[userIndex];
  user.weeklyPlans ||= [];
  user.wellnessGoals ||= [];
  const profileId = cleanText(url.searchParams.get("profileId"), 80);
  const planMatch = /^\/api\/diet\/plans\/([^/]+)$/.exec(url.pathname);
  const goalLogMatch = /^\/api\/diet\/goals\/([^/]+)\/log$/.exec(url.pathname);
  const goalMatch = /^\/api\/diet\/goals\/([^/]+)$/.exec(url.pathname);

  if (url.pathname === "/api/diet/plan" && request.method === "GET") {
    if (!user.profiles.some(profile => profile.id === profileId)) throw new HttpError(404, "That health profile could not be found.");
    const weekOf = cleanText(url.searchParams.get("weekOf"), 10);
    const plans = user.weeklyPlans.filter(plan => plan.profileId === profileId).sort((left, right) => right.weekOf.localeCompare(left.weekOf));
    const plan = plans.find(item => item.weekOf === weekOf) || plans[0] || null;
    sendJson(response, 200, { plan });
    return;
  }

  if (url.pathname === "/api/diet/plans" && request.method === "POST") {
    const body = await readJsonBody(request);
    if (!user.profiles.some(profile => profile.id === body.profileId)) throw new HttpError(400, "Choose one of your health profiles.");
    if (!validDate(body.weekOf) || new Date(`${body.weekOf}T00:00:00Z`).getUTCDay() !== 1) throw new HttpError(400, "Choose the Monday that starts this plan week.");
    const meals = weeklyMealPayload(body.meals);
    const existing = user.weeklyPlans.find(plan => plan.profileId === body.profileId && plan.weekOf === body.weekOf);
    const timestamp = new Date().toISOString();
    const plan = { id: existing?.id || randomUUID(), profileId: body.profileId, weekOf: body.weekOf, preferences: { dietStyle: cleanText(body.preferences?.dietStyle, 30), allergies: cleanStringList(body.preferences?.allergies), goals: cleanStringList(body.preferences?.goals, 8) }, meals, createdAt: existing?.createdAt || timestamp, updatedAt: timestamp };
    const plans = existing ? user.weeklyPlans.map(item => item.id === plan.id ? plan : item) : [...user.weeklyPlans, plan];
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...user, weeklyPlans: plans };
    await persistAccountUsers(nextUsers);
    sendJson(response, existing ? 200 : 201, { plan });
    return;
  }

  if (planMatch && request.method === "PUT") {
    const planId = decodeURIComponent(planMatch[1]);
    const body = await readJsonBody(request);
    const existing = user.weeklyPlans.find(plan => plan.id === planId);
    if (!existing) throw new HttpError(404, "That weekly plan could not be found.");
    const meals = weeklyMealPayload(body.meals);
    const plan = { ...existing, meals, updatedAt: new Date().toISOString() };
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...user, weeklyPlans: user.weeklyPlans.map(item => item.id === planId ? plan : item) };
    await persistAccountUsers(nextUsers);
    sendJson(response, 200, { plan });
    return;
  }

  if (url.pathname === "/api/diet/goals" && request.method === "GET") {
    if (!user.profiles.some(profile => profile.id === profileId)) throw new HttpError(404, "That health profile could not be found.");
    sendJson(response, 200, { goals: user.wellnessGoals.filter(goal => goal.profileId === profileId) });
    return;
  }

  if (url.pathname === "/api/diet/goals" && request.method === "POST") {
    const body = await readJsonBody(request);
    if (!user.profiles.some(profile => profile.id === body.profileId)) throw new HttpError(400, "Choose one of your health profiles.");
    const payload = wellnessGoalPayload(body);
    const goal = { id: randomUUID(), profileId: body.profileId, ...payload, history: {}, createdAt: new Date().toISOString() };
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...user, wellnessGoals: [...user.wellnessGoals, goal] };
    await persistAccountUsers(nextUsers);
    sendJson(response, 201, { goal });
    return;
  }

  if (goalLogMatch && request.method === "PUT") {
    const goalId = decodeURIComponent(goalLogMatch[1]);
    const body = await readJsonBody(request);
    const goal = user.wellnessGoals.find(item => item.id === goalId);
    if (!goal) throw new HttpError(404, "That wellness goal could not be found.");
    const date = cleanText(body.date, 10);
    const value = Number(body.value);
    const maximum = { steps: 100000, water: 20000, sleep: 1440, weight: 500 }[goal.type];
    if (!validDate(date) || !Number.isFinite(value) || value < 0 || value > maximum) throw new HttpError(400, "Enter a valid daily goal value.");
    const updated = { ...goal, history: { ...goal.history, [date]: Math.round(value * 10) / 10 } };
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...user, wellnessGoals: user.wellnessGoals.map(item => item.id === goalId ? updated : item) };
    await persistAccountUsers(nextUsers);
    sendJson(response, 200, { goal: updated });
    return;
  }

  if (goalMatch && request.method === "DELETE") {
    const goalId = decodeURIComponent(goalMatch[1]);
    if (!user.wellnessGoals.some(goal => goal.id === goalId)) throw new HttpError(404, "That wellness goal could not be found.");
    const nextUsers = [...users];
    nextUsers[userIndex] = { ...user, wellnessGoals: user.wellnessGoals.filter(goal => goal.id !== goalId) };
    await persistAccountUsers(nextUsers);
    sendJson(response, 200, { removed: true });
    return;
  }

  sendJson(response, 404, { error: "That diet planning endpoint was not found." });
}

async function handleHealthApi(request, response, url) {
  const session = getSession(request);
  if (!session) {
    sendJson(response, 401, { error: "Your session has expired." }, { "Set-Cookie": expiredCookie });
    return;
  }
  const userIndex = users.findIndex(user => user.email === session.email);
  if (userIndex < 0) {
    sendJson(response, 401, { error: "Your account is no longer available." }, { "Set-Cookie": expiredCookie });
    return;
  }

  const user = users[userIndex];
  user.profiles ||= [];
  user.records ||= [];
  const family = familyForUser(user);
  const actor = familyMember(family, user.email);
  const profileMatch = /^\/api\/profiles\/([^/]+)$/.exec(url.pathname);
  const recordMatch = /^\/api\/records\/([^/]+)$/.exec(url.pathname);

  if (url.pathname === "/api/profiles" && request.method === "GET") {
    sendJson(response, 200, { profiles: user.profiles });
    return;
  }

  if (url.pathname === "/api/profiles" && request.method === "POST") {
    const body = await readJsonBody(request);
    const profile = await serialiseMutation(async () => {
      if (users[userIndex].profiles.length >= 50) throw new HttpError(400, "This local account has reached its profile limit.");
      const payload = profilePayload(body);
      const timestamp = new Date().toISOString();
      const created = { id: randomUUID(), ...payload, createdAt: timestamp, updatedAt: timestamp, onboardingComplete: Boolean(body.onboardingComplete) };
      const nextUsers = [...users];
      nextUsers[userIndex] = { ...users[userIndex], profiles: [...users[userIndex].profiles, created] };
      await persistAccountUsers(nextUsers);
      return created;
    });
    sendJson(response, 201, { profile });
    return;
  }

  if (profileMatch && request.method === "PATCH") {
    const profileId = decodeURIComponent(profileMatch[1]);
    const body = await readJsonBody(request);
    const profile = await serialiseMutation(async () => {
      const existing = users[userIndex].profiles.find(item => item.id === profileId);
      if (!existing) throw new HttpError(404, "That profile could not be found.");
      const payload = profilePayload({ ...existing, ...body });
      const updated = { ...existing, ...payload, onboardingComplete: Boolean(body.onboardingComplete ?? existing.onboardingComplete), updatedAt: new Date().toISOString() };
      const nextUsers = [...users];
      nextUsers[userIndex] = { ...users[userIndex], profiles: users[userIndex].profiles.map(item => item.id === profileId ? updated : item) };
      await persistAccountUsers(nextUsers);
      return updated;
    });
    sendJson(response, 200, { profile });
    return;
  }

  if (url.pathname === "/api/records" && request.method === "GET") {
    const profileId = url.searchParams.get("profileId");
    const search = cleanText(url.searchParams.get("q"), 120).toLowerCase();
    const type = cleanText(url.searchParams.get("type"), 30);
    const from = cleanText(url.searchParams.get("from"), 4);
    const to = cleanText(url.searchParams.get("to"), 4);
    const records = recordsVisibleTo(user, family, profileId).filter(record => {
      const visibleToProfile = record.ownerEmail !== user.email || !profileId || record.profileId === profileId || record.visibility === "family" || (record.visibility === "selected" && (record.selectedProfileIds || []).includes(profileId));
      const matchesSearch = !search || `${record.title} ${record.details?.note || ""} ${Object.values(record.details || {}).join(" ")}`.toLowerCase().includes(search);
      const matchesType = !type || record.type === type;
      const year = record.date.slice(0, 4);
      const matchesYears = (!from || year >= from) && (!to || year <= to);
      return visibleToProfile && matchesSearch && matchesType && matchesYears;
    }).sort((left, right) => right.date.localeCompare(left.date) || right.updatedAt.localeCompare(left.updatedAt));
    sendJson(response, 200, { records });
    return;
  }

  if (url.pathname === "/api/records" && request.method === "POST") {
    const body = await readJsonBody(request);
    const record = await serialiseMutation(async () => {
      if (!users[userIndex].profiles.some(profile => profile.id === body.profileId)) throw new HttpError(400, "Choose a family profile for this record.");
      const payload = recordPayload(body);
      if (payload.selectedProfileIds.some(id => !users[userIndex].profiles.some(profile => profile.id === id))) {
        throw new HttpError(400, "Selected visibility can only include profiles in this account.");
      }
      if (payload.selectedMemberEmails.some(email => !family.members.some(member => member.email === email))) {
        throw new HttpError(400, "Selected visibility can only include members of this family circle.");
      }
      const timestamp = new Date().toISOString();
      const created = { id: randomUUID(), profileId: body.profileId, ...payload, createdAt: timestamp, updatedAt: timestamp, audit: [{ action: "created", at: timestamp, actor: session.email }] };
      const nextUsers = [...users];
      nextUsers[userIndex] = { ...users[userIndex], records: [...users[userIndex].records, created] };
      await persistAccountUsers(nextUsers);
      return created;
    });
    sendJson(response, 201, { record });
    return;
  }

  if (recordMatch && request.method === "GET") {
    const recordId = decodeURIComponent(recordMatch[1]);
    const owner = users.find(account => account.familyId === family.id && account.records?.some(item => item.id === recordId));
    const original = owner?.records.find(item => item.id === recordId);
    const record = original && owner ? recordForViewer(original, owner, user, family) : null;
    if (!record) throw new HttpError(404, "That timeline record could not be found.");
    sendJson(response, 200, { record });
    return;
  }

  if (recordMatch && request.method === "PUT") {
    const recordId = decodeURIComponent(recordMatch[1]);
    const body = await readJsonBody(request);
    const record = await serialiseMutation(async () => {
      const ownerIndex = users.findIndex(account => account.familyId === family.id && account.records?.some(item => item.id === recordId));
      if (ownerIndex !== userIndex && !actor.consent?.canEditRecords) throw new HttpError(403, "Your family role does not allow editing this record.");
      const existing = users[ownerIndex]?.records.find(item => item.id === recordId);
      if (!existing) throw new HttpError(404, "That timeline record could not be found.");
      const payload = recordPayload({ ...existing, ...body, profileId: existing.profileId, attachments: body.attachments ?? existing.attachments });
      if (payload.selectedProfileIds.some(id => !users[ownerIndex].profiles.some(profile => profile.id === id))) {
        throw new HttpError(400, "Selected visibility can only include profiles in this account.");
      }
      if (payload.selectedMemberEmails.some(email => !family.members.some(member => member.email === email))) {
        throw new HttpError(400, "Selected visibility can only include members of this family circle.");
      }
      const timestamp = new Date().toISOString();
      const updated = { ...existing, ...payload, updatedAt: timestamp, audit: [...existing.audit, { action: "updated", at: timestamp, actor: session.email }] };
      const nextUsers = [...users];
      nextUsers[ownerIndex] = { ...users[ownerIndex], records: users[ownerIndex].records.map(item => item.id === recordId ? updated : item) };
      await persistAccountUsers(nextUsers);
      return updated;
    });
    sendJson(response, 200, { record });
    return;
  }

  sendJson(response, 404, { error: "That profile or record endpoint was not found." });
}

async function handleApi(request, response, url) {
  if (!originIsLocal(request)) {
    sendJson(response, 403, { error: "Requests must come from this local app." });
    return;
  }

  if (url.pathname === "/api/auth/session" && request.method === "GET") {
    const session = getSession(request);
    if (!session) {
      sendJson(response, 401, { error: "Your session has expired." }, { "Set-Cookie": expiredCookie });
      return;
    }
    sendJson(response, 200, { authenticated: true, user: { email: session.email }, expiresAt: session.expiresAt });
    return;
  }

  if (url.pathname === "/api/auth/logout" && request.method === "POST") {
    const session = getSession(request);
    if (session) sessions.delete(session.token);
    sendJson(response, 200, { signedOut: true }, { "Set-Cookie": expiredCookie });
    return;
  }

  if (url.pathname.startsWith("/api/family")) {
    await handleFamilyApi(request, response, url);
    return;
  }

  if (url.pathname.startsWith("/api/emergency/") || url.pathname.startsWith("/api/appointments")) {
    await handleSafetyApi(request, response, url);
    return;
  }

  if (url.pathname.startsWith("/api/diet/")) {
    await handleDietApi(request, response, url);
    return;
  }

  if (url.pathname.startsWith("/api/diet/")) {
    await handleDietApi(request, response, url);
    return;
  }

  if (url.pathname.startsWith("/api/profiles") || url.pathname.startsWith("/api/records")) {
    await handleHealthApi(request, response, url);
    return;
  }

  if (request.method !== "POST") {
    sendJson(response, 405, { error: "That method is not available." }, { Allow: "GET, POST" });
    return;
  }

  if (!allowAttempt(request)) {
    sendJson(response, 429, { error: "Too many attempts. Wait a few minutes and try again." });
    return;
  }

  const body = await readJsonBody(request);

  if (url.pathname === "/api/auth/register") {
    const email = normaliseEmail(body.email);
    if (!validEmail(email)) throw new HttpError(400, "Enter a valid email address.");
    if (!validPassword(body.password)) throw new HttpError(400, "Choose a password with at least 12 characters.");

    const result = await serialiseMutation(async () => {
      if (users.some(user => user.email === email)) throw new HttpError(409, "An account already exists for that email.");
      const salt = randomBytes(16);
      const passwordHash = await derivePassword(body.password, salt);
      const recoveryCodes = generateRecoveryCodes();
      let nextFamilies = families;
      const user = {
        email,
        salt: salt.toString("hex"),
        passwordHash: passwordHash.toString("hex"),
        recoveryCodeHashes: recoveryCodes.map(code => digest(normaliseRecoveryCode(code))),
        profiles: [],
        records: [],
        createdAt: new Date().toISOString(),
      };
      const inviteCode = cleanText(body.inviteCode, 120);
      if (inviteCode) {
        const match = inviteByCode(inviteCode);
        if (!match || match.invite.expiresAt <= Date.now()) throw new HttpError(400, "That invitation is invalid or has expired.");
        if (match.invite.email !== email) throw new HttpError(403, "Use the email address this invitation was sent to.");
        if (match.family.members.length >= 12) throw new HttpError(400, "This family circle has reached its member limit.");
        user.familyId = match.family.id;
        const joinedFamily = {
          ...match.family,
          members: [...match.family.members, { email, role: match.invite.role, consent: roleConsent(match.invite.role), joinedAt: new Date().toISOString() }],
          invites: match.family.invites.filter(invite => invite.id !== match.invite.id),
        };
        nextFamilies = families.map(family => family.id === joinedFamily.id ? joinedFamily : family);
      } else {
        const family = createFamily(user);
        user.familyId = family.id;
        nextFamilies = [...families, family];
      }
      const nextUsers = [...users, user];
      await persistUsers(nextUsers, nextFamilies);
      return { recoveryCodes, joinedFamily: Boolean(inviteCode) };
    });

    clearAttempts(request);
    const session = createSession(email);
    sendJson(response, 201, { authenticated: true, user: { email }, recoveryCodes: result.recoveryCodes, expiresAt: session.expiresAt }, { "Set-Cookie": sessionCookie(session.token) });
    return;
  }

  if (url.pathname === "/api/auth/login") {
    const email = normaliseEmail(body.email);
    const password = typeof body.password === "string" ? body.password : "";
    const user = users.find(record => record.email === email);
    const salt = user ? Buffer.from(user.salt, "hex") : Buffer.alloc(16, 7);
    const expectedHash = user ? Buffer.from(user.passwordHash, "hex") : await derivePassword("not-a-real-password", salt);
    const actualHash = await derivePassword(password.slice(0, 128), salt);
    const passwordMatches = actualHash.length === expectedHash.length && timingSafeEqual(actualHash, expectedHash);
    if (!user || !passwordMatches) throw new HttpError(401, "Email or password is incorrect.");

    clearAttempts(request);
    const session = createSession(email);
    sendJson(response, 200, { authenticated: true, user: { email }, expiresAt: session.expiresAt }, { "Set-Cookie": sessionCookie(session.token) });
    return;
  }

  if (url.pathname === "/api/auth/reset") {
    const email = normaliseEmail(body.email);
    const recoveryCode = normaliseRecoveryCode(body.recoveryCode);
    if (!validEmail(email)) throw new HttpError(400, "Enter the email address for your account.");
    if (!validPassword(body.password)) throw new HttpError(400, "Choose a password with at least 12 characters.");
    if (recoveryCode.length < 24 || recoveryCode.length > 40) throw new HttpError(400, "Enter one of your recovery codes.");

    const result = await serialiseMutation(async () => {
      const userIndex = users.findIndex(record => record.email === email);
      const user = users[userIndex];
      const codeHash = digest(recoveryCode);
      const codeIndex = user?.recoveryCodeHashes.findIndex(stored => constantTimeHexEqual(stored, codeHash)) ?? -1;
      if (!user || codeIndex < 0) throw new HttpError(400, "That email and recovery code could not be verified.");

      const salt = randomBytes(16);
      const passwordHash = await derivePassword(body.password, salt);
      const updatedUser = {
        ...user,
        salt: salt.toString("hex"),
        passwordHash: passwordHash.toString("hex"),
        recoveryCodeHashes: user.recoveryCodeHashes.filter((_, index) => index !== codeIndex),
        passwordUpdatedAt: new Date().toISOString(),
      };
      const nextUsers = users.map((record, index) => index === userIndex ? updatedUser : record);
      await persistUsers(nextUsers);
      users = nextUsers;
      for (const [token, session] of sessions) {
        if (session.email === email) sessions.delete(token);
      }
      return { remainingRecoveryCodes: updatedUser.recoveryCodeHashes.length };
    });

    clearAttempts(request);
    sendJson(response, 200, { passwordReset: true, remainingRecoveryCodes: result.remainingRecoveryCodes }, { "Set-Cookie": expiredCookie });
    return;
  }

  sendJson(response, 404, { error: "That local endpoint was not found." });
}

function setSecurityHeaders(response) {
  response.setHeader("X-Content-Type-Options", "nosniff");
  response.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
  response.setHeader("Cross-Origin-Resource-Policy", "same-origin");
  response.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)");
  response.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'self'; object-src 'none'");
}

async function serveStatic(request, response, url) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end("Method not allowed.");
    return;
  }

  if (url.pathname === "/") {
    response.writeHead(302, { Location: "/landing.html", "Cache-Control": "no-store" });
    response.end();
    return;
  }

  if (url.pathname === "/app-shell.html" && url.searchParams.get("preview") !== "1" && !getSession(request)) {
    const reason = readCookie(request, "fieldnote_session") ? "?reason=expired" : "";
    response.writeHead(302, { Location: `/login.html${reason}`, "Cache-Control": "no-store" });
    response.end();
    return;
  }

  if (url.pathname === "/health-card.html" && !getSession(request)) {
    const reason = readCookie(request, "fieldnote_session") ? "?reason=expired" : "";
    response.writeHead(302, { Location: `/login.html${reason}`, "Cache-Control": "no-store" });
    response.end();
    return;
  }

  const filename = staticFiles.get(url.pathname);
  if (!filename) {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" });
    response.end("Not found.");
    return;
  }

  const extensions = { ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml" };
  const content = await readFile(join(ROOT, filename));
  setSecurityHeaders(response);
  response.writeHead(200, {
    "Content-Type": extensions[extname(filename)] || "application/octet-stream",
    "Cache-Control": "no-store",
    "Content-Length": content.length,
  });
  response.end(request.method === "HEAD" ? undefined : content);
}

async function handleRequest(request, response) {
  setSecurityHeaders(response);
  if (!isLoopbackRequest(request)) {
    sendJson(response, 403, { error: "This prototype is available only on this computer." });
    return;
  }

  let url;
  try {
    url = new URL(request.url || "/", `http://${request.headers.host}`);
  } catch {
    response.writeHead(400);
    response.end("Bad request.");
    return;
  }

  try {
    if (url.pathname.startsWith("/api/")) await handleApi(request, response, url);
    else await serveStatic(request, response, url);
  } catch (error) {
    if (response.headersSent) {
      response.destroy();
      return;
    }
    if (error instanceof HttpError) {
      sendJson(response, error.status, { error: error.message });
      return;
    }
    console.error("Local request failed:", error);
    sendJson(response, 500, { error: "The local service had a problem. Try again." });
  }
}

await mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
users = await readUsers();
const server = createServer((request, response) => { void handleRequest(request, response); });
server.listen(PORT, HOST, () => {
  console.log(`Fieldnote local prototype: http://${HOST}:${PORT}`);
  console.log(`Local auth data directory: ${DATA_DIR}`);
});
