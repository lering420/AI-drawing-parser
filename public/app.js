/* AI 工程图纸解析 — 前端逻辑 */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function api(path, payload) {
  const opts = payload === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ ok: false, error: "响应解析失败" }));
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data.data;
}

function toastErr(e) {
  alert("出错了：" + (e && e.message ? e.message : e));
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ================= 导航 ================= */
$$(".nav-item[data-page]").forEach(btn => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".page").forEach(p => p.classList.remove("active"));
    $("#page-" + btn.dataset.page).classList.add("active");
    if (btn.dataset.page === "audit") refreshAuditSelect();
    if (btn.dataset.page === "manage") loadManage();
    if (btn.dataset.page === "query") refreshQueryFilters();
  });
});

/* ================= 设置 ================= */
async function refreshKeyState() {
  try {
    const cfg = await api("/api/config");
    const el = $("#key-state");
    if (cfg.has_key) {
      el.textContent = "密钥已配置 " + cfg.key_masked;
      el.className = "key-state ok";
    } else {
      el.textContent = "⚠ 未配置 API 密钥";
      el.className = "key-state bad";
    }
  } catch (e) { /* 忽略 */ }
}

$("#btn-settings").addEventListener("click", async () => {
  const cfg = await api("/api/config").catch(() => ({ has_key: false, model: "deepseek-flash" }));
  $("#in-apikey").value = "";
  $("#in-apikey").placeholder = cfg.has_key ? `已配置（${cfg.key_masked}），留空则保持不变` : "sk-…";
  $("#in-model").value = cfg.model || "deepseek-flash";
  $("#config-msg").textContent = "";
  $("#settings-mask").hidden = false;
});
$("#settings-close").addEventListener("click", () => { $("#settings-mask").hidden = true; });
$("#btn-save-config").addEventListener("click", async () => {
  try {
    const payload = { model: $("#in-model").value.trim() || "deepseek-flash" };
    const key = $("#in-apikey").value.trim();
    if (key) payload.api_key = key;
    await api("/api/config", payload);
    $("#config-msg").textContent = "✓ 已保存";
    $("#config-msg").style.color = "var(--ok)";
    refreshKeyState();
  } catch (e) { $("#config-msg").textContent = e.message; $("#config-msg").style.color = "var(--severe)"; }
});
$("#btn-test-key").addEventListener("click", async () => {
  const msg = $("#config-msg");
  msg.textContent = "测试中…"; msg.style.color = "var(--ink2)";
  try {
    // 先保存再测
    const payload = { model: $("#in-model").value.trim() || "deepseek-flash" };
    const key = $("#in-apikey").value.trim();
    if (key) payload.api_key = key;
    await api("/api/config", payload);
    const r = await api("/api/config/test", {});
    msg.textContent = "✓ 连接成功（AI 回复：" + r.reply + "）";
    msg.style.color = "var(--ok)";
    refreshKeyState();
  } catch (e) {
    msg.textContent = "✗ " + e.message;
    msg.style.color = "var(--severe)";
  }
});

/* ================= 模块1：提取 ================= */
const extract = {
  images: [],        // data URL 列表
  fileName: "",
  pages: 0,
  drawingId: null,
  params: null,
};

pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.min.js";

