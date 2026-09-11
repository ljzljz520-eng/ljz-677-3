"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const api = async (url, opts) => {
  const resp = await fetch(url, opts);
  const ct = resp.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await resp.json() : await resp.text();
  if (!resp.ok) throw new Error((data && data.error) || `请求失败（${resp.status}）`);
  return data;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => (n == null ? "0" : Number(n).toLocaleString("zh-CN"));

const PAGE_SIZE = 50;
let selectedFile = null;
let detail = { taskId: null, filter: "", page: 0, total: 0 };

/* ---------------- 上传 ---------------- */

const fileBox = $("#fileBox");
$("#fileInput").addEventListener("change", (e) => pickFile(e.target.files[0]));
["dragover", "dragenter"].forEach((ev) =>
  fileBox.addEventListener(ev, (e) => { e.preventDefault(); fileBox.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  fileBox.addEventListener(ev, (e) => { e.preventDefault(); fileBox.classList.remove("drag"); })
);
fileBox.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

function pickFile(file) {
  const msg = $("#uploadMsg");
  msg.textContent = "";
  msg.className = "upload-msg";
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    msg.textContent = "仅支持 .xlsx 格式，请另存后重试";
    msg.className = "upload-msg error";
    selectedFile = null;
    $("#btnUpload").disabled = true;
    return;
  }
  selectedFile = file;
  $("#fileText").textContent = `已选择：${file.name}（${(file.size / 1024).toFixed(1)} KB）`;
  $("#btnUpload").disabled = false;
}

$("#btnUpload").addEventListener("click", async () => {
  if (!selectedFile) return;
  const btn = $("#btnUpload");
  btn.disabled = true;
  btn.textContent = "上传中…";
  const msg = $("#uploadMsg");
  try {
    const fd = new FormData();
    fd.append("file", selectedFile);
    const data = await api("/api/tasks", { method: "POST", body: fd });
    msg.textContent = `上传成功，任务 ${data.task_id} 已开始校验与上送，可在下方查看实时进度。`;
    msg.className = "upload-msg ok";
    selectedFile = null;
    $("#fileInput").value = "";
    $("#fileText").textContent = "点击选择 .xlsx 文件（含人员编号、项目、结果、单位、体检日期五列）";
    await loadTasks();
    openDrawer(data.task_id);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "upload-msg error";
  } finally {
    btn.disabled = !selectedFile;
    btn.textContent = "开始校验并上送";
  }
});

/* ---------------- 任务列表 ---------------- */

function progressHtml(t) {
  const done = t.success_count + t.platform_error_count + t.invalid_count + t.duplicate_count;
  const pct = t.active && !t.total_count ? 5 : t.progress_pct;
  const label = t.active ? `${t.stage || ""} ${pct}%（${done}/${t.total_count}）` : `${done}/${t.total_count}`;
  return `<div class="progress">
    <div class="progress-track"><div class="progress-bar" style="width:${pct}%"></div></div>
    <div class="progress-txt">${esc(label)}</div>
  </div>`;
}

function actionHtml(t) {
  const btns = [`<button class="link-btn" data-act="detail" data-id="${t.id}">查看明细</button>`];
  if (t.active) {
    btns.push(`<button class="link-btn" data-act="cancel" data-id="${t.id}">取消</button>`);
  }
  if (t.platform_error_count > 0 && t.status === "COMPLETED_WITH_ERRORS") {
    btns.push(`<button class="link-btn" data-act="resume" data-id="${t.id}">重试异常</button>`);
  }
  if (t.status === "CANCELLED" && t.pending_count > 0) {
    btns.push(`<button class="link-btn" data-act="resume" data-id="${t.id}">继续上送</button>`);
  }
  const abnormal = t.platform_error_count + t.invalid_count + t.duplicate_count;
  if (abnormal > 0) {
    btns.push(`<a class="link-btn" href="/api/tasks/${t.id}/errors.xlsx">导出异常</a>`);
  }
  return btns.join("");
}

async function loadTasks() {
  const data = await api("/api/tasks");
  const tbody = $("#taskTable tbody");
  if (!data.tasks.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty">暂无任务，请先上传 Excel</td></tr>`;
    return;
  }
  tbody.innerHTML = data.tasks.map((t) => `
    <tr>
      <td class="mono">${esc(t.id)}</td>
      <td title="${esc(t.filename)}">${esc(t.filename.length > 22 ? t.filename.slice(0, 21) + "…" : t.filename)}</td>
      <td><span class="badge b-${t.status}">${esc(t.status_text)}</span>
          ${t.error_message ? `<div class="warn-text" title="${esc(t.error_message)}">${esc(t.error_message.slice(0, 30))}</div>` : ""}</td>
      <td>${progressHtml(t)}</td>
      <td>${fmtNum(t.total_count)}</td>
      <td style="color:#166534;font-weight:600">${fmtNum(t.success_count)}</td>
      <td style="color:#92590a;font-weight:600">${fmtNum(t.invalid_count)}</td>
      <td style="color:#b42318;font-weight:600">${fmtNum(t.platform_error_count)}</td>
      <td style="color:#5e35b1;font-weight:600">${fmtNum(t.duplicate_count)}</td>
      <td class="mono">${esc(t.created_at)}</td>
      <td>${actionHtml(t)}</td>
    </tr>`).join("");
}

$("#taskTable").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  if (act === "detail") openDrawer(id);
  if (act === "cancel" && confirm("确认取消该任务？尚未上送的行将停止发送。")) {
    try { await api(`/api/tasks/${id}/cancel`, { method: "POST" }); toast("已请求取消"); }
    catch (err) { toast(err.message, true); }
  }
  if (act === "resume") {
    try {
      await api(`/api/tasks/${id}/resume`, { method: "POST" });
      toast("已开始重上送平台异常行");
      openDrawer(id);
    } catch (err) { toast(err.message, true); }
  }
});

