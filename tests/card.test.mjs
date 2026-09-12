/**
 * Unit tests for the bundled Lovelace card (custom_components/cellarion/www).
 *
 * The card renders one HTML string into its shadow root, so it can be
 * exercised under Node with a small stub of the few browser globals it
 * touches — no browser, no dependencies.
 *
 * Run with: node --test tests/
 */

import test from "node:test";
import assert from "node:assert/strict";

const defined = {};

class FakeElement {
  constructor() {
    this.shadowRoot = null;
    this.children = [];
    this.dispatched = [];
    this._listeners = {};
  }

  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  addEventListener(type, handler) {
    (this._listeners[type] ??= []).push(handler);
  }

  dispatchEvent(event) {
    this.dispatched.push(event);
    return true;
  }

  setAttribute() {}

  /** Test helper: deliver an event to the listeners registered above. */
  fire(type, detail) {
    for (const handler of this._listeners[type] || []) {
      handler({ type, detail, stopPropagation() {} });
    }
  }
}

class FakeNode {
  constructor(attrs) {
    this.attributes = attrs;
    this.dataset = Object.fromEntries(
      Object.entries(attrs)
        .filter(([k]) => k.startsWith("data-"))
        .map(([k, v]) => [k.slice(5).replace(/-(\w)/g, (_, ch) => ch.toUpperCase()), v]),
    );
    this.disabled = false;
    this.focused = false;
    this._listeners = {};
    this.classList = {
      names: new Set((attrs.class || "").split(/\s+/).filter(Boolean)),
      add(n) { this.names.add(n); },
      contains(n) { return this.names.has(n); },
    };
  }
  addEventListener(type, handler) { (this._listeners[type] ??= []).push(handler); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  removeAttribute(k) { delete this.attributes[k]; }
  getAttribute(k) { return this.attributes[k] ?? null; }
  focus() { this.focused = true; }
  fire(type, init = {}) {
    for (const h of this._listeners[type] || []) {
      h({ type, ...init, stopPropagation() {}, preventDefault() {} });
    }
  }
}

/** Parses the rendered HTML just enough to find tags and their attributes. */
class FakeShadowRoot {
  constructor() { this.innerHTML = ""; this._nodes = null; this.activeElement = null; }
  _all() {
    if (this._nodes?.html === this.innerHTML) return this._nodes.list;
    const list = [];
    for (const m of this.innerHTML.matchAll(/<(\w[\w-]*)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*\/?>/g)) {
      const attrs = {};
      for (const a of m[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = a[2] ?? "";
      list.push(new FakeNode({ tag: m[1], ...attrs }));
    }
    this._nodes = { html: this.innerHTML, list };
    return list;
  }
  querySelectorAll(sel) {
    const all = this._all();
    if (sel === "[data-entity]") return all.filter((n) => "data-entity" in n.attributes);
    if (sel === "button.consume") return all.filter((n) => n.attributes.tag === "button" && n.classList.contains("consume"));
    throw new Error("unsupported selector in stub: " + sel);
  }
  querySelector(sel) {
    const m = /^\[(data-[\w-]+)="([^"]*)"\]$/.exec(sel);
    return m ? this._all().find((n) => n.attributes[m[1]] === m[2]) ?? null : null;
  }
}

globalThis.HTMLElement = FakeElement;
globalThis.CustomEvent ??= class {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
};
globalThis.customElements = { define: (name, cls) => { defined[name] = cls; } };
globalThis.document = {
  createElement: (tag) => {
    const el = defined[tag] ? new defined[tag]() : new FakeElement();
    el.tagName = tag;
    return el;
  },
};
globalThis.window = globalThis;

await import("../custom_components/cellarion/www/cellarion-card.js");

const CellarionCard = defined["cellarion-card"];
const CellarionCardEditor = defined["cellarion-card-editor"];

const state = (value, attributes = {}) => ({
  state: String(value),
  last_updated: "2026-01-01T00:00:00+00:00",
  attributes,
});

function fakeHass(overrides = {}) {
  return {
    locale: { language: "en" },
    callService() {},
    states: {
      "sensor.cellarion_total_bottles": state(128),
      "sensor.cellarion_collection_value": state(4210, { currency: "SEK" }),
      "sensor.cellarion_unique_wines": state(87),
      "sensor.cellarion_collection_health_score": state(82, { grade: "A" }),
      "sensor.cellarion_service_status": state("ok", {
        instance_url: "https://cellarion.app",
      }),
      "sensor.cellarion_bottles_not_ready": state(30),
      "sensor.cellarion_bottles_early_window": state(20),
      "sensor.cellarion_bottles_at_peak": state(12, {
        peak_bottles: [
          { id: "b1", name: "Barolo Cannubi", vintage: 2015, drink_to: 2030 },
        ],
      }),
      "sensor.cellarion_bottles_declining": state(4, {
        urgent_bottles: [
          { id: "b2", name: "Chianti Classico", vintage: 2012, status: "declining" },
        ],
      }),
      "sensor.cellarion_bottles_late_window": state(2),
      ...overrides,
    },
  };
}

function render(config = {}, hass = fakeHass()) {
  const card = new CellarionCard();
  card.setConfig({ type: "custom:cellarion-card", ...config });
  card.hass = hass;
  return card.shadowRoot.innerHTML;
}

const VALUE_TILE = 'data-entity="sensor.cellarion_collection_value"';

test("shows every piece by default", () => {
  const html = render();
  assert.match(html, /class="stats"/);
  assert.ok(html.includes(">Bottles<"));
  assert.ok(html.includes(">Value<"));
  assert.ok(html.includes(">Wines<"));
  assert.ok(html.includes(VALUE_TILE));
  assert.match(html, /class="health"/);
  assert.ok(html.includes("Drink window"));
  assert.ok(html.includes("Ready to drink"));
  assert.ok(html.includes("Drink soon"));
  assert.match(html, /button class="consume"/);
});

test("show_value: false hides the collection value only", () => {
  const html = render({ show_value: false });
  assert.ok(!html.includes(">Value<"));
  assert.ok(!html.includes(VALUE_TILE));
  assert.ok(!html.includes("SEK"));
  assert.ok(html.includes(">Bottles<"));
  assert.ok(html.includes(">Wines<"));
  assert.match(html, /class="stats"/);
});

test("hiding all three stats drops the stats row", () => {
  const html = render({
    show_bottles: false, show_value: false, show_wines: false,
  });
  assert.ok(!html.includes('class="stats"'));
  assert.ok(html.includes("Drink window"));
});

test("show_health: false hides the header score", () => {
  const html = render({ show_health: false });
  assert.ok(!html.includes('class="health"'));
  assert.ok(html.includes("Wine Cellar"));
});

test("show_drink_window: false hides the bar and legend", () => {
  const html = render({ show_drink_window: false });
  assert.ok(!html.includes("Drink window"));
  assert.ok(!html.includes('class="legend"'));
  assert.ok(html.includes("Ready to drink"));
});

test("show_ready / show_soon hide their lists independently", () => {
  const noReady = render({ show_ready: false });
  assert.ok(!noReady.includes("Ready to drink"));
  assert.ok(noReady.includes("Drink soon"));

  const noSoon = render({ show_soon: false });
  assert.ok(noSoon.includes("Ready to drink"));
  assert.ok(!noSoon.includes("Drink soon"));
});

test("show_consume: false keeps the bottles but drops the buttons", () => {
  const html = render({ show_consume: false });
  assert.ok(!html.includes('class="consume"'));
  assert.ok(html.includes("Barolo Cannubi"));
  assert.ok(html.includes("Chianti Classico"));
});

test("only an explicit false hides a piece", () => {
  assert.ok(render({ show_value: true }).includes(VALUE_TILE));
  assert.ok(render({ show_value: undefined }).includes(VALUE_TILE));
  // A config written before these options existed keeps the full card.
  assert.ok(render({ title: "Cellar" }).includes(VALUE_TILE));
});

test("a service warning still shows with everything else hidden", () => {
  const hidden = Object.fromEntries(
    ["show_health", "show_bottles", "show_value", "show_wines",
      "show_drink_window", "show_ready", "show_soon"].map((k) => [k, false]),
  );
  const html = render(hidden, fakeHass({
    "sensor.cellarion_service_status": state("degraded"),
  }));
  assert.match(html, /class="banner"/);
  assert.ok(html.includes("degraded"));
});

test("the missing-entities notice is unaffected by the toggles", () => {
  const html = render({ show_value: false }, { locale: {}, states: {} });
  assert.ok(html.includes("Cellarion entities not found"));
});

test("getCardSize shrinks as pieces are hidden", () => {
  const size = (config) => {
    const card = new CellarionCard();
    card.setConfig({ type: "custom:cellarion-card", ...config });
    return card.getCardSize();
  };
  assert.equal(size({}), 5);
  assert.ok(size({ show_drink_window: false }) < 5);
  assert.equal(
    size({
      show_health: false, show_bottles: false, show_value: false,
      show_wines: false, show_drink_window: false, show_ready: false,
      show_soon: false,
    }),
    1,
  );
});

test("the editor ticks the toggles that are on by default", () => {
  const editor = new CellarionCardEditor();
  editor.setConfig({ title: "Wine Cellar" });
  const form = editor.children[0];
  assert.equal(form.tagName, "ha-form");
  assert.equal(form.data.show_value, true);
  assert.equal(form.data.show_consume, true);
  assert.equal(form.data.title, "Wine Cellar");
  assert.ok(form.schema.some((s) => s.type === "expandable"));
  assert.equal(form.computeLabel({ name: "show_value" }), "Collection value");
});

test("the editor writes back only the pieces turned off", () => {
  const editor = new CellarionCardEditor();
  editor.setConfig({ title: "Wine Cellar", prefix: "sensor.cellarion" });
  const form = editor.children[0];
  form.fire("value-changed", { value: { ...form.data, show_value: false } });

  const config = editor.dispatched.at(-1).detail.config;
  assert.equal(config.show_value, false);
  assert.equal(config.title, "Wine Cellar");
  for (const key of ["show_health", "show_bottles", "show_wines",
    "show_drink_window", "show_ready", "show_soon", "show_consume"]) {
    assert.ok(!(key in config), `${key} should be pruned when left on`);
  }
});

test("a config change re-renders a live card at once", () => {
  // Older editor previews call setConfig without re-assigning hass; the
  // toggle must still take effect immediately.
  const card = new CellarionCard();
  card.setConfig({ type: "custom:cellarion-card" });
  card.hass = fakeHass();
  assert.ok(card.shadowRoot.innerHTML.includes(VALUE_TILE));
  card.setConfig({ type: "custom:cellarion-card", show_value: false });
  assert.ok(!card.shadowRoot.innerHTML.includes(VALUE_TILE));
});

test("the editor mirrors the card for a malformed toggle value", () => {
  // `show_value:` left empty in YAML arrives as null. The card shows the
  // piece (only false hides), so the form must tick it too.
  assert.ok(render({ show_value: null }).includes(VALUE_TILE));
  const editor = new CellarionCardEditor();
  editor.setConfig({ show_value: null });
  assert.equal(editor.children[0].data.show_value, true);
});

test("the editor keeps a hidden piece hidden across re-renders", () => {
  const editor = new CellarionCardEditor();
  editor.setConfig({ show_value: false });
  const form = editor.children[0];
  assert.equal(form.data.show_value, false);

  editor.setConfig({ show_value: false, title: "Cellar" });
  assert.equal(editor.children.length, 1, "the form is created once");
  assert.equal(form.data.show_value, false);
  assert.equal(form.data.title, "Cellar");
});


// ── Entity resolution through the registry ─────────────────────────

const ENTRY_A = "entry-a";
const ENTRY_B = "entry-b";

/** A hass with two Cellarion accounts, the second one with renamed ids. */
function registryHass() {
  const hass = fakeHass();
  const keys = {
    total_bottles: "_total_bottles", collection_value: "_collection_value",
    unique_wines: "_unique_wines", health_score: "_collection_health_score",
    service_health: "_service_status", bottles_not_ready: "_bottles_not_ready",
    bottles_early: "_bottles_early_window", bottles_at_peak: "_bottles_at_peak",
    bottles_declining: "_bottles_declining", bottles_late: "_bottles_late_window",
  };
  hass.entities = {};
  hass.devices = {
    "dev-a": { id: "dev-a", config_entries: [ENTRY_A], primary_config_entry: ENTRY_A },
    "dev-b": { id: "dev-b", config_entries: [ENTRY_B], primary_config_entry: ENTRY_B },
  };
  for (const [key, suffix] of Object.entries(keys)) {
    hass.entities["sensor.cellarion" + suffix] = {
      entity_id: "sensor.cellarion" + suffix, platform: "cellarion",
      translation_key: key, device_id: "dev-a",
    };
    // Second account: HA de-duplicated its ids with a _2 suffix, and the
    // user renamed one of them by hand
    const id2 = key === "total_bottles"
      ? "sensor.summer_house_bottles" : "sensor.cellarion" + suffix + "_2";
    hass.entities[id2] = {
      entity_id: id2, platform: "cellarion", translation_key: key, device_id: "dev-b",
    };
  }
  hass.states["sensor.summer_house_bottles"] = state(7);
  hass.states["sensor.cellarion_unique_wines_2"] = state(5);
  hass.states["sensor.cellarion_collection_value_2"] = state(900, { currency: "EUR" });
  hass.states["sensor.cellarion_service_status_2"] = state("ok", {});
  hass.states["sensor.cellarion_bottles_at_peak_2"] = state(3, {
    peak_bottles: [{ id: "b9", name: "Summer Rosé", vintage: 2024, drink_to: 2026 }],
  });
  hass.states["sensor.cellarion_bottles_declining_2"] = state(0, { urgent_bottles: [] });
  return hass;
}

test("with one account the card finds its entities through the registry", () => {
  const hass = registryHass();
  delete hass.devices["dev-b"];
  for (const [id, e] of Object.entries(hass.entities)) if (e.device_id === "dev-b") delete hass.entities[id];
  const html = render({}, hass);
  assert.ok(html.includes('data-entity="sensor.cellarion_total_bottles"'));
  assert.ok(html.includes(">128<"));
});

test("entry_id selects the second account even with renamed ids", () => {
  const html = render({ entry_id: ENTRY_B }, registryHass());
  assert.ok(html.includes('data-entity="sensor.summer_house_bottles"'));
  assert.ok(html.includes(">7<"), "bottle count of account B");
  assert.ok(html.includes("Summer Rosé"));
  assert.ok(!html.includes("Barolo Cannubi"), "account A's list must not leak in");
});

test("with several accounts and no entry_id the first one set up is shown", () => {
  const html = render({}, registryHass());
  assert.ok(html.includes('data-entity="sensor.cellarion_total_bottles"'));
  assert.ok(html.includes(">128<"));
});

test("an entry_id that matches no account explains itself", () => {
  const html = render({ entry_id: "gone" }, registryHass());
  assert.match(html, /No Cellarion account matches/);
  assert.ok(html.includes("entry_id: gone"));
});

test("an explicit prefix bypasses the registry", () => {
  const hass = registryHass();
  hass.states["sensor.custom_total_bottles"] = state(3);
  const html = render({ prefix: "sensor.custom" }, hass);
  assert.ok(html.includes('data-entity="sensor.custom_total_bottles"'));
  assert.ok(html.includes(">3<"));
});

test("an empty or blank prefix falls back to the default ids", () => {
  for (const prefix of ["", "   "]) {
    const html = render({ prefix });
    assert.ok(html.includes('data-entity="sensor.cellarion_total_bottles"'), JSON.stringify(prefix));
  }
});

test("a non-string prefix is rejected so HA shows its error card", () => {
  const card = new CellarionCard();
  assert.throws(() => card.setConfig({ prefix: 42 }), /prefix must be a string/);
});

test("the stub config no longer pins a prefix", () => {
  assert.deepEqual(CellarionCard.getStubConfig(), { title: "Wine Cellar" });
});

test("the editor drops cleared text fields", () => {
  const editor = new CellarionCardEditor();
  editor.setConfig({ type: "custom:cellarion-card", prefix: "sensor.x" });
  const form = editor.children[0];
  form.fire("value-changed", { value: { type: "custom:cellarion-card", prefix: "", url: "  ", title: "Cellar" } });
  assert.deepEqual(editor.dispatched[0].detail.config, { type: "custom:cellarion-card", title: "Cellar" });
});

// ── Accessibility, theming and status text ──────────────────────────

test("interactive pieces carry accessible names", () => {
  const html = render();
  assert.match(html, /aria-label="Mark Barolo Cannubi as drunk"/);
  assert.match(html, /aria-label="At peak: 12 bottles"/);
  assert.match(html, /aria-label="Health score 82, grade A"/);
  assert.match(html, /class="bar" role="img" aria-label="Drink window: Not ready 30, Early 20, At peak 12, Declining 4, Late 2"/);
  assert.match(html, /<ha-icon icon="mdi:bottle-wine" aria-hidden="true">/);
});

test("semantic colours are theme tokens with contrast-mixed text", () => {
  const html = render();
  assert.match(html, /var\(--cellarion-peak-color, #059669\)/);
  assert.match(html, /color-mix\(in srgb, var\(--cellarion-warn-color, #D97706\) 72%, var\(--primary-text-color\)\)/);
  assert.ok(!/color:#[0-9A-F]{6}/i.test(html), "no raw hex used directly as a text colour");
});

test("an unknown service status is not reported as unreachable", () => {
  const unknown = render({}, fakeHass({ "sensor.cellarion_service_status": state("unknown") }));
  assert.match(unknown, /status is unknown/);
  const down = render({}, fakeHass({ "sensor.cellarion_service_status": state("unreachable") }));
  assert.match(down, /Cannot reach Cellarion/);
});

test("a status the server invents does not reach the prototype chain", () => {
  const hass = fakeHass({
    "sensor.cellarion_bottles_declining": state(1, {
      urgent_bottles: [{ id: "b3", name: "Odd", vintage: 2000, status: "constructor" }],
    }),
  });
  const html = render({}, hass);
  assert.ok(!html.includes("function"), "prototype lookup leaked");
  assert.match(html, /--cellarion-warn-color/);
});

test("numbers follow the HA locale and a missing currency shows a bare number", () => {
  const hass = fakeHass({
    "sensor.cellarion_total_bottles": state(12345),
    "sensor.cellarion_collection_value": state(4210, {}),
  });
  hass.locale = { language: "de" };
  const html = render({}, hass);
  assert.ok(html.includes(">12.345<"), "German thousands separator");
  assert.ok(html.includes(">4.210<"), "value without an invented currency");
});

test("a locale change re-renders the card", () => {
  const hass = fakeHass({ "sensor.cellarion_total_bottles": state(12345) });
  const card = new CellarionCard();
  card.setConfig({});
  card.hass = hass;
  assert.ok(card.shadowRoot.innerHTML.includes(">12,345<"));
  card.hass = { ...hass, locale: { language: "de" } };
  assert.ok(card.shadowRoot.innerHTML.includes(">12.345<"));
});

// ── Interaction: clicks, keyboard, consume ──────────────────────────

test("tiles open more-info on click and on Enter", () => {
  const card = new CellarionCard();
  card.setConfig({});
  card.hass = fakeHass();
  const tile = card.shadowRoot.querySelectorAll("[data-entity]")
    .find((n) => n.dataset.entity === "sensor.cellarion_total_bottles");
  assert.equal(tile.getAttribute("role"), "button");
  assert.equal(tile.getAttribute("tabindex"), "0");
  tile.fire("click");
  tile.fire("keydown", { key: "Enter" });
  tile.fire("keydown", { key: "x" });
  const opened = card.dispatched.filter((e) => e.type === "hass-more-info");
  assert.equal(opened.length, 2);
  assert.equal(opened[0].detail.entityId, "sensor.cellarion_total_bottles");
});

test("the consume button calls the service once, with entry_id, and locks itself", async () => {
  const calls = [];
  let resolveCall;
  const hass = fakeHass();
  hass.callService = (...args) => { calls.push(args); return new Promise((r) => { resolveCall = r; }); };
  globalThis.confirm = () => true;
  const card = new CellarionCard();
  card.setConfig({ entry_id: "entry-a" });
  card.hass = hass;
  const [btn] = card.shadowRoot.querySelectorAll("button.consume");
  btn.fire("click");
  btn.fire("click"); // double tap while in flight
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], ["cellarion", "consume_bottle", { bottle_id: "b1", entry_id: "entry-a" }]);
  assert.equal(btn.disabled, true);
  assert.equal(btn.getAttribute("aria-busy"), "true");
  resolveCall();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(btn.classList.contains("done"));
  assert.match(btn.getAttribute("aria-label"), /marked as drunk/);
});

test("a failed consume call unlocks the button again", async () => {
  const hass = fakeHass();
  hass.callService = () => Promise.reject(new Error("nope"));
  globalThis.confirm = () => true;
  const card = new CellarionCard();
  card.setConfig({});
  card.hass = hass;
  const [btn] = card.shadowRoot.querySelectorAll("button.consume");
  btn.fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(btn.disabled, false);
  assert.equal(btn.getAttribute("aria-busy"), null);
});

test("declining the confirmation sends nothing", () => {
  const calls = [];
  const hass = fakeHass();
  hass.callService = (...args) => { calls.push(args); };
  globalThis.confirm = () => false;
  const card = new CellarionCard();
  card.setConfig({});
  card.hass = hass;
  card.shadowRoot.querySelectorAll("button.consume")[0].fire("click");
  assert.equal(calls.length, 0);
});

test("keyboard focus survives a re-render", () => {
  const card = new CellarionCard();
  card.setConfig({});
  card.hass = fakeHass();
  const [btn] = card.shadowRoot.querySelectorAll("button.consume");
  card.shadowRoot.activeElement = btn;
  card.hass = fakeHass({ "sensor.cellarion_total_bottles": state(129) });
  const again = card.shadowRoot.querySelector('[data-bottle="b1"]');
  assert.ok(again && again !== btn, "a new node was rendered");
  assert.equal(again.focused, true);
});

test("javascript: links never become the title link", () => {
  const html = render({ url: "javascript:alert(1)" });
  assert.ok(!html.includes("javascript:"));
  assert.match(html, /<div class="title">Wine Cellar<\/div>/);
});
