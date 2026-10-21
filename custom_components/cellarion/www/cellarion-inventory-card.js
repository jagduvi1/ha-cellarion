/**
 * Cellarion Inventory Card — find a bottle, see where it is, take it out.
 *
 * Reads the whole inventory through the response-only action
 * `cellarion.get_bottles` (database reads only, nothing that costs AI
 * money), then searches and filters it in the browser. No build step;
 * ships as a single file with the integration.
 *
 * Config:
 *   type: custom:cellarion-inventory-card
 *   title: Cellar inventory      (optional)
 *   entry_id: <config entry>     (optional — only needed when more than one
 *                                 Cellarion account is configured)
 *   max_results: 30              (optional — wines listed before "Show more")
 *   show_value: false            (optional — each show_* flag in TOGGLES
 *                                 below defaults to true; false hides prices,
 *                                 notes, personal fields, label images or the
 *                                 take-out buttons)
 *
 * Refresh: fetched once when the card is first shown, again (debounced,
 * at most every 30 s) when a Cellarion entity of the same account changes,
 * and when the page becomes visible after a minute or more in the
 * background. The integration caches the inventory, so a refetch is cheap.
 *
 * Theming: the same tokens as the Cellarion card —
 *   --cellarion-not-ready-color, --cellarion-early-color,
 *   --cellarion-peak-color, --cellarion-declining-color,
 *   --cellarion-late-color, --cellarion-good-color, --cellarion-bad-color
 */

const CARD_TAG = "cellarion-inventory-card";
const EDITOR_TAG = "cellarion-inventory-card-editor";
const DEFAULT_TITLE = "Cellar inventory";
const DEFAULT_MAX = 30;

const DEBOUNCE_MS = 2000;       // quiet period after an entity change
const MAX_DEFER_MS = 10000;     // a chatty sensor cannot postpone it longer
const MIN_INTERVAL_MS = 30000;  // never refetch more often than this
const STALE_MS = 60000;         // refetch on return after this long away
const TAKEN_TTL_MS = 300000;    // hide a taken-out bottle until data catches up
const IMAGE_CONCURRENCY = 4;
const IMAGE_CACHE_MAX = 200;

// The only image paths the integration's image view serves (mirrors
// pycellarion's IMAGE_PATH_RE), so the card never asks for anything else.
const THUMB_RE = /^\/api\/uploads\/thumbs\/(processed|originals)\/([A-Za-z0-9_-]+\.(?:png|jpe?g|webp)\.webp)$/;

// Drink-window states in chip order. "none" stands for a null maturity
// (no window known), the same word the Cellarion API uses for it.
const MATURITY = [
  { key: "not-ready", label: "Not ready", color: "var(--cellarion-not-ready-color, #2563EB)" },
  { key: "early", label: "Early", color: "var(--cellarion-early-color, #0891B2)" },
  { key: "peak", label: "At peak", color: "var(--cellarion-peak-color, #059669)" },
  { key: "late", label: "Late", color: "var(--cellarion-late-color, #DC2626)" },
  { key: "declining", label: "Declining", color: "var(--cellarion-declining-color, #D97706)" },
  { key: "none", label: "No window", color: "var(--secondary-text-color)" },
];
const MATURITY_BY_KEY = Object.fromEntries(MATURITY.map((m) => [m.key, m]));
// Which state a wine's pill shows when its bottles differ: the most urgent.
const URGENCY = ["declining", "late", "peak", "early", "not-ready", "none"];
const [NOT_READY, EARLY, PEAK, LATE, DECLINING] = MATURITY.map((m) => m.color);

const TYPES = {
  red: "Red", white: "White", "rosé": "Rosé", sparkling: "Sparkling",
  dessert: "Dessert", fortified: "Fortified",
};

// Fills keep the palette; text is mixed towards the theme's text colour so
// small labels reach AA contrast on light and dark themes alike.
const GOOD = "var(--cellarion-good-color, #059669)";
const BAD = "var(--cellarion-bad-color, #DC2626)";
const ink = (color) => `color-mix(in srgb, ${color} 72%, var(--primary-text-color))`;

// Pieces that can be switched off. Only an explicit false hides one.
const TOGGLES = [
  { name: "show_images", label: "Label images" },
  { name: "show_value", label: "Prices" },
  { name: "show_notes", label: "Notes" },
  { name: "show_personal", label: "Personal fields" },
  { name: "show_consume", label: "Take-out buttons" },
];
const isShown = (config, name) => config?.[name] !== false;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Case- and accent-insensitive form for matching: "Château" → "chateau".
const fold = (s) => String(s ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

const typeLabel = (t) => (!t ? "" : Object.hasOwn(TYPES, t) ? TYPES[t]
  : String(t).charAt(0).toUpperCase() + String(t).slice(1));

/**
 * Where a bottle is, as one line: "Left cabinet · shelf 2 · row 1 · slot 3"
 * for a cabinet, "Grid · row 2 · col 4" for a grid rack, "Rack · position
 * 14" for anything else; the free-text place when it is in no rack; else
 * "Not placed".
 */
function placeText(b) {
  const loc = b?.location;
  if (loc && (loc.rack || loc.rackName)) {
    const parts = [loc.rackName || "Rack"];
    if (loc.shelf != null) {
      parts.push(`shelf ${loc.shelf}`);
      if (loc.row != null) parts.push(`row ${loc.row}`);
      if (loc.col != null) parts.push(`slot ${loc.col}`);
    } else if (loc.row != null || loc.col != null) {
      if (loc.row != null) parts.push(`row ${loc.row}`);
      if (loc.col != null) parts.push(`col ${loc.col}`);
    } else if (loc.position != null) {
      parts.push(`position ${loc.position}`);
    }
    return parts.join(" · ");
  }
  if (typeof b?.place === "string" && b.place.trim()) return b.place.trim();
  return "Not placed";
}

const personalValue = (v) => (v === true ? "yes" : v === false ? "no" : v == null ? "" : String(v));

function errorText(err) {
  const text = typeof err === "string" ? err
    : err?.message || err?.error?.message || err?.code || "Unknown error";
  // HA prefixes a ServiceValidationError's message; the card's own heading
  // already says what failed.
  return String(text).replace(/^Validation error:\s*/i, "");
}

/**
 * The entity ids of one config entry, through the entity and device
 * registries (an entity knows its device, a device its config entries).
 */
function entryEntityIds(hass, entryId) {
  if (!entryId || !hass?.entities || !hass?.devices) return [];
  const ids = [];
  for (const e of Object.values(hass.entities)) {
    if (e?.platform !== "cellarion" || !e.device_id) continue;
    if (hass.devices[e.device_id]?.config_entries?.includes(entryId)) ids.push(e.entity_id);
  }
  return ids.sort();
}

/** Flatten the get_bottles response into what search and grouping need. */
function buildIndex(data) {
  const wines = data.wines && typeof data.wines === "object" ? data.wines : {};
  const cellars = Array.isArray(data.cellars) ? data.cellars.filter((c) => c?.id != null) : [];
  const cellarName = new Map(cellars.map((c) => [c.id, c.name || "Cellar"]));
  const cellarRole = new Map(cellars.map((c) => [c.id, c.role]));
  const bottles = [];
  const byId = new Map();
  const types = new Set();
  const maturities = new Set();
  for (const raw of data.bottles) {
    if (!raw || typeof raw !== "object" || raw.id == null) continue;
    const wine = raw.wine != null && Object.hasOwn(wines, raw.wine) ? wines[raw.wine] || null : null;
    const pending = raw.pending && typeof raw.pending === "object" ? raw.pending : null;
    const name = wine?.name || pending?.name || "Unidentified wine";
    const producer = wine?.producer || pending?.producer || "";
    const maturity = typeof raw.maturity === "string" && Object.hasOwn(MATURITY_BY_KEY, raw.maturity)
      ? raw.maturity : "none";
    const grapes = Array.isArray(wine?.grapes) ? wine.grapes : [];
    const personal = Array.isArray(raw.personal) ? raw.personal.filter((p) => p && p.key) : [];
    const hay = fold([
      wine?.name, wine?.producer, wine?.region, wine?.country, wine?.appellation,
      ...grapes, raw.vintage, raw.location?.rackName, raw.place, pending?.name, pending?.producer,
    ].filter((v) => v != null && v !== "").join(" "));
    const type = wine?.type || null;
    // Same wine + vintage = one row; wine-less bottles group by their
    // pending name and producer instead.
    const ident = raw.wine != null ? `w:${raw.wine}`
      : `p:${JSON.stringify([fold(pending?.name), fold(pending?.producer)])}`;
    const b = {
      raw, wine, name, producer, type, maturity, personal,
      place: placeText(raw), hay,
      personalHay: fold(personal.map((p) => personalValue(p.value)).join(" ")),
      key: `${ident}|${raw.vintage ?? ""}`,
    };
    bottles.push(b);
    byId.set(String(raw.id), b);
    if (type) types.add(type);
    maturities.add(maturity);
  }
  const typeOrder = Object.keys(TYPES);
  const rank = (t) => (typeOrder.includes(t) ? typeOrder.indexOf(t) : typeOrder.length);
  return {
    data, bottles, byId, cellars, cellarName, cellarRole,
    types: [...types].sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : 1)),
    maturities: MATURITY.map((m) => m.key).filter((k) => maturities.has(k)),
  };
}

