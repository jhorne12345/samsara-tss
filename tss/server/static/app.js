// TSS dashboard — polls /api/fleet/status every second and renders.
//
// Diff-friendly DOM updates: tiles are keyed by agent id, queue items by job
// id. We rebuild children of the relevant containers on each tick rather than
// patching individual fields — at this fleet size (≤ ~50 agents) the cost is
// trivial and the code is much simpler than a virtual-DOM library.

(() => {
  const POLL_INTERVAL_MS = 1000;
  const els = {
    statIdle: document.getElementById("stat-idle"),
    statBusy: document.getElementById("stat-busy"),
    statOffline: document.getElementById("stat-offline"),
    statQueue: document.getElementById("stat-queue"),
    statRunning: document.getElementById("stat-running"),
    statCompleted: document.getElementById("stat-completed"),
    statFailed: document.getElementById("stat-failed"),
    agentsMeta: document.getElementById("agents-meta"),
    agentGrid: document.getElementById("agent-grid"),
    queueList: document.getElementById("queue-list"),
    queueMeta: document.getElementById("queue-meta"),
    runningList: document.getElementById("running-list"),
    eventsList: document.getElementById("events-list"),
    pollStatus: document.getElementById("poll-status"),
  };

  const state = {
    lastFetchAt: null,
  };

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
  }

  function setPollStatus(kind, msg) {
    els.pollStatus.classList.toggle("error", kind === "error");
    els.pollStatus.textContent = msg;
  }

  function render(data) {
    renderStats(data.stats);
    renderAgents(data.agents);
    renderJobs(data.queue, data.running_jobs);
    renderEvents(data.recent_events);
  }

  function renderStats(s) {
    els.statIdle.textContent = s.idle;
    els.statBusy.textContent = s.busy;
    els.statOffline.textContent = s.offline;
    els.statQueue.textContent = s.queue_depth;
    els.statRunning.textContent = s.jobs_running;
    els.statCompleted.textContent = s.jobs_completed;
    els.statFailed.textContent = s.jobs_failed;
    els.agentsMeta.textContent =
      s.total_agents === 1 ? "1 testbed" : `${s.total_agents} testbeds`;
    els.queueMeta.textContent =
      s.queue_depth === 1 ? "1 job" : `${s.queue_depth} jobs`;
  }

  function renderAgents(agents) {
    if (!agents.length) {
      // empty state already in DOM from the initial HTML; only re-render if
      // we previously had content.
      if (els.agentGrid.dataset.populated === "true") {
        els.agentGrid.dataset.populated = "false";
        els.agentGrid.innerHTML = `
          <div class="empty-state">
            <img src="/static/img/owl-192.png" alt="" class="empty-mark">
            <p>No testbeds registered yet.</p>
            <p class="empty-hint">Run <code>tss agent --name vg-01 --caps vehicle_gateway</code></p>
          </div>`;
      }
      return;
    }
    els.agentGrid.dataset.populated = "true";
    const tiles = agents
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(renderAgentTile);
    els.agentGrid.replaceChildren(...tiles);
  }

  function renderAgentTile(a) {
    const tile = document.createElement("div");
    tile.className = `agent-tile status-${a.status}`;
    tile.dataset.agentId = a.id;

    const nameRow = document.createElement("div");
    nameRow.className = "agent-row";
    nameRow.innerHTML = `
      <span class="agent-name">${escapeHtml(a.name)}</span>
      <span class="agent-status status-${a.status}">${a.status}</span>`;
    tile.appendChild(nameRow);

    const caps = document.createElement("div");
    caps.className = "agent-caps";
    for (const c of a.capabilities) {
      const b = document.createElement("span");
      b.className = "cap-badge";
      b.textContent = c.replace(/_/g, " ");
      caps.appendChild(b);
    }
    tile.appendChild(caps);

    const meta = document.createElement("div");
    meta.className = "agent-meta";
    const lastHb = relativeTime(new Date(a.last_heartbeat_at));
    const job = a.current_job_id ? a.current_job_id.slice(0, 8) : "—";
    meta.innerHTML = `
      <div class="agent-meta-row"><span>job</span><span>${escapeHtml(job)}</span></div>
      <div class="agent-meta-row"><span>epoch</span><span>${a.epoch}</span></div>
      <div class="agent-meta-row"><span>heartbeat</span><span>${escapeHtml(lastHb)}</span></div>`;
    tile.appendChild(meta);

    if (a.status === "busy") {
      // Approximate progress: we don't have job duration on the agent record,
      // so just show an indeterminate bar. The exact progress lives on the job.
      const progress = document.createElement("div");
      progress.className = "progress";
      const bar = document.createElement("div");
      bar.className = "progress-bar";
      bar.style.width = "60%";
      progress.appendChild(bar);
      tile.appendChild(progress);
    }

    const kill = document.createElement("button");
    kill.className = "kill-button";
    kill.textContent = a.status === "offline" ? "offline" : "kill (demo)";
    kill.disabled = a.status === "offline";
    kill.addEventListener("click", () => killAgent(a.id, kill));
    tile.appendChild(kill);

    return tile;
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

  function renderJobs(queue, running) {
    renderJobList(els.queueList, queue, "queue is empty");
    renderJobList(els.runningList, running, "no jobs running");
  }

  function renderJobList(container, jobs, emptyMsg) {
    if (!jobs.length) {
      container.replaceChildren(emptyDiv(emptyMsg));
      return;
    }
    const items = jobs
      .slice()
      .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
      .map(renderJobItem);
    container.replaceChildren(...items);
  }

  function renderJobItem(j) {
    const item = document.createElement("div");
    item.className = `queue-item status-${j.status}`;
    item.dataset.jobId = j.id;

    const bar = document.createElement("div");
    bar.className = "queue-item-bar";
    item.appendChild(bar);

    const info = document.createElement("div");
    info.className = "queue-item-info";
    const created = relativeTime(new Date(j.created_at));
    const detail = j.status === "running"
      ? `started ${j.started_at ? relativeTime(new Date(j.started_at)) : "—"} · ${j.duration_seconds.toFixed(1)}s declared`
      : `submitted ${created} · ${j.duration_seconds.toFixed(1)}s`;
    info.innerHTML = `
      <span class="queue-item-product">${escapeHtml(j.product.replace(/_/g, " "))}</span>
      <span class="queue-item-detail">${escapeHtml(j.id.slice(0, 8))} · ${escapeHtml(detail)}</span>`;
    item.appendChild(info);

    const attempts = document.createElement("span");
    attempts.className =
      "queue-item-attempts" + (j.attempt_count >= 2 ? " warning" : "");
    attempts.textContent = `${j.attempt_count}/${j.max_attempts}`;
    item.appendChild(attempts);

    return item;
  }

  function renderEvents(events) {
    if (!events.length) {
      els.eventsList.replaceChildren(emptyLi("waiting for events…"));
      return;
    }
    const items = events.slice(0, 30).map(renderEventItem);
    els.eventsList.replaceChildren(...items);
  }

  function renderEventItem(e) {
    const li = document.createElement("li");
    const t = new Date(e.at);
    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = formatClock(t);
    li.appendChild(time);

    const line = document.createElement("span");
    line.className = "event-line";
    const kind = document.createElement("span");
    kind.className = `event-kind kind-${e.kind}`;
    kind.textContent = String(e.kind).replace(/_/g, " ");
    line.appendChild(kind);
    line.appendChild(
      document.createTextNode(
        `${e.product || ""}${e.agent_name ? " · " + e.agent_name : ""}`
      )
    );
    li.appendChild(line);

    if (e.detail) {
      const detail = document.createElement("span");
      detail.className = "event-detail";
      detail.textContent = e.detail;
      li.appendChild(detail);
    }
    return li;
  }

  // ----- helpers -----

  function emptyDiv(msg) {
    const d = document.createElement("div");
    d.className = "empty-state-small";
    d.textContent = msg;
    return d;
  }

  function emptyLi(msg) {
    const li = document.createElement("li");
    li.className = "empty-state-small";
    li.textContent = msg;
    return li;
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
