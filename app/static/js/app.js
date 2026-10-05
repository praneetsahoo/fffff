// Hash router + live system health in the top bar.
import { get } from "./api.js";
import { h } from "./ui.js";
import { renderUpload } from "./pages/upload.js";
import { renderDashboard } from "./pages/dashboard.js";
import { renderRecords } from "./pages/records.js";
import { renderQuality } from "./pages/quality.js";

const routes = {
  upload: renderUpload,
  dashboard: renderDashboard,
  records: renderRecords,
  quality: renderQuality,
};
const titles = { upload: "Upload", dashboard: "Dashboard", records: "Records", quality: "Data quality" };

let cleanup = null;

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const [name, ...rest] = path.split("/");
  return { name: routes[name] ? name : "upload", args: rest.filter(Boolean), params: new URLSearchParams(query) };
}

async function route() {
  const { name, args, params } = parseHash();
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.route === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${titles[name]} · OpsIntel`;
  const main = document.getElementById("main");
  main.replaceChildren();
  cleanup = await routes[name](main, { args, params });
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

async function refreshHealth() {
  const box = document.getElementById("health");
  const item = (ok, label, title) => h("span", { class: ok ? "ok" : "bad", title },
    h("span", { class: "dot", "aria-hidden": "true" }), h("span", { class: "lbl" }, label));
  try {
    const res = await fetch("/api/health", { headers: { Accept: "application/json" } });
    const d = await res.json();
    const storageName = d.storage_backend === "s3" ? "Amazon S3" : "Local storage";
    box.replaceChildren(
      item(d.checks.database === "ok", d.checks.database === "ok" ? "Database connected" : "Database unavailable", "Relational database"),
      item(d.checks.storage === "ok", d.checks.storage === "ok" ? storageName : `${storageName} unavailable`, "Object storage"));
  } catch {
    box.replaceChildren(item(false, "Server unreachable", "API"));
  }
}

window.addEventListener("hashchange", route);
route();
refreshHealth();
setInterval(refreshHealth, 30000);
