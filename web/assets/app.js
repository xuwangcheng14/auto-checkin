/* 自动签到管理平台 - 前端逻辑(原生 JS, 无构建依赖) */
"use strict";

/* ---------------- 基础工具 ---------------- */
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function api(url, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch(url, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok || !body || body.ok === false) {
    throw new Error((body && body.message) || ("请求失败 (" + res.status + ")"));
  }
  return body.data;
}

function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = "toast" + (type === "error" ? " toast-error" : type === "success" ? " toast-success" : "");
  el.textContent = msg;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function fmtDuration(sec) {
  if (sec == null) return "—";
  if (sec < 60) return sec.toFixed(1).replace(/\.0$/, "") + " 秒";
  return Math.floor(sec / 60) + " 分 " + Math.round(sec % 60) + " 秒";
}

function statusBadge(status) {
  const map = {
    success: ["badge-success", "成功"],
    fail: ["badge-fail", "失败"],
    running: ["badge-running", "运行中"],
  };
  const [cls, label] = map[status] || ["badge-none", "未运行"];
  return '<span class="badge ' + cls + '"><span class="dot"></span>' + label + "</span>";
}

function triggerBadge(trigger) {
  return trigger === "manual"
    ? '<span class="badge badge-warn">手动</span>'
    : '<span class="badge badge-none">定时</span>';
}

/* ---------------- 弹窗 ---------------- */
function openModal(title, bodyHTML, footHTML, large) {
  closeModal();
  const root = $("#modal-root");
  root.innerHTML =
    '<div class="modal-overlay">' +
    '<div class="modal' + (large ? " modal-lg" : "") + '">' +
    '<div class="modal-head"><div class="modal-title">' + title + '</div>' +
    '<button class="modal-close" data-close="1">✕</button></div>' +
    '<div class="modal-body">' + bodyHTML + "</div>" +
    (footHTML ? '<div class="modal-foot">' + footHTML + "</div>" : "") +
    "</div></div>";
  root.querySelector(".modal-overlay").addEventListener("mousedown", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });
  $$("[data-close]", root).forEach((el) => el.addEventListener("click", closeModal));
  return root.querySelector(".modal");
}

function closeModal() { $("#modal-root").innerHTML = ""; }

