const state = {
  token: localStorage.getItem("skein.token") || "",
};

const $ = (id) => document.getElementById(id);

function api(path, options = {}) {
  return fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Skein-Token": state.token,
      ...(options.headers || {}),
    },
  }).then(async (res) => {
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  });
}

function setStatus(text, cls = "muted") {
  const el = $("status");
  el.textContent = text;
  el.className = cls;
}

function card(label, value) {
  return `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`;
}

async function loadSummary() {
  const s = await api("/internal/summary");
  $("summary-cards").innerHTML = [
    card("事件", s.events.total),
    card("Thread", s.threads),
    card("工单", s.tickets.total),
    card("待验收", s.tickets.by_status.review || 0),
    card("会话", s.sessions),
    card("影子判定", Object.values(s.shadow.by_action).reduce((a, b) => a + b, 0)),
  ].join("");
}

async function loadTickets() {
  const { tickets } = await api("/internal/tickets?limit=100");
  $("tickets-body").innerHTML = tickets
    .map((t) => {
      const actions =
        t.status === "review"
          ? `<button class="action primary" data-review="approve" data-id="${t.id}">通过</button>
             <button class="action danger" data-review="reject" data-id="${t.id}">驳回</button>`
          : `<button class="action" data-dispatch="${t.id}">派单</button>`;
      const cls = t.status === "done" ? "status-ok" : t.status === "rejected" ? "status-bad" : "";
      return `<tr><td>${t.code}</td><td class="title">${escapeHtml(t.title)}</td>
        <td class="${cls}">${t.status}</td><td>${actions}</td></tr>`;
    })
    .join("");
}

async function loadNodes() {
  const { nodes } = await api("/internal/nodes");
  $("nodes-body").innerHTML = nodes
    .map((n) => {
      const cls = n.status === "online" ? "status-ok" : "status-bad";
      return `<tr><td>${n.id}</td><td>${escapeHtml(n.host)}</td><td>${n.platforms}</td>
        <td class="${cls}">${n.status}</td><td>${n.max_workers}</td>
        <td>${new Date(n.last_seen * 1000).toLocaleString()}</td></tr>`;
    })
    .join("");
}

async function loadRuns() {
  const { runs } = await api("/internal/runs?limit=30");
  $("runs-body").innerHTML = runs
    .map((r) => {
      const cls = r.status === "succeeded" ? "status-ok" : r.status === "failed" ? "status-bad" : "";
      return `<tr><td>${r.job}</td><td>${r.day}</td><td class="${cls}">${r.status}</td>
        <td>${escapeHtml(r.detail || "")}</td></tr>`;
    })
    .join("");
}

async function loadSignals() {
  const { signals } = await api("/internal/signals");
  $("signals-list").innerHTML = signals.length
    ? signals.map((s) => `<li>${escapeHtml(describeSignal(s))}</li>`).join("")
    : `<li class="muted">暂无信号</li>`;
}

function describeSignal(s) {
  if (s.kind === "dispatch_timeout") return `派单超时：工单 ${s.ticket_id}，节点 ${s.node_id}，已 ${Math.round(s.age_s / 60)} 分钟`;
  if (s.kind === "run_failed") return `作业失败：${s.job} ${s.day} ${s.detail || ""}`;
  if (s.kind === "effect_failed") return `写回失败：${s.effect_kind} ${s.object}`;
  if (s.kind === "node_offline_with_work") return `节点离线但有在途工单：${s.node_id} / ${s.ticket_id}`;
  return JSON.stringify(s);
}

async function loadPrompts() {
  const { prompts } = await api("/internal/prompts");
  $("prompts-body").innerHTML = prompts
    .map((p) => {
      const frozen = p.frozen_until ? new Date(p.frozen_until * 1000).toLocaleString() : "-";
      const actions = [
        p.canary_revision ? `<button class="action primary" data-promote="${p.part}">升格</button>` : "",
        p.canary_revision ? `<button class="action danger" data-rollback="${p.part}">回退</button>` : "",
        p.frozen_until ? `<button class="action" data-unfreeze="${p.part}">解冻</button>` : "",
      ].join("");
      return `<tr><td>${p.part}</td><td>${p.stable_revision}</td>
        <td>${p.canary_revision || "-"}</td><td>${frozen}</td><td>${actions}</td></tr>`;
    })
    .join("");
}

function escapeHtml(text) {
  return String(text || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

const loaders = {
  summary: loadSummary,
  tickets: loadTickets,
  nodes: loadNodes,
  runs: loadRuns,
  signals: loadSignals,
  prompts: loadPrompts,
};

async function show(view) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  try {
    await loaders[view]();
    setStatus("已连接", "status-ok");
  } catch (err) {
    setStatus(`失败：${err.message}`, "status-bad");
  }
}

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => show(btn.dataset.view));
});

$("token").value = state.token;
$("token").addEventListener("change", (event) => {
  state.token = event.target.value.trim();
  localStorage.setItem("skein.token", state.token);
  show("summary");
});

document.addEventListener("click", async (event) => {
  const target = event.target;
  try {
    if (target.dataset.review) {
      await api(`/internal/tickets/${target.dataset.id}/review`, {
        method: "POST",
        body: JSON.stringify({ decision: target.dataset.review }),
      });
      await loadTickets();
    } else if (target.dataset.dispatch) {
      await api(`/internal/tickets/${target.dataset.dispatch}/dispatch`, { method: "POST" });
      await loadTickets();
    } else if (target.dataset.promote) {
      await api("/internal/prompts/promote", {
        method: "POST",
        body: JSON.stringify({ part: target.dataset.promote }),
      });
      await loadPrompts();
    } else if (target.dataset.rollback) {
      await api("/internal/prompts/rollback", {
        method: "POST",
        body: JSON.stringify({ part: target.dataset.rollback }),
      });
      await loadPrompts();
    } else if (target.dataset.unfreeze) {
      await api("/internal/prompts/unfreeze", {
        method: "POST",
        body: JSON.stringify({ part: target.dataset.unfreeze }),
      });
      await loadPrompts();
    }
  } catch (err) {
    setStatus(`操作失败：${err.message}`, "status-bad");
  }
});

if (state.token) show("summary");