const vintageNum = (v) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : Infinity; };
const windowSig = (w) => (w ? [w.drinkFrom, w.peakFrom, w.peakUntil, w.drinkTo].join(",") : "");

const STYLES = `
  :host { display:block; }
  ha-card { padding:16px; overflow:hidden; }
  [hidden] { display:none !important; }
  .sr-only { position:absolute; width:1px; height:1px; margin:-1px; padding:0; overflow:hidden;
    clip:rect(0 0 0 0); white-space:nowrap; border:0; }
  .head { display:flex; align-items:center; margin-bottom:12px; }
  .title { font-size:1.1em; font-weight:500; color:var(--primary-text-color); text-decoration:none;
    overflow-wrap:anywhere; }
  a.title:hover { text-decoration:underline; }
  .ext { font-size:.75em; color:var(--secondary-text-color); margin-left:4px; vertical-align:super; }
  .search-wrap { position:relative; }
  .search-wrap ha-icon { position:absolute; left:10px; top:50%; transform:translateY(-50%);
    --mdc-icon-size:20px; color:var(--secondary-text-color); pointer-events:none; }
  .search { width:100%; box-sizing:border-box; min-height:44px; padding:10px 12px 10px 38px;
    font:inherit; color:var(--primary-text-color); background:var(--card-background-color, transparent);
    border:1px solid var(--divider-color, rgba(127,127,127,.35)); border-radius:8px; }
  .filters { display:flex; flex-direction:column; gap:8px; margin-top:10px; }
  .chips, .tools { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .chip, .sort button { font:inherit; font-size:.85em; min-height:32px; padding:4px 12px; cursor:pointer;
    display:inline-flex; align-items:center; gap:6px; color:var(--primary-text-color); background:none;
    border:1px solid var(--divider-color, rgba(127,127,127,.35)); border-radius:16px; }
  .chip[aria-pressed="true"], .sort button[aria-pressed="true"] { border-color:var(--primary-color);
    background:color-mix(in srgb, var(--primary-color) 14%, transparent); font-weight:500; }
  .dot { width:8px; height:8px; border-radius:50%; flex:none; background:var(--c); }
  select.cellar { font:inherit; font-size:.85em; min-height:32px; max-width:100%; padding:4px 8px;
    color:var(--primary-text-color); background:var(--card-background-color, transparent);
    border:1px solid var(--divider-color, rgba(127,127,127,.35)); border-radius:16px; }
  .sort { display:inline-flex; gap:4px; margin-left:auto; align-items:center; }
  .sort .lbl { font-size:.8em; color:var(--secondary-text-color); }
  .summary { font-size:.8em; color:var(--secondary-text-color); margin:10px 0 4px; }
  .msg { color:var(--secondary-text-color); padding:12px 0; }
  .error { display:flex; flex-wrap:wrap; align-items:center; gap:8px 10px; margin:8px 0; padding:10px 12px;
    border-radius:8px; background:color-mix(in srgb, ${BAD} 12%, transparent); color:${ink(BAD)}; }
  .error .etxt { flex:1; min-width:12em; overflow-wrap:anywhere; }
  .error .etitle { font-weight:500; }
  .btn { font:inherit; font-size:.9em; min-height:36px; padding:6px 14px; cursor:pointer;
    color:var(--primary-color); background:none; border:1px solid currentColor; border-radius:18px; }
  .list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:2px; }
  .row { font:inherit; color:inherit; text-align:left; width:100%; box-sizing:border-box; min-height:56px;
    display:flex; align-items:center; gap:12px; padding:8px; cursor:pointer; background:none; border:0;
    border-radius:8px; }
  .row:hover { background:var(--secondary-background-color, rgba(127,127,127,.08)); }
  .thumb { flex:none; width:40px; height:56px; border-radius:4px; overflow:hidden; display:flex;
    align-items:center; justify-content:center; color:var(--secondary-text-color);
    background:var(--secondary-background-color, rgba(127,127,127,.12)); }
  .thumb.big { width:72px; height:100px; }
  .thumb img { width:100%; height:100%; object-fit:cover; display:block; }
  .main { flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .name { font-weight:500; color:var(--primary-text-color); overflow-wrap:anywhere; }
  .meta, .where { font-size:.85em; color:var(--secondary-text-color); overflow-wrap:anywhere; }
  .more-places { white-space:nowrap; }
  .side { flex:none; display:flex; flex-direction:column; align-items:flex-end; gap:4px; font-size:.8em; }
  .count { color:var(--secondary-text-color); white-space:nowrap; }
  .pill { display:inline-flex; align-items:center; gap:5px; padding:1px 8px; border-radius:10px;
    white-space:nowrap; background:color-mix(in srgb, var(--c) 12%, transparent);
    color:color-mix(in srgb, var(--c) 72%, var(--primary-text-color)); }
  .more { margin-top:8px; width:100%; }
  .back { font:inherit; display:inline-flex; align-items:center; gap:4px; min-height:36px;
    padding:4px 8px 4px 0; margin-bottom:8px; cursor:pointer; color:var(--primary-color);
    background:none; border:0; }
  .back ha-icon { --mdc-icon-size:20px; }
  .d-top { display:flex; gap:14px; align-items:flex-start; }
  .d-title { flex:1; min-width:0; display:flex; flex-direction:column; gap:4px; align-items:flex-start; }
  .d-name { margin:0; font-size:1.25em; font-weight:500; color:var(--primary-text-color);
    overflow-wrap:anywhere; }
  .d-producer { color:var(--secondary-text-color); overflow-wrap:anywhere; }
  .facts { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:3px 12px; margin:12px 0 0;
    font-size:.9em; }
  .facts dt { color:var(--secondary-text-color); }
  .facts dd { margin:0; overflow-wrap:anywhere; }
  .pending { font-size:.85em; color:var(--secondary-text-color); margin:8px 0 0; }
  h3 { font-size:.78em; font-weight:500; color:var(--secondary-text-color); text-transform:uppercase;
    letter-spacing:.05em; margin:18px 0 6px; }
  .bottles { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
  .bottle { border:1px solid var(--divider-color, rgba(127,127,127,.25)); border-radius:8px; padding:10px 12px; }
  .b-where { display:flex; gap:6px; align-items:flex-start; font-weight:500; overflow-wrap:anywhere; }
  .b-where ha-icon { --mdc-icon-size:18px; flex:none; color:var(--secondary-text-color); }
  .b-sub { font-size:.85em; color:var(--secondary-text-color); margin:2px 0 0 24px; overflow-wrap:anywhere; }
  .b-meta { display:flex; flex-wrap:wrap; gap:2px 14px; margin-top:6px; font-size:.88em; }
  .k { color:var(--secondary-text-color); }
  .notes { white-space:pre-line; overflow-wrap:anywhere; margin:8px 0 0; font-size:.9em; }
  .personal { list-style:none; margin:6px 0 0; padding:0; font-size:.88em; display:flex;
    flex-direction:column; gap:2px; overflow-wrap:anywhere; }
  .lvl { font-size:.78em; color:var(--secondary-text-color); margin-left:6px; padding:0 6px;
    border:1px solid var(--divider-color, rgba(127,127,127,.35)); border-radius:8px; }
  .b-actions { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; justify-content:flex-end;
    margin-top:10px; }
  .link { color:var(--primary-color); font-size:.9em; text-decoration:none; }
  .link:hover { text-decoration:underline; }
  .take { font:inherit; font-size:.9em; min-height:36px; padding:6px 16px; cursor:pointer; background:none;
    color:${ink(GOOD)}; border:1px solid ${ink(GOOD)}; border-radius:18px; }
  .take:hover { background:color-mix(in srgb, ${GOOD} 12%, transparent); }
  .take[aria-disabled="true"] { opacity:.5; cursor:progress; }
  .take.go { font-weight:600; background:color-mix(in srgb, ${GOOD} 16%, transparent); }
  .ask { flex:1; display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:8px; }
  .ask .q { flex:1 1 12em; font-size:.9em; overflow-wrap:anywhere; }
  .b-err { margin-top:6px; font-size:.85em; color:${ink(BAD)}; overflow-wrap:anywhere; }
  .win { margin-top:8px; }
  .win.none { font-size:.85em; color:var(--secondary-text-color); }
  .track-wrap { position:relative; padding:3px 0; }
  .track { position:relative; height:10px; border-radius:5px; overflow:hidden;
    background:var(--secondary-background-color, rgba(127,127,127,.15)); }
  .track span { position:absolute; top:0; bottom:0; }
  .now { position:absolute; top:0; bottom:0; width:2px; margin-left:-1px; border-radius:1px;
    background:var(--primary-text-color); }
  .years { display:flex; flex-wrap:wrap; justify-content:space-between; gap:2px 12px; margin-top:4px;
    font-size:.8em; color:var(--secondary-text-color); }
  .years .now-lbl { color:var(--primary-text-color); }
  button:focus-visible, a:focus-visible, select:focus-visible, .search:focus-visible {
    outline:2px solid var(--primary-color); outline-offset:2px; }
  /* Focused by script only (tabindex -1) so screen readers land on it: no box. */
  .d-name:focus, [tabindex="-1"]:focus { outline:none; }
`;