/* ---------------- 配置获取指南 ---------------- */
/* 极简 Markdown 渲染: 先转义再处理标题/列表/表格/加粗/行内代码, 保证安全 */
function renderGuide(md) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const lines = md.split(/\r?\n/);
  const html = [];
  let tableBuf = [];
  const flushTable = () => {
    if (!tableBuf.length) return;
    const rows = tableBuf
      .filter((cells) => !cells.every((c) => /^:?-{2,}:?$/.test(c.trim()) || c.trim() === ""))
      .map((cells) => cells.map((c) => c.trim()));
    if (rows.length) {
      html.push("<table><thead><tr>" +
        rows[0].map((c) => "<th>" + inline(c) + "</th>").join("") + "</tr></thead><tbody>" +
        rows.slice(1).map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") +
        "</tbody></table>");
    }
    tableBuf = [];
  };
  for (const line of lines) {
    if (/^\s*\|.*\|\s*$/.test(line)) {
      tableBuf.push(line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|"));
      continue;
    }
    flushTable();
    if (/^###\s+/.test(line)) html.push("<h4>" + inline(line.replace(/^###\s+/, "")) + "</h4>");
    else if (/^##\s+/.test(line)) html.push("<h3>" + inline(line.replace(/^##\s+/, "")) + "</h3>");
    else if (/^#\s+/.test(line)) html.push("<h3>" + inline(line.replace(/^#\s+/, "")) + "</h3>");
    else if (/^\s*[-*]\s+/.test(line)) html.push("<li>" + inline(line.replace(/^\s*[-*]\s+/, "")) + "</li>");
    else if (/^\s*$/.test(line)) html.push("");
    else html.push("<p>" + inline(line) + "</p>");
  }
  flushTable();
  return '<div class="guide-body">' + html.join("") + "</div>";
}

function showGuide(channelId) {
  const meta = state.channels.find((c) => c.id === channelId);
  if (!meta || !meta.guide) return;
  openModal(esc(meta.name) + " · 配置获取指南", renderGuide(meta.guide),
    '<button class="btn btn-secondary" data-close="1">关闭</button>', true);
}

/* ---------------- 状态与路由 ---------------- */
const state = { channels: [], tasks: [], logFilter: { task_id: "", status: "", page: 1 } };

const pages = {
  dashboard: { title: "仪表盘", render: renderDashboard },
  tasks: { title: "任务管理", render: renderTasks },
  logs: { title: "执行日志", render: renderLogs },
  channels: { title: "渠道管理", render: renderChannels },
  guide: { title: "配置指南", render: renderGuidePage },
};

function currentPage() {
  const name = (location.hash || "#/dashboard").replace(/^#\//, "");
  return pages[name] ? name : "dashboard";
}

window.addEventListener("hashchange", route);

function route() {
  const name = currentPage();
  $$(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  closeModal();
  pages[name].render().catch((e) => {
    $("#view").innerHTML = '<div class="card card-pad" style="color:var(--danger)">' + esc(e.message) + "</div>";
  });
}

/* 每 5 秒静默刷新当前页(弹窗打开时不刷新) */
setInterval(() => {
  if (document.hidden || $("#modal-root").children.length) return;
  const name = currentPage();
  if (name === "dashboard" || name === "tasks" || name === "logs") {
    pages[name].render().catch(() => {});
  }
}, 5000);

/* ---------------- 仪表盘 ---------------- */
async function renderDashboard() {
  const [stats, logs] = await Promise.all([
    api("/api/stats"),
    api("/api/logs?page=1&page_size=10"),
  ]);
  const onboard = stats.tasks_total === 0
    ? '<div class="card onboard">' +
      '<span class="empty-icon">🚀</span><h3>欢迎使用自动签到管理平台</h3>' +
      "<p>第一步: 在「渠道管理」确认已有可用渠道<br>" +
      "第二步: 在「任务管理」新建签到任务并配置定时规则</p>" +
      '<a class="btn" href="#/tasks">去新建任务</a></div>'
    : "";

  $("#view").innerHTML =
    '<div class="page-header"><div class="page-title">仪表盘</div>' +
    '<div class="page-actions"><button class="btn btn-secondary btn-sm" id="btn-refresh">↻ 刷新</button></div></div>' +
    '<div class="stat-grid">' +
    statCard(stats.tasks_total, "任务总数") +
    statCard(stats.tasks_enabled, "启用中") +
    statCard(stats.today_success, "今日成功", "stat-success") +
    statCard(stats.today_fail, "今日失败", "stat-danger") +
    statCard(stats.channels_total, "可用渠道", "stat-primary") +
    "</div>" + onboard +
    '<div class="section-title">最近执行</div>' +
    '<div class="card"><div class="table-wrap"><table><thead><tr>' +
    "<th>时间</th><th>任务</th><th>触发</th><th>状态</th><th>耗时</th><th>结果</th></tr></thead><tbody>" +
    (logs.items.length
      ? logs.items.map(logRowSimple).join("")
      : '<tr><td colspan="6" class="empty"><span class="empty-icon">📭</span>暂无执行记录</td></tr>') +
    "</tbody></table></div></div>";
  $("#btn-refresh").addEventListener("click", () => renderDashboard());
}

function statCard(value, label, extra) {
  return '<div class="stat-card"><div class="stat-value ' + (extra || "") + '">' + value +
    '</div><div class="stat-label">' + label + "</div></div>";
}

function logRowSimple(l) {
  return "<tr>" +
    "<td>" + esc(l.started_at) + "</td>" +
    '<td class="td-main">' + esc(l.task_name || "(已删除任务)") + "</td>" +
    "<td>" + triggerBadge(l.trigger) + "</td>" +
    "<td>" + statusBadge(l.status) + "</td>" +
    "<td>" + fmtDuration(l.duration) + "</td>" +
    '<td><div class="td-sub" style="max-width:340px">' + esc(l.message || "—") + "</div></td></tr>";
}

/* ---------------- 任务管理 ---------------- */
/* 按渠道分组, 每个渠道渲染一个独立子表格 */
function groupTasksByChannel(tasks) {
  const groups = new Map();
  for (const t of tasks) {
    if (!groups.has(t.channel)) {
      groups.set(t.channel, {
        channel: t.channel,
        name: t.channel_name || t.channel,
        valid: !!t.channel_valid,
        tasks: [],
      });
    }
    groups.get(t.channel).tasks.push(t);
  }
  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name, "zh"));
}

function taskGroupCard(g) {
  return '<div class="card task-group">' +
    '<div class="task-group-head">' +
    '<span class="task-group-name">' + esc(g.name) + "</span>" +
    (g.valid ? "" : '<span class="badge badge-fail">渠道异常</span>') +
    '<span class="task-group-count">' + g.tasks.length + " 个任务</span>" +
    "</div>" +
    '<div class="table-wrap"><table><thead><tr>' +
    "<th>任务</th><th>定时规则</th><th>启用</th><th>上次运行</th><th>下次运行</th><th>令牌有效期</th><th>剩余积分</th><th>状态</th><th style='text-align:right'>操作</th>" +
    "</tr></thead><tbody>" + g.tasks.map(taskRow).join("") + "</tbody></table></div></div>";
}

async function renderTasks() {
  const tasks = await api("/api/tasks");
  state.tasks = tasks;
  const groups = groupTasksByChannel(tasks);
  const body = groups.length
    ? groups.map(taskGroupCard).join("")
    : '<div class="card card-empty"><span class="empty-icon">📋</span>还没有任务, 点击右上角「新建任务」开始</div>';
  $("#view").innerHTML =
    '<div class="page-header"><div class="page-title">任务管理</div>' +
    '<div class="page-actions"><button class="btn btn-sm" id="btn-run-all">▶ 一键执行</button>' +
    '<button class="btn btn-secondary btn-sm" id="btn-refresh">↻ 刷新</button>' +
    '<button class="btn btn-sm" id="btn-new">＋ 新建任务</button></div></div>' +
    body;

  $("#btn-new").addEventListener("click", () => openTaskModal());
  $("#btn-refresh").addEventListener("click", () => renderTasks());
  $("#btn-run-all").addEventListener("click", () => runAllTasks());
  bindTableActions($("#view"), {
    run: (id) => runTaskNow(id),
    edit: (id) => openTaskModal(state.tasks.find((t) => t.id === id)),
    del: (id) => deleteTask(id),
    logs: (id) => { state.logFilter = { task_id: id, status: "", page: 1 }; location.hash = "#/logs"; },
    toggle: async (id, checked) => {
      try { await api("/api/tasks/" + id, { method: "PUT", body: { enabled: checked } }); }
      catch (e) { toast(e.message, "error"); renderTasks(); }
    },
  });
}

function taskRow(t) {
  const statusCell = t.running
    ? statusBadge("running")
    : t.last_status
      ? statusBadge(t.last_status)
      : '<span class="badge badge-none">未运行</span>';
  return "<tr>" +
    '<td class="td-main">' + esc(t.name) + "</td>" +
    '<td><code style="font-size:12px">' + esc(t.cron) + "</code></td>" +
    '<td><label class="switch"><input type="checkbox" data-action="toggle" data-id="' + t.id + '"' + (t.enabled ? " checked" : "") + '><span class="slider"></span></label></td>' +
    "<td>" + (t.last_run_at ? esc(t.last_run_at) : "—") + "</td>" +
    "<td>" + (t.enabled ? (t.next_run_at ? esc(t.next_run_at) : "—") : '<span style="color:var(--text-sub)">已停用</span>') + "</td>" +
    "<td>" + expiresCell(t) + "</td>" +
    "<td>" + balanceCell(t) + "</td>" +
    "<td>" + statusCell + (t.last_message ? '<div class="td-sub">' + esc(t.last_message) + "</div>" : "") + "</td>" +
    '<td class="td-actions">' +
    '<button class="btn btn-secondary btn-sm btn-icon" data-action="run" data-id="' + t.id + '" title="立即运行"' + (t.running ? " disabled" : "") + ">▶</button> " +
    '<button class="btn btn-secondary btn-sm btn-icon" data-action="logs" data-id="' + t.id + '" title="查看日志">📜</button> ' +
    '<button class="btn btn-secondary btn-sm btn-icon" data-action="edit" data-id="' + t.id + '" title="编辑">✎</button> ' +
    '<button class="btn btn-secondary btn-sm btn-icon" data-action="del" data-id="' + t.id + '" title="删除">🗑</button>' +
    "</td></tr>";
}

/* 令牌有效期列: 渠道上报的 JSON 数组 [{label, expires_at}], 按剩余天数着色 */
function expiresCell(t) {
  const dash = '<span style="color:var(--text-sub)">—</span>';
  if (!t.expires_info) return dash;
  let items;
  try { items = JSON.parse(t.expires_info); } catch (e) { return dash; }
  if (!Array.isArray(items) || !items.length) return dash;
  const now = Date.now();
  return items.map((it) => {
    const d = new Date(String(it.expires_at || "").replace(" ", "T"));
    if (isNaN(d.getTime())) return '<div class="exp-item">' + esc(it.label || "") + "</div>";
    const days = Math.ceil((d.getTime() - now) / 86400000);
    let cls, text;
    if (days < 0) { cls = "exp-danger"; text = "已过期"; }
    else if (days < 7) { cls = "exp-danger"; text = "剩" + days + "天"; }
    else if (days < 30) { cls = "exp-warn"; text = "剩" + days + "天"; }
    else { cls = "exp-ok"; text = "剩" + days + "天"; }
    const dateStr = String(it.expires_at).slice(0, 16);
    return '<div class="exp-item ' + cls + '">' + esc(it.label || "") +
      " " + esc(dateStr) + " <b>" + text + "</b></div>";
  }).join("");
}

/* 剩余积分列: 渠道上报的 JSON 数组 [{label, value}] */
function balanceCell(t) {
  const dash = '<span style="color:var(--text-sub)">—</span>';
  if (!t.balance_info) return dash;
  let items;
  try { items = JSON.parse(t.balance_info); } catch (e) { return dash; }
  if (!Array.isArray(items) || !items.length) return dash;
  return items.map((it) =>
    '<div class="exp-item bal-item">' + esc(it.label || "") + " <b>" + esc(it.value) + "</b></div>"
  ).join("");
}

function bindTableActions(root, handlers) {
  $$("[data-action]", root).forEach((el) => {
    el.addEventListener(el.type === "checkbox" ? "change" : "click", async (e) => {
      const id = Number(el.dataset.id);
      const fn = handlers[el.dataset.action];
      if (fn) await fn(id, el.checked, e);
    });
  });
}

async function runTaskNow(id) {
  try {
    await api("/api/tasks/" + id + "/run", { method: "POST" });
    toast("已触发执行, 稍后查看日志", "success");
    renderTasks();
  } catch (e) { toast(e.message, "error"); }
}

/* 一键执行: 触发全部启用中的任务(服务端自动跳过正在运行中的) */
async function runAllTasks() {
  const enabled = state.tasks.filter((t) => t.enabled);
  if (!enabled.length) { toast("没有启用中的任务", "error"); return; }
  const btn = $("#btn-run-all");
  if (!confirm("确定立即执行全部 " + enabled.length + " 个启用中的任务?")) return;
  btn.disabled = true;
  btn.textContent = "触发中…";
  try {
    const r = await api("/api/tasks/run-all", { method: "POST" });
    toast(r.message || "已触发全部任务", r.accepted ? "success" : "info");
  } catch (e) { toast(e.message, "error"); }
  renderTasks();
}

async function deleteTask(id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!confirm("确定删除任务「" + (task ? task.name : id) + "」? 其执行日志也会一并删除。")) return;
  try {
    await api("/api/tasks/" + id, { method: "DELETE" });
    toast("已删除", "success");
    renderTasks();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------------- 新建/编辑任务弹窗 ---------------- */
const CRON_PRESETS = [
  ["每天 09:30", "30 9 * * *"],
  ["每天 09:30 / 20:30", "30 9,20 * * *"],
  ["每 12 小时", "0 */12 * * *"],
  ["每小时", "0 * * * *"],
  ["工作日 09:30", "30 9 * * 1-5"],
];

async function openTaskModal(task) {
  if (!state.channels.length) {
    state.channels = await api("/api/channels");
  }
  const validChannels = state.channels.filter((c) => c.valid);
  if (!validChannels.length) {
    toast("没有可用渠道, 请先在 channels/ 目录添加渠道脚本", "error");
    return;
  }

  const editing = !!task;
  const selectedMeta = validChannels.find((c) => c.id === (task ? task.channel : validChannels[0].id)) || validChannels[0];
  const options = validChannels
    .map((c) => '<option value="' + esc(c.id) + '"' + (task && task.channel === c.id ? " selected" : "") + ">" +
      esc(c.name) + "</option>").join("");

  openModal(editing ? "编辑任务" : "新建任务",
    '<div class="form-item"><label>签到渠道 <span class="req">*</span></label>' +
    '<select id="f-channel"' + (editing ? " disabled" : "") + ">" + options + "</select>" +
    (editing ? '<div class="form-help">编辑时不允许更换渠道(如需更换请新建任务)</div>' : "") + "</div>" +

    '<div class="form-item"><label>任务名称 <span class="req">*</span></label>' +
    '<input type="text" id="f-name" maxlength="100" value="' + esc(task ? task.name : "") + '" placeholder="例如: WorkBuddy 每日签到"></div>' +

    '<div class="form-item"><label>定时规则(cron) <span class="req">*</span></label>' +
    '<input type="text" id="f-cron" value="' + esc(task ? task.cron : "30 9 * * *") + '" placeholder="分 时 日 月 周, 例如 30 9 * * *">' +
    '<div class="cron-presets" id="cron-presets">' +
    CRON_PRESETS.map(([label, expr]) => '<button type="button" data-cron="' + expr + '">' + label + "</button>").join("") +
    "</div>" +
    '<div class="cron-preview" id="cron-preview"></div></div>' +

    '<div class="form-row">' +
    '<div class="form-item"><label>超时时间(秒)</label>' +
    '<input type="number" id="f-timeout" min="5" max="86400" value="' + (task ? task.timeout : 300) + '">' +
    '<div class="form-help">超过此时间进程会被强制终止</div></div>' +
    '<div class="form-item"><label>启用状态</label>' +
    '<label class="check-line"><input type="checkbox" id="f-enabled"' + (!task || task.enabled ? " checked" : "") + "> 启用后按定时规则自动执行</label></div>" +
    "</div>" +

    '<div class="section-title" style="margin-top:6px">渠道配置' +
    (selectedMeta.guide ? '&nbsp;<a href="javascript:void(0)" id="f-guide" style="font-size:12.5px;font-weight:400">如何获取这些信息?</a>' : "") + "</div>" +
    '<div id="f-config"></div>',

    '<button class="btn btn-secondary" data-close="1">取消</button>' +
    '<button class="btn" id="f-save">' + (editing ? "保存修改" : "创建任务") + "</button>");

  const rebuildConfig = () => {
    const meta = validChannels.find((c) => c.id === $("#f-channel").value) || validChannels[0];
    renderConfigFields($("#f-config"), meta, task ? task.config : {});
  };
  $("#f-channel").addEventListener("change", rebuildConfig);
  rebuildConfig();

  $("#cron-presets").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cron]");
    if (!btn) return;
    $("#f-cron").value = btn.dataset.cron;
    previewCron();
  });

  const guideBtn = $("#f-guide");
  if (guideBtn) guideBtn.addEventListener("click", () => showGuide($("#f-channel").value));

  let timer = null;
  $("#f-cron").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(previewCron, 350);
  });
  previewCron();

  $("#f-save").addEventListener("click", () => saveTask(editing ? task.id : null));
}

