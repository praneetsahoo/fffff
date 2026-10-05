// Upload page: drop a CSV, watch it move through the pipeline, see the row accounting.
import { get, post, uploadFile } from "../api.js";
import { ledger } from "../charts.js";
import { h, icon, num, pct, bytes, ago, statusChip, alertBox, emptyState, errorState, loading, toast } from "../ui.js";

const STEPS = ["Upload and store original", "Validate every row", "Save cleaned and rejected files", "Load database", "Ready to explore"];
const STAGE_INDEX = { queued: 1, reading: 1, validating: 1, "storing outputs": 2, "loading database": 3, completed: 5 };

export async function renderUpload(root) {
  const timers = new Set();
  const stopAll = () => timers.forEach((t) => clearTimeout(t));
  let meta = { required_columns: [], columns: [], max_upload_mb: 10 };
  let samples = [];
  try {
    [meta, samples] = await Promise.all([get("/api/meta"), get("/api/samples")]);
  } catch { /* the page still works; requirements text is just shorter */ }

  // ---------- hero ----------
  const fileInput = h("input", { type: "file", accept: ".csv,text/csv", "aria-label": "Choose a CSV file to upload" });
  const drop = h("label", { class: "dropzone" },
    icon("upload"),
    h("strong", {}, "Drop a CSV file here, or choose one"),
    h("span", { class: "req" }, `CSV up to ${meta.max_upload_mb} MB. Required columns: ${meta.required_columns.join(", ") || "see the data rules"}.`),
    fileInput);
  const datasets = samples.filter((x) => x.kind === "dataset");
  const edge = samples.filter((x) => x.kind === "edge_case");
  const sampleLinks = h("div", { class: "samples" },
    datasets.length ? h("p", {}, "Need a file? ",
      datasets.map((x) => h("a", { href: `/api/samples/${encodeURIComponent(x.name)}`, download: x.name }, `${x.name} (${bytes(x.size_bytes)})`))) : null,
    edge.length ? h("p", {}, "Files that should be refused: ",
      edge.map((x) => h("a", { href: `/api/samples/${encodeURIComponent(x.name)}`, download: x.name }, x.name))) : null);

  const hero = h("section", { class: "upload-hero", "aria-labelledby": "hero-title" },
    h("div", {},
      h("h1", { id: "hero-title" }, "Turn raw operations exports into data you can trust"),
      h("p", { class: "lede" }, "Upload a CSV of orders. OpsIntel keeps the original, checks every row, fixes what it safely can, and loads clean records for analysis. Anything it refuses is listed with the reason."),
      h("ol", { class: "flow" },
        [["Stored", "the original file is kept unchanged in cloud storage"],
         ["Checked", "every row is validated against the data rules"],
         ["Cleaned", "formats are standardised and duplicates removed"],
         ["Loaded", "clean records are saved to the database"],
         ["Explained", "dashboard, insights and a quality report update"]]
          .map(([b, t], i) => h("li", {}, h("span", { class: "n", "aria-hidden": "true" }, i + 1), h("span", {}, h("b", {}, b), `: ${t}`))))),
    h("div", {}, drop, sampleLinks));

  const trackerSlot = h("div", { "aria-live": "polite" });
  const historySlot = h("section", { "aria-labelledby": "hist-title" });
  root.append(hero, trackerSlot, historySlot);

  // drag & drop
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer?.files?.[0]; if (f) start(f); });
  fileInput.addEventListener("change", () => { const f = fileInput.files?.[0]; if (f) start(f); fileInput.value = ""; });

  // ---------- upload + tracking ----------
  let busy = false;
  async function start(file) {
    if (busy) return toast("An upload is already in progress.");
    stopAll();
    // Client-side pre-checks give instant feedback; the server re-checks everything.
    if (!/\.csv$/i.test(file.name)) return showError("This isn't a CSV file", `"${file.name}" is not a .csv file. Export your data as CSV and try again.`);
    if (file.size === 0) return showError("The file is empty", "Choose a CSV that contains a header row and data.");
    if (file.size > meta.max_upload_mb * 1024 * 1024) return showError("The file is too large", `The limit is ${meta.max_upload_mb} MB; this file is ${bytes(file.size)}.`);

    busy = true;
    const view = tracker(file.name, file.size);
    trackerSlot.replaceChildren(view.el);
    view.el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const up = await uploadFile(file, (p) => view.setUploading(p));
      view.setStage(up);
      poll(up.id, view);
    } catch (err) {
      busy = false;
      view.fail(0);
      const extra = err.code === "duplicate_upload" && err.details?.upload_id
        ? h("a", { href: `#/quality/${err.details.upload_id}` }, "Open the existing quality report") : null;
      view.showResult(h("div", { class: "stack" }, alertBox(err.message, hintFor(err)), extra));
    }
  }

  function poll(id, view) {
    const t = setTimeout(async () => {
      timers.delete(t);
      try {
        const up = await get(`/api/uploads/${id}`);
        view.setStage(up);
        if (up.status === "COMPLETED") {
          busy = false;
          view.showResult(completed(up));
          toast(`${up.original_filename}: ${num(up.valid_rows)} records loaded`);
          loadHistory();
        } else if (up.status === "FAILED") {
          busy = false;
          view.showResult(h("div", { class: "stack" }, alertBox("Processing failed", up.error_message),
            h("button", { class: "btn", onclick: () => retry(id, view) }, "Retry processing")));
          loadHistory();
        } else {
          poll(id, view);
        }
      } catch (err) {
        view.setNote(`${err.message} Still checking…`);
        poll(id, view);
      }
    }, 600);
    timers.add(t);
  }

  async function retry(id, view) {
    try {
      const up = await post(`/api/uploads/${id}/retry`);
      busy = true;
      view.reset();
      view.setStage(up);
      poll(id, view);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function showError(title, detail) {
    trackerSlot.replaceChildren(h("div", { class: "panel tracker" }, alertBox(title, detail)));
  }

  // ---------- history ----------
  async function loadHistory() {
    if (!historySlot.childElementCount) historySlot.replaceChildren(loading(4));
    try {
      const list = await get("/api/uploads", { limit: 20 });
      historySlot.replaceChildren(history(list));
      if (list.some((u) => u.status === "QUEUED" || u.status === "PROCESSING")) {
        const t = setTimeout(() => { timers.delete(t); loadHistory(); }, 2000);
        timers.add(t);
      }
    } catch (err) {
      historySlot.replaceChildren(errorState(err, loadHistory));
    }
  }

  function history(list) {
    if (!list.length) {
      return emptyState("No uploads yet", "Your upload history will appear here, with the outcome of every file.");
    }
    return h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", { id: "hist-title" }, "Recent uploads"), h("span", { class: "hint" }, `${list.length} shown`)),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {},
          h("th", {}, "File"), h("th", {}, "Uploaded"), h("th", {}, "Status"),
          h("th", { class: "num" }, "Rows"), h("th", { class: "num" }, "Loaded"), h("th", { class: "num" }, "Success"), h("th", {}, h("span", { class: "sr-only" }, "Actions")))),
        h("tbody", {}, list.map((u) => h("tr", {},
          h("td", {}, h("div", {}, u.original_filename), h("div", { class: "muted small" }, bytes(u.file_size_bytes))),
          h("td", { title: new Date(u.created_at).toLocaleString("en-IN") }, ago(u.created_at)),
          h("td", {}, statusChip(u.status), u.status === "FAILED" ? h("div", { class: "small muted" }, u.error_message) : null),
          h("td", { class: "num" }, num(u.total_rows)),
          h("td", { class: "num" }, num(u.valid_rows)),
          h("td", { class: "num" }, pct(u.success_rate)),
          h("td", {}, u.status === "COMPLETED"
            ? h("a", { href: `#/quality/${u.id}` }, "Quality report")
            : u.status === "FAILED"
              ? h("button", { class: "btn-link", onclick: async () => {
                  try { await post(`/api/uploads/${u.id}/retry`); toast("Retry started"); loadHistory(); }
                  catch (err) { toast(err.message, "error"); } } }, "Retry")
              : h("span", { class: "muted small" }, "Working…"))))))));
  }

  loadHistory();
  return stopAll;
}

