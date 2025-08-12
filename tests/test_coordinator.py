import sys
from pathlib import Path
# Add repo root to relative path for later imports
sys.path.append(str(Path(__file__).parent.parent))

import pytest
from pytest_socket import enable_socket, socket_allow_hosts
import json
from homeassistant.core import HomeAssistant

HOST = "192.168.1.233"
PORT = 80

@pytest.mark.asyncio
async def test_coordinator_setup(hass: HomeAssistant):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from custom_components.amplipi.coordinator import AmpliPiDataClient

    # enable usage of requests with provided host
    enable_socket()
    socket_allow_hosts([HOST], allow_unix_socket=True)

    with open(Path(__file__).parent / "house.json", "r", encoding="utf-8") as f:
        base_data = json.load(f)

    with open(Path(__file__).parent / "data.json", "r", encoding="utf-8") as f:
        expected_data = json.load(f)

    data_client = AmpliPiDataClient(
        hass=hass,
        endpoint=f"http://{HOST}:{PORT}/api/",
        timeout=10,
        http_session=async_get_clientsession(hass)
    )

    # Process data, convert to json for apples to apples comparison
    data = await data_client.set_data(base_data)
    result = data.model_dump_json()

    assert result == expected_data, "transformed data does not match expected data"
