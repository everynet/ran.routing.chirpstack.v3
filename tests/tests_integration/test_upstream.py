import asyncio
import hashlib
import secrets
import uuid
from typing import Any

import pylorawan
import pytest

from lib.chirpstack.devices import ApplicationDeviceList
from lib.chirpstack.multicast_groups import ApplicationMulticastGroupList
from lib.traffic.chirpstack import ChirpstackTrafficRouter
from lib.traffic.models import DownlinkDeviceContext, LoRaModulation, Uplink, UpstreamRadio

from .conftest import ChirpStackExtendedApi, ChirpStackInternalAPI, MQTTClient
from .lorawan import generate_data_message, generate_join_request


async def get_or_create_ns(chirpstack_api: ChirpStackExtendedApi, name, server):
    async for network_server in chirpstack_api.get_network_servers(None):
        if network_server.server.lower() == server.lower():
            return network_server.id
    return await chirpstack_api.create_network_server(name=name, server=server)


@pytest.fixture
async def gateway(chirpstack_internal_api: ChirpStackInternalAPI, chirpstack_api: ChirpStackExtendedApi):
    user_profile = await chirpstack_internal_api.profile()
    organization_id = int(user_profile["organizations"][0]["organizationID"])

    network_server_id = await get_or_create_ns(chirpstack_api, "test-chirpstack-api", "chirpstack-network-server:8000")
    service_profile_id = await chirpstack_api.create_service_profile(
        name="test-chirpstack-service-profile", organization_id=organization_id, network_server_id=network_server_id
    )

    gateway_id = secrets.token_hex(8)
    await chirpstack_api.create_gateway(
        gateway_id,
        "pytest-gw-" + gateway_id,
        "pytest gateway",
        network_server_id,
        organization_id=organization_id,
        service_profile_id=service_profile_id,
    )
    yield {"gateway_id": gateway_id, "service_profile_id": service_profile_id, "network_server_id": network_server_id}

    await chirpstack_api.delete_gateway(gateway_id)
    await chirpstack_api.delete_service_profile(service_profile_id)


@pytest.fixture
async def device_otaa(chirpstack_internal_api: ChirpStackInternalAPI, chirpstack_api: ChirpStackExtendedApi):
    user_profile = await chirpstack_internal_api.profile()
    organization_id = int(user_profile["organizations"][0]["organizationID"])

    network_server_id = await get_or_create_ns(chirpstack_api, "test-otaa-dev", "chirpstack-network-server:8000")

    service_profile_id = await chirpstack_api.create_service_profile(
        name="test-otaa-service-profile", organization_id=organization_id, network_server_id=network_server_id
    )
    device_profile_id = await chirpstack_api.create_device_profile(
        name="test-otaa-service-profile",
        organization_id=organization_id,
        network_server_id=network_server_id,
        supports_join=True,
    )
    application_id = await chirpstack_api.create_application(
        name="test-otaa-app-" + service_profile_id,
        organization_id=organization_id,
        service_profile_id=service_profile_id,
    )

    device_params = {
        "app_eui": "0" * 16,
        "dev_eui": secrets.token_hex(8),
        "name": "test-uplink-dev-abp",
        "application_id": application_id,
        "device_profile_id": device_profile_id,
        "skip_f_cnt_check": True,
        "tags": {},
    }
    dev_eui = await chirpstack_api.create_device(**device_params)

    device_keys = {
        "dev_eui": dev_eui,
        "nwk_key": secrets.token_hex(16),
        "app_key": "0" * 32,
    }
    await chirpstack_api.create_device_keys(**device_keys)

    yield dict(list(device_params.items()) + list(device_keys.items()))

    await chirpstack_api.delete_device(dev_eui)
    await chirpstack_api.delete_application(application_id)
    await chirpstack_api.delete_device_profile(device_profile_id)
    await chirpstack_api.delete_service_profile(service_profile_id)


@pytest.fixture
async def device_abp(chirpstack_internal_api, chirpstack_api):
    user_profile = await chirpstack_internal_api.profile()
    organization_id = int(user_profile["organizations"][0]["organizationID"])

    network_server_id = await get_or_create_ns(chirpstack_api, "test-abp-dev", "chirpstack-network-server:8000")

    service_profile_id = await chirpstack_api.create_service_profile(
        name="test-abp-service-profile", organization_id=organization_id, network_server_id=network_server_id
    )
    device_profile_id = await chirpstack_api.create_device_profile(
        name="test-abp-service-profile",
        organization_id=organization_id,
        network_server_id=network_server_id,
        supports_join=False,
    )
    application_id = await chirpstack_api.create_application(
        name="test-abp-app-" + service_profile_id,
        organization_id=organization_id,
        service_profile_id=service_profile_id,
    )

    device_params = {
        "dev_eui": secrets.token_hex(8),
        "name": "test-abp-uplink-dev-abp",
        "application_id": application_id,
        "device_profile_id": device_profile_id,
        "skip_f_cnt_check": True,
        "tags": {},
    }
    dev_eui = await chirpstack_api.create_device(**device_params)

    device_keys = {
        "dev_eui": dev_eui,
        "dev_addr": secrets.token_hex(4),
        "app_s_key": secrets.token_hex(16),
        "nwk_s_enc_key": secrets.token_hex(16),
    }
    await chirpstack_api.activate_device(**device_keys)

    yield dict(list(device_params.items()) + list(device_keys.items()))

    await chirpstack_api.delete_device(dev_eui)
    await chirpstack_api.delete_application(application_id)
    await chirpstack_api.delete_device_profile(device_profile_id)
    await chirpstack_api.delete_service_profile(service_profile_id)