const dropzone = $("#dropzone");
dropzone.addEventListener("click", () => $("#file-input").click());
dropzone.addEventListener("dragover", e => { e.preventDefault(); dropzone.classList.add("drag"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
dropzone.addEventListener("drop", e => {
  e.preventDefault(); dropzone.classList.remove("drag");
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
});
$("#file-input").addEventListener("change", e => { if (e.target.files[0]) handleFile(e.target.files[0]); });

async function handleFile(file) {
  if (!/\.pdf$/i.test(file.name)) { alert("请上传 PDF 文件"); return; }
  resetExtract();
  extract.fileName = file.name;
  $("#file-meta").hidden = false;
  $("#fm-name").textContent = file.name;
  $("#fm-status").textContent = "正在生成页面预览…";

  try {
    const buf = await file.arrayBuffer();
    // 1) 上传 PDF 归档
    const b64 = arrayBufferToBase64(buf);
    const up = await api("/api/upload", { file_name: file.name, pdf_base64: b64, pages: 1 });
    extract.drawingId = up.drawing_id;

    // 2) 浏览器渲染成图片（后续发给 AI）
    const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
    extract.pages = Math.min(pdf.numPages, 12);
    $("#fm-pages").textContent = pdf.numPages + (pdf.numPages > 12 ? "（本次识别前 12 页）" : "");
    const thumbs = $("#thumbs");
    thumbs.innerHTML = "";
    for (let i = 1; i <= extract.pages; i++) {
      const page = await pdf.getPage(i);
      const viewport = page.getViewport({ scale: 1.4 });
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width; canvas.height = viewport.height;
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      const url = canvas.toDataURL("image/jpeg", 0.85);
      extract.images.push(url);
      const img = document.createElement("img");
      img.src = url; img.title = "第 " + i + " 页";
      thumbs.appendChild(img);
    }
    $("#fm-status").textContent = "就绪，共 " + extract.pages + " 页";
    $("#btn-extract").disabled = false;
    $("#btn-reset").hidden = false;
  } catch (e) {
    $("#fm-status").textContent = "处理失败";
    toastErr(e);
  }
}

function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function resetExtract() {
  extract.images = []; extract.fileName = ""; extract.pages = 0;
  extract.drawingId = null; extract.params = null;
  $("#file-meta").hidden = true;
  $("#thumbs").innerHTML = "";
  $("#btn-extract").disabled = true;
  $("#btn-reset").hidden = true;
  $("#result-editor").hidden = true;
  $("#result-empty").hidden = false;
  $("#save-ok").hidden = true;
}
$("#btn-reset").addEventListener("click", () => { resetExtract(); $("#file-input").value = ""; });

$("#btn-extract").addEventListener("click", runExtract);
$("#btn-reextract").addEventListener("click", runExtract);

async function runExtract() {
  if (!extract.images.length) return;
  $("#btn-extract").disabled = true;
  $("#extract-progress").hidden = false;
  $("#extract-progress-text").textContent = "AI 正在读图提取参数（约 10-60 秒）…";
  try {
    const params = await api("/api/extract", { images: extract.images, drawing_id: extract.drawingId });
    extract.params = params;
    renderEditor(params);
    $("#fm-status").textContent = "已提取，核对后点「确认入库」";
    $("#result-empty").hidden = true;
    $("#result-editor").hidden = false;
    $("#save-ok").hidden = true;
  } catch (e) {
    toastErr(e);
    $("#fm-status").textContent = "提取失败，可重试";
  } finally {
    $("#btn-extract").disabled = false;
    $("#extract-progress").hidden = true;
  }
}

/* ---- 结果编辑器 ---- */
const TITLE_FIELDS = [
  ["drawing_no", "图号"], ["title", "图纸名称"], ["material", "材料"],
  ["scale", "比例"], ["qty", "数量"], ["category", "类别"],
  ["version", "版本"], ["draw_date", "日期"],
];
const EXTRA_FIELDS = [
  ["heat_treatment", "热处理"], ["hardness", "硬度"],
  ["unspecified_tolerance", "未注公差"],
];

function renderEditor(p) {
  $("#title-fields").innerHTML = TITLE_FIELDS.map(([k, label]) =>
    `<label>${label}<input type="text" data-tf="${k}" value="${esc(p[k])}"></label>`).join("");

  $("#extra-fields").innerHTML = EXTRA_FIELDS.map(([k, label]) =>
    `<label>${label}<input type="text" data-xf="${k}" value="${esc(p[k])}"></label>`).join("");

  renderTable("tb-dimensions", p.dimensions || [], ["name", "value", "tolerance"]);
  renderTable("tb-fits", p.fits || [], ["name", "value", "tolerance"]);
  renderTable("tb-geo", p.geometric_tolerances || [], ["feature", "symbol", "value", "datum"]);
  renderTable("tb-bom", p.bom || [], ["no", "name", "material", "qty"]);
  renderChips(p.roughness || []);
  renderList("list-tech", p.technical_requirements || []);
  $("#in-notes").value = p.notes || "";
}

function renderTable(tbodyId, rows, keys) {
  const tb = $("#" + tbodyId);
  tb.innerHTML = rows.map((r, i) => "<tr>" +
    keys.map(k => `<td><input value="${esc(r[k])}" data-row="${i}" data-key="${k}"></td>`).join("") +
    `<td><button class="del" data-del="${i}">✕</button></td></tr>`).join("");
  tb.dataset.keys = JSON.stringify(keys);
  tb._rows = rows;
}

function renderChips(items) {
  const box = $("#chips-roughness");
  box.innerHTML = items.map((t, i) =>
    `<span class="chip">${esc(t)}<button data-chip="${i}">✕</button></span>`).join("") ||
    '<span class="empty-hint" style="padding:2px">无</span>';
  box._items = items;
}

function renderList(id, items) {
  const box = $("#" + id);
  box.innerHTML = items.map((t, i) =>
    `<div class="li"><input value="${esc(t)}" data-li="${i}"><button class="del mini-btn" data-lidel="${i}">✕</button></div>`).join("") ||
    '<span class="empty-hint" style="padding:2px">无</span>';
  box._items = items;
}

// 添加按钮
$$(".mini-btn[data-add]").forEach(btn => btn.addEventListener("click", () => {
  const kind = btn.dataset.add;
  const p = extract.params; if (!p) return;
  const blank = {
    dimensions: { name: "", value: "", tolerance: "" },
    fits: { name: "", value: "", tolerance: "" },
    geometric_tolerances: { feature: "", symbol: "", value: "", datum: "" },
    bom: { no: "", name: "", material: "", qty: "" },
  };
  if (kind === "roughness") (p.roughness = p.roughness || []).push("Ra3.2");
  else if (kind === "technical_requirements") (p.technical_requirements = p.technical_requirements || []).push("新要求");
  else (p[kind] = p[kind] || []).push({ ...blank[kind] });
  renderEditor(p);
}));

// 删除行 / 修改
document.addEventListener("click", e => {
  const p = extract.params; if (!p) return;
  const tb = e.target.closest("tbody");
  if (e.target.classList.contains("del") && tb) {
    const rows = tb._rows || [];
    rows.splice(+e.target.dataset.del, 1);
    renderEditor(p);
  }
  if (e.target.dataset.chip !== undefined && e.target.tagName === "BUTTON") {
    p.roughness.splice(+e.target.dataset.chip, 1); renderEditor(p);
  }
  if (e.target.dataset.lidel !== undefined) {
    p.technical_requirements.splice(+e.target.dataset.lidel, 1); renderEditor(p);
  }
});
document.addEventListener("input", e => {
  const p = extract.params; if (!p) return;
  if (e.target.dataset.tf) p[e.target.dataset.tf] = e.target.value;
  if (e.target.dataset.xf) p[e.target.dataset.xf] = e.target.value;
  const tb = e.target.closest("tbody");
  if (tb && e.target.dataset.key !== undefined) {
    (tb._rows || [])[+e.target.dataset.row][e.target.dataset.key] = e.target.value;
  }
  if (e.target.dataset.li !== undefined) p.technical_requirements[+e.target.dataset.li] = e.target.value;
});

$("#btn-save").addEventListener("click", async () => {
  const p = extract.params; if (!p || !extract.drawingId) return;
  try {
    await api("/api/drawings/save", {
      id: extract.drawingId,
      drawing_no: p.drawing_no || "", title: p.title || "", material: p.material || "",
      scale: p.scale || "", qty: p.qty || "", category: p.category || "",
      version: p.version || "", draw_date: p.draw_date || "",
      params: p, status: "confirmed",
    });
    $("#save-ok").hidden = false;
    $("#fm-status").textContent = "已入库 ✓";
  } catch (e) { toastErr(e); }
});

/* ================= 模块2：勘误 ================= */
let auditImages = [];   // 当前要检查的图纸页面图片
let auditDrawingId = null;

async function refreshAuditSelect() {
  const sel = $("#audit-select");
  const cur = sel.value;
  try {
    const list = await api("/api/drawings");
    sel.innerHTML = '<option value="">— 请选择已上传的图纸 —</option>' +
      list.map(d => `<option value="${d.id}">${esc(d.drawing_no || "（无图号）")} ${esc(d.title || d.file_name)}${d.status === "draft" ? "（草稿）" : ""}</option>`).join("");
    sel.value = cur;
  } catch (e) { /* 忽略 */ }
}

$("#audit-select").addEventListener("change", async () => {
  const id = $("#audit-select").value;
  $("#audit-result").hidden = true;
  $("#audit-empty").hidden = true;
  auditImages = []; auditDrawingId = id ? +id : null;
  if (!id) return;
  try {
    // 打开已有勘误结果
    const d = await api("/api/drawings/" + id);
    if (d.issues && d.issues.length) renderIssues(d.issues, true);
  } catch (e) { /* 忽略 */ }
});

$("#btn-audit").addEventListener("click", async () => {
  if (!$("#audit-select").value) { alert("请先选择一张图纸"); return; }
  auditDrawingId = +$("#audit-select").value;
  $("#btn-audit").disabled = true;
  $("#audit-progress").hidden = false;
  try {
    // 1) 取原图 PDF 重新渲染页面
    if (!auditImages.length) {
      const res = await fetch("/api/pdf?id=" + auditDrawingId);
      if (!res.ok) throw new Error("找不到原图 PDF，请重新上传");
      const buf = await res.arrayBuffer();
      const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
      const n = Math.min(pdf.numPages, 12);
      auditImages = [];
      for (let i = 1; i <= n; i++) {
        const page = await pdf.getPage(i);
        const vp = page.getViewport({ scale: 1.4 });
        const canvas = document.createElement("canvas");
        canvas.width = vp.width; canvas.height = vp.height;
        await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
        auditImages.push(canvas.toDataURL("image/jpeg", 0.85));
      }
    }
    // 2) AI 勘误
    const r = await api("/api/audit", { images: auditImages, drawing_id: auditDrawingId });
    $("#audit-empty").hidden = true;
    renderIssues(r.issues, false);
  } catch (e) {
    toastErr(e);
  } finally {
    $("#btn-audit").disabled = false;
    $("#audit-progress").hidden = true;
  }
});

const SEV_LABEL = { severe: "严重", general: "一般", hint: "提示" };

function renderIssues(issues, fromDb) {
  $("#audit-result").hidden = false;
  const count = s => issues.filter(i => i.severity === s && (!fromDb || i.status === "open")).length;
  const done = issues.filter(i => fromDb && i.status !== "open").length;
  $("#sev-summary").innerHTML =
    `<span class="sev-pill severe">⚠ 严重 ${count("severe")} 项</span>` +
    `<span class="sev-pill general">🔸 一般 ${count("general")} 项</span>` +
    `<span class="sev-pill hint">💡 提示 ${count("hint")} 项</span>` +
    (fromDb ? `<span class="sev-pill done">✔ 已处理 ${done} 项</span>` : "") +
    (issues.length === 0 ? `<span class="sev-pill done">✔ 未发现问题</span>` : "");

  ["severe", "general", "hint"].forEach(sev => {
    const box = $("#issues-" + sev);
    const list = issues.filter(i => i.severity === sev);
    box.innerHTML = list.length ? list.map(i => {
      const resolved = fromDb && i.status !== "open";
      return `<div class="issue-card ${sev} ${resolved ? "resolved" : ""}" data-iid="${i.id || ""}">
        <div class="issue-meta">
          <span class="tag">第 ${i.page || 1} 页</span>
          <span class="tag">${esc(i.category || "其它")}</span>
          ${i.location ? `<span class="tag">${esc(i.location)}</span>` : ""}
          ${resolved ? `<span class="tag">${i.status === "accepted" ? "已采纳" : "已忽略"}</span>` : ""}
        </div>
        <div class="issue-problem">${esc(i.problem)}</div>
        ${i.suggestion ? `<div class="issue-fix"><b>建议：</b>${esc(i.suggestion)}</div>` : ""}
        ${i.basis ? `<div class="issue-basis">依据：${esc(i.basis)}</div>` : ""}
        ${fromDb && i.id ? `<div class="issue-actions">
          <button class="accept" data-istatus="accepted">✔ 采纳</button>
          <button class="ignore" data-istatus="ignored">忽略</button>
        </div>` : ""}
      </div>`;
    }).join("") : '<div class="no-issue">无</div>';
  });
}

$("#audit-result").addEventListener("click", async e => {
  const st = e.target.dataset.istatus;
  if (!st) return;
  const card = e.target.closest(".issue-card");
  try {
    await api("/api/issues/update", { id: +card.dataset.iid, status: st });
    card.classList.add("resolved");
    e.target.parentElement.innerHTML =
      `<span class="tag">${st === "accepted" ? "已采纳" : "已忽略"}</span>`;
  } catch (err) { toastErr(err); }
});

/* ================= 模块3：数据管理 ================= */
let manageStatus = "confirmed";

$$("#status-tabs .tab").forEach(t => t.addEventListener("click", () => {
  $$("#status-tabs .tab").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  manageStatus = t.dataset.status;
  loadManage();
}));
let searchTimer = null;
$("#manage-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadManage, 300);
});

async function loadManage() {
  try {
    const list = await api(`/api/drawings?status=${manageStatus}&keyword=${encodeURIComponent($("#manage-search").value.trim())}`);
    const stats = await api("/api/issues/stats");
    const statMap = {};
    stats.forEach(s => statMap[s.id] = s);
    const rows = list.map(d => {
      const s = statMap[d.id] || { open_cnt: 0, total: 0 };
      let errBadge = '<span class="badge err-none">未勘误</span>';
      if (s.total) {
        errBadge = s.open_cnt
          ? `<span class="badge err-severe">${s.open_cnt} 项待处理</span>`
          : '<span class="badge err-none">已处理完</span>';
      }
      return `<tr>
        <td><b>${esc(d.drawing_no) || "—"}</b></td>
        <td>${esc(d.title) || "—"}</td>
        <td>${esc(d.material) || "—"}</td>
        <td>${esc(d.category) || "—"}</td>
        <td>${esc(d.version) || "—"}</td>
        <td>${esc(d.draw_date) || "—"}</td>
        <td>${errBadge}</td>
        <td><span class="badge ${d.status}">${{confirmed: "在库", draft: "草稿", archived: "归档"}[d.status] || d.status}</span></td>
        <td>${esc(d.updated_at)}</td>
        <td class="row-actions">
          <button data-act="view" data-id="${d.id}">详情</button>
          ${d.status === "archived"
            ? '<button data-act="restore" data-id="' + d.id + '">恢复</button>'
            : '<button data-act="archive" data-id="' + d.id + '">归档</button>'}
          <button class="del" data-act="delete" data-id="${d.id}">删除</button>
        </td>
      </tr>`;
    }).join("");
    $("#drawing-rows").innerHTML = rows;
    $("#manage-empty").hidden = !!list.length;
  } catch (e) { toastErr(e); }
}

$("#drawing-rows").addEventListener("click", async e => {
  const act = e.target.dataset.act;
  if (!act) return;
  const id = +e.target.dataset.id;
  try {
    if (act === "view") return openDrawer(id);
    if (act === "archive") { await api("/api/drawings/save", { id, status: "archived" }); loadManage(); }
    if (act === "restore") { await api("/api/drawings/save", { id, status: "confirmed" }); loadManage(); }
    if (act === "delete") {
      if (!confirm("删除这张图纸及其参数、勘误记录？原图 PDF 也会一并删除，不可恢复。")) return;
      await api("/api/drawings/delete", { id });
      loadManage();
    }
  } catch (err) { toastErr(err); }
});

/* ---- 详情抽屉 ---- */
async function openDrawer(id) {
  const d = await api("/api/drawings/" + id);
  $("#drawer-title").textContent = (d.drawing_no || "") + " " + (d.title || d.file_name);
  const p = d.params || {};

  const kvRows = [
    ["图号", d.drawing_no], ["名称", d.title], ["材料", d.material],
    ["比例", d.scale], ["数量", d.qty], ["类别", d.category],
    ["版本", d.version], ["日期", d.draw_date], ["页数", d.pages],
    ["状态", {confirmed: "在库", draft: "草稿", archived: "归档"}[d.status]],
    ["原文件", d.file_name],
  ].map(([k, v]) => `<span class="k">${k}</span><span>${esc(v) || "—"}</span>`).join("");

  const tbl = (rows, cols) => rows && rows.length
    ? `<table class="mini-table"><tr>${cols.map(c => `<th>${c[1]}</th>`).join("")}</tr>` +
      rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c[0]]) || "—"}</td>`).join("")}</tr>`).join("") + "</table>"
    : '<p class="hint">无</p>';

  const issuesHtml = d.issues.length
    ? d.issues.map(i => `<div class="issue-card ${i.severity} ${i.status !== "open" ? "resolved" : ""}">
        <div class="issue-meta"><span class="tag">${SEV_LABEL[i.severity] || i.severity}</span>
        <span class="tag">第 ${i.page} 页</span><span class="tag">${{open: "待处理", accepted: "已采纳", ignored: "已忽略"}[i.status]}</span></div>
        <div class="issue-problem">${esc(i.problem)}</div></div>`).join("")
    : '<p class="hint">未做过勘误检查</p>';

  $("#drawer-body").innerHTML = `
    <div class="detail-sec"><h3>标题栏信息（可编辑）</h3>
      <div class="edit-inline" id="edit-meta">
        ${[["drawing_no", "图号"], ["title", "名称"], ["material", "材料"], ["scale", "比例"],
           ["qty", "数量"], ["category", "类别"], ["version", "版本"], ["draw_date", "日期"]]
          .map(([k, label]) => `<label class="field">${label}<input data-mk="${k}" value="${esc(d[k])}"></label>`).join("")}
      </div>
      <div class="btn-row"><button class="btn primary" id="btn-meta-save">保存修改</button></div>
    </div>
    <div class="detail-sec"><h3>特征参数</h3>
      ${tbl(p.dimensions, [["name", "特征"], ["value", "尺寸值"], ["tolerance", "公差"]])}
      <h3 style="margin-top:12px">配合</h3>
      ${tbl(p.fits, [["name", "特征"], ["value", "配合代号"], ["tolerance", "公差"]])}
      <h3 style="margin-top:12px">形位公差</h3>
      ${tbl(p.geometric_tolerances, [["feature", "被测要素"], ["symbol", "符号"], ["value", "公差值"], ["datum", "基准"]])}
      <p class="hint">粗糙度：${esc((p.roughness || []).join("、")) || "—"}<br>
      热处理：${esc(p.heat_treatment) || "—"}　硬度：${esc(p.hardness) || "—"}<br>
      技术要求：${esc((p.technical_requirements || []).join("；")) || "—"}</p>
    </div>
    ${(p.bom || []).length ? `<div class="detail-sec"><h3>零件明细表（BOM）</h3>
      ${tbl(p.bom, [["no", "序号"], ["name", "名称"], ["material", "材料"], ["qty", "数量"]])}</div>` : ""}
    <div class="detail-sec"><h3>勘误记录（${d.issues.length} 项）</h3>${issuesHtml}</div>
    <div class="detail-sec"><h3>原图 PDF</h3>
      <div class="btn-row" style="margin-top:0">
        <a class="btn ghost" href="/api/pdf?id=${d.id}" target="_blank">📂 打开原图</a>
        <button class="btn ghost" id="btn-export-one">⬇ 导出本图 JSON</button>
      </div>
    </div>`;

  $("#btn-meta-save").addEventListener("click", async () => {
    const fields = {};
    $$("#edit-meta input").forEach(i => fields[i.dataset.mk] = i.value);
    try {
      await api("/api/drawings/save", { id: d.id, ...fields });
      alert("已保存");
      loadManage();
    } catch (err) { toastErr(err); }
  });
  $("#btn-export-one").addEventListener("click", () =>
    downloadFile((d.drawing_no || "drawing") + ".json", JSON.stringify(d, null, 2), "application/json"));

  $("#drawer-mask").hidden = false;
}
$("#drawer-close").addEventListener("click", () => { $("#drawer-mask").hidden = true; });
$("#drawer-mask").addEventListener("click", e => { if (e.target.id === "drawer-mask") $("#drawer-mask").hidden = true; });

