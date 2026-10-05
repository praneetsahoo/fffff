// Records explorer: search, filter, sort, paginate and export the clean records.
import { get, queryString } from "../api.js";
import { h, icon, num, money2, emptyState, errorState, loading } from "../ui.js";

const KEYS = ["q", "region", "category", "status", "city", "date_from", "date_to", "sla_breached", "upload_id", "sort", "order", "page", "page_size"];
const COLS = [
  ["order_id", "Order", false], ["order_date", "Date", false], ["region", "Region", false],
  ["city", "City", false], ["category", "Category", false], ["product", "Product", false],
  ["quantity", "Qty", true], ["unit_price", "Unit price", true], ["revenue", "Revenue", true],
  ["status", "Status", false], ["delivery_days", "Delivery", true],
];

export async function renderRecords(root, { params }) {
  const state = { sort: "order_date", order: "desc", page: "1", page_size: "25" };
  for (const k of KEYS) if (params.get(k)) state[k] = params.get(k);
  let debounce;

  let meta;
  try {
    meta = await get("/api/meta");
  } catch (err) {
    root.append(errorState(err));
    return;
  }

  const sub = h("p", {}, "Search the clean, validated records stored in the database.");
  const exportBtn = h("a", { class: "btn", href: "#" }, "Export CSV");
  root.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Records"), sub), exportBtn));

  if (!meta.date_range.max) {
    root.append(emptyState("No records yet", "Records appear here once an upload has been processed.",
      h("a", { class: "btn btn-primary", href: "#/upload" }, "Upload a dataset")));
    return;
  }

  const set = (k, v, resetPage = true) => {
    if (v === "" || v === null || v === false) delete state[k]; else state[k] = String(v);
    if (resetPage) state.page = "1";
    syncUrl();
    load();
  };
  const sel = (key, label, options) => h("div", { class: "field" },
    h("label", { for: `r-${key}` }, label),
    h("select", { class: "select", id: `r-${key}`, onchange: (e) => set(key, e.target.value) },
      h("option", { value: "" }, "All"),
      options.map((o) => h("option", { value: o, selected: state[key] === o ? true : null }, o))));
  const date = (key, label) => h("div", { class: "field" }, h("label", { for: `r-${key}` }, label),
    h("input", { class: "input", type: "date", id: `r-${key}`, value: state[key] || "", min: meta.date_range.min, max: meta.date_range.max,
      onchange: (e) => set(key, e.target.value) }));
  const search = h("input", { class: "input", id: "r-q", type: "search", value: state.q || "", maxlength: 100,
    placeholder: "Order ID, product or city", autocomplete: "off",
    oninput: (e) => { clearTimeout(debounce); debounce = setTimeout(() => set("q", e.target.value.trim()), 300); } });
  const late = h("input", { type: "checkbox", id: "r-late", checked: state.sla_breached === "true" ? true : null,
    onchange: (e) => set("sla_breached", e.target.checked ? "true" : "") });

  const uploadNote = state.upload_id
    ? h("div", { class: "chip chip-info" }, "Showing one upload ",
        h("button", { class: "btn-link", onclick: () => { set("upload_id", ""); uploadNote.remove(); } }, "show all"))
    : null;

  root.append(h("section", { class: "panel filters", "aria-label": "Filters" },
    h("div", { class: "field grow" }, h("label", { for: "r-q" }, "Search"), search),
    sel("region", "Region", meta.filter_options.region),
    sel("category", "Category", meta.filter_options.category),
    sel("status", "Status", meta.filter_options.status),
    sel("city", "City", meta.filter_options.city),
    date("date_from", "From"), date("date_to", "To"),
    h("label", { class: "check", for: "r-late" }, late, `Late deliveries only (over ${meta.sla_days} days)`),
    uploadNote));

  const results = h("section", { class: "panel", style: "margin-top:24px", "aria-live": "polite" });
  root.append(results);

  function syncUrl() {
    const qs = new URLSearchParams(Object.entries(state).filter(([, v]) => v)).toString();
    history.replaceState(null, "", `#/records?${qs}`);
    const exportParams = Object.fromEntries(Object.entries(state).filter(([k]) => !["sort", "order", "page", "page_size"].includes(k)));
    exportBtn.href = `/api/records/export?${queryString(exportParams)}`;
  }

  async function load() {
    results.replaceChildren(loading(6));
    try {
      const page = await get("/api/records", state);
      render(page);
    } catch (err) {
      results.replaceChildren(errorState(err, load));
    }
  }

  function render(p) {
    sub.textContent = `${num(p.total)} clean record${p.total === 1 ? "" : "s"} match. Click a column heading to sort.`;
    if (!p.total) {
      results.replaceChildren(h("div", { class: "state" }, h("h2", {}, "No records match"),
        h("p", {}, state.q ? `Nothing matches "${state.q}" with the current filters.` : "Try widening the filters.")));
      return;
    }
    const header = COLS.map(([key, label, isNum]) => {
      const active = state.sort === key;
      const dir = active ? (state.order === "asc" ? "ascending" : "descending") : null;
      return h("th", { class: isNum ? "num" : "", "aria-sort": dir, scope: "col" },
        h("button", { class: "sort", onclick: () => {
          state.order = active && state.order === "desc" ? "asc" : "desc";
          state.sort = key; state.page = "1"; syncUrl(); load();
        } }, label, h("span", { class: "arrow", "aria-hidden": "true" }, active ? (state.order === "asc" ? "▲" : "▼") : "↕")));
    });
    const rows = p.items.map((o) => h("tr", {},
      h("td", {}, o.order_id), h("td", {}, o.order_date), h("td", {}, o.region),
      h("td", {}, o.city), h("td", {}, o.category), h("td", {}, o.product),
      h("td", { class: "num" }, num(o.quantity)), h("td", { class: "num" }, money2(o.unit_price)),
      h("td", { class: "num" }, money2(o.revenue)), h("td", {}, o.status),
      h("td", { class: "num" }, o.delivery_days === null ? h("span", { class: "muted" }, "—")
        : o.sla_breached ? h("span", { class: "chip chip-crit", title: `Over the ${meta.sla_days}-day target` }, icon("alert"), `${o.delivery_days} d late`)
        : `${o.delivery_days} d`)));

    const pageNum = Number(state.page), size = Number(state.page_size);
    const from = (pageNum - 1) * size + 1, to = Math.min(p.total, pageNum * size);
    const go = (n) => { state.page = String(n); syncUrl(); load(); results.scrollIntoView({ block: "start" }); };
    results.replaceChildren(
      h("div", { class: "table-wrap" }, h("table", {},
        h("caption", { class: "sr-only" }, "Processed records"),
        h("thead", {}, h("tr", {}, header)), h("tbody", {}, rows))),
      h("div", { class: "pager" },
        h("span", { class: "muted small" }, `Showing ${num(from)}–${num(to)} of ${num(p.total)}`),
        h("div", { class: "btn-group" },
          h("label", { class: "sr-only", for: "r-size" }, "Rows per page"),
          h("select", { class: "select", id: "r-size", onchange: (e) => set("page_size", e.target.value) },
            [25, 50, 100].map((n) => h("option", { value: n, selected: size === n ? true : null }, `${n} per page`))),
          h("button", { class: "btn", disabled: pageNum <= 1 ? true : null, onclick: () => go(pageNum - 1) }, "Previous"),
          h("span", { class: "small muted", style: "align-self:center" }, `Page ${num(pageNum)} of ${num(p.pages)}`),
          h("button", { class: "btn", disabled: pageNum >= p.pages ? true : null, onclick: () => go(pageNum + 1) }, "Next"))));
  }

  syncUrl();
  load();
  return () => clearTimeout(debounce);
}