function renderConfigFields(container, meta, existing) {
  // config 可能是 JSON 字符串(API 原样返回), 统一解析为对象
  if (typeof existing === "string") {
    try { existing = JSON.parse(existing); } catch (e) { existing = {}; }
  }
  existing = existing || {};
  if (!meta.config_schema.length) {
    container.innerHTML = '<div class="form-help">该渠道无需配置</div>';
    return;
  }
  container.innerHTML = meta.config_schema.map((f) => {
    const val = existing[f.key] !== undefined && existing[f.key] !== "" ? existing[f.key] : f.default;
    const reqMark = f.required ? ' <span class="req">*</span>' : "";
    let control = "";
    if (f.type === "textarea") {
      control = '<textarea id="cfg-' + esc(f.key) + '" rows="4" placeholder="' + esc(f.placeholder) + '">' + esc(val) + "</textarea>";
    } else if (f.type === "bool") {
      control = '<label class="check-line"><input type="checkbox" id="cfg-' + esc(f.key) + '"' + (val && val !== "false" ? " checked" : "") + "> 启用</label>";
    } else if (f.type === "select") {
      control = '<select id="cfg-' + esc(f.key) + '">' + f.options.map((o) =>
        '<option value="' + esc(o.value) + '"' + (String(val) === o.value ? " selected" : "") + ">" + esc(o.label) + "</option>").join("") + "</select>";
    } else if (f.type === "number") {
      control = '<input type="number" id="cfg-' + esc(f.key) + '" value="' + esc(val) + '" placeholder="' + esc(f.placeholder) + '">';
    } else if (f.type === "password") {
      control = '<span class="pw-wrap">' +
        '<input type="password" id="cfg-' + esc(f.key) + '" value="' + esc(val) + '" placeholder="' + esc(f.placeholder) + '" autocomplete="new-password">' +
        '<button type="button" class="pw-toggle" data-target="cfg-' + esc(f.key) + '" title="显示/隐藏">👁</button></span>';
    } else {
      control = '<input type="text" id="cfg-' + esc(f.key) + '" value="' + esc(val) + '" placeholder="' + esc(f.placeholder) + '">';
    }
    return '<div class="form-item"><label>' + esc(f.label) + reqMark + "</label>" + control +
      (f.help ? '<div class="form-help">' + esc(f.help) + "</div>" : "") + "</div>";
  }).join("");

  /* 凭证字段: 显示/隐藏明文切换 */
  $$(".pw-toggle", container).forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      input.type = input.type === "password" ? "text" : "password";
    });
  });
}

