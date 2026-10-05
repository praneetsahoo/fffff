// Data-quality report for one upload: row accounting, issues per column, every rejected row.
import { get, post } from "../api.js";
import { ledger } from "../charts.js";
import { h, icon, num, pct, when, statusChip, alertBox, emptyState, errorState, loading, toast } from "../ui.js";

const PROBLEMS = [["missing", "Missing"], ["invalid_type", "Wrong type"], ["invalid_value", "Invalid value"], ["duplicate", "Duplicate"], ["malformed", "Malformed line"]];
const FIXES = [["standardized", "Standardised"], ["filled", "Default filled"]];

export async function renderQuality(root, { args }) {
  const timers = new Set();
  let uploads;
  try {
    uploads = await get("/api/uploads", { limit: 100 });
  } catch (err) {
    root.append(errorState(err));
    return;
  }
  if (!uploads.length) {
    root.append(h("div", { class: "page-head" }, h("h1", {}, "Data quality report")),
      emptyState("No uploads yet", "Upload a CSV to get a report of what was checked, fixed and rejected.",
        h("a", { class: "btn btn-primary", href: "#/upload" }, "Upload a dataset")));
    return;
  }
  const id = args[0] || (uploads.find((u) => u.status === "COMPLETED") || uploads[0]).id;

  const picker = h("select", { class: "select", id: "q-upload", onchange: (e) => { location.hash = `#/quality/${e.target.value}`; } },
    uploads.map((u) => h("option", { value: u.id, selected: u.id === id ? true : null },
      `${u.original_filename} (${when(u.created_at)})`)));
  if (!uploads.some((u) => u.id === id)) picker.prepend(h("option", { value: id, selected: true }, "Selected upload"));

  const downloads = h("div", { class: "btn-group" });
  root.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Data quality report"),
      h("p", {}, "Every row received is accounted for: loaded, rejected with reasons, or removed as a duplicate.")),
    h("div", { class: "field" }, h("label", { for: "q-upload" }, "Upload"), picker)));
  const body = h("div", { class: "stack" }, loading(5));
  root.append(body);

  async function load() {
    try {
      const up = await get(`/api/uploads/${id}`);
      if (up.status === "QUEUED" || up.status === "PROCESSING") {
        body.replaceChildren(h("div", { class: "panel state" }, h("h2", {}, "Still processing"),
          h("p", {}, `${up.original_filename} is at the "${up.stage}" step. This page will update when it finishes.`)));
        const t = setTimeout(() => { timers.delete(t); load(); }, 1500);
        timers.add(t);
        return;
      }
      if (up.status === "FAILED") {
        body.replaceChildren(h("div", { class: "panel panel-pad stack" },
          h("div", {}, statusChip("FAILED")),
          alertBox(`${up.original_filename} could not be processed`, up.error_message),
          h("p", { class: "muted small" }, "The original file is kept in storage, so it can be processed again without re-uploading."),
          h("div", { class: "btn-group" },
            h("button", { class: "btn btn-primary", onclick: async () => {
              try { await post(`/api/uploads/${id}/retry`); toast("Retry started"); load(); } catch (err) { toast(err.message, "error"); }
            } }, "Retry processing"),
            fileLink(id, "raw", "Download original"))));
        return;
      }
      const report = await get(`/api/uploads/${id}/quality`);
      render(report);
    } catch (err) {
      body.replaceChildren(err.status === 404
        ? emptyState("Upload not found", "It may have been removed. Pick another upload from the list.")
        : errorState(err, load));
    }
  }

  function render(rep) {
    const up = rep.upload;
    downloads.replaceChildren(fileLink(id, "raw", "Original file"), fileLink(id, "processed", "Cleaned data"), fileLink(id, "rejected", "Rejected rows"));
    const summary = h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, up.original_filename),
        h("span", { class: "hint" }, `Processed ${when(up.completed_at)}`)),
      h("div", { class: "panel-pad stack" },
        ledger({ total: up.total_rows, valid: up.valid_rows, invalid: up.invalid_rows, duplicate: up.duplicate_rows, rate: up.success_rate }),
        h("p", { class: "muted" }, `${pct(up.quality_score)} of rows arrived perfect. The rest of the loaded rows were repaired automatically, for example dates in other formats, inconsistent spelling, or a missing optional value.`),
        h("div", {}, h("p", { class: "small muted", style: "margin-bottom:8px" }, "Download"), downloads)));

    body.replaceChildren(summary, issuesMatrix(rep), rejectedPanel(id));
  }

  load();
  return () => timers.forEach((t) => clearTimeout(t));
}

function fileLink(id, kind, label) {
  return h("a", { class: "btn", href: `/api/uploads/${id}/files/${kind}`, download: "" }, label);
}

