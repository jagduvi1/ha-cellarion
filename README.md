# Cellarion for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/jagduvi1/ha-cellarion)](https://github.com/jagduvi1/ha-cellarion/releases)
[![Validate](https://github.com/jagduvi1/ha-cellarion/actions/workflows/validate.yml/badge.svg)](https://github.com/jagduvi1/ha-cellarion/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration for [Cellarion](https://cellarion.app) — the wine cellar management service. Track your collection, drink windows, and cellar value at **[cellarion.app](https://cellarion.app)**, and bring it all into your smart home. (Prefer to run your own? Cellarion can also be self-hosted — the integration works with both.)

Your wine data stays in your Cellarion account. This integration reads from the Cellarion API and exposes sensors in Home Assistant for dashboards and automations.

## Features

- **Dashboard card included** — a ready-made card with collection stats, a drink-window bar, and the bottles that need attention; no extra install
- **Collection overview** — total bottles, value, unique wines, average rating
- **Drink window tracking** — bottles at peak, declining, not ready, early/late window
- **Maturity alerts** — urgent bottles listed as sensor attributes
- **Consume from Home Assistant** — mark bottles drank/gifted/sold via the `cellarion.consume_bottle` service, from the card, automations, or NFC tags
- **Instant updates** — on Cellarion v1.75+, sensors update within seconds of a change (automatic, no ports to open)
- **Secure by default** — authenticates with a scoped API token; your Cellarion password is never stored in Home Assistant
- **Pace & runway** — intake per year, years until your cellar is empty
- **Cellar breakdown** — per-cellar bottle counts and values
- **Wine types & producers** — breakdown by type, top producers
- **Service health** — monitor your Cellarion instance status
- **Notifications** — unread notification count

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) and select **Custom repositories**
3. Add this repository URL: `https://github.com/jagduvi1/ha-cellarion`
4. Select category: **Integration**
5. Click **Add**, then install **Cellarion**
6. Restart Home Assistant

### Manual

1. Copy the `custom_components/cellarion` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **Cellarion**
3. Pick an authentication method:
   - **API token (recommended)** — create one in Cellarion under
     **Settings → API tokens** with the `read` and `consume` scopes and
     paste it in. Your password never touches Home Assistant.
   - **Email & password** — on current Cellarion servers this mints a
     scoped API token for Home Assistant automatically and does **not**
     store your password. (Older self-hosted servers fall back to
     password login.)
4. Done! Sensors will appear under the **Cellarion** device

### Options

After setup, you can adjust the polling interval (default: 30 minutes) via the integration's options.

### How updates arrive

The integration polls your Cellarion instance on the configured interval.
On Cellarion v1.75+ it also holds an outbound connection to the push
event stream and updates sensors within seconds of a change, relaxing
polling to a 6-hour safety net. This is detected automatically — no
configuration, and your Home Assistant never needs to be reachable from
the internet.

### Security

- Home Assistant stores a **scoped API token** (`read` + `consume`), not
  your Cellarion password — the password path only uses your credentials
  once to create the token.
- Setups made with older versions of this integration are migrated to a
  token automatically on their first start against Cellarion v1.75+.
- Tokens can be reviewed and revoked anytime in Cellarion under
  **Settings → API tokens**; revoking one triggers Home Assistant's
  re-authentication prompt.
- Only on pre-v1.75 self-hosted servers (no token support) does the
  integration fall back to storing the password.

## Sensors

| Sensor | Description | Attributes |
|--------|-------------|------------|
| Total bottles | Bottles in your collection | — |
| Collection value | Total value in your currency | currency, average price |
| Unique wines | Distinct wine definitions | — |
| Cellars | Number of cellars | per-cellar breakdown |
| Average rating | Mean rating across bottles | — |
| Countries | Countries represented | top 5 countries |
| Bottles at peak | Ready to drink now | ready-to-drink list (top 10, with ids) |
| Bottles declining | Past peak, drink soon! | urgent bottles list |
| Bottles not ready | Too young to open | — |
| Bottles early window | Approaching peak | — |
| Bottles late window | Past optimal window | — |
| Consumed bottles | Total consumed (increasing) | — |
| Intake per year | Average bottles added/year | — |
| Runway (years) | Years until cellar is empty | — |
| Oldest vintage | Oldest bottle year | — |
| Newest vintage | Newest bottle year | — |
| Health score | Collection health metric | grade |
| Unread notifications | Pending notifications | — |
| Wine types | Number of wine types | type breakdown |
| Top producers | Number of top producers | producer list (top 10) |
| Service status | Cellarion instance health | — |

## Services

### `cellarion.consume_bottle`

Mark a bottle as consumed in Cellarion — frees its rack slot and updates
your statistics, exactly like consuming it in the app.

| Field | Required | Description |
|-------|----------|-------------|
| `bottle_id` | yes | The Cellarion bottle id |
| `reason` | no | `drank` (default), `gifted`, `sold`, or `other` |
| `rating` | no | Rating to record with the consumption |
| `note` | no | Tasting note or comment |
| `entry_id` | no | Only when multiple Cellarion accounts are configured |

Example — log a bottle by scanning an NFC tag on its rack slot:

```yaml
automation:
  - alias: "NFC: consume bottle"
    trigger:
      - platform: tag
        tag_id: rack-a1        # write the bottle id into the tag's automation
    action:
      - service: cellarion.consume_bottle
        data:
          bottle_id: "6a50805b785f507654afdc78"
          reason: drank
```

## Dashboard Examples

> **Home Assistant 2026+:** the default **Overview** dashboard is
> auto-generated and doesn't accept custom cards. Create your own under
> **Settings → Dashboards → Add dashboard → New dashboard from scratch**,
> then add cards there.

### Cellarion Card (bundled)

The integration ships with a custom Lovelace card and registers it
automatically — no extra install. Add it from the dashboard card picker
(**Custom: Cellarion Card**) or in YAML:

```yaml
type: custom:cellarion-card
title: Wine Cellar          # optional
prefix: sensor.cellarion    # optional — entity id prefix
```

It shows your collection stats, a drink-window distribution bar, a
**Ready to drink** list (bottles at their peak, soonest-closing window
first), and a **Drink soon** list (declining/late bottles). Clicking any
number opens the sensor's more-info dialog, the card title opens your
Cellarion instance, and — on Cellarion v1.75+ — every listed bottle gets
a one-tap consume button (with confirmation). For browsing your full
inventory, follow the card link into Cellarion itself. If your
dashboards run in YAML mode, add `/cellarion-files/cellarion-card.js`
as a module resource manually.

### Simple Entities Card

```yaml
type: entities
title: Wine Cellar
entities:
  - entity: sensor.cellarion_total_bottles
  - entity: sensor.cellarion_collection_value
  - entity: sensor.cellarion_bottles_at_peak
  - entity: sensor.cellarion_bottles_declining
  - entity: sensor.cellarion_runway_years
```

### Automation: Drink Window Alert

```yaml
automation:
  - alias: "Wine ready to drink"
    trigger:
      - platform: numeric_state
        entity_id: sensor.cellarion_bottles_at_peak
        above: 0
    action:
      - service: notify.mobile_app
        data:
          title: "Wine at peak!"
          message: >
            You have {{ states('sensor.cellarion_bottles_at_peak') }}
            bottles at their peak. Time to open one!
```

## Requirements

- Home Assistant 2024.1 or newer (the bundled brand icon shows on 2026.3+)
- A [cellarion.app](https://cellarion.app) account (or your own self-hosted Cellarion instance)
- Cellarion server v1.75+ unlocks instant updates, API tokens, and the
  card's consume button; older servers work with polling and password login

## Development

A Docker-based Home Assistant test environment lives in [dev/](dev/):

```bash
cd dev
docker compose up -d
```

Open http://localhost:8123, create a user, and add the Cellarion integration.
The container joins the local Cellarion compose network, so use
`http://cellarion-backend:5000` as the instance URL. After changing the
integration code, run `docker compose restart homeassistant`.

## License

MIT
