import secrets
import asyncio
import json
import base64
from time import sleep

import structlog
import pytest
import pylorawan

from lib.traffic_manager import UplinkRadioParams
from lib.traffic_manager import LoRaModulation
from lib.traffic_handler import LoRaWANTrafficHandler
from lib.traffic_manager import DownlinkResult
from lib import chirpstack
from lib import lorawan


logger = structlog.getLogger(__name__)


async def get_or_create_ns(chirpstack_api, name, server):
    async for network_server in chirpstack_api.get_network_servers(None):
        if network_server.server.lower() == server.lower():
            return network_server.id
    return await chirpstack_api.create_network_server(name=name, server=server)


@pytest.fixture
async def gateway(chirpstack_internal_api, chirpstack_api):
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
async def device_otaa(chirpstack_internal_api, chirpstack_api):
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


async def test_otaa_uplink(chirpstack_api, chirpstack_mqtt_client, device_otaa, gateway):
    devices = chirpstack.FlatDeviceList(chirpstack_api)
    await devices.refresh()
    device = devices.get_device(device_otaa["dev_eui"])

    # Init mqtt client
    mqtt_client_task = asyncio.create_task(chirpstack_mqtt_client.run())
    await chirpstack_mqtt_client.wait_for_connection(10)

    lorawan_traffic_handler = LoRaWANTrafficHandler(
        gateway["gateway_id"], chirpstack_mqtt_client=chirpstack_mqtt_client, devices=devices
    )

    lora_modulation = LoRaModulation(spreading=12, bandwidth=125000)
    uplink_radio_params = UplinkRadioParams(frequency=867100000, rssi=-120, snr=1.0, lora=lora_modulation)

    message = lorawan.generate_join_request(
        device_otaa["nwk_key"],
        device_otaa["app_eui"],
        device_otaa["dev_eui"],
    )
    downlink = await lorawan_traffic_handler.handle_join_request(message, uplink_radio_params)
    await asyncio.sleep(2)

    stream_frame_logs = chirpstack_api.stream_frame_logs(device_otaa["dev_eui"], 10)
    frame = await anext(stream_frame_logs)

    assert frame.uplink_frame.phy_payload_json
    join_request_frame = json.loads(frame.uplink_frame.phy_payload_json)
    assert join_request_frame["mhdr"]["mType"] == "JoinRequest"
    assert join_request_frame["macPayload"]["devEUI"] == device_otaa["dev_eui"]
    logger.info("JoinRequest messages confirmed!")

    mhdr = pylorawan.message.MHDR.parse(downlink.payload[:1])
    assert mhdr.mtype == pylorawan.message.MType.JoinAccept

    await device.refresh()

    join_accept = pylorawan.message.JoinAccept.parse(downlink.payload, bytes.fromhex(device.nwk_key))
    assert hex(join_accept.dev_addr)[2:].zfill(8) == device.dev_addr
    logger.info("JoinAccept messages confirmed!")

    # Downlink ACK
    await lorawan_traffic_handler.handle_dowstream_result(downlink, DownlinkResult.OK)

    frame = await anext(stream_frame_logs)

    assert frame.downlink_frame.phy_payload_json
    join_accept_frame = json.loads(frame.downlink_frame.phy_payload_json)
    assert join_accept_frame["mhdr"]["mType"] == "JoinAccept"

    # Test uplink for joined device
    message_payload = secrets.token_bytes(6)
    message = lorawan.generate_data_message(
        # The device derives FNwkSIntKey & AppSKey from the NwkKey
        device.nwk_key,
        device.nwk_s_enc_key,
        device.dev_addr,
        message_payload,
        confirmed=False,
        f_cnt=0,
    )
    downlink = await lorawan_traffic_handler.handle_uplink(message, uplink_radio_params)

    frame = await anext(stream_frame_logs)

    assert frame.uplink_frame.phy_payload_json
    unconfirmed_data_up_frame = json.loads(frame.uplink_frame.phy_payload_json)
    assert unconfirmed_data_up_frame["mhdr"]["mType"] == "UnconfirmedDataUp"
    assert unconfirmed_data_up_frame["macPayload"]["fhdr"]["devAddr"] == device.dev_addr
    logger.info("UnconfirmedDataUp aftre join confirmed!")

    # Downlink ACK
    await lorawan_traffic_handler.handle_dowstream_result(downlink, DownlinkResult.OK)

    mqtt_client_task.cancel()


