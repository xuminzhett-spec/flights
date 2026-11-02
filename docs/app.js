"use strict";
/* Flight price tracker front-end. No libraries.
   Reads routes.json + data/prices.json (served by GitHub Pages).
   Saves route changes back to the repo through the GitHub API using a token kept in this browser only. */

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const state = {
  routesDoc: { settings: {}, routes: [] },
  prices: { routes: {} },
  openId: null,
  filters: { visa: store.get("f_visa") === "1", bag: store.get("f_bag") === "1", direct: store.get("f_direct") === "1" },
};

const VISA_OK = new Set(["free", "voa", "home"]);
const VISA_CLASS = { free: "ok", voa: "ok", home: "ok", evisa: "warn", eta: "warn", required: "bad", banned: "bad", unknown: "" };

/* ---------------------------------------------------------------- utils */
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), ms);
}
function repoName() {
  const manual = store.get("repo").trim();
  if (manual) return manual;
  const m = location.hostname.match(/^([^.]+)\.github\.io$/i);
  const seg = location.pathname.split("/").filter(Boolean)[0];
  return m && seg ? `${m[1]}/${seg}` : "";
}
const money = (n, cur) => `${cur || state.prices.currency || state.routesDoc.settings.currency || ""} ${Math.round(n).toLocaleString()}`.trim();
function fmtDate(d) { return d || ""; }
function ago(iso) {
  if (!iso) return "尚未检查";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (mins < 60) return `${mins} 分钟前`;
  if (mins < 1440) return `${Math.round(mins / 60)} 小时前`;
  return `${Math.round(mins / 1440)} 天前`;
}
function gflights(r) {
  let q = `Flights from ${r.from} to ${r.to} on ${r.depart}`;
  if (r.return) q += ` through ${r.return}`;
  return "https://www.google.com/travel/flights?q=" + encodeURIComponent(q);
}