function issuesMatrix(rep) {
  const byCol = {};
  for (const i of rep.issues) (byCol[i.column] ||= {})[i.issue_type] = i.count;
  const cols = Object.keys(byCol).sort((a, b) => (a === "_row") - (b === "_row") || a.localeCompare(b));
  if (!cols.length) {
    return h("section", { class: "panel state" }, h("h2", {}, "No issues found"), h("p", {}, "Every row passed every rule without needing a fix."));
  }
  const maxP = Math.max(1, ...cols.flatMap((c) => PROBLEMS.map(([t]) => byCol[c][t] || 0)));
  const maxF = Math.max(1, ...cols.flatMap((c) => FIXES.map(([t]) => byCol[c][t] || 0)));
  const cell = (v, max, rgb) => h("td", { class: "cell" }, v
    ? h("span", { style: `background:rgba(${rgb},${(0.08 + 0.32 * (v / max)).toFixed(2)})` }, num(v))
    : h("span", { class: "muted" }, "–"));
  const usedProblems = PROBLEMS.filter(([t]) => cols.some((c) => byCol[c][t]));
  const usedFixes = FIXES.filter(([t]) => cols.some((c) => byCol[c][t]));
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Issues by column"),
      h("span", { class: "hint" }, "Counts of rows affected; one row can have several issues")),
    h("div", { class: "table-wrap" }, h("table", { class: "matrix" },
      h("thead", {},
        h("tr", {}, h("th", { rowspan: 2, scope: "col" }, "Column"),
          usedProblems.length ? h("th", { colspan: usedProblems.length, class: "num", scope: "colgroup" }, "Found problems") : null,
          usedFixes.length ? h("th", { colspan: usedFixes.length, class: "num", scope: "colgroup" }, "Fixed automatically") : null),
        h("tr", {}, usedProblems.map(([, l]) => h("th", { class: "num", scope: "col" }, l)), usedFixes.map(([, l]) => h("th", { class: "num", scope: "col" }, l)))),
      h("tbody", {}, cols.map((c) => h("tr", {},
        h("th", { scope: "row" }, c === "_row" ? "Whole line" : c),
        usedProblems.map(([t]) => cell(byCol[c][t], maxP, "208,59,59")),
        usedFixes.map(([t]) => cell(byCol[c][t], maxF, "42,120,214"))))))));
}

function rejectedPanel(id) {
  let kind = "all", page = 1;
  const tabs = [["all", "All refused rows"], ["invalid", "Failed validation"], ["duplicate", "Duplicates"]];
  const tabBar = h("div", { class: "tabs", role: "tablist", "aria-label": "Rejected row type" });
  const list = h("div", { role: "tabpanel" }, loading(4));

  function drawTabs() {
    tabBar.replaceChildren(...tabs.map(([k, label]) => h("button", { role: "tab", "aria-selected": String(k === kind),
      onclick: () => { kind = k; page = 1; drawTabs(); load(); } }, label)));
  }

  async function load() {
    list.replaceChildren(loading(4));
    try {
      const p = await get(`/api/uploads/${id}/rejected`, { kind, page, page_size: 20 });
      if (!p.total) {
        list.replaceChildren(h("div", { class: "state" }, h("h2", {}, "Nothing here"), h("p", {}, "No rows were refused in this category.")));
        return;
      }
      const kindChip = (k) => k === "duplicate"
        ? h("span", { class: "chip chip-warn" }, icon("copy"), "Duplicate")
        : h("span", { class: "chip chip-crit" }, icon("cross"), k === "malformed" ? "Malformed" : "Invalid");
      list.replaceChildren(
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Line"), h("th", {}, "Type"), h("th", {}, "Why it was refused"))),
          h("tbody", {}, p.items.map((r) => h("tr", {},
            h("td", { class: "num" }, num(r.row_number)),
            h("td", {}, kindChip(r.kind)),
            h("td", {},
              h("ul", { class: "reasons" }, r.reasons.map((x) => h("li", {}, x))),
              h("div", { class: "rawline" }, Object.entries(r.raw_data).map(([k, v]) => `${k}=${v === "" ? "∅" : v}`).join("  ")))))))),
        h("div", { class: "pager" },
          h("span", { class: "muted small" }, `${num(p.total)} row${p.total === 1 ? "" : "s"}; line numbers match the original file`),
          h("div", { class: "btn-group" },
            h("button", { class: "btn", disabled: page <= 1 ? true : null, onclick: () => { page--; load(); } }, "Previous"),
            h("span", { class: "small muted", style: "align-self:center" }, `Page ${page} of ${p.pages}`),
            h("button", { class: "btn", disabled: page >= p.pages ? true : null, onclick: () => { page++; load(); } }, "Next"))));
    } catch (err) {
      list.replaceChildren(errorState(err, load));
    }
  }

  drawTabs();
  load();
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Refused rows"), h("span", { class: "hint" }, "Kept with every reason, so they can be corrected at the source")),
    tabBar, list);
}
