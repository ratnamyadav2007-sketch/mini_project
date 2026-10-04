(() => {
  const metricLabels = {
    systolic_blood_pressure: "Systolic blood pressure",
    blood_glucose: "Blood glucose",
    body_temperature: "Body temperature",
  };
  const allowedActions = new Set(["#/sos", "#/appointments", "#/diet"]);
  const previewMode = new URLSearchParams(window.location.search).get("preview") === "1";
  let chart = null;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function header(kicker, title, copy = "") {
    return `<header class="page-header"><p class="page-kicker">${kicker}</p><h1 class="page-title">${title}</h1>${copy ? `<p class="page-lede">${copy}</p>` : ""}</header>`;
  }

  function profileChoices() {
    return window.HealthPages.memberProfiles().map(profile => ({
      id: profile.id,
      name: profile.name || profile.display_name || "Family member",
    }));
  }

  function renderInsights() {
    const profiles = profileChoices();
    const activeId = window.HealthPages.activeProfile()?.id;
    return `<div class="insights-page">${header("Patterns / Health insights", "See the shape of a trend.", "Reference ranges are context, not diagnoses. Only records you are authorized to see are returned.")}
      <form class="insights-controls surface surface-pad" data-insights-filter>
        <label class="health-field"><span>Metric</span><select class="health-input" name="metric">${Object.entries(metricLabels).map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label>
        <label class="health-field"><span>Range</span><select class="health-input" name="range"><option value="7d">7 days</option><option value="30d" selected>30 days</option><option value="90d">90 days</option><option value="1y">1 year</option><option value="all">All time</option></select></label>
        <fieldset class="insights-members"><legend>Members</legend>${profiles.map(profile => `<label><input type="checkbox" name="members" value="${escapeHtml(profile.id)}" ${profile.id === activeId ? "checked" : ""}> ${escapeHtml(profile.name)}</label>`).join("")}</fieldset>
        <button class="primary-action" type="submit">Update chart</button>
      </form>
      <div id="insights-result" class="surface surface-pad" aria-live="polite"><p>Loading chart data…</p></div></div>`;
  }

  function destroyChart() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
  }

  async function loadInsights(form) {
    const result = document.querySelector("#insights-result");
    if (!result) return;
    const values = new FormData(form);
    const members = values.getAll("members").map(String);
    if (!members.length) {
      result.innerHTML = '<p role="alert">Select at least one member.</p>';
      return;
    }
    result.innerHTML = '<p role="status">Loading authorized readings…</p>';
    destroyChart();
    try {
      const metric = String(values.get("metric"));
      const range = String(values.get("range"));
      let data;
      if (previewMode) {
        const end = new Date();
        const start = new Date(end);
        start.setDate(start.getDate() - (range === "7d" ? 6 : range === "90d" ? 89 : range === "1y" || range === "all" ? 364 : 29));
        const formatDate = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
        const activeId = window.HealthPages.activeProfile()?.id;
        const units = { systolic_blood_pressure: "mmHg", blood_glucose: "mg/dL", body_temperature: "°C" };
        const baseValue = metric === "body_temperature" ? 37 : metric === "blood_glucose" ? 102 : 122;
        data = {
          metric,
          start_date: formatDate(start),
          end_date: formatDate(end),
          series: members.map((id, index) => ({
            profile_id: id,
            member_label: id === activeId ? "You" : `Family member ${index}`,
            locked: id !== activeId,
            points: id === activeId ? [
              { recorded_at: `${formatDate(start)}T09:00:00Z`, value: baseValue + 14, unit: units[metric], range_flag: "above", reference_population: "Illustrative range", reference_note: null, reference_lower: baseValue - 32, reference_upper: baseValue },
              { recorded_at: `${formatDate(end)}T09:00:00Z`, value: baseValue, unit: units[metric], range_flag: "within", reference_population: "Illustrative range", reference_note: null, reference_lower: baseValue - 32, reference_upper: baseValue },
            ] : [],
            trend: "decreasing",
            improvement_delta: 0.5,
            improvement: "improved",
          })),
          disclaimer: "Preview data is illustrative only; reference ranges are context, not diagnoses.",
        };
      } else {
        const { apiGet } = await import("./js/api.js");
        const query = new URLSearchParams({ metric, range, members: members.join(",") });
        data = await apiGet(`/insights?${query}`);
      }
      const locked = data.series.filter(series => series.locked);
      result.innerHTML = `<p class="page-kicker">${escapeHtml(metricLabels[data.metric] || data.metric)} / ${escapeHtml(String(data.start_date || "All available"))} to ${escapeHtml(data.end_date)}</p>
        <canvas id="insights-chart" role="img" aria-label="${escapeHtml(metricLabels[data.metric] || data.metric)} chart"></canvas>
        ${locked.length ? `<section class="locked-series" aria-label="Restricted series"><h2>Sharing is restricted</h2><ul>${locked.map(item => `<li><strong>Locked</strong> — ${escapeHtml(item.member_label)}'s series is consent-locked.</li>`).join("")}</ul></section>` : ""}
        <div class="insight-trends">${data.series.filter(item => !item.locked).map(item => `<p><strong>${escapeHtml(item.member_label)}</strong>: ${escapeHtml(item.trend.replaceAll("_", " "))}; ${escapeHtml(item.improvement)}${item.improvement_delta == null ? "" : ` (delta ${escapeHtml(item.improvement_delta)})`}</p>`).join("")}</div>
        <p class="nutrient-disclaimer">${escapeHtml(data.disclaimer)}</p>`;
      const series = data.series.filter(item => !item.locked);
      const labels = [...new Set(series.flatMap(item => item.points.map(point => point.recorded_at.slice(0, 10))))].sort();
      const colors = ["#456c58", "#416c78", "#b94e3d", "#b37b14", "#786288"];
      const datasets = series.map((item, index) => ({
        label: item.member_label,
        data: labels.map(label => item.points.find(point => point.recorded_at.slice(0, 10) === label)?.value ?? null),
        borderColor: colors[index % colors.length],
        backgroundColor: colors[index % colors.length],
        tension: 0.25,
        spanGaps: false,
      }));
      const referencePoint = series.flatMap(item => item.points).find(point => point.reference_lower != null || point.reference_upper != null);
      if (referencePoint) {
        for (const [label, value] of [["Reference lower", referencePoint.reference_lower], ["Reference upper", referencePoint.reference_upper]]) {
          if (value == null) continue;
          datasets.push({
            label,
            data: labels.map(() => value),
            borderColor: "#8a8f88",
            borderDash: [5, 5],
            borderWidth: 1,
            pointRadius: 0,
          });
        }
      }
      const canvas = document.querySelector("#insights-chart");
      if (window.Chart && canvas) {
        chart = new window.Chart(canvas, {
          type: "line",
          data: { labels, datasets },
          options: {
            responsive: true,
            interaction: { mode: "index", intersect: false },
            scales: { y: { beginAtZero: false } },
            plugins: { legend: { position: "bottom" } },
          },
        });
      } else {
        result.insertAdjacentHTML("afterbegin", '<p role="alert">The chart library is unavailable. Reload this page to try again.</p>');
      }
    } catch (error) {
      result.innerHTML = `<p role="alert">Insights could not load: ${escapeHtml(error.message)}</p>`;
    }
  }

  function renderChat() {
    return `<div class="chat-page">${header("A thoughtful guide / Wellness chat", "What would you like to understand?", "Summaries use only authorized health data. This assistant does not dispatch emergency alerts.")}
      <section class="chat-panel surface surface-pad"><ol class="chat-messages" id="chat-messages" aria-live="polite"><li class="chat-message assistant">Ask about an authorized reading, an upcoming reminder, or general wellness education.</li></ol>
      <form class="chat-form" data-chat-form><label class="sr-only" for="chat-message">Message</label><textarea class="health-input" id="chat-message" name="message" rows="2" maxlength="1000" required placeholder="Type a question…"></textarea><button class="primary-action" type="submit">Send</button></form></section></div>`;
  }

  function addChatMessage(className, body, actions = []) {
    const list = document.querySelector("#chat-messages");
    if (!list) return;
    const item = document.createElement("li");
    item.className = `chat-message ${className}`;
    item.textContent = body;
    list.append(item);
    for (const action of actions) {
      if (!allowedActions.has(action.href)) continue;
      const link = document.createElement("a");
      link.className = "secondary-action chat-action";
      link.href = action.href;
      link.textContent = action.label;
      item.append(document.createElement("br"), link);
      if (action.description) {
        const description = document.createElement("small");
        description.textContent = action.description;
        item.append(document.createElement("br"), description);
      }
    }
    list.scrollTop = list.scrollHeight;
  }

  async function submitChat(form) {
    const message = String(new FormData(form).get("message") || "").trim();
    if (!message) return;
    const profile = window.HealthPages.activeProfile();
    if (!profile) {
      addChatMessage("assistant", "Select a profile before starting a chat.");
      return;
    }
    addChatMessage("user", message);
    form.reset();
    const typing = document.createElement("li");
    typing.className = "chat-message assistant chat-typing";
    typing.setAttribute("role", "status");
    typing.textContent = "Thinking…";
    document.querySelector("#chat-messages")?.append(typing);
    try {
      let reply;
      if (previewMode) {
        const emergency = /\b(chest pain|can't breathe|cannot breathe|emergency|stroke|overdose)\b/i.test(message);
        reply = {
          answer: emergency
            ? "If someone is in immediate danger, contact emergency services now. This chat cannot contact emergency services."
            : "Preview reply: I can summarize authorized readings, list reminders, or share general wellness education.",
          actions: emergency ? [{
            type: "navigation",
            label: "Open SOS",
            href: "#/sos",
            description: "Trigger SOS yourself.",
          }] : [],
        };
      } else {
        const { apiPost } = await import("./js/api.js");
        reply = await apiPost("/chat/messages", { profile_id: profile.id, message });
      }
      typing.remove();
      addChatMessage("assistant", reply.answer, reply.actions || []);
    } catch (error) {
      typing.remove();
      addChatMessage("assistant", `I couldn't send that message: ${error.message}`);
    }
  }

  document.addEventListener("submit", event => {
    const filter = event.target.closest("[data-insights-filter]");
    if (filter) {
      event.preventDefault();
      window.clearTimeout(insightsFilterTimer);
      void loadInsights(filter);
    }
    const form = event.target.closest("[data-chat-form]");
    if (form) {
      event.preventDefault();
      void submitChat(form);
    }
  });

  let insightsFilterTimer = 0;
  document.addEventListener("change", event => {
    const filter = event.target.closest("[data-insights-filter]");
    if (!filter) return;
    window.clearTimeout(insightsFilterTimer);
    insightsFilterTimer = window.setTimeout(() => void loadInsights(filter), 180);
  });

  async function afterRender(route) {
    if (route === "/insights") {
      const form = document.querySelector("[data-insights-filter]");
      if (form) await loadInsights(form);
    }
  }

  window.InsightsChatPages = { renderInsights, renderChat, afterRender };
})();