/* ---------------------------------------------------------------- load */
async function fetchJson(url) {
  const r = await fetch(`${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
async function load() {
  try { state.routesDoc = await fetchJson("routes.json"); } catch { toast("读取航线失败，请刷新页面"); }
  try { state.prices = await fetchJson("data/prices.json"); } catch { state.prices = { routes: {} }; }
  render();
}

/* ---------------------------------------------------------------- price logic */
function viewFor(route) {
  const rec = state.prices.routes?.[route.id];
  if (!rec) return { rec: null };
  const f = state.filters;
  let opts = rec.latest || [];
  if (f.bag) opts = opts.filter((o) => o.bag === "yes");
  if (f.direct) opts = opts.filter((o) => o.stops === 0);
  const price = opts.length ? Math.min(...opts.map((o) => o.price)) : null;
  const key = f.bag ? "min_bag" : "min";
  const series = (rec.history || []).filter((h) => h[key] != null).map((h) => ({ t: h.t, v: h[key] }));
  let delta = null;
  if (!f.direct && series.length >= 2) delta = series[series.length - 1].v - series[series.length - 2].v;
  const visa = rec.meta?.visa || { code: "unknown", text: "未知" };
  return { rec, opts, price, series, delta, visa };
}
function passesFilters(route, v) {
  if (state.filters.visa && !(v.visa && VISA_OK.has(v.visa.code))) return false;
  if ((state.filters.bag || state.filters.direct) && v.rec && v.price == null) return false;
  return true;
}

/* ---------------------------------------------------------------- render */
function chartSvg(series, target) {
  if (series.length < 2) return `<p class="muted small">至少需要两次检查才能画出走势（目前 ${series.length} 次）。</p>`;
  const W = 320, H = 130, L = 8, R = 8, T = 18, B = 22;
  const vals = series.map((s) => s.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (target) { lo = Math.min(lo, target); hi = Math.max(hi, target); }
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = (i) => L + (i / (series.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const pts = series.map((s, i) => `${x(i).toFixed(1)},${y(s.v).toFixed(1)}`).join(" ");
  const minI = vals.indexOf(Math.min(...vals));
  let minLabelY = y(vals[minI]) + 14 < H - B ? y(vals[minI]) + 14 : y(vals[minI]) - 8;
  if (target && Math.abs(minLabelY - y(target)) < 14) minLabelY = y(vals[minI]) - 8; // keep clear of the target line
  const d0 = series[0].t.slice(5, 10), d1 = series[series.length - 1].t.slice(5, 10);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="价格走势">
    ${target ? `<line class="goal" x1="${L}" x2="${W - R}" y1="${y(target)}" y2="${y(target)}"/><text x="${L}" y="${y(target) - 4}">目标 ${target}</text>` : ""}
    <polyline class="line" points="${pts}"/>
    <circle class="dot" cx="${x(minI)}" cy="${y(vals[minI])}" r="4"/>
    <text x="${x(minI)}" y="${minLabelY}" text-anchor="${minI > series.length / 2 ? "end" : "start"}">最低 ${Math.round(vals[minI])}</text>
    <text x="${L}" y="${H - 6}">${d0}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${d1}</text>
  </svg>`;
}
function bagText(b) { return b === "yes" ? "含托运" : b === "no" ? "托运另付" : "托运未知"; }
function stopsText(n) { return n === 0 ? "直飞" : `${n} 次中转`; }
function durText(m) { return m ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}` : ""; }

function cardHtml(r, v) {
  const open = state.openId === r.id;
  const title = r.label ? `${esc(r.label)}` : `${esc(r.from)} → ${esc(r.to)}`;
  const trip = `${esc(r.from)} → ${esc(r.to)} · ${fmtDate(r.depart)}${r.return ? " ~ " + fmtDate(r.return) : "（单程）"}`;
  let right = `<div class="right"><div class="price muted">—</div></div>`;
  if (v.rec && v.price != null) {
    const d = v.delta;
    right = `<div class="right"><div class="price">${money(v.price)}</div>` + (d == null || d === 0 ? "" :
      `<div class="delta ${d < 0 ? "down" : "up"}">较上次 ${d < 0 ? "▼" : "▲"} ${Math.abs(Math.round(d))}</div>`) + `</div>`;
  }
  const badges = [];
  if (v.rec) {
    badges.push(`<span class="badge ${VISA_CLASS[v.visa.code] || ""}">签证：${esc(v.visa.text)}</span>`);
    const best = (v.opts || []).find((o) => o.price === v.price);
    if (best) {
      badges.push(`<span class="badge ${best.bag === "yes" ? "ok" : ""}">${bagText(best.bag)}</span>`);
      badges.push(`<span class="badge">${stopsText(best.stops)}</span>`);
    }
    if (r.alert_below) badges.push(`<span class="badge ${v.price != null && v.price <= r.alert_below ? "ok" : ""}">目标 ${r.alert_below}</span>`);
  } else {
    badges.push(`<span class="badge">等待首次检查</span>`);
  }
  if (r.enabled === false) badges.push(`<span class="badge warn">已暂停</span>`);
  const sub = `<div class="sub">${trip}</div>`;

  let detail = "";
  if (open) {
    const rows = (v.opts || []).slice(0, 8).map((o) =>
      `<tr><td>${esc(o.airline)}</td><td>${stopsText(o.stops)} ${durText(o.minutes)}</td><td class="nw">${bagText(o.bag)}</td><td class="num nw">${money(o.price)}</td></tr>`).join("");
    detail = `<div class="detail">
      ${v.rec ? chartSvg(v.series, Number(r.alert_below) || 0) : ""}
      ${v.rec ? `<p class="muted small">最近检查：${ago(v.rec.meta?.last_checked)}${state.filters.direct ? "（走势图包含非直飞）" : ""}</p>` : `<p class="muted small">还没有价格数据。点右上角“立即检查”，或等待每天的自动检查。</p>`}
      ${v.rec?.meta?.last_error ? `<p class="err">上次检查出错：${esc(v.rec.meta.last_error)}</p>` : ""}
      ${rows ? `<table><thead><tr><th>航空公司</th><th>行程</th><th>行李</th><th class="num">价格</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="muted small">“托运未知”表示价格数据里没有写明，订票前请自己确认；签证信息仅供参考。</p>` : ""}
      <div class="tools">
        <a class="btn" href="${gflights(r)}" target="_blank" rel="noopener">在 Google Flights 查看</a>
        <button class="ghost" data-act="edit" data-id="${esc(r.id)}">编辑</button>
        <button class="ghost" data-act="toggle" data-id="${esc(r.id)}">${r.enabled === false ? "恢复追踪" : "暂停"}</button>
        <button class="ghost" data-act="delete" data-id="${esc(r.id)}">删除</button>
      </div>
    </div>`;
  }
  return `<article class="card ${open ? "open" : ""} ${r.enabled === false ? "off" : ""}">
    <button class="card-top" data-act="open" data-id="${esc(r.id)}" aria-expanded="${open}">
      <div><div class="route">${title}</div>${sub}</div>${right}
      <div class="badges">${badges.join("")}</div>
    </button>${detail}</article>`;
}

function render() {
  $("#f-visa").checked = state.filters.visa;
  $("#f-bag").checked = state.filters.bag;
  $("#f-direct").checked = state.filters.direct;
  $("#updated").textContent = state.prices.updated ? `数据更新：${ago(state.prices.updated)}` : "还没有价格数据";
  const routes = state.routesDoc.routes || [];
  if (!routes.length) {
    $("#list").innerHTML = `<div class="empty">还没有航线。<br>点下面的“添加航线”开始追踪。</div>`;
    return;
  }
  const cards = [];
  let hidden = 0;
  for (const r of routes) {
    const v = viewFor(r);
    if (!passesFilters(r, v)) { hidden++; continue; }
    cards.push(cardHtml(r, v));
  }
  $("#list").innerHTML = cards.join("") + (hidden ? `<p class="muted small" style="text-align:center">有 ${hidden} 条航线被筛选条件隐藏</p>` : "");
}

/* ---------------------------------------------------------------- GitHub saving */
function ghHeaders() {
  return { Authorization: `Bearer ${store.get("token").trim()}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
}
const b64 = (s) => { const bytes = new TextEncoder().encode(s); let bin = ""; bytes.forEach((b) => (bin += String.fromCharCode(b))); return btoa(bin); };
async function gh(path, opts = {}) {
  const r = await fetch(`https://api.github.com${path}`, { ...opts, headers: { ...ghHeaders(), ...(opts.headers || {}) } });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg += " " + (await r.json()).message; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}
