// Hand-built SVG charts (no CDN dependency on demo day).
// Single-series bars use one hue; thin marks with rounded data-ends anchored to the baseline;
// recessive grid; selective direct labels; hover tooltips; a table view for accessibility.
import { h, s, showTip, moveTip, hideTip } from "./ui.js";

// Render fn(width) into container now and whenever its width changes.
function responsive(container, draw) {
  let lastW = 0;
  const render = () => {
    const w = Math.max(260, Math.floor(container.clientWidth));
    if (w === lastW) return;
    lastW = w;
    container.replaceChildren(draw(w));
  };
  render();
  new ResizeObserver(render).observe(container);
}

// Path for a bar with 4px rounded corners on the data end only.
function hBarPath(x, y, w, hgt, r = 4) {
  r = Math.min(r, w, hgt / 2);
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt - r}Q${x + w},${y + hgt} ${x + w - r},${y + hgt}H${x}Z`;
}
function vBarPath(x, y, w, hgt, r = 4) {
  r = Math.min(r, hgt, w / 2);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function tableView(caption, headers, rows) {
  return h("details", { class: "table-view" },
    h("summary", {}, "Show as table"),
    h("table", {},
      h("caption", { class: "sr-only" }, caption),
      h("thead", {}, h("tr", {}, headers.map((x, i) => h("th", { class: i ? "num" : "" }, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i ? "num" : "" }, c)))))));
}

/**
 * Horizontal bars, sorted as given. items: [{label, value, count?}]
 * opts: { fmt, tip(d) -> [nodes], flag(d) -> bool, threshold: {value, label}, title }
 */
export function barsH(items, opts = {}) {
  const fmt = opts.fmt || String;
  const wrap = h("div", {});
  const holder = h("div", {});
  wrap.append(holder);
  if (!items.length) {
    holder.append(h("p", { class: "muted small" }, "No data for the current filters."));
    return wrap;
  }
  const row = 30, gap = 8, top = 4;
  const height = top + items.length * (row + gap);
  const max = Math.max(...items.map((d) => d.value), opts.threshold?.value || 0);

  responsive(holder, (W) => {
    const labelW = Math.min(150, Math.max(90, W * 0.28));
    const valueW = 76;
    const plotW = W - labelW - valueW;
    const x = (v) => (max ? (v / max) * plotW : 0);
    const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, width: W, height, role: "img", "aria-label": opts.title || "Bar chart" });
    items.forEach((d, i) => {
      const y = top + i * (row + gap);
      const bw = Math.max(2, x(d.value));
      const flagged = opts.flag?.(d);
      const g = s("g", { class: "row" },
        s("text", { class: "lbl", x: labelW - 10, y: y + row / 2 + 4, "text-anchor": "end" }, d.label),
        s("path", { class: `bar${flagged ? " flag" : ""}`, d: hBarPath(labelW, y + 6, bw, row - 12) }),
        s("text", { class: "val", x: labelW + bw + 8, y: y + row / 2 + 4 }, fmt(d.value)),
        s("rect", { class: "hit", x: 0, y, width: W, height: row,
          onmouseenter: (e) => showTip(e, ...(opts.tip ? opts.tip(d) : [h("b", {}, d.label), ` ${fmt(d.value)}`])),
          onmousemove: moveTip, onmouseleave: hideTip }));
      svg.append(g);
    });
    svg.append(s("line", { class: "base", x1: labelW, x2: labelW, y1: 0, y2: height - gap }));
    if (opts.threshold) {
      const tx = labelW + x(opts.threshold.value);
      svg.append(s("line", { class: "threshold", x1: tx, x2: tx, y1: 0, y2: height - gap }));
    }
    return svg;
  });
  if (opts.note) wrap.append(h("p", { class: "chart-note" }, opts.note));
  wrap.append(tableView(opts.title || "Chart data", opts.tableHeaders || ["Label", "Value"],
    items.map((d) => [d.label, fmt(d.value)])));
  return wrap;
}

/**
 * Vertical columns over time. items: [{label, value, sub?}]
 */
export function columns(items, opts = {}) {
  const fmt = opts.fmt || String;
  const axisFmt = opts.axisFmt || fmt;
  const wrap = h("div", {});
  const holder = h("div", {});
  wrap.append(holder);
  if (!items.length) {
    holder.append(h("p", { class: "muted small" }, "No data for the current filters."));
    return wrap;
  }
  const H = 240, top = 22, bottom = 26, left = 64;
  const max = niceMax(Math.max(...items.map((d) => d.value)));
  const peak = items.reduce((a, b) => (b.value > a.value ? b : a), items[0]);
  const last = items[items.length - 1];

  responsive(holder, (W) => {
    const plotW = W - left - 4, plotH = H - top - bottom;
    const step = plotW / items.length;
    const bw = Math.max(6, Math.min(48, step * 0.62));
    const y = (v) => top + plotH - (v / max) * plotH;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.title || "Column chart" });
    const grid = s("g", { class: "grid" });
    const axis = s("g", { class: "axis" });
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i, yy = y(v);
      if (i) grid.append(s("line", { x1: left, x2: W, y1: yy, y2: yy }));
      axis.append(s("text", { x: left - 8, y: yy + 4, "text-anchor": "end" }, axisFmt(v)));
    }
    svg.append(grid, axis);
    const every = Math.max(1, Math.ceil(48 / step)); // thin labels on narrow screens
    items.forEach((d, i) => {
      const cx = left + step * i + step / 2;
      const hgt = Math.max(1, top + plotH - y(d.value));
      const showLabel = i % every === 0 || i === items.length - 1 && every === 1;
      const g = s("g", { class: "row" },
        s("path", { class: "bar", d: vBarPath(cx - bw / 2, y(d.value), bw, hgt) }),
        showLabel ? s("text", { class: "lbl", x: cx, y: H - 8, "text-anchor": "middle" }, d.label) : null,
        s("rect", { class: "hit", x: cx - step / 2, y: top, width: step, height: plotH,
          onmouseenter: (e) => showTip(e, ...(opts.tip ? opts.tip(d) : [h("b", {}, d.label), ` ${fmt(d.value)}`])),
          onmousemove: moveTip, onmouseleave: hideTip }));
      if (d === peak || d === last) {
        g.insertBefore(s("text", { class: "val", x: cx, y: y(d.value) - 6, "text-anchor": "middle" }, fmt(d.value)), g.lastChild);
      }
      svg.append(g);
    });
    svg.append(s("line", { class: "base", x1: left, x2: W, y1: top + plotH, y2: top + plotH }));
    return svg;
  });
  wrap.append(tableView(opts.title || "Chart data", opts.tableHeaders || ["Period", "Value"],
    items.map((d) => [d.fullLabel || d.label, fmt(d.value), ...(d.extra || [])])));
  return wrap;
}

/**
 * The row-accounting ledger: every received row as loaded / rejected / duplicate.
 */
export function ledger({ total, valid, invalid, duplicate, rate, caption = "rows received" }) {
  const nf = new Intl.NumberFormat("en-IN");
  const share = (n) => (total ? (100 * n) / total : 0);
  const segs = [
    { n: valid, cls: "good", label: "Loaded", desc: "clean and stored in the database" },
    { n: invalid, cls: "crit", label: "Rejected", desc: "failed validation, kept with reasons" },
    { n: duplicate, cls: "warn", label: "Duplicates", desc: "removed, kept with reasons" },
  ];
  const bar = h("div", { class: "ledger-bar", role: "img",
    "aria-label": `${nf.format(total)} rows: ${nf.format(valid)} loaded, ${nf.format(invalid)} rejected, ${nf.format(duplicate)} duplicates` });
  for (const sg of segs) {
    if (!sg.n) continue;
    bar.append(h("span", { class: `seg-${sg.cls}`, style: `flex:${sg.n} 0 0`,
      onmouseenter: (e) => showTip(e, h("b", {}, `${sg.label}: ${nf.format(sg.n)}`), ` (${share(sg.n).toFixed(1)}%) ${sg.desc}`),
      onmousemove: moveTip, onmouseleave: hideTip }));
  }
  return h("div", { class: "ledger" },
    h("div", { class: "ledger-total" },
      h("div", {}, h("div", { class: "big" }, nf.format(total)), h("div", { class: "muted small" }, caption)),
      h("div", { class: "rate" },
        h("div", { class: "big" }, rate === null || rate === undefined ? "—" : `${Number(rate).toFixed(1)}%`),
        h("div", { class: "muted small" }, "processing success"))),
    bar,
    h("div", { class: "ledger-legend" },
      segs.map((sg) => h("div", {},
        h("span", { class: `sw`, style: `background:var(--${sg.cls})`, "aria-hidden": "true" }),
        h("span", {}, `${sg.label} `, h("b", {}, nf.format(sg.n || 0)), " ",
          h("span", { class: "pct" }, `${share(sg.n || 0).toFixed(1)}%`))))));
}
