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
 *   prefix: sensor.cellarion     (optional — entity id prefix)
 */

const MATURITY = [
  { key: "not_ready", entity: "_bottles_not_ready", label: "Not ready", color: "#2563EB" },
  { key: "early", entity: "_bottles_early_window", label: "Early", color: "#0891B2" },
  { key: "peak", entity: "_bottles_at_peak", label: "At peak", color: "#059669" },
  { key: "declining", entity: "_bottles_declining", label: "Declining", color: "#D97706" },
  { key: "late", entity: "_bottles_late_window", label: "Late", color: "#DC2626" },
];

const STATUS_COLOR = {
  declining: "#D97706",
  late: "#DC2626",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

class CellarionCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Wine Cellar", prefix: "sensor.cellarion" };
  }

  static getConfigElement() {
    return document.createElement("cellarion-card-editor");
  }

  setConfig(config) {
    this._config = {
      title: "Wine Cellar",
      prefix: "sensor.cellarion",
      ...config,
    };
    this._fingerprint = null;
  }

  set hass(hass) {
    this._hass = hass;
    const fp = this._computeFingerprint();
    if (fp !== this._fingerprint) {
      this._fingerprint = fp;
      this._render();
    }
  }

  getCardSize() {
    return 5;
  }

  _entityIds() {
    const p = this._config.prefix;
    return [
      `${p}_total_bottles`, `${p}_collection_value`, `${p}_unique_wines`,
      `${p}_collection_health_score`, `${p}_service_status`,
      ...MATURITY.map((m) => `${p}${m.entity}`),
    ];
  }

  _computeFingerprint() {
    if (!this._hass || !this._config) return null;
    return this._entityIds()
      .map((id) => {
        const s = this._hass.states[id];
        return s ? `${s.state}|${s.last_updated}` : "missing";
      })
      .join(";");
  }

  _state(suffix) {
    return this._hass.states[`${this._config.prefix}${suffix}`];
  }

  _num(suffix) {
    const s = this._state(suffix);
    if (!s || s.state === "unknown" || s.state === "unavailable") return null;
    const n = Number(s.state);
    return Number.isFinite(n) ? n : null;
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
    const currency = s.attributes.currency
      || s.attributes.unit_of_measurement || "USD";
    try {
      return new Intl.NumberFormat(this._hass.locale?.language || "en", {
        style: "currency", currency, maximumFractionDigits: 0,
      }).format(n);
    } catch (e) {
      return `${Math.round(n)} ${currency}`;
    }
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    const totalState = this._state("_total_bottles");
    if (!totalState) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding:16px;color:var(--secondary-text-color)">
            Cellarion entities not found (looked for
            <code>${esc(this._config.prefix)}_total_bottles</code>).
            Is the Cellarion integration set up?
          </div>
        </ha-card>`;
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

    const healthColor = health == null ? "var(--secondary-text-color)"
      : health >= 80 ? "#059669" : health >= 60 ? "#D97706" : "#DC2626";

    const bar = maturityTotal > 0
      ? counts.filter((c) => c.count > 0).map((c) => `
          <div class="seg" title="${esc(c.label)}: ${c.count}"
               data-entity="${esc(this._config.prefix + c.entity)}"
               style="flex-grow:${c.count};background:${c.color}"></div>`).join("")
      : `<div class="seg empty" style="flex-grow:1"></div>`;

    const legend = counts.map((c) => `
        <div class="lg ${c.count === 0 ? "zero" : ""}"
             data-entity="${esc(this._config.prefix + c.entity)}">
          <span class="chip" style="background:${c.color}"></span>
          <span>${esc(c.label)}</span>
          <span class="cnt">${c.count}</span>
        </div>`).join("");

    const urgentHtml = urgent.length ? `
      <div class="section-label">Drink soon</div>
      <div class="urgent">
        ${urgent.map((b) => `
          <div class="bottle">
            <ha-icon icon="mdi:bottle-wine"></ha-icon>
            <span class="bname">${esc(b.name)}</span>
            <span class="vintage">${esc(b.vintage)}</span>
            <span class="status" style="background:${STATUS_COLOR[b.status] || "#D97706"}1a;color:${STATUS_COLOR[b.status] || "#D97706"}">${esc(b.status)}</span>
            ${b.id ? `
            <button class="consume" data-bottle="${esc(b.id)}"
                    data-name="${esc(b.name)}" title="Mark as drunk">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z"/></svg>
            </button>` : ""}
          </div>`).join("")}
      </div>` : "";

    const serviceHtml = service && service !== "ok" ? `
      <div class="banner">
        <ha-icon icon="mdi:alert"></ha-icon>
        Cellarion instance is ${esc(service)}
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
        .consume:hover { border-color:#059669; color:#059669; }
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
                  background:#D977061a; color:#D97706; border-radius:8px;
                  padding:8px 12px; font-size:0.9em; }
        .banner ha-icon { --mdc-icon-size:18px; }
        .section-label:not(:first-child) { margin-top:16px; }
      </style>
      <ha-card>
        <div class="head">
          ${(() => {
            const link = this._config.url
              || this._state("_service_status")?.attributes?.instance_url;
            return link
              ? `<a class="title" href="${esc(link)}" target="_blank"
                    rel="noopener" title="Open Cellarion">${esc(this._config.title)}<span class="ext">↗</span></a>`
              : `<div class="title">${esc(this._config.title)}</div>`;
          })()}
          ${health != null ? `
            <div class="health" data-entity="${esc(this._config.prefix)}_collection_health_score">
              <span>${health}</span>
              ${grade ? `<span class="grade">${esc(grade)}</span>` : ""}
            </div>` : ""}
        </div>
        <div class="stats">
          <div class="stat" data-entity="${esc(this._config.prefix)}_total_bottles">
            <div class="v">${bottles ?? "—"}</div><div class="l">Bottles</div>
          </div>
          <div class="stat" data-entity="${esc(this._config.prefix)}_collection_value">
            <div class="v">${esc(this._formatValue())}</div><div class="l">Value</div>
          </div>
          <div class="stat" data-entity="${esc(this._config.prefix)}_unique_wines">
            <div class="v">${wines ?? "—"}</div><div class="l">Wines</div>
          </div>
        </div>
        <div class="section-label">Drink window${maturityTotal ? ` · ${maturityTotal} bottles` : ""}</div>
        <div class="bar">${bar}</div>
        <div class="legend">${legend}</div>
        ${urgentHtml}
        ${serviceHtml}
      </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      el.addEventListener("click", () => this._moreInfo(el.dataset.entity));
    });
    this.shadowRoot.querySelectorAll("button.consume").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (window.confirm(`Mark "${el.dataset.name}" as drunk?`)) {
          this._hass.callService("cellarion", "consume_bottle", {
            bottle_id: el.dataset.bottle,
          });
        }
      });
    });
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

  _render() {
    if (this._form) {
      this._form.data = this._config;
      return;
    }
    this._form = document.createElement("ha-form");
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      { name: "title", selector: { text: {} } },
      { name: "prefix", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      s.name === "title" ? "Title" : "Entity prefix";
    this._form.addEventListener("value-changed", (ev) => {
      this._config = ev.detail.value;
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
