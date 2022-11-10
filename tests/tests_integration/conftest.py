import asyncio
import uuid

import grpc
import pytest

from lib.chirpstack import ChirpStackInternalAPI
from lib.mqtt import MQTTClient

from .ext_chirpstack_api import ChirpStackExtendedApi


@pytest.fixture
async def chirpstack_internal_api() -> ChirpStackInternalAPI:
    try:
        api = ChirpStackInternalAPI("http://localhost:8080", "admin", "admin")
        await api.authenticate()
    except Exception as e:
        return pytest.exit(f"Could not connect to internal api: {e}")
    return api


@pytest.fixture
async def chirpstack_mqtt_client() -> MQTTClient:
    # TODO: shutdown tests if could not connect to mqtt
    mqtt_client = MQTTClient("mqtt://localhost:1883", client_id=uuid.uuid4().hex)
    stop = asyncio.Event()
    stop.clear()

    client_task = asyncio.create_task(mqtt_client.run(stop))

    yield mqtt_client

    stop.set()
    await client_task


@pytest.fixture
async def chirpstack_api(chirpstack_internal_api) -> ChirpStackExtendedApi:
    try:
        grpc_channel = grpc.aio.insecure_channel("localhost:8080")
        chirpstack_api = ChirpStackExtendedApi(grpc_channel, chirpstack_internal_api._jwt_token)
    except Exception as e:
        return pytest.exit(f"Could not connect to grpc api: {e}")
    return chirpstack_api
