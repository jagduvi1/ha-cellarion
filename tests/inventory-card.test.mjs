/**
 * Unit tests for the inventory card
 * (custom_components/cellarion/www/cellarion-inventory-card.js).
 *
 * The card renders a shell once and then swaps regions inside it, uses
 * delegated events and restores focus, so it needs more DOM than
 * card.test.mjs stubs: a small HTML parser into a node tree with
 * querySelector (tag, class, attribute, descendant), closest, bubbling
 * events and focus tracking. No browser, no dependencies.
 *
 * Run with: node --test tests/
 */

import test, { afterEach, mock } from "node:test";
import assert from "node:assert/strict";

// ── A small DOM ─────────────────────────────────────────────────────

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const RAW = new Set(["style", "script"]);
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" };
const decode = (s) => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ENTITIES[e]);
const encode = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

class TextNode {
  constructor(text) { this.data = text; this.parentNode = null; }
  get textContent() { return this.data; }
  get outerHTML() { return RAW.has(this.parentNode?.localName) ? this.data : encode(this.data); }
}

class El {
  constructor(tag, attrs = {}) {
    this.localName = tag;
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attrs };
    this.childNodes = [];
    this.parentNode = null;
    this._listeners = {};
  }

  getAttribute(k) { return Object.hasOwn(this.attributes, k) ? this.attributes[k] : null; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) { return Object.hasOwn(this.attributes, k); }
  get dataset() {
    return Object.fromEntries(Object.entries(this.attributes)
      .filter(([k]) => k.startsWith("data-"))
      .map(([k, v]) => [k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()), v]));
  }
  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(v) { if (v) this.setAttribute("hidden", ""); else this.removeAttribute("hidden"); }
  get disabled() { return this.hasAttribute("disabled"); }
  get className() { return this.getAttribute("class") || ""; }
  get classList() {
    const names = () => this.className.split(/\s+/).filter(Boolean);
    return {
      contains: (n) => names().includes(n),
      add: (...ns) => this.setAttribute("class", [...new Set([...names(), ...ns])].join(" ")),
      remove: (...ns) => this.setAttribute("class", names().filter((n) => !ns.includes(n)).join(" ")),
    };
  }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.localName === "select") {
      const opts = this.querySelectorAll("option");
      return (opts.find((o) => o.hasAttribute("selected")) || opts[0])?.value ?? "";
    }
    return this.getAttribute("value") ?? (this.localName === "option" ? this.textContent : "");
  }
  set value(v) { this._value = String(v); }

  get children() { return this.childNodes.filter((n) => n instanceof El); }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(""); }
  set textContent(t) { this._setChildren([new TextNode(String(t))]); }
  get innerHTML() { return this.childNodes.map((n) => n.outerHTML).join(""); }
  set innerHTML(html) { this._setChildren(parseHTML(String(html))); }
  get outerHTML() {
    const attrs = Object.entries(this.attributes)
      .map(([k, v]) => (v === "" ? ` ${k}` : ` ${k}="${encode(v)}"`)).join("");
    return VOID.has(this.localName) ? `<${this.localName}${attrs}>`
      : `<${this.localName}${attrs}>${this.innerHTML}</${this.localName}>`;
  }
  _setChildren(nodes) {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = nodes;
    for (const n of nodes) n.parentNode = this;
  }
  appendChild(n) { this.childNodes.push(n); n.parentNode = this; return n; }
  getRootNode() { let n = this; while (n.parentNode) n = n.parentNode; return n; }

  matches(sel) { return parseSelector(sel).some((parts) => matchComplex(this, parts)); }
  closest(sel) {
    for (let n = this; n instanceof El; n = n.parentNode) if (n.matches(sel)) return n;
    return null;
  }
  querySelectorAll(sel) {
    const groups = parseSelector(sel);
    const out = [];
    const walk = (node) => {
      for (const c of node.childNodes) {
        if (!(c instanceof El)) continue;
        if (groups.some((p) => matchComplex(c, p))) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }

  addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn); }
  focus() {
    const root = this.getRootNode();
    if (root instanceof FakeShadowRoot) root.activeElement = this;
  }
  /** Dispatch a bubbling event from this node up to the shadow root. */
  dispatch(type, init = {}) {
    const ev = {
      type, target: this, defaultPrevented: false, stopped: false, ...init,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.stopped = true; },
    };
    for (let n = this; n && !ev.stopped; n = n.parentNode) {
      for (const fn of [...(n._listeners?.[type] || [])]) fn.call(n, ev);
    }
    return ev;
  }
  click() { return this.dispatch("click"); }
}

class FakeShadowRoot extends El {
  constructor(host) { super("#shadow-root"); this.host = host; this._active = null; }
  // Like a browser: a focused node that was removed is no longer active.
  get activeElement() { return this._active?.getRootNode() === this ? this._active : null; }
  set activeElement(v) { this._active = v; }
}

function parseHTML(html) {
  const top = new El("#fragment");
  const stack = [top];
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>"']+))?)*)\s*(\/?)>|[^<]+|</g;
  let m;
  while ((m = re.exec(html))) {
    const parent = stack[stack.length - 1];
    if (m[0].startsWith("<!--")) continue;
    if (m[1]) {
      const tag = m[1].toLowerCase();
      const i = stack.findLastIndex((n, idx) => idx > 0 && n.localName === tag);
      if (i > 0) stack.length = i;
      continue;
    }
    if (m[2]) {
      const tag = m[2].toLowerCase();
      const attrs = {};
      for (const a of m[3].matchAll(/([^\s=>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+)))?/g)) {
        attrs[a[1].toLowerCase()] = decode(a[2] ?? a[3] ?? a[4] ?? "");
      }
      const el = new El(tag, attrs);
      parent.appendChild(el);
      if (RAW.has(tag)) {
        const end = html.toLowerCase().indexOf(`</${tag}`, re.lastIndex);
        el.appendChild(new TextNode(html.slice(re.lastIndex, end < 0 ? html.length : end)));
        re.lastIndex = end < 0 ? html.length : html.indexOf(">", end) + 1;
      } else if (!VOID.has(tag) && !m[4]) {
        stack.push(el);
      }
      continue;
    }
    parent.appendChild(new TextNode(decode(m[0])));
  }
  return top.childNodes;
}

function parseSelector(sel) {
  return sel.split(",").map((s) => s.trim().match(/(?:\[[^\]]*\]|[^\s[])+/g).map(parseCompound));
}

