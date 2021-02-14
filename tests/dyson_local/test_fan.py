from typing import Type
from libdyson.dyson_device import DysonDevice, DysonFanDevice
from custom_components.dyson_local.fan import ATTR_AUTO_MODE, ATTR_DYSON_SPEED, ATTR_DYSON_SPEED_LIST, ATTR_NIGHT_MODE, SERVICE_SET_AUTO_MODE, SERVICE_SET_DYSON_SPEED, SPEED_LIST_DYSON, SPEED_LIST_HA
from unittest.mock import MagicMock, patch
from libdyson.const import DEVICE_TYPE_PURE_COOL_LINK
import pytest
from custom_components.dyson_local.const import CONF_CREDENTIAL, CONF_DEVICE_TYPE, CONF_SERIAL
from homeassistant.core import HomeAssistant
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_NAME, STATE_ON
from homeassistant.components.fan import ATTR_OSCILLATING, ATTR_SPEED, ATTR_SPEED_LIST, SERVICE_SET_SPEED, SPEED_HIGH, SPEED_LOW, SPEED_MEDIUM, DOMAIN as FAN_DOMAIN
from homeassistant.helpers import entity_registry
from tests.common import MockConfigEntry
from custom_components.dyson_local import DOMAIN
from libdyson import DEVICE_TYPE_PURE_COOL, DysonPureCool, DysonPureCoolLink
from . import NAME, SERIAL, CREDENTIAL, HOST, MODULE, get_base_device

DEVICE_TYPE = DEVICE_TYPE_PURE_COOL

ENTITY_ID = f"fan.{NAME}"


@pytest.fixture(
    params=[
        (DysonPureCool, DEVICE_TYPE_PURE_COOL),
        (DysonPureCoolLink, DEVICE_TYPE_PURE_COOL_LINK),
    ]
)
def device(request: pytest.FixtureRequest) -> DysonFanDevice:
    device = get_base_device(request.param[0], request.param[1])
    device.is_on = True
    device.speed = 5
    device.auto_mode = False
    device.oscillation = True
    with patch(f"{MODULE}._async_get_platforms", return_value=["fan"]):
        yield device


async def test_state(hass: HomeAssistant, setup_entry):
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    attributes = state.attributes
    assert attributes[ATTR_SPEED] == SPEED_MEDIUM
    assert attributes[ATTR_SPEED_LIST] == SPEED_LIST_HA
    assert attributes[ATTR_DYSON_SPEED] == 5
    assert attributes[ATTR_DYSON_SPEED_LIST] == SPEED_LIST_DYSON
    assert attributes[ATTR_AUTO_MODE] == False
    assert attributes[ATTR_OSCILLATING] == True

    er = await entity_registry.async_get_registry(hass)
    assert er.async_get(ENTITY_ID).unique_id == SERIAL

@pytest.mark.parametrize(
    "service,service_data,command,command_args",
    [
        ("turn_on", {}, "turn_on", []),
        ("turn_on", {ATTR_SPEED: SPEED_HIGH}, "set_speed", [10]),
        ("turn_off", {}, "turn_off", []),
        ("set_speed", {ATTR_SPEED: SPEED_LOW}, "set_speed", [4]),
        ("oscillate", {ATTR_OSCILLATING: True}, "enable_oscillation", []),
        ("oscillate", {ATTR_OSCILLATING: False}, "disable_oscillation", []),
    ]
)
async def test_command(hass: HomeAssistant, device: DysonFanDevice, setup_entry, service: str, service_data: dict, command: str, command_args: list):
    service_data[ATTR_ENTITY_ID] = ENTITY_ID
    await hass.services.async_call(FAN_DOMAIN, service, service_data, blocking=True)
    func = getattr(device, command)
    func.assert_called_once_with(*command_args)


@pytest.mark.parametrize(
    "service,service_data,command,command_args",
    [
        (SERVICE_SET_DYSON_SPEED, {ATTR_DYSON_SPEED: 3}, "set_speed", [3]),
        (SERVICE_SET_AUTO_MODE, {ATTR_AUTO_MODE: True}, "enable_auto_mode", []),
        (SERVICE_SET_AUTO_MODE, {ATTR_AUTO_MODE: False}, "disable_auto_mode", []),
    ]
)
async def test_service(hass: HomeAssistant, device: DysonFanDevice, setup_entry, service: str, service_data: dict, command: str, command_args: list):
    service_data[ATTR_ENTITY_ID] = ENTITY_ID
    await hass.services.async_call(DOMAIN, service, service_data, blocking=True)
    func = getattr(device, command)
    func.assert_called_once_with(*command_args)