class CellarionInventoryCard extends HTMLElement {
  constructor() {
    super();
    // UI state: lives on the element, so it survives every re-render.
    this._query = "";
    this._types = new Set();
    this._maturities = new Set();
    this._cellar = "";
    this._sort = "name";
    this._limit = DEFAULT_MAX;
    this._openKey = null;
    this._returnKey = null;
    // Data and fetch bookkeeping.
    this._index = null;
    this._version = 0;
    this._error = null;
    this._loading = false;
    this._fetchGen = 0;
    this._lastFetch = null;
    this._timer = null;
    this._stale = false;
    this._watch = null;
    this._taken = new Map();   // bottle id → time it was taken out here
    this._takenVersion = 0;
    this._askId = null;        // bottle whose inline take-out prompt is open
    this._busy = new Set();    // bottle ids whose consume call is in flight
    this._takeError = null;
    // Label images: url → { src } | { pending } | { failed }.
    this._images = new Map();
    this._imgQueue = [];
    this._imgActive = 0;
    this._imgGen = 0;
    this._onVisible = () => this._visibilityChanged();
  }

  static getStubConfig() {
    return { title: DEFAULT_TITLE };
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  setConfig(config) {
    if (config?.max_results != null && !(Number(config.max_results) > 0)) {
      throw new Error("cellarion-inventory-card: max_results must be a positive number");
    }
    const entryChanged = this._config && this._config.entry_id !== config?.entry_id;
    this._config = { title: DEFAULT_TITLE, ...config };
    this._limit = this._max();
    this._viewCache = null;
    if (entryChanged) {
      // Another account: forget the old one's data and fetch afresh.
      this._fetchGen++;
      this._loading = false;
      this._index = null;
      this._error = null;
      this._lastFetch = null;
      this._watch = null;
      this._openKey = null;
      this._clearImages();
    }
    if (this._el) this._renderAll();
    this._maybeStart();
  }

  set hass(hass) {
    const lang = hass?.locale?.language || "en";
    const langChanged = this._hass && lang !== this._lang();
    this._hass = hass;
    if (!this._config) return;
    this._ensureShell();
    if (langChanged && this._index) this._renderAll();
    this._maybeStart();
    this._checkEntities();
  }

  connectedCallback() {
    this._connected = true;
    document.addEventListener("visibilitychange", this._onVisible);
    if (!this._config) return;
    this._ensureShell();
    if (this._lastFetch != null && Date.now() - this._lastFetch >= STALE_MS) this._fetch();
    else if (this._index) this._renderAll();  // re-observe the label thumbs
    this._maybeStart();
  }

  disconnectedCallback() {
    this._connected = false;
    document.removeEventListener("visibilitychange", this._onVisible);
    clearTimeout(this._timer);
    this._timer = null;
    this._clearImages();
  }

  // Sections dashboards: full width by default, content-sized height.
  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  getCardSize() {
    return 8;
  }

  _show(name) {
    return isShown(this._config, name);
  }

  _max() {
    const n = Math.floor(Number(this._config?.max_results) || DEFAULT_MAX);
    return Math.min(500, Math.max(1, n));
  }

  _lang() {
    return this._hass?.locale?.language || "en";
  }

  _collator() {
    const lang = this._lang();
    if (this._coll?.lang !== lang) {
      let c;
      try { c = new Intl.Collator(lang, { sensitivity: "base", numeric: true }); } catch (e) {
        c = new Intl.Collator("en", { sensitivity: "base", numeric: true });
      }
      this._coll = { lang, c };
    }
    return this._coll.c;
  }

  // ── Fetching and refresh ────────────────────────────────────────────

  _maybeStart() {
    if (this._connected && this._hass && this._config && this._lastFetch == null && !this._loading) {
      this._fetch();
    }
  }

  async _fetch() {
    if (!this._hass || !this._config || typeof this._hass.callWS !== "function") return;
    clearTimeout(this._timer);
    this._timer = null;
    if (this._loading) { this._again = true; return; }
    this._loading = true;
    this._lastFetch = Date.now();
    const gen = this._fetchGen;
    const entryId = this._config.entry_id;
    if (!this._index) this._renderResults();
    let index = null;
    let error = null;
    try {
      const res = await this._hass.callWS({
        type: "call_service", domain: "cellarion", service: "get_bottles",
        service_data: entryId ? { entry_id: entryId } : {},
        return_response: true,
      });
      const data = res?.response;
      if (!data || !Array.isArray(data.bottles)) throw new Error("Cellarion sent no inventory.");
      index = buildIndex(data);
    } catch (err) {
      error = errorText(err);
    }
    if (gen !== this._fetchGen) return;  // the account changed meanwhile
    this._loading = false;
    if (index) { this._index = index; this._version++; }
    this._error = error;
    this._pruneState();
    this._renderAll();
    this._checkEntities();
    if (this._again) { this._again = false; this._requestRefresh(); }
  }

  // Refetch when any entity of the account changes. Registry walks only
  // happen when HA replaces its registry objects; per update it is one
  // string over the account's few entities.
  _checkEntities() {
    const hass = this._hass;
    const entryId = this._index?.data?.entry_id || this._config?.entry_id;
    if (!hass || !entryId) return;
    const w = this._watch;
    if (!w || w.entities !== hass.entities || w.devices !== hass.devices || w.entryId !== entryId) {
      const ids = entryEntityIds(hass, entryId);
      const same = w && w.entryId === entryId && w.ids.join() === ids.join();
      this._watch = { entities: hass.entities, devices: hass.devices, entryId, ids, fp: same ? w.fp : null };
    }
    const fp = this._watch.ids.map((id) => hass.states?.[id]?.last_updated || "").join("|");
    if (this._watch.fp == null) { this._watch.fp = fp; return; }
    if (fp !== this._watch.fp) {
      this._watch.fp = fp;
      this._requestRefresh();
    }
  }

  _requestRefresh() {
    if (!this._connected) return;
    if (document.visibilityState === "hidden") { this._stale = true; return; }
    const now = Date.now();
    if (this._timer) {
      // Debounce: a change in the quiet period pushes the refetch back,
      // but no more than MAX_DEFER_MS past the first change.
      if (now - this._changeSince >= MAX_DEFER_MS) return;
      clearTimeout(this._timer);
    } else {
      this._changeSince = now;
    }
    const due = Math.max(now + DEBOUNCE_MS, (this._lastFetch ?? -Infinity) + MIN_INTERVAL_MS);
    this._timer = setTimeout(() => {
      this._timer = null;
      if (document.visibilityState === "hidden") { this._stale = true; return; }
      this._fetch();
    }, due - now);
  }

  _visibilityChanged() {
    if (document.visibilityState !== "visible" || !this._connected || this._lastFetch == null) return;
    const stale = this._stale;
    this._stale = false;
    if (Date.now() - this._lastFetch >= STALE_MS) this._fetch();
    else if (stale) this._requestRefresh();
  }

  // Drop filter values the new data no longer has (an invisible active
  // chip would hide everything), and forget taken-out bottles once the
  // server agrees they are gone.
  _pruneState() {
    const idx = this._index;
    if (!idx) return;
    for (const t of this._types) if (!idx.types.includes(t)) this._types.delete(t);
    for (const m of this._maturities) if (!idx.maturities.includes(m)) this._maturities.delete(m);
    if (this._cellar && !idx.cellarName.has(this._cellar)) this._cellar = "";
    if (this._askId && !idx.byId.has(this._askId)) this._askId = null;
    const now = Date.now();
    for (const [id, at] of this._taken) {
      if (!idx.byId.has(id) || now - at > TAKEN_TTL_MS) { this._taken.delete(id); this._takenVersion++; }
    }
    this._viewCache = null;
  }

  // ── Filtering and grouping ──────────────────────────────────────────

  _view() {
    const sig = [this._version, this._query, [...this._types].join(), [...this._maturities].join(),
      this._cellar, this._sort, this._takenVersion, this._show("show_personal"), this._lang()].join("|");
    if (this._viewCache?.sig === sig) return this._viewCache.groups;
    const idx = this._index;
    const terms = fold(this._query).split(/\s+/).filter(Boolean);
    const personal = this._show("show_personal");
    const map = new Map();
    for (const b of idx?.bottles || []) {
      if (this._taken.has(String(b.raw.id))) continue;
      if (this._types.size && !this._types.has(b.type)) continue;
      if (this._maturities.size && !this._maturities.has(b.maturity)) continue;
      if (this._cellar && b.raw.cellar !== this._cellar) continue;
      if (terms.length) {
        const hay = personal ? `${b.hay} ${b.personalHay}` : b.hay;
        if (!terms.every((t) => hay.includes(t))) continue;
      }
      let g = map.get(b.key);
      if (!g) {
        g = { key: b.key, name: b.name, producer: b.producer, wine: b.wine, vintage: b.raw.vintage, bottles: [] };
        map.set(b.key, g);
      }
      g.bottles.push(b);
    }
    const coll = this._collator();
    const rank = (b) => (b.raw.location ? 0 : b.raw.place ? 1 : 2);
    const groups = [...map.values()];
    for (const g of groups) {
      g.bottles.sort((a, b) => rank(a) - rank(b) || coll.compare(a.place, b.place));
      g.sizes = [...new Set(g.bottles.map((b) => b.raw.size).filter(Boolean))];
      g.places = new Set(g.bottles.map((b) => b.place)).size;
      const ends = g.bottles.map((b) => num(b.raw.window?.drinkTo)).filter((v) => v != null);
      g.drinkBy = ends.length ? Math.min(...ends) : null;
      g.maturity = URGENCY.find((k) => g.bottles.some((b) => b.maturity === k)) || "none";
      const own = g.bottles.find((b) => THUMB_RE.test(b.raw.image || ""));
      g.image = own ? own.raw.image : THUMB_RE.test(g.wine?.image || "") ? g.wine.image : null;
    }
    const byName = (a, b) => coll.compare(a.name, b.name)
      || (vintageNum(a.vintage) - vintageNum(b.vintage) || 0)
      || coll.compare(a.producer, b.producer) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
    groups.sort(this._sort === "drink"
      ? (a, b) => ((a.drinkBy ?? Infinity) - (b.drinkBy ?? Infinity) || 0) || byName(a, b)
      : byName);
    this._viewCache = { sig, groups };
    return groups;
  }

  _instanceUrl() {
    const raw = this._index?.data?.instance_url;
    // Only ever link to an http(s) target, never javascript:/data:.
    return typeof raw === "string" && /^https?:\/\/[^\s"'<>]+$/i.test(raw) ? raw.replace(/\/+$/, "") : null;
  }

  _entryId() {
    return this._index?.data?.entry_id || this._config?.entry_id || null;
  }

  _money(n, currency) {
    const lang = this._lang();
    const digits = Number.isInteger(n) ? 0 : 2;
    try {
      return new Intl.NumberFormat(lang, currency
        ? { style: "currency", currency, minimumFractionDigits: digits, maximumFractionDigits: digits }
        : { maximumFractionDigits: 2 }).format(n);
    } catch (e) {
      return currency ? `${n} ${currency}` : String(n);
    }
  }

  // ── Rendering ───────────────────────────────────────────────────────

  // The shell (search box included) is built once; afterwards only the
  // regions inside it are replaced, so typing, caret and focus survive.
  _ensureShell() {
    if (this._el) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="head"></div>
        <div class="sr-only announce" role="status" aria-live="polite"></div>
        <div class="list-view">
          <div class="search-wrap">
            <ha-icon icon="mdi:magnify" aria-hidden="true"></ha-icon>
            <input class="search" type="search" aria-label="Search bottles" autocomplete="off"
                   spellcheck="false" enterkeyhint="search"
                   placeholder="Search wine, producer, grape, place…">
          </div>
          <div class="filters"></div>
          <div class="summary" aria-live="polite"></div>
          <div class="notice"></div>
          <ul class="list" aria-label="Wines"></ul>
          <div class="more-wrap"></div>
        </div>
        <div class="detail" role="region" aria-label="Wine details" hidden></div>
      </ha-card>`;
    const q = (s) => this.shadowRoot.querySelector(s);
    this._el = {
      card: q("ha-card"), head: q(".head"), announce: q(".announce"), list: q(".list-view"),
      search: q(".search"), filters: q(".filters"), summary: q(".summary"), notice: q(".notice"),
      results: q(".list"), more: q(".more-wrap"), detail: q(".detail"),
    };
    // One delegated listener per event type: regions can be re-rendered
    // freely without re-wiring anything.
    this._el.card.addEventListener("click", (ev) => this._onClick(ev));
    this._el.card.addEventListener("change", (ev) => this._onChange(ev));
    this._el.card.addEventListener("keydown", (ev) => this._onKeydown(ev));
    this._el.search.addEventListener("input", (ev) => this._setQuery(ev.target.value));
    this._renderAll();
  }

  // Replace a region's content, keeping focus on the element with the same
  // data-key when there is one again (else on `fallback`, if focus was here).
  _swap(el, html, fallback) {
    const inside = this._focusInside(el);
    const key = inside ? this.shadowRoot.activeElement?.dataset?.key : null;
    el.innerHTML = html;
    if (!inside) return;
    const again = key && [...el.querySelectorAll("[data-key]")].find((n) => n.dataset.key === key);
    const target = again || (fallback && this.shadowRoot.querySelector(fallback));
    target?.focus?.();
  }

  _focusInside(el) {
    for (let n = this.shadowRoot.activeElement; n; n = n.parentNode) if (n === el) return true;
    return false;
  }

  _renderAll() {
    if (!this._el) return;
    this._renderHead();
    this._renderFilters();
    this._renderResults();
    this._renderDetail();
  }

  _renderHead() {
    const title = this._config.title;
    const link = this._instanceUrl();
    this._el.head.hidden = !title;
    this._swap(this._el.head, !title ? "" : link
      ? `<a class="title" href="${esc(link)}" target="_blank" rel="noopener noreferrer" data-key="title"
            title="Open Cellarion">${esc(title)}<span class="ext" aria-hidden="true">↗</span></a>`
      : `<div class="title">${esc(title)}</div>`);
  }

  _renderFilters() {
    const idx = this._index;
    const el = this._el.filters;
    if (!idx || !idx.bottles.length) { el.hidden = true; this._swap(el, ""); return; }
    el.hidden = false;
    const chip = (action, value, label, pressed, color) => `
      <button type="button" class="chip" data-action="${action}" data-value="${esc(value)}"
              data-key="${esc(`${action}:${value}`)}" aria-pressed="${pressed ? "true" : "false"}">${
        color ? `<span class="dot" style="--c:${color}" aria-hidden="true"></span>` : ""}${esc(label)}</button>`;
    const types = idx.types.length > 1 ? `
      <div class="chips" role="group" aria-label="Wine type">
        ${idx.types.map((t) => chip("type", t, typeLabel(t), this._types.has(t))).join("")}
      </div>` : "";
    const maturity = idx.maturities.length > 1 ? `
      <div class="chips" role="group" aria-label="Drink window">
        ${idx.maturities.map((k) => chip("maturity", k, MATURITY_BY_KEY[k].label,
          this._maturities.has(k), MATURITY_BY_KEY[k].color)).join("")}
      </div>` : "";
    const cellars = idx.cellars.length > 1 ? `
      <select class="cellar" data-action="cellar" data-key="cellar" aria-label="Cellar">
        <option value=""${this._cellar ? "" : " selected"}>All cellars</option>
        ${idx.cellars.map((c) => `<option value="${esc(c.id)}"${c.id === this._cellar ? " selected" : ""}>${
          esc(c.name || "Cellar")}</option>`).join("")}
      </select>` : "";
    const sortBtn = (value, label) => `<button type="button" data-action="sort" data-value="${value}"
      data-key="sort:${value}" aria-pressed="${this._sort === value ? "true" : "false"}">${label}</button>`;
    this._swap(el, `${types}${maturity}
      <div class="tools">
        ${cellars}
        <div class="sort" role="group" aria-label="Sort by">
          <span class="lbl" aria-hidden="true">Sort</span>${sortBtn("name", "Name")}${sortBtn("drink", "Drink by")}
        </div>
      </div>`);
  }

  _renderResults() {
    const el = this._el;
    if (!el) return;
    const idx = this._index;
    const errorHtml = this._error ? `
      <div class="error" role="alert">
        <div class="etxt"><div class="etitle">Could not load the inventory</div>${esc(this._error)}</div>
        <button type="button" class="btn" data-action="retry" data-key="retry">Try again</button>
      </div>` : "";
    if (!idx) {
      el.summary.textContent = "";
      this._swap(el.notice, errorHtml || `<div class="msg">Loading your cellar…</div>`);
      this._swap(el.results, "");
      this._swap(el.more, "");
      return;
    }
    const groups = this._view();
    const bottles = groups.reduce((n, g) => n + g.bottles.length, 0);
    const filtered = this._query.trim() || this._types.size || this._maturities.size || this._cellar;
    el.summary.textContent = groups.length ? `${plural(groups.length, "wine", "wines")} · ${plural(bottles, "bottle", "bottles")}` : "";
    let notice = errorHtml;
    if (!groups.length) {
      notice += filtered ? `
        <div class="msg">No bottles match.
          <button type="button" class="btn" data-action="clear" data-key="clear">Clear search and filters</button>
        </div>` : `<div class="msg">No bottles in your cellar yet.</div>`;
    }
    this._swap(el.notice, notice, ".search");  // e.g. Try again / Clear just vanished
    this._swap(el.results, groups.slice(0, this._limit).map((g) => this._rowHtml(g)).join(""));
    const left = groups.length - this._limit;
    this._swap(el.more, left > 0 ? `
      <button type="button" class="btn more" data-action="more" data-key="more">Show more (${left} more)</button>` : "");
    this._observeThumbs();
  }

  _pill(key) {
    const m = MATURITY_BY_KEY[key];
    if (!m || key === "none") return "";
    return `<span class="pill" style="--c:${m.color}"><span class="dot" aria-hidden="true"></span>${esc(m.label)}</span>`;
  }

  _rowHtml(g) {
    const meta = [g.producer, g.vintage, g.sizes.join(", ")].filter((v) => v != null && v !== "");
    const more = g.places - 1;
    return `<li><button type="button" class="row" data-action="open" data-group="${esc(g.key)}"
                data-key="${esc(`g:${g.key}`)}">
        ${this._thumbHtml(g.image, "")}
        <span class="main">
          <span class="name">${esc(g.name)}</span>
          ${meta.length ? `<span class="meta">${meta.map(esc).join(" · ")}</span>` : ""}
          <span class="where">${esc(g.bottles[0].place)}${more > 0
            ? ` <span class="more-places">+${plural(more, "more place", "more places")}</span>` : ""}</span>
        </span>
        <span class="side">${this._pill(g.maturity)} <span class="count">${plural(g.bottles.length, "bottle", "bottles")}</span></span>
      </button></li>`;
  }

  _renderDetail() {
    const el = this._el;
    if (!el) return;
    if (!this._openKey) {
      el.detail.hidden = true;
      el.list.hidden = false;
      return;
    }
    const g = this._view().find((x) => x.key === this._openKey);
    if (!g) {
      // Its last bottle was taken out (here or elsewhere): back to the
      // list. Focus only moves if it was in the detail, never off-card.
      this._announce("No bottles of that wine are left.");
      this._closeDetail(this._focusInside(el.detail));
      return;
    }
    el.list.hidden = true;
    el.detail.hidden = false;
    this._swap(el.detail, this._detailHtml(g), ".d-name");
    if (g.image && this._show("show_images")) this._loadImage(this._imageUrl(g.image));
  }

  _detailHtml(g) {
    const idx = this._index;
    const w = g.wine || {};
    const grapes = Array.isArray(w.grapes) ? w.grapes : [];
    const type = typeLabel(w.type) + (w.type && w.colour && w.colour !== w.type ? ` (${w.colour})` : "");
    const facts = [
      ["Vintage", g.vintage], ["Size", g.sizes.join(", ")], ["Type", type], ["Region", w.region],
      ["Appellation", w.appellation !== w.region ? w.appellation : null], ["Country", w.country],
      [grapes.length === 1 ? "Grape" : "Grapes", grapes.join(", ")],
    ].filter(([, v]) => v != null && v !== "");
    const sharedWindow = new Set(g.bottles.map((b) => windowSig(b.raw.window))).size === 1;
    const first = g.bottles[0];
    const opts = {
      sharedWindow,
      multiCellar: idx.cellars.length > 1,
      mixedSize: g.sizes.length > 1,
    };
    let winePersonal = "";
    if (this._show("show_personal")) {
      const seen = new Map();
      for (const b of g.bottles) {
        for (const p of b.personal) {
          if (p.level !== "wine") continue;
          const k = [p.key, personalValue(p.value), p.unit, p.by].join("\u0001");
          if (!seen.has(k)) seen.set(k, p);
        }
      }
      winePersonal = this._personalHtml([...seen.values()]);
    }
    return `
      <button type="button" class="back" data-action="back" data-key="back" aria-label="Back to the list">
        <ha-icon icon="mdi:arrow-left" aria-hidden="true"></ha-icon>Back
      </button>
      <div class="d-top">
        ${this._thumbHtml(g.image, "big")}
        <div class="d-title">
          <h2 class="d-name" tabindex="-1" data-key="d-name">${esc(g.name)}</h2>
          ${g.producer ? `<div class="d-producer">${esc(g.producer)}</div>` : ""}
          ${this._pill(g.maturity)}
        </div>
      </div>
      ${facts.length ? `<dl class="facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
      ${g.wine ? "" : `<p class="pending">This wine is still waiting to be identified in Cellarion.</p>`}
      ${sharedWindow ? `<h3>Drink window</h3>${this._windowHtml(first.raw.window, first.maturity)}` : ""}
      ${winePersonal ? `<h3>Personal</h3>${winePersonal}` : ""}
      <h3>${esc(plural(g.bottles.length, "bottle", "bottles"))}</h3>
      <ul class="bottles">${g.bottles.map((b) => this._bottleHtml(b, opts)).join("")}</ul>`;
  }

  _bottleHtml(b, { sharedWindow, multiCellar, mixedSize }) {
    const r = b.raw;
    const id = String(r.id);
    const meta = [];
    if (multiCellar) meta.push(`<span><span class="k">Cellar</span> ${esc(this._index.cellarName.get(r.cellar) || "—")}</span>`);
    if (mixedSize && r.size) meta.push(`<span><span class="k">Size</span> ${esc(r.size)}</span>`);
    if (this._show("show_value") && num(r.price) != null) {
      meta.push(`<span><span class="k">Price</span> ${esc(this._money(r.price, r.currency))}</span>`);
    }
    if (num(r.rating) != null) {
      meta.push(`<span><span class="k">Rating</span> ${esc(r.rating)}${r.ratingScale ? `/${esc(r.ratingScale)}` : ""}</span>`);
    }
    if (r.opened) meta.push(`<span>Opened</span>`);
    const bottlePersonal = this._show("show_personal")
      ? this._personalHtml(b.personal.filter((p) => p.level !== "wine")) : "";
    const link = this._deepLink(r);
    const busy = this._busy.has(id);
    const canTake = this._show("show_consume") && this._index.cellarRole.get(r.cellar) !== "viewer";
    // aria-disabled rather than disabled while the call runs, so keyboard
    // focus stays on the button; the _busy lock ignores further taps.
    const take = canTake ? `
      <button type="button" class="take" data-action="take" data-bottle="${esc(id)}" data-key="${esc(`take:${id}`)}"
              aria-label="${esc(`Take out ${this._takeText(b)}`)}"${busy
                ? ' aria-disabled="true" aria-busy="true"' : ""}>${busy ? "Taking out…" : "Take out"}</button>` : "";
    // The open prompt replaces this bottle's actions until confirmed or
    // cancelled. Only one is open, so its fixed id is unique.
    const question = `Take out ${this._takeText(b)}?`;
    const actions = canTake && !busy && this._askId === id ? `
      <div class="ask" role="group" aria-labelledby="ask-q">
        <span class="q" id="ask-q">${esc(question)}</span>
        <button type="button" class="btn" data-action="cancel-take" data-bottle="${esc(id)}"
                data-key="${esc(`cancel:${id}`)}">Cancel</button>
        <button type="button" class="take go" data-action="confirm-take" data-bottle="${esc(id)}"
                data-key="${esc(`confirm:${id}`)}">Take out</button>
      </div>` : `${link}${take}`;
    const err = this._takeError?.id === id ? `<div class="b-err" role="alert">${esc(this._takeError.message)}</div>` : "";
    return `<li class="bottle">
        <div class="b-where"><ha-icon icon="mdi:map-marker-outline" aria-hidden="true"></ha-icon><span>${esc(b.place)}</span></div>
        ${r.location && r.place ? `<div class="b-sub">${esc(r.place)}</div>` : ""}
        ${meta.length ? `<div class="b-meta">${meta.join(" ")}</div>` : ""}
        ${sharedWindow ? "" : this._windowHtml(r.window, b.maturity)}
        ${this._show("show_notes") && r.notes ? `<p class="notes">${esc(r.notes)}</p>` : ""}
        ${bottlePersonal}
        ${actions.trim() ? `<div class="b-actions">${actions}</div>` : ""}
        ${err}
      </li>`;
  }

  _personalHtml(entries) {
    if (!entries.length) return "";
    return `<ul class="personal">${entries.map((p) => `<li><span class="k">${esc(p.key)}:</span> ${
      esc(personalValue(p.value))}${p.unit ? ` ${esc(p.unit)}` : ""}${
      p.level ? ` <span class="lvl">${esc(p.level)}</span>` : ""}</li>`).join("")}</ul>`;
  }

  _deepLink(r) {
    const base = this._instanceUrl();
    if (!base || r.cellar == null || r.id == null) return "";
    const href = `${base}/cellars/${encodeURIComponent(r.cellar)}/bottles/${encodeURIComponent(r.id)}`;
    return `<a class="link" href="${esc(href)}" target="_blank" rel="noopener noreferrer">Open in Cellarion<span
      class="ext" aria-hidden="true">↗</span><span class="sr-only"> (opens in a new tab)</span></a>`;
  }

  /**
   * The drink window as a bar: not-ready before drinkFrom, early up to
   * the peak, the peak band, late after it, declining past drinkTo, and
   * a marker for now. The bar is decorative; the years are spelled out
   * underneath, so nothing depends on colour alone.
   */
  _windowHtml(w, maturity) {
    const from = num(w?.drinkFrom), to = num(w?.drinkTo), pf = num(w?.peakFrom), pu = num(w?.peakUntil);
    const start = from ?? pf, end = to ?? pu;
    if (start == null && end == null) return `<div class="win none">No drink window yet.</div>`;
    const d = new Date();
    const nowY = d.getFullYear() + d.getMonth() / 12;
    const known = [from, to, pf, pu].filter((v) => v != null);
    const lo = Math.min(...known, d.getFullYear());
    const hi = Math.max(...known, d.getFullYear()) + 1;
    const pct = (y) => Math.min(100, Math.max(0, ((y - lo) / (hi - lo)) * 100));
    const seg = (a, b, color) => (b > a
      ? `<span style="left:${pct(a).toFixed(2)}%;width:${(pct(b) - pct(a)).toFixed(2)}%;background:${color}"></span>` : "");
    const fade = (c) => `color-mix(in srgb, ${c} 35%, transparent)`;
    const s = start ?? lo;
    const e = end != null ? end + 1 : hi;
    let bands = seg(lo, start ?? lo, fade(NOT_READY));
    if (pf != null || pu != null) {
      const ps = pf ?? s, pe = pu != null ? pu + 1 : e;
      bands += seg(s, ps, EARLY) + seg(ps, pe, PEAK) + seg(pe, e, LATE);
    } else {
      bands += seg(s, e, PEAK);
    }
    bands += end != null ? seg(e, hi, fade(DECLINING)) : "";
    const labels = [
      start != null ? `From ${start}` : null,
      pf != null || pu != null ? `Peak ${pf ?? "?"}–${pu ?? "?"}` : null,
      end != null ? `Until ${end}` : null,
    ].filter(Boolean);
    const m = MATURITY_BY_KEY[maturity];
    const nowText = m && maturity !== "none" ? `Now: ${m.label.toLowerCase()}` : `Now: ${d.getFullYear()}`;
    return `<div class="win" role="group" aria-label="Drink window">
        <div class="track-wrap" aria-hidden="true">
          <div class="track">${bands}</div>
          <span class="now" style="left:${pct(nowY).toFixed(2)}%"></span>
        </div>
        <div class="years">${labels.map((t) => `<span>${esc(t)}</span>`).join(" ")} <span class="now-lbl">${esc(nowText)}</span></div>
      </div>`;
  }

  _announce(text) {
    if (this._el) this._el.announce.textContent = text;
  }

  // ── Interaction ─────────────────────────────────────────────────────

  _onClick(ev) {
    const el = ev.target?.closest?.("[data-action]");
    if (!el) return;
    const { action, value } = el.dataset;
    if (action === "type" || action === "maturity") {
      const set = action === "type" ? this._types : this._maturities;
      if (set.has(value)) set.delete(value); else set.add(value);
      this._limit = this._max();
      this._renderFilters();
      this._renderResults();
    } else if (action === "sort") {
      this._sort = value === "drink" ? "drink" : "name";
      this._renderFilters();
      this._renderResults();
    } else if (action === "open") {
      this._openDetail(el.dataset.group);
    } else if (action === "back") {
      this._closeDetail(true);
    } else if (action === "more") {
      const shown = this._limit;
      this._limit += this._max();
      this._renderResults();
      // Keyboard users land on the first newly shown wine.
      (this._el.results.querySelectorAll("button.row")[shown] || this._el.search).focus?.();
    } else if (action === "clear") {
      this._types.clear();
      this._maturities.clear();
      this._cellar = "";
      this._el.search.value = "";
      this._setQuery("");
      this._renderFilters();
      this._el.search.focus?.();
    } else if (action === "retry") {
      this._fetch();
    } else if (action === "take") {
      this._ask(el.dataset.bottle);
    } else if (action === "cancel-take") {
      this._cancelAsk();
    } else if (action === "confirm-take") {
      this._take(el.dataset.bottle);
    }
  }

  _onChange(ev) {
    if (ev.target?.dataset?.action !== "cellar") return;
    this._cellar = ev.target.value || "";
    this._limit = this._max();
    this._renderResults();
  }

  _onKeydown(ev) {
    if (ev.key !== "Escape") return;
    if (this._askId) {
      // An open take-out prompt goes first; the detail stays open.
      ev.preventDefault();
      ev.stopPropagation();
      this._cancelAsk();
    } else if (this._openKey) {
      ev.preventDefault();
      ev.stopPropagation();
      this._closeDetail(true);
    } else if (ev.target === this._el.search && this._el.search.value) {
      ev.preventDefault();
      ev.stopPropagation();
      this._el.search.value = "";
      this._setQuery("");
    }
  }

  _setQuery(text) {
    this._query = String(text ?? "");
    this._limit = this._max();
    this._renderResults();
  }

  _openDetail(key) {
    if (!key) return;
    this._openKey = key;
    this._returnKey = `g:${key}`;
    this._askId = null;
    this._takeError = null;
    this._renderDetail();
    this._el.detail.querySelector(".d-name")?.focus?.();
  }

  _closeDetail(focus) {
    this._openKey = null;
    this._askId = null;
    this._takeError = null;
    this._el.detail.hidden = true;
    this._el.detail.innerHTML = "";
    this._el.list.hidden = false;
    if (focus && !this._focusKey(this._returnKey)) this._el.search.focus?.();
  }

  _focusKey(key) {
    const n = key && [...this.shadowRoot.querySelectorAll("[data-key]")].find((x) => x.dataset.key === key);
    n?.focus?.();
    return !!n;
  }

  _takeText(b) {
    const label = b.raw.vintage ? `${b.name} ${b.raw.vintage}` : b.name;
    return b.raw.location || b.raw.place ? `${label} from ${b.place}` : `${label} (not placed)`;
  }

  // Take out asks inline, in the bottle's own row: no dialog-box (not
  // loaded in a fresh HA session) and no window.confirm (a native dialog
  // makes the HA frontend reload the page). One prompt at a time.
  _ask(id) {
    if (!id || this._busy.has(id) || !this._index?.byId.has(id)) return;
    this._askId = id;
    this._takeError = null;
    this._renderDetail();
    this._focusKey(`confirm:${id}`);
  }

  _cancelAsk() {
    const id = this._askId;
    if (!id) return;
    this._askId = null;
    this._renderDetail();
    this._focusKey(`take:${id}`);
  }

  async _take(id) {
    const b = id && this._index?.byId.get(id);
    // One bottle, one request: only the open prompt's own bottle, and
    // never while its call is in flight.
    if (!b || this._busy.has(id) || this._askId !== id) return;
    this._askId = null;
    this._busy.add(id);
    this._takeError = null;
    this._renderDetail();
    this._focusKey(`take:${id}`);
    const data = { bottle_id: id };
    // Needed with several accounts; the response says which one this is.
    const entryId = this._entryId();
    if (entryId) data.entry_id = entryId;
    let done = false;
    try {
      // notifyOnError false: the card shows its own inline error, and
      // HA's toast would only repeat it.
      await this._hass.callService("cellarion", "consume_bottle", data, undefined, false);
      done = true;
    } catch (err) {
      this._takeError = { id, message: `Could not take it out: ${errorText(err)}` };
    }
    this._busy.delete(id);
    if (done) {
      // Hidden right away; the refetch below confirms it is gone.
      this._taken.set(id, Date.now());
      this._takenVersion++;
      this._announce(`Taken out: ${this._takeText(b)}.`);
    }
    this._renderResults();
    this._renderDetail();
    if (done) this._fetch();
  }

  // ── Label images ────────────────────────────────────────────────────

  _imageUrl(path) {
    const m = THUMB_RE.exec(path || "");
    const entryId = this._entryId();
    if (!m || !entryId) return null;
    return `/api/cellarion/image/${encodeURIComponent(entryId)}/${m[1]}/${encodeURIComponent(m[2])}`;
  }

  _thumbHtml(path, cls) {
    if (!this._show("show_images") || !path) return "";
    const url = this._imageUrl(path);
    if (!url) return "";
    const src = this._images.get(url)?.src;
    return `<span class="thumb${cls ? ` ${cls}` : ""}" data-thumb="${esc(url)}" aria-hidden="true">${src
      ? `<img src="${esc(src)}" alt="">` : `<ha-icon icon="mdi:bottle-wine-outline"></ha-icon>`}</span>`;
  }

  // Rows load their thumb when scrolled near; without IntersectionObserver
  // only the open detail loads one.
  _observeThumbs() {
    if (!this._show("show_images") || typeof IntersectionObserver !== "function" || !this._connected) return;
    if (!this._io) {
      this._io = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          this._io.unobserve(e.target);
          this._loadImage(e.target.dataset.thumb);
        }
      }, { rootMargin: "200px" });
    } else {
      this._io.disconnect();
    }
    for (const n of this._el.results.querySelectorAll("[data-thumb]")) {
      if (!this._images.has(n.dataset.thumb)) this._io.observe(n);
    }
  }