async def test_abp_uplink(chirpstack_api, chirpstack_mqtt_client, device_abp, gateway):
    devices = chirpstack.FlatDeviceList(chirpstack_api)
    await devices.refresh()

    # Init mqtt client
    mqtt_client_task = asyncio.create_task(chirpstack_mqtt_client.run())
    await chirpstack_mqtt_client.wait_for_connection(10)

    lorawan_traffic_handler = LoRaWANTrafficHandler(
        gateway["gateway_id"], chirpstack_mqtt_client=chirpstack_mqtt_client, devices=devices
    )

    message_payload = secrets.token_bytes(6)
    message = lorawan.generate_data_message(
        device_abp["app_s_key"],
        device_abp["nwk_s_enc_key"],
        device_abp["dev_addr"],
        message_payload,
        confirmed=True,
        f_cnt=1,
    )

    lora_modulation = LoRaModulation(spreading=12, bandwidth=125000)
    uplink_radio_params = UplinkRadioParams(frequency=867100000, rssi=-120, snr=1.0, lora=lora_modulation)

    downlink = await lorawan_traffic_handler.handle_uplink(message, uplink_radio_params)

    stream_frame_logs = chirpstack_api.stream_frame_logs(device_abp["dev_eui"], 10)
    frame = await anext(stream_frame_logs)

    assert frame.uplink_frame.phy_payload_json
    uplink_frame = json.loads(frame.uplink_frame.phy_payload_json)
    uplink_frame_payload = base64.b64decode(uplink_frame["macPayload"]["frmPayload"][0]["bytes"])
    assert uplink_frame["mhdr"]["mType"] == "ConfirmedDataUp"
    assert uplink_frame["macPayload"]["fhdr"]["devAddr"] == device_abp["dev_addr"]
    assert message.payload.frm_payload == uplink_frame_payload

    phypayload = pylorawan.message.PHYPayload.parse(downlink.payload)
    assert phypayload.mhdr.mtype in [
        pylorawan.message.MType.ConfirmedDataDown,
        pylorawan.message.MType.UnconfirmedDataDown,
    ]
    assert phypayload.payload.fhdr.f_ctrl.ack == True

    # Downlink ack and down message test
    await lorawan_traffic_handler.handle_dowstream_result(downlink, DownlinkResult.OK)
    frame = await anext(stream_frame_logs)

    assert frame.downlink_frame.phy_payload_json
    downlink_frame = json.loads(frame.downlink_frame.phy_payload_json)
    assert downlink_frame["mhdr"]["mType"] == "UnconfirmedDataDown"

    mqtt_client_task.cancel()


async def generate_and_send_join_request(device, lorawan_traffic_handler):
    lora_modulation = LoRaModulation(spreading=12, bandwidth=125000)
    uplink_radio_params = UplinkRadioParams(frequency=867100000, rssi=-120, snr=1.0, lora=lora_modulation)

    message = lorawan.generate_join_request(
        device_otaa["nwk_key"],
        device_otaa["app_eui"],
        device_otaa["dev_eui"],
    )

    # Handle join request by ChirpStack and get downlink response
    downlink = await lorawan_traffic_handler.handle_join_request(message, uplink_radio_params)
    await device.refresh()

    mhdr = pylorawan.message.MHDR.parse(downlink.payload[:1])
    assert mhdr.mtype == pylorawan.message.MType.JoinAccept

    join_accept = pylorawan.message.JoinAccept.parse(downlink.payload, bytes.fromhex(device.nwk_key))
    assert hex(join_accept.dev_addr)[2:].zfill(8) == device.dev_addr
    logger.info("JoinAccept messages confirmed!")


async def test_join_raсe(chirpstack_api, chirpstack_mqtt_client, device_otaa, gateway):
    devices = chirpstack.FlatDeviceList(chirpstack_api)
    await devices.refresh()
    device = devices.get_device(device_otaa["dev_eui"])

    # Init mqtt client
    mqtt_client_task = asyncio.create_task(chirpstack_mqtt_client.run())
    await chirpstack_mqtt_client.wait_for_connection(10)

    lorawan_traffic_handler = LoRaWANTrafficHandler(
        gateway["gateway_id"], chirpstack_mqtt_client=chirpstack_mqtt_client, devices=devices
    )

    # Device is fresh new?
    assert device.dev_addr is None

    # Send join request #1
    await generate_and_send_join_request(device, lorawan_traffic_handler)

    await asyncio.sleep(2)

    # Send join request #2
    await generate_and_send_join_request(device, lorawan_traffic_handler)

    mqtt_client_task.cancel()
