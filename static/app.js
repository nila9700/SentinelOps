"use strict";

const POLL_MS = 15000;
const view = document.getElementById("view");

const state = {
  directory: [],
  faults: [],
  pageToken: 0,
  busy: 0,
  compOpen: {},
  ticketFilter: "active",
  simFault: "",
  simComponent: "",
  confirmingRemove: false,
};

const STATUS = {
  operational: { label: "Working", tone: "ok", icon: "check" },
  degraded: { label: "Some issues", tone: "warn", icon: "alert" },
  partial: { label: "Partly down", tone: "partial", icon: "alert" },
  down: { label: "Down", tone: "bad", icon: "cross" },
  checking: { label: "Checking", tone: "idle", icon: "clock" },
};
const STEP_TONE = { ok: "ok", warn: "warn", fail: "bad", skipped: "idle" };
const STEP_ICON = { ok: "check", warn: "alert", fail: "cross", skipped: "minus" };
const TICKET_STATUS = { assigned: "Assigned", in_progress: "In progress", resolved: "Resolved" };

const ICON_PATHS = {
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  cross: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  alert: '<path d="M12 7v6.5M12 17.2v.3"/>',
  minus: '<path d="M7 12h10"/>',
  clock: '<path d="M12 7v5.2l3.2 2"/>',
  chevron: '<path d="M6 9.5l6 6 6-6"/>',
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET" };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    setOffline(true);
    throw new Error("We can't reach the SentinelOps server.");
  }
  setOffline(false);
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(data.error || "Something went wrong.");
    failure.status = response.status;
    failure.suggestions = data.suggestions || [];
    throw failure;
  }
  return data;
}

function setOffline(isOffline) {
  document.getElementById("offline").hidden = !isOffline;
}

let toastTimer;
function toast(message) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 4000);
}

