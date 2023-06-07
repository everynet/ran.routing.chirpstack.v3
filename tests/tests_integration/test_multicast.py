import asyncio
import secrets
from typing import Any

import pylorawan
import pytest

from lib.traffic.models import DownlinkDeviceContext, DownlinkResult, DownlinkResultStatus

from .conftest import ChirpStackExtendedApi, ChirpstackTrafficRouter, MQTTClient
from .lorawan import generate_data_message, generate_downlink, generate_join_request, UplinkMaker


@pytest.mark.integration
@pytest.mark.multicast
@pytest.mark.parametrize(
    "device_profile", [{"name": "test-multicast-abp-service-profile", "supports_class_c": True}], indirect=True
)
@pytest.mark.parametrize("application", [{"name": "test-multicast-abp-application"}], indirect=True)
async def test_abp_multicast(
    chirpstack_api: ChirpStackExtendedApi,
    chirpstack_router: ChirpstackTrafficRouter,
    mqtt_client: MQTTClient,
    multicast_group: dict[str, Any],
    device_abp: dict[str, Any],
    gateway: dict[str, Any],
    make_uplink: UplinkMaker,
):
    # Router must be synced before operations (because device may be added after creating router)
    await chirpstack_router.force_sync()
    # Starting ran-router downstream listening loop
    stop = asyncio.Event()
    listener = asyncio.create_task(chirpstack_router.run(stop))

    device = device_abp

    # Adding device to multicast group
    await chirpstack_api.add_device_to_multicast_group(multicast_group["id"], device["dev_eui"])

    # First step - send and verify basic uplink-downlink chain, ta make device active in ChirpStack.
    # This step is equal to "test_abp_uplink" from "test_upstream.py"
    message = generate_data_message(
        # The device derives FNwkSIntKey & AppSKey from the NwkKey
        device["app_s_key"],
        device["nwk_s_enc_key"],
        device["dev_addr"],
        secrets.token_bytes(6),
        confirmed=False,
        f_cnt=0,
    )
    uplink = make_uplink(message)
    uplink_ack = await chirpstack_router.handle_upstream(uplink)
    downlink = await chirpstack_router.downstream_rx.get()
    await chirpstack_router.handle_downstream_result(
        DownlinkResult(downlink_id=downlink.downlink_id, status=DownlinkResultStatus.OK)
    )

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

    # Second step - enqueue multicast downlink for group, and verify, is it handled by bridge
    lora_downlink = generate_downlink(
        dev_addr=multicast_group["mc_addr"],
        app_s_key=multicast_group["mc_app_s_key"],
        nwk_s_key=multicast_group["mc_nwk_s_key"],
        frm_payload=b"hello",
    )
    f_port = 2
    await chirpstack_api.enqueue_multicast_downlink(
        group_id=multicast_group["id"],
        f_port=f_port,
        data=lora_downlink.generate(),
    )
    downlink = await chirpstack_router.downstream_rx.get()

    assert isinstance(downlink.device_ctx, DownlinkDeviceContext.Multicast)
    assert downlink.device_ctx.multicast_addr == multicast_group["mc_addr"]

    downlink_payload = pylorawan.message.PHYPayload.parse(downlink.payload)
    assert downlink_payload.mhdr.mtype == pylorawan.message.MType.UnconfirmedDataDown
    assert downlink_payload.payload.f_port == f_port
    assert downlink_payload.payload.fhdr.dev_addr == int(multicast_group["mc_addr"], 16)

    # Terminating listener
    stop.set()
    await listener


@pytest.mark.integration
@pytest.mark.multicast
@pytest.mark.parametrize(
    "device_profile",
    [{"name": "test-multicast-otaa-service-profile", "supports_class_c": True, "supports_join": True}],
    indirect=True,
)
@pytest.mark.parametrize("application", [{"name": "test-multicast-otaa-application"}], indirect=True)
async def test_otaa_multicast(
    chirpstack_api: ChirpStackExtendedApi,
    chirpstack_router: ChirpstackTrafficRouter,
    mqtt_client: MQTTClient,
    multicast_group: dict[str, Any],
    device_otaa: dict[str, Any],
    gateway: dict[str, Any],
    make_uplink: UplinkMaker,
):
    # Router must be synced before operations (because device may be added after creating router)
    await chirpstack_router.force_sync()
    # Starting ran-router downstream listening loop
    stop = asyncio.Event()
    listener = asyncio.create_task(chirpstack_router.run(stop))

    device = device_otaa

    # Adding device to multicast group
    await chirpstack_api.add_device_to_multicast_group(multicast_group["id"], device["dev_eui"])

    # First step - perform join for device
    message = generate_join_request(
        device_otaa["nwk_key"],
        device_otaa["app_eui"],
        device_otaa["dev_eui"],
    )
    uplink = make_uplink(message)
    uplink_ack = await chirpstack_router.handle_upstream(uplink)
    downlink = await chirpstack_router.downstream_rx.get()
    await chirpstack_router.handle_downstream_result(
        DownlinkResult(downlink_id=downlink.downlink_id, status=DownlinkResultStatus.OK)
    )

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

    # Second step - send and verify basic uplink-downlink chain, ta make device active in ChirpStack.
    # This step is equal to "test_abp_uplink" from "test_upstream.py", but instead of using device keys and dev_addr,
    # already generated, we are fetching it from ChirpStack with "get_device_activation"
    device_activation = await chirpstack_api.get_device_activation(dev_eui=device["dev_eui"])
    dev_addr = device_activation.dev_addr
    message = generate_data_message(
        # The device derives FNwkSIntKey & AppSKey from the NwkKey
        device_activation.app_s_key,
        device_activation.nwk_s_enc_key,
        dev_addr,
        secrets.token_bytes(6),
        confirmed=False,
        f_cnt=0,
    )

    uplink = make_uplink(message)
    uplink_ack = await chirpstack_router.handle_upstream(uplink)
    downlink = await chirpstack_router.downstream_rx.get()
    await chirpstack_router.handle_downstream_result(
        DownlinkResult(downlink_id=downlink.downlink_id, status=DownlinkResultStatus.OK)
    )

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
    assert downlink_payload.payload.fhdr.dev_addr == int(dev_addr, 16)

    # Third step - enqueue multicast downlink for group, and verify, is it handled by bridge
    lora_downlink = generate_downlink(
        dev_addr=multicast_group["mc_addr"],
        app_s_key=multicast_group["mc_app_s_key"],
        nwk_s_key=multicast_group["mc_nwk_s_key"],
        frm_payload=b"hello",
    )
    f_port = 2
    await chirpstack_api.enqueue_multicast_downlink(
        group_id=multicast_group["id"],
        f_port=f_port,
        data=lora_downlink.generate(),
    )
    downlink = await chirpstack_router.downstream_rx.get()

    assert isinstance(downlink.device_ctx, DownlinkDeviceContext.Multicast)
    assert downlink.device_ctx.multicast_addr == multicast_group["mc_addr"]

    downlink_payload = pylorawan.message.PHYPayload.parse(downlink.payload)
    assert downlink_payload.mhdr.mtype == pylorawan.message.MType.UnconfirmedDataDown
    assert downlink_payload.payload.f_port == f_port
    assert downlink_payload.payload.fhdr.dev_addr == int(multicast_group["mc_addr"], 16)

    # Terminating listener
    stop.set()
    await listener