function collectConfig(meta) {
  const config = {};
  for (const f of meta.config_schema) {
    const el = $("#cfg-" + f.key);
    if (!el) continue;
    if (f.type === "bool") { config[f.key] = el.checked; continue; }
    let v = el.value;
    if (f.type === "number") { v = v === "" ? "" : Number(v); }
    if (!(v === "" && !f.required)) config[f.key] = v;
  }
  return config;
}

async function previewCron() {
  const box = $("#cron-preview");
  const expr = $("#f-cron").value.trim();
  if (!expr) { box.textContent = ""; return; }
  try {
    const data = await api("/api/cron/next?expr=" + encodeURIComponent(expr));
    box.className = "cron-preview";
    box.textContent = data.times.length
      ? "接下来执行: " + data.times.join(" · ")
      : "该规则在未来一年内没有可执行的时间";
  } catch (e) {
    box.className = "cron-preview error";
    box.textContent = e.message;
  }
}

async function saveTask(taskId) {
  const channelId = $("#f-channel").value;
  const meta = state.channels.find((c) => c.id === channelId);
  if (!meta) return;

  const name = $("#f-name").value.trim();
  if (!name) { toast("请填写任务名称", "error"); return; }
  const cronExpr = $("#f-cron").value.trim();
  if (!cronExpr) { toast("请填写定时规则", "error"); return; }

  for (const f of meta.config_schema) {
    if (!f.required) continue;
    const el = $("#cfg-" + f.key);
    if (el && f.type !== "bool" && !String(el.value).trim()) {
      toast("请填写「" + f.label + "」", "error");
      el.focus();
      return;
    }
  }

  const body = {
    channel: channelId,
    name,
    cron: cronExpr,
    timeout: Number($("#f-timeout").value) || 300,
    enabled: $("#f-enabled").checked,
    config: meta ? collectConfig(meta) : {},
  };

  try {
    if (taskId) {
      await api("/api/tasks/" + taskId, { method: "PUT", body });
      toast("任务已更新", "success");
    } else {
      await api("/api/tasks", { method: "POST", body });
      toast("任务已创建", "success");
    }
    closeModal();
    renderTasks();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------------- 执行日志 ---------------- */
async function renderLogs() {
  const f = state.logFilter;
  const [tasks, data] = await Promise.all([
    state.tasks.length ? Promise.resolve(state.tasks) : api("/api/tasks"),
    api("/api/logs?page=" + f.page + "&page_size=20" +
      (f.task_id ? "&task_id=" + f.task_id : "") +
      (f.status ? "&status=" + f.status : "")),
  ]);
  state.tasks = tasks;

  const taskOptions = '<option value="">全部任务</option>' + tasks.map((t) =>
    '<option value="' + t.id + '"' + (String(f.task_id) === String(t.id) ? " selected" : "") + ">" +
    esc(t.name) + "</option>").join("");
  const statusOptions = [["", "全部状态"], ["success", "成功"], ["fail", "失败"], ["running", "运行中"]]
    .map(([v, label]) => '<option value="' + v + '"' + (f.status === v ? " selected" : "") + ">" + label + "</option>").join("");
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));

  const rows = data.items.length
    ? data.items.map((l) => "<tr>" +
      "<td>" + esc(l.started_at) + "</td>" +
      '<td class="td-main">' + esc(l.task_name || "(已删除任务)") + "</td>" +
      "<td>" + triggerBadge(l.trigger) + "</td>" +
      "<td>" + statusBadge(l.status) + "</td>" +
      "<td>" + fmtDuration(l.duration) + "</td>" +
      '<td><div class="td-sub" style="max-width:300px">' + esc(l.message || "—") + "</div></td>" +
      '<td class="td-actions"><button class="btn btn-secondary btn-sm" data-log="' + l.id + '">详情</button></td></tr>').join("")
    : '<tr><td colspan="7" class="empty"><span class="empty-icon">📭</span>暂无日志</td></tr>';

  $("#view").innerHTML =
    '<div class="page-header"><div class="page-title">执行日志</div>' +
    '<div class="page-actions"><button class="btn btn-secondary btn-sm" id="btn-refresh">↻ 刷新</button></div></div>' +
    '<div class="filter-bar">' +
    '<select id="filter-task">' + taskOptions + "</select>" +
    '<select id="filter-status">' + statusOptions + "</select>" +
    '<span style="color:var(--text-sub);font-size:12.5px">共 ' + data.total + " 条</span></div>" +
    '<div class="card"><div class="table-wrap"><table><thead><tr>' +
    "<th>时间</th><th>任务</th><th>触发</th><th>状态</th><th>耗时</th><th>结果</th><th style='text-align:right'>操作</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
    '<div class="pager">' +
    '<button class="btn btn-secondary btn-sm" id="pg-prev"' + (data.page <= 1 ? " disabled" : "") + ">上一页</button>" +
    "<span>" + data.page + " / " + totalPages + "</span>" +
    '<button class="btn btn-secondary btn-sm" id="pg-next"' + (data.page >= totalPages ? " disabled" : "") + ">下一页</button>" +
    "</div></div>";

  $("#filter-task").addEventListener("change", (e) => { f.task_id = e.target.value; f.page = 1; renderLogs(); });
  $("#filter-status").addEventListener("change", (e) => { f.status = e.target.value; f.page = 1; renderLogs(); });
  $("#btn-refresh").addEventListener("click", () => renderLogs());
  $("#pg-prev").addEventListener("click", () => { if (f.page > 1) { f.page--; renderLogs(); } });
  $("#pg-next").addEventListener("click", () => { if (f.page < totalPages) { f.page++; renderLogs(); } });
  $$("[data-log]").forEach((btn) => btn.addEventListener("click", () => showLogDetail(Number(btn.dataset.log))));
}

