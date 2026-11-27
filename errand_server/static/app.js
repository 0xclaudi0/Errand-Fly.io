"use strict";

// Errand web UI. No framework and no build step. Every piece of text goes in with
// textContent (through h()), so run output or model replies cannot inject HTML.

const view = document.getElementById("view");
let timer = null;
let publicUrl = location.origin; // replaced by the Sprite URL from /api/session
let lastProposal = null; // so the work order animates only when it first appears

// ---- small helpers ---------------------------------------------------------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value; // CSSOM: allowed by the CSP, unlike a style attribute
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// Replace the page content. Skips false/null children, so `cond && h(...)` is safe.
function show(...children) {
  view.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: { "X-Errand-Client": "web" } };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const resp = await fetch(path, init);
  let payload = null;
  try { payload = await resp.json(); } catch (_) { /* empty or not JSON */ }
  if (resp.status === 401) { showLogin(); throw new Error("Log in first."); }
  if (!resp.ok) {
    const err = new Error((payload && payload.error) || `Request failed (${resp.status})`);
    err.status = resp.status;
    throw err;
  }
  return payload ? payload.data : null;
}

function ago(seconds) {
  if (!seconds) return "";
  const diff = Date.now() / 1000 - seconds;
  const s = Math.abs(diff);
  const text = s < 60 ? `${Math.round(s)} s`
    : s < 3600 ? `${Math.round(s / 60)} min`
    : s < 86400 ? `${Math.round(s / 3600)} h`
    : `${Math.round(s / 86400)} d`;
  return diff < 0 ? `in ${text}` : `${text} ago`;
}

function when(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString() : "";
}

function nextText(seconds) {
  return seconds * 1000 <= Date.now() ? "Due now" : `Next run ${ago(seconds)}`;
}

function triggerText(a) {
  if (a.trigger === "schedule") return a.schedule;
  if (a.trigger === "webhook") return "When its webhook is called";
  return "When you run it";
}

// Words from the owner's side, not the system's.
const RUN_STATUS = {
  ok: ["Done", "done"], error: ["Failed", "failed"], timeout: ["Timed out", "failed"],
  running: ["Running", "busy"], interrupted: ["Stopped", "idle"],
};
const DRAFT_STATUS = {
  working: ["Working", "busy"], waiting: ["Needs your reply", "ready"],
  proposed: ["Ready to approve", "ready"], error: ["Stopped", "failed"],
  installed: ["Installed", "done"], discarded: ["Discarded", "idle"],
};

function status(map, key) {
  const [label, tone] = map[key] || [key, "idle"];
  return h("span", { class: `chip ${tone}` }, h("span", { class: "dot", "aria-hidden": "true" }), label);
}

function draftTitle(d) {
  if (d.kind === "repair") return `Fix for ${d.target}`;
  if (d.kind === "change") return `Change to ${d.target}`;
  return "New errand";
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  if (label) button.textContent = label;
}

function toast(message, bad = true) {
  const note = h("div", { class: `toast ${bad ? "bad" : ""}`, role: bad ? "alert" : "status" }, message);
  document.body.append(note);
  setTimeout(() => note.remove(), bad ? 8000 : 3000);
}

function fail(err) {
  toast(err.message || String(err));
}

function stopTimer() {
  if (timer) clearTimeout(timer);
  timer = null;
}

function backLink() {
  return h("a", { class: "back", href: "#/" }, "All errands");
}

// ---- routing ---------------------------------------------------------------

