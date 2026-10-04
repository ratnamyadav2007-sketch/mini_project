(() => {
  const slots = ["breakfast", "lunch", "dinner", "snack"];
  const slotLabels = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };
  const goalUnits = { steps: "steps", water: "ml", sleep: "hours", weight: "kg" };
  const goalTargets = { steps: 8000, water: 2000, sleep: 8, weight: 70 };
  const goalMaxima = { steps: 50000, water: 10000, sleep: 24, weight: 500 };
  const state = {
    profile: null,
    preview: new URLSearchParams(window.location.search).get("preview") === "1",
    mode: "plan",
    currentWeek: mondayOf(new Date()),
    plan: null,
    goals: [],
    badges: [],
    pendingMoves: new Map(),
  };

  function mondayOf(date) {
    const value = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    value.setDate(value.getDate() - ((value.getDay() + 6) % 7));
    return value;
  }

  function dateString(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }

  function parseDate(value) {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function api() {
    return import("./js/api.js");
  }

  function activeProfile() {
    return state.profile || window.HealthPages.activeProfile();
  }

  function notify(message, kind = "success") {
    const region = document.querySelector("#health-toasts");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `shell-toast${kind === "error" ? " error" : ""}${kind === "celebration" ? " badge-celebration" : ""}`;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");
    toast.textContent = message;
    region.append(toast);
    window.setTimeout(() => toast.remove(), 4000);
  }

  function header(kicker, title, copy = "") {
    return `<header class="page-header"><p class="page-kicker">${kicker}</p><h1 class="page-title">${title}</h1>${copy ? `<p class="page-lede">${copy}</p>` : ""}</header>`;
  }

  function dayDate(dayIndex) {
    const value = new Date(state.plan.starts_on + "T00:00:00");
    value.setDate(value.getDate() + dayIndex);
    return dateString(value);
  }

  function renderGenerateForm() {
    return `<section class="diet-wizard"><h2>Generate a week of meals</h2><p>Allergies, conditions, and clinician exclusions recorded on this profile override suggestions. The plan is an estimate, not medical advice.</p><form class="diet-wizard-form" data-plan-generate>
      <label class="health-field"><span>Weight (kg)</span><input class="health-input" name="weight_kg" type="number" min="30" max="300" step="0.1" value="68" required></label>
      <label class="health-field"><span>Height (cm)</span><input class="health-input" name="height_cm" type="number" min="120" max="240" step="0.1" value="165" required></label>
      <label class="health-field"><span>Activity level</span><select class="health-input" name="activity_level"><option value="sedentary">Sedentary</option><option value="light" selected>Light</option><option value="moderate">Moderate</option><option value="high">High</option></select></label>
      <button class="primary-action" type="submit">Generate weekly plan</button>
    </form></section>`;
  }

  function renderMeals() {
    if (!state.plan) return renderGenerateForm();
    const meals = state.plan.meals;
    const days = Array.from({ length: 7 }, (_, index) => {
      const date = dayDate(index);
      const label = parseDate(date).toLocaleDateString(undefined, { weekday: "long" });
      const dayMeals = meals.filter(meal => meal.scheduled_on === date);
      return `<section class="meal-day-column" data-day-column="${index}"><h3>${label}</h3><div class="meal-day-list">${dayMeals.length ? dayMeals.map(meal => {
        const nutrients = meal.nutrients || {};
        const warnings = meal.allergen_warnings || [];
        return `<article class="meal-card${warnings.length ? " has-allergen-warning" : ""}" data-meal-card="${escapeHtml(meal.id)}">
          <span class="meal-slot">${escapeHtml(slotLabels[meal.meal_type] || meal.meal_type)}</span>
          <strong class="meal-name">${escapeHtml(meal.name)}</strong>
          ${warnings.length ? `<p class="allergen-warning" role="alert">Check: ${warnings.map(escapeHtml).join(", ")}</p>` : ""}
          <div class="meal-nutrient-mini"><span>${Math.round(nutrients.energy_kcal || 0)} kcal</span><span>${Math.round(nutrients.protein_g || 0)}g protein</span></div>
          <label class="meal-move-label"><span class="sr-only">Move ${escapeHtml(meal.name)}</span><select class="meal-move-select" data-move-meal="${escapeHtml(meal.id)}" data-original-day="${index}" data-original-slot="${escapeHtml(meal.meal_type)}">
            ${Array.from({ length: 7 }, (_, dayIndex) => slots.map(slot => `<option value="${dayIndex}|${slot}" ${dayIndex === index && slot === meal.meal_type ? "selected" : ""}>${parseDate(dayDate(dayIndex)).toLocaleDateString(undefined, { weekday: "short" })} / ${slotLabels[slot]}</option>`).join("")).join("")}
          </select></label>
        </article>`;
      }).join("") : '<p class="empty-meal-day">No meals planned.</p>'}</div></section>`;
    }).join("");
    const totals = state.plan.weekly_totals || {};
    return `<div class="weekly-plan-toolbar"><div><p class="page-kicker">${escapeHtml(state.plan.starts_on)} – ${escapeHtml(state.plan.ends_on)}</p><h2>${escapeHtml(state.plan.name)}</h2></div><div class="weekly-plan-actions">
      <button class="icon-button" type="button" data-week-shift="-1" aria-label="Previous week">‹</button><button class="icon-button" type="button" data-week-shift="1" aria-label="Next week">›</button><button class="secondary-action" type="button" data-regenerate-plan>Generate another week</button>
      </div></div>
      <div class="nutrient-strip" aria-label="Weekly nutrient totals">${[
        ["energy_kcal", "kcal / week"], ["protein_g", "protein g"], ["carbohydrate_g", "carbohydrate g"], ["fat_g", "fat g"], ["fiber_g", "fiber g"],
      ].map(([key, label]) => `<div><strong>${Math.round(totals[key] || 0)}</strong><span>${label}</span></div>`).join("")}</div>
      <p class="nutrient-disclaimer">${escapeHtml(state.plan.disclaimer)}</p>
      <div class="weekly-meal-board" aria-label="Weekly meal plan">${days}</div>`;
  }

  function renderGoals() {
    const cards = state.goals.map(goal => {
      const meta = goalUnits[goal.kind] || goal.unit;
      const todayLog = (goal.logs || []).find(log => log.logged_on === dateString(new Date()));
      const value = todayLog?.value ?? "";
      const progress = todayLog ? Math.min(100, Math.round(Number(value) / goal.target_value * 100)) : 0;
      return `<article class="wellness-goal-card fern"><h3>${escapeHtml(goal.title)}</h3><p>${escapeHtml(String(value || 0))} / ${escapeHtml(String(goal.target_value))} ${escapeHtml(meta)} today</p><p class="goal-streak">${goal.current_streak ? `${goal.current_streak}-day streak` : "Log today to get started"}</p>
        <form class="goal-log-form" data-goal-log="${escapeHtml(goal.id)}"><label><span class="sr-only">Today's ${escapeHtml(meta)}</span><input class="health-input" name="value" type="number" min="0.01" max="${goalMaxima[goal.kind]}" step="${goal.kind === "weight" ? "0.1" : "1"}" value="${escapeHtml(String(value))}" required></label><span>${escapeHtml(meta)}</span><button class="secondary-action" type="submit">Log today</button></form><div class="goal-progress" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progress}%"></span></div></article>`;
    }).join("");
    const badges = state.badges.map(badge => `<li>${escapeHtml(badge.badge_code.replaceAll("_", " "))}</li>`).join("");
    return `<section class="goals-heading"><div><p class="page-kicker">Small steps / Your rhythm</p><h2>Wellness goals</h2><p>Logs, streaks, and badges are returned by the server.</p></div></section>
      <div class="wellness-goal-grid">${cards || '<div class="goals-empty"><h3>Choose a small goal to begin.</h3></div>'}</div>
      <section class="wellness-add-goal"><h3>Add a goal</h3><form class="goal-create-form" data-goal-create>
      <label class="health-field"><span>Focus</span><select class="health-input" name="kind">${Object.keys(goalUnits).map(kind => `<option value="${kind}">${kind[0].toUpperCase()}${kind.slice(1)}</option>`).join("")}</select></label>
      <label class="health-field"><span>Goal name</span><input class="health-input" name="title" maxlength="160" required></label>
      <label class="health-field"><span data-goal-unit>Daily target (${goalUnits.steps})</span><input class="health-input" name="target_value" type="number" min="0.1" value="${goalTargets.steps}" required></label><button class="primary-action" type="submit">Add goal</button></form></section>
      ${badges ? `<section class="wellness-add-goal"><h3>Badge awards</h3><ul>${badges}</ul></section>` : ""}`;
  }

  function renderDiet() {
    const profile = activeProfile();
    if (!profile) return `${header("Food & wellbeing", "Add a profile first.")}<a class="primary-action" href="#/onboarding">Add profile</a>`;
    return `<div class="wellness-page">${header(`Food & wellbeing / ${escapeHtml(profile.name)}`, "A plan that fits real life.", "Plan meals using profile safety records, and track goals with server-calculated progress.")}
      <div class="wellness-tabs" role="tablist" aria-label="Diet and wellness views"><button type="button" role="tab" aria-selected="${state.mode === "plan"}" data-wellness-mode="plan">Weekly plan</button><button type="button" role="tab" aria-selected="${state.mode === "goals"}" data-wellness-mode="goals">Wellness goals</button></div><div id="wellness-content">${state.mode === "plan" ? renderMeals() : renderGoals()}</div></div>`;
  }

  async function loadPlan() {
    const profile = activeProfile();
    if (!profile) return;
    if (state.preview) {
      const starts = dateString(state.currentWeek);
      state.plan = {
        id: "preview-plan",
        name: "Flexible weekly plan",
        starts_on: starts,
        ends_on: dateString(new Date(state.currentWeek.getFullYear(), state.currentWeek.getMonth(), state.currentWeek.getDate() + 6)),
        meals: Array.from({ length: 7 }, (_, day) => ({
          id: `preview-meal-${day}`,
          name: "Lentil and vegetable bowl",
          meal_type: slots[day % slots.length],
          scheduled_on: dateString(new Date(state.currentWeek.getFullYear(), state.currentWeek.getMonth(), state.currentWeek.getDate() + day)),
          allergens: [],
          allergen_warnings: [],
          nutrients: { energy_kcal: 420, protein_g: 20 },
        })),
        weekly_totals: { energy_kcal: 2940, protein_g: 140, carbohydrate_g: 350, fat_g: 70, fiber_g: 80 },
        disclaimer: "Preview nutrition estimates are examples, not medical advice.",
      };
      return;
    }
    const { apiGet } = await api();
    const result = await apiGet(`/profiles/${encodeURIComponent(profile.id)}/diet/plans`);
    state.plan = result.items.find(plan => plan.starts_on === dateString(state.currentWeek)) || null;
  }

  async function loadGoals() {
    const profile = activeProfile();
    if (!profile) return;
    if (state.preview) {
      const today = dateString(new Date());
      state.goals = [
        {
          id: "preview-steps",
          kind: "steps",
          title: "A little more movement",
          target_value: 8000,
          current_streak: 3,
          logs: [{ logged_on: today, value: 6200 }],
        },
        {
          id: "preview-water",
          kind: "water",
          title: "Keep water nearby",
          target_value: 2000,
          current_streak: 1,
          logs: [{ logged_on: today, value: 1500 }],
        },
      ];
      state.badges = [];
      return;
    }
    const { apiGet } = await api();
    const [goals, badges] = await Promise.all([
      apiGet(`/profiles/${encodeURIComponent(profile.id)}/goals`),
      apiGet(`/profiles/${encodeURIComponent(profile.id)}/badges`),
    ]);
    state.badges = badges;
    state.goals = await Promise.all(goals.map(async goal => ({
      ...goal,
      logs: await apiGet(`/goals/${encodeURIComponent(goal.id)}/logs?limit=366`),
    })));
  }

  async function refresh() {
    const root = document.querySelector("#wellness-content");
    if (!root) return;
    root.innerHTML = '<div class="wellness-loading" role="status">Loading saved wellness data…</div>';
    try {
      if (state.mode === "plan") await loadPlan();
      else await loadGoals();
      root.innerHTML = state.mode === "plan" ? renderMeals() : renderGoals();
    } catch (error) {
      root.innerHTML = `<div class="timeline-empty" role="alert"><h2>Wellness data could not load.</h2><p>${escapeHtml(error.message)}</p><button class="secondary-action" type="button" data-wellness-retry>Try again</button></div>`;
    }
  }

  async function generatePlan(form) {
    if (state.preview) {
      notify("Plan generation is unavailable in preview mode.", "error");
      return;
    }
    const profile = activeProfile();
    const values = new FormData(form);
    const { apiPost } = await api();
    const root = document.querySelector("#wellness-content");
    root.innerHTML = '<div class="wellness-loading" role="status">Generating a safe plan…</div>';
    try {
      state.plan = await apiPost(`/diet/plans/generate?profile_id=${encodeURIComponent(profile.id)}`, {
        weight_kg: Number(values.get("weight_kg")),
        height_cm: Number(values.get("height_cm")),
        activity_level: String(values.get("activity_level")),
        starts_on: dateString(state.currentWeek),
      });
      root.innerHTML = renderMeals();
      notify("Your weekly plan is ready.");
    } catch (error) {
      root.innerHTML = `${renderGenerateForm()}<p class="field-hint" role="alert">${escapeHtml(error.message)}</p>`;
    }
  }

  async function persistMove(select) {
    const id = select.dataset.moveMeal;
    const [dayIndex, mealType] = select.value.split("|");
    if (state.preview) return;
    const { apiPatch } = await api();
    const oldPlan = state.plan;
    try {
      const moved = await apiPatch(`/diet/plans/${encodeURIComponent(state.plan.id)}/meals/${encodeURIComponent(id)}`, {
        scheduled_on: dayDate(Number(dayIndex)),
        meal_type: mealType,
        sort_order: 0,
      });
      state.plan = moved;
      document.querySelector("#wellness-content").innerHTML = renderMeals();
    } catch (error) {
      state.plan = oldPlan;
      document.querySelector("#wellness-content").innerHTML = renderMeals();
      notify(error.message, "error");
    }
  }

  document.addEventListener("submit", event => {
    const generate = event.target.closest("[data-plan-generate]");
    if (generate) {
      event.preventDefault();
      void generatePlan(generate);
      return;
    }
    const create = event.target.closest("[data-goal-create]");
    if (create) {
      event.preventDefault();
      if (state.preview) {
        notify("Goal changes are not saved in preview mode.", "error");
        return;
      }
      void (async () => {
        const { apiPost } = await api();
        const values = new FormData(create);
        await apiPost(`/profiles/${encodeURIComponent(activeProfile().id)}/goals`, {
          kind: String(values.get("kind")),
          title: String(values.get("title")).trim(),
          target_value: Number(values.get("target_value")),
        });
        await refresh();
        notify("Wellness goal added.");
      })().catch(error => notify(error.message, "error"));
      return;
    }
    const log = event.target.closest("[data-goal-log]");
    if (log) {
      event.preventDefault();
      if (state.preview) {
        notify("Goal logs are not saved in preview mode.", "error");
        return;
      }
      void (async () => {
        const { apiPost } = await api();
        const response = await apiPost(`/goals/${encodeURIComponent(log.dataset.goalLog)}/logs`, {
          value: Number(new FormData(log).get("value")),
          logged_on: dateString(new Date()),
        });
        await refresh();
        const awarded = response.awarded_badges || [];
        if (awarded.length) notify(`Badge earned: ${awarded.join(", ").replaceAll("_", " ")}!`, "celebration");
        else notify("Goal log saved.");
      })().catch(error => notify(error.message, "error"));
    }
  });

  document.addEventListener("change", event => {
    const move = event.target.closest("[data-move-meal]");
    if (move) {
      const id = move.dataset.moveMeal;
      window.clearTimeout(state.pendingMoves.get(id));
      state.pendingMoves.set(id, window.setTimeout(() => void persistMove(move), 250));
    }
    const form = event.target.closest("[data-goal-create]");
    if (form) {
      const kind = form.elements.namedItem("kind").value;
      form.elements.namedItem("target_value").value = goalTargets[kind];
      form.querySelector("[data-goal-unit]").textContent = `Daily target (${goalUnits[kind]})`;
    }
  });

  document.addEventListener("click", event => {
    const mode = event.target.closest("[data-wellness-mode]");
    if (mode) {
      state.mode = mode.dataset.wellnessMode;
      void refresh();
      return;
    }
    if (event.target.closest("[data-wellness-retry]")) {
      void refresh();
      return;
    }
    const shift = event.target.closest("[data-week-shift]");
    if (shift) {
      state.currentWeek.setDate(state.currentWeek.getDate() + Number(shift.dataset.weekShift) * 7);
      state.plan = null;
      void refresh();
    }
    if (event.target.closest("[data-regenerate-plan]")) {
      state.plan = null;
      document.querySelector("#wellness-content").innerHTML = renderGenerateForm();
    }
  });

  async function afterRender(route) {
    if (route !== "/diet") return;
    state.profile = window.HealthPages.activeProfile();
    await refresh();
  }

  window.WellnessPages = {
    renderDiet,
    afterRender,
    bootstrap() {
      if (state.preview || new URLSearchParams(window.location.search).get("mock") === "1") {
        state.profile = window.HealthPages.activeProfile();
      }
    },
  };
})();
