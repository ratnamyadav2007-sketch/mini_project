(() => {
  const slots = ["breakfast", "lunch", "dinner", "snack"];
  const slotLabels = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };
  const dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const today = new Date();
  const palette = { butter: "#b37b14", coral: "#b94e3d", fern: "#456c58", lake: "#416c78" };
  const mealTemplates = {
    omnivore: [
      { slot: "breakfast", name: "Apple cinnamon oats", ingredients: ["oats", "apple", "milk", "cinnamon"], allergens: ["milk"], nutrients: { calories: 360, protein: 15, carbs: 58, fat: 8, fiber: 8 } },
      { slot: "lunch", name: "Lentil and roast vegetable bowl", ingredients: ["lentils", "sweet potato", "greens", "olive oil"], allergens: [], nutrients: { calories: 510, protein: 24, carbs: 68, fat: 15, fiber: 16 } },
      { slot: "dinner", name: "Salmon, rice and greens", ingredients: ["salmon", "brown rice", "broccoli", "lemon"], allergens: ["fish"], nutrients: { calories: 590, protein: 38, carbs: 56, fat: 22, fiber: 7 } },
    ],
    vegetarian: [
      { slot: "breakfast", name: "Pear yogurt and oats", ingredients: ["yogurt", "oats", "pear", "pumpkin seeds"], allergens: ["milk"], nutrients: { calories: 390, protein: 21, carbs: 55, fat: 11, fiber: 9 } },
      { slot: "lunch", name: "Chickpea garden bowl", ingredients: ["chickpeas", "cucumber", "tomato", "brown rice"], allergens: [], nutrients: { calories: 485, protein: 20, carbs: 72, fat: 12, fiber: 14 } },
      { slot: "dinner", name: "Tofu and vegetable stir-fry", ingredients: ["tofu", "rice", "pepper", "snow peas"], allergens: ["soy"], nutrients: { calories: 530, protein: 27, carbs: 69, fat: 16, fiber: 11 } },
    ],
    vegan: [
      { slot: "breakfast", name: "Banana chia oats", ingredients: ["oats", "banana", "chia", "oat drink"], allergens: [], nutrients: { calories: 375, protein: 13, carbs: 62, fat: 10, fiber: 12 } },
      { slot: "lunch", name: "Lentil and roast vegetable bowl", ingredients: ["lentils", "sweet potato", "greens", "olive oil"], allergens: [], nutrients: { calories: 510, protein: 24, carbs: 68, fat: 15, fiber: 16 } },
      { slot: "dinner", name: "Tofu and vegetable stir-fry", ingredients: ["tofu", "rice", "pepper", "snow peas"], allergens: ["soy"], nutrients: { calories: 530, protein: 27, carbs: 69, fat: 16, fiber: 11 } },
    ],
    pescatarian: [
      { slot: "breakfast", name: "Apple cinnamon oats", ingredients: ["oats", "apple", "milk", "cinnamon"], allergens: ["milk"], nutrients: { calories: 360, protein: 15, carbs: 58, fat: 8, fiber: 8 } },
      { slot: "lunch", name: "Chickpea garden bowl", ingredients: ["chickpeas", "cucumber", "tomato", "brown rice"], allergens: [], nutrients: { calories: 485, protein: 20, carbs: 72, fat: 12, fiber: 14 } },
      { slot: "dinner", name: "Salmon, rice and greens", ingredients: ["salmon", "brown rice", "broccoli", "lemon"], allergens: ["fish"], nutrients: { calories: 590, protein: 38, carbs: 56, fat: 22, fiber: 7 } },
    ],
  };
  const state = {
    preview: false,
    profile: null,
    plan: null,
    goals: [],
    mode: "plan",
    currentWeek: mondayOf(today),
    wizardStep: 0,
    wizard: { dietStyle: "omnivore", allergies: [], goals: ["more variety"] },
    addingMeal: false,
    editingMealId: "",
    draggingMealId: "",
    celebratoryGoalId: "",
  };
  const previewPlan = {
    id: "preview-plan",
    weekOf: "2026-09-28",
    preferences: { dietStyle: "vegetarian", allergies: ["Peanuts"], goals: ["More variety"] },
    meals: dayNames.slice(0, 5).flatMap((day, index) => mealTemplates.vegetarian.map((meal, mealIndex) => ({ ...meal, id: `preview-${day}-${mealIndex}`, day, allergens: meal.allergens, ingredients: [...meal.ingredients], nutrients: { ...meal.nutrients }, name: index === 2 && mealIndex === 1 ? "Peanut noodle bowl" : meal.name, allergens: index === 2 && mealIndex === 1 ? ["Peanuts"] : meal.allergens }))),
  };

  function mondayOf(date) {
    const copy = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const offset = (copy.getDay() + 6) % 7;
    copy.setDate(copy.getDate() - offset);
    return copy;
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  function request(path, options = {}) {
    const url = new URL(path, window.location.origin);
    const activeProfileId = window.HealthPages?.activeProfile()?.id;
    if (activeProfileId) url.searchParams.set("profile_id", activeProfileId);
    return fetch(`${url.pathname}${url.search}${url.hash}`, { credentials: "same-origin", cache: "no-store", ...options, headers: { "Content-Type": "application/json", ...options.headers } }).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) {
        if (!state.preview) window.location.replace("/login.html?reason=expired");
        throw new Error("Your session ended. Sign in again to continue.");
      }
      if (!response.ok) throw new Error(data.error || "That request could not be completed.");
      return data;
    });
  }

  function profile() {
    return state.profile || window.HealthPages.activeProfile();
  }

  function pageHeader(kicker, title, copy = "") {
    return `<header class="page-header"><p class="page-kicker">${kicker}</p><h1 class="page-title">${title}</h1>${copy ? `<p class="page-lede">${copy}</p>` : ""}</header>`;
  }

  function goalMeta(type) {
    return {
      steps: { unit: "steps", target: 8000, max: 50000, icon: "activity", accent: "fern", placeholder: "6400" },
      water: { unit: "ml", target: 2000, max: 10000, icon: "droplet", accent: "lake", placeholder: "1500" },
      sleep: { unit: "minutes", target: 480, max: 1440, icon: "moon", accent: "coral", placeholder: "450" },
      weight: { unit: "kg", target: 70, max: 500, icon: "sliders", accent: "butter", placeholder: "68.5" },
    }[type];
  }

  function mealWarnings(meal) {
    const avoid = (state.plan?.preferences?.allergies || []).map(value => value.toLowerCase());
    return meal.allergens.filter(allergen => avoid.some(item => item === allergen.toLowerCase() || item.includes(allergen.toLowerCase()) || allergen.toLowerCase().includes(item)));
  }

  function nutrientTotals(meals) {
    return meals.reduce((totals, meal) => {
      for (const nutrient of ["calories", "protein", "carbs", "fat", "fiber"]) totals[nutrient] += Number(meal.nutrients?.[nutrient] || 0);
      return totals;
    }, { calories: 0, protein: 0, carbs: 0, fat: 0, fiber: 0 });
  }

  function generateMeals() {
    const templates = mealTemplates[state.wizard.dietStyle] || mealTemplates.omnivore;
    return dayNames.flatMap((day, dayIndex) => templates.map((template, index) => ({ ...structuredClone(template), id: crypto.randomUUID(), day, slot: slots[index] || template.slot })));
  }

  function renderWizard() {
    const allergies = profile()?.allergies || [];
    if (state.wizardStep === 0) {
      return `<section class="diet-wizard"><div class="wellness-step-indicator"><span class="step-count">01 / 02</span><span>Preferences</span></div><h2>What feels good to plan around?</h2><p>Choose a starting point. You can change meals later; this is not medical advice.</p><form data-diet-wizard class="diet-wizard-form"><div class="health-field"><label for="diet-style">Eating style</label><select class="health-input" id="diet-style" name="dietStyle"><option value="omnivore" ${state.wizard.dietStyle === "omnivore" ? "selected" : ""}>A little of everything</option><option value="vegetarian" ${state.wizard.dietStyle === "vegetarian" ? "selected" : ""}>Vegetarian</option><option value="vegan" ${state.wizard.dietStyle === "vegan" ? "selected" : ""}>Vegan</option><option value="pescatarian" ${state.wizard.dietStyle === "pescatarian" ? "selected" : ""}>Pescatarian</option></select></div><div class="wellness-choice-row"><label><input type="checkbox" name="goals" value="more variety" checked><span>More variety</span></label><label><input type="checkbox" name="goals" value="easier prep"><span>Easier prep</span></label><label><input type="checkbox" name="goals" value="family meals"><span>Family meals</span></label></div><div class="wellness-form-actions"><span></span><button class="primary-action" type="submit">Next / Allergies</button></div></form></section>`;
    }
    return `<section class="diet-wizard"><div class="wellness-step-indicator"><span class="step-count">02 / 02</span><span>Allergies &amp; preferences</span></div><h2>What should the plan keep in mind?</h2><p>Known profile allergies are prefilled. Review them carefully; ingredient warnings are prompts to double-check, not a guarantee of safety.</p><form data-diet-wizard class="diet-wizard-form"><div class="health-field"><label for="diet-allergies">Allergies and ingredients to avoid</label><textarea class="health-input" id="diet-allergies" name="allergies" rows="4">${escapeHtml(state.wizard.allergies.length ? state.wizard.allergies.join(", ") : allergies.join(", "))}</textarea><p class="field-hint">Separate items with commas. Check labels and preparation details.</p></div><div class="wellness-form-actions"><button type="button" class="secondary-action" data-diet-back>Back</button><button class="primary-action" type="submit">Build weekly plan</button></div></form></section>`;
  }

  function totalsMarkup(meals) {
    const totals = nutrientTotals(meals);
    return `<div class="nutrient-strip" aria-label="Estimated weekly nutrient totals"><div><strong>${Math.round(totals.calories).toLocaleString()}</strong><span>kcal / week</span></div><div><strong>${Math.round(totals.protein)}g</strong><span>protein</span></div><div><strong>${Math.round(totals.carbs)}g</strong><span>carbs</span></div><div><strong>${Math.round(totals.fat)}g</strong><span>fat</span></div><div><strong>${Math.round(totals.fiber)}g</strong><span>fiber</span></div></div>`;
  }

  function mealMoveOptions(meal) {
    return dayNames.flatMap(day => slots.map(slot => `<option value="${day}|${slot}" ${day === meal.day && slot === meal.slot ? "selected" : ""}>${day} / ${slotLabels[slot]}</option>`)).join("");
  }

  function renderMealCard(meal) {
    const warnings = mealWarnings(meal);
    const nutrient = meal.nutrients || {};
    const editing = state.editingMealId === meal.id;
    return `<article class="meal-card${warnings.length ? " has-allergen-warning" : ""}" draggable="true" data-meal-card data-meal-id="${escapeHtml(meal.id)}" aria-label="${escapeHtml(meal.slot)}: ${escapeHtml(meal.name)}">
      ${editing ? `<form class="meal-edit-form" data-meal-edit="${escapeHtml(meal.id)}"><label class="sr-only" for="edit-meal-name">Meal name</label><input class="health-input" id="edit-meal-name" name="name" value="${escapeHtml(meal.name)}" required maxlength="120"><label class="sr-only" for="edit-meal-allergens">Allergens, comma separated</label><input class="health-input" id="edit-meal-allergens" name="allergens" value="${escapeHtml(meal.allergens.join(", "))}" placeholder="Allergens, comma separated"><div class="meal-edit-actions"><button type="button" class="text-action" data-cancel-meal>Edit cancelled</button><button type="submit" class="secondary-action">Save</button></div></form>` : `<div class="meal-card-head"><span class="meal-slot">${escapeHtml(slotLabels[meal.slot] || meal.slot)}</span><button class="icon-button meal-edit-button" type="button" data-edit-meal="${escapeHtml(meal.id)}" aria-label="Edit ${escapeHtml(meal.name)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#edit"></use></svg></button></div><strong class="meal-name">${escapeHtml(meal.name)}</strong><p class="meal-ingredients">${meal.ingredients.map(escapeHtml).join(" / ")}</p>${warnings.length ? `<p class="allergen-warning" role="alert"><svg class="icon" aria-hidden="true"><use href="icons.svg#alert-triangle"></use></svg>Check allergen: ${warnings.map(escapeHtml).join(", ")}</p>` : ""}<div class="meal-nutrient-mini"><span>${Math.round(nutrient.calories || 0)} kcal</span><span>${Math.round(nutrient.protein || 0)}g protein</span></div><label class="meal-move-label"><span class="sr-only">Move ${escapeHtml(meal.name)} to another day or meal</span><select class="meal-move-select" data-move-meal="${escapeHtml(meal.id)}">${mealMoveOptions(meal)}</select></label><button class="meal-remove" type="button" data-remove-meal="${escapeHtml(meal.id)}">Remove meal</button>`}
    </article>`;
  }

  function renderWeeklyBoard() {
    const meals = state.plan?.meals || [];
    const columns = dayNames.map(day => `<section class="meal-day-column" data-day-column="${day}"><h3>${day}</h3><div class="meal-day-list">${meals.filter(meal => meal.day === day).sort((left, right) => slots.indexOf(left.slot) - slots.indexOf(right.slot)).map(renderMealCard).join("") || '<p class="empty-meal-day">Drop or move a meal here.</p>'}</div></section>`).join("");
    const weekEnd = new Date(`${state.plan.weekOf}T00:00:00`);
    weekEnd.setDate(weekEnd.getDate() + 6);
    const dateRange = `${new Date(`${state.plan.weekOf}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${weekEnd.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
    return `<div class="weekly-plan-toolbar"><div><p class="page-kicker">Weekly plan / ${dateRange}</p><h2>${escapeHtml(state.plan.preferences?.dietStyle || "Flexible")} / ${meals.length} meals</h2></div><div class="weekly-plan-actions"><button class="icon-button" type="button" data-week-shift="-1" aria-label="Previous week"><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-left"></use></svg></button><button class="icon-button" type="button" data-week-shift="1" aria-label="Next week"><svg class="icon" aria-hidden="true"><use href="icons.svg#chevron-right"></use></svg></button><button class="secondary-action" type="button" data-edit-preferences>Edit preferences</button><button class="secondary-action" type="button" data-add-meal>Add a meal</button></div></div>${totalsMarkup(meals)}<p class="nutrient-disclaimer">Nutrition totals are estimates from the meal cards, not dietary or medical guidance.</p><div class="meal-board-help">Drag a meal to another day, or use the move menu on its card. On touch, the menu is the direct move control.</div><div class="weekly-meal-board" aria-label="Weekly meal plan">${columns}</div>${state.addingMeal ? `<section class="wellness-add-meal"><h3>Add a meal</h3><form data-meal-create class="meal-create-form"><label class="health-field"><span>Meal</span><input class="health-input" name="name" required maxlength="120" placeholder="Vegetable bowl"></label><label class="health-field"><span>Day</span><select class="health-input" name="day">${dayNames.map(day => `<option>${day}</option>`).join("")}</select></label><label class="health-field"><span>Slot</span><select class="health-input" name="slot">${slots.map(slot => `<option value="${slot}">${slotLabels[slot]}</option>`).join("")}</select></label><label class="health-field"><span>Calories estimate</span><input class="health-input" type="number" name="calories" min="0" max="5000" value="350"></label><label class="health-field"><span>Allergens</span><input class="health-input" name="allergens" placeholder="Separate with commas"></label><div class="meal-create-actions"><button type="button" class="secondary-action" data-cancel-add-meal>Cancel</button><button class="primary-action" type="submit">Save meal</button></div></form></section>` : ""}`;
  }

  function buildWeeklyPlan() {
    const templates = mealTemplates[state.wizard.dietStyle] || mealTemplates.omnivore;
    return dayNames.flatMap((day, dayIndex) => templates.map((template, index) => ({ ...structuredClone(template), id: crypto.randomUUID(), day, slot: slots[index] || template.slot })));
  }

  function renderPlanView() {
    if (!state.plan) return renderWizard();
    return renderWeeklyBoard();
  }

  function goalUnit(type) {
    return { steps: "steps", water: "ml", sleep: "minutes", weight: "kg" }[type] || "";
  }

  function goalStreak(goal) {
    let cursor = new Date();
    const history = goal.history || {};
    let streak = 0;
    if (!history[dateKey(cursor)]) cursor.setDate(cursor.getDate() - 1);
    for (let count = 0; count < 366; count += 1) {
      if (!history[dateKey(cursor)]) break;
      streak += 1;
      cursor.setDate(cursor.getDate() - 1);
    }
    return streak;
  }

  function goalRing(goal) {
    const meta = goalMeta(goal.type);
    const history = goal.history || {};
    const logged = Number(history[dateKey(today)] || 0);
    const progress = Math.min(100, Math.round(logged / goal.target * 100));
    const circumference = 150.8;
    const offset = circumference * (1 - progress / 100);
    return `<svg class="goal-ring ${meta.accent}" viewBox="0 0 52 52" role="img" aria-label="${escapeHtml(goal.title)}: ${progress}%"><circle class="ring-track" cx="26" cy="26" r="24"/><circle class="ring-value" cx="26" cy="26" r="24" style="stroke-dasharray:${circumference};stroke-dashoffset:${offset}"/><text x="26" y="27">${progress}</text></svg>`;
  }

  function renderGoals() {
    const cards = state.goals.map(goal => {
      const meta = goalMeta(goal.type);
      const todayValue = Number(goal.history?.[dateKey(today)] || 0);
      const streak = goalStreak(goal);
      const milestone = [30, 14, 7, 3].find(value => streak >= value);
      return `<article class="wellness-goal-card ${meta.accent}${state.celebratoryGoalId === goal.id ? " goal-celebrate" : ""}"><div class="goal-card-top"><span class="goal-kind-icon"><svg class="icon" aria-hidden="true"><use href="icons.svg#${meta.icon}"></use></svg></span><button type="button" class="goal-delete" aria-label="Remove ${escapeHtml(goal.title)}" data-delete-goal="${escapeHtml(goal.id)}"><svg class="icon" aria-hidden="true"><use href="icons.svg#close"></use></svg></button></div><div class="goal-card-main">${goalRing(goal)}<div><h3>${escapeHtml(goal.title)}</h3><p>${todayValue} / ${goal.target} ${meta.unit} today</p><span class="goal-streak">${streak ? `🔥 ${streak}-day streak` : "A fresh start today"}</span>${milestone ? `<span class="goal-medal ${milestone >= 14 ? "coral-medal" : "butter-medal"}" role="status">${milestone >= 14 ? "Coral" : "Butter"} / ${milestone}-day badge</span>` : ""}</div></div><form class="goal-log-form" data-goal-log="${escapeHtml(goal.id)}"><label class="sr-only" for="goal-value-${escapeHtml(goal.id)}">Today's ${meta.unit} for ${escapeHtml(goal.title)}</label><input class="health-input" id="goal-value-${escapeHtml(goal.id)}" name="value" type="number" min="0" max="${meta.max}" step="${goal.type === "weight" ? "0.1" : "1"}" value="${todayValue || ""}" placeholder="${meta.placeholder}" required><span>${meta.unit}</span><button type="submit" class="secondary-action">Log today</button></form></article>`;
    }).join("");
    return `<div class="goals-heading"><div><p class="page-kicker">Small steps / Your rhythm</p><h2>Wellness goals</h2><p>Track what matters to you, without streak pressure or a perfect-day score.</p></div></div><div class="wellness-goal-grid">${cards || '<div class="goals-empty"><h3>Choose a small goal to begin.</h3><p>Steps, water, sleep, or weight are private to this profile.</p></div>'}</div><section class="wellness-add-goal"><h3>Add a goal</h3><form data-goal-create class="goal-create-form"><label class="health-field"><span>Focus</span><select class="health-input" name="type"><option value="steps">Steps</option><option value="water">Water</option><option value="sleep">Sleep</option><option value="weight">Weight</option></select></label><label class="health-field"><span>Goal name</span><input class="health-input" name="title" maxlength="100" required placeholder="A gentle daily target"></label><label class="health-field"><span>Daily target</span><input class="health-input" type="number" name="target" min="1" value="8000" required></label><button class="primary-action" type="submit">Add goal</button></form></section>`;
  }

  function renderDiet() {
    const profile = state.profile || window.HealthPages.activeProfile();
    if (!profile) return `${pageHeader("Food &amp; wellbeing", "Add a profile first.", "Planning and goals are saved to a family health profile.")}<a class="primary-action" href="#/onboarding">Add profile</a>`;
    const tabs = `<div class="wellness-tabs" role="tablist" aria-label="Diet and wellness views"><button type="button" role="tab" aria-selected="${state.mode === "plan"}" data-wellness-mode="plan">Weekly plan</button><button type="button" role="tab" aria-selected="${state.mode === "goals"}" data-wellness-mode="goals">Wellness goals</button></div>`;
    const content = state.mode === "goals" ? renderGoals() : renderPlanView();
    return `<div class="wellness-page">${pageHeader(`Food &amp; wellbeing / ${escapeHtml(profile.name)}`, "A plan that fits real life.", "Build a flexible week, keep allergies visible, and track gentle goals at your own pace.")}${tabs}<div id="wellness-content">${content}</div></div>`;
  }

  async function loadPlan() {
    if (state.preview) { state.plan = previewPlan; return; }
    const profile = state.profile || window.HealthPages.activeProfile();
    if (!profile) return;
    const data = await request(`/api/diet/plan?profileId=${encodeURIComponent(profile.id)}&weekOf=${dateKey(state.currentWeek)}`);
    state.plan = data.plan;
  }

  async function loadGoals() {
    if (state.preview) { state.goals = [{ id: "preview-steps", profileId: state.profile.id, type: "steps", title: "A little more movement", target: 8000, history: { [dateKey(today)]: 6200 } }, { id: "preview-water", profileId: state.profile.id, type: "water", title: "Keep water nearby", target: 2000, history: { [dateKey(today)]: 1500 } }]; return; }
    const profile = state.profile || window.HealthPages.activeProfile();
    if (!profile) return;
    const data = await request(`/api/diet/goals?profileId=${encodeURIComponent(profile.id)}`);
    state.goals = data.goals || [];
  }

  async function refreshView() {
    const profile = window.HealthPages.activeProfile();
    if (!profile) return;
    state.profile = profile;
    const root = document.querySelector("#wellness-content");
    if (!root) return;
    root.innerHTML = '<div class="wellness-loading" role="status">Updating your plan...</div>';
    try {
      if (state.mode === "plan") await loadPlan(); else await loadGoals();
      root.innerHTML = state.mode === "plan" ? renderPlanView() : renderGoals();
    } catch (error) {
      root.innerHTML = `<div class="timeline-empty"><h2>Wellness data could not load.</h2><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  async function savePlan() {
    if (state.preview) { showToast("Create an account to save a meal plan.", "error"); return; }
    const payload = { profileId: profile().id, weekOf: dateKey(state.currentWeek), preferences: state.plan.preferences, meals: state.plan.meals };
    const result = await request(state.plan.id ? `/api/diet/plans/${encodeURIComponent(state.plan.id)}` : "/api/diet/plans", { method: state.plan.id ? "PUT" : "POST", body: JSON.stringify(payload) });
    state.plan = result.plan;
    return result.plan;
  }

  function renderWizard() {
    const active = profile();
    if (!active) return '<p class="wellness-loading">Select a health profile to make a plan.</p>';
    if (state.wizardStep === 0) {
      return `<section class="diet-wizard"><div class="wellness-step-indicator"><span class="step-count">01 / 02</span><span>Preferences</span></div><h2>What feels good to plan around?</h2><p>Choose a starting point. You can change any meal later.</p><form data-diet-wizard class="diet-wizard-form"><div class="health-field"><label for="diet-style">Eating style</label><select class="health-input" id="diet-style" name="dietStyle"><option value="omnivore" ${state.wizard.dietStyle === "omnivore" ? "selected" : ""}>A little of everything</option><option value="vegetarian" ${state.wizard.dietStyle === "vegetarian" ? "selected" : ""}>Vegetarian</option><option value="vegan" ${state.wizard.dietStyle === "vegan" ? "selected" : ""}>Vegan</option><option value="pescatarian" ${state.wizard.dietStyle === "pescatarian" ? "selected" : ""}>Pescatarian</option></select></div><div class="wellness-choice-row"><label><input type="checkbox" name="goals" value="more variety" checked><span>More variety</span></label><label><input type="checkbox" name="goals" value="easier prep"><span>Easier prep</span></label><label><input type="checkbox" name="goals" value="family meals"><span>Family meals</span></label></div><div class="wellness-form-actions"><span></span><button class="primary-action" type="submit">Next / Allergies</button></div></form></section>`;
    }
    return `<section class="diet-wizard"><div class="wellness-step-indicator"><span class="step-count">02 / 02</span><span>Allergies &amp; goals</span></div><h2>What should the plan keep in mind?</h2><p>Known profile allergies are prefilled. Review them; matching ingredients get a warning, not a safety guarantee.</p><form data-diet-wizard class="diet-wizard-form"><div class="health-field"><label for="diet-allergies">Allergies and ingredients to avoid</label><textarea class="health-input" id="diet-allergies" name="allergies" rows="4">${escapeHtml(state.wizard.allergies.length ? state.wizard.allergies.join(", ") : (active.allergies || []).join(", "))}</textarea><p class="field-hint">Separate items with commas. Check labels and preparation details.</p></div><div class="wellness-form-actions"><button type="button" class="secondary-action" data-diet-back>Back</button><button class="primary-action" type="submit">Build weekly plan</button></div></form></section>`;
  }

  function showToast(message, kind = "success") {
    const region = document.querySelector("#health-toasts");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `shell-toast${kind === "error" ? " error" : ""}`;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");
    toast.textContent = message;
    region.append(toast);
    window.setTimeout(() => toast.remove(), 3600);
  }

  function renderWellnessContent() {
    const root = document.querySelector("#wellness-content");
    if (root) root.innerHTML = state.mode === "plan" ? renderPlanView() : renderGoals();
  }

  function updateMeal(mealId, transform) {
    state.plan.meals = state.plan.meals.map(meal => meal.id === mealId ? transform(meal) : meal);
    renderWellnessContent();
    void savePlan().then(renderWellnessContent).catch(error => showToast(error.message, "error"));
  }

  async function createPlan() {
    state.plan = {
      id: "",
      weekOf: dateKey(state.currentWeek),
      preferences: { dietStyle: state.wizard.dietStyle, allergies: state.wizard.allergies, goals: state.wizard.goals },
      meals: buildWeeklyPlan(),
    };
    try {
      await savePlan();
      renderWellnessContent();
      showToast("Your week is ready to shape.");
    } catch (error) {
      state.plan = null;
      renderWellnessContent();
      showToast(error.message, "error");
    }
  }

  document.addEventListener("submit", event => {
    const wizard = event.target.closest("[data-diet-wizard]");
    if (wizard) {
      event.preventDefault();
      const values = new FormData(wizard);
      if (state.wizardStep === 0) {
        state.wizard.dietStyle = String(values.get("dietStyle") || "omnivore");
        state.wizard.goals = values.getAll("goals").map(String);
        state.wizardStep = 1;
        renderWellnessContent();
      } else {
        state.wizard.allergies = String(values.get("allergies") || "").split(/[\n,]/).map(value => value.trim()).filter(Boolean);
        void createPlan();
      }
      return;
    }
    const editMeal = event.target.closest("[data-meal-edit]");
    if (editMeal) {
      event.preventDefault();
      const mealId = editMeal.dataset.mealEdit;
      const values = new FormData(editMeal);
      updateMeal(mealId, meal => ({ ...meal, name: String(values.get("name") || "").trim(), allergens: String(values.get("allergens") || "").split(",").map(item => item.trim()).filter(Boolean) }));
      state.editingMealId = "";
      return;
    }
    const createMeal = event.target.closest("[data-meal-create]");
    if (createMeal) {
      event.preventDefault();
      const values = new FormData(createMeal);
      const calories = Number(values.get("calories") || 0);
      state.plan.meals.push({ id: crypto.randomUUID(), day: String(values.get("day")), slot: String(values.get("slot")), name: String(values.get("name")).trim(), ingredients: [], allergens: String(values.get("allergens") || "").split(",").map(item => item.trim()).filter(Boolean), nutrients: { calories, protein: 0, carbs: 0, fat: 0, fiber: 0 } });
      state.addingMeal = false;
      renderWellnessContent();
      void savePlan().then(renderWellnessContent).catch(error => showToast(error.message, "error"));
      return;
    }
    const createGoal = event.target.closest("[data-goal-create]");
    if (createGoal) {
      event.preventDefault();
      const values = new FormData(createGoal);
      void request("/api/diet/goals", { method: "POST", body: JSON.stringify({ profileId: profile().id, type: values.get("type"), title: values.get("title"), target: values.get("target") }) })
        .then(async () => { await loadGoals(); renderWellnessContent(); showToast("Wellness goal added."); })
        .catch(error => showToast(error.message, "error"));
      return;
    }
    const logGoal = event.target.closest("[data-goal-log]");
    if (logGoal) {
      event.preventDefault();
      const values = new FormData(logGoal);
      const goalId = logGoal.dataset.goalLog;
      void request(`/api/diet/goals/${encodeURIComponent(goalId)}/log`, { method: "PUT", body: JSON.stringify({ date: dateKey(today), value: values.get("value") }) })
        .then(async () => {
          state.celebratoryGoalId = goalId;
          await loadGoals();
          renderWellnessContent();
          window.setTimeout(() => { state.celebratoryGoalId = ""; }, 850);
          showToast("Logged gently. Nice work showing up.");
        })
        .catch(error => showToast(error.message, "error"));
    }
  });

  document.addEventListener("click", event => {
    if (event.target.closest("[data-wellness-mode]")) {
      state.mode = event.target.closest("[data-wellness-mode]").dataset.wellnessMode;
      void (state.mode === "goals" ? loadGoals() : loadPlan()).then(renderWellnessContent).catch(error => showToast(error.message, "error"));
      return;
    }
    if (event.target.closest("[data-diet-back]")) {
      state.wizardStep = 0;
      renderWellnessContent();
      return;
    }
    if (event.target.closest("[data-add-meal]")) { state.addingMeal = true; renderWellnessContent(); return; }
    if (event.target.closest("[data-cancel-add-meal]")) { state.addingMeal = false; renderWellnessContent(); return; }
    if (event.target.closest("[data-edit-meal]")) { state.editingMealId = event.target.closest("[data-edit-meal]").dataset.editMeal; renderWellnessContent(); return; }
    if (event.target.closest("[data-cancel-meal]")) { state.editingMealId = ""; renderWellnessContent(); return; }
    if (event.target.closest("[data-remove-meal]")) {
      const mealId = event.target.closest("[data-remove-meal]").dataset.removeMeal;
      state.plan.meals = state.plan.meals.filter(meal => meal.id !== mealId);
      renderWellnessContent();
      void savePlan().then(renderWellnessContent).catch(error => showToast(error.message, "error"));
      return;
    }
    if (event.target.closest("[data-edit-preferences]")) {
      state.wizardStep = 0;
      state.wizard = { ...state.wizard, dietStyle: state.plan.preferences?.dietStyle || "omnivore", allergies: state.plan.preferences?.allergies || [] };
      state.plan = null;
      renderWellnessContent();
      return;
    }
    const shift = event.target.closest("[data-week-shift]");
    if (shift) {
      state.currentWeek = new Date(state.currentWeek.getFullYear(), state.currentWeek.getMonth(), state.currentWeek.getDate() + Number(shift.dataset.weekShift) * 7);
      state.plan = null;
      void loadPlan().then(renderWellnessContent).catch(error => showToast(error.message, "error"));
      return;
    }
    const deleteGoal = event.target.closest("[data-delete-goal]");
    if (deleteGoal) {
      void request(`/api/diet/goals/${encodeURIComponent(deleteGoal.dataset.deleteGoal)}`, { method: "DELETE" }).then(async () => { await loadGoals(); renderWellnessContent(); }).catch(error => showToast(error.message, "error"));
    }
  });

  document.addEventListener("change", event => {
    if (event.target.matches("[data-move-meal]")) {
      const [day, slot] = event.target.value.split("|");
      updateMeal(event.target.dataset.moveMeal, meal => ({ ...meal, day, slot }));
      return;
    }
    const typeSelect = event.target.closest('[data-goal-create] select[name="type"]');
    if (typeSelect) {
      const meta = goalMeta(typeSelect.value);
      const input = typeSelect.form.elements.namedItem("target");
      input.value = meta.target;
      input.max = meta.max;
    }
  });

  document.addEventListener("dragstart", event => {
    const card = event.target.closest("[data-meal-card]");
    if (!card || state.editingMealId) return;
    state.draggingMealId = card.dataset.mealId;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", state.draggingMealId);
  });
  document.addEventListener("dragend", event => { event.target.closest("[data-meal-card]")?.classList.remove("is-dragging"); state.draggingMealId = ""; });
  document.addEventListener("dragover", event => { if (event.target.closest("[data-day-column]")) event.preventDefault(); });
  document.addEventListener("drop", event => {
    const column = event.target.closest("[data-day-column]");
    if (!column) return;
    event.preventDefault();
    const mealId = event.dataTransfer.getData("text/plain") || state.draggingMealId;
    if (mealId) updateMeal(mealId, meal => ({ ...meal, day: column.dataset.dayColumn }));
  });

  async function afterRender(route) {
    if (route === "/diet") {
      state.profile = window.HealthPages.activeProfile();
      if (state.mode === "plan") await loadPlan().catch(() => {}); else await loadGoals().catch(() => {});
      const root = document.querySelector("#wellness-content");
      if (root) root.innerHTML = state.mode === "plan" ? renderPlanView() : renderGoals();
    }
  }

  // Interactions are delegated because the planner replaces its board after each saved edit.
  window.WellnessPages = { renderDiet, afterRender, bootstrap(preview) { state.preview = preview; if (preview) state.profile = { id: "preview-riya", name: "Riya Patel", allergies: [] }; } };
})();
