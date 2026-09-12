/**
 * Cellarion Card — a Lovelace card for the Cellarion integration.
 *
 * Shows collection stats, a drink-window maturity bar, and the bottles
 * that need attention. No build step; ships as a single file with the
 * integration, which registers it as a dashboard resource automatically.
 *
 * Config:
 *   type: custom:cellarion-card
 *   title: Wine Cellar            (optional)
 *   entry_id: <config entry>     (optional — picks the account when more
 *                                 than one Cellarion account is configured;
 *                                 with one account nothing is needed)
 *   url: https://cellarion.app   (optional — overrides the title/link target)
 *   prefix: sensor.cellarion     (optional, advanced — entity id prefix. The
 *                                 card normally finds its entities through
 *                                 the entity registry, so renamed ids and a
 *                                 second account just work; set a prefix
 *                                 only to force a specific set of ids)
 *   show_value: false            (optional — each show_* flag in TOGGLES
 *                                 below defaults to true; set one to false
 *                                 to keep that piece off the card, e.g. the
 *                                 collection value on a shared dashboard)
 *
 * Theming: every semantic colour can be overridden from a HA theme:
 *   --cellarion-not-ready-color, --cellarion-early-color,
 *   --cellarion-peak-color, --cellarion-declining-color,
 *   --cellarion-late-color, --cellarion-good-color, --cellarion-warn-color,
 *   --cellarion-bad-color
 */

const DEFAULT_PREFIX = "sensor.cellarion";

// Maturity states in drink-window order. `entity` is the id suffix used in
// prefix mode; `key` is the sensor's translation_key used in registry mode.
const MATURITY = [
  { key: "bottles_not_ready", entity: "_bottles_not_ready", label: "Not ready",
    color: "var(--cellarion-not-ready-color, #2563EB)" },
  { key: "bottles_early", entity: "_bottles_early_window", label: "Early",
    color: "var(--cellarion-early-color, #0891B2)" },
  { key: "bottles_at_peak", entity: "_bottles_at_peak", label: "At peak",
    color: "var(--cellarion-peak-color, #059669)" },
  { key: "bottles_declining", entity: "_bottles_declining", label: "Declining",
    color: "var(--cellarion-declining-color, #D97706)" },
  { key: "bottles_late", entity: "_bottles_late_window", label: "Late",
    color: "var(--cellarion-late-color, #DC2626)" },
];

// Semantic colours for text and small chips. Fills keep the palette as is;
// text is mixed towards the theme's text colour (see `ink()`), which
// darkens it on a light theme and lightens it on a dark one, so small
// labels reach AA contrast either way.
const GOOD = "var(--cellarion-good-color, #059669)";
const WARN = "var(--cellarion-warn-color, #D97706)";
const BAD = "var(--cellarion-bad-color, #DC2626)";
const STATUS_COLOR = { declining: WARN, late: BAD };
const ink = (color) => `color-mix(in srgb, ${color} 72%, var(--primary-text-color))`;

// Every entity the card reads: id suffix (prefix mode) → translation_key
// (registry mode).
const ENTITY_KEYS = {
  _total_bottles: "total_bottles",
  _collection_value: "collection_value",
  _unique_wines: "unique_wines",
  _collection_health_score: "health_score",
  _service_status: "service_health",
  ...Object.fromEntries(MATURITY.map((m) => [m.entity, m.key])),
};

// Pieces of the card that can be switched off from the config. All are
// shown unless the config sets them to false, so existing cards and the
// card picker's preview keep the full layout.
const TOGGLES = [
  { name: "show_health", label: "Health score" },
  { name: "show_bottles", label: "Bottles count" },
  { name: "show_value", label: "Collection value" },
  { name: "show_wines", label: "Unique wines" },
  { name: "show_drink_window", label: "Drink window bar" },
  { name: "show_ready", label: "“Ready to drink” list" },
  { name: "show_soon", label: "“Drink soon” list" },
  { name: "show_consume", label: "Consume buttons" },
];

