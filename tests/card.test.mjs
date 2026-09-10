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
    this.shadowRoot = { innerHTML: "", querySelectorAll: () => [] };
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