function needToken() {
  if (!store.get("token").trim() || !repoName()) {
    toast("请先在“设置”里填写 GitHub 令牌（和仓库）");
    openSettings();
    return true;
  }
  return false;
}
async function saveRoutesDoc(doc, message) {
  if (needToken()) return false;
  const repo = repoName();
  try {
    let sha;
    try { sha = (await gh(`/repos/${repo}/contents/docs/routes.json`)).sha; } catch (e) { if (!String(e.message).startsWith("404")) throw e; }
    await gh(`/repos/${repo}/contents/docs/routes.json`, {
      method: "PUT",
      body: JSON.stringify({ message, content: b64(JSON.stringify(doc, null, 2) + "\n"), ...(sha ? { sha } : {}) }),
    });
    state.routesDoc = doc;
    render();
    toast("已保存。网页约 1–2 分钟后对所有设备生效");
    return true;
  } catch (e) {
    toast(`保存失败：${e.message}`, 6000);
    return false;
  }
}
async function runNow() {
  if (needToken()) return;
  const btn = $("#btn-run"); btn.disabled = true;
  try {
    const repo = repoName();
    const info = await gh(`/repos/${repo}`);
    await gh(`/repos/${repo}/actions/workflows/check.yml/dispatches`, { method: "POST", body: JSON.stringify({ ref: info.default_branch }) });
    toast("已开始检查，约 1–3 分钟后刷新页面查看", 6000);
  } catch (e) {
    toast(`启动失败：${e.message}（令牌需要 Actions 读写权限）`, 7000);
  } finally { btn.disabled = false; }
}