  _loadImage(url) {
    if (!url || this._images.has(url) || typeof this._hass?.fetchWithAuth !== "function") return;
    this._images.set(url, { pending: true });
    this._imgQueue.push(url);
    this._pumpImages();
  }

  _pumpImages() {
    while (this._imgActive < IMAGE_CONCURRENCY && this._imgQueue.length) {
      const url = this._imgQueue.shift();
      const gen = this._imgGen;
      this._imgActive++;
      Promise.resolve()
        .then(() => this._hass.fetchWithAuth(url))
        .then((res) => {
          if (!res?.ok) throw new Error(`HTTP ${res?.status}`);
          return res.blob();
        })
        .then((blob) => {
          if (gen !== this._imgGen) return;  // card left the page meanwhile
          const src = URL.createObjectURL(blob);
          this._images.set(url, { src });
          this._evictImages();
          this._paintThumb(url, src);
        })
        .catch(() => {
          // The placeholder stays; no retry until the card is shown again.
          if (gen === this._imgGen) this._images.set(url, { failed: true });
        })
        .finally(() => {
          if (gen !== this._imgGen) return;
          this._imgActive--;
          this._pumpImages();
        });
    }
  }

  _paintThumb(url, src) {
    for (const n of this.shadowRoot.querySelectorAll("[data-thumb]")) {
      if (n.dataset.thumb === url) n.innerHTML = `<img src="${esc(src)}" alt="">`;
    }
  }