/* ---- 导出 ---- */
function downloadFile(name, content, type) {
  const blob = new Blob(["\ufeff" + content], { type: type || "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
function toCsv(rows, cols) {
  const q = v => '"' + String(v ?? "").replace(/"/g, '""') + '"';
  return [cols.map(c => q(c[1])).join(",")]
    .concat(rows.map(r => cols.map(c => q(r[c[0]])).join(","))).join("\r\n");
}
$("#btn-export-all").addEventListener("click", async () => {
  const list = await api(`/api/drawings?status=${manageStatus}`);
  downloadFile("图纸台账.csv", toCsv(list, [
    ["drawing_no", "图号"], ["title", "名称"], ["material", "材料"], ["category", "类别"],
    ["scale", "比例"], ["qty", "数量"], ["version", "版本"], ["draw_date", "日期"],
    ["status", "状态"], ["updated_at", "更新时间"],
  ]), "text/csv;charset=utf-8");
});

/* ================= 模块4：智能查询 ================= */
$$(".chip-btn").forEach(b => b.addEventListener("click", () => {
  $("#query-input").value = b.textContent;
  runQuery();
}));
$("#btn-query").addEventListener("click", runQuery);
$("#query-input").addEventListener("keydown", e => { if (e.key === "Enter") runQuery(); });

async function runQuery() {
  const q = $("#query-input").value.trim();
  if (!q) return;
  $("#btn-query").disabled = true;
  $("#query-progress").hidden = false;
  $("#query-answer").hidden = true;
  $("#query-hits").innerHTML = "";
  try {
    const r = await api("/api/query", { question: q });
    const ans = $("#query-answer");
    ans.textContent = r.answer || "（无回答）";
    ans.hidden = false;
    $("#query-hits").innerHTML = r.hits.map(d => `
      <div class="hit-card" data-id="${d.id}">
        <div class="hit-no">${esc(d.drawing_no) || "（无图号）"}</div>
        <div class="hit-title">${esc(d.title) || esc(d.file_name)}</div>
        <div class="hit-meta">${esc(d.material) || "—"} · ${esc(d.category) || "—"} · ${esc(d.draw_date) || "—"}</div>
      </div>`).join("");
  } catch (e) {
    const ans = $("#query-answer");
    ans.textContent = "查询失败：" + e.message;
    ans.hidden = false;
  } finally {
    $("#btn-query").disabled = false;
    $("#query-progress").hidden = true;
  }
}

$("#query-hits").addEventListener("click", e => {
  const card = e.target.closest(".hit-card");
  if (!card) return;
  $$(".nav-item").find(b => b.dataset.page === "manage").click();
  setTimeout(() => openDrawer(+card.dataset.id), 100);
});

/* ---- 条件筛选 ---- */
async function refreshQueryFilters() {
  try {
    const list = await api("/api/drawings");
    const mats = [...new Set(list.map(d => d.material).filter(Boolean))];
    const sel = $("#f-material");
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部</option>' + mats.map(m => `<option>${esc(m)}</option>`).join("");
    sel.value = cur;
    if (!$("#filter-rows").children.length) runFilter();
  } catch (e) { /* 忽略 */ }
}

async function runFilter() {
  try {
    const all = await api("/api/drawings");
    const m = $("#f-material").value, c = $("#f-category").value, s = $("#f-status").value;
    const list = all.filter(d =>
      (!m || d.material === m) && (!c || d.category === c) && (!s || d.status === s));
    $("#filter-rows").innerHTML = list.map(d => `
      <tr data-id="${d.id}" style="cursor:pointer">
        <td><b>${esc(d.drawing_no) || "—"}</b></td>
        <td>${esc(d.title) || "—"}</td>
        <td>${esc(d.material) || "—"}</td>
        <td>${esc(d.category) || "—"}</td>
        <td>${esc(d.draw_date) || "—"}</td>
      </tr>`).join("");
    $("#filter-empty").hidden = !!list.length;
    window._filterList = list;
  } catch (e) { toastErr(e); }
}
$("#btn-filter").addEventListener("click", runFilter);
$("#filter-rows").addEventListener("click", e => {
  const tr = e.target.closest("tr");
  if (tr) openDrawer(+tr.dataset.id);
});
$("#btn-export-filter").addEventListener("click", () => {
  const list = window._filterList || [];
  downloadFile("查询结果.csv", toCsv(list, [
    ["drawing_no", "图号"], ["title", "名称"], ["material", "材料"],
    ["category", "类别"], ["draw_date", "日期"], ["status", "状态"],
  ]), "text/csv;charset=utf-8");
});

/* ================= 启动 ================= */
refreshKeyState();
