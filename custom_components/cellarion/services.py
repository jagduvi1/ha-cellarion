"""Services of the Cellarion integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from pycellarion import (
    CellarionApiError,
    CellarionAuthError,
    CellarionInventoryNotSupported,
    CellarionScopeError,
)
import voluptuous as vol

from .const import DOMAIN
from .coordinator import CellarionConfigEntry

# @feature inventory
from .inventory import (
    INVENTORY_MIN_VERSION,
    MATURITY_STATES,
    async_get_inventory,
    filter_inventory,
)

# @endfeature

SERVICE_CONSUME_BOTTLE = "consume_bottle"
CONSUME_REASONS = ["drank", "gifted", "sold", "other"]
# Cellarion bottle ids are MongoDB ObjectIds: exactly 24 hex characters. The
# id is interpolated into a request path, so anything else is refused here.
BOTTLE_ID_PATTERN = r"^[0-9a-fA-F]{24}$"

SERVICE_CONSUME_SCHEMA = vol.Schema(
    {
        vol.Required("bottle_id"): vol.All(cv.string, vol.Match(BOTTLE_ID_PATTERN)),
        vol.Optional("reason", default="drank"): vol.In(CONSUME_REASONS),
        vol.Optional("rating"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("note"): vol.All(cv.string, vol.Length(max=1000)),
        vol.Optional("entry_id"): cv.string,
    }
)

# @feature inventory
SERVICE_GET_BOTTLES = "get_bottles"
SERVICE_GET_BOTTLES_SCHEMA = vol.Schema(
    {
        vol.Optional("search"): vol.All(cv.string, vol.Length(max=200)),
        vol.Optional("maturity"): vol.All(cv.ensure_list, [vol.In(MATURITY_STATES)]),
        vol.Optional("type"): vol.All(cv.ensure_list, [vol.All(cv.string, vol.Length(max=40))]),
        vol.Optional("cellar"): vol.All(cv.string, vol.Length(max=100)),
        vol.Optional("entry_id"): cv.string,
    }
)
# @endfeature


def _resolve_entry(call: ServiceCall) -> CellarionConfigEntry:
    """The loaded entry a service call is for: entry_id, or the only one."""
    entries: list[CellarionConfigEntry] = [
        entry
        for entry in call.hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_accounts")

    if entry_id := call.data.get("entry_id"):
        matches = [entry for entry in entries if entry.entry_id == entry_id]
        if not matches:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entry",
                translation_placeholders={"entry_id": entry_id},
            )
        return matches[0]
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="multiple_accounts")


async def _async_consume_bottle(call: ServiceCall) -> None:
    """Handle the cellarion.consume_bottle service."""
    entry = _resolve_entry(call)
    coordinator = entry.runtime_data
    try:
        await coordinator.client.consume_bottle(
            call.data["bottle_id"],
            reason=call.data["reason"],
            rating=call.data.get("rating"),
            note=call.data.get("note"),
        )
    except CellarionScopeError as err:
        # Token is valid but was created without the consume scope — the
        # user has to mint a new one; say so instead of a generic failure
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="consume_scope_missing",
            translation_placeholders={"error": str(err)},
        ) from err
    except CellarionAuthError as err:
        # Revoked token / changed password: start the reauth prompt now
        # rather than waiting for the next poll to notice
        entry.async_start_reauth(call.hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="consume_auth_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except CellarionApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="consume_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    await coordinator.async_request_refresh()


# @feature inventory
async def _async_get_bottles(call: ServiceCall) -> ServiceResponse:
    """Handle the cellarion.get_bottles service: the (filtered) inventory."""
    entry = _resolve_entry(call)
    inventory = async_get_inventory(call.hass, entry)
    try:
        data = await inventory.async_get()
    except CellarionInventoryNotSupported as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="inventory_not_supported",
            translation_placeholders={"version": INVENTORY_MIN_VERSION},
        ) from err
    except CellarionAuthError as err:
        entry.async_start_reauth(call.hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="inventory_auth_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except CellarionApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="inventory_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    result = filter_inventory(
        data,
        search=call.data.get("search"),
        maturity=call.data.get("maturity"),
        types=call.data.get("type"),
        cellar=call.data.get("cellar"),
    )
    return {
        "entry_id": entry.entry_id,
        "instance_url": entry.runtime_data.url,
        **result,
    }


# @endfeature


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services (once, at component setup)."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONSUME_BOTTLE,
        _async_consume_bottle,
        schema=SERVICE_CONSUME_SCHEMA,
    )
    # @feature inventory
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_BOTTLES,
        _async_get_bottles,
        schema=SERVICE_GET_BOTTLES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # @endfeature