// Only an explicit false hides a piece; anything else (including a config
// written before these options existed) shows it.
const isShown = (config, name) => config?.[name] !== false;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/**
 * Find the card's entities through the entity registry: every entity of
 * the cellarion platform, grouped by device (= config entry). Returns a
 * map translation_key → entity_id for the chosen account, or null when the
 * registry isn't available (older HA, card picker without hass) so the
 * caller falls back to id prefixes.
 */
function resolveEntities(hass, entryId) {
  if (!hass?.entities || !hass?.devices) return null;
  const byDevice = new Map();
  for (const e of Object.values(hass.entities)) {
    if (e.platform !== "cellarion" || !e.translation_key || !e.device_id) continue;
    if (!byDevice.has(e.device_id)) byDevice.set(e.device_id, new Map());
    byDevice.get(e.device_id).set(e.translation_key, e.entity_id);
  }
  if (!byDevice.size) return null;
  let deviceId;
  if (entryId) {
    deviceId = [...byDevice.keys()].find((id) =>
      hass.devices[id]?.config_entries?.includes(entryId));
    if (!deviceId) return { missingEntry: true };
  } else {
    // One account: the only device. Several without entry_id: the one set
    // up first (stable across reloads), so the card never flips accounts.
    deviceId = [...byDevice.keys()].sort((a, b) => {
      const ea = hass.devices[a]?.primary_config_entry || "";
      const eb = hass.devices[b]?.primary_config_entry || "";
      return ea < eb ? -1 : ea > eb ? 1 : 0;
    })[0];
  }
  return { ids: byDevice.get(deviceId), accounts: byDevice.size };
}

class CellarionCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Wine Cellar" };
  }

  static getConfigElement() {
    return document.createElement("cellarion-card-editor");
  }

  setConfig(config) {
    if (config?.prefix != null && typeof config.prefix !== "string") {
      throw new Error("cellarion-card: prefix must be a string");
    }
    this._config = { title: "Wine Cellar", ...config };
    this._fingerprint = null;
    this._registry = null;
    // Re-render right away when the config of a live card changes (the
    // editor preview does that) instead of waiting for the next state
    // update to come in.
    if (this._hass) this.hass = this._hass;
  }

  set hass(hass) {
    this._hass = hass;
    this._resolve();
    const fp = this._computeFingerprint();
    if (fp !== this._fingerprint) {
      this._fingerprint = fp;
      this._render();
    }
  }

  getCardSize() {
    // Rough row count for the masonry layout: start from the full card and
    // give back the rows the hidden pieces would have taken.
    let size = 5;
    if (!this._show("show_bottles") && !this._show("show_value")
        && !this._show("show_wines")) size -= 1;
    if (!this._show("show_drink_window")) size -= 2;
    if (!this._show("show_ready")) size -= 1;
    if (!this._show("show_soon")) size -= 1;
    return Math.max(1, size);
  }

  _show(name) {
    return isShown(this._config, name);
  }

  _prefix() {
    return this._config.prefix?.trim() || DEFAULT_PREFIX;
  }

  // Registry lookups walk every entity in HA, so only redo them when the
  // registry objects themselves changed (HA replaces them on any update).
  _resolve() {
    const hass = this._hass;
    if (this._config.prefix?.trim()) { this._registry = null; return; }
    if (this._registry && this._registry.entities === hass.entities
        && this._registry.devices === hass.devices) return;
    this._registry = {
      entities: hass.entities,
      devices: hass.devices,
      result: resolveEntities(hass, this._config.entry_id),
    };
  }

  _entityId(suffix) {
    const ids = this._registry?.result?.ids;
    return ids?.get(ENTITY_KEYS[suffix]) || `${this._prefix()}${suffix}`;
  }

  _entityIds() {
    return Object.keys(ENTITY_KEYS).map((s) => this._entityId(s));
  }

  _computeFingerprint() {
    if (!this._hass || !this._config) return null;
    const lang = this._hass.locale?.language || "";
    return lang + ";" + this._entityIds()
      .map((id) => {
        const s = this._hass.states[id];
        return s ? `${s.state}|${s.last_updated}` : "missing";
      })
      .join(";");
  }

  _state(suffix) {
    return this._hass.states[this._entityId(suffix)];
  }

  _num(suffix) {
    const s = this._state(suffix);
    if (!s || s.state === "unknown" || s.state === "unavailable") return null;
    const n = Number(s.state);
    return Number.isFinite(n) ? n : null;
  }

  _fmt(n) {
    if (n == null) return "—";
    try {
      return new Intl.NumberFormat(this._hass.locale?.language || "en").format(n);
    } catch (e) {
      return String(n);
    }
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      bubbles: true, composed: true, detail: { entityId },
    }));
  }

  _formatValue() {
    const s = this._state("_collection_value");
    if (!s || s.state === "unknown" || s.state === "unavailable") return "—";
    const n = Number(s.state);
    if (!Number.isFinite(n)) return "—";
    const currency = s.attributes.currency || s.attributes.unit_of_measurement;
    const lang = this._hass.locale?.language || "en";
    if (!currency) return this._fmt(Math.round(n));
    try {
      return new Intl.NumberFormat(lang, {
        style: "currency", currency, maximumFractionDigits: 0,
      }).format(n);
    } catch (e) {
      return `${Math.round(n)} ${currency}`;
    }
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    // Keep keyboard focus where it was: the whole shadow root is rebuilt on
    // every state change, which would otherwise drop focus to the page.
    const active = this.shadowRoot.activeElement;
    const focusKey = active?.dataset?.bottle
      ? `[data-bottle="${active.dataset.bottle}"]`
      : active?.dataset?.entity ? `[data-entity="${active.dataset.entity}"]` : null;

    const notice = (text) => {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding:16px;color:var(--secondary-text-color)">${text}</div>
        </ha-card>`;
    };

    if (this._registry?.result?.missingEntry) {
      notice(`No Cellarion account matches <code>entry_id: ${esc(this._config.entry_id)}</code>.
        Pick the account again in the card editor.`);
      return;
    }

    const totalState = this._state("_total_bottles");
    if (!totalState) {
      const looked = esc(this._entityId("_total_bottles"));
      notice(`Cellarion entities not found (looked for <code>${looked}</code>).
        Is the Cellarion integration set up?`);
      return;
    }

    const bottles = this._num("_total_bottles");
    const wines = this._num("_unique_wines");
    const peak = this._num("_bottles_at_peak") ?? 0;
    const health = this._num("_collection_health_score");
    const grade = this._state("_collection_health_score")?.attributes?.grade;
    const service = this._state("_service_status")?.state;

    const counts = MATURITY.map((m) => ({
      ...m, count: this._num(m.entity) ?? 0,
    }));
    const maturityTotal = counts.reduce((a, c) => a + c.count, 0);

    const urgent = (this._state("_bottles_declining")?.attributes
      ?.urgent_bottles || []).slice(0, 5);
    const ready = (this._state("_bottles_at_peak")?.attributes
      ?.peak_bottles || []).slice(0, 5);
    const rawLink = this._config.url
      || this._state("_service_status")?.attributes?.instance_url;
    // Only ever open an http(s) target — never a javascript:/data: URL that
    // could arrive via card config or a spoofed instance_url attribute.
    const link = /^https?:\/\//i.test(rawLink || "") ? rawLink : null;
    const urgentTotal = (this._num("_bottles_declining") ?? 0)
      + (this._num("_bottles_late_window") ?? 0);
    const moreLine = (total, shown) => total > shown ? (link
      ? `<a class="more" href="${esc(link)}" target="_blank" rel="noopener">+ ${total - shown} more in Cellarion</a>`
      : `<div class="more">+ ${total - shown} more</div>`) : "";

    const healthColor = health == null ? "var(--secondary-text-color)"
      : ink(health >= 80 ? GOOD : health >= 60 ? WARN : BAD);

    const bar = maturityTotal > 0
      ? counts.filter((c) => c.count > 0).map((c) => `
          <div class="seg" title="${esc(c.label)}: ${c.count}"
               aria-label="${esc(c.label)}: ${c.count} bottles"
               data-entity="${esc(this._entityId(c.entity))}"
               style="flex-grow:${c.count};background:${c.color}"></div>`).join("")
      : `<div class="seg empty" style="flex-grow:1"></div>`;

    const legend = counts.map((c) => `
        <div class="lg ${c.count === 0 ? "zero" : ""}"
             aria-label="${esc(c.label)}: ${c.count} bottles"
             data-entity="${esc(this._entityId(c.entity))}">
          <span class="chip" style="background:${c.color}" aria-hidden="true"></span>
          <span>${esc(c.label)}</span>
          <span class="cnt">${c.count}</span>
        </div>`).join("");

    const consumeBtn = (b) => b.id && this._show("show_consume") ? `
            <button class="consume" data-bottle="${esc(b.id)}"
                    data-name="${esc(b.name)}" title="Mark as drunk"
                    aria-label="Mark ${esc(b.name)} as drunk">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z"/></svg>
            </button>` : "";

    const readyHtml = this._show("show_ready") && ready.length ? `
      <div class="section-label">Ready to drink</div>
      <div class="urgent">
        ${ready.map((b) => `
          <div class="bottle">
            <ha-icon icon="mdi:bottle-wine" aria-hidden="true"></ha-icon>
            <span class="bname">${esc(b.name)}</span>
            <span class="vintage">${esc(b.vintage)}</span>
            ${b.drink_to ? `<span class="vintage">until ${esc(b.drink_to)}</span>` : ""}
            ${consumeBtn(b)}
          </div>`).join("")}
        ${moreLine(peak, ready.length)}
      </div>` : "";

    const statusStyle = (status) => {
      const c = Object.hasOwn(STATUS_COLOR, status) ? STATUS_COLOR[status] : WARN;
      return `background:color-mix(in srgb, ${c} 12%, transparent);color:${ink(c)}`;
    };
    const urgentHtml = this._show("show_soon") && urgent.length ? `
      <div class="section-label">Drink soon</div>
      <div class="urgent">
        ${urgent.map((b) => `
          <div class="bottle">
            <ha-icon icon="mdi:bottle-wine" aria-hidden="true"></ha-icon>
            <span class="bname">${esc(b.name)}</span>
            <span class="vintage">${esc(b.vintage)}</span>
            <span class="status" style="${statusStyle(b.status)}">${esc(b.status)}</span>
            ${consumeBtn(b)}
          </div>`).join("")}
        ${moreLine(urgentTotal, urgent.length)}
      </div>` : "";

    const statTiles = [
      { name: "show_bottles", entity: "_total_bottles", label: "Bottles",
        value: this._fmt(bottles) },
      { name: "show_value", entity: "_collection_value", label: "Value",
        value: this._formatValue() },
      { name: "show_wines", entity: "_unique_wines", label: "Wines",
        value: this._fmt(wines) },
    ].filter((t) => this._show(t.name));
    const statsHtml = statTiles.length ? `
        <div class="stats">
          ${statTiles.map((t) => `
          <div class="stat" data-entity="${esc(this._entityId(t.entity))}"
               aria-label="${esc(t.label)}: ${esc(t.value)}">
            <div class="v">${esc(t.value)}</div><div class="l">${esc(t.label)}</div>
          </div>`).join("")}
        </div>` : "";

    const windowHtml = this._show("show_drink_window") ? `
        <div class="section-label">Drink window${maturityTotal ? ` · ${this._fmt(maturityTotal)} bottles` : ""}</div>
        <div class="bar" role="img" aria-label="Drink window: ${counts.map((c) => `${c.label} ${c.count}`).join(", ")}">${bar}</div>
        <div class="legend">${legend}</div>` : "";

    const serviceMsg =
      ["unavailable", "unreachable"].includes(service)
        ? "Cannot reach Cellarion right now — sensors resume automatically"
        : service === "unknown"
          ? "Cellarion status is unknown — check the integration"
          : service === "degraded"
            ? "Cellarion is up but degraded (database issue)"
            : `Cellarion status: ${service}`;
    const serviceHtml = service && service !== "ok" ? `
      <div class="banner" role="status">
        <ha-icon icon="mdi:alert" aria-hidden="true"></ha-icon>
        ${esc(serviceMsg)}
      </div>` : "";

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .head { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
        .title { font-size:1.1em; font-weight:500; color:var(--primary-text-color);
                 text-decoration:none; }
        a.title:hover { text-decoration:underline; }
        .title .ext { font-size:0.75em; color:var(--secondary-text-color);
                 margin-left:4px; vertical-align:super; }
        .consume { display:inline-flex; align-items:center; justify-content:center;
                 width:26px; height:26px; flex:none; border-radius:50%;
                 border:1px solid var(--secondary-text-color); background:none;
                 color:var(--secondary-text-color); cursor:pointer; padding:0; }
        .consume:hover, .consume:focus-visible { border-color:${ink(GOOD)}; color:${ink(GOOD)}; }
        .consume:disabled { opacity:.45; cursor:progress; }
        .consume.done { border-color:${ink(GOOD)}; color:${ink(GOOD)}; opacity:1; cursor:default; }
        .more { display:block; font-size:0.8em; color:var(--secondary-text-color);
                margin-top:2px; text-decoration:none; }
        a.more:hover { text-decoration:underline; }
        .health { display:flex; align-items:baseline; gap:6px; cursor:pointer;
                  font-weight:600; color:${healthColor}; }
        .health .grade { font-size:0.8em; border:1px solid ${healthColor};
                  border-radius:10px; padding:1px 8px; }
        .stats { display:flex; gap:8px; margin-bottom:16px; }
        .stat { flex:1; background:var(--secondary-background-color, rgba(127,127,127,.08));
                border-radius:8px; padding:10px 12px; cursor:pointer; }
        .stat .v { font-size:1.5em; font-weight:600; color:var(--primary-text-color);
                   font-variant-numeric: tabular-nums; }
        .stat .l { font-size:0.78em; color:var(--secondary-text-color); margin-top:2px; }
        .section-label { font-size:0.78em; color:var(--secondary-text-color);
                   text-transform:uppercase; letter-spacing:.05em; margin-bottom:6px; }
        .bar { display:flex; gap:2px; height:14px; margin-bottom:8px; }
        .seg { border-radius:3px; min-width:6px; cursor:pointer; }
        .seg.empty { background:var(--secondary-background-color, rgba(127,127,127,.15)); cursor:default; }
        .legend { display:flex; flex-wrap:wrap; gap:4px 14px; margin-bottom:4px; }
        .lg { display:flex; align-items:center; gap:5px; font-size:0.82em;
              color:var(--primary-text-color); cursor:pointer; }
        .lg.zero { color:var(--secondary-text-color); }
        .lg .chip { width:10px; height:10px; border-radius:3px; flex:none; }
        .lg .cnt { color:var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .urgent { display:flex; flex-direction:column; gap:6px; }
        .bottle { display:flex; align-items:center; gap:8px; font-size:0.92em; }
        .bottle ha-icon { --mdc-icon-size:18px; color:var(--secondary-text-color); flex:none; }
        .bname { color:var(--primary-text-color); overflow:hidden; text-overflow:ellipsis;
                 white-space:nowrap; flex:1; min-width:0; }
        .vintage { color:var(--secondary-text-color); flex:none; }
        .status { font-size:0.78em; border-radius:10px; padding:1px 8px; flex:none; }
        .banner { display:flex; align-items:center; gap:8px; margin-top:12px;
                  background:color-mix(in srgb, ${WARN} 12%, transparent); color:${ink(WARN)};
                  border-radius:8px; padding:8px 12px; font-size:0.9em; }
        .banner ha-icon { --mdc-icon-size:18px; }
        .section-label:not(:first-child) { margin-top:16px; }
        [data-entity]:focus-visible, .consume:focus-visible, a:focus-visible {
          outline:2px solid var(--primary-color); outline-offset:2px; }
      </style>
      <ha-card>
        <div class="head">
          ${link
            ? `<a class="title" href="${esc(link)}" target="_blank"
                  rel="noopener" title="Open Cellarion">${esc(this._config.title)}<span class="ext" aria-hidden="true">↗</span></a>`
            : `<div class="title">${esc(this._config.title)}</div>`}
          ${health != null && this._show("show_health") ? `
            <div class="health" data-entity="${esc(this._entityId("_collection_health_score"))}"
                 aria-label="Health score ${health}${grade ? `, grade ${esc(grade)}` : ""}">
              <span>${health}</span>
              ${grade ? `<span class="grade">${esc(grade)}</span>` : ""}
            </div>` : ""}
        </div>
        ${statsHtml}
        ${windowHtml}
        ${readyHtml}
        ${urgentHtml}
        ${serviceHtml}
      </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      // Make the click-to-open targets reachable by keyboard/screen readers.
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      const open = () => this._moreInfo(el.dataset.entity);
      el.addEventListener("click", open);
      el.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          open();
        }
      });
    });
    this.shadowRoot.querySelectorAll("button.consume").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (el.disabled) return;
        if (!window.confirm(`Mark "${el.dataset.name}" as drunk?`)) return;
        const data = { bottle_id: el.dataset.bottle };
        // Needed when more than one Cellarion account is configured;
        // harmless (ignored) for a single account.
        if (this._config.entry_id) data.entry_id = this._config.entry_id;
        // One tap, one request: stay disabled until the coordinator's
        // refresh removes the bottle from the list. HA shows its own toast
        // if the call fails; the button just becomes tappable again.
        el.disabled = true;
        el.setAttribute("aria-busy", "true");
        Promise.resolve(this._hass.callService("cellarion", "consume_bottle", data))
          .then(() => {
            el.classList.add("done");
            el.setAttribute("aria-label", `${el.dataset.name} marked as drunk`);
          }, () => {
            el.disabled = false;
            el.removeAttribute("aria-busy");
          });
      });
    });
    if (focusKey) this.shadowRoot.querySelector(focusKey)?.focus?.();
  }
}

class CellarionCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  // Give the form an explicit boolean per toggle that mirrors what the card
  // does with the config (an unset one would otherwise render as an
  // unticked box on a card that does show that piece)...
  _formData() {
    return {
      ...this._config,
      ...Object.fromEntries(
        TOGGLES.map((t) => [t.name, isShown(this._config, t.name)]),
      ),
    };
  }

  // ...and drop the ones left on again on the way out, so a config only
  // ever carries the pieces the user actually turned off. Cleared text
  // fields go too: an empty prefix or url would otherwise be stored and
  // then break the defaults.
  static _prune(value) {
    const config = { ...value };
    for (const t of TOGGLES) {
      if (isShown(config, t.name)) delete config[t.name];
    }
    for (const key of ["title", "prefix", "url", "entry_id"]) {
      if (typeof config[key] === "string" && !config[key].trim()) delete config[key];
    }
    return config;
  }

  _render() {
    if (this._form) {
      this._form.data = this._formData();
      return;
    }
    this._form = document.createElement("ha-form");
    this._form.hass = this._hass;
    this._form.data = this._formData();
    this._form.schema = [
      { name: "title", selector: { text: {} } },
      { name: "entry_id", selector: { config_entry: { integration: "cellarion" } } },
      { name: "url", selector: { text: {} } },
      {
        name: "",
        type: "expandable",
        title: "Show on the card",
        icon: "mdi:eye-outline",
        schema: TOGGLES.map((t) => ({
          name: t.name, selector: { boolean: {} },
        })),
      },
      {
        name: "",
        type: "expandable",
        title: "Advanced",
        icon: "mdi:tune",
        schema: [{ name: "prefix", selector: { text: {} } }],
      },
    ];
    this._form.computeLabel = (s) => ({
      title: "Title",
      prefix: "Entity id prefix (leave empty to detect automatically)",
      url: "Link URL (optional)",
      entry_id: "Account (only if you have several)",
      ...Object.fromEntries(TOGGLES.map((t) => [t.name, t.label])),
    }[s.name] || s.name);
    this._form.addEventListener("value-changed", (ev) => {
      this._config = CellarionCardEditor._prune(ev.detail.value);
      this.dispatchEvent(new CustomEvent("config-changed", {
        bubbles: true, composed: true, detail: { config: this._config },
      }));
    });
    this.appendChild(this._form);
  }
}

customElements.define("cellarion-card", CellarionCard);
customElements.define("cellarion-card-editor", CellarionCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "cellarion-card",
  name: "Cellarion Card",
  description: "Wine cellar overview: collection stats, drink windows, and bottles that need attention.",
  preview: true,
});
