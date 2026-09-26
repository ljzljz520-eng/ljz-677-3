/* 体检结果批量上送平台 — 前端逻辑（原生 JS） */
"use strict";

const TASK_STATUS = {
  uploaded:   { text: "已上传", cls: "uploaded" },
  validated:  { text: "待确认", cls: "validated" },
  uploading:  { text: "上送中", cls: "uploading" },
  completed:  { text: "已完成", cls: "completed" },
  failed:     { text: "任务失败", cls: "failed" },
  cancelled:  { text: "已取消", cls: "cancelled" },
};
const ROW_STATUS = {
  pending:   { text: "待上送", cls: "pending" },
  success:   { text: "上送成功", cls: "success" },
  failed:    { text: "平台拒绝", cls: "failed-row" },
  retryable: { text: "可重试", cls: "retryable" },
  invalid:   { text: "校验失败", cls: "invalid" },
  duplicate: { text: "重复", cls: "duplicate" },
};

const state = {
  selectedFile: null,
  currentTaskId: null,
  currentTask: null,
  rowFilter: "",         // "" | status | exception | duplicate
  rowPage: 1,
  pageSize: 20,
  rowTotal: 0,
  searchTimer: null,
  pollTimer: null,
  uploadedTaskId: null,  // 新上传的任务，用于自动选中
};

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, kind = "") {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2800);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `请求失败（${res.status}）`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

const qs = (p) => new URLSearchParams(p).toString();

/* ---------------- 文件选择 ---------------- */

