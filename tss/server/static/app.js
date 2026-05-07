// TSS dashboard — polls /api/fleet/status every second and re-renders.
//
// Two role-scoped views — engineer and operator — toggled in the topbar.
// Engineer view centers on a single "my build" hero with a journey track;
// operator view centers on fleet health + queue throughput. Both share
// the slide-over job and agent detail panels.
//
// No build step. Plain ES2020+. Renders rebuild containers on each tick;
// slide-overs live outside the rebuilt subtrees so open panels and scroll
// state survive polls.

(() => {
  // ===== State =====

  const POLL_MS = 1000;
  const IDENTITY_KEY = "tss.submitter";
  const ROLE_KEY = "tss.role";
  const NOTIFIED_KEY = "tss.notified";

  /** @type {{role:'engineer'|'operator', identity:string|null, lastSnapshot:any, openPanel:{kind:'job'|'agent', id:string}|null}} */
  const state = {
    role: "engineer",
    identity: null,
    lastSnapshot: null,
    openPanel: null,
  };

  // ===== Storage helpers =====

  const loadIdentity = () => {
    try { return localStorage.getItem(IDENTITY_KEY) || ""; } catch { return ""; }
  };
  const saveIdentity = (name) => {
    try { localStorage.setItem(IDENTITY_KEY, name); } catch {}
    state.identity = name || null;
    refreshIdentityUI();
    requestNotifyPermission();
  };
  const loadRole = () => {
    try { return localStorage.getItem(ROLE_KEY) || "engineer"; } catch { return "engineer"; }
  };
  const saveRole = (role) => {
    try { localStorage.setItem(ROLE_KEY, role); } catch {}
  };

  // ===== DOM helpers =====

  /** Build an element. children may be strings, nodes, or null/undefined. */
  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null) continue;
      if (k === "class")    node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k === "dataset") for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = dv;
      else node.setAttribute(k, v);
    }
    appendChildren(node, children);
    return node;
  }
  function appendChildren(node, children) {
    if (children == null) return;
    if (!Array.isArray(children)) children = [children];
    for (const c of children) {
      if (c == null || c === false) continue;
      if (typeof c === "string" || typeof c === "number") node.appendChild(document.createTextNode(String(c)));
      else node.appendChild(c);
    }
  }
  const text = (s) => document.createTextNode(s == null ? "" : String(s));

  // ===== Time formatting =====

  /** ISO timestamp → "12s ago" / "3m 15s ago" / "47s ago". */
  function relTime(iso, nowMs = Date.now()) {
    if (!iso) return "—";
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return "—";
    const sec = Math.max(0, Math.floor((nowMs - t) / 1000));
    if (sec < 1) return "just now";
    if (sec < 60) return `${sec}s ago`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m < 60) return s > 0 ? `${m}m ${s}s ago` : `${m}m ago`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m ago`;
  }
  /** ISO timestamp → "14:54:31". */
  function hms(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toTimeString().slice(0, 8);
  }

  // ===== Status pill =====

  /** Capability abbreviation for fleet tiles. Title attr keeps full name on hover. */
  function abbreviateCap(c) {
    const map = { vehicle_gateway: "vg", asset_gateway: "ag", dashcam: "dc", driver_app: "da" };
    return map[c] || c.slice(0, 2);
  }

  function pill(status, label, opts = {}) {
    const cls = `pill ${status}` + (opts.extra ? ` ${opts.extra}` : "");
    return el("span", { class: cls }, [
      el("span", { class: "dot" + (opts.pulse ? " pulse" : "") }),
      label || status.replace(/_/g, " "),
    ]);
  }

  // ===== Identity (submitter) =====

  function refreshIdentityUI() {
    const banner = document.getElementById("identity-banner");
    const pill = document.getElementById("identity-pill");
    const nameEl = document.getElementById("identity-name");
    const id = loadIdentity();
    if (id) {
      banner.hidden = true;
      pill.hidden = false;
      if (nameEl) nameEl.textContent = id;
    } else {
      banner.hidden = false;
      pill.hidden = true;
    }
  }

  // ===== Role toggle =====

  function applyRole(role) {
    state.role = role;
    saveRole(role);
    const eng = document.getElementById("view-engineer");
    const ops = document.getElementById("view-operator");
    const tabE = document.getElementById("role-engineer");
    const tabO = document.getElementById("role-operator");
    if (role === "operator") {
      eng.hidden = true; ops.hidden = false;
      tabE.classList.remove("is-active"); tabE.setAttribute("aria-selected", "false");
      tabO.classList.add("is-active");    tabO.setAttribute("aria-selected", "true");
    } else {
      eng.hidden = false; ops.hidden = true;
      tabE.classList.add("is-active");    tabE.setAttribute("aria-selected", "true");
      tabO.classList.remove("is-active"); tabO.setAttribute("aria-selected", "false");
    }
    if (state.lastSnapshot) renderAll(state.lastSnapshot);
  }

  // ===== Polling =====

  async function poll() {
    const status = document.getElementById("poll-status");
    try {
      const r = await fetch("/api/fleet/status");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const snap = await r.json();
      state.lastSnapshot = snap;
      status.classList.remove("error");
      status.textContent = `live · ${snap.stats.total_agents} agents · ${snap.stats.queue_depth} queued`;
      renderAll(snap);
      maybeNotifyOnComplete(snap);
    } catch (e) {
      status.classList.add("error");
      status.textContent = `offline · ${e.message || e}`;
    }
  }

  // ===== Render orchestration =====

  function renderAll(snap) {
    if (state.role === "operator") renderOperator(snap);
    else renderEngineer(snap);
  }

  // ----- Section primitive -----
  function tssSection({ label, meta, aside }, body) {
    const head = el("header", { class: "tss-section-head" }, [
      el("div", { style: { display: "flex", alignItems: "baseline", gap: "12px" } }, [
        el("h2", {}, label),
        meta != null ? el("span", { class: "tss-section-meta" }, meta) : null,
      ]),
      aside ? el("div", { class: "tss-section-aside" }, aside) : null,
    ]);
    return el("section", { class: "tss-section" }, [head, body]);
  }

  // ===== ENGINEER VIEW =====

  function renderEngineer(snap) {
    const root = document.getElementById("view-engineer");
    root.replaceChildren();

    // The "my build" is the most recent job belonging to the identity.
    const id = loadIdentity();
    const allJobs = [
      ...(snap.running_jobs || []),
      ...(snap.queue || []),
      ...(snap.recent_completed || []),
    ];
    const my = id
      ? pickMyBuild(allJobs, id)
      : null;

    root.appendChild(buildHero(my, id));

    const grid = el("div", { class: "engineer-grid" }, [
      fleetSection(snap.agents || []),
      queueContext(snap.queue || [], my ? my.id : null),
    ]);
    root.appendChild(grid);

    root.appendChild(eventStrip(snap.recent_events || []));
  }

  function pickMyBuild(jobs, identity) {
    const mine = jobs.filter((j) => (j.submitter || "") === identity);
    if (mine.length === 0) return null;
    // Prefer running, then queued, then most-recent terminal.
    const order = { running: 0, queued: 1, completed: 2, failed: 2 };
    mine.sort((a, b) => {
      const oa = order[a.status] ?? 9;
      const ob = order[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      const ta = new Date(a.completed_at || a.started_at || a.created_at).getTime();
      const tb = new Date(b.completed_at || b.started_at || b.created_at).getTime();
      return tb - ta;
    });
    return mine[0];
  }

  function buildHero(job, identity) {
    const accentColor = (s) => {
      switch (s) {
        case "running":   return "var(--status-cool)";
        case "queued":    return "var(--text-mute)";
        case "completed": return "var(--status-live)";
        case "failed":    return "var(--status-offline)";
        default:          return "var(--text-mute)";
      }
    };

    if (!identity) {
      return el("section", { class: "hero" }, [
        el("div", { class: "hero-accent", style: { background: "var(--text-mute)" } }),
        el("div", { class: "hero-row" }, [
          el("div", { style: { flex: 1 } }, [
            el("div", { class: "hero-meta-row" }, [
              el("span", { class: "hero-eyebrow" }, "my build"),
              pill("queued", "no identity"),
            ]),
            el("h1", { class: "hero-headline" }, "tell us your name above"),
            el("p", { class: "hero-sub" }, "set your identity in the banner to track your builds. you'll see which testbed claimed your job and watch the journey live."),
          ]),
        ]),
      ]);
    }

    if (!job) {
      return el("section", { class: "hero" }, [
        el("div", { class: "hero-accent", style: { background: "var(--text-mute)" } }),
        el("div", { class: "hero-row" }, [
          el("div", { style: { flex: 1 } }, [
            el("div", { class: "hero-meta-row" }, [
              el("span", { class: "hero-eyebrow" }, "my build"),
              pill("queued", "nothing recent"),
            ]),
            el("h1", { class: "hero-headline" }, "no recent build"),
            el("p", { class: "hero-sub" }, `submit a job with submitter="${identity}" via the CLI or POST /api/jobs to start tracking it here.`),
          ]),
        ]),
      ]);
    }

    const status = job.status;
    let headline, sub;
    if (status === "running") {
      const ag = lastAgentName(job);
      headline = ag ? `running on ${ag}` : "running";
      const elapsed = job.started_at ? relTime(job.started_at) : "just claimed";
      sub = `attempt ${job.attempt_count} of ${job.max_attempts} · started ${elapsed}`;
    } else if (status === "queued" && (job.attempt_count || 0) === 0) {
      headline = `waiting for a ${job.product} testbed`;
      sub = "no compatible agent available yet · will claim as soon as one frees up";
    } else if (status === "queued") {
      headline = "reassigning";
      const reason = lastReassignReason(job);
      sub = reason || `attempt ${job.attempt_count} did not complete · re-queued for retry`;
    } else if (status === "completed") {
      headline = "passed";
      sub = `completed ${relTime(job.completed_at)}`;
    } else {
      headline = "failed";
      sub = lastFailReason(job) || `exhausted ${job.max_attempts} attempts`;
    }

    const branchCommit = (job.branch || job.commit)
      ? `${job.branch || ""}${job.branch && job.commit ? " · " : ""}${job.commit || ""}`
      : "";

    const right = el("div", { class: "hero-jobid-block" }, [
      el("span", { class: "hero-jobid-label" }, "job id"),
      el("button", { class: "hero-jobid mono", onclick: () => openJobPanel(job.id) }, shortId(job.id)),
      el("span", { class: "hero-submitted" }, `submitted ${relTime(job.created_at)}`),
    ]);

    const journeyEl = renderJourney(job);

    let progressEl = null;
    if (status === "running" && job.started_at && job.duration_seconds) {
      const elapsedSec = (Date.now() - new Date(job.started_at).getTime()) / 1000;
      const pct = Math.min(1, elapsedSec / job.duration_seconds);
      progressEl = el("div", { class: "build-progress-wrap" }, [
        el("div", { class: "build-progress-label" }, [
          el("span", {}, "elapsed"),
          el("span", {}, `${Math.floor(elapsedSec)}s of ${Math.floor(job.duration_seconds)}s`),
        ]),
        el("div", { class: "build-progress-bar" }, [
          el("span", { style: { width: `${pct * 100}%` } }),
        ]),
      ]);
    }

    return el("section", { class: "hero" }, [
      el("div", { class: "hero-accent", style: { background: accentColor(status) } }),
      el("div", { class: "hero-row" }, [
        el("div", { style: { flex: 1, minWidth: 0 } }, [
          el("div", { class: "hero-meta-row" }, [
            el("span", { class: "hero-eyebrow" }, "my build"),
            pill(status),
            branchCommit ? el("span", { class: "ref" }, branchCommit) : null,
          ]),
          el("h1", { class: "hero-headline" }, headline),
          el("p", { class: "hero-sub" }, sub),
        ]),
        right,
      ]),
      journeyEl,
      progressEl,
    ]);
  }

  /** Build a journey track of stops from the job's history.
   *  Pillar 2 (routing) + Pillar 3 (resiliency) + Pillar 4 (visibility). */
  function renderJourney(job) {
    const stops = [
      { kind: "submitted", label: "submitted", detail: hms(job.created_at) },
    ];
    for (const ev of job.history || []) {
      if (ev.kind === "claimed") {
        stops.push({
          kind: job.status === "running" && isLastAttempt(job, ev) ? "running" : "claimed",
          label: ev.detail && ev.detail.startsWith("attempt=") ? `attempt ${ev.detail.split("=")[1]}` : "claimed",
          agent: ev.agent_name, detail: hms(ev.at), pulse: job.status === "running" && isLastAttempt(job, ev),
        });
      } else if (ev.kind === "reassigned" || ev.kind === "overrun") {
        stops.push({
          kind: ev.kind, label: ev.kind, agent: ev.agent_name, detail: ev.detail || hms(ev.at),
        });
      } else if (ev.kind === "completed") {
        stops.push({
          kind: "completed", label: "completed", agent: ev.agent_name, detail: ev.detail || hms(ev.at),
        });
      } else if (ev.kind === "failed") {
        stops.push({
          kind: "failed", label: "failed", agent: ev.agent_name, detail: ev.detail || hms(ev.at),
        });
      }
    }
    if (job.status === "queued" && (job.attempt_count || 0) > 0) {
      stops.push({ kind: "queued", label: "next", detail: "waiting for compatible testbed" });
    } else if (job.status === "running") {
      stops.push({ kind: "queued", label: "pending", detail: "completed if exit 0 within declared duration" });
    }

    const cols = stops.length;
    const track = el("div", { class: "journey-track", style: { gridTemplateColumns: `repeat(${cols}, 1fr)` } });
    for (const s of stops) {
      const circle = el("span", { class: `journey-circle ${s.kind}${s.pulse ? " active" : ""}` }, [
        el("span", { class: "inner" }),
      ]);
      const agentEl = s.agent
        ? el("button", { class: "journey-agent mono", onclick: () => openAgentPanelByName(s.agent) }, s.agent)
        : el("span", { class: "journey-agent placeholder mono" }, s.label === "next" || s.label === "pending" ? "—" : "—");
      track.appendChild(el("div", { class: "journey-stop" }, [
        circle,
        el("div", { style: { display: "flex", flexDirection: "column", gap: "3px" } }, [
          el("span", { class: "journey-label" }, s.label),
          agentEl,
          el("span", { class: "journey-detail" }, s.detail || ""),
        ]),
      ]));
    }
    return el("div", { class: "journey" }, [
      el("div", { class: "journey-line" }),
      track,
    ]);
  }

  function isLastAttempt(job, ev) {
    // The most recent claim event in history.
    let last = null;
    for (const h of job.history || []) if (h.kind === "claimed") last = h;
    return last && last.at === ev.at;
  }

  function fleetSection(agents) {
    const stats = {
      idle: agents.filter(a => a.status === "idle").length,
      busy: agents.filter(a => a.status === "busy").length,
      offline: agents.filter(a => a.status === "offline").length,
    };
    const aside = [
      el("span", { style: { color: "var(--status-live)" } }, `● ${stats.idle} idle`),
      el("span", { style: { color: "var(--status-busy)" } }, `● ${stats.busy} busy`),
      el("span", { style: { color: "var(--status-offline)" } }, `● ${stats.offline} off`),
    ];
    const body = el("div", { style: { padding: "16px" } }, [
      el("div", { class: "fleet-grid" }, agents.map(renderFleetTile)),
    ]);
    return tssSection({ label: "fleet", meta: `${agents.length} testbeds`, aside }, body);
  }

  function renderFleetTile(agent) {
    const accentVar =
      agent.status === "idle"    ? "var(--status-live)"    :
      agent.status === "busy"    ? "var(--status-busy)"    :
                                   "var(--status-offline)";
    return el("button", {
      class: "fleet-tile",
      onclick: () => openAgentPanel(agent.id),
    }, [
      el("span", { class: "fleet-tile-accent", style: { background: accentVar } }),
      el("div", { class: "fleet-tile-row" }, [
        el("span", { class: "fleet-tile-name" }, agent.name),
        el("span", { class: `fleet-status-dot ${agent.status}` }),
      ]),
      el("div", { class: "fleet-tile-caps" }, (agent.capabilities || []).map(c =>
        el("span", { class: "fleet-cap-badge", title: c }, abbreviateCap(c))
      )),
      el("div", { class: "fleet-tile-foot" }, [
        el("span", {}, `e${agent.epoch}`),
        el("span", {}, `♥ ${relTime(agent.last_heartbeat_at)}`),
      ]),
    ]);
  }

  function queueContext(queue, myJobId) {
    const body = queue.length === 0
      ? el("div", { style: { padding: "8px 0" } }, [
          el("p", { class: "empty-queue" }, "queue is clear · your build will start as soon as you submit"),
        ])
      : el("div", { style: { padding: "8px 0" } }, queue.slice(0, 5).map((j, i) => renderQueueItem(j, i, myJobId)));
    return tssSection({ label: "queue", meta: queue.length === 0 ? "empty" : `${queue.length} waiting` }, body);
  }

  function renderQueueItem(job, i, myJobId) {
    return el("button", {
      class: "queue-item" + (job.id === myJobId ? " is-mine" : ""),
      onclick: () => openJobPanel(job.id),
    }, [
      el("span", { class: "num" }, String(i + 1)),
      el("div", { style: { display: "flex", flexDirection: "column", gap: "2px", minWidth: 0 } }, [
        el("span", { class: "product" }, job.product),
        el("span", { class: "meta" }, `by ${job.submitter || "unknown"} · ${relTime(job.created_at)}`),
      ]),
      el("span", { class: "ref" }, shortId(job.id)),
    ]);
  }

  // ===== OPERATOR VIEW =====

  function renderOperator(snap) {
    const root = document.getElementById("view-operator");
    root.replaceChildren();

    root.appendChild(fleetHero(snap));
    root.appendChild(fleetGridSection(snap.agents || []));
    root.appendChild(el("div", {
      style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" },
    }, [
      runningTable(snap.running_jobs || []),
      queueTable(snap.queue || []),
    ]));
    root.appendChild(eventStrip(snap.recent_events || []));
  }

  function fleetHero(snap) {
    const stats = snap.stats || {};
    const offline = stats.offline || 0;
    const failed = stats.jobs_failed || 0;
    let health, headline, color, blurb;
    if (offline === 0 && failed === 0) {
      health = "healthy"; headline = "all systems nominal";
      color = "var(--status-live)";
      blurb = `${stats.total_agents || 0} testbeds online · queue ${stats.queue_depth || 0}`;
    } else if (offline >= 2 || (snap.recent_events || []).filter(e => e.kind === "failed").length >= 2) {
      health = "degraded"; headline = "degraded";
      color = "var(--status-offline)";
      blurb = `${offline} offline · ${failed} failed total · queue ${stats.queue_depth || 0}`;
    } else {
      health = "watch"; headline = "watch";
      color = "var(--status-busy)";
      blurb = `${offline} offline · ${stats.busy || 0} busy · queue ${stats.queue_depth || 0}`;
    }

    const series = (stats.throughput_per_min && stats.throughput_per_min.length)
      ? stats.throughput_per_min : new Array(12).fill(0);

    const sparkCanvas = el("canvas", { width: 160, height: 28 });
    requestAnimationFrame(() => drawSparkline(sparkCanvas, series, color));

    const completed = stats.jobs_completed || 0;
    const kpiBlock = el("div", { class: "kpi-stack" }, [
      el("div", { class: "kpi-group" }, [
        el("span", { class: "kpi-group-label" }, "testbeds"),
        el("div", { class: "kpi-strip" }, [
          kpi("total",   stats.total_agents || 0),
          kpi("busy",    stats.busy || 0,    stats.busy ? "warn" : null),
          kpi("idle",    stats.idle || 0,    stats.idle ? "live" : null),
          kpi("offline", offline,            offline ? "fail" : null),
        ]),
      ]),
      el("div", { class: "kpi-group" }, [
        el("span", { class: "kpi-group-label" }, "jobs"),
        el("div", { class: "kpi-strip" }, [
          kpi("queue",   stats.queue_depth || 0),
          kpi("running", stats.jobs_running || 0),
          kpi("done",    completed,           completed ? "live" : null),
          kpi("failed",  failed,              failed ? "fail" : null),
          el("div", { class: "spark" }, [
            el("span", { class: "kpi-label" }, "throughput / min"),
            sparkCanvas,
            el("span", { class: "spark-foot" }, `last 12 min · ${series[series.length - 1]} /min`),
          ]),
        ]),
      ]),
    ]);

    return el("section", { class: "hero" }, [
      el("div", { class: "hero-accent", style: { background: color } }),
      el("div", { class: "hero-row" }, [
        el("div", {}, [
          el("span", { class: "hero-eyebrow" }, "fleet status"),
          el("h1", { class: "hero-headline" }, headline),
          el("p", { class: "hero-sub" }, blurb),
        ]),
        kpiBlock,
      ]),
    ]);
  }

  function kpi(label, value, accentClass = null) {
    return el("div", { class: "kpi" }, [
      el("span", { class: "kpi-label" }, label),
      el("span", { class: `kpi-value${accentClass ? " " + accentClass : ""}` }, String(value)),
    ]);
  }

  function drawSparkline(canvas, values, color) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width, h = canvas.height;
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    if (!values.length) return;
    const max = Math.max(...values, 1);
    const step = w / Math.max(values.length - 1, 1);
    const ys = values.map((v, i) => ({ x: i * step, y: h - (v / max) * (h - 2) - 1 }));

    // Filled area
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (const p of ys) ctx.lineTo(p.x, p.y);
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = colorWithAlpha(color, 0.12);
    ctx.fill();

    // Line
    ctx.beginPath();
    ys.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = resolveColor(color);
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }
  function resolveColor(c) {
    // CSS vars have to be resolved against the document.
    if (c.startsWith("var(")) {
      const name = c.slice(4, -1).trim();
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#5ce0d2";
    }
    return c;
  }
  function colorWithAlpha(c, a) {
    const hex = resolveColor(c).trim();
    if (!hex.startsWith("#") || hex.length !== 7) return hex;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }

  function fleetGridSection(agents) {
    const body = el("div", { style: { padding: "18px" } }, [
      el("div", { class: "fleet-grid" }, agents.map(renderFleetTile)),
    ]);
    return tssSection({
      label: "testbeds",
      meta: `${agents.length} registered`,
      aside: [el("span", {}, "click any tile")],
    }, body);
  }

  function runningTable(rows) {
    const body = rows.length === 0
      ? el("div", {}, [el("p", { class: "empty-row" }, "nothing running.")])
      : el("div", {}, rows.map(renderRunningRow));
    return tssSection({ label: "running", meta: `${rows.length} jobs` }, body);
  }

  function renderRunningRow(j) {
    const elapsed = j.started_at ? (Date.now() - new Date(j.started_at).getTime()) / 1000 : 0;
    const pct = Math.min(1, elapsed / Math.max(j.duration_seconds, 1));
    return el("div", { class: "row-grid running" }, [
      el("button", { class: "row-id", onclick: () => openJobPanel(j.id) }, shortId(j.id)),
      el("div", { style: { display: "flex", flexDirection: "column", gap: "4px", minWidth: 0 } }, [
        el("div", { style: { display: "flex", alignItems: "center", gap: "6px" } }, [
          el("span", { class: "row-product" }, j.product),
          (j.attempt_count || 0) > 1
            ? el("span", { class: "row-attempt-badge" }, `attempt ${j.attempt_count}/${j.max_attempts}`)
            : null,
        ]),
        el("div", { class: "row-progress" }, [el("span", { style: { width: `${pct * 100}%` } })]),
      ]),
      el("button", {
        class: "row-agent",
        onclick: () => j.assigned_agent_id && openAgentPanel(j.assigned_agent_id),
      }, j.assigned_agent_id ? `on ${assignedAgentName(j) || shortId(j.assigned_agent_id)}` : "—"),
      el("span", { class: "row-when" }, j.started_at ? relTime(j.started_at) : "—"),
    ]);
  }

  function queueTable(rows) {
    const body = rows.length === 0
      ? el("div", {}, [el("p", { class: "empty-row" }, "queue is clear.")])
      : el("div", {}, rows.map((j, i) => renderQueueRow(j, i)));
    return tssSection({ label: "queue", meta: `${rows.length} waiting` }, body);
  }

  function renderQueueRow(j, i) {
    return el("div", { class: "row-grid queue" }, [
      el("span", { class: "row-num" }, String(i + 1)),
      el("button", { class: "row-id", onclick: () => openJobPanel(j.id) }, shortId(j.id)),
      el("span", { class: "row-product" }, j.product),
      el("span", { class: "row-when" }, `by ${j.submitter || "unknown"}`),
      el("span", { class: "row-when" }, relTime(j.created_at)),
    ]);
  }

  // ===== Event strip (shared) =====

  function eventStrip(events) {
    const body = events.length === 0
      ? el("div", {}, [el("p", { class: "empty-row" }, "no events yet.")])
      : el("div", {}, events.map(renderEventRow));
    return tssSection({
      label: "events", meta: `${events.length} recent`,
      aside: [el("span", {}, "newest first")],
    }, body);
  }

  function renderEventRow(e) {
    return el("div", { class: "row-grid event" }, [
      el("span", { class: "row-when" }, hms(e.at)),
      pill(e.kind),
      e.job_id
        ? el("button", { class: "row-id", onclick: () => openJobPanel(e.job_id) }, shortId(e.job_id))
        : el("span", { class: "row-when" }, "—"),
      e.agent_id
        ? el("button", { class: "row-agent", onclick: () => openAgentPanel(e.agent_id) }, e.agent_name || shortId(e.agent_id))
        : el("span", { class: "row-when" }, "—"),
      el("span", { class: "row-detail", title: detailFor(e) }, detailFor(e)),
    ]);
  }
  function detailFor(e) {
    if (e.detail) return e.detail;
    if (e.kind === "submitted" && e.submitter) return `by ${e.submitter}`;
    return "";
  }

  // ===== Slide-over: job =====

  async function openJobPanel(jobId) {
    state.openPanel = { kind: "job", id: jobId };
    const panel = document.getElementById("job-panel");
    const titleEl = document.getElementById("job-panel-title");
    const body = document.getElementById("job-panel-body");
    titleEl.textContent = shortId(jobId);
    body.replaceChildren(el("p", {}, "loading…"));
    panel.hidden = false;
    panel.setAttribute("aria-hidden", "false");
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const job = await r.json();
      titleEl.textContent = job.id;
      body.replaceChildren(renderJobDetail(job));
    } catch (e) {
      body.replaceChildren(el("p", { class: "empty-row" }, `failed to load · ${e.message || e}`));
    }
  }

  function renderJobDetail(job) {
    const branchCommit = (job.branch || job.commit)
      ? `${job.product} · ${job.branch || ""}${job.branch && job.commit ? " · " : ""}${job.commit || ""}`
      : job.product;
    const head = el("div", { style: { display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" } }, [
      pill(job.status),
      el("span", { class: "mono", style: { fontSize: "12.5px", color: "var(--text-mute)" } }, branchCommit),
    ]);
    const meta = renderMetaGrid([
      ["submitter", job.submitter || "—"],
      ["submitted", hms(job.created_at)],
      ["declared",  `${job.duration_seconds}s`],
      ["actual",    durationActual(job)],
      ["exit code", lastExitCode(job)],
      ["attempts",  `${job.attempt_count} of ${job.max_attempts}`],
    ]);
    const attempts = renderAttemptTimeline(job);
    const prose = renderTSSDidProse(job);

    return el("div", { style: { display: "flex", flexDirection: "column", gap: "24px" } }, [
      head, meta,
      el("section", {}, [el("h3", {}, "attempt timeline"), attempts]),
      el("section", {}, [el("h3", {}, "what tss did, in order"), prose]),
    ]);
  }

  function renderMetaGrid(items) {
    return el("dl", { class: "meta-grid" }, items.map(([k, v]) =>
      el("div", {}, [el("dt", {}, k), el("dd", {}, String(v))])
    ));
  }

  function durationActual(job) {
    for (let i = (job.history || []).length - 1; i >= 0; i--) {
      const ev = job.history[i];
      if ((ev.kind === "completed" || ev.kind === "failed") && ev.detail) {
        const m = ev.detail.match(/duration=([\d.]+)s/);
        if (m) return `${m[1]}s`;
      }
    }
    return "—";
  }
  function lastExitCode(job) {
    for (let i = (job.history || []).length - 1; i >= 0; i--) {
      const ev = job.history[i];
      if ((ev.kind === "completed" || ev.kind === "failed") && ev.detail) {
        const m = ev.detail.match(/exit=(-?\d+)/);
        if (m) return m[1];
      }
    }
    return "—";
  }

  /** Group history events into per-attempt cards. An attempt opens at "claimed"
   *  and closes at the next "reassigned" / "completed" / "failed" / "overrun". */
  function renderAttemptTimeline(job) {
    const attempts = [];
    let cur = null;
    for (const ev of job.history || []) {
      if (ev.kind === "claimed") {
        cur = {
          n: attempts.length + 1,
          status: "running",
          agent: ev.agent_name,
          epoch: extractEpoch(ev) ?? "",
          started: hms(ev.at),
          ended: null,
          detail: ev.detail || "claimed",
        };
        attempts.push(cur);
      } else if (cur && (ev.kind === "completed" || ev.kind === "failed" ||
                         ev.kind === "reassigned" || ev.kind === "overrun")) {
        cur.status = ev.kind;
        cur.ended = hms(ev.at);
        cur.detail = ev.detail || ev.kind;
        cur = null;
      }
    }
    if (attempts.length === 0) return el("p", { class: "empty-row" }, "no attempts yet.");
    return el("ol", { class: "attempts" }, attempts.map(a => el("li", { class: "attempt" }, [
      el("span", { class: `attempt-num ${a.status}` }, String(a.n)),
      el("div", { class: "attempt-card" }, [
        el("div", { class: "attempt-head" }, [
          el("div", { class: "attempt-head-left" }, [
            pill(a.status),
            a.agent
              ? el("button", { class: "row-agent", onclick: () => openAgentPanelByName(a.agent) }, a.agent)
              : null,
            a.epoch !== "" ? el("span", { class: "row-when" }, `epoch ${a.epoch}`) : null,
          ]),
          el("span", { class: "attempt-times" }, `${a.started} → ${a.ended || "now"}`),
        ]),
        el("p", { class: "attempt-detail" }, a.detail),
      ]),
    ])));
  }
  function extractEpoch(ev) {
    if (!ev.detail) return null;
    const m = ev.detail.match(/epoch=(\d+)/);
    return m ? Number(m[1]) : null;
  }

  /** Plain-English description of routing + resiliency decisions for this job. */
  function renderTSSDidProse(job) {
    const lines = [];
    lines.push(el("li", {}, [text(`matched `), el("em", {}, job.product), text(` capability against ready agents`)]));
    let attempt = 0;
    for (const ev of job.history || []) {
      if (ev.kind === "claimed") {
        attempt += 1;
        lines.push(el("li", {}, `attempt ${attempt}: assigned to ${ev.agent_name || "an agent"} · recorded epoch with claim`));
      } else if (ev.kind === "reassigned") {
        lines.push(el("li", {}, `released stale claim · re-queued for retry${ev.detail ? ` · ${ev.detail}` : ""}`));
      } else if (ev.kind === "overrun") {
        lines.push(el("li", {}, `watchdog killed run for duration overrun · re-queued${ev.detail ? ` · ${ev.detail}` : ""}`));
      } else if (ev.kind === "stale_result_rejected") {
        lines.push(el("li", {}, `rejected late result from a previous incarnation${ev.detail ? ` · ${ev.detail}` : ""}`));
      } else if (ev.kind === "completed") {
        lines.push(el("li", {}, `accepted result · marked completed${ev.detail ? ` · ${ev.detail}` : ""}`));
      } else if (ev.kind === "failed") {
        lines.push(el("li", {}, `gave up · marked failed${ev.detail ? ` · ${ev.detail}` : ""}`));
      }
    }
    return el("ol", { class: "tss-prose" }, lines);
  }

  // ===== Slide-over: agent =====

  async function openAgentPanel(agentId) {
    state.openPanel = { kind: "agent", id: agentId };
    const panel = document.getElementById("agent-panel");
    const titleEl = document.getElementById("agent-panel-title");
    const body = document.getElementById("agent-panel-body");
    body.replaceChildren(el("p", {}, "loading…"));
    panel.hidden = false;
    panel.setAttribute("aria-hidden", "false");
    titleEl.textContent = shortId(agentId);
    try {
      // We piggyback on the snapshot for the Agent record (capabilities,
      // epoch_history, current status) and fetch /api/agents/{id}/history
      // for cross-job event stream.
      const agent = (state.lastSnapshot && (state.lastSnapshot.agents || []).find(a => a.id === agentId)) || null;
      if (!agent) throw new Error("agent not in current snapshot");
      titleEl.textContent = agent.name;
      const hist = await fetch(`/api/agents/${agentId}/history`).then(r => r.ok ? r.json() : { events: [] });
      body.replaceChildren(renderAgentDetail(agent, hist.events || []));
    } catch (e) {
      body.replaceChildren(el("p", { class: "empty-row" }, `failed to load · ${e.message || e}`));
    }
  }

  /** Click handler from journey/attempt cards: agent name, no id. */
  function openAgentPanelByName(name) {
    if (!state.lastSnapshot) return;
    const a = (state.lastSnapshot.agents || []).find(a => a.name === name);
    if (a) openAgentPanel(a.id);
  }

  function renderAgentDetail(agent, historyEvents) {
    const head = el("div", { style: { display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" } }, [
      pill(agent.status),
      ...(agent.capabilities || []).map(c =>
        el("span", { class: "mono", style: {
          fontSize: "11px", color: "var(--text-mute)",
          padding: "3px 8px", background: "rgba(31,53,89,0.6)", borderRadius: "3px",
        } }, c)
      ),
    ]);

    const lifetimeJobs =
      (agent.jobs_claimed || 0) +
      (agent.epoch_history || []).reduce((s, b) => s + (b.jobs_claimed || 0), 0);

    const meta = renderMetaGrid([
      ["epoch", `e${agent.epoch}`],
      ["registered", relTime(agent.registered_at)],
      ["heartbeat", relTime(agent.last_heartbeat_at)],
      ["caps", (agent.capabilities || []).length],
      ["this epoch", `${agent.jobs_completed || 0} done · ${agent.jobs_claimed || 0} claimed`],
      ["lifetime jobs", lifetimeJobs],
    ]);

    const actions = el("div", { class: "agent-actions" }, [
      el("button", {
        class: "kill-button",
        onclick: () => killAgent(agent.id),
      }, "kill (demo)"),
    ]);

    const bands = renderEpochBands(agent);
    const recent = renderAgentRecentJobs(historyEvents);

    return el("div", { style: { display: "flex", flexDirection: "column", gap: "24px" } }, [
      head, meta, actions,
      el("section", {}, [
        el("h3", {}, "epoch history"),
        el("p", {}, "each epoch is a fresh registration. claims from a previous epoch are rejected by the dispatcher."),
        bands,
      ]),
      el("section", {}, [
        el("h3", {}, "recent events on this testbed"),
        recent,
      ]),
    ]);
  }

  function renderEpochBands(agent) {
    // Build the list newest-epoch-first: current epoch on top (active),
    // then epoch_history in reverse (most-recent past first).
    const items = [];
    items.push({
      epoch: agent.epoch,
      started: agent.epoch_started_at || agent.registered_at,
      ended: null,
      reason: "current",
      jobs_claimed: agent.jobs_claimed || 0,
      jobs_completed: agent.jobs_completed || 0,
      jobs_failed: agent.jobs_failed || 0,
      active: true,
    });
    for (const b of [...(agent.epoch_history || [])].reverse()) {
      items.push({
        epoch: b.epoch,
        started: b.started_at,
        ended: b.ended_at,
        reason: b.reason_ended || "—",
        jobs_claimed: b.jobs_claimed || 0,
        jobs_completed: b.jobs_completed || 0,
        jobs_failed: b.jobs_failed || 0,
        active: false,
      });
    }
    return el("ol", { class: "epoch-bands" }, items.map(b => el("li", {
      class: "epoch-band" + (b.active ? " active" : ""),
    }, [
      el("span", { class: "epoch-num" }, `e${b.epoch}`),
      el("div", { class: "epoch-band-info" }, [
        el("span", { class: "epoch-when" }, `${relTime(b.started)} → ${b.ended ? relTime(b.ended) : "now"}`),
        el("span", { class: "epoch-reason" },
          `${b.reason} · ${b.jobs_completed}/${b.jobs_claimed} completed${b.jobs_failed ? ` · ${b.jobs_failed} failed` : ""}`),
      ]),
      b.active ? el("span", { class: "epoch-active-tag" }, "active") : null,
    ])));
  }

  function renderAgentRecentJobs(events) {
    if (events.length === 0) return el("p", { class: "empty-row" }, "no events on this testbed yet.");
    return el("ol", { class: "agent-recent" }, events.slice(0, 12).map(ev => el("li", {}, [
      el("button", { class: "row-id", onclick: () => openJobPanel(ev.job_id) }, shortId(ev.job_id)),
      el("span", { class: "row-product" }, ev.product),
      pill(ev.kind),
      el("span", { class: "row-when" }, hms(ev.at)),
    ])));
  }

  async function killAgent(agentId) {
    if (!confirm("Send kill signal? The agent will be marked offline and its current job re-queued.")) return;
    try {
      const r = await fetch(`/api/agents/${agentId}/kill`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      poll();
    } catch (e) {
      alert(`kill failed: ${e.message || e}`);
    }
  }

  function closeSlideOvers() {
    document.getElementById("job-panel").hidden = true;
    document.getElementById("agent-panel").hidden = true;
    state.openPanel = null;
  }

  // ===== Helpers used by renderers =====

  function shortId(id) { return id ? String(id).slice(0, 8) : "—"; }
  function lastAgentName(job) {
    for (let i = (job.history || []).length - 1; i >= 0; i--) {
      const ev = job.history[i];
      if (ev.kind === "claimed" && ev.agent_name) return ev.agent_name;
    }
    return null;
  }
  function lastReassignReason(job) {
    for (let i = (job.history || []).length - 1; i >= 0; i--) {
      const ev = job.history[i];
      if (ev.kind === "reassigned" || ev.kind === "overrun") return ev.detail || ev.kind;
    }
    return null;
  }
  function lastFailReason(job) {
    for (let i = (job.history || []).length - 1; i >= 0; i--) {
      const ev = job.history[i];
      if (ev.kind === "failed") return ev.detail;
    }
    return null;
  }
  function assignedAgentName(job) {
    if (!state.lastSnapshot || !job.assigned_agent_id) return null;
    const a = (state.lastSnapshot.agents || []).find(a => a.id === job.assigned_agent_id);
    return a ? a.name : null;
  }

  // ===== Notifications (preserve from prior dashboard) =====

  function loadNotified() {
    try { return JSON.parse(localStorage.getItem(NOTIFIED_KEY) || "[]"); } catch { return []; }
  }
  function saveNotified(ids) {
    try { localStorage.setItem(NOTIFIED_KEY, JSON.stringify(ids.slice(-200))); } catch {}
  }
  function requestNotifyPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") Notification.requestPermission().catch(() => {});
  }
  function maybeNotifyOnComplete(snap) {
    const me = loadIdentity();
    if (!me) return;
    const notified = new Set(loadNotified());
    let changed = false;
    for (const j of (snap.recent_completed || [])) {
      if (j.submitter !== me) continue;
      if (notified.has(j.id)) continue;
      notified.add(j.id); changed = true;
      const title = j.status === "completed" ? `tss: ${j.product} passed` : `tss: ${j.product} failed`;
      const body = `job ${shortId(j.id)} · ${j.status}`;
      if ("Notification" in window && Notification.permission === "granted") {
        try { new Notification(title, { body, tag: j.id }); } catch {}
      } else {
        flashBanner(`${title} · ${body}`);
      }
    }
    if (changed) saveNotified([...notified]);
  }
  function flashBanner(msg) {
    const b = document.createElement("div");
    b.className = "flash-banner";
    b.textContent = msg;
    document.body.appendChild(b);
    setTimeout(() => b.remove(), 4000);
  }

  // ===== Demo panel — on-demand failure-mode triggers =====

  /** Each handler submits a job tuned to demonstrate one failure mode, or
   *  kills an agent to demonstrate disconnect-driven reassignment. The
   *  configured durations + crash points are short on purpose — a presenter
   *  shouldn't have to wait more than ~10s to see the watchdog react. */
  const DEMO_ACTIONS = {
    "submit-normal-vg": {
      label: "submit normal vg",
      payload: { product: "vehicle_gateway", duration_seconds: 4, max_attempts: 3, slow_multiplier: 1.0 },
    },
    "submit-normal-ag": {
      label: "submit normal ag",
      payload: { product: "asset_gateway", duration_seconds: 4, max_attempts: 3, slow_multiplier: 1.0 },
    },
    "submit-crashing": {
      label: "submit crashing job",
      payload: {
        product: "vehicle_gateway", duration_seconds: 6, max_attempts: 3,
        crash_at_pct: 0.5,
      },
      hint: "watch: claimed → fails at 50% → re-queued → retried (up to max_attempts).",
    },
    "submit-overrun": {
      label: "submit overrunning job",
      payload: {
        product: "vehicle_gateway", duration_seconds: 3, max_attempts: 3,
        slow_multiplier: 5.0,
      },
      hint: "watch: claimed → watchdog kills at ~9s (3× declared) → re-queued.",
    },
  };

  async function fireDemo(action) {
    const toast = document.getElementById("demo-toast");
    const showToast = (msg, isError = false) => {
      toast.textContent = msg;
      toast.classList.toggle("error", !!isError);
      toast.hidden = false;
      setTimeout(() => { toast.hidden = true; }, 3200);
    };

    if (action === "kill-random") {
      const snap = state.lastSnapshot;
      const candidates = (snap?.agents || []).filter(a => a.status !== "offline");
      if (candidates.length === 0) {
        showToast("no live agents to kill", true);
        return;
      }
      const victim = candidates[Math.floor(Math.random() * candidates.length)];
      try {
        const r = await fetch(`/api/agents/${victim.id}/kill`, { method: "POST" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        showToast(`killed ${victim.name} · watch its job re-queue`);
        poll();
      } catch (e) { showToast(`kill failed: ${e.message || e}`, true); }
      return;
    }

    const def = DEMO_ACTIONS[action];
    if (!def) return;
    const payload = { ...def.payload, submitter: loadIdentity() || "demo" };
    try {
      const r = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      showToast(def.hint || `submitted job ${shortId(body.job_id)}`);
      poll();
    } catch (e) { showToast(`submit failed: ${e.message || e}`, true); }
  }

  function wireDemoPanel() {
    const panel = document.getElementById("demo-panel");
    if (!panel) return;
    document.getElementById("demo-collapse").addEventListener("click", () => {
      panel.classList.toggle("collapsed");
      const btn = document.getElementById("demo-collapse");
      btn.textContent = panel.classList.contains("collapsed") ? "+" : "−";
    });
    for (const btn of panel.querySelectorAll(".demo-btn")) {
      btn.addEventListener("click", () => fireDemo(btn.dataset.demo));
    }
  }

  // ===== Init =====

  document.addEventListener("DOMContentLoaded", () => {
    state.identity = loadIdentity() || null;
    state.role = loadRole();
    refreshIdentityUI();
    if (state.identity) requestNotifyPermission();
    applyRole(state.role);

    // Identity wiring (banner / pill)
    document.getElementById("identity-save").addEventListener("click", () => {
      const v = document.getElementById("identity-input").value.trim();
      if (v) saveIdentity(v);
    });
    document.getElementById("identity-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") document.getElementById("identity-save").click();
    });
    document.getElementById("identity-change").addEventListener("click", () => {
      const v = prompt("Update your name:", loadIdentity());
      if (v !== null) saveIdentity(v.trim());
    });

    // Role toggle
    document.getElementById("role-engineer").addEventListener("click", () => applyRole("engineer"));
    document.getElementById("role-operator").addEventListener("click", () => applyRole("operator"));

    // Slide-over close (scrim, button, Esc)
    for (const node of document.querySelectorAll("[data-slide-close]")) {
      node.addEventListener("click", closeSlideOvers);
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeSlideOvers();
    });

    wireDemoPanel();

    poll();
    setInterval(poll, POLL_MS);
  });
})();