async function showLogDetail(logId) {
  const l = await api("/api/logs/" + logId);
  openModal(
    "执行详情 #" + l.id,
    '<table style="margin-bottom:14px"><tbody>' +
    "<tr><td style='color:var(--text-sub);width:90px'>任务</td><td class='td-main'>" + esc(l.task_name || "(已删除任务)") + "</td></tr>" +
    "<tr><td style='color:var(--text-sub)'>开始时间</td><td>" + esc(l.started_at) + "</td></tr>" +
    "<tr><td style='color:var(--text-sub)'>结束时间</td><td>" + esc(l.finished_at || "—") + "</td></tr>" +
    "<tr><td style='color:var(--text-sub)'>耗时</td><td>" + fmtDuration(l.duration) + "</td></tr>" +
    "<tr><td style='color:var(--text-sub)'>状态</td><td>" + statusBadge(l.status) + " " + esc(l.message || "") + "</td></tr>" +
    "</tbody></table>" +
    '<div style="font-weight:600;font-size:13px;margin-bottom:8px">脚本输出</div>' +
    '<div class="log-output">' + esc(l.output || "(无输出)") + "</div>",
    '<button class="btn btn-secondary" data-close="1">关闭</button>', true);
}

/* ---------------- 配置指南(面向所有用户) ---------------- */
/* 汇总全部渠道的凭证获取教程: 通用概念 -> 渠道速查表 -> 逐渠道详细步骤 */
function guideBasicsHTML(channelCount) {
  return (
    '<div class="card card-pad" style="margin-bottom:14px">' +
    '<div style="font-weight:700;font-size:15px;margin-bottom:8px">① 开始之前: 3 分钟了解「凭证」</div>' +
    '<p style="margin:0 0 8px">签到网站怎么确认「你是你」？靠的就是<b>凭证</b>——它相当于一张临时门禁卡，' +
    "本平台拿着它替你去网站执行签到。凭证不用背、不用记，只需要按下面的方法「复制一次，长期使用」。</p>" +
    "<p style=\"margin:0 0 8px\">常见的凭证有三种形态：</p>" +
    "<table><thead><tr><th>形态</th><th>长什么样</th><th>一般从哪来</th></tr></thead><tbody>" +
    "<tr><td><b>Cookie</b></td><td>很长的一串文本，肉眼读不懂</td><td>登录网站后，按 F12 从请求头里复制</td></tr>" +
    "<tr><td><b>Token / 令牌</b></td><td>带固定前缀的代码，如 <code>pt-</code>、<code>dt-</code> 开头</td><td>网站「设置」页里生成，或 F12 请求头里复制</td></tr>" +
    "<tr><td><b>账号信息</b></td><td>账号列表 / 本机文件</td><td>按渠道教程填写，个别渠道可自动读取</td></tr>" +
    "</tbody></table>" +
    "</div>" +

    '<div class="card card-pad" style="margin-bottom:14px">' +
    '<div style="font-weight:700;font-size:15px;margin-bottom:8px">② 万能三步法（适用于大多数网站）</div>' +
    "<p style='margin:0 0 6px'><b>第 1 步</b>：用 Chrome / Edge 打开目标网站并登录，确认右上角是已登录状态。</p>" +
    "<p style='margin:0 0 6px'><b>第 2 步</b>：按 <b>F12</b> 打开开发者工具 → 点顶部 <b>Network（网络）</b>标签 → 按 <b>F5</b> 刷新页面 → 在请求列表里随便点一个发往该网站的请求 → 右侧切到 <b>Headers（标头）</b>。</p>" +
    "<p style='margin:0 0 6px'><b>第 3 步</b>：下拉到 <b>Request Headers（请求标头）</b>——要 Cookie 就复制 <code>Cookie:</code> 这一行的整串值；要 Token 就复制 <code>Authorization: Bearer</code> 后面那一串。粘贴到本平台对应任务的配置框即可。</p>" +
    '<div class="form-help">不同渠道的细节略有差异（点哪个网站、复制哪一行），请以下方各渠道的详细教程为准。</div>' +
    "</div>" +

    '<div class="card card-pad" style="margin-bottom:14px">' +
    '<div style="font-weight:700;font-size:15px;margin-bottom:8px">③ 安全须知（请务必阅读）</div>' +
    "<ul style='margin:0;padding-left:20px'>" +
    "<li>凭证等同于账号密码，<b>谁拿到谁就能进你的账号</b>。</li>" +
    "<li>凭证只填在本平台的任务配置里，<b>不要发给任何人、不要贴到群聊或公开场合</b>。</li>" +
    "<li>怀疑泄露时，去对应网站退出全部登录 / 修改密码 / 重新生成令牌，旧凭证会立即作废。</li>" +
    "<li>凭证都有有效期（几天到几个月不等），过期后任务会报「凭证失效」，按下方对应渠道的教程重新获取一次即可。</li>" +
    "</ul>" +
    '<div class="form-help">当前平台共有 ' + channelCount + " 个可用渠道，教程见下方。</div>" +
    "</div>"
  );
}

