import sys
from pathlib import Path
# Add repo root to relative path for later imports
sys.path.append(str(Path(__file__).parent.parent))

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

HOST = "192.168.1.233"

@pytest.mark.asyncio
async def test_setup_entry(hass: HomeAssistant):
    from homeassistant.const import CONF_HOST, CONF_PORT, CONF_NAME, CONF_ID

    from custom_components.amplipi.const import DOMAIN, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, CONF_API_PATH
    from custom_components.amplipi import async_setup_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Device",
        data={
            CONF_HOST: HOST,
            CONF_PORT: "80",
            CONF_VENDOR: CONF_VENDOR,
            CONF_VERSION: CONF_VERSION,
            CONF_WEBAPP: CONF_WEBAPP,
            CONF_API_PATH: CONF_API_PATH,
            CONF_NAME: CONF_NAME,
            CONF_ID: CONF_ID
        },
    )
    entry.add_to_hass(hass)


    result = await async_setup_entry(hass, entry)
    assert result is True
    assert DOMAIN in hass.data
