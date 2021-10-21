"""The openevse component."""
from __future__ import annotations
import asyncio
import logging
from datetime import timedelta
from typing import Any

import homeassistant.helpers.device_registry as dr
import openevsehttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from requests import RequestException

from .const import (
    CONF_NAME,
    COORDINATOR,
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    SENSOR_TYPES,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)
states = {
    0: "unknown",
    1: "not connected",
    2: "connected",
    3: "charging",
    4: "vent required",
    5: "diode check failed",
    6: "gfci fault",
    7: "no ground",
    8: "stuck relay",
    9: "gfci self-test failure",
    10: "over temperature",
    254: "sleeping",
    255: "disabled",
}


async def async_setup(hass: HomeAssistant, config: Config) -> bool:
    """Disallow configuration via YAML."""
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up is called when Home Assistant is loading our component."""
    hass.data.setdefault(DOMAIN, {})
    _LOGGER.info(
        "Version %s is starting, if you have any issues please report" " them here: %s",
        VERSION,
        ISSUE_URL,
    )

    config_entry.add_update_listener(update_listener)
    interval = 10
    coordinator = OpenEVSEUpdateCoordinator(hass, interval, config_entry)

    # Fetch initial data so we have data when entities subscribe
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    hass.data[DOMAIN][config_entry.entry_id] = {
        COORDINATOR: coordinator,
    }

    model_info, sw_version = await hass.async_add_executor_job(
        get_firmware, config_entry
    )

    device_registry = await dr.async_get_registry(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        connections={(DOMAIN, config_entry.entry_id)},
        name=config_entry.data[CONF_NAME],
        manufacturer="OpenEVSE",
        model=f"Wifi version {model_info}",
        sw_version=sw_version,
    )

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(config_entry, platform)
        )

    return True


def get_firmware(config: ConfigEntry) -> tuple:
    """Get firmware version."""
    host = config.data.get(CONF_HOST)
    username = config.data.get(CONF_USERNAME)
    password = config.data.get(CONF_PASSWORD)
    _LOGGER.debug("Connecting to %s, with username %s", host, username)
    charger = openevsehttp.OpenEVSE(host, user=username, pwd=password)
    try:
        charger.update()
    except Exception as error:
        _LOGGER.error("Problem retreiving firmware data: %s", error)
        return "", ""

    return charger.wifi_firmware, charger.openevse_firmware


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""

    _LOGGER.debug("Attempting to unload entities from the %s integration", DOMAIN)

    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(config_entry, platform)
                for platform in PLATFORMS
            ]
        )
    )

    if unload_ok:
        _LOGGER.debug("Successfully removed entities from the %s integration", DOMAIN)
        hass.data[DOMAIN].pop(config_entry.entry_id)

    return unload_ok


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update listener."""

    _LOGGER.debug("Attempting to reload entities from the %s integration", DOMAIN)

    if config_entry.data == config_entry.options:
        _LOGGER.debug("No changes detected not reloading entities.")
        return

    new_data = config_entry.options.copy()

    hass.config_entries.async_update_entry(
        entry=config_entry,
        data=new_data,
    )

    await hass.config_entries.async_reload(config_entry.entry_id)


def get_sensors(hass: HomeAssistant, config: ConfigEntry) -> dict:

    data = {}
    host = config.data.get(CONF_HOST)
    username = config.data.get(CONF_USERNAME)
    password = config.data.get(CONF_PASSWORD)
    charger = openevsehttp.OpenEVSE(host, user=username, pwd=password)
    try:
        charger.update()
    except Exception as error:
        _LOGGER.error("Error updating sesnors: %s", error)
        return {}

    for sensor in SENSOR_TYPES:
        _sensor = {}
        try:
            sensor_property = SENSOR_TYPES[sensor].key
            if sensor == "current_power":
                _sensor[sensor] = None
            else:
                _sensor[sensor] = getattr(charger, sensor_property)
            _LOGGER.debug(
                "sensor: %s sensor_property: %s value: %s",
                sensor,
                sensor_property,
                _sensor[sensor],
            )
        except (RequestException, ValueError, KeyError):
            _LOGGER.warning("Could not update status for %s", sensor)
        data.update(_sensor)
    _LOGGER.debug("DEBUG: %s", data)
    return data


class OpenEVSEUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching mail data."""

    def __init__(self, hass, interval, config):
        """Initialize."""
        self.interval = timedelta(seconds=interval)
        self.name = f"OpenEVSE ({config.data.get(CONF_NAME)})"
        self.config = config
        self.hass = hass

        _LOGGER.debug("Data will be update every %s", self.interval)

        super().__init__(hass, _LOGGER, name=self.name, update_interval=self.interval)

    async def _async_update_data(self):
        """Fetch data"""
        try:
            data = await self.hass.async_add_executor_job(
                get_sensors, self.hass, self.config
            )
        except Exception as error:
            raise UpdateFailed(error) from error
        return data


def send_command(handler, command) -> None:
    cmd, response = handler.send_command(command)
    _LOGGER.debug("send_command: %s, %s", cmd, response)
    if cmd == command:
        if response == "$NK^21":
            raise InvalidValue
        return None

    raise CommandFailed


def connect(host: str, username: str = None, password: str = None) -> Any:
    return openevsehttp.OpenEVSE(host, user=username, pwd=password)


class InvalidValue(Exception):
    """Exception for invalid value errors."""


class CommandFailed(Exception):
    """Exception for invalid command errors."""