async function renderGuidePage() {
  const channels = await api("/api/channels");
  state.channels = channels;
  const valid = channels.filter((c) => c.valid);
  const withGuide = valid.filter((c) => c.guide);
  const withoutGuide = valid.filter((c) => !c.guide);

  /* 速查表 */
  const quickRows = withGuide.map((c) => {
    const reqFields = (c.config_schema || []).filter((f) => f.required).map((f) => f.label);
    const optCount = (c.config_schema || []).length - reqFields.length;
    const need = reqFields.length
      ? reqFields.map(esc).join("、") + (optCount > 0 ? '<span class="form-help">（另有 ' + optCount + " 个可选项）</span>" : "")
      : '<span class="form-help">无需配置</span>';
    return "<tr>" +
      '<td class="td-main">' + esc(c.name) + "</td>" +
      '<td><div class="td-sub" style="max-width:340px">' + esc(c.description || "") + "</div></td>" +
      "<td>" + need + "</td>" +
      '<td class="td-actions"><button class="btn btn-secondary btn-sm" data-goto="guide-' + esc(c.id) + '">查看教程</button></td>' +
      "</tr>";
  }).join("");

  /* 逐渠道详细教程 */
  const sections = channels.map((c) => {
    const body = c.valid
      ? (c.guide
        ? renderGuide(c.guide)
        : '<div class="form-help">该渠道暂无图文教程，可参考渠道目录内的 README.md，或在「渠道管理」页查看配置字段说明。</div>')
      : '<div class="form-help">该渠道当前加载失败，请先联系平台维护者处理。</div>';
    return '<div class="card card-pad" id="guide-' + esc(c.id) + '" style="margin-bottom:14px;scroll-margin-top:16px">' +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">' +
      '<span style="font-weight:700;font-size:15px">' + esc(c.name) + "</span>" +
      '<span class="channel-version">v' + esc(c.version) + "</span>" +
      (c.valid
        ? '<span class="badge badge-success"><span class="dot"></span>可用</span>'
        : '<span class="badge badge-fail"><span class="dot"></span>加载失败</span>') +
      "</div>" +
      '<div class="form-help" style="margin-bottom:10px">' + esc(c.description || "") + "</div>" +
      body +
      "</div>";
  }).join("");

  $("#view").innerHTML =
    '<div class="page-header"><div class="page-title">配置指南<small>手把手教你拿到各渠道需要的凭证, 全程约 1 分钟/渠道</small></div>' +
    '<div class="page-actions"><button class="btn btn-secondary btn-sm" id="btn-refresh">↻ 刷新</button></div></div>' +
    guideBasicsHTML(valid.length) +
    '<div class="section-title">渠道速查表</div>' +
    '<div class="card" style="margin-bottom:14px"><div class="table-wrap"><table><thead><tr>' +
    "<th>渠道</th><th>签到内容</th><th>需要准备</th><th style='text-align:right'>详细教程</th>" +
    "</tr></thead><tbody>" +
    (quickRows || '<tr><td colspan="4" class="empty">暂无可用渠道</td></tr>') +
    "</tbody></table></div></div>" +
    (withoutGuide.length
      ? '<div class="form-help" style="margin-bottom:8px">以下渠道暂无图文教程：' +
        withoutGuide.map((c) => esc(c.name)).join("、") + "</div>"
      : "") +
    '<div class="section-title">各渠道详细教程</div>' +
    sections;

  $("#btn-refresh").addEventListener("click", () => renderGuidePage());
  $$("[data-goto]").forEach((btn) => btn.addEventListener("click", () => {
    const el = document.getElementById(btn.dataset.goto);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

/* ---------------- 渠道管理 ---------------- */
async function renderChannels() {
  const channels = await api("/api/channels");
  state.channels = channels;
  const cards = channels.map((c) => {
    const fields = c.valid && c.config_schema.length
      ? '<div class="channel-fields">' + c.config_schema.map((f) =>
        '<div class="channel-field"><b>' + esc(f.label) + "</b>" +
        '<span class="field-type">' + esc(f.type) + (f.required ? " · 必填" : "") + "</span>" +
        (f.help ? "<span>" + esc(f.help) + "</span>" : "") + "</div>").join("") + "</div>"
      : "";
    return '<div class="channel-card">' +
      '<div class="channel-head"><div><span class="channel-name">' + esc(c.name) +
      '</span><span class="channel-version">v' + esc(c.version) + "</span>" +
      (c.author ? '<div class="channel-version">by ' + esc(c.author) + "</div>" : "") + "</div>" +
      (c.valid ? '<span class="badge badge-success"><span class="dot"></span>正常</span>'
               : '<span class="badge badge-fail"><span class="dot"></span>加载失败</span>') + "</div>" +
      '<div class="channel-desc">' + esc(c.description || "无描述") + "</div>" + fields +
      (c.valid ? "" : '<div class="channel-error">⚠ ' + esc(c.error || "加载失败") + "</div>") +
      '<div class="form-help" style="margin-top:10px">目录: channels/' + esc(c.id) + "/channel.py</div>" +
      (c.guide ? '<div style="margin-top:10px"><button class="btn btn-secondary btn-sm" data-guide="' + esc(c.id) + '">📖 配置获取指南</button></div>' : "") +
      "</div>";
  }).join("");

  $("#view").innerHTML =
    '<div class="page-header"><div class="page-title">渠道管理<small>渠道脚本位于 channels/ 目录, 新增方式见 docs/渠道开发指南.md</small></div>' +
    '<div class="page-actions"><button class="btn btn-secondary btn-sm" id="btn-refresh">↻ 刷新</button></div></div>' +
    '<div class="channel-grid">' + (cards || '<div class="card card-pad">channels/ 目录下还没有渠道</div>') + "</div>";
  $("#btn-refresh").addEventListener("click", () => renderChannels());
  $$("[data-guide]").forEach((btn) => btn.addEventListener("click", () => showGuide(btn.dataset.guide)));
}

/* ---------------- 启动 ---------------- */
route();