const routes = [
  [/^#\/draft\/(d-[0-9a-f]{8})$/, (m) => showDraft(m[1])],
  [/^#\/automation\/([a-z0-9-]+)$/, (m) => showAutomation(m[1])],
  [/^#\/settings$/, () => showSettings()],
  [/^(#\/?)?$/, () => showHome()],
];

async function render() {
  stopTimer();
  const hash = location.hash;
  const section = hash.startsWith("#/settings") ? "#/settings" : "#/";
  for (const link of document.querySelectorAll("nav a")) {
    const on = link.getAttribute("href") === section;
    link.classList.toggle("on", on);
    if (on) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
  for (const [pattern, page] of routes) {
    const match = hash.match(pattern);
    if (match) {
      try { await page(match); } catch (err) { fail(err); }
      return;
    }
  }
  location.hash = "#/";
}

window.addEventListener("hashchange", render);

// ---- login -----------------------------------------------------------------

function showLogin() {
  stopTimer();
  document.getElementById("nav").hidden = true;
  const expired = new URLSearchParams(location.search).get("login") === "expired";
  const command = "sprite exec -- sh -c 'cd ~/errand && ERRAND_HOME=~/.errand python3 -m errand_server login-link'";
  const result = h("p", { class: "caption", role: "status", style: "margin-top:12px" });
  const other = h("details", { class: "fold" },
    h("summary", {}, "Other ways to log in"),
    h("p", { class: "muted small", style: "margin-top:12px" },
      "Run this on your computer. It uses your own Sprite login and prints a one-time link:"),
    h("pre", {}, command));
  const button = h("button", { class: "primary" }, "Email me a login link");
  button.addEventListener("click", async () => {
    setBusy(button, true, "Sending");
    try {
      const sent = await api("/login/email", { method: "POST", body: {} });
      result.textContent = `Sent to ${sent.sent_to}. Open the link in this browser. It works once, for 15 minutes.`;
      setBusy(button, false, "Send another link");
    } catch (err) {
      result.textContent = err.message;
      other.open = err.status !== 429; // a rate limit only means "wait"; email still works
      setBusy(button, false, "Email me a login link");
    }
  });
  show(
    h("section", { class: "hero" },
      h("h1", { class: "display" }, "Log in to Errand"),
      h("p", { class: "lede" }, "Errand has no password. It emails a one-time link to the address in your settings."),
      expired && h("div", { class: "notice bad", style: "text-align:left" },
        h("p", {}, "That link expired or was already used. Ask for a new one.")),
      h("div", { class: "card", style: "max-width:520px;margin:0 auto;text-align:left;padding:24px" },
        button, result, other)),
  );
}

// ---- home ------------------------------------------------------------------

async function showHome() {
  const [automations, drafts] = await Promise.all([api("/api/automations"), api("/api/drafts")]);
  document.getElementById("nav").hidden = false;
  const open = drafts.filter((d) => !["installed", "discarded"].includes(d.status));
  show(
    requestPad(),
    open.length > 0 && [h("h2", {}, "Waiting on you"), h("div", { class: "card list" }, open.map(draftEntry))],
    h("h2", {}, "Your errands"),
    h("div", { class: "card list" },
      automations.length ? automations.map(automationEntry)
        : h("p", { class: "empty" }, "Nothing yet. Describe your first errand above.")),
  );
}

function requestPad() {
  const input = h("textarea", {
    id: "request", rows: 4,
    placeholder: "Every weekday at 8:00, email me the top 5 Hacker News stories with one line on each.",
  });
  const button = h("button", { class: "primary", type: "submit" }, "Build it");
  return h("section", { class: "hero" },
    h("h1", { class: "display" }, h("label", { for: "request" }, "What should Errand do?")),
    h("form", {
      class: "card composer",
      onsubmit: async (event) => {
        event.preventDefault();
        if (!input.value.trim()) { input.focus(); return; }
        setBusy(button, true, "Starting");
        try {
          const draft = await api("/api/drafts", { method: "POST", body: { message: input.value } });
          location.hash = `#/draft/${draft.id}`;
        } catch (err) { fail(err); setBusy(button, false, "Build it"); }
      },
    }, input, h("div", { class: "actions" }, button)));
}

function draftEntry(d) {
  return h("a", { class: "entry", href: `#/draft/${d.id}` },
    h("span", {},
      h("div", { class: "entry-title" }, draftTitle(d)),
      h("div", { class: "entry-sub" }, d.request)),
    h("span", { class: "entry-side" }, status(DRAFT_STATUS, d.status),
      h("span", { class: "caption" }, ago(d.updated_at))));
}

function automationEntry(a) {
  const last = a.last_run;
  const side = !a.enabled ? "Paused"
    : a.next_run_at ? nextText(a.next_run_at)
    : last ? `Ran ${ago(last.started_at)}` : "Not run yet";
  return h("a", { class: "entry", href: `#/automation/${a.id}` },
    h("span", {},
      h("div", { class: "entry-title" }, a.name),
      h("div", { class: "entry-sub" }, triggerText(a))),
    h("span", { class: "entry-side" }, last && status(RUN_STATUS, last.status),
      h("span", { class: "caption" }, side)));
}

// ---- draft -----------------------------------------------------------------

async function showDraft(id) {
  const draft = await api(`/api/drafts/${id}`);
  document.getElementById("nav").hidden = false;
  const fresh = draft.status === "proposed" && lastProposal !== draft.id;
  if (draft.status === "proposed") lastProposal = draft.id;
  show(
    backLink(),
    h("h1", {}, draftTitle(draft)),
    h("p", { class: "lede" },
      draft.kind === "repair" ? "Errand started this after a run failed. Nothing changes until you approve the fix."
        : draft.kind === "change" ? "Nothing changes until you approve the new version."
        : "Nothing is installed until you approve it."),
    h("div", { class: "convo" }, draft.transcript.map(convoLine),
      draft.status === "working" && h("div", { class: "line step" }, h("span", { class: "who" }), h("span", { class: "said working" }, "Working"))),
    draft.status === "error" && h("div", { class: "notice bad" }, h("p", {}, `Errand stopped: ${draft.error}`)),
    draft.proposal && draft.status === "proposed" && workOrder(draft, fresh),
    draft.status === "installed" && h("div", { class: "notice good" },
      h("p", {}, "Installed. ", h("a", { href: `#/automation/${draft.installed}` }, "Open the errand"))),
    !["installed", "discarded"].includes(draft.status) && replyForm(draft),
    h("div", { style: "margin-top:2rem" }, dryRunFold(draft), filesFold(draft.files)),
  );
  if (draft.status === "working") timer = setTimeout(render, 1500);
}

function convoLine(entry) {
  if (entry.role === "step") {
    return h("div", { class: "line step" }, h("span", { class: "who" }), h("span", { class: "said" }, entry.text));
  }
  const who = entry.role === "user" ? "You" : "Errand";
  return h("div", { class: `line ${entry.role}` }, h("span", { class: "who" }, who), h("span", { class: "said" }, entry.text));
}

function workOrder(draft, fresh) {
  const p = draft.proposal;
  const label = draft.target ? "Approve and update" : "Approve and install";
  const approve = h("button", { class: "primary" }, label);
  approve.addEventListener("click", async () => {
    setBusy(approve, true, "Installing");
    try {
      const automation = await api(`/api/drafts/${draft.id}/approve`, { method: "POST", body: {} });
      toast(draft.target ? "Updated" : "Installed", false);
      location.hash = `#/automation/${automation.id}`;
    } catch (err) { fail(err); setBusy(approve, false, label); }
  });
  const discard = h("button", {
    onclick: async () => {
      try { await api(`/api/drafts/${draft.id}/discard`, { method: "POST", body: {} }); location.hash = "#/"; }
      catch (err) { fail(err); }
    },
  }, "Discard");
  return h("section", { class: `card proposal ${fresh ? "fresh" : ""}`, "aria-label": "Proposal" },
    status(DRAFT_STATUS, "proposed"),
    h("h2", {}, p.name),
    h("p", {}, p.summary),
    h("dl", { class: "facts" },
      h("dt", {}, "Runs"), h("dd", {}, triggerText(p)),
      h("dt", {}, "Uses"), h("dd", {}, (p.services || []).join(", ") || "Nothing outside the Sprite"),
      h("dt", {}, "Time limit"), h("dd", {}, `${p.timeout_s} s`)),
    h("div", { class: "actions" },
      h("span", { class: "caption" }, "The dry run passed. Emails and changes in it were not sent."),
      approve, discard));
}

function dryRunFold(draft) {
  const runs = draft.dry_runs || [];
  if (!runs.length) return null;
  const last = runs[runs.length - 1];
  const word = last.status === "ok" ? "passed" : last.status === "timeout" ? "timed out" : "failed";
  return h("details", { class: "fold", open: draft.status === "proposed" },
    h("summary", {}, `Last dry run ${word} in ${last.seconds} s`),
    h("pre", {}, last.output || "(no output)"));
}

function filesFold(files) {
  const names = Object.keys(files || {});
  if (!names.length) return null;
  return h("details", { class: "fold" }, h("summary", {}, `Code: ${names.join(", ")}`),
    names.map((name) => [h("p", { class: "file-name" }, name), h("pre", {}, files[name])]));
}

function replyForm(draft) {
  const input = h("textarea", {
    "aria-label": "Your reply",
    placeholder: draft.status === "proposed" ? "Ask for a change before you approve" : "Reply to Errand",
  });
  const button = h("button", { class: "primary", type: "submit", disabled: draft.status === "working" }, "Send");
  return h("form", {
    style: "margin-top:24px",
    onsubmit: async (event) => {
      event.preventDefault();
      if (!input.value.trim()) { input.focus(); return; }
      setBusy(button, true, "Sending");
      try {
        await api(`/api/drafts/${draft.id}/messages`, { method: "POST", body: { message: input.value } });
        render();
      } catch (err) { fail(err); setBusy(button, false, "Send"); }
    },
  }, input, h("div", { class: "actions end" }, button));
}

// ---- automation ------------------------------------------------------------

async function showAutomation(id) {
  const a = await api(`/api/automations/${id}`);
  document.getElementById("nav").hidden = false;
  const running = a.runs.some((r) => r.status === "running");
  show(
    backLink(),
    h("h1", {}, a.name),
    h("p", { class: "lede" }, a.description),
    !a.enabled && h("p", {}, h("span", { class: "chip idle" }, h("span", { class: "dot" }), "Paused")),
    h("dl", { class: "card facts", style: "margin-top:22px" },
      h("dt", {}, "Runs"), h("dd", {}, triggerText(a)),
      a.next_run_at && a.enabled && [h("dt", {}, "Next run"), h("dd", { title: when(a.next_run_at) }, nextText(a.next_run_at).replace("Next run ", ""))],
      a.hook_path && [h("dt", {}, "Webhook URL"), h("dd", {}, h("code", {}, publicUrl + a.hook_path))],
      h("dt", {}, "Time limit"), h("dd", {}, `${a.timeout_s} s`)),
    h("div", { class: "actions" }, actionButtons(a)),
    a.drafts.length > 0 && [h("h2", {}, "Waiting on you"), h("div", { class: "card list" }, a.drafts.map(draftEntry))],
    h("h2", {}, "Ask for a change"),
    changeForm(a.id, "change", null, "For example: also include the link to the comments"),
    h("h2", {}, "Runs"),
    runsTable(a),
    h("h2", {}, "Versions"),
    versionsTable(a),
    h("div", { style: "margin-top:2rem" }, filesFold(a.files)),
  );
  if (running) timer = setTimeout(render, 3000);
}

function actionButtons(a) {
  const run = h("button", { class: "primary" }, "Run now");
  run.addEventListener("click", async () => {
    setBusy(run, true, "Starting");
    try {
      await api(`/api/automations/${a.id}/run`, { method: "POST", body: {} });
      toast("Run started", false);
      setTimeout(render, 800);
    } catch (err) { fail(err); setBusy(run, false, "Run now"); }
  });
  const pause = h("button", {
    onclick: async () => {
      try {
        await api(`/api/automations/${a.id}/enabled`, { method: "POST", body: { enabled: !a.enabled } });
        toast(a.enabled ? "Paused" : "Resumed", false);
        render();
      } catch (err) { fail(err); }
    },
  }, a.enabled ? "Pause" : "Resume");
  const remove = h("button", {
    class: "quiet danger",
    onclick: async () => {
      if (!confirm(`Delete "${a.name}"? Errand saves a checkpoint first, so you can restore it from Settings.`)) return;
      try { await api(`/api/automations/${a.id}`, { method: "DELETE" }); toast("Deleted", false); location.hash = "#/"; }
      catch (err) { fail(err); }
    },
  }, "Delete");
  return [run, pause, remove];
}

function changeForm(id, kind, runId, placeholder) {
  const repair = kind === "repair";
  const label = repair ? "Fix it" : "Ask Errand";
  const input = h("textarea", { placeholder, "aria-label": repair ? "What should have happened?" : "What should change?" });
  const button = h("button", { class: repair ? "" : "primary", type: "submit" }, label);
  return h("form", {
    class: repair ? "" : "card composer",
    onsubmit: async (event) => {
      event.preventDefault();
      const message = input.value.trim() || (repair ? "This run went wrong. Fix it." : "");
      if (!message) { input.focus(); return; }
      setBusy(button, true, "Starting");
      try {
        const body = { message, kind };
        if (runId !== null) body.run_id = runId;
        const draft = await api(`/api/automations/${id}/edit`, { method: "POST", body });
        location.hash = `#/draft/${draft.id}`;
      } catch (err) { fail(err); setBusy(button, false, label); }
    },
  }, input, h("div", { class: "actions" }, button));
}

function runsTable(a) {
  if (!a.runs.length) return h("div", { class: "card" }, h("p", { class: "empty" }, "No runs yet. Use Run now to try it."));
  const rows = a.runs.map((r) => {
    const detail = h("tr", { class: "detail", hidden: true }, h("td", { colspan: 5 },
      h("pre", {}, r.output || "(no output)"),
      h("p", { class: "small muted", style: "margin:0.9rem 0 0.4rem" }, "If this run did the wrong thing, tell Errand what you expected:"),
      changeForm(a.id, "repair", r.id, "Optional: what should have happened?")));
    const toggle = h("button", {
      class: "quiet", "aria-expanded": "false",
      onclick: (event) => {
        detail.hidden = !detail.hidden;
        event.currentTarget.setAttribute("aria-expanded", String(!detail.hidden));
        event.currentTarget.textContent = detail.hidden ? "Output" : "Hide";
      },
    }, "Output");
    const took = r.finished_at ? `${(r.finished_at - r.started_at).toFixed(1)} s` : "";
    return [h("tr", {},
      h("td", { class: "num" }, `#${r.id}`),
      h("td", {}, status(RUN_STATUS, r.status)),
      h("td", { title: when(r.started_at) }, ago(r.started_at)),
      h("td", { class: "num hide-narrow" }, took),
      h("td", { class: "end" }, toggle)), detail];
  });
  return h("div", { class: "card table-card" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Run"), h("th", {}, "Result"), h("th", {}, "Started"),
      h("th", { class: "hide-narrow" }, "Took"), h("th", { class: "end" }))),
    h("tbody", {}, rows)));
}

function versionsTable(a) {
  if (!a.versions.length) return h("div", { class: "card" }, h("p", { class: "empty" }, "No history."));
  return h("div", { class: "card table-card" }, h("table", {}, h("tbody", {},
    a.versions.map((v, index) => h("tr", {},
      h("td", {}, h("code", {}, v.sha.slice(0, 7))),
      h("td", {}, v.message),
      h("td", { class: "num hide-narrow", title: when(v.at) }, ago(v.at)),
      h("td", { class: "end" }, index === 0 ? h("span", { class: "caption" }, "Current") : h("button", {
        class: "quiet",
        onclick: async () => {
          if (!confirm(`Go back to version ${v.sha.slice(0, 7)}? Its saved state does not change.`)) return;
          try {
            await api(`/api/automations/${a.id}/rollback`, { method: "POST", body: { version: v.sha } });
            toast("Rolled back", false);
            render();
          } catch (err) { fail(err); }
        },
      }, "Roll back")))))));
}

// ---- settings --------------------------------------------------------------

async function showSettings() {
  const [session, checkpoints, network] = await Promise.all([
    api("/api/session"), api("/api/checkpoints").catch(() => []), api("/api/egress").catch(() => null),
  ]);
  document.getElementById("nav").hidden = false;
  const s = session.settings;
  const guess = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const email = h("input", { type: "email", id: "notify_email", value: s.notify_email || "", autocomplete: "email" });
  const zone = h("input", { type: "text", id: "timezone", value: s.timezone || "", placeholder: guess });
  const repair = h("input", { type: "checkbox", id: "auto_repair", checked: s.auto_repair !== "off" });
  const save = h("button", { class: "primary", type: "submit" }, "Save settings");
  const form = h("form", {
    class: "settings",
    onsubmit: async (event) => {
      event.preventDefault();
      const body = { auto_repair: repair.checked ? "on" : "off", timezone: zone.value.trim() || guess };
      if (email.value.trim()) body.notify_email = email.value.trim();
      setBusy(save, true, "Saving");
      try { await api("/api/settings", { method: "POST", body }); toast("Settings saved", false); }
      catch (err) { fail(err); }
      setBusy(save, false, "Save settings");
    },
  },
  h("div", { class: "field" }, h("label", { for: "notify_email" }, "Email for results and failures"), email,
    h("p", { class: "caption" }, "Without your own domain in Resend, this must be the address of your Resend account.")),
  h("div", { class: "field" }, h("label", { for: "timezone" }, "Timezone"), zone,
    h("p", { class: "caption" }, `Used for times like "every day at 8:00". Your browser says ${guess}.`)),
  h("div", { class: "field" }, h("label", { class: "check" }, repair, "When a run fails, let Errand prepare a fix for me to approve")),
  save);

  show(
    h("h1", {}, "Settings"),
    form,
    network && networkSection(network),
    h("h2", {}, "Checkpoints"),
    h("p", { class: "lede" }, "Errand saves a checkpoint of the whole Sprite before each install, update and delete. A restore puts every errand, run and setting back to that moment, and restarts the Sprite."),
    checkpointsTable(checkpoints),
    h("h2", {}, "Session"),
    h("button", {
      onclick: async () => {
        try { await api("/logout", { method: "POST" }); } catch (_) { /* 204 has no body */ }
        showLogin();
      },
    }, "Log out"),
  );
}

function networkSection(network) {
  const sites = network.wanted.length ? network.wanted.join(", ") : "none yet";
  const command = network.blocked.length
    ? `scripts/egress.sh allow ${network.blocked.join(" ")}` : "scripts/egress.sh sync";
  return [
    h("h2", {}, "Network"),
    h("div", { class: "card" },
      h("p", {}, status({ open: ["Open", "idle"], locked: ["Locked", "done"] }, network.mode),
        "  ", network.mode === "open"
          ? "Errands can reach any site."
          : "Errands can reach the platform, your connectors and the allowed sites only."),
      h("p", { class: "muted small" }, `Sites your errands use: ${sites}.`),
      network.blocked.length > 0 && h("div", { class: "notice bad" },
        h("p", {}, `The policy blocks ${network.blocked.join(", ")}. Errands that use them will fail.`)),
      h("p", { class: "caption", style: "margin-top:12px" },
        "The Sprite cannot change its own network policy. To lock it or allow a site, run this in the errand folder on your computer:"),
      h("pre", {}, command)),
  ];
}

function checkpointsTable(checkpoints) {
  const make = h("button", {
    onclick: async () => {
      try {
        const made = await api("/api/checkpoints", { method: "POST", body: { comment: "saved by hand" } });
        toast(`Checkpoint ${made.id} saved`, false);
        render();
      } catch (err) { fail(err); }
    },
  }, "Save a checkpoint now");
  const rows = checkpoints.slice(0, 15).map((c) => h("tr", {},
    h("td", {}, h("code", {}, c.id)),
    h("td", {}, (c.comment || "").replace(/^errand: /, "")),
    h("td", { class: "num hide-narrow" }, new Date(c.create_time).toLocaleString()),
    h("td", { class: "end" }, h("button", {
      class: "quiet danger",
      onclick: async () => {
        if (!confirm(`Restore checkpoint ${c.id}? Everything after it is lost, and the Sprite restarts. This page reloads in about 20 seconds.`)) return;
        try { await api(`/api/checkpoints/${c.id}/restore`, { method: "POST", body: {} }); }
        catch (err) { fail(err); return; }
        show(h("h1", {}, "Restoring"), h("p", { class: "working" }, "The Sprite is restarting"));
        setTimeout(() => { location.hash = "#/"; location.reload(); }, 20000);
      },
    }, "Restore"))));
  return [
    rows.length ? h("div", { class: "card table-card" }, h("table", {}, h("tbody", {}, rows)))
      : h("div", { class: "card" }, h("p", { class: "empty" }, "No checkpoints yet.")),
    h("div", { class: "actions" }, make),
  ];
}

// ---- start -----------------------------------------------------------------

api("/api/session").then((session) => {
  if (session.sprite_url) publicUrl = session.sprite_url.replace(/\/$/, "");
  render();
}).catch(() => { /* showLogin() already ran on 401 */ });
