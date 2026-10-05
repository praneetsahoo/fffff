// Motion layer: scroll reveals, chart entrances, count-ups, page transitions, sliding tab
// indicators. Purely presentational — content and behaviour are unchanged without it, and it
// switches itself off for people who ask their OS for reduced motion.

export const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Elements that ease in when they scroll into view.
const REVEAL = ".page-head, .panel, .filters, .upload-hero, .chart, .ledger";
// Children that cascade in, in order, once their parent is in view.
const STAGGER = ".insights .insight, .upload-hero h1, .upload-hero .lede, .upload-hero .flow li, .upload-hero .dropzone, .upload-hero .samples";
// Numbers that count up when they appear.
const COUNT = ".kpi .value, .ledger-total .big";

const observer = new IntersectionObserver((entries) => {
  // Reveal everything that entered in this frame top-to-bottom, so the eye is led down the page.
  const entering = entries.filter((e) => e.isIntersecting)
    .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
  entering.forEach((e, i) => {
    const el = e.target;
    el.style.setProperty("--d", `${Math.min(i * 70, 420)}ms`);
    el.querySelectorAll(STAGGER).forEach((child, k) => child.style.setProperty("--k", k));
    el.classList.add("in-view");
    observer.unobserve(el);
    countUp(el);
  });
}, { threshold: 0, rootMargin: "0px 0px -8% 0px" });

function register(el) {
  if (el.classList.contains("in-view") || el.dataset.motion) return;
  el.dataset.motion = "1";
  el.classList.add("reveal");
  if (reduceMotion) { el.classList.add("in-view"); return; }
  observer.observe(el);
}

/** Watch a container: anything matching REVEAL that gets added is revealed on scroll. */
export function watch(root) {
  const scan = (node) => {
    if (!(node instanceof Element)) return;
    if (node.matches(REVEAL)) register(node);
    node.querySelectorAll(REVEAL).forEach(register);
  };
  scan(root);
  new MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach(scan)))
    .observe(root, { childList: true, subtree: true });
}

// ---------- count-up numbers ----------
const NUM_RE = /^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$/;

function countUp(scope) {
  if (reduceMotion) return;
  const targets = [...(scope.matches(COUNT) ? [scope] : []), ...scope.querySelectorAll(COUNT)];
  for (const el of targets) {
    if (el.dataset.counted) continue; // a parent and a child may both enter; count once
    el.dataset.counted = "1";
    const text = el.textContent;
    const m = text.match(NUM_RE);
    if (!m) continue;
    const [, prefix, digits, suffix] = m;
    const target = parseFloat(digits.replace(/,/g, ""));
    if (!isFinite(target) || target === 0) continue;
    const decimals = (digits.split(".")[1] || "").length;
    const fmt = new Intl.NumberFormat("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    const start = performance.now(), dur = 900;
    const delay = parseFloat(getComputedStyle(scope).getPropertyValue("--d")) || 0;
    const tick = (now) => {
      const t = Math.min(1, Math.max(0, (now - start - delay) / dur));
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = t >= 1 ? text : `${prefix}${fmt.format(target * eased)}${suffix}`;
      if (t < 1) requestAnimationFrame(tick);
    };
    el.textContent = `${prefix}${fmt.format(0)}${suffix}`;
    requestAnimationFrame(tick);
  }
}

// ---------- page transitions ----------
export async function leave(main) {
  if (reduceMotion || !main.childElementCount) return;
  main.classList.remove("page-enter");
  main.classList.add("page-leave");
  await new Promise((r) => setTimeout(r, 140));
}

export function enter(main) {
  main.classList.remove("page-leave");
  if (reduceMotion) return;
  main.classList.remove("page-enter");
  void main.offsetWidth; // restart the animation
  main.classList.add("page-enter");
}

/** Cross-fade an element whose contents were just replaced (tables, tab panels). */
export function swapIn(el) {
  if (reduceMotion) return;
  el.classList.remove("swap-in");
  void el.offsetWidth;
  el.classList.add("swap-in");
}

// ---------- sliding underline for navs / tab bars ----------
export function slideIndicator(container, activeEl) {
  let bar = container.querySelector(":scope > .indicator");
  if (!bar) {
    bar = document.createElement("span");
    bar.className = "indicator";
    bar.setAttribute("aria-hidden", "true");
    container.append(bar);
    bar.classList.add("no-anim"); // first placement: no slide from nowhere
  }
  if (!activeEl) { bar.style.opacity = "0"; return; }
  bar.style.opacity = "1";
  bar.style.width = `${activeEl.offsetWidth}px`;
  bar.style.transform = `translateX(${activeEl.offsetLeft}px)`;
  if (bar.classList.contains("no-anim")) requestAnimationFrame(() => bar.classList.remove("no-anim"));
}
