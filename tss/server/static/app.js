// TSS dashboard — polls /api/fleet/status every second and renders.
//
// Diff-friendly DOM updates: tiles are keyed by agent id, queue items by job
// id. We rebuild children of the relevant containers on each tick rather than
// patching individual fields — at this fleet size (≤ ~50 agents) the cost is
// trivial and the code is much simpler than a virtual-DOM library.

(() => {
  // ---- Identity (submitter) management ----
  const STORAGE_KEY = "tss.submitter";

  function loadSubmitter() {
    try {
      return localStorage.getItem(STORAGE_KEY) || "";
    } catch (e) {
      return "";
    }
  }

  function saveSubmitter(name) {
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch (e) {
      /* localStorage disabled — accept and continue */
    }
    refreshIdentityUI();
    requestNotifyPermission();
  }

  function refreshIdentityUI() {
    const banner = document.getElementById("identity-banner");
    const pill = document.getElementById("identity-pill");
    const nameEl = document.getElementById("identity-name");
    const submitter = loadSubmitter();
    if (submitter) {
      banner.hidden = true;
      pill.hidden = false;
      if (nameEl) nameEl.textContent = submitter;
    } else {
      banner.hidden = false;
      pill.hidden = true;
    }
  }

  // Wire up identity UI on DOM ready
  document.addEventListener("DOMContentLoaded", () => {
    refreshIdentityUI();
    if (loadSubmitter()) requestNotifyPermission();
    const saveBtn = document.getElementById("identity-save");
    const input = document.getElementById("identity-input");
    if (saveBtn && input) {
      saveBtn.addEventListener("click", () => {
        const value = input.value.trim();
        if (value) saveSubmitter(value);
      });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") saveBtn.click();
      });
    }
    const changeBtn = document.getElementById("identity-change");
    if (changeBtn) {
      changeBtn.addEventListener("click", () => {
        const name = prompt("Update your name:", loadSubmitter());
        if (name !== null) saveSubmitter(name.trim());
      });
    }
    const mineToggle = document.getElementById("mine-toggle");
    if (mineToggle) {
      mineToggle.checked = !!loadSubmitter();
      mineToggle.addEventListener("change", () => {
        if (window.__lastFleetData) render(window.__lastFleetData);
      });
    }
    const closeBtn = document.getElementById("job-panel-close");
    if (closeBtn) closeBtn.addEventListener("click", closeJobPanel);
    const closeAgentBtn = document.getElementById("agent-panel-close");
    if (closeAgentBtn) closeAgentBtn.addEventListener("click", closeAgentPanel);
    const eventAgentSel = document.getElementById("event-agent-filter");
    if (eventAgentSel) {
      eventAgentSel.addEventListener("change", () => {
        if (window.__lastFleetData) renderEvents(window.__lastFleetData.recent_events);
      });
    }
    const jobStatusSel = document.getElementById("job-status-filter");
    if (jobStatusSel) {
      jobStatusSel.addEventListener("change", () => {
        if (window.__lastFleetData) renderJobsTable(window.__lastFleetData);
      });
    }
    document.querySelectorAll(".tss-table-jobs th.sortable").forEach((th) => {
      th.addEventListener("click", () => cycleJobSort(th.dataset.sort));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeJobPanel(); closeAgentPanel(); }
    });
  });

  // ---- Mine filter ----
  function isMineOn() {
    const t = document.getElementById("mine-toggle");
    return t ? t.checked : false;
  }

  function normalizeSubmitter(s) {
    return (s || "").trim().toLowerCase();
  }

  function isMine(submitter) {
    const mine = normalizeSubmitter(loadSubmitter());
    if (!mine) return false;
    return normalizeSubmitter(submitter) === mine;
  }

  function applySubmitterFilter(jobs) {
    if (!isMineOn()) return jobs;
    if (!normalizeSubmitter(loadSubmitter())) return jobs;
    return jobs.filter((j) => isMine(j.submitter));
  }

  // ---- Job detail panel ----
  const panel = () => document.getElementById("job-panel");

  async function openJobPanel(jobId) {
    try {
      const resp = await fetch(`/api/jobs/${jobId}`);
      if (!resp.ok) throw new Error(`status ${resp.status}`);
      const job = await resp.json();
      renderJobPanel(job);
      const p = panel();
      if (p) p.hidden = false;
    } catch (e) {
      console.error("failed to load job", jobId, e);
    }
  }

  function closeJobPanel() {
    const p = panel();
    if (p) p.hidden = true;
  }

  // ---- Agent detail panel ----
  const agentPanel = () => document.getElementById("agent-panel");

  async function openAgentPanel(agentId) {
    try {
      const resp = await fetch(`/api/agents/${agentId}/history`);
      if (!resp.ok) throw new Error(`status ${resp.status}`);
      const data = await resp.json();
      renderAgentPanel(data);
      const p = agentPanel();
      if (p) p.hidden = false;
    } catch (e) {
      console.error("failed to load agent", agentId, e);
    }
  }

  function closeAgentPanel() {
    const p = agentPanel();
    if (p) p.hidden = true;
  }

  function renderAgentPanel(data) {
    const a = data.agent;
    document.getElementById("agent-panel-title").textContent = `Agent ${a.name}`;
    document.getElementById("agent-panel-id").textContent = a.id;
    document.getElementById("agent-panel-caps").textContent = a.capabilities.join(", ");
    document.getElementById("agent-panel-status").textContent = a.status;
    document.getElementById("agent-panel-epoch").textContent = a.epoch;
    document.getElementById("agent-panel-job").textContent = a.current_job_id || "—";
    document.getElementById("agent-panel-registered").textContent =
      new Date(a.registered_at).toLocaleString();
    document.getElementById("agent-panel-heartbeat").textContent =
      relativeTime(new Date(a.last_heartbeat_at));
    const killBtn = document.getElementById("agent-panel-kill");
    if (killBtn) {
      killBtn.disabled = a.status === "offline";
      killBtn.textContent = a.status === "offline" ? "offline" : "kill (demo)";
      killBtn.onclick = () => killAgent(a.id, killBtn);
    }
    const events = document.getElementById("agent-panel-events");
    events.innerHTML = "";
    if (!data.events.length) {
      const li = document.createElement("li");
      li.className = "empty-state-small";
      li.textContent = "no events yet";
      events.appendChild(li);
      return;
    }
    for (const e of data.events) {
      const li = document.createElement("li");
      const at = new Date(e.at).toLocaleTimeString();
      const tail = e.detail ? ` — ${e.detail}` : "";
      li.textContent = `${at}  ${e.kind} · job ${e.job_id.slice(0, 8)} · ${e.product}${tail}`;
      li.style.cursor = "pointer";
      li.addEventListener("click", () => {
        closeAgentPanel();
        openJobPanel(e.job_id);
      });
      events.appendChild(li);
    }
  }

  function renderJobPanel(job) {
    document.getElementById("job-panel-title").textContent = `Job ${job.id.slice(0, 8)}`;
    document.getElementById("job-panel-id").textContent = job.id;
    document.getElementById("job-panel-product").textContent = job.product;
    document.getElementById("job-panel-status").textContent = job.status;
    document.getElementById("job-panel-submitter").textContent = job.submitter || "—";
    document.getElementById("job-panel-attempts").textContent =
      `${job.attempt_count} of ${job.max_attempts}`;
    document.getElementById("job-panel-agent").textContent =
      job.assigned_agent_id || "—";
    const events = document.getElementById("job-panel-events");
    events.innerHTML = "";
    for (const e of (job.history || [])) {
      const li = document.createElement("li");
      const at = new Date(e.at).toLocaleTimeString();
      const tail = e.detail ? ` — ${e.detail}` : "";
      const agent = e.agent_name ? ` [${e.agent_name}]` : "";
      li.textContent = `${at}  ${e.kind}${agent}${tail}`;
      events.appendChild(li);
    }
    document.getElementById("job-panel-raw").textContent =
      JSON.stringify(job, null, 2);
  }

  // ---- Completion notifications ----
  let notifyPermission = "default";
  const lastJobStatusById = new Map(); // jobId -> previous status

  function requestNotifyPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      Notification.requestPermission().then((p) => { notifyPermission = p; });
    } else {
      notifyPermission = Notification.permission;
    }
  }

  function notifyTerminal(job) {
    if (!isMine(job.submitter)) return;
    const body = job.status === "completed"
      ? `Job ${job.id.slice(0, 8)} completed`
      : `Job ${job.id.slice(0, 8)} failed`;
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("TSS", { body });
    } else {
      flashBanner(body);
    }
  }

  function flashBanner(message) {
    const el = document.createElement("div");
    el.className = "flash-banner";
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function checkForCompletions(allJobs) {
    for (const job of allJobs) {
      const prev = lastJobStatusById.get(job.id);
      if (prev && prev !== job.status && (job.status === "completed" || job.status === "failed")) {
        notifyTerminal(job);
      }
      lastJobStatusById.set(job.id, job.status);
    }
  }

  async function pollForCompletions() {
    const submitter = loadSubmitter();
    if (!submitter) return;
    try {
      const resp = await fetch(`/api/jobs?submitter=${encodeURIComponent(submitter)}`);
      if (!resp.ok) return;
      const jobs = await resp.json();
      checkForCompletions(jobs);
    } catch (e) {
      /* swallow — best-effort */
    }
  }

  const POLL_INTERVAL_MS = 1000;
  const els = {
    pulseDots: document.getElementById("pulse-dots"),
    pulseQueue: document.getElementById("pulse-queue"),
    pulseRunning: document.getElementById("pulse-running"),
    pulseDone: document.getElementById("pulse-done"),
    pulseFailed: document.getElementById("pulse-failed"),
    pulseOffline: document.getElementById("pulse-offline"),
    jobsList: document.getElementById("jobs-list"),
    jobsMeta: document.getElementById("jobs-meta"),
    eventsList: document.getElementById("events-list"),
    pollStatus: document.getElementById("poll-status"),
  };

  const state = {
    lastFetchAt: null,
    jobSort: null, // { col, dir } or null = use smart default
  };

  const JOB_SORTERS = {
    id:      (a, b) => a.id.localeCompare(b.id),
    product: (a, b) => a.product.localeCompare(b.product),
    status:  (a, b) => a.status.localeCompare(b.status),
    when: (a, b) => {
      const ta = new Date(a.completed_at || a.started_at || a.created_at);
      const tb = new Date(b.completed_at || b.started_at || b.created_at);
      return ta - tb;
    },
  };

  function cycleJobSort(col) {
    const cur = state.jobSort;
    if (!cur || cur.col !== col) {
      state.jobSort = { col, dir: "asc" };
    } else if (cur.dir === "asc") {
      state.jobSort = { col, dir: "desc" };
    } else {
      state.jobSort = null;
    }
    if (window.__lastFleetData) renderJobsTable(window.__lastFleetData);
    refreshSortIndicators();
  }

  function refreshSortIndicators() {
    document.querySelectorAll(".tss-table-jobs th.sortable").forEach((th) => {
      th.classList.remove("sort-asc", "sort-desc");
      if (state.jobSort && th.dataset.sort === state.jobSort.col) {
        th.classList.add(state.jobSort.dir === "asc" ? "sort-asc" : "sort-desc");
      }
    });
  }

  async function tick() {
    try {
      const res = await fetch("/api/fleet/status", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      render(data);
      state.lastFetchAt = Date.now();
      setPollStatus("ok", `updated ${formatClock(new Date())}`);
    } catch (e) {
      setPollStatus("error", `dispatcher unreachable — ${e.message}`);
    }
    pollForCompletions();
  }

  function setPollStatus(kind, msg) {
    els.pollStatus.classList.toggle("error", kind === "error");
    els.pollStatus.textContent = msg;
  }

  function render(data) {
    window.__lastFleetData = data;
    syncEventAgentOptions(data.agents);
    renderPulse(data);
    renderJobsTable(data);
    renderEvents(data.recent_events);
  }

  function renderPulse(data) {
    const s = data.stats;
    // Dots — one per agent, sorted by name, status-coloured, click → slide-in.
    const sorted = data.agents.slice().sort((a, b) => a.name.localeCompare(b.name));
    const dots = sorted.map((a) => {
      const dot = document.createElement("span");
      dot.className = `pulse-dot status-${a.status}`;
      dot.title = `${a.name} · ${a.status}`;
      dot.dataset.agentId = a.id;
      dot.addEventListener("click", () => openAgentPanel(a.id));
      return dot;
    });
    if (!dots.length) {
      const empty = document.createElement("span");
      empty.className = "timeline-empty";
      empty.textContent = "no testbeds registered";
      els.pulseDots.replaceChildren(empty);
    } else {
      els.pulseDots.replaceChildren(...dots);
    }

    // Numbers — queue/running reflect the Mine-only filter when on, so the
    // header doesn't disagree with the table below it.
    const queueLen   = applySubmitterFilter(data.queue).length;
    const runningLen = applySubmitterFilter(data.running_jobs).length;
    els.pulseQueue.textContent = queueLen;
    els.pulseRunning.textContent = runningLen;
    els.pulseDone.textContent = s.jobs_completed;
    els.pulseFailed.textContent = s.jobs_failed;
    els.pulseOffline.textContent = s.offline;

    els.pulseFailed.parentElement.classList.toggle("has-failures", s.jobs_failed > 0);
    els.pulseOffline.parentElement.classList.toggle("has-offline", s.offline > 0);
  }

  function renderJobsTable(data) {
    const all = [
      ...(data.queue || []),
      ...(data.running_jobs || []),
      ...(data.recent_completed || []),
    ];
    // Deduplicate by id (a job can appear in both queue/running across ticks).
    const byId = new Map();
    for (const j of all) byId.set(j.id, j);
    let jobs = Array.from(byId.values());

    // Filters
    jobs = applySubmitterFilter(jobs);
    const statusFilter = document.getElementById("job-status-filter")?.value || "";
    if (statusFilter) jobs = jobs.filter((j) => j.status === statusFilter);

    if (state.jobSort && JOB_SORTERS[state.jobSort.col]) {
      const cmp = JOB_SORTERS[state.jobSort.col];
      const sign = state.jobSort.dir === "asc" ? 1 : -1;
      jobs.sort((a, b) => sign * cmp(a, b));
    } else {
      // Smart default: running, queued, then most recent terminal.
      jobs.sort((a, b) => {
        const rank = (j) => ({ running: 0, queued: 1, completed: 2, failed: 2 }[j.status] ?? 3);
        const ra = rank(a), rb = rank(b);
        if (ra !== rb) return ra - rb;
        if (ra <= 1) return new Date(a.created_at) - new Date(b.created_at);
        return new Date(b.completed_at || b.created_at) - new Date(a.completed_at || a.created_at);
      });
    }

    if (els.jobsMeta) {
      els.jobsMeta.textContent = jobs.length === 1 ? "1 job" : `${jobs.length} jobs`;
    }
    if (!jobs.length) {
      els.jobsList.replaceChildren(emptyRow("no jobs match", 5));
      return;
    }
    els.jobsList.replaceChildren(...jobs.map(renderJobRow));
  }

  function renderJobRow(j) {
    const tr = document.createElement("tr");
    tr.dataset.jobId = j.id;

    tr.appendChild(td(j.id.slice(0, 8), "cell-id"));
    tr.appendChild(td(j.product.replace(/_/g, " "), "cell-product"));

    const statusTd = document.createElement("td");
    statusTd.className = "cell-status";
    const pill = document.createElement("span");
    pill.className = `status-pill ${j.status}`;
    pill.textContent = j.status;
    statusTd.appendChild(pill);
    tr.appendChild(statusTd);

    const timelineTd = document.createElement("td");
    timelineTd.className = "cell-timeline";
    timelineTd.appendChild(buildTimeline(j));
    tr.appendChild(timelineTd);

    tr.appendChild(td(jobWhen(j), "cell-when"));

    tr.addEventListener("click", () => openJobPanel(j.id));
    return tr;
  }

  function jobWhen(j) {
    if (j.status === "completed" || j.status === "failed") {
      return `${j.status} ${j.completed_at ? relativeTime(new Date(j.completed_at)) : ""}`.trim();
    }
    if (j.status === "running") {
      return `started ${j.started_at ? relativeTime(new Date(j.started_at)) : "—"}`;
    }
    return `submitted ${relativeTime(new Date(j.created_at))}`;
  }

  // Distill a job's history into a one-line story of which agents touched it
  // and what happened on each. Reassignment is the interesting story —
  // dropped agents are styled struck-through; the final outcome is the
  // colored chip at the end.
  function buildTimeline(job) {
    const wrap = document.createElement("span");
    wrap.className = "timeline";
    const hist = job.history || [];

    // Walk history and build (agent_name, outcome) entries in claim order.
    const entries = []; // [{name, outcome}]
    let current = null;
    for (const e of hist) {
      if (e.kind === "claimed") {
        if (current) entries.push(current);
        current = { name: e.agent_name || "?", outcome: "running" };
      } else if (e.kind === "completed" && current) {
        current.outcome = "completed";
      } else if (e.kind === "failed" && current) {
        current.outcome = "failed";
      } else if (e.kind === "reassigned" && current) {
        current.outcome = "dropped";
      } else if (e.kind === "overrun" && current) {
        current.outcome = "dropped";
      }
    }
    if (current) entries.push(current);

    if (!entries.length) {
      const empty = document.createElement("span");
      empty.className = "timeline-empty";
      empty.textContent = job.status === "queued" ? "waiting for agent" : "—";
      wrap.appendChild(empty);
      return wrap;
    }

    entries.forEach((entry, i) => {
      if (i > 0) {
        const arrow = document.createElement("span");
        arrow.className = "timeline-arrow";
        arrow.textContent = "→";
        wrap.appendChild(arrow);
      }
      const chip = document.createElement("span");
      chip.className = `timeline-chip outcome-${entry.outcome}`;
      chip.textContent = entry.name;
      wrap.appendChild(chip);
    });
    return wrap;
  }


  async function killAgent(agentId, btn) {
    btn.disabled = true;
    btn.textContent = "killing…";
    try {
      const res = await fetch(`/api/agents/${agentId}/kill`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      btn.textContent = `error: ${e.message}`;
    }
    // Next tick will re-render.
  }


  function selectedEventAgentId() {
    const sel = document.getElementById("event-agent-filter");
    return sel && sel.value ? sel.value : "";
  }

  function applyEventFilter(events) {
    let out = events;
    if (isMineOn() && normalizeSubmitter(loadSubmitter())) {
      out = out.filter((e) => isMine(e.submitter));
    }
    const agentId = selectedEventAgentId();
    if (agentId) {
      out = out.filter((e) => e.agent_id === agentId);
    }
    return out;
  }

  function syncEventAgentOptions(agents) {
    const sel = document.getElementById("event-agent-filter");
    if (!sel) return;
    const current = sel.value;
    const sorted = agents.slice().sort((a, b) => a.name.localeCompare(b.name));
    const desiredKeys = sorted.map((a) => a.id).join(",");
    if (sel.dataset.keys === desiredKeys) return; // no change, don't rebuild
    sel.dataset.keys = desiredKeys;
    sel.innerHTML = "";
    const all = document.createElement("option");
    all.value = ""; all.textContent = "all";
    sel.appendChild(all);
    for (const a of sorted) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.name;
      sel.appendChild(opt);
    }
    if (current && sorted.some((a) => a.id === current)) sel.value = current;
  }

  function renderEvents(events) {
    const filtered = applyEventFilter(events);
    if (!filtered.length) {
      els.eventsList.replaceChildren(emptyRow("waiting for events…", 5));
      return;
    }
    const rows = filtered.slice(0, 30).map(renderEventRow);
    els.eventsList.replaceChildren(...rows);
  }

  function renderEventRow(e) {
    const tr = document.createElement("tr");
    tr.appendChild(td(formatClock(new Date(e.at)), "cell-when"));

    const kindTd = document.createElement("td");
    kindTd.className = "cell-kind";
    const pill = document.createElement("span");
    pill.className = `event-kind kind-${e.kind}`;
    pill.textContent = String(e.kind).replace(/_/g, " ");
    kindTd.appendChild(pill);
    tr.appendChild(kindTd);

    tr.appendChild(td(e.job_id ? e.job_id.slice(0, 8) : "—", "cell-id"));
    tr.appendChild(td(e.agent_name || "—", "cell-agent"));
    tr.appendChild(td(e.detail || "", "cell-detail"));

    if (e.job_id) {
      tr.addEventListener("click", () => openJobPanel(e.job_id));
    } else {
      tr.style.cursor = "default";
    }
    return tr;
  }

  // ----- helpers -----

  function td(text, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = text;
    return cell;
  }

  function emptyRow(msg, colspan) {
    const tr = document.createElement("tr");
    tr.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = colspan;
    cell.textContent = msg;
    tr.appendChild(cell);
    return tr;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function formatClock(d) {
    return d.toLocaleTimeString(undefined, {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function relativeTime(d) {
    const seconds = Math.floor((Date.now() - d.getTime()) / 1000);
    if (seconds < 2) return "now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ago`;
  }

  // ----- main loop -----
  tick();
  setInterval(tick, POLL_INTERVAL_MS);
})();
