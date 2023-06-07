import asyncio
import secrets
from typing import Any

import pylorawan
import pytest

from lib.traffic.models import DownlinkDeviceContext, DownlinkResult, DownlinkResultStatus, DownlinkTiming

from .conftest import ChirpStackExtendedApi, ChirpstackTrafficRouter
from .lorawan import generate_data_message, generate_join_request, UplinkMaker


@pytest.mark.integration
@pytest.mark.downstream
@pytest.mark.parametrize(
    "device_profile", [{"name": "test-downlink-abp-service-profile", "supports_class_c": True}], indirect=True
)
@pytest.mark.parametrize("application", [{"name": "test-downlink-abp-application"}], indirect=True)
async def test_abp_downstream(
    chirpstack_api: ChirpStackExtendedApi,
    chirpstack_router: ChirpstackTrafficRouter,
    device_abp: dict[str, Any],
    make_uplink: UplinkMaker,
):
    # Router must be synced before operations (because device may be added after creating router)
    await chirpstack_router.force_sync()
    # Starting ran-router downstream listening loop
    stop = asyncio.Event()
    listener = asyncio.create_task(chirpstack_router.run(stop))

    # First step - send and verify basic uplink-downlink chain, ta make device active in ChirpStack.
    # This step is equal to "test_abp_uplink" from "test_upstream.py"
    device = device_abp
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

    # Second step - enqueue downlink for device, and verify, is it handled by bridge
    f_port = 2
    await chirpstack_api.enqueue_downlink(dev_eui=device["dev_eui"], data=b"hello", confirmed=False, f_port=f_port)
    downlink = await chirpstack_router.downstream_rx.get()

    assert isinstance(downlink.timing, DownlinkTiming.Immediately)
    assert downlink.device_ctx.dev_eui == device["dev_eui"]
    downlink_payload = pylorawan.message.PHYPayload.parse(downlink.payload)
    assert downlink_payload.mhdr.mtype == pylorawan.message.MType.UnconfirmedDataDown
    assert downlink_payload.payload.f_port == f_port
    assert downlink_payload.payload.fhdr.dev_addr == int(device["dev_addr"], 16)

    # Terminating listener
    stop.set()
    await listener


@pytest.mark.integration
@pytest.mark.downstream
@pytest.mark.parametrize(
    "device_profile",
    [{"name": "test-downlink-otaa-service-profile", "supports_class_c": True, "supports_join": True}],
    indirect=True,
)
@pytest.mark.parametrize("application", [{"name": "test-downlink-otaa-application"}], indirect=True)
async def test_otaa_downstream(
    chirpstack_api: ChirpStackExtendedApi,
    chirpstack_router: ChirpstackTrafficRouter,
    device_otaa: dict[str, Any],
    make_uplink: UplinkMaker,
):
    # Router must be synced before operations (because device may be added after creating router)
    await chirpstack_router.force_sync()
    # Starting ran-router downstream listening loop
    stop = asyncio.Event()
    listener = asyncio.create_task(chirpstack_router.run(stop))

    # First step - perform join for device
    device = device_otaa
    message = generate_join_request(
        device["nwk_key"],
        device["app_eui"],
        device["dev_eui"],
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

    # Third step - enqueue downlink for device, and verify, is it handled by bridge
    f_port = 2
    await chirpstack_api.enqueue_downlink(dev_eui=device["dev_eui"], data=b"hello", confirmed=False, f_port=f_port)
    downlink = await chirpstack_router.downstream_rx.get()

    assert isinstance(downlink.timing, DownlinkTiming.Immediately)
    assert downlink.device_ctx.dev_eui == device["dev_eui"]
    downlink_payload = pylorawan.message.PHYPayload.parse(downlink.payload)
    assert downlink_payload.mhdr.mtype == pylorawan.message.MType.UnconfirmedDataDown
    assert downlink_payload.payload.f_port == f_port
    assert downlink_payload.payload.fhdr.dev_addr == int(dev_addr, 16)

    # Terminating listener
    stop.set()
    await listener