const dz = $("dropzone");
dz.addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", (e) => {
  if (e.target.files[0]) selectFile(e.target.files[0]);
});
["dragover", "dragenter"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragover"); }));
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragover"); }));
dz.addEventListener("drop", (e) => {
  if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
});

function selectFile(file) {
  if (!/\.(xlsx|xlsm)$/i.test(file.name)) {
    toast("仅支持 .xlsx 文件", "error");
    return;
  }
  state.selectedFile = file;
  $("selectedName").textContent = `${file.name}（${(file.size / 1024).toFixed(1)} KB）`;
  $("selectedFile").classList.remove("hidden");
  $("uploadBtn").disabled = false;
}
$("clearFile").addEventListener("click", () => {
  state.selectedFile = null;
  $("fileInput").value = "";
  $("selectedFile").classList.add("hidden");
  $("uploadBtn").disabled = true;
});

/* ---------------- 上传 + 校验 ---------------- */

$("uploadBtn").addEventListener("click", async () => {
  if (!state.selectedFile) return;
  const btn = $("uploadBtn");
  const msg = $("uploadMsg");
  btn.disabled = true;
  msg.className = "inline-msg";
  msg.textContent = "正在上传并校验…";
  try {
    const fd = new FormData();
    fd.append("file", state.selectedFile);
    const data = await api("/api/tasks", { method: "POST", body: fd });
    msg.textContent = "";
    state.uploadedTaskId = data.task.id;
    renderValidate(data.task);
    $("validateCard").classList.remove("hidden");
    $("validateCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
    await loadTasks();
    toast("校验完成", "ok");
  } catch (e) {
    msg.className = "inline-msg error";
    msg.textContent = e.message;
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
  }
});

function renderValidate(task) {
  const s = [
    { num: task.total_rows, label: "总行数", tone: "" },
    { num: task.valid_rows, label: "有效行", tone: "green" },
    { num: task.invalid_rows, label: "校验失败", tone: "red" },
    { num: task.duplicate_rows, label: "疑似重复", tone: "amber" },
  ];
  $("validateStats").innerHTML = s.map(
    (x) => `<div class="stat-box ${x.tone ? "tone-" + x.tone : ""}">
      <div class="stat-num">${x.num}</div><div class="stat-label">${x.label}</div></div>`
  ).join("");

  $("validateTip").innerHTML = task.valid_rows > 0
    ? `有 <b>${task.valid_rows}</b> 行通过校验，可以上送；` +
      `校验失败与重复行 <b>不会上送</b>，可在明细中查看并导出。`
    : `没有可上送的有效行，请根据明细修正 Excel 后重新导入。`;
  $("confirmBtn").disabled = task.valid_rows === 0;
  $("confirmBtn").dataset.taskId = task.id;
}

$("confirmBtn").addEventListener("click", () => confirmTask(Number($("confirmBtn").dataset.taskId)));
$("viewAfterValidate").addEventListener("click", () => openDetail(Number($("confirmBtn").dataset.taskId)));

async function confirmTask(taskId) {
  const btn = $("confirmBtn");
  btn.disabled = true;
  $("confirmMsg").className = "inline-msg";
  $("confirmMsg").textContent = "正在提交…";
  try {
    const r = await api(`/api/tasks/${taskId}/confirm`, { method: "POST" });
    $("confirmMsg").className = "inline-msg ok";
    $("confirmMsg").textContent = r.message + "，进度实时更新中";
    toast("已开始上送", "ok");
    openDetail(taskId);
  } catch (e) {
    $("confirmMsg").className = "inline-msg error";
    $("confirmMsg").textContent = e.message;
    toast(e.message, "error");
    btn.disabled = false;
  }
}

/* ---------------- 任务列表 ---------------- */

$("taskFilter").addEventListener("change", loadTasks);

function badge(kind, map) {
  const b = map[kind] || { text: kind, cls: "uploaded" };
  return `<span class="badge ${b.cls}">${esc(b.text)}</span>`;
}

async function loadTasks() {
  const status = $("taskFilter").value;
  const url = "/api/tasks" + (status ? "?" + qs({ status }) : "");
  const data = await api(url);
  const el = $("taskList");
  if (!data.tasks.length) {
    el.innerHTML = `<div class="empty-state">暂无任务，请先导入 Excel</div>`;
    return;
  }
  el.innerHTML = data.tasks.map((t) => {
    const pct = t.progress_pct || 0;
    return `<div class="task-item ${t.id === state.currentTaskId ? "active" : ""}" data-id="${t.id}">
      <div class="task-main">
        <div class="task-filename">${esc(t.filename)}</div>
        <div class="task-sub">
          <span>#${t.id}</span><span>${esc(t.created_at)}</span>
          ${badge(t.status, TASK_STATUS)}
        </div>
        <div class="task-counts">
          <span class="count-pill">共 ${t.total_rows}</span>
          <span class="count-pill ok">成功 ${t.success_rows}</span>
          ${t.failed_rows ? `<span class="count-pill bad">异常 ${t.failed_rows}</span>` : ""}
          ${t.duplicate_rows ? `<span class="count-pill warn">重复 ${t.duplicate_rows}</span>` : ""}
          ${t.invalid_rows ? `<span class="count-pill bad">校验失败 ${t.invalid_rows}</span>` : ""}
        </div>
        ${t.status === "uploading"
          ? `<div class="mini-progress"><div style="width:${pct}%"></div></div>` : ""}
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll(".task-item").forEach((node) =>
    node.addEventListener("click", () => openDetail(Number(node.dataset.id))));
}

/* ---------------- 明细 ---------------- */

async function openDetail(id) {
  state.currentTaskId = id;
  state.rowFilter = "";
  state.rowPage = 1;
  $("detailPanel").classList.remove("hidden");
  $("detailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  await refreshDetail();
  await loadTasks();
  startPolling();
}

$("backBtn").addEventListener("click", () => {
  $("detailPanel").classList.add("hidden");
  stopPolling();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

$("detailConfirmBtn").addEventListener("click", () => confirmTask(state.currentTaskId));
$("retryBtn").addEventListener("click", retryFailed);

async function retryFailed() {
  try {
    const r = await api(`/api/tasks/${state.currentTaskId}/retry`, { method: "POST" });
    toast(r.message, "ok");
    startPolling();
    await refreshDetail();
  } catch (e) {
    toast(e.message, "error");
  }
}

const FILTERS = [
  { key: "", label: "全部" },
  { key: "pending", label: "待上送" },
  { key: "success", label: "成功" },
  { key: "exception", label: "全部异常" },
  { key: "duplicate", label: "重复行" },
  { key: "invalid", label: "校验失败" },
  { key: "failed", label: "平台拒绝" },
  { key: "retryable", label: "可重试" },
];

function renderChips() {
  $("rowFilters").innerHTML = FILTERS.map(
    (f) => `<button class="chip ${state.rowFilter === f.key ? "active" : ""}" data-key="${f.key}">${f.label}</button>`
  ).join("");
  $("rowFilters").querySelectorAll(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      state.rowFilter = c.dataset.key;
      state.rowPage = 1;
      renderChips();
      loadRows();
    }));
}
renderChips();

$("rowSearch").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => { state.rowPage = 1; loadRows(); }, 300);
});
$("prevPage").addEventListener("click", () => { if (state.rowPage > 1) { state.rowPage--; loadRows(); } });
$("nextPage").addEventListener("click", () => {
  if (state.rowPage * state.pageSize < state.rowTotal) { state.rowPage++; loadRows(); }
});
$("exportBtn").addEventListener("click", () => {
  if (!state.currentTaskId) return;
  window.location.href = `/api/tasks/${state.currentTaskId}/export`;
});

async function refreshDetail() {
  const t = await api(`/api/tasks/${state.currentTaskId}`);
  state.currentTask = t;
  $("detailTitle").textContent = `任务 #${t.id} 明细`;
  $("detailMeta").innerHTML =
    `<span class="fname">${esc(t.filename)}</span>` +
    badge(t.status, TASK_STATUS) +
    `<span>创建：${esc(t.created_at)}</span>` +
    (t.finished_at ? `<span>完成：${esc(t.finished_at)}</span>` : "");

  // 进度条
  if (t.status === "uploading" || t.status === "completed") {
    $("progressWrap").classList.remove("hidden");
    $("progressFill").style.width = t.progress_pct + "%";
    $("progressText").textContent =
      `${t.processed_rows}/${t.valid_rows} 行 · 成功 ${t.success_rows} · 异常 ${t.failed_rows}` +
      (t.status === "uploading" ? `（${t.progress_pct}%）` : "（100%）");
  } else {
    $("progressWrap").classList.add("hidden");
  }

  // 平台异常聚合
  const sums = (t.error_summary || []).filter((x) => x.count > 0);
  if (sums.length && (t.status === "uploading" || t.status === "completed")) {
    $("errorSummary").classList.remove("hidden");
    $("errorSummary").innerHTML =
      `<h4>平台返回异常汇总（${sums.reduce((a, b) => a + b.count, 0)} 行）</h4><ul>` +
      sums.map((s) => `<li><code>${esc(s.code)}</code> ${esc(s.message)}：<b>${s.count}</b> 行</li>`).join("") +
      `</ul>`;
  } else {
    $("errorSummary").classList.add("hidden");
  }

  // 待确认任务显示确认按钮
  $("detailConfirmBtn").classList.toggle("hidden", !(t.status === "validated" && t.valid_rows > 0));
  $("retryBtn").classList.toggle("hidden", !((t.retryable_rows || 0) > 0 &&
    (t.status === "completed" || t.status === "failed")));

  if (t.status !== "uploading") stopPolling();
  await loadRows();
}

function rowMessage(r) {
  const parts = [];
  if (r.error_messages) parts.push(esc(r.error_messages));
  if (r.platform_message) {
    const prefix = r.platform_code ? `[${esc(r.platform_code)}] ` : "";
    parts.push(prefix + esc(r.platform_message));
  }
  if (r.status === "success" && r.reference_no) {
    parts.push(`<span class="ref-no">回执：${esc(r.reference_no)}</span>`);
  }
  if (r.status === "pending") parts.push("等待上送…");
  return parts.join("<br>") || "—";
}

async function loadRows() {
  if (!state.currentTaskId) return;
  const p = { page: state.rowPage, page_size: state.pageSize };
  if (state.rowFilter === "exception" || state.rowFilter === "duplicate") p.category = state.rowFilter;
  else if (state.rowFilter) p.status = state.rowFilter;
  const q = $("rowSearch").value.trim();
  if (q) p.q = q;

  const data = await api(`/api/tasks/${state.currentTaskId}/rows?` + qs(p));
  state.rowTotal = data.total;
  $("rowTbody").innerHTML = data.rows.length
    ? data.rows.map((r) => `
        <tr class="row-${r.status}">
          <td>${r.row_no}</td>
          <td>${esc(r.person_id)}</td>
          <td>${esc(r.item_name)}${r.item_code ? `<br><span class="count-pill">${esc(r.item_code)}</span>` : ""}</td>
          <td>${esc(r.result)}</td>
          <td>${esc(r.unit)}</td>
          <td>${esc(r.exam_date)}</td>
          <td>${badge(r.status, ROW_STATUS)}${r.is_duplicate ? `<br><span class="badge duplicate">重复标记</span>` : ""}</td>
          <td class="msg-cell">${rowMessage(r)}</td>
        </tr>`).join("")
    : `<tr><td colspan="8" class="empty-state">没有符合条件的行</td></tr>`;

  const pages = Math.max(1, Math.ceil(data.total / state.pageSize));
  $("pageInfo").textContent = `第 ${state.rowPage} / ${pages} 页 · 共 ${data.total} 行`;
  $("prevPage").disabled = state.rowPage <= 1;
  $("nextPage").disabled = state.rowPage >= pages;
}

/* ---------------- 轮询 ---------------- */

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(async () => {
    if (!state.currentTaskId) return;
    try {
      await refreshDetail();
      await loadTasks();
    } catch (_) {}
  }, 1500);
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

/* ---------------- 初始化 ---------------- */

(async function init() {
  try {
    await loadTasks();
  } catch (e) {
    toast("后端服务不可用：" + e.message, "error");
  }
})();
