// Dashboard: KPIs, insights, data-quality ledger and charts — all from /api/dashboard (live SQL).
import { get } from "../api.js";
import { barsH, columns, ledger } from "../charts.js";
import { h, icon, num, pct, money, money2, monthLabel, emptyState, errorState, loading } from "../ui.js";

const FILTER_KEYS = ["date_from", "date_to", "region", "category", "status"];

export async function renderDashboard(root, { params }) {
  const state = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) || ""]));
  let meta;
  try {
    meta = await get("/api/meta");
  } catch (err) {
    root.append(errorState(err, () => renderDashboard(root.replaceChildren() || root, { params })));
    return;
  }
  const hasData = meta.date_range.max !== null;

  const head = h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Operations dashboard"),
      h("p", { id: "dash-sub" }, hasData ? "" : "")));
  root.append(head);

  if (!hasData) {
    root.append(emptyState("No data yet", "Upload an orders CSV and the dashboard will fill in from the processed records.",
      h("a", { class: "btn btn-primary", href: "#/upload" }, "Upload a dataset")));
    return;
  }

  // ---------- filters ----------
  const sel = (key, label, options) => h("div", { class: "field" },
    h("label", { for: `f-${key}` }, label),
    h("select", { class: "select", id: `f-${key}`, onchange: (e) => setFilter(key, e.target.value) },
      h("option", { value: "" }, `All`),
      options.map((o) => h("option", { value: o, selected: state[key] === o ? true : null }, o))));
  const dateField = (key, label) => h("div", { class: "field" },
    h("label", { for: `f-${key}` }, label),
    h("input", { class: "input", type: "date", id: `f-${key}`, value: state[key],
      min: meta.date_range.min, max: meta.date_range.max, onchange: (e) => setFilter(key, e.target.value) }));
  const filters = h("section", { class: "panel filters", "aria-label": "Filters" },
    dateField("date_from", "From"), dateField("date_to", "To"),
    sel("region", "Region", meta.filter_options.region),
    sel("category", "Category", meta.filter_options.category),
    sel("status", "Order status", meta.filter_options.status),
    h("button", { class: "btn", onclick: () => { FILTER_KEYS.forEach((k) => (state[k] = "")); syncUrl(); location.reload(); } }, "Clear filters"));

  const body = h("div", { class: "stack" });
  root.append(filters, h("div", { style: "height:24px" }), body);

  function setFilter(key, value) {
    state[key] = value;
    syncUrl();
    load();
  }
  function syncUrl() {
    const qs = new URLSearchParams(Object.entries(state).filter(([, v]) => v)).toString();
    history.replaceState(null, "", `#/dashboard${qs ? `?${qs}` : ""}`);
  }

  // insights are global (whole dataset), load once
  const insightsSlot = h("div", {}, loading(3));
  get("/api/insights").then((list) => insightsSlot.replaceChildren(insightsPanel(list)))
    .catch((err) => insightsSlot.replaceChildren(errorState(err)));

  async function load() {
    body.replaceChildren(loading(2), h("div", { class: "skeleton sk-block" }));
    try {
      const d = await get("/api/dashboard", state);
      render(d);
    } catch (err) {
      body.replaceChildren(errorState(err, load));
    }
  }

  function render(d) {
    const k = d.kpis;
    const sub = document.getElementById("dash-sub");
    const filtered = FILTER_KEYS.some((x) => state[x]);
    sub.textContent = k.orders
      ? `${num(k.orders)} clean records${filtered ? " match the filters" : ""}, ${fmtDate(d.date_range.min)} to ${fmtDate(d.date_range.max)}. Every figure is calculated from the database.`
      : "No records match these filters.";

    if (!k.orders) {
      body.replaceChildren(emptyState("Nothing matches these filters", "Widen the date range or clear a filter to see results.",
        h("button", { class: "btn", onclick: () => { FILTER_KEYS.forEach((x) => (state[x] = "")); syncUrl(); location.reload(); } }, "Clear filters")));
      return;
    }

    const kpi = (label, value, subText) => h("div", { class: "kpi" },
      h("div", { class: "label" }, label), h("div", { class: "value" }, value), subText ? h("div", { class: "sub" }, subText) : null);
    const kpis = h("section", { class: "panel kpis", "aria-label": "Key figures" },
      kpi("Revenue", money(k.revenue), `${num(k.units)} units`),
      kpi("Orders", num(k.orders)),
      kpi("Average order value", money(k.avg_order_value, false)),
      kpi("Delivered", pct(k.delivered_pct)),
      kpi("Late deliveries", k.sla_breach_pct === null ? "—" : pct(k.sla_breach_pct),
        `over ${meta.sla_days} days; average ${k.avg_delivery_days ?? "—"} days`),
      kpi("Cancelled or returned", pct(k.cancel_return_pct)));

    const dq = d.data_quality;
    const quality = h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, "Data behind these figures"),
        h("a", { class: "small", href: "#/quality" }, "Open quality reports")),
      h("div", { class: "panel-pad" }, ledger({ total: dq.records_received, valid: dq.records_valid,
        invalid: dq.records_invalid, duplicate: dq.records_duplicate, rate: dq.success_rate,
        caption: `rows received across ${dq.uploads_completed} upload${dq.uploads_completed === 1 ? "" : "s"}` })));

    const monthly = d.monthly.map((m) => ({ label: monthLabel(m.month), fullLabel: m.month, value: m.revenue, orders: m.orders, extra: [num(m.orders)] }));
    const trend = chartPanel("Revenue by month", `${d.monthly.length} months`,
      columns(monthly, { title: "Revenue by month", fmt: money, axisFmt: money, tableHeaders: ["Month", "Revenue", "Orders"],
        tip: (x) => [h("b", {}, x.fullLabel), h("br"), `${money2(x.value)}`, h("br"), `${num(x.orders)} orders`] }));

    const revTip = (x) => [h("b", {}, x.label), h("br"), money2(x.value), h("br"), `${num(x.count)} orders`];
    const byCat = chartPanel("Revenue by category", null, barsH(d.by_category, { title: "Revenue by category", fmt: money, tip: revTip, tableHeaders: ["Category", "Revenue"] }));
    const byRegion = chartPanel("Revenue by region", null, barsH(d.by_region, { title: "Revenue by region", fmt: money, tip: revTip, tableHeaders: ["Region", "Revenue"] }));

    const slaItems = d.sla_by_region.filter((r) => r.delivered > 0)
      .map((r) => ({ label: r.region, value: r.breach_pct, count: r.delivered, breached: r.breached, avg: r.avg_delivery_days }))
      .sort((a, b) => b.value - a.value);
    const LIMIT = 10;
    const sla = chartPanel("Late deliveries by region", `share of deliveries over ${meta.sla_days} days`,
      barsH(slaItems, { title: "Late deliveries by region", fmt: (v) => pct(v), threshold: { value: LIMIT },
        flag: (x) => x.value > LIMIT,
        note: `Red bars are above the ${LIMIT}% tolerance line.`,
        tableHeaders: ["Region", "Late deliveries"],
        tip: (x) => [h("b", {}, x.label), h("br"), `${num(x.breached)} of ${num(x.count)} deliveries late (${pct(x.value)})`, h("br"), `Average ${x.avg} days`] }));

    const cities = chartPanel("Top cities by revenue", "top 10", barsH(d.top_cities, { title: "Top cities by revenue", fmt: money, tip: revTip, tableHeaders: ["City", "Revenue"] }));
    const status = chartPanel("Orders by status", null, barsH(d.by_status, { title: "Orders by status", fmt: num,
      tip: (x) => [h("b", {}, x.label), h("br"), `${num(x.count)} orders (${pct((100 * x.count) / k.orders)})`], tableHeaders: ["Status", "Orders"] }));

    body.replaceChildren(kpis, insightsSlot, trend,
      h("div", { class: "grid-2" }, byCat, byRegion),
      h("div", { class: "grid-2" }, sla, status),
      cities, quality);
  }

  load();
}

function chartPanel(title, hint, chart) {
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, title), hint ? h("span", { class: "hint" }, hint) : null),
    h("div", { class: "chart" }, chart));
}

function insightsPanel(list) {
  const iconFor = { warning: "alert", positive: "up", info: "info" };
  return h("section", { class: "panel", "aria-labelledby": "ins-title" },
    h("div", { class: "panel-head" }, h("h2", { id: "ins-title" }, "What changed"),
      h("span", { class: "hint" }, "Calculated automatically across all data")),
    h("ul", { class: "insights" }, list.map((i) => h("li", { class: `insight ${i.kind}` },
      icon(iconFor[i.kind]),
      h("div", {}, h("h3", {}, i.title), h("p", {}, i.detail)),
      i.metric ? h("div", { class: "metric" }, i.metric) : null))));
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—";
}
