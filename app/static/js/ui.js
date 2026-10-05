// DOM + formatting helpers.
// Every piece of data (CSV values, file names, error messages) is inserted with
// textContent via h(), never innerHTML, so uploaded content cannot inject markup.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    // Styles go through the CSSOM: the Content-Security-Policy blocks style="" attributes.
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

const SVGNS = "http://www.w3.org/2000/svg";
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Static icon paths (constants only — never data).
const ICONS = {
  check: "M3.5 8.5l3 3 6-7",
  cross: "M4 4l8 8M12 4l-8 8",
  copy: "M5 5h7v7H5zM3 3h7v1M3 3v7h1",
  clock: "M8 4v4l3 2M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z",
  up: "M8 13V3M4 7l4-4 4 4",
  down: "M8 3v10M4 9l4 4 4-4",
  alert: "M8 2l6.5 11.5h-13zM8 6.5v3.5M8 12v.01",
  info: "M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 7v4.5M8 4.8v.01",
  upload: "M8 11V2M4.5 5.5L8 2l3.5 3.5M2 10v3.5h12V10",
};
export function icon(name, cls = "") {
  return s("svg", { viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.6,
    "stroke-linecap": "round", "stroke-linejoin": "round", class: cls, "aria-hidden": "true" },
    s("path", { d: ICONS[name] || ICONS.info }));
}

// ---------- formatting (Indian locale: lakh/crore grouping, ₹) ----------
const nf = new Intl.NumberFormat("en-IN");
const nf1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
export const num = (v) => (v === null || v === undefined ? "—" : nf.format(v));
export const pct = (v, d = 1) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(d)}%`);
export function money(v, compact = true) {
  if (v === null || v === undefined) return "—";
  if (compact && Math.abs(v) >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (compact && Math.abs(v) >= 1e5) return `₹${(v / 1e5).toFixed(1)} L`;
  return `₹${nf.format(Math.round(v))}`;
}
export const money2 = (v) => `₹${new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)}`;
export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${nf1.format(n / 1024)} KB`;
  return `${nf1.format(n / 1024 / 1024)} MB`;
}
export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
export function monthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleString("en-IN", { month: "short" }) + (m === 1 ? ` ${y}` : "");
}
export function ago(iso) {
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
  return when(iso);
}

// ---------- status chips: always icon + label, never colour alone ----------
const STATUS = {
  COMPLETED: ["chip-good", "check", "Completed"],
  FAILED: ["chip-crit", "cross", "Failed"],
  PROCESSING: ["chip-info", "clock", "Processing"],
  QUEUED: ["chip-info", "clock", "Queued"],
};
export function statusChip(status) {
  const [cls, ic, label] = STATUS[status] || ["chip-info", "info", status];
  return h("span", { class: `chip ${cls}` }, icon(ic), label);
}

// ---------- states ----------
export function loading(lines = 3) {
  return h("div", { class: "panel panel-pad", "aria-busy": "true" },
    h("span", { class: "sr-only" }, "Loading"),
    Array.from({ length: lines }, (_, i) => h("div", { class: "skeleton sk-line", style: `width:${90 - i * 15}%` })));
}
export function emptyState(title, text, action) {
  return h("div", { class: "panel state" }, h("h2", {}, title), h("p", {}, text), action || null);
}
export function errorState(err, retry) {
  return h("div", { class: "panel state error", role: "alert" },
    h("h2", {}, "Couldn't load this view"),
    h("p", {}, err?.message || "The server did not respond."),
    retry ? h("button", { class: "btn", onclick: retry }, "Try again") : null);
}
export function alertBox(title, detail) {
  return h("div", { class: "alert", role: "alert" }, icon("alert"),
    h("div", {}, h("p", {}, h("b", {}, title)), detail ? h("p", {}, detail) : null));
}

export function toast(message, kind = "info") {
  const region = document.getElementById("toasts");
  const t = h("div", { class: `toast ${kind === "error" ? "error" : ""}` }, message);
  region.append(t);
  setTimeout(() => t.remove(), 5000);
}

// ---------- shared tooltip ----------
const tip = () => document.getElementById("tooltip");
export function showTip(evt, ...content) {
  const t = tip();
  t.replaceChildren(...content.map((c) => (c instanceof Node ? c : document.createTextNode(c))));
  t.hidden = false;
  moveTip(evt);
}
export function moveTip(evt) {
  const t = tip();
  const pad = 14;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  const r = t.getBoundingClientRect();
  if (x + r.width > window.innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = evt.clientY - r.height - pad;
  t.style.left = `${x}px`;
  t.style.top = `${y}px`;
}
export function hideTip() { tip().hidden = true; }