@pytest.fixture
def chirpstack_router_factory(chirpstack_api: ChirpStackExtendedApi, chirpstack_mqtt_client: MQTTClient):
    async def make_router(
        app_id: int, gw_id: str, sync_devices: bool = True, sync_multicast: bool = False
    ) -> ChirpstackTrafficRouter:
        devices = ApplicationDeviceList(chirpstack_api, application_id=app_id)
        if sync_devices:
            await devices.sync_from_remote()

        multicast_groups = ApplicationMulticastGroupList(chirpstack_api, application_id=app_id)
        if sync_multicast:
            await multicast_groups.sync_from_remote()

        chirpstack_router = ChirpstackTrafficRouter(
            gateway_mac=gw_id,
            chirpstack_mqtt_client=chirpstack_mqtt_client,
            devices=devices,
            multicast_groups=multicast_groups,
        )

        return chirpstack_router

    return make_router


@pytest.mark.integration
async def test_otaa_join(
    chirpstack_router_factory,
    gateway: dict[str, str],
    device_otaa: dict[str, Any],
):
    device = device_otaa
    lora_modulation = LoRaModulation(spreading=12, bandwidth=125000)
    radio_params = UpstreamRadio(frequency=867100000, rssi=-120, snr=1.0, lora=lora_modulation)

    message = generate_join_request(
        device_otaa["nwk_key"],
        device_otaa["app_eui"],
        device_otaa["dev_eui"],
    )
    uplink = Uplink(
        uplink_id=str(uuid.UUID(bytes=hashlib.md5(message.generate()).digest())),
        payload=message,
        used_mic=int.from_bytes(message.mic, byteorder="little"),  # TODO: ensure byte order
        radio=radio_params,
    )

    chirpstack_router: ChirpstackTrafficRouter = await chirpstack_router_factory(
        device["application_id"], gateway["gateway_id"]
    )
    # Starting ran-router downstream listening just for one downlink
    stop = asyncio.Event()
    listener = asyncio.create_task(chirpstack_router.run(stop))

    uplink_ack = await chirpstack_router.handle_upstream(uplink)
    downlink = await chirpstack_router.downstream_rx.get()

    # Terminating listener
    stop.set()
    await listener

    # # Printing packets (debug)
    # print(repr(uplink))
    # print(repr(uplink_ack))
    # print(repr(downlink))

    assert uplink.used_mic == uplink_ack.mic
    assert uplink.context_id == uplink_ack.context_id
    assert uplink.uplink_id == uplink_ack.uplink_id
    assert uplink_ack.dev_eui == device["dev_eui"]

    assert uplink.context_id == downlink.context_id
    assert isinstance(downlink.device_ctx, DownlinkDeviceContext.Regular)
    assert downlink.device_ctx.dev_eui == device["dev_eui"]
    assert downlink.device_ctx.target_dev_addr is not None

    mhdr = pylorawan.message.MHDR.parse(downlink.payload[:1])
    assert mhdr.mtype == pylorawan.message.MType.JoinAccept

    join_accept_payload = pylorawan.message.JoinAccept.parse(downlink.payload, bytes.fromhex(device["nwk_key"]))
    assert join_accept_payload.dev_addr == int(downlink.device_ctx.target_dev_addr, 16)


@pytest.mark.integration
async def test_abp_uplink(
    chirpstack_router_factory,
    gateway: dict[str, str],
    device_abp: dict[str, Any],
):
    device = device_abp
    lora_modulation = LoRaModulation(spreading=12, bandwidth=125000)
    radio_params = UpstreamRadio(frequency=867100000, rssi=-120, snr=1.0, lora=lora_modulation)

    # Test uplink for joined device
    message_payload = secrets.token_bytes(6)
    message = generate_data_message(
        # The device derives FNwkSIntKey & AppSKey from the NwkKey
        device["app_s_key"],
        device["nwk_s_enc_key"],
        device["dev_addr"],
        message_payload,
        confirmed=False,
        f_cnt=0,
    )
    uplink = Uplink(
        uplink_id=str(uuid.UUID(bytes=hashlib.md5(message.generate()).digest())),
        payload=message,
        used_mic=int.from_bytes(message.mic, byteorder="little"),  # TODO: ensure byte order
        radio=radio_params,
    )

    chirpstack_router = await chirpstack_router_factory(device["application_id"], gateway["gateway_id"])
    # Starting ran-router downstream listening just for one downlink
    stop = asyncio.Event()
    listener = asyncio.create_task(chirpstack_router.run(stop))

    uplink_ack = await chirpstack_router.handle_upstream(uplink)
    downlink = await chirpstack_router.downstream_rx.get()

    # Terminating listener
    stop.set()
    await listener

    # # Printing packets (debug)
    # print(repr(uplink))
    # print(repr(uplink_ack))
    # print(repr(downlink))

    assert uplink.used_mic == uplink_ack.mic
    assert uplink.context_id == uplink_ack.context_id
    assert uplink.uplink_id == uplink_ack.uplink_id
    assert uplink_ack.dev_eui == device["dev_eui"]

    assert uplink.context_id == downlink.context_id
    assert isinstance(downlink.device_ctx, DownlinkDeviceContext.Regular)
    assert downlink.device_ctx.dev_eui == device["dev_eui"]
    assert downlink.device_ctx.target_dev_addr is None

    downlink_payload = pylorawan.message.PHYPayload.parse(downlink.payload)
    assert downlink_payload.mhdr.mtype == pylorawan.message.MType.UnconfirmedDataDown
    assert downlink_payload.payload.fhdr.dev_addr == int(device["dev_addr"], 16)
