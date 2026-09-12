---
title: Cellarion
description: Instructions on how to integrate Cellarion wine cellar statistics into Home Assistant.
ha_category:
  - Sensor
ha_release: '2026.11'
ha_iot_class: Cloud Polling
ha_codeowners:
  - '@jagduvi1'
ha_domain: cellarion
ha_platforms:
  - sensor
ha_integration_type: service
ha_config_flow: true
ha_quality_scale: bronze
---

The **Cellarion** {% term integration %} brings your [Cellarion](https://cellarion.app) wine cellar into Home Assistant: how many bottles you have, what they are worth, how many are at their peak or past it, and a health score for the collection. Cellarion is a wine cellar manager available as a hosted service and as a self-hosted server.

## Prerequisites

- A Cellarion account on the hosted service, or your own Cellarion server running version 1.75 or newer.
- A personal API token, or the email address and password of the account. Create a token in Cellarion under **Settings** > **API tokens** with the `read` and `consume` scopes. When you sign in with email and password instead, the integration creates such a token for you and does not keep the password.

{% include integrations/config_flow.md %}

{% configuration_basic %}
URL:
  description: The address of your Cellarion instance. Use `https://cellarion.app` for the hosted service, or the address of your own server.
API token:
  description: A personal API token created in Cellarion with the `read` and `consume` scopes. Only asked when you choose the token method.
Email or username:
  description: The email address or username of your Cellarion account. Only asked when you choose the email and password method.
Password:
  description: Your Cellarion password. It is used once to create a scoped API token and is not stored.
{% endconfiguration_basic %}

## Configuration options

The integration provides the following configuration options:

{% configuration_basic %}
Polling interval:
  description: How often Home Assistant fetches statistics from Cellarion, in seconds. Defaults to 1800 (30 minutes); the minimum is 300.
{% endconfiguration_basic %}

## Supported functionality

### Sensors

One device named Cellarion is created per account, with these sensors:

| Sensor | Description |
| --- | --- |
| Total bottles | Bottles currently in the cellar. |
| Collection value | The value of the collection in the account's currency. |
| Unique wines | Distinct wines in the cellar. |
| Cellars | Number of cellars, with a per-cellar bottle count as an attribute. |
| Average rating | Average rating of rated bottles. |
| Bottles at peak | Bottles in their peak drinking window. The `peak_bottles` attribute lists up to ten of them, soonest-closing window first. |
| Bottles declining | Bottles past their peak. The `urgent_bottles` attribute lists the bottles that need attention first. |
| Bottles not ready | Bottles that have not reached their drinking window yet. |
| Bottles early window | Bottles at the start of their drinking window. |
| Bottles late window | Bottles at the end of their drinking window. |
| Collection health score | Cellarion's health score for the collection, with the grade as an attribute. |
| Unread notifications | Unread notifications in Cellarion. |
| Service status | Whether Cellarion can be reached: `ok`, `degraded`, `unreachable` or `unknown`. |

The following sensors are disabled by default and can be enabled from the entity settings:

| Sensor | Description |
| --- | --- |
| Countries | Number of countries represented, with the top five as an attribute. |
| Oldest vintage | The oldest vintage in the cellar. |
| Newest vintage | The newest vintage in the cellar. |
| Wine types | Number of wine types, with the breakdown as attributes. |
| Top producers | Number of listed producers, with the top ten as an attribute. |
| Consumed bottles | Bottles consumed over the account's lifetime. |
| Intake per year | Average bottles added per year. |
| Runway years | How many years the cellar lasts at the current pace. |

## Data updates

The integration polls Cellarion at the configured interval, 30 minutes by default. While Cellarion's push channel is connected, changes such as a bottle being added or consumed arrive within seconds and polling only serves as a safety net.

## Known limitations

- The integration reads the cellar; adding, moving or rating bottles is done in Cellarion.
- Self-hosted servers behind a reverse proxy that buffers or compresses the event stream fall back to polling.

## Troubleshooting

### The integration asks to re-authenticate

Cellarion rejected the stored token: it was revoked in Cellarion, or the account was deleted. Open the notification and sign in again; nothing else needs to change.

### The setup form says the token is missing the read scope

The token was created without the scope the integration needs. Create a new one in Cellarion under **Settings** > **API tokens** with both the `read` and `consume` scopes and use **Reconfigure** on the integration.

### "Rate limited by Cellarion" during setup

Cellarion limits sign-in attempts per address. Wait 15 minutes and prefer the API-token method, which never signs in.

## Removing the integration

This integration follows standard integration removal. The API token that was created for Home Assistant is revoked on the server at the same time; on a Cellarion server older than 1.220 revoke it by hand under **Settings** > **API tokens**.

{% include integrations/remove_device_service.md %}