$("#btnRefresh").addEventListener("click", () => loadTasks().catch((e) => toast(e.message, true)));

/* ---------------- 明细抽屉 ---------------- */

const ROW_TABS = [
  ["", "全部"], ["INVALID", "校验失败"], ["PLATFORM_ERROR", "平台异常"],
  ["DUPLICATE", "重复数据"], ["PENDING", "待上送"], ["SUCCESS", "成功"],
];

async function openDrawer(taskId) {
  detail = { taskId, filter: "", page: 0, total: 0 };
  $("#drawerMask").hidden = false;
  await refreshDetail();
}

async function refreshDetail() {
  const { taskId, filter, page } = detail;
  const [t, r] = await Promise.all([
    api(`/api/tasks/${taskId}`),
    api(`/api/tasks/${taskId}/rows?status=${filter}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`),
  ]);
  const task = t.task;
  detail.total = r.total;
  $("#detailTitle").textContent = `任务明细 · ${task.id}`;
  $("#detailSub").textContent = `${task.filename} ｜ ${task.status_text} ｜ ${task.stage || ""}`;
  $("#btnExport").href = `/api/tasks/${taskId}/errors.xlsx`;
  $("#btnExport").style.display =
    task.platform_error_count + task.invalid_count + task.duplicate_count > 0 ? "" : "none";

  const counts = {
    "": task.total_count, INVALID: task.invalid_count, PLATFORM_ERROR: task.platform_error_count,
    DUPLICATE: task.duplicate_count, PENDING: task.pending_count, SUCCESS: task.success_count,
  };
  $("#rowTabs").innerHTML = ROW_TABS.map(([val, label]) =>
    `<button class="tab ${filter === val ? "active" : ""}" data-filter="${val}">${label}<span class="n">${fmtNum(counts[val] || 0)}</span></button>`
  ).join("");

  $("#rowTable tbody").innerHTML = r.rows.length ? r.rows.map((row) => {
    const item = row.item_code ? `${esc(row.item_name)}<br><span class="muted mono">${esc(row.item_code)}</span>` : esc(row.item_name);
    let note = "";
    if (row.status === "INVALID") note = esc(row.warning || "");
    if (row.status === "SUCCESS") note = `<span class="mono">${esc(row.platform_record_id || "")}</span>`;
    if (row.status === "PLATFORM_ERROR") note = `<span class="mono">${esc(row.error_code || "")}</span>`;
    const err = row.error_message
      ? `<div class="${row.status === "DUPLICATE" ? "warn-text" : "err-text"}">${esc(row.error_message)}</div>` : "";
    return `<tr>
      <td class="mono">${row.row_no}</td><td class="mono">${esc(row.person_id)}</td>
      <td>${item}</td><td>${esc(row.result)}</td><td>${esc(row.unit)}</td>
      <td class="mono">${esc(row.exam_date)}</td>
      <td><span class="badge b-${row.status}">${esc(row.status_text)}</span></td>
      <td>${err}${row.status === "INVALID" && row.warning ? `<div class="warn-text">${esc(row.warning)}</div>` : ""}</td>
      <td>${note}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="9" class="empty">该分类下暂无数据</td></tr>`;

  const pages = Math.max(1, Math.ceil(r.total / PAGE_SIZE));
  $("#pageInfo").textContent = `第 ${page + 1} / ${pages} 页，共 ${fmtNum(r.total)} 行`;
  $("#btnPrev").disabled = page === 0;
  $("#btnNext").disabled = (page + 1) * PAGE_SIZE >= r.total;
}

$("#rowTabs").addEventListener("click", async (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  detail.filter = tab.dataset.filter;
  detail.page = 0;
  await refreshDetail();
});
$("#btnPrev").addEventListener("click", async () => {
  if (detail.page > 0) { detail.page -= 1; await refreshDetail(); }
});
$("#btnNext").addEventListener("click", async () => {
  if ((detail.page + 1) * PAGE_SIZE < detail.total) { detail.page += 1; await refreshDetail(); }
});
$("#btnCloseDrawer").addEventListener("click", () => { $("#drawerMask").hidden = true; });
$("#drawerMask").addEventListener("click", (e) => { if (e.target.id === "drawerMask") $("#drawerMask").hidden = true; });

/* ---------------- 项目目录 ---------------- */

$("#btnCatalog").addEventListener("click", async () => {
  $("#catalogMask").hidden = false;
  const data = await api("/api/catalog");
  $("#catalogTable tbody").innerHTML = data.items.map((i) =>
    `<tr><td class="mono">${esc(i.code)}</td><td>${esc(i.name)}</td><td>${esc(i.unit)}</td><td>${esc(i.ref_range)}</td></tr>`
  ).join("");
});
$("#btnCloseCatalog").addEventListener("click", () => { $("#catalogMask").hidden = true; });
$("#catalogMask").addEventListener("click", (e) => { if (e.target.id === "catalogMask") e.target.hidden = true; });

/* ---------------- 轮询 ---------------- */

async function tick() {
  try {
    await loadTasks();
    if (!$("#drawerMask").hidden) await refreshDetail();
  } catch (err) {
    console.error(err);
  }
}
setInterval(tick, 2000);
tick();

/* ---------------- toast ---------------- */

let toastTimer = null;
function toast(text, isError = false) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast" + (isError ? " error" : " ok");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}
