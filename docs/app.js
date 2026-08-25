/* Portfolio News dashboard.

   Everything is computed in the browser from docs/data/articles.json. There
   is no backend, no upload path and no third-party script.

   One rule governs the whole renderer: headlines and publisher names come
   from the open web and are therefore untrusted. They reach the page only
   via document.createElement and .textContent, never innerHTML, and every
   href is scheme-checked before it is set. A crafted headline is displayed
   as characters, never parsed as markup.
*/
"use strict";

const DATA_URL = "data/articles.json";
const PAGE_SIZE = 120;
const SPARK_DAYS = 28;

const STATE = {
  articles: [],
  entities: [],
  selected: new Set(),   // empty means "every company"
  from: "",
  to: "",
  confidence: "all",
  kind: "all",
  source: "all",
  query: "",
  sort: "new",
  shown: PAGE_SIZE,
};

/* ---------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);

/** Only http(s) links are ever rendered. Anything else becomes inert text. */
function safeHref(url) {
  if (typeof url !== "string" || url.length > 2048) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") ? parsed.href : null;
  } catch (err) {
    return null;
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

const dayOf = (iso) => (typeof iso === "string" ? iso.slice(0, 10) : "");

function fmtDay(ymd) {
  const parts = ymd.split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return ymd;
  const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  return date.toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}

function fmtTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function shiftDays(ymd, delta) {
  const date = new Date(ymd + "T00:00:00Z");
  date.setUTCDate(date.getUTCDate() + delta);
  return date.toISOString().slice(0, 10);
}

function notice(message) {
  const bar = $("notice");
  bar.textContent = message;
  bar.hidden = false;
}

/* ---------------------------------------------------------------- filtering */

function passesFilters(article) {
  if (STATE.selected.size && !STATE.selected.has(article.ticker)) return false;

  const day = dayOf(article.published);
  if (STATE.from && day < STATE.from) return false;
  if (STATE.to && day > STATE.to) return false;

  if (STATE.confidence !== "all" && article.confidence !== STATE.confidence) return false;
  if (STATE.kind !== "all" && (article.kind || "company") !== STATE.kind) return false;
  if (STATE.source !== "all" && article.source !== STATE.source) return false;

  if (STATE.query) {
    const hay = (article.headline + " " + (article.publisher || "") + " " + article.company).toLowerCase();
    if (!hay.includes(STATE.query)) return false;
  }
  return true;
}

/** Same filters minus the company selection — used for the rail counts, so
    narrowing the date range updates every company's number at once. */
function passesExceptCompany(article) {
  const day = dayOf(article.published);
  if (STATE.from && day < STATE.from) return false;
  if (STATE.to && day > STATE.to) return false;
  if (STATE.confidence !== "all" && article.confidence !== STATE.confidence) return false;
  if (STATE.kind !== "all" && (article.kind || "company") !== STATE.kind) return false;
  if (STATE.source !== "all" && article.source !== STATE.source) return false;
  if (STATE.query) {
    const hay = (article.headline + " " + (article.publisher || "") + " " + article.company).toLowerCase();
    if (!hay.includes(STATE.query)) return false;
  }
  return true;
}

function filtered() {
  const rows = STATE.articles.filter(passesFilters);
  if (STATE.sort === "old") {
    rows.sort((a, b) => a.published.localeCompare(b.published));
  } else if (STATE.sort === "company") {
    rows.sort((a, b) =>
      a.company.localeCompare(b.company) || b.published.localeCompare(a.published));
  } else {
    rows.sort((a, b) => b.published.localeCompare(a.published));
  }
  return rows;
}

/* ---------------------------------------------------------------- rail */

function buildSpark(days) {
  const strip = el("div", "spark");
  strip.setAttribute("aria-hidden", "true");
  const end = todayISO();
  for (let i = SPARK_DAYS - 1; i >= 0; i--) {
    const count = days.get(shiftDays(end, -i)) || 0;
    const mark = el("i");
    if (count >= 5) mark.className = "a3";
    else if (count >= 2) mark.className = "a2";
    else if (count === 1) mark.className = "a1";
    strip.appendChild(mark);
  }
  return strip;
}

function renderRail() {
  const list = $("coList");
  list.textContent = "";

  const pool = STATE.articles.filter(passesExceptCompany);
  const counts = new Map();
  const dayMap = new Map();
  for (const article of pool) {
    counts.set(article.ticker, (counts.get(article.ticker) || 0) + 1);
    if (!dayMap.has(article.ticker)) dayMap.set(article.ticker, new Map());
    const days = dayMap.get(article.ticker);
    const day = dayOf(article.published);
    days.set(day, (days.get(day) || 0) + 1);
  }

  const needle = $("coSearch").value.trim().toLowerCase();
  const visible = STATE.entities.filter((entity) => {
    if (STATE.kind !== "all" && (entity.kind || "company") !== STATE.kind) return false;
    if (!needle) return true;
    return (entity.name + " " + entity.ticker + " " + (entity.country || "") + " " +
            (entity.industry || "")).toLowerCase().includes(needle);
  });

  visible.sort((a, b) => (counts.get(b.ticker) || 0) - (counts.get(a.ticker) || 0)
    || a.name.localeCompare(b.name));

  for (const entity of visible) {
    const count = counts.get(entity.ticker) || 0;
    const row = el("li", "corow" + (count ? "" : " zero"));

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = STATE.selected.has(entity.ticker);
    box.id = "co_" + entity.ticker.replace(/[^A-Za-z0-9]/g, "_");
    box.addEventListener("change", () => {
      if (box.checked) STATE.selected.add(entity.ticker);
      else STATE.selected.delete(entity.ticker);
      STATE.shown = PAGE_SIZE;
      renderRail();
      renderResults();
    });

    const body = el("div");
    const label = document.createElement("label");
    label.className = "coname";
    label.setAttribute("for", box.id);
    label.appendChild(document.createTextNode(entity.name));
    if (entity.kind === "macro") {
      label.appendChild(el("span", "cokind", "economy"));
    } else if (entity.status && entity.status !== "active") {
      label.appendChild(el("span", "costatus", entity.status));
    }
    body.appendChild(label);
    body.appendChild(el("div", "cometa", entity.ticker + " · " + (entity.country || "")));
    body.appendChild(buildSpark(dayMap.get(entity.ticker) || new Map()));
    if (entity.note) row.title = entity.note;

    row.appendChild(box);
    row.appendChild(body);
    row.appendChild(el("span", "con", count));
    list.appendChild(row);
  }

  const chosen = STATE.selected.size;
  $("coCount").textContent = chosen
    ? chosen + " of " + visible.length + " selected"
    : "All " + visible.length + " rows";
}

/* ---------------------------------------------------------------- results */

function renderItem(article) {
  const item = el("article", "item");

  const heading = el("h3");
  const href = safeHref(article.url);
  if (href) {
    const link = el("a", null, article.headline);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer nofollow";
    heading.appendChild(link);
  } else {
    heading.appendChild(document.createTextNode(article.headline));
  }
  item.appendChild(heading);

  const meta = el("div", "itemmeta");
  meta.appendChild(el("span", "co", article.company));
  if (article.publisher) meta.appendChild(el("span", "pub", article.publisher));
  meta.appendChild(el("span", "time", fmtTime(article.published)));
  meta.appendChild(el("span", "tag src", article.source === "gnews" ? "Google News" : "GDELT"));
  if (article.confidence === "low") {
    const flag = el("span", "tag low", "Needs a look");
    flag.title = "This company's name could refer to something else, and the headline "
               + "did not contain anything that confirms it.";
    meta.appendChild(flag);
  }
  item.appendChild(meta);
  return item;
}

function renderResults() {
  const target = $("results");
  target.textContent = "";

  const rows = filtered();
  const total = rows.length;
  const page = rows.slice(0, STATE.shown);

  const companies = new Set(page.map((row) => row.ticker)).size;
  const line = $("resultLine");
  line.textContent = "";
  if (total === 0) {
    line.textContent = "No headlines match these filters.";
  } else {
    line.appendChild(el("b", null, total.toLocaleString()));
    line.appendChild(document.createTextNode(
      (total === 1 ? " headline" : " headlines") + " across "));
    line.appendChild(el("b", null, companies));
    line.appendChild(document.createTextNode(
      companies === 1 ? " company" : " companies"));
    if (STATE.shown < total) {
      line.appendChild(document.createTextNode(" · showing the first " + page.length));
    }
  }

  if (total === 0) {
    const empty = el("div", "empty");
    empty.appendChild(el("strong", null, "Nothing here yet"));
    empty.appendChild(el("p", null,
      "Widen the date range, clear the company selection, or switch certainty back to All. "
      + "A quiet week for a small holding is normal."));
    target.appendChild(empty);
    $("moreBtn").hidden = true;
    return;
  }

  if (STATE.sort === "company") {
    let current = null;
    let group = null;
    for (const article of page) {
      if (article.company !== current) {
        current = article.company;
        group = el("section", "daygroup");
        group.appendChild(el("h2", "dayhead", current));
        target.appendChild(group);
      }
      group.appendChild(renderItem(article));
    }
  } else {
    let current = null;
    let group = null;
    for (const article of page) {
      const day = dayOf(article.published);
      if (day !== current) {
        current = day;
        group = el("section", "daygroup");
        group.appendChild(el("h2", "dayhead", fmtDay(day)));
        target.appendChild(group);
      }
      group.appendChild(renderItem(article));
    }
  }

  $("moreBtn").hidden = STATE.shown >= total;
}

function rerender() {
  STATE.shown = PAGE_SIZE;
  renderRail();
  renderResults();
}

/* ---------------------------------------------------------------- wiring */

function setSeg(container, attr, value) {
  for (const button of container.querySelectorAll("button")) {
    button.classList.toggle("on", button.dataset[attr] === value);
  }
}

function applyQuickRange(days) {
  if (!days) {
    STATE.from = "";
    STATE.to = "";
  } else {
    STATE.to = todayISO();
    STATE.from = shiftDays(STATE.to, -(days - 1));
  }
  $("dFrom").value = STATE.from;
  $("dTo").value = STATE.to;
}

function wire() {
  $("dFrom").addEventListener("change", (event) => {
    STATE.from = event.target.value;
    setSeg($("segRange"), "days", "");
    rerender();
  });
  $("dTo").addEventListener("change", (event) => {
    STATE.to = event.target.value;
    setSeg($("segRange"), "days", "");
    rerender();
  });

  $("segRange").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    setSeg($("segRange"), "days", button.dataset.days);
    applyQuickRange(Number(button.dataset.days));
    rerender();
  });

  $("segConf").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    STATE.confidence = button.dataset.conf;
    setSeg($("segConf"), "conf", STATE.confidence);
    rerender();
  });

  $("segKind").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    STATE.kind = button.dataset.kind;
    setSeg($("segKind"), "kind", STATE.kind);
    // A selection made under one view would silently hide everything in the
    // other, so switching views clears it.
    STATE.selected.clear();
    rerender();
  });

  $("srcSel").addEventListener("change", (event) => {
    STATE.source = event.target.value;
    rerender();
  });

  $("sortSel").addEventListener("change", (event) => {
    STATE.sort = event.target.value;
    renderResults();
  });

  let debounce = null;
  $("qText").addEventListener("input", (event) => {
    const value = event.target.value.trim().toLowerCase();
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => {
      STATE.query = value;
      rerender();
    }, 180);
  });

  $("coSearch").addEventListener("input", renderRail);

  $("coAll").addEventListener("click", () => {
    STATE.selected = new Set(STATE.entities.map((entity) => entity.ticker));
    rerender();
  });
  $("coNone").addEventListener("click", () => {
    STATE.selected.clear();
    rerender();
  });

  $("moreBtn").addEventListener("click", () => {
    STATE.shown += PAGE_SIZE;
    renderResults();
  });

  $("resetAll").addEventListener("click", () => {
    STATE.selected.clear();
    STATE.confidence = "all";
    STATE.kind = "all";
    STATE.source = "all";
    STATE.query = "";
    STATE.sort = "new";
    $("qText").value = "";
    $("coSearch").value = "";
    $("srcSel").value = "all";
    $("sortSel").value = "new";
    setSeg($("segConf"), "conf", "all");
    setSeg($("segKind"), "kind", "all");
    setSeg($("segRange"), "days", "0");
    applyQuickRange(0);
    rerender();
  });
}