function hintFor(err) {
  switch (err.code) {
    case "missing_columns": return `Expected columns: ${(err.details?.expected_columns || []).join(", ")}.`;
    case "unsupported_format": return "Save or export the data as a comma-separated .csv file.";
    case "no_data": return "Add at least one data row under the header.";
    case "duplicate_upload": return "Each file is processed once, so records are never counted twice.";
    case "network": return "Nothing was uploaded. You can try again once the connection is back.";
    default: return null;
  }
}

function completed(up) {
  return h("div", { class: "stack" },
    ledger({ total: up.total_rows, valid: up.valid_rows, invalid: up.invalid_rows, duplicate: up.duplicate_rows,
      rate: up.success_rate, caption: `rows received in ${up.original_filename}` }),
    h("div", { class: "btn-group" },
      h("a", { class: "btn btn-primary", href: "#/dashboard" }, "Open dashboard"),
      h("a", { class: "btn", href: `#/quality/${up.id}` }, "See what was fixed and rejected"),
      h("a", { class: "btn", href: `#/records?upload_id=${up.id}` }, "Browse these records")));
}

function tracker(name, size) {
  const items = STEPS.map((label) => h("li", {}, label));
  const status = h("span", { class: "muted small" }, "Uploading…");
  const result = h("div", { class: "result" });
  const el = h("section", { class: "panel tracker", "aria-label": "Upload progress" },
    h("div", { class: "tracker-head" }, h("h2", {}, name), h("span", { class: "muted small" }, bytes(size)), status),
    h("ol", { class: "steps" }, items), result);
  const mark = (activeIdx) => items.forEach((li, i) => {
    li.className = i < activeIdx ? "done" : i === activeIdx ? "active" : "";
    li.removeAttribute("aria-current");
    if (i === activeIdx) li.setAttribute("aria-current", "step");
  });
  mark(0);
  let current = 0;
  return {
    el,
    setUploading(p) { status.textContent = `Uploading ${Math.round(p * 100)}%`; },
    setStage(up) {
      if (up.status === "FAILED") { this.fail(current); status.textContent = "Failed"; return; }
      current = STAGE_INDEX[up.stage] ?? 1;
      mark(current);
      status.textContent = up.status === "COMPLETED" ? "Done" : `${up.stage ? up.stage[0].toUpperCase() + up.stage.slice(1) : "Queued"}…`;
    },
    setNote(text) { status.textContent = text; },
    fail(idx) { items.forEach((li, i) => { li.className = i < idx ? "done" : i === idx ? "failed" : ""; }); },
    showResult(node) { result.replaceChildren(node); },
    reset() { result.replaceChildren(); mark(1); },
  };
}
