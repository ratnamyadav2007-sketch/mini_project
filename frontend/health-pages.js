(() => {
  const today = new Date().toISOString().slice(0, 10);
  const currentYear = new Date().getFullYear();
  const palette = [
    { color: "#456c58", wash: "#e5ede5" },
    { color: "#416c78", wash: "#e3edef" },
    { color: "#806016", wash: "#f5edcf" },
    { color: "#b94e3d", wash: "#f5e6e1" },
  ];
  const emptyDraft = () => ({ name: "", relationship: "you", birthDate: "", bloodGroup: "", allergies: [], conditions: [] });
  const state = {
    preview: false,
    profiles: [],
    records: [],
    recordTypes: [],
    families: [],
    activeFamilyId: "",
    familyData: null,
    lastInvite: null,
    activeProfileId: "",
    onboardingProfileId: "",
    onboarding: emptyDraft(),
    onboardingStep: 0,
    zoom: "lifetime",
    year: currentYear,
    type: "",
    query: "",
    editingRecord: null,
    attachments: [],
    searchTimer: 0,
    timelineCursor: null,
    timelineHasMore: false,
    timelineLoading: false,
    selectedShareEmail: "",
  };
  const mockProfiles = [
    { id: "preview-riya", name: "Riya Patel", relationship: "you", birthDate: "1990-06-18", bloodGroup: "O+", allergies: [], conditions: [], onboardingComplete: true },
    { id: "preview-maya", name: "Maya Patel", relationship: "child", birthDate: "2018-04-09", bloodGroup: "", allergies: ["Pollen"], conditions: [], onboardingComplete: true },
    { id: "preview-arun", name: "Arun Patel", relationship: "partner", birthDate: "1988-11-24", bloodGroup: "", allergies: [], conditions: [], onboardingComplete: true },
  ];
  const mockRecords = [
    { id: "preview-early", profileId: "preview-riya", type: "milestone", title: "First school day", date: "1996-09-03", visibility: "family", selectedProfileIds: [], details: { note: "A little blue backpack, a big first morning." }, attachments: [], audit: [{ action: "created", at: "2026-01-03T09:00:00.000Z", actor: "Local preview" }] },
    { id: "preview-appointment", profileId: "preview-riya", type: "appointment", title: "Annual visit", date: "2026-08-20", visibility: "family", selectedProfileIds: [], details: { provider: "Dr. Lee", location: "Family clinic", note: "Routine follow-up." }, attachments: [], audit: [{ action: "created", at: "2026-08-20T14:00:00.000Z", actor: "Local preview" }] },
    { id: "preview-symptom", profileId: "preview-riya", type: "symptom", title: "Headache after lunch", date: "2026-09-16", visibility: "private", selectedProfileIds: [], details: { symptom: "Headache", severity: "Mild", note: "Rest and water helped." }, attachments: [], audit: [{ action: "created", at: "2026-09-16T14:30:00.000Z", actor: "Local preview" }] },
    { id: "preview-medication", profileId: "preview-riya", type: "medication", title: "Medication reviewed", date: "2026-09-24", visibility: "selected", selectedProfileIds: ["preview-maya"], details: { medication: "Vitamin D", dosage: "1000 IU", frequency: "Daily" }, attachments: [], audit: [{ action: "created", at: "2026-09-24T10:00:00.000Z", actor: "Local preview" }] },
    { id: "preview-measurement", profileId: "preview-riya", type: "measurement", title: "Height recorded", date: "2026-09-29", visibility: "family", selectedProfileIds: [], details: { measurement: "Height", value: "168", unit: "cm" }, attachments: [], audit: [{ action: "created", at: "2026-09-29T10:00:00.000Z", actor: "Local preview" }] },
  ];

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  function initials(name) {
    return name.trim().split(/\s+/).slice(0, 2).map(part => part[0]?.toUpperCase() || "").join("");
  }

  function memberProfiles() {
    return state.profiles.map((profile, index) => ({
      id: profile.id,
      name: profile.name,
      relation: profile.relationship[0].toUpperCase() + profile.relationship.slice(1),
      initials: initials(profile.name),
      color: profile.color || palette[index % palette.length].color,
      wash: profile.wash || palette[index % palette.length].wash,
      birthDate: profile.birthDate,
    }));
  }

  async function request(path, options = {}) {
    const { apiRequest } = await import("./js/api.js");
    const normalizedPath = path.replace(/^\/api\/v1(?=\/|$)/, "").replace(/^\/api(?=\/|$)/, "");
    const body = typeof options.body === "string" ? JSON.parse(options.body) : options.body;
    return apiRequest(normalizedPath, { ...options, body });
  }

  async function refreshEmergencyCardCache(profileId) {
    const { apiGet } = await import("./js/api.js");
    await apiGet(`/profiles/${encodeURIComponent(profileId)}/emergency-card?profile_id=${encodeURIComponent(profileId)}`);
  }

  function fromApiProfile(profile) {
    const preferences = profile.preferences || {};
    return {
      id: profile.id,
      userId: profile.user_id,
      name: profile.display_name,
      relationship: profile.relationship || preferences.relationship || (profile.user_id ? "you" : "child"),
      birthDate: profile.date_of_birth || "",
      sex: profile.sex || "",
      timezone: profile.timezone || "UTC",
      bloodGroup: profile.blood_group || "",
      allergies: profile.allergies || [],
      conditions: profile.conditions || [],
      color: profile.avatar_color || palette[0].color,
      wash: profile.avatar_wash || palette[0].wash,
      onboardingStep: profile.onboarding_step || 0,
      onboardingComplete: Boolean(profile.onboarding_complete),
    };
  }

  function onboardingValues(profile) {
    return {
      name: profile.name,
      relationship: profile.relationship,
      birthDate: profile.birthDate,
      bloodGroup: profile.bloodGroup,
      allergies: [...profile.allergies],
      conditions: [...profile.conditions],
    };
  }

  async function bootstrap(preview) {
    state.preview = preview;
    if (preview) {
      state.profiles = mockProfiles.map((profile, index) => ({
        ...profile,
        userId: index === 1 ? null : `preview-user-${index + 1}`,
        color: palette[index % palette.length].color,
        wash: palette[index % palette.length].wash,
        onboardingStep: 0,
      }));
      state.recordTypes = [
        { code: "condition", display_name: "Condition", icon: "heart", fields: [{ name: "name", label: "Condition", type: "text", required: true }, { name: "notes", label: "Notes", type: "textarea" }] },
        { code: "medication", display_name: "Medication", icon: "pill", fields: [{ name: "name", label: "Medication name", type: "text", required: true }, { name: "dosage", label: "Dose", type: "text" }] },
        { code: "allergy", display_name: "Allergy", icon: "alert-triangle", fields: [{ name: "name", label: "Allergen", type: "text", required: true }, { name: "reaction", label: "Reaction", type: "text" }] },
        { code: "vaccination", display_name: "Vaccination", icon: "shield", fields: [{ name: "name", label: "Vaccine", type: "text", required: true }] },
        { code: "lab", display_name: "Laboratory", icon: "activity", fields: [{ name: "name", label: "Test", type: "text", required: true }, { name: "value", label: "Result", type: "text" }] },
        { code: "vital", display_name: "Vital", icon: "activity", fields: [{ name: "name", label: "Measurement", type: "text", required: true }, { name: "value", label: "Value", type: "text", required: true }] },
        { code: "surgery", display_name: "Surgery", icon: "activity", fields: [{ name: "name", label: "Procedure", type: "text", required: true }] },
        { code: "visit_note", display_name: "Visit note", icon: "clipboard", fields: [{ name: "summary", label: "Visit summary", type: "textarea", required: true }] },
        { code: "blood_group", display_name: "Blood group", icon: "heart", fields: [{ name: "group", label: "Blood group", type: "text", required: true }] },
        { code: "systolic_blood_pressure", display_name: "Systolic blood pressure", icon: "activity", fields: [{ name: "value", label: "Systolic value", type: "number", required: true, unit: "mmHg" }] },
        { code: "body_temperature", display_name: "Body temperature", icon: "activity", fields: [{ name: "value", label: "Temperature", type: "number", required: true, unit: "°C" }] },
        { code: "blood_glucose", display_name: "Blood glucose", icon: "activity", fields: [{ name: "value", label: "Blood glucose", type: "number", required: true, unit: "mg/dL" }] },
      ];
      state.records = mockRecords;
      state.families = [{ id: "preview-family", name: "Patel family" }];
      state.activeFamilyId = "preview-family";
      state.familyData = {
        familyId: "preview-family",
        family: { name: "Patel family" },
        currentEmail: "riya@example.test",
        currentProfileId: "preview-riya",
        currentRole: "owner",
        members: mockProfiles.map((profile, index) => ({
          profileId: profile.id,
          userId: index === 1 ? null : `preview-user-${index + 1}`,
          email: index === 1 ? null : `${profile.name.split(" ")[0].toLowerCase()}@example.test`,
          displayName: profile.name,
          role: index === 0 ? "owner" : index === 1 ? "dependent" : "adult",
          isSelf: index === 0,
          profiles: [profile],
        })),
        consents: [],
        invites: [],
        updates: mockRecords.slice(1, 4),
        upcomingAppointments: [{ id: "preview-next-visit", title: "Next clinic visit", date: "2026-10-08", profileName: "Maya Patel", ownerName: "Riya Patel", type: "appointment" }],
        goals: [{ id: "preview-goal", title: "Bring questions to the next visit", targetDate: "2026-10-08", completed: false }],
        goalSummaries: [{ profileId: "preview-riya", active: 1, completed: 0, other: 0 }],
      };
    } else {
      const [result, typeSchemas, families] = await Promise.all([
        request("/profiles"),
        request("/meta/record-types"),
        request("/families"),
      ]);
      state.profiles = result.map(fromApiProfile);
      state.recordTypes = typeSchemas;
      state.families = families;
      state.activeFamilyId = state.families.some(family => family.id === state.activeFamilyId)
        ? state.activeFamilyId
        : state.families[0]?.id || "";
      state.records = [];
    }
    const incompleteProfile = state.profiles.find(profile => !profile.onboardingComplete);
    if (incompleteProfile) {
      state.onboardingProfileId = incompleteProfile.id;
      state.onboarding = onboardingValues(incompleteProfile);
      state.onboardingStep = Math.min(incompleteProfile.onboardingStep || 0, 2);
    }
    state.activeProfileId ||= incompleteProfile?.id || state.profiles[0]?.id || "";
    return state.profiles;
  }

  function setProfiles(profiles) {
    state.profiles = profiles;
    if (!state.profiles.some(profile => profile.id === state.activeProfileId)) state.activeProfileId = state.profiles[0]?.id || "";
    return memberProfiles();
  }

  function setActiveProfile(profileId) {
    if (state.profiles.some(profile => profile.id === profileId) && state.activeProfileId !== profileId) {
      state.activeProfileId = profileId;
      state.records = [];
      state.timelineCursor = null;
      state.timelineHasMore = false;
    }
  }

  function activeProfile() {
    return state.profiles.find(profile => profile.id === state.activeProfileId) || state.profiles[0] || null;
  }

  function pageHeader(kicker, title, copy = "") {
    return `<header class="page-header"><p class="page-kicker">${kicker}</p><h1 class="page-title">${title}</h1>${copy ? `<p class="page-lede">${copy}</p>` : ""}</header>`;
  }

  function progressRing(step) {
    const complete = (step + 1) / 3;
    const offset = 125.66 * (1 - complete);
    return `<svg class="onboarding-ring" viewBox="0 0 52 52" role="img" aria-label="Step ${step + 1} of 3"><circle class="ring-track" cx="26" cy="26" r="20"/><circle class="ring-value" cx="26" cy="26" r="20" style="stroke-dasharray:125.66;stroke-dashoffset:${offset}"/><text x="26" y="27" text-anchor="middle">${step + 1}/3</text></svg>`;
  }

  function onboardingStepMarkup() {
    if (state.onboardingStep === 0) {
      return `<div class="health-field"><label for="profile-name">Profile name</label><input class="health-input" id="profile-name" name="name" autocomplete="name" maxlength="100" required value="${escapeHtml(state.onboarding.name)}" aria-describedby="onboarding-error"></div>
        <div class="health-field"><label for="profile-relationship">Relationship</label><select class="health-input" id="profile-relationship" name="relationship" required><option value="you" ${state.onboarding.relationship === "you" ? "selected" : ""}>You</option><option value="child" ${state.onboarding.relationship === "child" ? "selected" : ""}>Child</option><option value="partner" ${state.onboarding.relationship === "partner" ? "selected" : ""}>Partner</option><option value="parent" ${state.onboarding.relationship === "parent" ? "selected" : ""}>Parent</option><option value="family member" ${state.onboarding.relationship === "family member" ? "selected" : ""}>Family member</option></select></div>`;
    }
    if (state.onboardingStep === 1) {
      return `<div class="health-field"><label for="profile-birth-date">Date of birth <span class="optional-label">Optional</span></label><input class="health-input" id="profile-birth-date" name="birthDate" type="date" max="${today}" value="${escapeHtml(state.onboarding.birthDate)}"><p class="field-hint">Used to anchor a lifetime timeline. Leave blank if you prefer.</p></div>
        <div class="health-field"><label for="profile-blood-group">Blood group <span class="optional-label">Optional</span></label><select class="health-input" id="profile-blood-group" name="bloodGroup"><option value="">Not recorded</option>${["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map(group => `<option value="${group}" ${state.onboarding.bloodGroup === group ? "selected" : ""}>${group}</option>`).join("")}</select><p class="field-hint">Only add information you have confirmed.</p></div>`;
    }
    return `<div class="health-field"><label for="profile-allergies">Allergies <span class="optional-label">Optional / skippable</span></label><textarea class="health-input" id="profile-allergies" name="allergies" rows="3" placeholder="Separate items with commas">${escapeHtml(state.onboarding.allergies.join(", "))}</textarea><p class="field-hint">Keep this list factual. Leave it blank if unknown.</p></div>
      <div class="health-field"><label for="profile-conditions">Conditions <span class="optional-label">Optional / skippable</span></label><textarea class="health-input" id="profile-conditions" name="conditions" rows="3" placeholder="Separate items with commas">${escapeHtml(state.onboarding.conditions.join(", "))}</textarea></div>`;
  }

  function renderOnboarding() {
    const resume = state.profiles.find(profile =>
      profile.id === state.onboardingProfileId && !profile.onboardingComplete);
    if (state.onboardingProfileId && !resume) {
      state.onboardingProfileId = "";
      state.onboarding = emptyDraft();
      state.onboardingStep = 0;
    }
    const stepNames = ["Profile", "Birth details", "Health details"];
    return `<div class="onboarding-page">${pageHeader("A first page in the journal", state.profiles.length ? "Add a family profile." : "Begin with one profile.", "A few details make the timeline more useful. Health details are optional, and every step can be skipped where noted.")}
      <div class="onboarding-progress"><div>${progressRing(state.onboardingStep)}</div><div><p class="page-kicker">Step ${state.onboardingStep + 1} / 3</p><strong>${stepNames[state.onboardingStep]}</strong><ol class="progress-steps">${stepNames.map((name, index) => `<li class="${index <= state.onboardingStep ? "complete" : ""}"><span>${index + 1}</span>${name}</li>`).join("")}</ol></div></div>
      <form class="health-form onboarding-form" data-onboarding-form novalidate><div class="onboarding-fields">${onboardingStepMarkup()}</div><p class="health-error" id="onboarding-error" role="alert" aria-live="polite"></p><div class="form-actions"><button class="secondary-action" type="button" data-onboarding-back ${state.onboardingStep === 0 ? "disabled" : ""}>Back</button><span class="form-actions-end">${state.onboardingStep > 0 ? '<button class="text-action" type="button" data-onboarding-skip>Skip this step</button>' : ""}<button class="primary-action" type="submit">${state.onboardingStep < 2 ? 'Continue <svg class="icon" aria-hidden="true"><use href="icons.svg#arrow-right"></use></svg>' : 'Save profile <svg class="icon" aria-hidden="true"><use href="icons.svg#check"></use></svg>'}</button></span></div></form>
      <p class="privacy-note"><svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg><span>Saved to this local account. You can edit these details later.</span></p></div>`;
  }

  function collectOnboarding(form) {
    const values = new FormData(form);
    if (state.onboardingStep === 0) {
      state.onboarding.name = String(values.get("name") || "").trim();
      state.onboarding.relationship = String(values.get("relationship") || "");
    }
    if (state.onboardingStep === 1) {
      state.onboarding.birthDate = String(values.get("birthDate") || "");
      state.onboarding.bloodGroup = String(values.get("bloodGroup") || "");
    }
    if (state.onboardingStep === 2) {
      state.onboarding.allergies = String(values.get("allergies") || "").split(/[\n,]/).map(value => value.trim()).filter(Boolean);
      state.onboarding.conditions = String(values.get("conditions") || "").split(/[\n,]/).map(value => value.trim()).filter(Boolean);
    }
  }

  async function saveOnboarding(nextStep, complete = false) {
    const paletteEntry = palette[state.profiles.length % palette.length];
    if (state.preview) {
      const current = state.profiles.find(profile => profile.id === state.onboardingProfileId);
      const profile = {
        ...(current || {}),
        id: current?.id || `preview-${crypto.randomUUID()}`,
        name: state.onboarding.name,
        relationship: state.onboarding.relationship,
        birthDate: state.onboarding.birthDate,
        bloodGroup: state.onboarding.bloodGroup,
        allergies: [...state.onboarding.allergies],
        conditions: [...state.onboarding.conditions],
        color: current?.color || paletteEntry.color,
        wash: current?.wash || paletteEntry.wash,
        onboardingStep: nextStep,
        onboardingComplete: complete,
      };
      if (current) state.profiles[state.profiles.indexOf(current)] = profile;
      else state.profiles.push(profile);
      state.onboardingProfileId = profile.id;
      state.activeProfileId = profile.id;
      window.dispatchEvent(new CustomEvent("healthprofileschange", { detail: memberProfiles() }));
      if (complete) {
        state.onboarding = emptyDraft();
        state.onboardingProfileId = "";
        state.onboardingStep = 0;
        window.location.hash = "#/home";
      } else {
        state.onboardingStep = nextStep;
      }
      return;
    }
    const payload = {
      display_name: state.onboarding.name,
      relationship: state.onboarding.relationship,
      date_of_birth: state.onboarding.birthDate || null,
      blood_group: state.onboarding.bloodGroup || null,
      allergies: state.onboarding.allergies,
      conditions: state.onboarding.conditions,
      avatar_color: state.profiles.find(profile => profile.id === state.onboardingProfileId)?.color || paletteEntry.color,
      avatar_wash: state.profiles.find(profile => profile.id === state.onboardingProfileId)?.wash || paletteEntry.wash,
      onboarding_step: nextStep,
      onboarding_complete: complete,
    };
    let saved;
    if (state.onboardingProfileId) {
      saved = await request(`/profiles/${encodeURIComponent(state.onboardingProfileId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else if (state.onboarding.relationship === "you") {
      const ownProfile = state.profiles.find(profile => profile.userId);
      if (!ownProfile) throw new Error("Your account profile could not be found.");
      state.onboardingProfileId = ownProfile.id;
      saved = await request(`/profiles/${encodeURIComponent(ownProfile.id)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else {
      let families = await request("/families");
      if (!families.length) {
        const family = await request("/families", {
          method: "POST",
          body: JSON.stringify({ name: `${state.onboarding.name}'s family` }),
        });
        families = [family];
      }
      saved = await request("/profiles", {
        method: "POST",
        body: JSON.stringify({ ...payload, family_id: families[0].id }),
      });
      state.onboardingProfileId = saved.id;
    }
    const profile = fromApiProfile(saved);
    try {
      await refreshEmergencyCardCache(profile.id);
    } catch (error) {
      announce(`Profile saved, but the offline emergency card could not be refreshed: ${error.message}`, "error");
    }
    const existingIndex = state.profiles.findIndex(item => item.id === profile.id);
    if (existingIndex >= 0) state.profiles[existingIndex] = profile;
    else state.profiles.push(profile);
    state.activeProfileId = profile.id;
    window.dispatchEvent(new CustomEvent("healthprofileschange", { detail: memberProfiles() }));
    if (complete) {
      state.onboarding = emptyDraft();
      state.onboardingProfileId = "";
      state.onboardingStep = 0;
      window.location.hash = "#/home";
    } else {
      state.onboardingStep = nextStep;
    }
  }

  function profileCompleteness(profile) {
    const fields = [profile.name, profile.birthDate, profile.bloodGroup, profile.allergies?.length, profile.conditions?.length];
    return Math.round(fields.filter(Boolean).length / fields.length * 100);
  }

  function familyInviteCodeFromHash() {
    const query = window.location.hash.split("?").slice(1).join("?");
    return new URLSearchParams(query).get("invite") || "";
  }

  function familyDashboardFromApi(data) {
    const members = data.members.map(member => ({
      profileId: member.profile_id,
      userId: member.user_id,
      displayName: member.display_name,
      email: member.email,
      role: member.role,
      isSelf: member.profile_id === data.current_profile_id,
    }));
    const nameForProfile = profileId => members.find(member => member.profileId === profileId)?.displayName || "Family member";
    return {
      familyId: data.family_id,
      familyName: data.family_name,
      currentProfileId: data.current_profile_id,
      currentRole: data.current_role,
      members,
      consents: data.consents.map(consent => ({
        id: consent.id,
        profileId: consent.profile_id,
        recipientProfileId: consent.recipient_profile_id,
        resources: consent.resources,
        expiresAt: consent.expires_at,
        revokedAt: consent.revoked_at,
        recordId: consent.record_id,
      })),
      updates: data.recent_updates.map(update => ({
        id: update.id,
        profileId: update.profile_id,
        profileName: nameForProfile(update.profile_id),
        type: update.type,
        title: typeInfo(update.type).display_name,
        date: update.recorded_at.slice(0, 10),
        visibility: update.visibility,
      })),
      upcomingAppointments: data.upcoming_appointments.map(appointment => ({
        id: appointment.id,
        profileId: appointment.profile_id,
        profileName: nameForProfile(appointment.profile_id),
        title: appointment.title,
        startsAt: appointment.starts_at,
      })),
      goalSummaries: data.goal_summary,
    };
  }

  function progressRingMarkup(value, label) {
    const circumference = 100.53;
    const offset = circumference * (1 - value / 100);
    return `<svg class="member-health-ring" viewBox="0 0 42 42" role="img" aria-label="${escapeHtml(label)}: ${value}%"><circle class="ring-track" cx="21" cy="21" r="16"/><circle class="ring-value" cx="21" cy="21" r="16" style="stroke-dasharray:${circumference};stroke-dashoffset:${offset}"/><text x="21" y="22">${value}</text></svg>`;
  }

  function renderFamilyDashboard() {
    const family = state.familyData;
    if (state.families.length === 0) {
      const inviteCode = familyInviteCodeFromHash();
      return `<section class="family-section"><article class="family-panel"><div class="family-section-heading"><div><p class="page-kicker">Shared care</p><h2>${inviteCode ? "Join a care circle" : "Start your family circle"}</h2><p>Create a family or accept a one-time invitation code from someone you trust.</p></div></div>
        ${inviteCode ? `<form class="invite-form" data-family-decision><input type="hidden" name="inviteCode" value="${escapeHtml(inviteCode)}"><button class="primary-action" type="submit" data-decision="accept">Accept invitation</button><button class="secondary-action" type="submit" data-decision="decline">Decline</button><p class="health-error" role="alert"></p></form>` : ""}
        <form class="invite-form" data-family-create><div class="health-field"><label for="new-family-name">Family name</label><input class="health-input" id="new-family-name" name="name" maxlength="160" required placeholder="For example, the Patel family"></div><button class="primary-action" type="submit">Create family</button><p class="health-error" role="alert"></p></form>
        <form class="invite-form" data-family-decision><div class="health-field"><label for="family-invite-code">Invitation code</label><input class="health-input" id="family-invite-code" name="inviteCode" minlength="20" maxlength="128" required value="${escapeHtml(inviteCode)}" placeholder="Paste the one-time code"></div><button class="primary-action" type="submit" data-decision="accept">Join family</button><button class="secondary-action" type="submit" data-decision="decline">Decline</button><p class="health-error" role="alert"></p></form></article></section>`;
    }
    if (!family) return '<div class="family-loading" role="status"><span class="health-spinner" aria-hidden="true"></span>Loading family dashboard...</div>';
    const members = family.members || [];
    const ownerCanManage = ["owner", "guardian"].includes(family.currentRole);
    const memberKey = member => member.profileId || member.profile_id;
    const memberName = member => member.displayName || member.display_name;
    const memberUserId = member => member.userId || member.user_id;
    const memberRole = member => member.role || "viewer";
    const currentProfileId = family.currentProfileId || family.current_profile_id || "";
    const familyId = family.familyId || family.family_id || state.activeFamilyId;
    const consents = family.consents || [];
    const now = Date.now();
    const hasActiveConsent = consent => !(consent.revokedAt || consent.revoked_at)
      && (!(consent.expiresAt || consent.expires_at)
        || new Date(consent.expiresAt || consent.expires_at).getTime() > now)
      && (consent.recordId || consent.record_id) == null;
    const avatars = members.slice(0, 4).map((member, index) => `<span class="family-avatar" style="--member-color:${palette[index % palette.length].color};--member-wash:${palette[index % palette.length].wash}" aria-label="${escapeHtml(memberName(member))}">${escapeHtml(initials(memberName(member)))}</span>`).join("");
    const memberCards = members.map((member, index) => {
      const profileId = memberKey(member);
      const isSelf = profileId === currentProfileId || member.isSelf;
      const role = memberRole(member);
      const isManagedDependent = ownerCanManage && role === "dependent" && !memberUserId(member);
      const hasSharedData = isSelf
        || isManagedDependent
        || consents.some(consent =>
          (consent.profileId || consent.profile_id) === profileId
          && (consent.recipientProfileId || consent.recipient_profile_id) === currentProfileId
          && hasActiveConsent(consent)
          && (consent.resources || []).length > 0)
        || [...(family.updates || []), ...(family.upcomingAppointments || [])].some(item =>
          (item.profileId || item.profile_id) === profileId)
        || (family.goalSummaries || family.goal_summary || []).some(goal =>
          (goal.profileId || goal.profile_id) === profileId);
      const roleControl = ownerCanManage && !isSelf
        ? `<select class="role-select" aria-label="Role for ${escapeHtml(memberName(member))}" data-member-role="${escapeHtml(profileId)}"><option value="adult" ${role === "adult" ? "selected" : ""}>Adult</option><option value="guardian" ${role === "guardian" ? "selected" : ""}>Guardian</option><option value="dependent" ${role === "dependent" ? "selected" : ""}>Dependent</option><option value="viewer" ${role === "viewer" ? "selected" : ""}>Viewer</option></select>`
        : `<span class="status-pill ${role === "guardian" ? "info" : ""}">${escapeHtml(role.replaceAll("_", " "))}</span>`;
      const controls = ownerCanManage && (isSelf || role !== "owner")
        ? `<button class="text-action" type="button" data-remove-family-member="${escapeHtml(profileId)}">${isSelf ? "Leave family" : "Remove member"}</button>`
        : "";
      const privacyState = isSelf
        ? '<span class="status-pill neutral">Your profile</span>'
        : isManagedDependent
          ? '<span class="status-pill neutral">Managed profile</span>'
          : hasSharedData
            ? '<span class="status-pill info">Consented sharing</span>'
            : '<span class="ring-hidden"><svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg>Health details private</span>';
      const privacyCaption = isSelf
        ? "You control what you share with other family members."
        : isManagedDependent
          ? "You manage this dependent profile."
          : hasSharedData
            ? "Only the data covered by active consent is visible."
            : '<svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg>Health details have not been shared with you.';
      return `<article class="family-member-card"><div class="family-member-top"><span class="family-member-avatar" style="--member-color:${palette[index % palette.length].color};--member-wash:${palette[index % palette.length].wash}" aria-hidden="true">${escapeHtml(initials(memberName(member)))}</span><div class="family-member-identity"><strong>${escapeHtml(memberName(member))}</strong><small>${escapeHtml(member.email || (member.user_id ? "Family account" : "Managed profile"))}</small></div>${privacyState}</div><div class="family-member-meta">${roleControl}${isSelf ? '<span class="status-pill neutral">You</span>' : ""}${controls}</div><p class="ring-caption">${privacyCaption}</p></article>`;
    }).join("");
    const activeConsent = (sourceId, recipientId) => consents.find(consent =>
      (consent.profileId || consent.profile_id) === sourceId
      && (consent.recipientProfileId || consent.recipient_profile_id) === recipientId
      && hasActiveConsent(consent));
    const actor = members.find(member => memberKey(member) === currentProfileId);
    const actorUserId = memberUserId(actor || {});
    const shareableProfiles = state.profiles.filter(profile =>
      profile.userId === actorUserId
      || (ownerCanManage && !profile.userId && ["child", "parent"].includes(profile.relationship)));
    const consentResources = [
      ["health_records", "Health records"],
      ["appointments", "Appointments"],
      ["goals", "Goals"],
    ];
    const sharingRows = shareableProfiles.flatMap(profile => members
      .filter(member => memberUserId(member) && memberKey(member) !== profile.id)
      .map(member => {
        const grant = activeConsent(profile.id, memberKey(member));
        const toggles = consentResources.map(([resource, label]) => {
          const granted = (grant?.resources || []).includes(resource);
          return `<label class="consent-toggle"><input type="checkbox" data-consent-toggle data-source-profile="${escapeHtml(profile.id)}" data-recipient-profile="${escapeHtml(memberKey(member))}" data-consent-resource="${resource}" ${granted ? "checked" : ""}><span class="consent-switch" aria-hidden="true"></span><span>${label}</span></label>`;
        }).join("");
        return `<article class="family-member-card consent-share-card"><strong>${escapeHtml(profile.name)}</strong><small>Share with ${escapeHtml(memberName(member))}</small><div class="consent-list">${toggles}</div></article>`;
      })).join("");
    const updates = (family.updates || []).map(record => {
      const profileMember = members.find(member => memberKey(member) === (record.profileId || record.profile_id));
      const date = record.date || record.recordedAt?.slice(0, 10) || record.recorded_at?.slice(0, 10);
      const title = record.title || typeInfo(record.type).display_name;
      const visibilityLabel = record.visibility === "private" ? "Private" : record.visibility === "selected" ? "Selected" : "Family";
      return `<li class="family-feed-row"><span class="feed-type-dot type-${escapeHtml(record.type)}"></span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(record.profileName || memberName(profileMember || {}) || "Family")} / ${date ? new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : ""}</small></div><span class="status-pill visibility-${escapeHtml(record.visibility)}">${visibilityLabel}</span></li>`;
    }).join("");
    const upcoming = (family.upcomingAppointments || []).map(record => {
      const profileMember = members.find(member => memberKey(member) === (record.profileId || record.profile_id));
      const date = record.date || record.startsAt?.slice(0, 10) || record.starts_at?.slice(0, 10);
      return `<li class="family-feed-row"><span class="appointment-date">${date ? new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}</span><div><strong>${escapeHtml(record.title)}</strong><small>${escapeHtml(record.profileName || memberName(profileMember || {}) || "Family")}</small></div></li>`;
    }).join("");
    const goalSummaries = family.goalSummaries || family.goal_summary || [];
    const activeGoals = goalSummaries.reduce((total, goal) => total + goal.active, 0);
    const invitation = state.lastInvite ? `<div class="invite-result" role="status"><strong>One-time invitation link ready</strong><p>Share this link with the person you invited. It expires ${new Date(state.lastInvite.expiresAt).toLocaleString()} and works once.</p><div class="invite-copy-row"><input class="health-input" readonly value="${escapeHtml(state.lastInvite.inviteUrl)}" aria-label="One-time family invitation link"><button type="button" class="secondary-action" data-copy-invite>Copy link</button></div></div>` : "";
    const familySelector = state.families.length > 1
      ? `<label class="filter-field"><span>Family</span><select data-family-select>${state.families.map(item => `<option value="${escapeHtml(item.id)}" ${item.id === familyId ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>`
      : "";
    const inviteCode = familyInviteCodeFromHash();
    const joinPrompt = inviteCode ? `<form class="invite-form" data-family-decision><input type="hidden" name="inviteCode" value="${escapeHtml(inviteCode)}"><button class="primary-action" type="submit" data-decision="accept">Accept invitation</button><button class="secondary-action" type="submit" data-decision="decline">Decline</button><p class="health-error" role="alert"></p></form>` : "";
    const inviteForm = ownerCanManage ? `<section class="family-section invite-section"><div class="family-section-heading"><div><p class="page-kicker">Bring someone in</p><h2>Invite to your circle</h2><p>Create a one-time link to share. No email is sent.</p></div></div><form class="invite-form" data-family-invite><div class="health-field"><label for="invite-role">Starting role</label><select class="health-input" id="invite-role" name="role"><option value="adult">Adult</option><option value="guardian">Guardian</option><option value="dependent">Dependent</option><option value="viewer">Viewer</option></select></div><div class="health-field"><label for="invite-expiry">Expires in hours</label><input class="health-input" id="invite-expiry" name="expiresInHours" type="number" min="1" max="720" value="72" required></div><button class="primary-action" type="submit">Create invitation link</button><p class="health-error" id="invite-error" role="alert"></p></form>${invitation}</section>` : "";
    return `${joinPrompt}<section class="family-dashboard-grid" aria-label="Family overview"><article class="family-panel family-overview-panel"><div class="family-panel-heading"><div><p class="page-kicker">${escapeHtml(family.family?.name || family.familyName || family.family_name || "Family circle")}</p><h2>Your care circle</h2></div><span class="family-avatar-cluster" aria-label="${members.length} family members">${avatars}</span></div><p>${members.length} ${members.length === 1 ? "member" : "members"} / roles and consent stay visible here.</p>${familySelector}<a class="secondary-action" href="#/timeline">Open shared timeline</a></article>
      <article class="family-panel"><div class="family-panel-heading"><h2>What's new</h2><a href="#/timeline">Timeline</a></div><ul class="family-feed-list">${updates || '<li class="family-empty-row"><svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg>Nothing has been shared with you yet.</li>'}</ul></article>
      <article class="family-panel"><div class="family-panel-heading"><h2>Upcoming appointments</h2><span class="status-pill info">${(family.upcomingAppointments || []).length}</span></div><ul class="family-feed-list">${upcoming || '<li class="family-empty-row"><svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg>No appointments have been shared.</li>'}</ul></article>
      <article class="family-panel"><div class="family-panel-heading"><h2>Shared goals</h2><span class="status-pill neutral">${activeGoals} active</span></div><ul class="family-feed-list">${goalSummaries.map(goal => {
        const member = members.find(item => memberKey(item) === (goal.profileId || goal.profile_id));
        return `<li class="family-feed-row"><div><strong>${escapeHtml(memberName(member || {}) || "Family member")}</strong><small>${goal.active} active / ${goal.completed} completed</small></div></li>`;
      }).join("") || '<li class="family-empty-row"><svg class="icon" aria-hidden="true"><use href="icons.svg#lock"></use></svg>No goals have been shared.</li>'}</ul></article></section>
      <section class="family-section"><div class="family-section-heading"><div><p class="page-kicker">Shared care / Permission centre</p><h2>Family members</h2><p>Health details are kept private unless their owner grants access.</p></div></div><div class="family-member-grid">${memberCards || '<p class="family-empty-row">Invite someone to build your family circle.</p>'}</div></section>
      <section class="family-section"><div class="family-section-heading"><div><p class="page-kicker">Consent centre</p><h2>What you share</h2><p>Choose which parts of your profile each family account can read. Changes take effect immediately.</p></div></div><div class="family-member-grid">${sharingRows || '<p class="family-empty-row">No other family accounts are available to share with yet.</p>'}</div></section>${inviteForm}`;
  }

  function familyPage() {
    const people = state.profiles.map((profile, index) => {
      const colors = palette[index % palette.length];
      const years = profile.birthDate ? `${currentYear - Number(profile.birthDate.slice(0, 4))} years` : "Birth date not recorded";
      return `<button class="health-person-row" type="button" data-health-select-profile="${escapeHtml(profile.id)}"><span class="person-avatar" style="--member-color:${colors.color};--member-wash:${colors.wash}" aria-hidden="true">${escapeHtml(initials(profile.name))}</span><span class="person-meta"><strong>${escapeHtml(profile.name)}</strong><small>${escapeHtml(profile.relationship)} / ${escapeHtml(years)}</small></span><span class="status-pill">${profile.onboardingComplete ? "Profile ready" : "Setup needed"}</span><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-right"></use></svg></button>`;
    }).join("");
    return `${pageHeader("People / Shared care", "Family home", "A clear picture of who's here, what changed, and what your circle has agreed to share.")}
      <div id="family-dashboard" data-family-ready="false">${renderFamilyDashboard()}</div>
      <section class="health-family-list family-profiles-section"><div class="family-section-heading"><div><p class="page-kicker">Health profiles</p><h2>People in your care</h2></div><a class="secondary-action" href="#/onboarding"><svg class="icon" aria-hidden="true"><use href="icons.svg#user-plus"></use></svg>Add profile</a></div><div class="surface health-people">${people || '<p class="empty-copy">No profiles yet. Add one to begin a lifetime timeline.</p>'}</div></section>`;
  }

  function homePage() {
    const profile = activeProfile();
    if (!profile) return `${pageHeader("A first page in the journal", "Begin with one profile.", "Add a person to create a private lifetime health timeline.")}<a class="primary-action" href="#/onboarding">Add a profile</a>`;
    const fallbackColor = palette[state.profiles.indexOf(profile) % palette.length];
    const colors = { color: profile.color || fallbackColor.color, wash: profile.wash || fallbackColor.wash };
    const familyMembers = state.familyData?.members || [];
    const avatarCluster = familyMembers.slice(0, 4).map((member, index) => `<span class="family-avatar" style="--member-color:${palette[index % palette.length].color};--member-wash:${palette[index % palette.length].wash}" aria-hidden="true">${escapeHtml(initials(member.displayName))}</span>`).join("");
    const list = state.profiles.map((item, index) => {
      const colorsForProfile = item.color && item.wash ? item : palette[index % palette.length];
      return `<button class="health-person-row" type="button" data-health-select-profile="${escapeHtml(item.id)}"><span class="person-avatar" style="--member-color:${colorsForProfile.color};--member-wash:${colorsForProfile.wash}" aria-hidden="true">${escapeHtml(initials(item.name))}</span><span class="person-meta"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.relationship)} / Open lifetime timeline</small></span><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-right"></use></svg></button>`;
    }).join("");
    const healthFacts = [profile.birthDate && `Born ${new Date(`${profile.birthDate}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" })}`, profile.bloodGroup && `Blood group ${profile.bloodGroup}`, profile.allergies?.length && `${profile.allergies.length} recorded ${profile.allergies.length === 1 ? "allergy" : "allergies"}`, profile.conditions?.length && `${profile.conditions.length} recorded ${profile.conditions.length === 1 ? "condition" : "conditions"}`].filter(Boolean);
    return `${pageHeader("Your family / Care journal", `Good to have you here, ${escapeHtml(profile.name.split(" ")[0])}.`, "Your family's health details, in their own time.")}
      <div class="welcome-strip"><div><strong>A timeline that begins at the beginning.</strong><p>Add an appointment, a health note, or a moment you want to remember.</p><div class="home-family-presence"><span class="family-avatar-cluster">${avatarCluster}</span><span>${familyMembers.length} ${familyMembers.length === 1 ? "person" : "people"} in your care circle</span></div></div><a class="primary-action" href="#/timeline"><svg class="icon" aria-hidden="true"><use href="icons.svg#clock"></use></svg>Open timeline</a></div>
      <div class="content-grid home-health-grid"><section class="content-column"><h2 class="section-label">Profiles <a href="#/family">Manage all</a></h2><div class="surface health-people">${list}</div></section><aside class="content-column"><h2 class="section-label">${escapeHtml(profile.name)} / Profile details</h2><div class="surface surface-pad profile-summary"><span class="person-avatar" style="--member-color:${colors.color};--member-wash:${colors.wash}" aria-hidden="true">${escapeHtml(initials(profile.name))}</span><p>${healthFacts.length ? healthFacts.map(escapeHtml).join("<br>") : "Birth and health details have not been added."}</p><a href="#/family">Manage profiles</a></div></aside></div>`;
  }

  function typeInfo(type) {
    return state.recordTypes.find(item => item.code === type) || {
      code: type,
      display_name: type,
      icon: "clipboard",
    };
  }

  function timelineToolbar(profile) {
    const years = [];
    const startYear = profile.birthDate ? Number(profile.birthDate.slice(0, 4)) : currentYear - 100;
    for (let year = currentYear; year >= startYear; year -= 1) years.push(year);
    return `<form class="timeline-toolbar" data-timeline-filter>
      <label class="search-field"><svg class="icon" aria-hidden="true"><use href="icons.svg#search"></use></svg><input type="search" name="q" aria-label="Search health records" placeholder="Search this timeline" value="${escapeHtml(state.query)}"></label>
      <label class="filter-field"><span>Record type</span><select name="type" aria-label="Filter by record type"><option value="">All records</option>${state.recordTypes.map(type => `<option value="${escapeHtml(type.code)}" ${state.type === type.code ? "selected" : ""}>${escapeHtml(type.display_name)}</option>`).join("")}</select></label>
      <div class="zoom-group" role="group" aria-label="Timeline zoom">${[["lifetime", "Lifetime"], ["decade", "Decade"], ["year", "Year"]].map(([value, label]) => `<button type="button" class="zoom-button" data-timeline-zoom="${value}" aria-pressed="${state.zoom === value}">${label}</button>`).join("")}</div>
      ${state.zoom !== "lifetime" ? `<label class="filter-field year-select"><span>${state.zoom === "decade" ? "Decade containing" : "Year"}</span><select name="year" aria-label="Choose ${state.zoom === "decade" ? "decade" : "year"}">${years.map(year => `<option value="${year}" ${state.year === year ? "selected" : ""}>${state.zoom === "decade" ? `${Math.floor(year / 10) * 10}s` : year}</option>`).filter((option, index, options) => state.zoom !== "decade" || index === options.findIndex(item => item === option)).join("")}</select></label>` : ""}
      <button class="primary-action add-record-button" type="button" data-new-record><svg class="icon" aria-hidden="true"><use href="icons.svg#plus"></use></svg>Add record</button>
    </form>`;
  }

  function timelineRange() {
    if (state.zoom === "year") return [state.year, state.year];
    if (state.zoom === "decade") return [Math.floor(state.year / 10) * 10, Math.floor(state.year / 10) * 10 + 9];
    return ["", ""];
  }

  function visibleRecords() {
    const profile = activeProfile();
    if (!profile) return [];
    const [from, to] = timelineRange();
    const inRange = record => (!from || Number(record.date.slice(0, 4)) >= from) && (!to || Number(record.date.slice(0, 4)) <= to);
    return state.records.filter(record => {
      const demoVisible = !state.preview || record.profileId === profile.id || record.visibility === "family" || (record.visibility === "selected" && record.selectedProfileIds.includes(profile.id));
      return demoVisible && inRange(record);
    });
  }

  function birthEvent(profile) {
    if (!profile.birthDate) return null;
    return { id: `birth-${profile.id}`, type: "birth", title: "Born", date: profile.birthDate, details: { note: `${escapeHtml(profile.name)}'s timeline begins.` }, visibility: "private", audit: [] };
  }

  function renderTimelineEvents() {
    const profile = activeProfile();
    if (!profile) return '<div class="timeline-empty"><h2>Add a profile to begin.</h2><a href="#/onboarding" class="secondary-action">Create a profile</a></div>';
    const records = visibleRecords().filter(record => !state.query || `${record.title} ${Object.values(record.details || {}).join(" ")}`.toLowerCase().includes(state.query.toLowerCase())).filter(record => !state.type || record.type === state.type);
    const [from, to] = timelineRange();
    const events = [...records];
    const birth = birthEvent(profile);
    if (birth && (!from || Number(birth.date.slice(0, 4)) >= from) && (!to || Number(birth.date.slice(0, 4)) <= to)) events.push(birth);
    events.sort((left, right) => left.date.localeCompare(right.date));
    const earliest = profile.birthDate ? Number(profile.birthDate.slice(0, 4)) : (events[0] ? Number(events[0].date.slice(0, 4)) : currentYear);
    const displayedYears = state.zoom === "year" ? `${state.year}` : state.zoom === "decade" ? `${Math.floor(state.year / 10) * 10}s` : `${earliest} to ${currentYear}`;
    const legend = state.recordTypes.map(type => `<span class="timeline-legend-item type-${escapeHtml(type.code)}"><i aria-hidden="true"></i>${escapeHtml(type.display_name)}</span>`).join("");
    let previousYear = "";
    const list = events.map(event => {
      const year = event.date.slice(0, 4);
      const yearMarker = year !== previousYear ? `<li class="timeline-year" aria-label="${year}"><span>${year}</span></li>` : "";
      previousYear = year;
      const info = typeInfo(event.type);
      const summary = [event.details?.summary, event.details?.name, event.details?.provider, event.details?.medication, event.details?.symptom, event.details?.substance, event.details?.condition, event.details?.measurement && `${event.details.measurement}: ${event.details.value || ""} ${event.details.unit || ""}`, event.details?.notes, event.details?.note].filter(Boolean)[0] || "Open this note for more detail.";
      const item = event.type === "birth"
        ? `<li class="vine-event type-birth"><span class="vine-node" aria-hidden="true"><svg class="icon"><use href="icons.svg#sparkles"></use></svg></span><div class="vine-copy"><time datetime="${event.date}">${new Date(`${event.date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</time><h3>Born</h3><p>${escapeHtml(profile.name)}'s timeline begins.</p></div></li>`
        : `<li class="vine-event type-${escapeHtml(event.type)}"><span class="vine-node" aria-hidden="true"><svg class="icon"><use href="icons.svg#${escapeHtml(info.icon)}"></use></svg></span><button class="vine-copy" type="button" data-open-record="${escapeHtml(event.id)}"><time datetime="${event.date}">${new Date(`${event.date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</time><span class="vine-event-heading"><strong>${escapeHtml(event.title)}</strong><span class="status-pill visibility-${escapeHtml(event.visibility)}">${escapeHtml(event.visibility === "selected" ? "Selected" : event.visibility[0].toUpperCase() + event.visibility.slice(1))}</span></span><p>${escapeHtml(summary)}</p>${event.attachments?.length ? `<small class="attachment-count">${event.attachments.length} attachment${event.attachments.length === 1 ? "" : "s"}</small>` : ""}</button></li>`;
      return yearMarker + item;
    }).join("");
    return `<div class="timeline-overview"><div><span class="status-pill info">${escapeHtml(displayedYears)}</span><span class="field-hint">${events.length} ${events.length === 1 ? "moment" : "moments"}</span></div><div class="timeline-legend">${legend}</div></div>
      ${events.length ? `<ol class="vine-timeline" aria-label="${escapeHtml(profile.name)}'s health timeline">${list}</ol>${state.timelineHasMore ? '<button class="secondary-action timeline-load-more" type="button" data-load-more-timeline>Load older records</button>' : ""}` : `<div class="timeline-empty"><span class="empty-mark"><svg class="icon" aria-hidden="true"><use href="icons.svg#book-open"></use></svg></span><h2>No moments in this view.</h2><p>Change the year or filters, or add the first record to this timeline.</p><button class="primary-action" type="button" data-new-record><svg class="icon" aria-hidden="true"><use href="icons.svg#plus"></use></svg>Add a record</button></div>`}`;
  }

  function renderTimeline() {
    const profile = activeProfile();
    if (!profile) return `${pageHeader("Health history", "Your timeline starts with a profile.", "Add a profile to create a lifetime timeline.")}<a class="primary-action" href="#/onboarding">Add a profile</a>`;
    const body = state.preview
      ? renderTimelineEvents()
      : `<div id="vine-results" class="vine-results"><div class="timeline-loading" role="status"><span class="health-spinner" aria-hidden="true"></span>Loading ${escapeHtml(profile.name)}'s timeline...</div></div>`;
    return `<div class="lifetime-page">${pageHeader(`Lifetime / ${escapeHtml(profile.name)}`, "A life, in its own time.", "Keep the moments that matter close. Start at birth, follow the years, and find a detail when you need it.")}
      ${timelineToolbar(profile)}${body}</div>`;
  }

  function timelineRecordFromApi(record) {
    const data = record.data || {};
    const { title, ...details } = data;
    return {
      id: record.id,
      profileId: record.profile_id,
      type: record.type,
      title: title || data.name || data.summary || typeInfo(record.type).display_name,
      date: record.recorded_at.slice(0, 10),
      visibility: record.visibility,
      selectedProfileIds: [],
      details,
      attachments: (record.attachments || []).map(attachment => ({
        id: attachment.id,
        name: attachment.original_filename,
        type: attachment.content_type,
        size: attachment.file_size_bytes,
        downloadUrl: `/api/v1/attachments/${encodeURIComponent(attachment.id)}/download`,
      })),
      audit: [],
    };
  }

  async function loadTimeline({ append = false } = {}) {
    const container = document.querySelector("#vine-results");
    if (!container || state.timelineLoading) return;
    if (state.preview) {
      container.innerHTML = renderTimelineEvents();
      return;
    }
    const profile = activeProfile();
    if (!profile) return;
    state.timelineLoading = true;
    if (append) {
      const loadMore = container.querySelector("[data-load-more-timeline]");
      if (loadMore) {
        loadMore.disabled = true;
        loadMore.textContent = "Loading older records...";
      }
    } else {
      state.timelineCursor = null;
      state.timelineHasMore = false;
      container.innerHTML = '<div class="timeline-loading" role="status"><span class="health-spinner" aria-hidden="true"></span>Loading timeline...</div>';
    }
    const [from, to] = timelineRange();
    const params = new URLSearchParams();
    if (state.query) params.set("search", state.query);
    if (state.type) params.set("type", state.type);
    if (from) params.set("start_year", String(from));
    if (to) params.set("end_year", String(to));
    if (append && state.timelineCursor) params.set("cursor", state.timelineCursor);
    try {
      const result = await request(`/profiles/${encodeURIComponent(profile.id)}/timeline?${params}`);
      const page = (result.items || []).map(timelineRecordFromApi);
      state.records = append ? [...state.records, ...page] : page;
      state.timelineCursor = result.next_cursor;
      state.timelineHasMore = Boolean(result.has_more && result.next_cursor);
      container.innerHTML = renderTimelineEvents();
    } catch (error) {
      container.innerHTML = `<div class="timeline-empty"><h2>Timeline could not load.</h2><p>${escapeHtml(error.message)}</p><button class="secondary-action" type="button" data-reload-timeline>Try again</button></div>`;
    } finally {
      state.timelineLoading = false;
    }
  }

  function detailFields(type) {
    const schema = typeInfo(type);
    return (schema.fields || []).map(field => {
      const id = `detail-${field.name}`;
      const required = Boolean(field.required);
      const label = `${escapeHtml(field.label)}${required
        ? '<span class="required-mark" aria-label="required"> *</span>'
        : '<span class="optional-label">Optional</span>'}`;
      const hint = field.unit ? `<p class="field-hint">Unit: ${escapeHtml(field.unit)}</p>` : "";
      if (field.type === "textarea") {
        return `<div class="health-field"><label for="${id}">${label}</label><textarea class="health-input" id="${id}" name="${escapeHtml(field.name)}" rows="3" maxlength="4000" ${required ? "required" : ""}></textarea>${hint}</div>`;
      }
      if (field.type === "select") {
        return `<div class="health-field"><label for="${id}">${label}</label><select class="health-input" id="${id}" name="${escapeHtml(field.name)}" ${required ? "required" : ""}><option value="">Choose...</option>${(field.options || []).map(option => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join("")}</select>${hint}</div>`;
      }
      return `<div class="health-field"><label for="${id}">${label}</label><input class="health-input" id="${id}" name="${escapeHtml(field.name)}" type="${field.type === "number" ? "number" : "text"}" maxlength="180" ${field.type === "number" ? 'step="any"' : ""} ${required ? "required" : ""} placeholder="${escapeHtml(field.placeholder || "")}">${hint}</div>`;
    }).join("");
  }

  function recordFormMarkup(record = null) {
    return `<form class="health-form record-form" id="record-form" data-record-form>
      <input type="hidden" name="recordId" value="${escapeHtml(record?.id || "")}">
      <div class="record-form-grid"><div class="health-field"><label for="record-type">Record type</label><select class="health-input" id="record-type" name="type" required>${state.recordTypes.map(type => `<option value="${escapeHtml(type.code)}" ${record?.type === type.code || (!record && type.code === "visit_note") ? "selected" : ""}>${escapeHtml(type.display_name)}</option>`).join("")}</select></div>
      <div class="health-field"><label for="record-date">Date</label><input class="health-input" type="date" id="record-date" name="date" max="${today}" value="${escapeHtml(record?.date || today)}" required></div>
      <div class="health-field form-span"><label for="record-title">Title</label><input class="health-input" id="record-title" name="title" maxlength="140" required value="${escapeHtml(record?.title || "")}" placeholder="A clear, memorable name"></div>
      <div class="form-span record-type-fields" id="record-type-fields"></div>
      <div class="health-field"><label for="record-visibility">Who can see this</label><select class="health-input" id="record-visibility" name="visibility"><option value="private" ${!record || record.visibility === "private" ? "selected" : ""}>Private / only me</option><option value="selected" ${record?.visibility === "selected" ? "selected" : ""}>Selected person</option><option value="family" ${record?.visibility === "family" ? "selected" : ""}>Family / care circle</option></select></div>
      <div class="health-field ${record?.visibility === "selected" ? "" : "is-hidden"}" id="selected-person-field"><label for="share-email">Share with email</label><input class="health-input" type="email" id="share-email" name="shareEmail" autocomplete="email" value="${escapeHtml(state.selectedShareEmail)}" placeholder="person@example.com"><p class="field-hint">The recipient must already have a local account.</p></div>
      <div class="health-field form-span"><label for="record-files">Attachments <span class="optional-label">Optional / up to 4 files</span></label><input class="health-input file-input" id="record-files" type="file" accept="application/pdf,image/png,image/jpeg" multiple><p class="field-hint">PDF, PNG, or JPEG. Up to 2 MB each / 6 MB total.</p><div class="attachment-preview-list" id="attachment-preview-list"></div><progress id="attachment-progress" class="attachment-progress" max="100" value="0" hidden></progress><p id="attachment-progress-label" class="field-hint" role="status" aria-live="polite"></p></div>
      <p class="health-error form-span" id="record-form-error" role="alert" aria-live="polite"></p></div>
      <div class="drawer-form-actions"><button class="secondary-action" type="button" data-close-record>Cancel</button><button class="primary-action" type="submit"><svg class="icon" aria-hidden="true"><use href="icons.svg#check"></use></svg>${record ? "Save changes" : "Add to timeline"}</button></div>
    </form>`;
  }

  function fillDetailFields(form, record) {
    form.elements.namedItem("title").value = record?.title || "";
    for (const field of form.querySelectorAll("[name]:not([name=recordId]):not([name=type]):not([name=date]):not([name=title]):not([name=visibility]):not([name=shareEmail])")) {
      field.value = record?.details?.[field.name] ?? "";
    }
  }

  function setRecordTypeFields(form, record = null) {
    const type = form.elements.namedItem("type").value;
    form.querySelector("#record-type-fields").innerHTML = detailFields(type);
    fillDetailFields(form, record);
    const visibility = form.elements.namedItem("visibility");
    form.querySelector("#selected-person-field").classList.toggle("is-hidden", visibility.value !== "selected");
  }

  function updateAttachmentPreview() {
    const container = document.querySelector("#attachment-preview-list");
    if (!container) return;
    container.innerHTML = state.attachments.map((file, index) => {
      const thumbnail = file.type.startsWith("image/") ? `<img src="${file.dataUrl}" alt="Preview of ${escapeHtml(file.name)}">` : `<span class="attachment-file-icon"><svg class="icon" aria-hidden="true"><use href="icons.svg#file"></use></svg></span>`;
      return `<div class="attachment-preview">${thumbnail}<span>${escapeHtml(file.name)}<small>${Math.ceil(file.size / 1024)} KB</small></span><button type="button" class="icon-button" aria-label="Remove ${escapeHtml(file.name)}" data-remove-attachment="${index}"><svg class="icon" aria-hidden="true"><use href="icons.svg#close"></use></svg></button></div>`;
    }).join("");
  }

  function openRecordDrawer(record = null) {
    if (state.preview) {
      const dialog = document.querySelector("#health-record-drawer");
      document.querySelector("#health-record-drawer-title").textContent = record ? "Record details" : "Add a record";
      document.querySelector("#health-record-drawer-content").innerHTML = record ? recordDetailMarkup(record) : '<p>Record creation is available after you create a local account.</p>';
      dialog.showModal();
      return;
    }
    state.editingRecord = record;
    state.attachments = [];
    state.selectedShareEmail = "";
    const dialog = document.querySelector("#health-record-drawer");
    document.querySelector("#health-record-drawer-title").textContent = record ? "Edit record" : "Add to timeline";
    document.querySelector("#health-record-drawer-content").innerHTML = recordFormMarkup(record);
    const form = document.querySelector("#record-form");
    setRecordTypeFields(form, record);
    updateAttachmentPreview();
    dialog.showModal();
    form.querySelector("#record-title")?.focus({ preventScroll: true });
  }

  function recordDetailMarkup(record) {
    const info = typeInfo(record.type);
    const profile = state.profiles.find(item => item.id === record.profileId) || activeProfile();
    const fields = Object.entries(record.details || {}).filter(([key, value]) => !["note", "notes"].includes(key) && value).map(([key, value]) => `<div class="detail-row"><dt>${escapeHtml(key.replace(/[A-Z]/g, character => ` ${character}`).replace(/^./, first => first.toUpperCase()))}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
    const attachments = (record.attachments || []).map(file => {
      const preview = file.type.startsWith("image/") ? `<img src="${escapeHtml(file.downloadUrl)}" alt="Preview of ${escapeHtml(file.name)}">` : `<svg class="icon" aria-hidden="true"><use href="icons.svg#file"></use></svg>`;
      return `<a class="detail-attachment" href="${escapeHtml(file.downloadUrl)}" download="${escapeHtml(file.name)}">${preview}<span>${escapeHtml(file.name)}<small>${Math.ceil(file.size / 1024)} KB / Download</small></span></a>`;
    }).join("");
    const audit = (record.audit || []).slice().reverse().map(event => `<li><span class="audit-dot"></span><span><strong>${event.action === "created" ? "Record added" : "Record edited"}</strong><small>${new Date(event.at).toLocaleString()} / ${escapeHtml(event.actor)}</small></span></li>`).join("");
    return `<div class="record-detail"><div class="detail-type type-${escapeHtml(record.type)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#${escapeHtml(info.icon)}"></use></svg><span>${escapeHtml(info.display_name)}</span><span class="status-pill visibility-${escapeHtml(record.visibility)}">${escapeHtml(record.visibility)}</span></div><h3>${escapeHtml(record.title)}</h3><p class="detail-date">${new Date(`${record.date}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" })} / ${escapeHtml(profile?.name || "Family")}</p>${record.redacted ? '<p class="consent-redaction">Some health details are hidden by this member\'s consent settings.</p>' : ""}${record.attachmentsHidden ? '<p class="field-hint">Attachments are hidden by this member\'s consent settings.</p>' : ""}${fields ? `<dl class="detail-fields">${fields}</dl>` : ""}${record.details?.notes || record.details?.note ? `<p class="detail-note">${escapeHtml(record.details.notes || record.details.note)}</p>` : ""}<section class="detail-section"><h4>Attachments <span>${(record.attachments || []).length}</span></h4>${attachments || '<p class="field-hint">No attachments are available for this record.</p>'}</section><section class="detail-section"><h4>Record history</h4><ol class="audit-list">${audit || ""}</ol></section><div class="drawer-form-actions"><button class="secondary-action" type="button" data-close-record>Close</button>${record.canEdit === false ? "" : `<button class="secondary-action" type="button" data-delete-record="${escapeHtml(record.id)}">Delete record</button><button class="primary-action" type="button" data-edit-record="${escapeHtml(record.id)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#edit"></use></svg>Edit record</button>`}</div></div>`;
  }

  async function saveRecord(form) {
    const error = form.querySelector("#record-form-error");
    error.textContent = "";
    const values = new FormData(form);
    const type = String(values.get("type"));
    const data = { title: String(values.get("title") || "").trim() };
    for (const [key, value] of values.entries()) {
      if (["recordId", "type", "date", "title", "visibility", "shareEmail"].includes(key)) continue;
      if (String(value).trim()) data[key] = String(value).trim();
    }
    const profileId = activeProfile()?.id;
    if (!profileId) {
      error.textContent = "Choose a profile before adding a record.";
      return;
    }
    const visibility = String(values.get("visibility") || "private");
    const shareEmail = String(values.get("shareEmail") || "").trim();
    if (visibility === "selected" && !shareEmail) {
      error.textContent = "Enter the account email to share this record.";
      form.elements.namedItem("shareEmail").focus();
      return;
    }
    const payload = {
      type,
      recorded_at: new Date(`${values.get("date")}T00:00:00Z`).toISOString(),
      visibility: visibility === "selected" ? "private" : visibility,
      data,
    };
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    const previousRecords = [...state.records];
    const isEdit = Boolean(state.editingRecord?.id);
    const oldRecord = state.editingRecord;
    const temporaryRecord = {
      ...(oldRecord || {}),
      id: oldRecord?.id || `pending-${crypto.randomUUID()}`,
      profileId,
      type,
      title: data.title,
      date: String(values.get("date")),
      visibility: visibility === "selected" ? "selected" : visibility,
      details: Object.fromEntries(Object.entries(data).filter(([key]) => key !== "title")),
      attachments: oldRecord?.attachments || [],
      audit: oldRecord?.audit || [],
    };
    state.records = isEdit
      ? state.records.map(record => record.id === oldRecord.id ? temporaryRecord : record)
      : [temporaryRecord, ...state.records];
    const timeline = document.querySelector("#vine-results");
    if (timeline) timeline.innerHTML = renderTimelineEvents();
    let savedRecord = null;
    try {
      const path = isEdit
        ? `/records/${encodeURIComponent(oldRecord.id)}`
        : `/profiles/${encodeURIComponent(profileId)}/records`;
      const result = await request(path, { method: isEdit ? "PATCH" : "POST", body: payload });
      savedRecord = timelineRecordFromApi(result);
      state.records = state.records.map(record =>
        record.id === temporaryRecord.id ? savedRecord : record);
      if (visibility === "selected") {
        await request(`/records/${encodeURIComponent(savedRecord.id)}/share`, {
          method: "POST",
          body: { email: shareEmail, role: "viewer", permissions: ["read"] },
        });
        savedRecord.visibility = "selected";
      }

      const progress = form.querySelector("#attachment-progress");
      const progressLabel = form.querySelector("#attachment-progress-label");
      const { apiUpload } = await import("./js/api.js");
      const totalUploads = state.attachments.length;
      let completedUploads = 0;
      if (totalUploads) {
        progress.hidden = false;
        progress.value = 0;
        for (const file of [...state.attachments]) {
          progressLabel.textContent = `Uploading ${file.name}...`;
          const upload = new FormData();
          upload.append("file", file.file, file.name);
          upload.append("record_id", savedRecord.id);
          const attachment = await apiUpload(
            `/profiles/${encodeURIComponent(profileId)}/attachments`,
            upload,
            {
              onProgress: fraction => {
                progress.value = Math.round(((completedUploads + fraction) / totalUploads) * 100);
              },
            },
          );
          savedRecord.attachments.push({
            id: attachment.id,
            name: attachment.original_filename,
            type: attachment.content_type,
            size: attachment.file_size_bytes,
            downloadUrl: `/api/v1/attachments/${encodeURIComponent(attachment.id)}/download`,
          });
          state.attachments.shift();
          completedUploads += 1;
          updateAttachmentPreview();
        }
      }
      document.querySelector("#health-record-drawer").close();
      state.timelineCursor = null;
      await loadTimeline();
      try {
        await refreshEmergencyCardCache(profileId);
        announce(isEdit ? "Record updated." : "Record added to the timeline.");
      } catch (cacheError) {
        announce(`Record saved, but the offline emergency card could not be refreshed: ${cacheError.message}`, "error");
      }
      state.editingRecord = null;
      state.selectedShareEmail = "";
    } catch (saveError) {
      error.textContent = saveError.message;
      if (savedRecord) {
        state.editingRecord = savedRecord;
        form.elements.namedItem("recordId").value = savedRecord.id;
        button.textContent = "Retry save";
        if (timeline) timeline.innerHTML = renderTimelineEvents();
      } else {
        state.records = previousRecords;
        if (timeline) timeline.innerHTML = renderTimelineEvents();
      }
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  async function deleteRecord(recordId) {
    const index = state.records.findIndex(record => record.id === recordId);
    if (index < 0) return;
    if (!window.confirm("Delete this record from the timeline? This cannot be undone.")) return;
    const [record] = state.records.splice(index, 1);
    const container = document.querySelector("#vine-results");
    if (container) container.innerHTML = renderTimelineEvents();
    document.querySelector("#health-record-drawer").close();
    try {
      const { apiDelete } = await import("./js/api.js");
      await apiDelete(`/records/${encodeURIComponent(recordId)}`);
      try {
        await refreshEmergencyCardCache(record.profileId);
        announce("Record deleted.");
      } catch (cacheError) {
        announce(`Record deleted, but the offline emergency card could not be refreshed: ${cacheError.message}`, "error");
      }
    } catch (error) {
      state.records.splice(index, 0, record);
      if (container) container.innerHTML = renderTimelineEvents();
      announce(`Record was not deleted: ${error.message}`, "error");
      throw error;
    }
  }

  function announce(message, kind = "success") {
    const region = document.querySelector("#health-toasts");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `shell-toast${kind === "error" ? " error" : ""}`;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");
    toast.textContent = message;
    region.append(toast);
    window.setTimeout(() => toast.remove(), 3600);
  }

  async function readAttachments(files) {
    if (state.attachments.length + files.length > 4) throw new Error("Attach up to four files to one record.");
    const allowed = new Set(["application/pdf", "image/png", "image/jpeg"]);
    let totalBytes = state.attachments.reduce((total, file) => total + file.size, 0);
    for (const file of files) {
      if (!allowed.has(file.type)) throw new Error("Use a PDF, PNG, or JPEG attachment.");
      if (file.size > 2 * 1024 * 1024) throw new Error("Each attachment must be 2 MB or smaller.");
      totalBytes += file.size;
      if (totalBytes > 6 * 1024 * 1024) throw new Error("Attachments must total 6 MB or less.");
    }
    const loaded = await Promise.all(files.map(file => new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({ id: crypto.randomUUID(), file, name: file.name, type: file.type, size: file.size, dataUrl: reader.result });
      reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
      reader.readAsDataURL(file);
    })));
    state.attachments.push(...loaded);
    updateAttachmentPreview();
  }

  async function afterRender(route) {
    if (route === "/timeline") await loadTimeline();
    if (route === "/family") await refreshFamilyDashboard();
  }

  async function refreshFamilyDashboard() {
    const container = document.querySelector("#family-dashboard");
    if (!container) return;
    container.dataset.familyReady = "false";
    if (state.preview) {
      container.innerHTML = renderFamilyDashboard();
      container.dataset.familyReady = "true";
      return;
    }
    try {
      state.families = await request("/families");
      if (!state.families.some(family => family.id === state.activeFamilyId)) {
        state.activeFamilyId = state.families[0]?.id || "";
      }
      state.familyData = state.activeFamilyId
        ? familyDashboardFromApi(await request(`/families/${encodeURIComponent(state.activeFamilyId)}/dashboard`))
        : null;
    } catch (error) {
      container.innerHTML = `<div class="timeline-empty"><h2>Family dashboard could not load.</h2><p>${escapeHtml(error.message)}</p><button class="secondary-action" type="button" data-refresh-family>Try again</button></div>`;
      container.dataset.familyReady = "true";
      return;
    }
    container.innerHTML = renderFamilyDashboard();
    container.dataset.familyReady = "true";
  }

  document.addEventListener("submit", event => {
    const createFamilyForm = event.target.closest("[data-family-create]");
    if (createFamilyForm) {
      event.preventDefault();
      const error = createFamilyForm.querySelector('[role="alert"]');
      const submit = createFamilyForm.querySelector('[type="submit"]');
      const name = String(new FormData(createFamilyForm).get("name") || "").trim();
      error.textContent = "";
      submit.disabled = true;
      void request("/families", { method: "POST", body: { name } })
        .then(async family => {
          state.families = await request("/families");
          state.activeFamilyId = family.id;
          state.lastInvite = null;
          await refreshFamilyDashboard();
          announce("Family circle created.");
        })
        .catch(requestError => { error.textContent = requestError.message; })
        .finally(() => { submit.disabled = false; });
      return;
    }
    const inviteDecisionForm = event.target.closest("[data-family-decision]");
    if (inviteDecisionForm) {
      event.preventDefault();
      const error = inviteDecisionForm.querySelector('[role="alert"]');
      const submit = event.submitter;
      const decision = submit?.dataset.decision || "accept";
      const inviteCode = String(new FormData(inviteDecisionForm).get("inviteCode") || "").trim();
      const previousFamilyIds = new Set(state.families.map(family => family.id));
      error.textContent = "";
      inviteDecisionForm.querySelectorAll("button").forEach(button => { button.disabled = true; });
      void request(`/family-invites/${decision}`, {
        method: "POST",
        body: { invite_code: inviteCode },
      })
        .then(async () => {
          if (decision === "accept") {
            const profiles = await request("/profiles");
            state.profiles = profiles.map(fromApiProfile);
            window.dispatchEvent(new CustomEvent("healthprofileschange", { detail: memberProfiles() }));
            state.families = await request("/families");
            state.activeFamilyId = state.families.find(family => !previousFamilyIds.has(family.id))?.id
              || state.activeFamilyId;
            announce("You joined the family circle.");
          } else {
            announce("Invitation declined.");
          }
          window.location.hash = "#/family";
          await refreshFamilyDashboard();
        })
        .catch(requestError => { error.textContent = requestError.message; })
        .finally(() => {
          inviteDecisionForm.querySelectorAll("button").forEach(button => { button.disabled = false; });
        });
      return;
    }
    const inviteForm = event.target.closest("[data-family-invite]");
    if (inviteForm) {
      event.preventDefault();
      const error = inviteForm.querySelector("#invite-error");
      const submit = inviteForm.querySelector('[type="submit"]');
      error.textContent = "";
      submit.disabled = true;
      const values = new FormData(inviteForm);
      void request(`/families/${encodeURIComponent(state.activeFamilyId)}/invites`, {
        method: "POST",
        body: {
          role: values.get("role"),
          expires_in_hours: Number(values.get("expiresInHours")),
        },
      })
        .then(result => {
          const inviteUrl = `${window.location.origin}${window.location.pathname}#/family?invite=${encodeURIComponent(result.invite_code)}`;
          state.lastInvite = { inviteUrl, expiresAt: result.expires_at };
          inviteForm.reset();
          return refreshFamilyDashboard();
        })
        .catch(requestError => { error.textContent = requestError.message; })
        .finally(() => { submit.disabled = false; });
      return;
    }
    const onboardingForm = event.target.closest("[data-onboarding-form]");
    if (onboardingForm) {
      event.preventDefault();
      collectOnboarding(onboardingForm);
      const message = document.querySelector("#onboarding-error");
      message.textContent = "";
      if (state.onboardingStep === 0 && !state.onboarding.name) {
        message.textContent = "Add a name to continue.";
        onboardingForm.querySelector("#profile-name").focus();
        return;
      }
      const submit = onboardingForm.querySelector('[type="submit"]');
      const actions = onboardingForm.querySelectorAll("button");
      actions.forEach(button => { button.disabled = true; });
      submit?.setAttribute("aria-busy", "true");
      if (state.onboardingStep < 2) {
        void saveOnboarding(state.onboardingStep + 1)
          .then(() => window.renderRoute())
          .catch(error => { message.textContent = error.message; })
          .finally(() => {
            actions.forEach(button => { button.disabled = false; });
            submit?.removeAttribute("aria-busy");
          });
      } else {
        void saveOnboarding(2, true)
          .catch(error => { message.textContent = error.message; })
          .finally(() => {
            actions.forEach(button => { button.disabled = false; });
            submit?.removeAttribute("aria-busy");
          });
      }
      return;
    }
    const recordForm = event.target.closest("[data-record-form]");
    if (recordForm) {
      event.preventDefault();
      void saveRecord(recordForm);
      return;
    }
    const filterForm = event.target.closest("[data-timeline-filter]");
    if (filterForm) {
      event.preventDefault();
      const values = new FormData(filterForm);
      state.query = String(values.get("q") || "").trim();
      state.type = String(values.get("type") || "");
      if (values.get("year")) state.year = Number(values.get("year"));
      void loadTimeline();
    }
  });

  document.addEventListener("click", event => {
    if (event.target.closest("[data-copy-invite]")) {
      const input = document.querySelector(".invite-copy-row input");
      if (!input) return;
      void navigator.clipboard.writeText(input.value).then(() => announce("Invitation link copied.")).catch(() => { input.select(); announce("Select and copy the invitation link.", "error"); });
      return;
    }
    if (event.target.closest("[data-refresh-family]")) {
      void refreshFamilyDashboard();
      return;
    }
    const removeFamilyMember = event.target.closest("[data-remove-family-member]");
    if (removeFamilyMember) {
      const profileId = removeFamilyMember.dataset.removeFamilyMember;
      const isSelf = profileId === state.familyData?.currentProfileId;
      if (!window.confirm("Remove this member from the family circle? Their own account and records will remain.")) return;
      removeFamilyMember.disabled = true;
      void request(`/families/${encodeURIComponent(state.activeFamilyId)}/members/${encodeURIComponent(profileId)}`, {
        method: "DELETE",
      })
        .then(async () => {
          await refreshFamilyDashboard();
          announce(isSelf ? "You left the family circle." : "Family member removed.");
        })
        .catch(error => {
          announce(`The member could not be removed: ${error.message}`, "error");
          void refreshFamilyDashboard();
        });
      return;
    }
    const back = event.target.closest("[data-onboarding-back]");
    if (back && state.onboardingStep > 0) {
      const form = document.querySelector("[data-onboarding-form]");
      collectOnboarding(form);
      const previousStep = state.onboardingStep - 1;
      const buttons = form.querySelectorAll("button");
      buttons.forEach(button => { button.disabled = true; });
      void saveOnboarding(state.onboardingStep)
        .then(() => {
          state.onboardingStep = previousStep;
          window.renderRoute();
        })
        .catch(error => {
          document.querySelector("#onboarding-error").textContent = error.message;
          buttons.forEach(button => { button.disabled = false; });
        });
      return;
    }
    const skip = event.target.closest("[data-onboarding-skip]");
    if (skip) {
      const form = document.querySelector("[data-onboarding-form]");
      collectOnboarding(form);
      const message = document.querySelector("#onboarding-error");
      message.textContent = "";
      const nextStep = Math.min(state.onboardingStep + 1, 2);
      const complete = state.onboardingStep === 2;
      const buttons = form.querySelectorAll("button");
      buttons.forEach(button => { button.disabled = true; });
      void saveOnboarding(nextStep, complete)
        .then(() => { if (!complete) window.renderRoute(); })
        .catch(error => { message.textContent = error.message; })
        .finally(() => { buttons.forEach(button => { button.disabled = false; }); });
      return;
    }
    const zoom = event.target.closest("[data-timeline-zoom]");
    if (zoom) {
      state.zoom = zoom.dataset.timelineZoom;
      state.year = currentYear;
      window.renderRoute();
      return;
    }
    const addRecord = event.target.closest("[data-new-record]");
    if (addRecord) {
      openRecordDrawer();
      return;
    }
    const openRecord = event.target.closest("[data-open-record]");
    if (openRecord) {
      const record = state.records.find(item => item.id === openRecord.dataset.openRecord);
      if (record) {
        document.querySelector("#health-record-drawer-title").textContent = "Record details";
        document.querySelector("#health-record-drawer-content").innerHTML = recordDetailMarkup(record);
        document.querySelector("#health-record-drawer").showModal();
      }
      return;
    }
    const editRecord = event.target.closest("[data-edit-record]");
    if (editRecord) {
      const record = state.records.find(item => item.id === editRecord.dataset.editRecord);
      document.querySelector("#health-record-drawer").close();
      if (record) openRecordDrawer(record);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-record]");
    if (deleteButton) {
      void deleteRecord(deleteButton.dataset.deleteRecord).catch(() => {});
      return;
    }
    if (event.target.closest("[data-load-more-timeline]")) {
      void loadTimeline({ append: true });
      return;
    }
    if (event.target.closest("[data-close-record]")) {
      document.querySelector("#health-record-drawer").close();
      return;
    }
    const removeAttachment = event.target.closest("[data-remove-attachment]");
    if (removeAttachment) {
      state.attachments.splice(Number(removeAttachment.dataset.removeAttachment), 1);
      updateAttachmentPreview();
      return;
    }
    const profileButton = event.target.closest("[data-health-select-profile]");
    if (profileButton) {
      state.activeProfileId = profileButton.dataset.healthSelectProfile;
      window.setActiveHealthProfile?.(state.activeProfileId);
      window.location.hash = "#/timeline";
      return;
    }
    if (event.target.closest("[data-reload-timeline]")) void loadTimeline();
  });

  document.addEventListener("change", event => {
    const roleSelect = event.target.closest("[data-member-role]");
    if (roleSelect) {
      roleSelect.disabled = true;
      void request(`/families/${encodeURIComponent(state.activeFamilyId)}/members/${encodeURIComponent(roleSelect.dataset.memberRole)}`, {
        method: "PATCH",
        body: { role: roleSelect.value },
      })
        .then(() => refreshFamilyDashboard())
        .then(() => announce("Family role updated."))
        .catch(error => {
          announce(`The role could not be updated: ${error.message}`, "error");
          void refreshFamilyDashboard();
        });
      return;
    }
    const consentToggle = event.target.closest("[data-consent-toggle]");
    if (consentToggle) {
      const sourceProfileId = consentToggle.dataset.sourceProfile;
      const recipientProfileId = consentToggle.dataset.recipientProfile;
      const resource = consentToggle.dataset.consentResource;
      const current = (state.familyData?.consents || []).find(consent =>
        consent.profileId === sourceProfileId
        && consent.recipientProfileId === recipientProfileId
        && !consent.revokedAt
        && (!consent.expiresAt || new Date(consent.expiresAt).getTime() > Date.now()));
      const resources = new Set(current?.resources || []);
      if (consentToggle.checked) resources.add(resource);
      else resources.delete(resource);
      consentToggle.disabled = true;
      const path = `/families/${encodeURIComponent(state.activeFamilyId)}/consents`;
      const mutation = current
        ? request(`${path}/${encodeURIComponent(current.id)}`, {
          method: "PATCH",
          body: { resources: [...resources] },
        })
        : request(path, {
          method: "POST",
          body: {
            profile_id: sourceProfileId,
            recipient_profile_id: recipientProfileId,
            resources: [resource],
          },
        });
      void mutation
        .then(() => refreshFamilyDashboard())
        .then(() => announce(consentToggle.checked ? "Consent shared." : "Consent updated."))
        .catch(error => {
          announce(`Consent could not be updated: ${error.message}`, "error");
          void refreshFamilyDashboard();
        });
      return;
    }
    const familySelect = event.target.closest("[data-family-select]");
    if (familySelect) {
      state.activeFamilyId = familySelect.value;
      state.familyData = null;
      void refreshFamilyDashboard();
      return;
    }
    if (event.target.matches("#record-type")) setRecordTypeFields(event.target.form, state.editingRecord);
    if (event.target.matches("#record-visibility")) {
      document.querySelector("#selected-person-field")?.classList.toggle("is-hidden", event.target.value !== "selected");
    }
    if (event.target.matches("#record-files")) {
      void readAttachments([...event.target.files]).catch(error => {
        const message = document.querySelector("#record-form-error");
        if (message) message.textContent = error.message;
      });
      event.target.value = "";
    }
    if (event.target.matches('[data-timeline-filter] select')) {
      const form = event.target.form;
      const values = new FormData(form);
      state.type = String(values.get("type") || "");
      if (values.get("year")) state.year = Number(values.get("year"));
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(() => void loadTimeline(), 180);
    }
  });

  document.addEventListener("input", event => {
    if (!event.target.matches('[data-timeline-filter] input[name="q"]')) return;
    state.query = event.target.value.trim();
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => void loadTimeline(), 180);
  });

  window.HealthPages = { bootstrap, setProfiles, setActiveProfile, memberProfiles, activeProfile, renderOnboarding, renderHome: homePage, renderFamily: familyPage, renderTimeline, afterRender };
})();