function timeAgo(seconds) {
  if (!seconds) return "not yet";
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 10) return "just now";
  if (delta < 60) return `${Math.floor(delta)} seconds ago`;
  if (delta < 3600) {
    const minutes = Math.floor(delta / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  if (delta < 86400) {
    const hours = Math.floor(delta / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  return new Date(seconds * 1000).toLocaleDateString();
}

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function formatMs(ms) {
  if (ms === null || ms === undefined) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function pill(status) {
  const info = STATUS[status] || STATUS.checking;
  return `<span class="pill tone-${info.tone}">${icon(info.icon)}${info.label}</span>`;
}

function headline(service) {
  const name = service.name;
  const failing = service.components.filter((c) => c.state === "down").map((c) => c.name);
  switch (service.status) {
    case "operational": return `${name} is working normally`;
    case "degraded": return `${name} is working, with some issues`;
    case "partial": return `${name}: ${failing.join(" and ")} ${failing.length > 1 ? "are" : "is"} down`;
    case "down": return `${name} is down`;
    default: return `Checking ${name}…`;
  }
}

function subline(service) {
  const problems = service.components.filter((c) => c.state === "down" || c.state === "degraded");
  if (service.status === "checking") return "This usually takes a few seconds.";
  if (!problems.length) return "Every check passed. There's nothing you need to do.";
  return problems.map((c) => `${c.name}: ${c.summary}`).join(" ");
}

function bars(history, large) {
  const slots = 40;
  const items = history.slice(-slots);
  const known = items.map((h) => h.response_ms).filter((ms) => ms !== null);
  const max = Math.max(1000, ...known);
  const width = 3;
  const gap = 1;
  const total = slots * (width + gap);
  const padding = slots - items.length;
  let rects = "";
  for (let i = 0; i < padding; i += 1) {
    rects += `<rect class="bar-empty" x="${i * (width + gap)}" y="26" width="${width}" height="4"/>`;
  }
  items.forEach((item, index) => {
    const tone = (STATUS[item.status] || STATUS.checking).tone;
    const failed = item.response_ms === null;
    const height = failed ? 30 : Math.max(6, Math.round((item.response_ms / max) * 30));
    const x = (index + padding) * (width + gap);
    rects += `<rect class="bar-${tone}" x="${x}" y="${30 - height}" width="${width}" height="${height}"/>`;
  });
  const label = items.length
    ? `Response times for the last ${items.length} checks`
    : "No checks yet";
  return `<svg class="bars ${large ? "bars-large" : ""}" viewBox="0 0 ${total} 30" preserveAspectRatio="none" role="img" aria-label="${label}">${rects}</svg>`;
}

function avatar(name) {
  const initials = name.split(" ").map((part) => part[0]).slice(0, 2).join("");
  const hash = [...name].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 5;
  return `<span class="avatar a${hash}" aria-hidden="true">${esc(initials)}</span>`;
}

function simulatedTag(item) {
  return item.simulated ? '<span class="tag demo">Simulated</span>' : "";
}

function setNav(page) {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    if (link.dataset.nav === page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function setTicketCount(count) {
  const badge = document.getElementById("nav-count");
  badge.hidden = !count;
  badge.textContent = count;
  badge.setAttribute("aria-label", `${count} active`);
}

function notFound(message) {
  view.innerHTML = `<div class="wrap page"><div class="card empty"><strong>${esc(message)}</strong>
    <a href="#/">Back to status</a></div></div>`;
}

/* Home */

function renderHome() {
  view.innerHTML = `
    <section class="hero">
      <div class="wrap">
        <div class="hero-inner">
          <h1>Is it working right now?</h1>
          <p>Type a company or website. We'll check it and explain what we find in plain language.</p>
          <form class="search" id="search" role="search" autocomplete="off">
            <div class="search-row">
              <input id="query" type="text" maxlength="200" placeholder="Spotify, or example.com"
                aria-label="Company name or website address" role="combobox" aria-autocomplete="list"
                aria-expanded="false" aria-controls="suggestions" spellcheck="false">
              <button type="submit">Check</button>
            </div>
            <ul class="suggestions" id="suggestions" role="listbox" aria-label="Companies" hidden></ul>
          </form>
          <div id="search-message" class="search-message" aria-live="polite" hidden></div>
          <div class="chips" id="popular">
            <span>Try:</span>
            ${["Netflix", "Klarna", "Discord", "IKEA"].map((name) =>
              `<button type="button" class="chip" data-query="${esc(name)}">${esc(name)}</button>`).join("")}
          </div>
        </div>
      </div>
    </section>
    <div class="wrap page" id="home-live">
      <div class="grid">${'<div class="skeleton"></div>'.repeat(6)}</div>
    </div>`;
  bindSearch();
}

function summaryBand(services) {
  const checked = services.filter((s) => s.status !== "checking");
  if (!checked.length) {
    return { tone: "idle", iconName: "clock", text: "Running the first checks…" };
  }
  const trouble = checked.filter((s) => s.status !== "operational");
  if (!trouble.length) {
    const text = checked.length === 1 ? "The service we're watching is working" : `All ${checked.length} services are working`;
    return { tone: "ok", iconName: "check", text };
  }
  const down = trouble.some((s) => s.status === "down" || s.status === "partial");
  const noun = trouble.length === 1 ? "service needs" : "services need";
  return { tone: down ? "bad" : "warn", iconName: down ? "cross" : "alert", text: `${trouble.length} of ${checked.length} ${noun} attention` };
}

function serviceCard(service) {
  const ticket = service.open_incidents
    ? `<span class="ticket-flag">${service.open_incidents} open ticket${service.open_incidents === 1 ? "" : "s"}</span>`
    : `<span>${esc(formatMs(service.response_ms))}</span>`;
  return `
    <a class="card service-card" href="#/service/${service.id}">
      <header>
        <div><h3>${esc(service.name)}</h3><span class="muted small">${esc(service.key)}</span></div>
        ${pill(service.status)}
      </header>
      ${bars(service.history)}
      <footer>${ticket}<span>Checked ${esc(timeAgo(service.checked_at))}</span></footer>
    </a>`;
}

function ticketRows(incidents) {
  return `<div class="card table-card"><table>
    <thead><tr><th>Ticket</th><th>Problem</th><th>Service</th><th>Team</th><th>Agent</th><th>Status</th></tr></thead>
    <tbody>${incidents.map((incident) => `<tr>
      <td><a href="#/incidents/${incident.id}">${esc(incident.ticket)}</a></td>
      <td>${esc(incident.title)} ${simulatedTag(incident)}</td>
      <td>${esc(incident.service_name)}<br><span class="muted small">${esc(incident.component)}</span></td>
      <td>${esc(incident.department_name)}</td>
      <td>${esc(incident.agent)}</td>
      <td>${esc(TICKET_STATUS[incident.status])}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

async function refreshHome(token) {
  const data = await api("/api/overview");
  if (token !== state.pageToken) return;
  setTicketCount(data.active_incidents.length);
  const live = document.getElementById("home-live");
  if (!live) return;
  const band = summaryBand(data.services);
  const tickets = data.active_incidents.length
    ? `<section class="section"><div class="section-head"><h2>Being fixed now</h2><a href="#/incidents">All tickets</a></div>${ticketRows(data.active_incidents.slice(0, 5))}</section>`
    : "";
  const grid = data.services.length
    ? `<div class="grid">${data.services.map(serviceCard).join("")}</div>`
    : '<div class="card empty"><strong>Nothing here yet</strong>Search for a company above to start watching it.</div>';
  live.innerHTML = `
    <div class="summary-band tone-${band.tone}" role="status">${icon(band.iconName).replace("<svg", '<svg width="24" height="24"')}<span>${esc(band.text)}</span></div>
    ${tickets}
    <section class="section">
      <div class="section-head"><h2>Services we're watching</h2><span class="muted small">Rechecked every minute</span></div>
      ${grid}
    </section>`;
}

function matchesFor(text) {
  const needle = text.trim().toLowerCase();
  if (!needle) return [];
  const score = (company) => {
    const names = [company.name, ...company.aliases, company.domain].map((n) => n.toLowerCase());
    if (names.some((n) => n.startsWith(needle))) return 0;
    if (names.some((n) => n.includes(needle))) return 1;
    return 2;
  };
  return state.directory
    .map((company) => ({ company, rank: score(company) }))
    .filter((item) => item.rank < 2)
    .sort((a, b) => a.rank - b.rank)
    .slice(0, 6)
    .map((item) => item.company);
}

function bindSearch() {
  const form = document.getElementById("search");
  const input = document.getElementById("query");
  const list = document.getElementById("suggestions");
  const message = document.getElementById("search-message");
  const button = form.querySelector("button[type=submit]");
  let active = -1;
  let matches = [];

  const close = () => {
    list.hidden = true;
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  };

  const paint = () => {
    list.innerHTML = matches.map((company, index) => `
      <li id="option-${index}" role="option" data-index="${index}" aria-selected="${index === active}">
        <span>${esc(company.name)}</span><span>${esc(company.domain)}</span>
      </li>`).join("");
    list.hidden = !matches.length;
    input.setAttribute("aria-expanded", String(matches.length > 0));
    if (active >= 0) input.setAttribute("aria-activedescendant", `option-${active}`);
    else input.removeAttribute("aria-activedescendant");
  };

  const showError = (error) => {
    const chips = (error.suggestions || []).map((name) =>
      `<button type="button" class="chip" data-query="${esc(name)}">${esc(name)}</button>`).join("");
    message.className = "search-message error";
    message.innerHTML = `${esc(error.message)}${chips ? `<div class="chips">${chips}</div>` : ""}`;
    message.hidden = false;
  };

  const run = async (query) => {
    close();
    input.value = query;
    button.disabled = true;
    button.textContent = "Checking…";
    message.className = "search-message";
    message.textContent = `Checking ${query}. This usually takes a few seconds.`;
    message.hidden = false;
    state.busy += 1;
    try {
      const service = await api("/api/check", { method: "POST", body: { query } });
      state.compOpen = {};
      location.hash = `#/service/${service.id}`;
    } catch (error) {
      showError(error);
      button.disabled = false;
      button.textContent = "Check";
      input.focus();
    } finally {
      state.busy -= 1;
    }
  };

  input.addEventListener("input", () => {
    matches = matchesFor(input.value);
    active = -1;
    message.hidden = true;
    paint();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { close(); return; }
    if (list.hidden || !matches.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      active = (active + 1) % matches.length;
      paint();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      active = (active - 1 + matches.length) % matches.length;
      paint();
    }
  });
  input.addEventListener("blur", close);
  list.addEventListener("mousedown", (event) => {
    event.preventDefault();  // Keep focus in the input until the click lands.
    const option = event.target.closest("li");
    if (option) run(matches[Number(option.dataset.index)].name);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (button.disabled) return;
    const choice = active >= 0 ? matches[active].name : input.value.trim();
    if (!choice) {
      showError(new Error("Type a company name or website address."));
      input.focus();
      return;
    }
    run(choice);
  });
  // The hero is rebuilt on each visit, so this listener never stacks up.
  view.querySelector(".hero").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip[data-query]");
    if (chip && !button.disabled) run(chip.dataset.query);
  });
  input.focus();
}

/* Service page */

function componentBlock(component) {
  const info = STATUS[component.state] || STATUS.checking;
  const wanted = state.compOpen[component.name];
  const open = wanted === undefined ? component.state === "down" || component.state === "degraded" : wanted;
  const steps = (component.steps || []).map((step) => `
    <li>
      <span class="step-icon tone-${STEP_TONE[step.state]}">${icon(STEP_ICON[step.state])}</span>
      <div class="body">
        <strong>${esc(step.label)}</strong>
        <span class="small muted">${esc(step.summary)}</span>
        ${step.detail ? `<div class="mono muted">${esc(step.detail)}</div>` : ""}
      </div>
    </li>`).join("");
  return `
    <details class="card component" data-component="${esc(component.name)}" ${open ? "open" : ""}>
      <summary>
        <span class="pill tone-${info.tone}">${icon(info.icon)}${info.label}</span>
        <span class="name"><strong>${esc(component.name)}</strong><span>${esc(component.summary || "")}</span></span>
        <span class="time">${esc(formatMs(component.response_ms))}</span>
        <span class="chevron">${icon("chevron")}</span>
      </summary>
      <ol class="steps">${steps}</ol>
      <div class="mono muted notice">${esc(component.url)}</div>
    </details>`;
}

function ticketsPanel(service) {
  const active = service.incidents.filter((i) => i.status !== "resolved");
  const recent = service.incidents.filter((i) => i.status === "resolved").slice(0, 3);
  const card = (incident) => `
    <a class="ticket-card" href="#/incidents/${incident.id}">
      <span class="meta-row"><span class="muted">${esc(incident.ticket)}</span>${simulatedTag(incident)}</span>
      <strong>${esc(incident.title)}</strong>
      <span class="small muted">${esc(incident.department_name)} · ${esc(incident.agent)}</span>
    </a>`;
  const activeHtml = active.length
    ? `<p class="small muted">We've passed this to the right team.</p>${active.map(card).join("")}`
    : '<p class="small muted">No open tickets.</p>';
  const recentHtml = recent.length ? `<h3 class="section">Recently fixed</h3>${recent.map(card).join("")}` : "";
  return `<section class="card panel"><h2>Who's handling it</h2>${activeHtml}${recentHtml}</section>`;
}

function historyPanel(service) {
  const uptime = service.uptime;
  const percent = uptime.percent === null ? "–" : `${uptime.percent.toFixed(uptime.percent === 100 ? 0 : 1)}%`;
  return `<section class="card panel">
    <h2>Recent checks</h2>
    ${bars(service.history, true)}
    <div class="stats">
      <div><strong>${percent}</strong><span class="small muted">Uptime</span></div>
      <div><strong>${uptime.checks}</strong><span class="small muted">Real checks</span></div>
    </div>
  </section>`;
}

function demoPanel(service) {
  const components = service.components.map((c) => c.name);
  if (!components.includes(state.simComponent)) state.simComponent = components[0];
  const faults = state.faults.map((fault) =>
    `<option value="${esc(fault.key)}" ${fault.key === state.simFault ? "selected" : ""}>${esc(fault.label)}</option>`).join("");
  const parts = components.map((name) =>
    `<option ${name === state.simComponent ? "selected" : ""}>${esc(name)}</option>`).join("");
  return `<section class="card panel">
    <h2>Try a demo outage</h2>
    <p class="small muted">Real sites are rarely down. Create a pretend problem to watch it get routed to a team. Nothing is sent to anyone.</p>
    <label>Problem<select id="sim-fault">${faults}</select></label>
    <label>Which part<select id="sim-component">${parts}</select></label>
    <button class="btn full" data-action="simulate" data-id="${service.id}">Start demo outage</button>
  </section>`;
}

function serviceHtml(service) {
  const info = STATUS[service.status] || STATUS.checking;
  const assumed = service.source === "guess"
    ? `<div class="notice">We didn't have ${esc(service.name)} in our directory, so we checked <strong>${esc(service.key)}</strong>. If that's the wrong site, search for its exact address.</div>`
    : "";
  const sim = service.simulation
    ? `<div class="notice demo"><span>This is a demo outage on ${esc(service.simulation.component)}. The real site isn't affected.</span>
       <button class="btn secondary" data-action="end-simulation" data-id="${service.id}">End demo</button></div>`
    : "";
  const remove = state.confirmingRemove
    ? `<button class="btn danger" data-action="remove" data-id="${service.id}">Yes, stop watching</button>
       <button class="btn quiet" data-action="cancel-remove">Cancel</button>`
    : '<button class="btn quiet" data-action="confirm-remove">Stop watching</button>';
  return `
    <div class="wrap page">
      <a class="crumb" href="#/">← All services</a>
      <section class="status-banner tone-${info.tone}">
        ${icon(info.icon)}
        <div class="text">
          <h1>${esc(headline(service))}</h1>
          <p>${esc(subline(service))}</p>
          <p class="small muted">Checked ${esc(timeAgo(service.checked_at))}</p>
        </div>
        <button class="btn secondary" data-action="recheck" data-id="${service.id}">Check again</button>
      </section>
      ${sim}${assumed}
      <div class="columns">
        <div class="stack">
          <div class="section-head"><h2>What's working</h2><span class="muted small">Open a row to see each step</span></div>
          ${service.components.map(componentBlock).join("")}
          <div class="actions">${remove}</div>
        </div>
        <aside class="stack">
          ${ticketsPanel(service)}
          ${historyPanel(service)}
          ${service.simulation ? "" : demoPanel(service)}
        </aside>
      </div>
    </div>`;
}

async function loadService(id, token) {
  const service = await api(`/api/services/${id}`);
  if (token !== state.pageToken) return;
  view.innerHTML = serviceHtml(service);
}

/* Tickets */

function renderTicketsShell() {
  view.innerHTML = `<div class="wrap page">
    <h1 class="title">Tickets</h1>
    <p class="page-intro">Every problem we find becomes a ticket, sent to the team that can fix it. Tickets close by themselves once a check passes again.</p>
    <div id="tickets"><div class="skeleton"></div></div>
  </div>`;
}

async function loadTickets(token) {
  const data = await api("/api/incidents");
  if (token !== state.pageToken) return;
  const active = data.incidents.filter((i) => i.status !== "resolved");
  setTicketCount(active.length);
  const shown = state.ticketFilter === "active"
    ? active
    : data.incidents.filter((i) => i.status === "resolved");
  const tab = (key, label, count) =>
    `<button data-action="ticket-filter" data-filter="${key}" aria-pressed="${state.ticketFilter === key}">${label} (${count})</button>`;
  const empty = state.ticketFilter === "active"
    ? '<strong>No open tickets</strong>To see routing in action, open a service and start a demo outage.'
    : "<strong>No resolved tickets yet</strong>Fixed problems will appear here.";
  document.getElementById("tickets").innerHTML = `
    <div class="tabs">${tab("active", "Open", active.length)}${tab("resolved", "Resolved", data.incidents.length - active.length)}</div>
    ${shown.length ? ticketRows(shown) : `<div class="card empty">${empty}</div>`}`;
}

async function loadTicket(id, token) {
  const ticket = await api(`/api/incidents/${id}`);
  if (token !== state.pageToken) return;
  const actions = [];
  if (ticket.status === "assigned") actions.push(`<button class="btn" data-action="ticket-start" data-id="${ticket.id}">Start work</button>`);
  if (ticket.status !== "resolved") actions.push(`<button class="btn secondary" data-action="ticket-resolve" data-id="${ticket.id}">Mark as resolved</button>`);
  const service = ticket.service_id
    ? `<a class="btn secondary full" href="#/service/${ticket.service_id}">View ${esc(ticket.service_name)}</a>`
    : "";
  const severity = ticket.severity === "Critical" ? "tag critical" : "tag";
  view.innerHTML = `<div class="wrap page">
    <a class="crumb" href="#/incidents">← All tickets</a>
    <div class="incident-head">
      <div>
        <div class="meta-row">
          <span class="muted">${esc(ticket.ticket)}</span>
          <span class="${severity}">${esc(ticket.severity)}</span>
          <span class="tag">${esc(TICKET_STATUS[ticket.status])}</span>
          ${simulatedTag(ticket)}
        </div>
        <h1>${esc(ticket.title)}</h1>
        <p class="muted">${esc(ticket.service_name)} · ${esc(ticket.component)}</p>
      </div>
      <div class="actions">${actions.join("")}</div>
    </div>
    <div class="columns">
      <div class="stack">
        <section class="card panel"><h2>What happened</h2><p>${esc(ticket.summary)}</p></section>
        <section class="card panel"><h2>What the team will do</h2><p>${esc(ticket.action)}</p></section>
        <section class="card panel"><h2>Timeline</h2>
          <ol class="timeline">${ticket.events.map((event) =>
            `<li><time>${esc(clockTime(event.ts))}</time>${esc(event.message)}</li>`).join("")}</ol>
        </section>
      </div>
      <aside class="stack">
        <section class="card panel">
          <h2>Assigned to</h2>
          <div class="person">${avatar(ticket.agent)}<div><strong>${esc(ticket.agent)}</strong><br><span class="small muted">${esc(ticket.department_name)}</span></div></div>
          <p class="small muted section">${esc(ticket.reason)}</p>
          ${service}
        </section>
      </aside>
    </div>
  </div>`;
}

/* Teams */

async function loadTeams(token) {
  const data = await api("/api/teams");
  if (token !== state.pageToken) return;
  const steps = [
    ["Detect", "A check finds a problem"],
    ["Diagnose", "We work out which step failed"],
    ["Route", "It goes to the right team"],
    ["Assign", "The least busy agent takes it"],
    ["Resolve", "It closes when checks pass"],
  ];
  view.innerHTML = `<div class="wrap page">
    <h1 class="title">Teams</h1>
    <p class="page-intro">How a problem finds the right people. All teams and agents here are fictional.</p>
    <ol class="flow">${steps.map(([title, text]) => `<li class="card"><strong>${title}</strong><span>${text}</span></li>`).join("")}</ol>
    <section class="section">
      <div class="grid">${data.teams.map((team) => `
        <article class="card team-card">
          <h2>${esc(team.name)}</h2>
          <p class="small muted">${esc(team.handles)}</p>
          ${team.agents.map((agent) => `
            <div class="person">${avatar(agent.name)}<div><strong>${esc(agent.name)}</strong>
              <small>${agent.open} open · ${agent.resolved} resolved</small></div></div>`).join("")}
        </article>`).join("")}</div>
    </section>
  </div>`;
}

/* Actions */

async function busyButton(button, label, work) {
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${esc(label)}`;
  state.busy += 1;
  try {
    await work();
  } catch (error) {
    button.disabled = false;
    button.innerHTML = original;
    toast(error.message);
  } finally {
    state.busy -= 1;
  }
}

const actions = {
  recheck: (button) => busyButton(button, "Checking…", async () => {
    await api(`/api/services/${button.dataset.id}/recheck`, { method: "POST", body: {} });
    await refresh();
    toast("Check finished.");
  }),
  simulate: (button) => busyButton(button, "Starting…", async () => {
    const fault = document.getElementById("sim-fault").value;
    const component = document.getElementById("sim-component").value;
    state.compOpen = {};
    await api(`/api/services/${button.dataset.id}/simulate`, { method: "POST", body: { fault, component } });
    await refresh();
    toast("Demo outage started. A ticket has been created.");
  }),
  "end-simulation": (button) => busyButton(button, "Ending…", async () => {
    state.compOpen = {};
    await api(`/api/services/${button.dataset.id}/simulate`, { method: "DELETE" });
    await refresh();
    toast("Demo ended. The ticket closed by itself.");
  }),
  "confirm-remove": () => { state.confirmingRemove = true; refresh(); },
  "cancel-remove": () => { state.confirmingRemove = false; refresh(); },
  remove: (button) => busyButton(button, "Removing…", async () => {
    await api(`/api/services/${button.dataset.id}`, { method: "DELETE" });
    state.confirmingRemove = false;
    location.hash = "#/";
  }),
  "ticket-filter": (button) => {
    state.ticketFilter = button.dataset.filter;
    refresh();
  },
  "ticket-start": (button) => busyButton(button, "Starting…", async () => {
    await api(`/api/incidents/${button.dataset.id}/start`, { method: "POST", body: {} });
    await refresh();
  }),
  "ticket-resolve": (button) => busyButton(button, "Resolving…", async () => {
    await api(`/api/incidents/${button.dataset.id}/resolve`, { method: "POST", body: {} });
    await refresh();
    toast("Ticket resolved.");
  }),
};

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (button) actions[button.dataset.action](button);
});

document.addEventListener("change", (event) => {
  if (event.target.id === "sim-fault") state.simFault = event.target.value;
  if (event.target.id === "sim-component") state.simComponent = event.target.value;
});

// Remember which rows are open, so a background refresh doesn't close them.
document.addEventListener("toggle", (event) => {
  const name = event.target.dataset && event.target.dataset.component;
  if (name) state.compOpen[name] = event.target.open;
}, true);

/* Router */

function currentRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (!parts.length) return { page: "home" };
  if (parts[0] === "service" && /^\d+$/.test(parts[1] || "")) return { page: "service", id: parts[1] };
  if (parts[0] === "incidents") {
    if (!parts[1]) return { page: "incidents" };
    if (/^\d+$/.test(parts[1])) return { page: "incident", id: parts[1] };
  }
  if (parts[0] === "teams" && !parts[1]) return { page: "teams" };
  return { page: "missing" };
}

async function load(route, token) {
  switch (route.page) {
    case "home": return refreshHome(token);
    case "service": return loadService(route.id, token);
    case "incidents": return loadTickets(token);
    case "incident": return loadTicket(route.id, token);
    case "teams": return loadTeams(token);
    default: return undefined;
  }
}

async function guardedLoad(route, token) {
  try {
    await load(route, token);
  } catch (error) {
    if (token !== state.pageToken) return;
    if (error.status === 404) notFound(error.message);
    else if (error.status) toast(error.message);
  }
}

async function navigate() {
  const route = currentRoute();
  state.pageToken += 1;
  state.confirmingRemove = false;
  state.compOpen = {};  // Rows are remembered per page: names repeat across companies.
  document.getElementById("toast").hidden = true;
  setNav({ home: "home", service: "home", incidents: "incidents", incident: "incidents", teams: "teams" }[route.page]);
  if (route.page === "home") renderHome();
  else if (route.page === "incidents") renderTicketsShell();
  else if (route.page === "missing") notFound("We couldn't find that page.");
  else view.innerHTML = '<div class="wrap page"><div class="skeleton"></div></div>';
  window.scrollTo(0, 0);
  await guardedLoad(route, state.pageToken);
}

function focusSelector(element) {
  if (!element || !view.contains(element)) return null;
  if (element.dataset.action) return `[data-action="${element.dataset.action}"]`;
  if (element.matches("a[href]")) return `a[href="${CSS.escape(element.getAttribute("href"))}"]`;
  if (element.tagName === "SUMMARY") {
    return `details[data-component="${CSS.escape(element.parentElement.dataset.component)}"] summary`;
  }
  return null;
}

async function refresh() {
  // Pages are redrawn from scratch, so keyboard focus has to be put back.
  const selector = focusSelector(document.activeElement);
  await guardedLoad(currentRoute(), state.pageToken);
  if (selector) view.querySelector(selector)?.focus();
}

function poll() {
  const focused = document.activeElement;
  const usingForm = focused && focused.tagName === "SELECT";
  if (!state.busy && !usingForm && !document.hidden) refresh();
}

async function start() {
  window.addEventListener("hashchange", navigate);
  try {
    const [directory, faults] = await Promise.all([api("/api/directory"), api("/api/faults")]);
    state.directory = directory.companies;
    state.faults = faults.faults;
    state.simFault = state.faults[0].key;
  } catch (error) {
    // The page still works without suggestions, and polling retries the rest.
  }
  await navigate();
  setInterval(poll, POLL_MS);
}

start();
