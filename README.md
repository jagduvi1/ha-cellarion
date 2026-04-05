# Cellarion for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration for [Cellarion](https://cellarion.app) — the self-hosted wine cellar management app.

Your wine data stays in Cellarion. This integration reads from the Cellarion API and exposes sensors in Home Assistant for dashboards and automations.

## Features

- **Collection overview** — total bottles, value, unique wines, average rating
- **Drink window tracking** — bottles at peak, declining, not ready, early/late window
- **Maturity alerts** — urgent bottles listed as sensor attributes
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
3. Enter your Cellarion instance URL (default: `https://cellarion.app`)
4. Enter your email and password
5. Done! Sensors will appear under the **Cellarion** device

### Options

After setup, you can adjust the polling interval (default: 30 minutes) via the integration's options.

## Sensors

| Sensor | Description | Attributes |
|--------|-------------|------------|
| Total bottles | Bottles in your collection | — |
| Collection value | Total value in your currency | currency, average price |
| Unique wines | Distinct wine definitions | — |
| Cellars | Number of cellars | per-cellar breakdown |
| Average rating | Mean rating across bottles | — |
| Countries | Countries represented | top 5 countries |
| Bottles at peak | Ready to drink now | — |
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

## Dashboard Examples

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

- Home Assistant 2024.1 or newer
- A Cellarion account (hosted or self-hosted)

## License

MIT