  _evictImages() {
    for (const [url, entry] of this._images) {
      if (this._images.size <= IMAGE_CACHE_MAX) break;
      if (!entry.src) continue;
      URL.revokeObjectURL(entry.src);
      this._images.delete(url);
    }
  }

  _clearImages() {
    for (const entry of this._images.values()) if (entry.src) URL.revokeObjectURL(entry.src);
    this._images.clear();
    this._imgQueue = [];
    this._imgActive = 0;
    this._imgGen++;
    this._io?.disconnect();
    this._io = null;
  }
}

class CellarionInventoryCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  // An explicit boolean per toggle that mirrors the card (unset = shown)...
  _formData() {
    return {
      ...this._config,
      ...Object.fromEntries(TOGGLES.map((t) => [t.name, isShown(this._config, t.name)])),
    };
  }

  // ...and only the pieces turned off written back. Cleared fields go too.
  static _prune(value) {
    const config = { ...value };
    for (const t of TOGGLES) {
      if (isShown(config, t.name)) delete config[t.name];
    }
    for (const key of ["title", "entry_id"]) {
      if (typeof config[key] === "string" && !config[key].trim()) delete config[key];
    }
    if (config.max_results == null || config.max_results === "" || !(Number(config.max_results) > 0)) {
      delete config.max_results;
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
      { name: "max_results", selector: { number: { min: 5, max: 500, step: 5, mode: "box" } } },
      {
        name: "",
        type: "expandable",
        title: "Show on the card",
        icon: "mdi:eye-outline",
        schema: TOGGLES.map((t) => ({ name: t.name, selector: { boolean: {} } })),
      },
    ];
    this._form.computeLabel = (s) => ({
      title: "Title",
      entry_id: "Account (only if you have several)",
      max_results: `Wines listed before “Show more” (default ${DEFAULT_MAX})`,
      ...Object.fromEntries(TOGGLES.map((t) => [t.name, t.label])),
    }[s.name] || s.name);
    this._form.addEventListener("value-changed", (ev) => {
      this._config = CellarionInventoryCardEditor._prune(ev.detail.value);
      this.dispatchEvent(new CustomEvent("config-changed", {
        bubbles: true, composed: true, detail: { config: this._config },
      }));
    });
    this.appendChild(this._form);
  }
}

// Guarded: the resource may be loaded twice (an old and a new ?v= URL).
if (!customElements.get(CARD_TAG)) customElements.define(CARD_TAG, CellarionInventoryCard);
if (!customElements.get(EDITOR_TAG)) customElements.define(EDITOR_TAG, CellarionInventoryCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Cellarion Inventory",
    description: "Find a bottle, see where it is, take it out",
    preview: true,
  });
}