/* ---------------------------------------------------------------- dialogs */
function confirmBox(title, text, okLabel = "确定") {
  return new Promise((resolve) => {
    const dlg = $("#dlg-confirm");
    $("#confirm-title").textContent = title; $("#confirm-text").textContent = text; $("#confirm-ok").textContent = okLabel;
    dlg.onclose = () => resolve(dlg.returnValue === "ok");
    $("#confirm-ok").value = "ok";
    dlg.showModal();
  });
}
let editingId = null;
function openRoute(id) {
  editingId = id || null;
  const f = $("#form-route"); f.reset();
  $("#route-title").textContent = id ? "编辑航线" : "添加航线";
  const r = id ? state.routesDoc.routes.find((x) => x.id === id) : null;
  if (r) {
    for (const k of ["label", "from", "to", "depart", "return", "alert_below"]) f.elements[k].value = r[k] ?? "";
    f.elements.need_checked_bag.checked = !!r.need_checked_bag;
    f.elements.visa_ok_only.checked = !!r.visa_ok_only;
    f.elements.enabled.checked = r.enabled !== false;
  } else {
    f.elements.depart.min = new Date().toISOString().slice(0, 10);
  }
  $("#dlg-route").showModal();
}
function openSettings() {
  if ($("#dlg-settings").open) return;
  const f = $("#form-settings");
  const s = state.routesDoc.settings || {};
  f.elements.currency.value = s.currency || "CAD";
  f.elements.daily_search_limit.value = s.daily_search_limit || 3;
  f.elements.token.value = store.get("token");
  f.elements.repo.value = store.get("repo");
  $("#dlg-settings").showModal();
}
const newId = () => "r" + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-3);
function cloneDoc() { return JSON.parse(JSON.stringify(state.routesDoc)); }

/* ---------------------------------------------------------------- events */
document.addEventListener("click", async (e) => {
  if (e.target.closest("[data-close]")) { e.target.closest("dialog").close(); return; }
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const id = el.dataset.id;
  const act = el.dataset.act;
  if (act === "open") { state.openId = state.openId === id ? null : id; render(); }
  else if (act === "edit") openRoute(id);
  else if (act === "toggle") {
    const doc = cloneDoc(); const r = doc.routes.find((x) => x.id === id);
    r.enabled = r.enabled === false; await saveRoutesDoc(doc, `${r.enabled ? "Resume" : "Pause"} route ${r.from}-${r.to}`);
  } else if (act === "delete") {
    const r = state.routesDoc.routes.find((x) => x.id === id);
    if (!(await confirmBox("删除航线", `确定删除 ${r.from} → ${r.to}（${r.depart}）及其价格记录吗？`, "删除"))) return;
    const doc = cloneDoc(); doc.routes = doc.routes.filter((x) => x.id !== id);
    if (await saveRoutesDoc(doc, `Delete route ${r.from}-${r.to}`)) state.openId = null;
  }
});
$("#btn-add").onclick = () => openRoute(null);
$("#btn-settings").onclick = openSettings;
$("#btn-run").onclick = runNow;
for (const [id, key] of [["#f-visa", "visa"], ["#f-bag", "bag"], ["#f-direct", "direct"]]) {
  $(id).onchange = (e) => { state.filters[key] = e.target.checked; store.set("f_" + key, e.target.checked ? "1" : "0"); render(); };
}

$("#form-route").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const from = f.from.value.trim().toUpperCase(), to = f.to.value.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(from) || !/^[A-Z]{3}$/.test(to)) return toast("机场代码必须是 3 个字母");
  if (from === to) return toast("出发和到达机场不能相同");
  if (!f.depart.value) return toast("请选择出发日期");
  if (f.return.value && f.return.value < f.depart.value) return toast("返程日期不能早于出发日期");
  const route = {
    id: editingId || newId(), label: f.label.value.trim(), from, to, depart: f.depart.value,
    return: f.return.value || null,
    alert_below: f.alert_below.value ? Number(f.alert_below.value) : null,
    need_checked_bag: f.need_checked_bag.checked, visa_ok_only: f.visa_ok_only.checked, enabled: f.enabled.checked,
  };
  const doc = cloneDoc();
  if (editingId) doc.routes = doc.routes.map((r) => (r.id === editingId ? route : r)); else doc.routes.push(route);
  const btn = $("#btn-save-route"); btn.disabled = true;
  const ok = await saveRoutesDoc(doc, `${editingId ? "Edit" : "Add"} route ${from}-${to}`);
  btn.disabled = false;
  if (ok) $("#dlg-route").close();
});

$("#form-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  store.set("token", f.token.value.trim()); store.set("repo", f.repo.value.trim());
  const limit = Math.max(1, Math.min(50, parseInt(f.daily_search_limit.value, 10) || 3));
  const cur = state.routesDoc.settings || {};
  if (cur.currency !== f.currency.value || cur.daily_search_limit !== limit) {
    const doc = cloneDoc(); doc.settings = { ...doc.settings, currency: f.currency.value, daily_search_limit: limit };
    if (!(await saveRoutesDoc(doc, "Update settings"))) return;
  }
  $("#dlg-settings").close();
  toast("设置已保存");
});

load();