function parseCompound(src) {
  const c = { tag: null, classes: [], attrs: [] };
  const re = /(\*|[a-zA-Z][\w-]*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
  let pos = 0;
  for (const m of src.matchAll(re)) {
    if (m.index !== pos) throw new Error(`unsupported selector: ${src}`);
    pos = m.index + m[0].length;
    if (m[1]) c.tag = m[1] === "*" ? null : m[1].toLowerCase();
    else if (m[2]) c.classes.push(m[2]);
    else if (m[3]) c.attrs.push(["id", m[3]]);
    else c.attrs.push([m[4], m[5]]);
  }
  if (pos !== src.length) throw new Error(`unsupported selector: ${src}`);
  return c;
}

function matchCompound(el, c) {
  if (!(el instanceof El) || el instanceof FakeShadowRoot) return false;
  if (c.tag && el.localName !== c.tag) return false;
  const cls = el.className.split(/\s+/);
  if (!c.classes.every((x) => cls.includes(x))) return false;
  return c.attrs.every(([k, v]) => el.hasAttribute(k) && (v === undefined || el.getAttribute(k) === v));
}

function matchComplex(el, parts) {
  if (!matchCompound(el, parts[parts.length - 1])) return false;
  let i = parts.length - 2;
  for (let n = el.parentNode; i >= 0 && n; n = n.parentNode) if (matchCompound(n, parts[i])) i--;
  return i < 0;
}

/** Stand-in for HTMLElement: the card host and the editor. */
class HostElement {
  constructor() {
    this.shadowRoot = null;
    this.childNodes = [];
    this.dispatched = [];
    this._listeners = {};
  }
  attachShadow() { this.shadowRoot = new FakeShadowRoot(this); return this.shadowRoot; }
  get children() { return this.childNodes; }
  appendChild(c) { this.childNodes.push(c); return c; }
  addEventListener(t, f) { (this._listeners[t] ??= []).push(f); }
  dispatchEvent(e) { this.dispatched.push(e); return true; }
  fire(type, detail) { for (const f of this._listeners[type] || []) f({ type, detail }); }
}

const defined = {};
const docListeners = {};
globalThis.HTMLElement = HostElement;
globalThis.CustomEvent = class {
  constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
};
globalThis.customElements = {
  define: (name, cls) => { defined[name] = cls; },
  get: (name) => defined[name],
  whenDefined: async () => {},
};
globalThis.document = {
  visibilityState: "visible",
  addEventListener: (t, f) => (docListeners[t] ??= new Set()).add(f),
  removeEventListener: (t, f) => docListeners[t]?.delete(f),
  createElement: (tag) => {
    const el = defined[tag] ? new defined[tag]() : new HostElement();
    el.tagName = tag;
    return el;
  },
};
globalThis.window = globalThis;
// The card must never use a native dialog: in HA it reloads the page.
const noNativeConfirm = () => { throw new Error("window.confirm must not be used"); };
globalThis.confirm = noNativeConfirm;

const objectUrls = { created: [], revoked: [] };
URL.createObjectURL = () => {
  const u = `blob:test/${objectUrls.created.length}`;
  objectUrls.created.push(u);
  return u;
};
URL.revokeObjectURL = (u) => { objectUrls.revoked.push(u); };

function setVisibility(state) {
  document.visibilityState = state;
  for (const f of docListeners.visibilitychange || []) f();
}

// Let promise chains (fetch → render) settle. setImmediate is not mocked
// by the timer tests below, so this works with mocked timers too.
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

await import("../custom_components/cellarion/www/cellarion-inventory-card.js");

const Card = defined["cellarion-inventory-card"];
const Editor = defined["cellarion-inventory-card-editor"];

// ── Fixture ─────────────────────────────────────────────────────────

const IMG = "/api/uploads/thumbs/processed/abc123.webp.webp";
const W_LAFITE = { drinkFrom: 2024, peakFrom: 2027, peakUntil: 2032, drinkTo: 2038 };

function bottle(over) {
  return {
    id: null, wine: null, pending: null, cellar: "c1", vintage: null, size: "750ml",
    price: null, currency: null, rating: null, ratingScale: null, notes: null,
    purchaseDate: null, purchaseLocation: null, addedAt: "2025-01-02T00:00:00.000Z",
    opened: false, maturity: null, window: null, location: null, place: null, image: null,
    personal: [], ...over,
  };
}

const CELLAR_TEMP = { key: "Cellar temp", type: "decimal", unit: "°C", value: 12.5, level: "wine", by: "johan" };

/** 2 cellars; cabinet, grid and position-only racks; free-text place;
 *  unplaced; pending wine (two bottles); personal fields; null maturity. */
function inventory() {
  return {
    entry_id: "entry-a",
    instance_url: "https://cellarion.example/",
    schema: 1,
    generatedAt: "2026-10-21T12:00:00.000Z",
    cellars: [
      { id: "c1", name: "Home", role: "owner" },
      { id: "c2", name: "Summer house", role: "editor" },
    ],
    racks: [
      { id: "r1", cellar: "c1", name: "Left cabinet" },
      { id: "r2", cellar: "c1", name: "Grid wall" },
      { id: "r3", cellar: "c2", name: "Shelf" },
    ],
    wines: {
      w1: { name: "Château Lafite", producer: "Château Lafite Rothschild", type: "red", colour: null,
        country: "France", region: "Pauillac", appellation: "Pauillac", classification: null,
        grapes: ["Cabernet Sauvignon", "Merlot"], image: IMG, profile: null },
      w2: { name: "Grüner Veltliner Federspiel", producer: "Domäne Wachau", type: "white", colour: null,
        country: "Austria", region: "Wachau", appellation: null, classification: null,
        grapes: ["Grüner Veltliner"], image: null, profile: null },
      w3: { name: "Côtes du Rhône Rosé", producer: "Famille Perrin", type: "rosé", colour: null,
        country: "France", region: "Rhône", appellation: "Côtes du Rhône", classification: null,
        grapes: ["Grenache", "Cinsault"], image: null, profile: null },
      w4: { name: "Barolo", producer: "Giacomo Conterno", type: "red", colour: null,
        country: "Italy", region: "Piedmont", appellation: "Barolo", classification: null,
        grapes: ["Nebbiolo"], image: null, profile: null },
    },
    bottles: [
      bottle({ id: "b1", wine: "w1", vintage: "2015", maturity: "peak", window: W_LAFITE,
        price: 3500, currency: "SEK", rating: 95, ratingScale: "100", notes: "Gift from Anna",
        location: { rack: "r1", rackName: "Left cabinet", position: 14, shelf: 2, row: 1, col: 3 },
        personal: [
          { key: "Closure", type: "enum", unit: null, value: "cork", level: "bottle", by: "johan" },
          CELLAR_TEMP,
        ] }),
      bottle({ id: "b2", wine: "w1", vintage: "2015", maturity: "peak", window: W_LAFITE,
        price: 3400, currency: "SEK",
        location: { rack: "r2", rackName: "Grid wall", position: 10, shelf: null, row: 2, col: 4 },
        personal: [CELLAR_TEMP] }),
      bottle({ id: "b3", wine: "w1", cellar: "c2", vintage: "2010", maturity: "declining",
        window: { drinkFrom: 2015, peakFrom: 2018, peakUntil: 2022, drinkTo: 2025 },
        location: { rack: "r3", rackName: "Shelf", position: 7, shelf: null, row: null, col: null } }),
      bottle({ id: "b4", wine: "w2", vintage: "2022", maturity: "early",
        window: { drinkFrom: 2024, peakFrom: 2027, peakUntil: 2030, drinkTo: 2034 },
        place: "Basement, box 3",
        personal: [{ key: "Bought for", type: "text", unit: null, value: "Midsommar", level: "bottle", by: "johan" }] }),
      bottle({ id: "b5", wine: "w3", cellar: "c2", vintage: "2023" }),
      bottle({ id: "b6", pending: { name: "Mystery Bordeaux", producer: "Unknown Château" }, vintage: "2019",
        location: { rack: "r1", rackName: "Left cabinet", position: 1, shelf: 1, row: 1, col: 1 } }),
      bottle({ id: "b7", wine: "w4", vintage: "2016", maturity: "not-ready",
        window: { drinkFrom: 2028, peakFrom: 2032, peakUntil: 2040, drinkTo: 2050 },
        location: { rack: "r1", rackName: "Left cabinet", position: 20, shelf: 3, row: 2, col: 1 } }),
      bottle({ id: "b8", pending: { name: "Mystery Bordeaux", producer: "Unknown Château" },
        cellar: "c2", vintage: "2019" }),
    ],
  };
}

/** A hass whose cellarion entities belong to two accounts (entry-a/b). */
function makeHass(inv = inventory()) {
  const hass = {
    inventory: inv,
    reject: null,
    locale: { language: "en" },
    wsCalls: [],
    serviceCalls: [],
    fetches: [],
    states: {
      "sensor.cellarion_total_bottles": { state: "8", last_updated: "t0", attributes: {} },
      "sensor.cellarion_bottles_at_peak": { state: "2", last_updated: "t0", attributes: {} },
      "sensor.cellarion_total_bottles_2": { state: "3", last_updated: "t0", attributes: {} },
    },
    entities: {
      "sensor.cellarion_total_bottles": { entity_id: "sensor.cellarion_total_bottles", platform: "cellarion", device_id: "dev-a" },
      "sensor.cellarion_bottles_at_peak": { entity_id: "sensor.cellarion_bottles_at_peak", platform: "cellarion", device_id: "dev-a" },
      "sensor.cellarion_total_bottles_2": { entity_id: "sensor.cellarion_total_bottles_2", platform: "cellarion", device_id: "dev-b" },
    },
    devices: {
      "dev-a": { id: "dev-a", config_entries: ["entry-a"] },
      "dev-b": { id: "dev-b", config_entries: ["entry-b"] },
    },
    callWS(msg) {
      this.wsCalls.push(msg);
      if (this.reject) return Promise.reject(this.reject);
      return Promise.resolve({ context: {}, response: structuredClone(this.inventory) });
    },
    callService(...args) {
      this.serviceCalls.push(args);
      // Like the server: the bottle leaves the inventory.
      const id = args[2]?.bottle_id;
      this.inventory.bottles = this.inventory.bottles.filter((b) => b.id !== id);
      return Promise.resolve();
    },
    fetchWithAuth(url) {
      this.fetches.push(url);
      return Promise.resolve({ ok: true, status: 200, blob: async () => new Blob(["img"]) });
    },
  };
  return hass;
}

const mounted = [];
afterEach(() => {
  for (const c of mounted.splice(0)) c.disconnectedCallback();
  document.visibilityState = "visible";
  delete globalThis.IntersectionObserver;
  globalThis.confirm = noNativeConfirm;
});

async function mount(config = {}, hass = makeHass()) {
  const card = new Card();
  card.setConfig({ type: "custom:cellarion-inventory-card", ...config });
  card.hass = hass;
  card.connectedCallback();
  mounted.push(card);
  await flush();
  return { card, hass, root: card.shadowRoot };
}

const rows = (root) => root.querySelectorAll("button.row");
const rowLabels = (root) => rows(root).map((r) => {
  const meta = r.querySelector(".meta")?.textContent || "";
  const vintage = /\b(\d{4}|NV)\b/.exec(meta)?.[1];
  return r.querySelector(".name").textContent + (vintage ? ` ${vintage}` : "");
});
const rowFor = (root, label) => rows(root)[rowLabels(root).indexOf(label)];
const search = (root, text) => {
  const input = root.querySelector("input.search");
  input.value = text;
  input.dispatch("input");
};
const chip = (root, action, value) => root.querySelector(`[data-action="${action}"][data-value="${value}"]`);
const openDetail = (root, label) => {
  const row = rowFor(root, label);
  assert.ok(row, `row ${label} exists`);
  row.click();
  return root.querySelector(".detail");
};
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();

// ── Loading, data, list ─────────────────────────────────────────────

test("fetches get_bottles once on connect, with return_response", async () => {
  const { hass } = await mount();
  assert.equal(hass.wsCalls.length, 1);
  assert.deepEqual(hass.wsCalls[0], {
    type: "call_service", domain: "cellarion", service: "get_bottles",
    service_data: {}, return_response: true,
  });
});

test("entry_id is passed to get_bottles and switching it refetches", async () => {
  const { card, hass } = await mount({ entry_id: "entry-a" });
  assert.deepEqual(hass.wsCalls[0].service_data, { entry_id: "entry-a" });
  card.setConfig({ type: "custom:cellarion-inventory-card", entry_id: "entry-b" });
  await flush();
  assert.equal(hass.wsCalls.length, 2);
  assert.deepEqual(hass.wsCalls[1].service_data, { entry_id: "entry-b" });
});

test("nothing is fetched before the card is connected, and hass updates do not refetch", async () => {
  const hass = makeHass();
  const card = new Card();
  card.setConfig({});
  card.hass = hass;
  await flush();
  assert.equal(hass.wsCalls.length, 0);
  card.connectedCallback();
  mounted.push(card);
  await flush();
  for (let i = 0; i < 10; i++) card.hass = { ...hass };
  await flush();
  assert.equal(hass.wsCalls.length, 1);
});

test("shows a loading state until the inventory arrives", async () => {
  const hass = makeHass();
  let release;
  hass.callWS = function (msg) {
    this.wsCalls.push(msg);
    return new Promise((r) => { release = () => r({ response: structuredClone(this.inventory) }); });
  };
  const { root } = await mount({}, hass);
  assert.match(text(root.querySelector(".notice")), /Loading your cellar/);
  assert.equal(rows(root).length, 0);
  release();
  await flush();
  assert.equal(text(root.querySelector(".notice")), "");
  assert.equal(rows(root).length, 6);
});

test("an empty cellar says so and shows no filters", async () => {
  const inv = inventory();
  inv.bottles = [];
  const { root } = await mount({}, makeHass(inv));
  assert.match(text(root.querySelector(".notice")), /No bottles in your cellar yet/);
  assert.equal(root.querySelector(".filters").hidden, true);
});

test("a rejected get_bottles shows its message, and Try again recovers", async () => {
  const hass = makeHass();
  // HA prefixes a ServiceValidationError's message; the card drops the prefix
  hass.reject = { code: "service_validation_error", message: "Validation error: The inventory needs Cellarion 1.275 or newer." };
  const { root } = await mount({}, hass);
  const err = root.querySelector(".error");
  assert.ok(err, "error box rendered");
  assert.equal(err.getAttribute("role"), "alert");
  assert.match(text(err), /Could not load the inventory/);
  assert.match(text(err), /needs Cellarion 1\.275 or newer/);
  assert.doesNotMatch(text(err), /Validation error/);
  assert.equal(rows(root).length, 0);

  hass.reject = null;
  root.querySelector('[data-action="retry"]').click();
  await flush();
  assert.equal(root.querySelector(".error"), null);
  assert.equal(rows(root).length, 6);
});

test("groups bottles by wine and vintage, wine-less ones by pending name", async () => {
  const { root } = await mount();
  assert.deepEqual(rowLabels(root), [
    "Barolo 2016", "Château Lafite 2010", "Château Lafite 2015",
    "Côtes du Rhône Rosé 2023", "Grüner Veltliner Federspiel 2022", "Mystery Bordeaux 2019",
  ]);
  assert.match(text(rowFor(root, "Château Lafite 2015")), /2 bottles/);
  assert.match(text(rowFor(root, "Château Lafite 2010")), /1 bottle\b/);
  const mystery = rowFor(root, "Mystery Bordeaux 2019");
  assert.match(text(mystery), /Unknown Château · 2019 · 750ml/);
  assert.match(text(mystery), /2 bottles/);
  assert.equal(text(root.querySelector(".summary")), "6 wines · 8 bottles");
});

test("rows show the maturity as text, not colour only", async () => {
  const { root } = await mount();
  const pill = rowFor(root, "Château Lafite 2015").querySelector(".pill");
  assert.equal(text(pill), "At peak");
  assert.match(pill.getAttribute("style"), /var\(--cellarion-peak-color, #059669\)/);
  assert.equal(rowFor(root, "Côtes du Rhône Rosé 2023").querySelector(".pill"), null, "null maturity: no pill");
});

// ── Locations ───────────────────────────────────────────────────────

test("location lines for cabinet, grid, position-only, free text and unplaced", async () => {
  const { root } = await mount();
  const where = (label) => text(rowFor(root, label).querySelector(".where"));
  assert.equal(where("Château Lafite 2015"), "Grid wall · row 2 · col 4 +1 more place");
  assert.equal(where("Château Lafite 2010"), "Shelf · position 7");
  assert.equal(where("Barolo 2016"), "Left cabinet · shelf 3 · row 2 · slot 1");
  assert.equal(where("Grüner Veltliner Federspiel 2022"), "Basement, box 3");
  assert.equal(where("Côtes du Rhône Rosé 2023"), "Not placed");
  assert.equal(where("Mystery Bordeaux 2019"), "Left cabinet · shelf 1 · row 1 · slot 1 +1 more place");

  const detail = openDetail(root, "Château Lafite 2015");
  assert.deepEqual(detail.querySelectorAll(".b-where").map(text), [
    "Grid wall · row 2 · col 4", "Left cabinet · shelf 2 · row 1 · slot 3",
  ]);
});

// ── Search ──────────────────────────────────────────────────────────

test("search: every term must match, ignoring case and accents", async () => {
  const { root } = await mount();
  search(root, "chateau 2015");
  assert.deepEqual(rowLabels(root), ["Château Lafite 2015"]);
  search(root, "CHÂTEAU");
  assert.deepEqual(rowLabels(root), ["Château Lafite 2010", "Château Lafite 2015", "Mystery Bordeaux 2019"]);
  search(root, "  gruner   wachau ");
  assert.deepEqual(rowLabels(root), ["Grüner Veltliner Federspiel 2022"]);
  search(root, "pauillac 2010");
  assert.deepEqual(rowLabels(root), ["Château Lafite 2010"]);
});

test("search covers grapes, rack names, free-text places, pending wines and countries", async () => {
  const { root } = await mount();
  search(root, "nebbiolo");
  assert.deepEqual(rowLabels(root), ["Barolo 2016"]);
  search(root, "basement");
  assert.deepEqual(rowLabels(root), ["Grüner Veltliner Federspiel 2022"]);
  search(root, "mystery");
  assert.deepEqual(rowLabels(root), ["Mystery Bordeaux 2019"]);
  search(root, "austria");
  assert.deepEqual(rowLabels(root), ["Grüner Veltliner Federspiel 2022"]);
  search(root, "left cabinet");
  assert.deepEqual(rowLabels(root), ["Barolo 2016", "Château Lafite 2015", "Mystery Bordeaux 2019"]);
  assert.match(text(rowFor(root, "Château Lafite 2015")), /1 bottle\b/, "only the matching bottle counts");
});

test("personal values are searchable only while show_personal is on", async () => {
  const on = await mount();
  search(on.root, "midsommar");
  assert.deepEqual(rowLabels(on.root), ["Grüner Veltliner Federspiel 2022"]);

  const off = await mount({ show_personal: false });
  search(off.root, "midsommar");
  assert.deepEqual(rowLabels(off.root), []);
  assert.match(text(off.root.querySelector(".notice")), /No bottles match/);
});

test("no match offers to clear search and filters", async () => {
  const { root } = await mount();
  chip(root, "type", "red").click();
  search(root, "xyz");
  assert.equal(rows(root).length, 0);
  root.querySelector('[data-action="clear"]').click();
  assert.equal(rows(root).length, 6);
  assert.equal(root.querySelector("input.search").value, "");
  assert.equal(chip(root, "type", "red").getAttribute("aria-pressed"), "false");
  assert.equal(root.activeElement, root.querySelector("input.search"));
});

test("Escape in the search box clears it", async () => {
  const { root } = await mount();
  search(root, "barolo");
  assert.equal(rows(root).length, 1);
  const ev = root.querySelector("input.search").dispatch("keydown", { key: "Escape" });
  assert.ok(ev.defaultPrevented);
  assert.equal(root.querySelector("input.search").value, "");
  assert.equal(rows(root).length, 6);
});

// ── Filters and sort ────────────────────────────────────────────────

test("type chips list only the types present, in a fixed order, with aria-pressed", async () => {
  const { root } = await mount();
  assert.deepEqual(root.querySelectorAll('[data-action="type"]').map((n) => n.dataset.value), ["red", "white", "rosé"]);
  assert.deepEqual(root.querySelectorAll('[data-action="type"]').map(text), ["Red", "White", "Rosé"]);
  const red = chip(root, "type", "red");
  assert.equal(red.localName, "button");
  assert.equal(red.getAttribute("aria-pressed"), "false");
  red.click();
  assert.equal(chip(root, "type", "red").getAttribute("aria-pressed"), "true");
  assert.deepEqual(rowLabels(root), ["Barolo 2016", "Château Lafite 2010", "Château Lafite 2015"]);
  chip(root, "type", "white").click();
  assert.equal(rows(root).length, 4, "types combine with OR");
  chip(root, "type", "red").click();
  chip(root, "type", "white").click();
  assert.equal(rows(root).length, 6);
});

test("maturity chips: present states only, in drink-window order, null as 'No window'", async () => {
  const { root } = await mount();
  const chips = root.querySelectorAll('[data-action="maturity"]');
  assert.deepEqual(chips.map((n) => n.dataset.value), ["not-ready", "early", "peak", "declining", "none"]);
  assert.deepEqual(chips.map(text), ["Not ready", "Early", "At peak", "Declining", "No window"]);
  chip(root, "maturity", "peak").click();
  assert.deepEqual(rowLabels(root), ["Château Lafite 2015"]);
  chip(root, "maturity", "none").click();
  assert.deepEqual(rowLabels(root), ["Château Lafite 2015", "Côtes du Rhône Rosé 2023", "Mystery Bordeaux 2019"]);
});

test("type and maturity filters combine with AND", async () => {
  const { root } = await mount();
  chip(root, "type", "red").click();
  chip(root, "maturity", "declining").click();
  assert.deepEqual(rowLabels(root), ["Château Lafite 2010"]);
});

test("the cellar selector appears with several cellars and filters by cellar", async () => {
  const { root } = await mount();
  const select = root.querySelector("select.cellar");
  assert.ok(select);
  assert.equal(select.getAttribute("aria-label"), "Cellar");
  assert.deepEqual(select.querySelectorAll("option").map(text), ["All cellars", "Home", "Summer house"]);
  select.value = "c2";
  select.dispatch("change");
  assert.deepEqual(rowLabels(root), ["Château Lafite 2010", "Côtes du Rhône Rosé 2023", "Mystery Bordeaux 2019"]);
  assert.match(text(rowFor(root, "Mystery Bordeaux 2019")), /1 bottle\b/);
});

test("no cellar selector with a single cellar", async () => {
  const inv = inventory();
  inv.cellars = inv.cellars.slice(0, 1);
  const { root } = await mount({}, makeHass(inv));
  assert.equal(root.querySelector("select.cellar"), null);
});

test("sort by drink-by: earliest drinkTo first, no window last", async () => {
  const { root } = await mount();
  const byName = rowLabels(root);
  assert.equal(chip(root, "sort", "name").getAttribute("aria-pressed"), "true");
  chip(root, "sort", "drink").click();
  assert.equal(chip(root, "sort", "drink").getAttribute("aria-pressed"), "true");
  assert.deepEqual(rowLabels(root), [
    "Château Lafite 2010", "Grüner Veltliner Federspiel 2022", "Château Lafite 2015", "Barolo 2016",
    "Côtes du Rhône Rosé 2023", "Mystery Bordeaux 2019",
  ]);
  chip(root, "sort", "name").click();
  assert.deepEqual(rowLabels(root), byName);
});

test("max_results pages the list with Show more and moves focus to the first new row", async () => {
  const { root } = await mount({ max_results: 2 });
  assert.equal(rows(root).length, 2);
  const more = root.querySelector('[data-action="more"]');
  assert.match(text(more), /Show more \(4 more\)/);
  more.click();
  assert.equal(rows(root).length, 4);
  assert.equal(root.activeElement, rows(root)[2]);
  root.querySelector('[data-action="more"]').click();
  assert.equal(rows(root).length, 6);
  assert.equal(root.querySelector('[data-action="more"]'), null);
});

test("an invalid max_results is rejected so HA shows its error card", () => {
  assert.throws(() => new Card().setConfig({ max_results: "lots" }), /max_results must be a positive number/);
  assert.throws(() => new Card().setConfig({ max_results: 0 }), /max_results/);
});

// ── State survives re-renders ───────────────────────────────────────

test("the search box is never rebuilt: hass updates and refetches keep it, its text and the filters", async () => {
  const { card, hass, root } = await mount();
  const input = root.querySelector("input.search");
  input.focus();
  search(root, "lafite");
  chip(root, "type", "red").click();
  input.focus();
  card.hass = { ...hass, states: { ...hass.states } };
  await card._fetch();  // a refetch, as an entity change would trigger
  await flush();
  assert.equal(hass.wsCalls.length, 2);
  assert.equal(root.querySelector("input.search"), input, "same input node");
  assert.equal(input.value, "lafite");
  assert.equal(root.activeElement, input, "focus stayed in the search box");
  assert.equal(chip(root, "type", "red").getAttribute("aria-pressed"), "true");
  assert.deepEqual(rowLabels(root), ["Château Lafite 2010", "Château Lafite 2015"]);
});

test("a re-rendered chip keeps keyboard focus", async () => {
  const { root } = await mount();
  const red = chip(root, "type", "red");
  red.focus();
  red.click();
  const again = chip(root, "type", "red");
  assert.notEqual(again, red, "the chip was re-rendered");
  assert.equal(root.activeElement, again);
});

test("an open detail survives a refetch", async () => {
  const { card, root } = await mount();
  openDetail(root, "Château Lafite 2015");
  await card._fetch();
  await flush();
  assert.equal(root.querySelector(".detail").hidden, false);
  assert.equal(text(root.querySelector(".d-name")), "Château Lafite");
});

// ── Detail ──────────────────────────────────────────────────────────

test("the detail replaces the list, takes focus, and Back/Escape return to the row", async () => {
  const { root } = await mount();
  const detail = openDetail(root, "Château Lafite 2015");
  assert.equal(detail.hidden, false);
  assert.equal(root.querySelector(".list-view").hidden, true);
  assert.equal(root.activeElement, root.querySelector(".d-name"));
  assert.equal(root.querySelector(".d-name").getAttribute("tabindex"), "-1");

  const ev = root.activeElement.dispatch("keydown", { key: "Escape" });
  assert.ok(ev.defaultPrevented);
  assert.equal(detail.hidden, true);
  assert.equal(root.querySelector(".list-view").hidden, false);
  assert.equal(root.activeElement, rowFor(root, "Château Lafite 2015"));

  openDetail(root, "Barolo 2016");
  const back = root.querySelector('.detail [data-action="back"]');
  assert.equal(back.getAttribute("aria-label"), "Back to the list");
  back.click();
  assert.equal(root.activeElement, rowFor(root, "Barolo 2016"));
});

test("the detail shows the wine facts, a shared drink window with year labels, and each bottle", async () => {
  const { root } = await mount();
  const detail = openDetail(root, "Château Lafite 2015");
  const facts = text(detail.querySelector(".facts"));
  for (const part of ["Vintage2015", "Size750ml", "TypeRed", "RegionPauillac", "CountryFrance",
    "GrapesCabernet Sauvignon, Merlot"]) {
    assert.ok(facts.replace(/ /g, "").includes(part.replace(/ /g, "")), `${part} in ${facts}`);
  }
  assert.ok(!facts.includes("Appellation"), "appellation equal to the region is not repeated");
  assert.equal(text(detail.querySelector(".d-producer")), "Château Lafite Rothschild");

  const wins = detail.querySelectorAll(".win");
  assert.equal(wins.length, 1, "same window for both bottles: shown once");
  const years = text(wins[0].querySelector(".years"));
  assert.match(years, /From 2024/);
  assert.match(years, /Peak 2027–2032/);
  assert.match(years, /Until 2038/);
  assert.match(years, /Now: at peak/);
  assert.equal(wins[0].querySelector(".track-wrap").getAttribute("aria-hidden"), "true");
  assert.match(wins[0].innerHTML, /var\(--cellarion-peak-color, #059669\)/);
  assert.equal(wins[0].querySelectorAll(".now").length, 1);

  assert.equal(detail.querySelectorAll(".bottle").length, 2);
  const b1 = detail.querySelectorAll(".bottle")[1];
  assert.match(text(b1), /Price SEK 3,500/);
  assert.match(text(b1), /Rating 95\/100/);
  assert.match(text(b1), /Gift from Anna/);
  assert.match(text(b1), /Closure: cork/);
  assert.match(text(b1), /Cellar Home/);
  assert.equal(text(b1.querySelector(".lvl")), "bottle");
  // The wine-level entry appears once, above the bottles.
  assert.equal(detail.innerHTML.split("Cellar temp").length - 1, 1);
  assert.match(text(detail), /Cellar temp: 12\.5 °C wine/);
});

test("bottles with different drink windows each get their own bar", async () => {
  const inv = inventory();
  inv.bottles[1].window = { drinkFrom: 2025, peakFrom: null, peakUntil: null, drinkTo: 2030 };
  const { root } = await mount({}, makeHass(inv));
  const detail = openDetail(root, "Château Lafite 2015");
  assert.equal(detail.querySelectorAll(".bottle .win").length, 2);
  assert.match(text(detail), /From 2025 Until 2030/);
});

test("a wine with no window and a pending wine say so in words", async () => {
  const { root } = await mount();
  const detail = openDetail(root, "Mystery Bordeaux 2019");
  assert.match(text(detail), /No drink window yet/);
  assert.match(text(detail), /waiting to be identified/);
});

test("prices follow the HA locale", async () => {
  const hass = makeHass();
  hass.locale = { language: "de" };
  const { root } = await mount({}, hass);
  const detail = openDetail(root, "Château Lafite 2015");
  assert.match(text(detail), /3\.500/);
});

// ── Toggles ─────────────────────────────────────────────────────────

test("show_value / show_notes / show_personal / show_consume hide their piece only", async () => {
  const cases = [
    ["show_value", /Price|3,500/],
    ["show_notes", /Gift from Anna/],
    ["show_personal", /Closure|Cellar temp/],
  ];
  for (const [flag, pattern] of cases) {
    const { root } = await mount({ [flag]: false });
    const detail = openDetail(root, "Château Lafite 2015");
    assert.doesNotMatch(text(detail), pattern, flag);
    assert.equal(detail.querySelectorAll("button.take").length, 2, `${flag} leaves take-out alone`);
  }
  const { root } = await mount({ show_consume: false });
  const detail = openDetail(root, "Château Lafite 2015");
  assert.equal(detail.querySelectorAll("button.take").length, 0);
  assert.match(text(detail), /Price SEK 3,500/);
});

test("only an explicit false hides a piece", async () => {
  for (const value of [true, null, undefined]) {
    const { root } = await mount({ show_value: value });
    assert.match(text(openDetail(root, "Château Lafite 2015")), /Price/, String(value));
  }
});

test("viewers of a shared cellar get no take-out button", async () => {
  const inv = inventory();
  inv.cellars[1].role = "viewer";
  const { root } = await mount({}, makeHass(inv));
  assert.equal(openDetail(root, "Château Lafite 2010").querySelectorAll("button.take").length, 0);
  root.querySelector('[data-action="back"]').click();
  assert.equal(openDetail(root, "Château Lafite 2015").querySelectorAll("button.take").length, 2);
});

// ── Take out ────────────────────────────────────────────────────────

/** Click a bottle's Take out button, then the prompt's confirm button. */
function takeOut(root, id) {
  root.querySelector(`.detail button.take[data-bottle="${id}"]`).click();
  root.querySelector(`.detail [data-action="confirm-take"][data-bottle="${id}"]`).click();
}
const prompts = (root) => root.querySelectorAll(".detail .ask");

test("Take out opens an inline prompt in the bottle's row, focused on its confirm button", async () => {
  const { card, hass, root } = await mount();
  const detail = openDetail(root, "Château Lafite 2015");
  const [take] = detail.querySelectorAll("button.take");
  assert.equal(take.getAttribute("aria-label"), "Take out Château Lafite 2015 from Grid wall · row 2 · col 4");
  take.click();
  const [ask] = prompts(root);
  assert.ok(ask, "prompt open");
  assert.equal(ask.getAttribute("role"), "group");
  assert.equal(text(ask.querySelector(".q")), "Take out Château Lafite 2015 from Grid wall · row 2 · col 4?");
  assert.equal(ask.getAttribute("aria-labelledby"), ask.querySelector(".q").getAttribute("id"));
  const buttons = ask.querySelectorAll("button");
  assert.deepEqual(buttons.map(text), ["Cancel", "Take out"]);
  assert.ok(buttons.every((b) => b.getAttribute("type") === "button"));
  assert.equal(root.activeElement, ask.querySelector('[data-action="confirm-take"]'));
  // It sits in that bottle's own row and replaces its actions.
  const row = detail.querySelectorAll(".bottle")[0];
  assert.equal(row.querySelector(".ask"), ask);
  assert.equal(row.querySelector("a.link"), null);
  assert.equal(hass.serviceCalls.length, 0, "nothing is sent before confirming");
  assert.ok(!card.dispatched.some((e) => e.type === "show-dialog"), "no HA dialog");
});

test("confirming sends consume_bottle with the response's entry_id and notifyOnError false, then refetches", async () => {
  const { hass, root } = await mount();
  const detail = openDetail(root, "Château Lafite 2015");
  takeOut(root, "b2");
  await flush();
  assert.deepEqual(hass.serviceCalls, [
    ["cellarion", "consume_bottle", { bottle_id: "b2", entry_id: "entry-a" }, undefined, false],
  ]);
  assert.equal(hass.wsCalls.length, 2, "refetched after the take-out");
  assert.equal(detail.querySelectorAll(".bottle").length, 1);
  assert.equal(text(detail.querySelector(".b-where")), "Left cabinet · shelf 2 · row 1 · slot 3");
  assert.match(text(root.querySelector(".announce")), /Taken out: Château Lafite 2015/);
  assert.match(text(rowFor(root, "Château Lafite 2015")), /1 bottle\b/);
});

test("Cancel sends nothing and returns focus to the Take out button", async () => {
  const { hass, root } = await mount();
  openDetail(root, "Côtes du Rhône Rosé 2023").querySelector("button.take").click();
  assert.equal(text(root.querySelector(".detail .ask .q")), "Take out Côtes du Rhône Rosé 2023 (not placed)?");
  root.querySelector('.detail [data-action="cancel-take"]').click();
  await flush();
  assert.equal(prompts(root).length, 0);
  assert.equal(hass.serviceCalls.length, 0);
  assert.equal(root.activeElement, root.querySelector('.detail button.take[data-bottle="b5"]'));
});

test("Escape cancels the prompt and leaves the detail open", async () => {
  const { hass, root } = await mount();
  const detail = openDetail(root, "Château Lafite 2015");
  detail.querySelector('button.take[data-bottle="b1"]').click();
  const ev = root.activeElement.dispatch("keydown", { key: "Escape" });
  assert.ok(ev.defaultPrevented);
  assert.equal(prompts(root).length, 0);
  assert.equal(detail.hidden, false, "the first Escape only closes the prompt");
  assert.equal(root.activeElement, detail.querySelector('button.take[data-bottle="b1"]'));
  root.activeElement.dispatch("keydown", { key: "Escape" });
  assert.equal(detail.hidden, true, "the next one closes the detail");
  assert.equal(hass.serviceCalls.length, 0);
});

test("the prompt survives hass updates and a refetch, keyboard focus included", async () => {
  const { card, hass, root } = await mount();
  openDetail(root, "Château Lafite 2015").querySelector('button.take[data-bottle="b1"]').click();
  const first = root.activeElement;
  card.hass = { ...hass, states: { ...hass.states } };
  card.hass = { ...hass, locale: { language: "de" } };  // re-renders every region
  await card._fetch();  // a refetch, as an entity change would trigger
  await flush();
  const [ask] = prompts(root);
  assert.ok(ask, "prompt still open");
  const confirm = ask.querySelector('[data-action="confirm-take"]');
  assert.equal(confirm.dataset.bottle, "b1");
  assert.notEqual(confirm, first, "the prompt was re-rendered");
  assert.equal(root.activeElement, confirm);
  confirm.click();
  await flush();
  assert.deepEqual(hass.serviceCalls.map((c) => c[2].bottle_id), ["b1"]);
});

test("only one prompt is open at a time", async () => {
  const { card, hass, root } = await mount();
  const detail = openDetail(root, "Château Lafite 2015");
  detail.querySelector('button.take[data-bottle="b2"]').click();
  detail.querySelector('button.take[data-bottle="b1"]').click();
  assert.equal(prompts(root).length, 1);
  assert.equal(prompts(root)[0].querySelector('[data-action="confirm-take"]').dataset.bottle, "b1");
  assert.ok(detail.querySelector('button.take[data-bottle="b2"]'), "the other bottle has its button back");
  assert.equal(root.querySelectorAll("#ask-q").length, 1);
  // A confirm for the replaced prompt (b2) is stale and sends nothing.
  await card._take("b2");
  assert.equal(hass.serviceCalls.length, 0);
});

test("a prompt does not outlive its detail", async () => {
  const { hass, root } = await mount();
  openDetail(root, "Château Lafite 2015").querySelector("button.take").click();
  root.querySelector('.detail [data-action="back"]').click();
  openDetail(root, "Château Lafite 2015");
  assert.equal(prompts(root).length, 0);
  assert.equal(hass.serviceCalls.length, 0);
});

test("a taken-out bottle stays hidden even if the refetch is still stale", async () => {
  const hass = makeHass();
  hass.callService = function (...args) { this.serviceCalls.push(args); return Promise.resolve(); };
  const { root } = await mount({}, hass);
  openDetail(root, "Château Lafite 2015");
  takeOut(root, "b2");
  await flush();
  assert.equal(hass.wsCalls.length, 2);
  assert.equal(root.querySelectorAll(".detail .bottle").length, 1);
});

test("taking out the last bottle returns to the list", async () => {
  const { root } = await mount();
  const detail = openDetail(root, "Barolo 2016");
  takeOut(root, "b7");
  await flush();
  assert.equal(detail.hidden, true);
  assert.equal(root.querySelector(".list-view").hidden, false);
  assert.ok(!rowLabels(root).includes("Barolo 2016"));
  assert.equal(root.activeElement, root.querySelector("input.search"));
});

test("Take out is locked while the call is in flight", async () => {
  const hass = makeHass();
  let finish;
  hass.callService = function (...args) {
    this.serviceCalls.push(args);
    return new Promise((r) => { finish = r; });
  };
  const { root } = await mount({}, hass);
  const detail = openDetail(root, "Château Lafite 2015");
  takeOut(root, "b2");
  await flush();
  const busy = detail.querySelector('button.take[data-bottle="b2"]');
  assert.equal(busy.getAttribute("aria-disabled"), "true");
  assert.equal(busy.getAttribute("aria-busy"), "true");
  assert.equal(text(busy), "Taking out…");
  assert.equal(root.activeElement, busy, "focus stays on the bottle's button");
  busy.click();  // a tap while in flight opens no new prompt
  assert.equal(prompts(root).length, 0);
  await flush();
  assert.equal(hass.serviceCalls.length, 1);
  finish();
  await flush();
  assert.equal(detail.querySelector('button.take[data-bottle="b2"]'), null);
});

test("a failed take-out explains itself and unlocks the button", async () => {
  const hass = makeHass();
  hass.callService = function (...args) { this.serviceCalls.push(args); return Promise.reject({ message: "Bottle not found" }); };
  const { root } = await mount({}, hass);
  const detail = openDetail(root, "Château Lafite 2015");
  takeOut(root, "b1");
  await flush();
  const err = detail.querySelector(".b-err");
  assert.equal(err.getAttribute("role"), "alert");
  assert.match(text(err), /Could not take it out: Bottle not found/);
  assert.equal(hass.serviceCalls[0][4], false, "HA's own toast is off; this is the only message");
  assert.equal(detail.querySelector('button.take[data-bottle="b1"]').getAttribute("aria-disabled"), null);
  assert.equal(hass.wsCalls.length, 1, "no refetch after a failure");
});

test("the script-focused detail heading draws no outline", async () => {
  const { root } = await mount();
  const css = root.querySelector("style").textContent;
  assert.match(css, /\.d-name:focus, \[tabindex="-1"\]:focus \{ outline:none; \}/);
  assert.doesNotMatch(css, /\.d-name:focus-visible/);
});

// ── Links and escaping ──────────────────────────────────────────────

test("Open in Cellarion deep-links each bottle in a new tab", async () => {
  const { root } = await mount();
  const links = openDetail(root, "Château Lafite 2015").querySelectorAll("a.link");
  assert.deepEqual(links.map((a) => a.getAttribute("href")), [
    "https://cellarion.example/cellars/c1/bottles/b2",
    "https://cellarion.example/cellars/c1/bottles/b1",
  ]);
  for (const a of links) {
    assert.equal(a.getAttribute("target"), "_blank");
    assert.equal(a.getAttribute("rel"), "noopener noreferrer");
    assert.match(text(a), /Open in Cellarion/);
  }
  assert.equal(root.querySelector("a.title").getAttribute("href"), "https://cellarion.example");
});

test("a non-http instance_url gives no link anywhere", async () => {
  for (const url of ["javascript:alert(1)", "data:text/html,x", "ftp://cellarion.example", ""]) {
    const inv = inventory();
    inv.instance_url = url;
    const { root } = await mount({}, makeHass(inv));
    const detail = openDetail(root, "Château Lafite 2015");
    assert.equal(detail.querySelectorAll("a").length, 0, url);
    assert.equal(root.querySelector("a.title"), null, url);
    assert.ok(!root.innerHTML.includes("javascript:"), url);
  }
});

test("wine names and other server text are escaped", async () => {
  const evil = `<img src=x onerror="alert(1)">`;
  const inv = inventory();
  inv.wines.w4.name = evil;
  inv.wines.w4.producer = `"><script>alert(2)</script>`;
  inv.bottles[6].notes = `<b onmouseover=alert(3)>hi</b>`;
  inv.bottles[6].location.rackName = `<svg onload=alert(4)>`;
  inv.cellars[0].name = `</select><img src=y onerror=alert(5)>`;
  const { root } = await mount({}, makeHass(inv));
  const detail = openDetail(root, `${evil} 2016`);
  const all = root.querySelectorAll("img, script, svg, b");
  assert.equal(all.length, 0, "no element injected");
  assert.ok(!root.querySelectorAll("*").some((n) => Object.keys(n.attributes).some((k) => k.startsWith("on"))));
  assert.equal(text(detail.querySelector(".d-name")), evil);
  assert.match(text(detail), /<b onmouseover=alert\(3\)>hi<\/b>/);
  root.querySelector('[data-action="back"]').click();
  assert.equal(text(rows(root).find((r) => text(r).includes("onerror=\"alert(1)\"")).querySelector(".name")), evil);
});

// ── Images ──────────────────────────────────────────────────────────

test("the detail loads its label through fetchWithAuth from the integration's image view", async () => {
  const { card, hass, root } = await mount();
  assert.equal(hass.fetches.length, 0, "no IntersectionObserver: rows load nothing");
  assert.ok(rowFor(root, "Château Lafite 2015").querySelector(".thumb ha-icon"), "row shows the placeholder slot");
  assert.equal(rowFor(root, "Barolo 2016").querySelector(".thumb"), null, "no image path: no slot");
  const detail = openDetail(root, "Château Lafite 2015");
  await flush();
  assert.deepEqual(hass.fetches, ["/api/cellarion/image/entry-a/processed/abc123.webp.webp"]);
  const img = detail.querySelector(".thumb img");
  assert.ok(img.getAttribute("src").startsWith("blob:test/"));
  assert.equal(img.getAttribute("alt"), "");
  // Cached: opening again does not fetch again.
  root.querySelector('[data-action="back"]').click();
  openDetail(root, "Château Lafite 2015");
  await flush();
  assert.equal(hass.fetches.length, 1);
  card.disconnectedCallback();
  assert.ok(objectUrls.revoked.includes(img.getAttribute("src")), "object URL revoked on disconnect");
});

test("show_images: false renders no slots and fetches nothing", async () => {
  const { hass, root } = await mount({ show_images: false });
  assert.equal(root.querySelectorAll("[data-thumb]").length, 0);
  openDetail(root, "Château Lafite 2015");
  await flush();
  assert.equal(hass.fetches.length, 0);
});

test("image paths outside the thumbs folder are never requested", async () => {
  const inv = inventory();
  inv.wines.w1.image = "/api/uploads/thumbs/processed/../../secret.webp.webp";
  inv.wines.w4.image = "https://evil.example/x.webp";
  const { hass, root } = await mount({}, makeHass(inv));
  assert.equal(root.querySelectorAll("[data-thumb]").length, 0);
  openDetail(root, "Château Lafite 2015");
  await flush();
  assert.equal(hass.fetches.length, 0);
});

test("a failed image keeps the placeholder", async () => {
  const hass = makeHass();
  hass.fetchWithAuth = function (url) { this.fetches.push(url); return Promise.resolve({ ok: false, status: 404 }); };
  const { root } = await mount({}, hass);
  const detail = openDetail(root, "Château Lafite 2015");
  await flush();
  assert.equal(detail.querySelector(".thumb img"), null);
  assert.ok(detail.querySelector(".thumb ha-icon"));
});

test("rows load thumbs lazily through IntersectionObserver, at most 4 at a time", async () => {
  class FakeIO {
    constructor(cb) { this.cb = cb; this.targets = new Set(); FakeIO.last = this; }
    observe(t) { this.targets.add(t); }
    unobserve(t) { this.targets.delete(t); }
    disconnect() { this.targets.clear(); }
    showAll() { this.cb([...this.targets].map((target) => ({ target, isIntersecting: true }))); }
  }
  globalThis.IntersectionObserver = FakeIO;
  const inv = inventory();
  inv.wines = {};
  inv.bottles = [];
  for (let i = 0; i < 6; i++) {
    inv.wines[`x${i}`] = { name: `Wine ${i}`, producer: "P", type: "red", grapes: [], image: `/api/uploads/thumbs/originals/img${i}.jpg.webp` };
    inv.bottles.push(bottle({ id: `x${i}`, wine: `x${i}`, vintage: "2020" }));
  }
  const hass = makeHass(inv);
  const waiting = [];
  hass.fetchWithAuth = function (url) {
    this.fetches.push(url);
    return new Promise((r) => waiting.push(() => r({ ok: true, status: 200, blob: async () => new Blob(["x"]) })));
  };
  const { card, root } = await mount({}, hass);
  assert.equal(hass.fetches.length, 0, "nothing before the rows are visible");
  assert.equal(FakeIO.last.targets.size, 6);
  FakeIO.last.showAll();
  await flush();
  assert.equal(hass.fetches.length, 4, "concurrency cap");
  assert.equal(hass.fetches[0], "/api/cellarion/image/entry-a/originals/img0.jpg.webp");
  waiting.shift()();
  await flush();
  assert.equal(hass.fetches.length, 5);
  while (waiting.length) { waiting.shift()(); await flush(); }
  assert.equal(hass.fetches.length, 6);
  assert.equal(root.querySelectorAll(".list .thumb img").length, 6);
  const created = root.querySelectorAll(".list .thumb img").map((i) => i.getAttribute("src"));
  card.disconnectedCallback();
  for (const src of created) assert.ok(objectUrls.revoked.includes(src));
});

// ── Refresh ─────────────────────────────────────────────────────────

const bump = (card, hass, id, stamp) => {
  hass.states = { ...hass.states, [id]: { ...hass.states[id], last_updated: stamp } };
  card.hass = hass;
};

test("entity changes refetch after a 2 s debounce, never more than once per 30 s", async () => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  try {
    const { card, hass } = await mount();
    assert.equal(hass.wsCalls.length, 1);

    // A change 1 s after the first fetch waits for the 30 s floor...
    mock.timers.tick(1000);
    bump(card, hass, "sensor.cellarion_total_bottles", "t1");
    mock.timers.tick(5000);
    bump(card, hass, "sensor.cellarion_bottles_at_peak", "t2");  // ...and coalesces
    mock.timers.tick(23_999);
    assert.equal(hass.wsCalls.length, 1);
    mock.timers.tick(1);
    assert.equal(hass.wsCalls.length, 2);
    await flush();

    // A burst right after: one refetch, 30 s after the previous one.
    mock.timers.tick(1000);
    for (const s of ["t3", "t4", "t5"]) bump(card, hass, "sensor.cellarion_total_bottles", s);
    mock.timers.tick(28_999);
    assert.equal(hass.wsCalls.length, 2);
    mock.timers.tick(1);
    assert.equal(hass.wsCalls.length, 3);
    await flush();

    // Long after: just the 2 s debounce.
    mock.timers.tick(120_000);
    bump(card, hass, "sensor.cellarion_total_bottles", "t6");
    mock.timers.tick(1999);
    assert.equal(hass.wsCalls.length, 3);
    mock.timers.tick(1);
    assert.equal(hass.wsCalls.length, 4);
    await flush();

    // No loop: unchanged states and the other account's entities do nothing.
    for (let i = 0; i < 5; i++) card.hass = { ...hass };
    bump(card, hass, "sensor.cellarion_total_bottles_2", "other");
    mock.timers.tick(300_000);
    await flush();
    assert.equal(hass.wsCalls.length, 4);
    card.disconnectedCallback();
  } finally {
    mock.timers.reset();
  }
});

test("a busy sensor cannot postpone the refetch forever", async () => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  try {
    const { card, hass } = await mount();
    mock.timers.tick(100_000);
    for (let i = 0; i < 30; i++) {
      bump(card, hass, "sensor.cellarion_total_bottles", `s${i}`);
      mock.timers.tick(1000);
    }
    assert.ok(hass.wsCalls.length >= 2, "refetched despite changes every second");
    assert.ok(hass.wsCalls.length <= 3, "still throttled");
    card.disconnectedCallback();
  } finally {
    mock.timers.reset();
  }
});

test("a hidden page defers refetches; coming back after 60 s refetches once", async () => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  try {
    const { card, hass } = await mount();
    setVisibility("hidden");
    bump(card, hass, "sensor.cellarion_total_bottles", "h1");
    mock.timers.tick(120_000);
    assert.equal(hass.wsCalls.length, 1, "nothing while hidden");
    setVisibility("visible");
    assert.equal(hass.wsCalls.length, 2);
    await flush();

    // Changed while hidden briefly: one refetch on return, still held to
    // the 30 s floor after the previous one.
    setVisibility("hidden");
    bump(card, hass, "sensor.cellarion_total_bottles", "h2");
    mock.timers.tick(5000);
    setVisibility("visible");
    assert.equal(hass.wsCalls.length, 2);
    mock.timers.tick(24_999);
    assert.equal(hass.wsCalls.length, 2);
    mock.timers.tick(1);
    assert.equal(hass.wsCalls.length, 3);
    await flush();

    // Back after only 10 s, nothing changed: no refetch.
    setVisibility("hidden");
    mock.timers.tick(10_000);
    setVisibility("visible");
    mock.timers.tick(60_000);
    assert.equal(hass.wsCalls.length, 3);
    card.disconnectedCallback();
  } finally {
    mock.timers.reset();
  }
});

test("a disconnected card stops listening and refetching", async () => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  try {
    const { card, hass } = await mount();
    mock.timers.tick(60_000);
    bump(card, hass, "sensor.cellarion_total_bottles", "d1");
    card.disconnectedCallback();
    mock.timers.tick(120_000);
    setVisibility("hidden");
    setVisibility("visible");
    assert.equal(hass.wsCalls.length, 1);
    card.connectedCallback();  // back on screen after more than a minute
    assert.equal(hass.wsCalls.length, 2);
    await flush();
    card.disconnectedCallback();
  } finally {
    mock.timers.reset();
  }
});

// ── Editor, registration, layout ────────────────────────────────────

test("the editor ticks every toggle by default and offers the options", () => {
  const editor = new Editor();
  editor.setConfig({ title: "Cellar" });
  const form = editor.children[0];
  assert.equal(form.tagName, "ha-form");
  for (const flag of ["show_value", "show_notes", "show_personal", "show_images", "show_consume"]) {
    assert.equal(form.data[flag], true, flag);
  }
  const names = form.schema.flatMap((s) => (s.schema ? s.schema.map((x) => x.name) : [s.name]));
  for (const name of ["title", "entry_id", "max_results", "show_value", "show_consume"]) {
    assert.ok(names.includes(name), name);
  }
  assert.equal(form.computeLabel({ name: "show_value" }), "Prices");
  assert.equal(form.computeLabel({ name: "show_consume" }), "Take-out buttons");
});

test("the editor writes back only what differs from the defaults", () => {
  const editor = new Editor();
  editor.setConfig({ type: "custom:cellarion-inventory-card" });
  const form = editor.children[0];
  form.fire("value-changed", { value: { ...form.data, show_value: false, title: " ", entry_id: "", max_results: null } });
  assert.deepEqual(editor.dispatched.at(-1).detail.config, { type: "custom:cellarion-inventory-card", show_value: false });
  form.fire("value-changed", { value: { ...form.data, max_results: 50 } });
  assert.equal(editor.dispatched.at(-1).detail.config.max_results, 50);
});

test("registers the card for the card picker", () => {
  const entry = window.customCards.find((c) => c.type === "cellarion-inventory-card");
  assert.deepEqual(entry, {
    type: "cellarion-inventory-card",
    name: "Cellarion Inventory",
    description: "Find a bottle, see where it is, take it out",
    preview: true,
  });
  assert.equal(Card.getConfigElement().tagName, "cellarion-inventory-card-editor");
  assert.deepEqual(Card.getStubConfig(), { title: "Cellar inventory" });
  assert.deepEqual(new Card().getGridOptions(), { columns: 12, min_columns: 6, rows: "auto" });
});

test("the title defaults and can be changed live without losing the search", async () => {
  const { card, root } = await mount();
  assert.equal(text(root.querySelector(".head")), "Cellar inventory↗");
  search(root, "barolo");
  card.setConfig({ type: "custom:cellarion-inventory-card", title: "Wine fridge" });
  assert.match(text(root.querySelector(".head")), /^Wine fridge/);
  assert.equal(root.querySelector("input.search").value, "barolo");
  assert.deepEqual(rowLabels(root), ["Barolo 2016"]);
});

test("controls are real buttons with accessible names", async () => {
  const { root } = await mount();
  assert.equal(root.querySelector("input.search").getAttribute("aria-label"), "Search bottles");
  assert.equal(root.querySelector(".chips").getAttribute("role"), "group");
  for (const r of rows(root)) assert.equal(r.getAttribute("type"), "button");
  assert.equal(root.querySelector(".summary").getAttribute("aria-live"), "polite");
  assert.equal(root.querySelector(".announce").getAttribute("role"), "status");
});
