import uuid

import pytest
import grpc

from lib import mqtt
from lib import chirpstack


def pytest_configure(config):
    if config.pluginmanager.getplugin("asyncio"):
        config.option.asyncio_mode = "auto"


@pytest.fixture
async def chirpstack_internal_api():
    api = chirpstack.ChirpStackInternalAPI("http://localhost:8080", "admin", "admin")
    await api.authenticate()
    return api


@pytest.fixture
async def chirpstack_mqtt_client():
    mqtt_client = mqtt.MQTTClient("mqtt://localhost:1883", client_id=uuid.uuid4().hex)
    yield mqtt_client


@pytest.fixture
async def chirpstack_api(chirpstack_internal_api):
    grpc_channel = grpc.aio.insecure_channel("localhost:8080")
    chirpstack_api = chirpstack.ChirpStackAPI(grpc_channel, chirpstack_internal_api._jwt_token)
    return chirpstack_api