/* ---------------------------------------------------------------- boot */

function boot(payload) {
  const articles = Array.isArray(payload.articles) ? payload.articles : [];
  STATE.articles = articles.filter((article) =>
    article && typeof article.headline === "string" &&
    typeof article.published === "string" && typeof article.ticker === "string");

  STATE.entities = Array.isArray(payload.entities) && payload.entities.length
    ? payload.entities.slice()
    : [...new Map(STATE.articles.map((article) =>
        [article.ticker, { ticker: article.ticker, name: article.company, country: "",
                           status: "active", note: "", kind: article.kind || "company" }]
      )).values()];

  $("mCount").textContent = STATE.articles.length.toLocaleString();
  $("mCos").textContent = STATE.entities.length;
  const macroCount = STATE.entities.filter((e) => e.kind === "macro").length;
  $("mCos").title = (STATE.entities.length - macroCount) + " holdings + "
                  + macroCount + " Sri Lanka economy themes";

  if (payload.generated) {
    const when = new Date(payload.generated);
    $("mUpdated").textContent = Number.isNaN(when.getTime())
      ? "—"
      : when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    $("mUpdated").title = payload.generated;
  }

  if (!STATE.articles.length) {
    notice("The archive is empty. Run the collector, then commit docs/data/articles.json.");
  }

  wire();
  applyQuickRange(0);
  rerender();
}

fetch(DATA_URL, { cache: "no-cache" })
  .then((response) => {
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  })
  .then(boot)
  .catch((err) => {
    notice("Could not load data/articles.json (" + err.message
         + "). If you are opening this file directly, serve the folder instead: "
         + "cd docs && python -m http.server 8000");
    wire();
  });
