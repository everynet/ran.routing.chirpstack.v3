import asyncio
import uuid
from contextlib import suppress
from typing import Optional

import pylorawan
import structlog
from chirpstack_api.common import common_pb2
from chirpstack_api.gw import gw_pb2

from lib.chirpstack.devices import Device

from .. import cache, chirpstack, mqtt
from ..logging_conf import lazy_protobuf_fmt
from .models import (
    Downlink,
    DownlinkDeviceContext,
    DownlinkRadioParams,
    DownlinkResult,
    DownlinkResultStatus,
    DownlinkTiming,
    LoRaModulation,
    Uplink,
    UplinkAck,
    UplinkReject,
    UplinkRejectReason,
)

logger = structlog.getLogger(__name__)


class ChirpstackTrafficRouter:
    def __init__(
        self,
        gateway_mac: str,
        chirpstack_mqtt_client: mqtt.MQTTClient,
        devices: chirpstack.DeviceList,
        multicast_groups: chirpstack.MulticastGroupList,
    ):
        self.gateway_mac = gateway_mac
        self.devices = devices
        self.chirpstack_mqtt_client = chirpstack_mqtt_client
        self.multicast_groups = multicast_groups

        # Track devices, who send uplinks
        self._chirpstack_context_to_device: cache.Cache[str, Device] = cache.Cache(ttl=100)
        # Communication
        self._downlinks_from_chirpstack: asyncio.Queue[Downlink] = asyncio.Queue()

        # Storing amount of not-processed ack's per downlink_id
        self._ignored_downlinks_count: cache.Cache[str, int] = cache.Cache(ttl=100)

        # Storing downlink frame tokens, used as fallback for chirpstack internal context
        self._downlink_id_to_frame_token: cache.Cache[str, int] = cache.Cache(ttl=100)

    @property
    def downstream_rx(self):
        return self._downlinks_from_chirpstack

    def _create_chirpstack_uplink(self, uplink: Uplink) -> gw_pb2.UplinkFrame:
        radio_params = uplink.radio

        location = common_pb2.Location()
        location.latitude = 0.0
        location.longitude = 0.0
        location.altitude = 0.0

        rx_info = gw_pb2.UplinkRXInfo()
        rx_info.gateway_id = bytes.fromhex(self.gateway_mac)
        rx_info.rssi = int(radio_params.rssi)
        rx_info.lora_snr = radio_params.snr
        rx_info.channel = 0
        rx_info.rf_chain = 0
        rx_info.board = 1
        rx_info.antenna = 1
        rx_info.location.MergeFrom(location)
        rx_info.uplink_id = uuid.UUID(hex=uplink.uplink_id).bytes
        rx_info.context = uuid.UUID(hex=uplink.context_id).bytes
        rx_info.crc_status = gw_pb2.CRC_OK

        lora_mod_info = gw_pb2.LoRaModulationInfo()
        lora_mod_info.bandwidth = radio_params.lora.bandwidth // 1000
        lora_mod_info.spreading_factor = radio_params.lora.spreading
        lora_mod_info.code_rate = "4/5"
        lora_mod_info.polarization_inversion = True

        tx_info = gw_pb2.UplinkTXInfo()
        tx_info.frequency = radio_params.frequency
        tx_info.modulation = common_pb2.LORA
        tx_info.lora_modulation_info.MergeFrom(lora_mod_info)

        chirpstack_uplink = gw_pb2.UplinkFrame()
        chirpstack_uplink.phy_payload = uplink.payload.generate()
        chirpstack_uplink.rx_info.MergeFrom(rx_info)
        chirpstack_uplink.tx_info.MergeFrom(tx_info)

        return chirpstack_uplink

    def _create_ran_downlinks(self, chirpstack_downlink_frame: gw_pb2.DownlinkFrame) -> list[Downlink]:
        downlink_id = str(uuid.UUID(bytes=chirpstack_downlink_frame.downlink_id))
        logger.debug("Downstream frame token stored", downlink_id=downlink_id, token=chirpstack_downlink_frame.token)
        self._downlink_id_to_frame_token.set(downlink_id, chirpstack_downlink_frame.token)

        downlinks = []
        for item in chirpstack_downlink_frame.items:
            if item.tx_info.modulation != common_pb2.Modulation.LORA:
                raise Exception("Supported only LoRa modulation")

            lora_modulation = LoRaModulation(
                bandwidth=item.tx_info.lora_modulation_info.bandwidth * 1000,
                spreading=item.tx_info.lora_modulation_info.spreading_factor,
            )

            downlink_radio_params = DownlinkRadioParams(frequency=item.tx_info.frequency, lora=lora_modulation)

            timing: DownlinkTiming.TimingBase
            if item.tx_info.timing == gw_pb2.DownlinkTiming.DELAY:
                # Class A
                timing = DownlinkTiming.Delay(seconds=item.tx_info.delay_timing_info.delay.seconds)
            elif item.tx_info.timing == gw_pb2.DownlinkTiming.GPS_EPOCH:
                # Class B
                seconds = item.tx_info.gps_epoch_timing_info.time_since_gps_epoch.seconds
                nanos = item.tx_info.gps_epoch_timing_info.time_since_gps_epoch.nanos
                # tmms measured in milliseconds
                timing = DownlinkTiming.GpsTime(tmms=seconds * 10**3 + nanos // 10**6)
            elif item.tx_info.timing == gw_pb2.DownlinkTiming.IMMEDIATELY:
                # Class C
                timing = DownlinkTiming.Immediately()

            if item.tx_info.context:
                chirpstack_context_id = str(uuid.UUID(bytes=item.tx_info.context))
            else:
                logger.debug("Downlink context_id missing, generating new one")
                chirpstack_context_id = str(uuid.uuid4())

            downlink = Downlink(
                downlink_id=downlink_id,
                context_id=chirpstack_context_id,
                payload=item.phy_payload,
                radio=downlink_radio_params,
                timing=timing,
                # We are don't know device context on this step. It MUST be populated later.
                device_ctx=None,
            )
            downlinks.append(downlink)
        return downlinks

    def _check_mic(self, phy_payload: pylorawan.message.PHYPayload, nwk_key: bytes) -> bool:
        return pylorawan.common.verify_mic_phy_payload(phy_payload, nwk_key)

    async def _send_uplink_to_chirpstack(self, uplink: Uplink):
        chirpstack_uplink = self._create_chirpstack_uplink(uplink)
        chirpstack_uplink_topic = "gateway/{}/event/up".format(self.gateway_mac)
        await self.chirpstack_mqtt_client.publish(chirpstack_uplink_topic, chirpstack_uplink.SerializeToString())
        logger.debug("Uplink message forwarded to chirpstack", chirpstack_uplink=lazy_protobuf_fmt(chirpstack_uplink))

    async def handle_upstream(self, uplink: Uplink) -> UplinkReject | UplinkAck:
        phy_payload = uplink.payload
        if phy_payload.mhdr.mtype == pylorawan.message.MType.JoinRequest:
            return await self._handle_join_request(uplink)
        elif phy_payload.mhdr.mtype in (
            pylorawan.message.MType.ConfirmedDataUp,
            pylorawan.message.MType.UnconfirmedDataUp,
        ):
            return await self._handle_uplink(uplink)
        else:
            logger.error(f"Unknown message type: {phy_payload.mhdr.mtype!r}")
            return UplinkReject(uplink_id=uplink.uplink_id, reason=UplinkRejectReason.NotSupported)

    async def _handle_uplink(self, uplink: Uplink) -> UplinkReject | UplinkAck:
        dev_addr = f"{uplink.payload.payload.fhdr.dev_addr:08x}"

        device = self.devices.get_device_by_dev_addr(dev_addr)
        if not device:
            logger.warning("handle_uplink: device not found", dev_addr=dev_addr)
            return UplinkReject(uplink_id=uplink.uplink_id, reason=UplinkRejectReason.DeviceNotFound)

        if not self._check_mic(uplink.payload, bytes.fromhex(device.nwk_s_enc_key)):  # type: ignore
            return UplinkReject(uplink_id=uplink.uplink_id, reason=UplinkRejectReason.MicChallengeFail)

        logger.debug(f"Mic challenge successful, handling {uplink.payload.mhdr.mtype!r} message.", uplink=repr(uplink))
        self._chirpstack_context_to_device.set(uplink.context_id, device)
        await self._send_uplink_to_chirpstack(uplink)
        uplink_ack = UplinkAck(uplink_id=uplink.uplink_id, mic=uplink.used_mic, dev_eui=device.dev_eui)
        logger.debug("UplinkAck message created", uplink_ack=repr(uplink_ack))
        return uplink_ack

    async def _handle_join_request(self, uplink: Uplink) -> UplinkReject | UplinkAck:
        dev_eui = f"{uplink.payload.payload.dev_eui:016x}"

        device = self.devices.get_device_by_dev_eui(dev_eui)
        if not device:
            logger.warning("handle_join_request: device not found", dev_eui=dev_eui)
            return UplinkReject(uplink_id=uplink.uplink_id, reason=UplinkRejectReason.DeviceNotFound)

        if device.nwk_key is None:
            logger.error("Join cannot be processed, nwk_key not set!", dev_eui=dev_eui)
            return UplinkReject(uplink_id=uplink.uplink_id, reason=UplinkRejectReason.InternalError)

        if not self._check_mic(uplink.payload, bytes.fromhex(device.nwk_key)):  # type: ignore
            return UplinkReject(uplink_id=uplink.uplink_id, reason=UplinkRejectReason.MicChallengeFail)

        logger.debug(f"Mic challenge successful, handling {uplink.payload.mhdr.mtype!r} message.", uplink=repr(uplink))
        self._chirpstack_context_to_device.set(uplink.context_id, device)
        await self._send_uplink_to_chirpstack(uplink)
        uplink_ack = UplinkAck(uplink_id=uplink.uplink_id, mic=uplink.used_mic, dev_eui=device.dev_eui)
        logger.debug("UplinkAck message created", uplink_ack=repr(uplink_ack))
        return uplink_ack

    async def _send_tx_ack(self, downlink_id: str, ack_statuses: list[gw_pb2.TxAckStatus]):
        downlink_tx_ack = gw_pb2.DownlinkTXAck()
        downlink_tx_ack.gateway_id = bytes.fromhex(self.gateway_mac)
        downlink_tx_ack.downlink_id = uuid.UUID(hex=downlink_id).bytes
        # NOTE: Token added as fallback, if chirpstack misses own local context.
        # https://github.com/brocaar/chirpstack-network-server/blob/d11e570763586b9366b995d679463877d9512e15/internal/downlink/ack/ack.go#L265
        if (token := self._downlink_id_to_frame_token.get(downlink_id, None)) is not None:
            logger.debug("Downstream frame token obtained from memory", downlink_id=downlink_id, token=token)
            downlink_tx_ack.token = token
        else:
            logger.warning("Downstream frame token missed", downlink_id=downlink_id)

        for ack_status in ack_statuses:
            item = gw_pb2.DownlinkTXAckItem(status=ack_status)
            downlink_tx_ack.items.append(item)

        chirpstack_downlink_ack_topic = "gateway/{}/event/ack".format(self.gateway_mac)
        await self.chirpstack_mqtt_client.publish(chirpstack_downlink_ack_topic, downlink_tx_ack.SerializeToString())
        logger.debug("DownlinkTXAck forwarded to chirpstack", chirpstack_tx_ack=lazy_protobuf_fmt(downlink_tx_ack))

    async def handle_downstream_result(self, downlink_result: DownlinkResult) -> None:
        logger.debug("Handling downstream result", downlink_result=repr(downlink_result))
        if downlink_result.status == DownlinkResultStatus.OK:
            ack_status = gw_pb2.TxAckStatus.OK
        elif downlink_result.status == DownlinkResultStatus.TOO_LATE:
            ack_status = gw_pb2.TxAckStatus.TOO_LATE
        elif downlink_result.status == DownlinkResultStatus.ERROR:
            ack_status = gw_pb2.TxAckStatus.INTERNAL_ERROR
        else:
            logger.error(
                f"Unknown downlink result status: {downlink_result.status}", downlink_id=downlink_result.downlink_id
            )
            return

        # NOTE: "ack_statuses" list must has the same length as the request and indicates which
        #   downlink frame has been emitted of the requested list (or why it failed).
        #   All downlinks, except first one are ignored by bridge in its current state.
        # EXTRA: https://github.com/brocaar/chirpstack-api/blob/master/protobuf/gw/gw.proto#L414
        # TODO: Consume all Ack's from ran-routing, when it supports multiple TxWindow's.
        acks_to_ignore: int = self._ignored_downlinks_count.get(downlink_result.downlink_id, 0)  # type: ignore
        ack_statuses = [ack_status] + [gw_pb2.TxAckStatus.IGNORED for _ in range(acks_to_ignore)]
        await self._send_tx_ack(downlink_result.downlink_id, ack_statuses)

    async def _fetch_downlink_device_context(self, downlink: Downlink) -> Optional[DownlinkDeviceContext.ContextBase]:
        device: Device | None = self._chirpstack_context_to_device.get(downlink.context_id, None)
        mhdr = pylorawan.message.MHDR.parse(downlink.payload[:1])

        # First branch - JoinAccept. It will use device data, stored in cache after handling uplink.
        if mhdr.mtype == pylorawan.message.MType.JoinAccept:
            if not device:
                # This condition is unreachable in normal conditions, because JoinAccept is answer to uplink, se we
                # will have this device in cache already.
                logger.warning("Missing device context for JoinAccept message")
                return None

            # If this is join - we need to force update device's new addr in local storage
            await device.sync_from_remote(trigger_update_callback=False, update_local_list=True)
            logger.debug("Device list synced for newly joined device", dev_eui=device.dev_eui, new_addr=device.dev_addr)
            logger.debug("Device context obtained from cache (JoinAccept)", context_id=downlink.context_id)
            return DownlinkDeviceContext.Regular(dev_eui=device.dev_eui, target_dev_addr=device.dev_addr)

        # If this is not JoinAccept - it can be class A downlink, so we using device from cache.
        if device is not None:
            self._chirpstack_context_to_device.pop(downlink.context_id)
            logger.debug("Device context obtained from cache (answering to uplink)", context_id=downlink.context_id)
            return DownlinkDeviceContext.Regular(dev_eui=device.dev_eui)

        # We can handle only ConfirmedDataDown/UnconfirmedDataDown downlinks
        if mhdr.mtype not in (
            pylorawan.message.MType.ConfirmedDataDown,
            pylorawan.message.MType.UnconfirmedDataDown,
        ):
            logger.warning("Downlink has unknown type", downlink_type=repr(mhdr.mtype))
            return None

        # If this downlink is not JoinAccept or class A downlink, we are trying to obtain device by it's DevAddr.
        # Here we parsing lora message, to extract target device's DevAddr
        parsed_downlink = pylorawan.message.PHYPayload.parse(downlink.payload)
        str_dev_addr = f"{parsed_downlink.payload.fhdr.dev_addr:08x}"
        device = self.devices.get_device_by_dev_addr(str_dev_addr)

        # If device found in devices list, we are currently processing B/C downlink. Device's DevEui found.
        if device is not None:
            logger.debug("Device context obtained (class B/C downlink)", dev_addr=str_dev_addr)
            return DownlinkDeviceContext.Regular(dev_eui=device.dev_eui)
        logger.debug("Could not obtain device context for device, looking in multicast groups", dev_addr=str_dev_addr)

        # If no device with provided DevAddr found, trying to obtain multicast group with this addr.
        # It means, we are processing mulitcast downlink for class B/C now.
        multicast_group = self.multicast_groups.get_group_by_addr(str_dev_addr)
        if multicast_group is not None:
            logger.debug("Device context obtained (multicast group)", multicast_addr=str_dev_addr)
            return DownlinkDeviceContext.Multicast(multicast_addr=multicast_group.addr)
        logger.debug("No multicast group context found", multicast_addr=str_dev_addr)

        # If nothing is found after all steps - we have no devices or multicast groups with this DevAddr stored.
        return None

    async def _process_downlink_frame(self, downlink_frame: gw_pb2.DownlinkFrame):
        # NOTE: ChirpStack may return two downlinks, when answering on class A uplink.
        #   This downlinks will contain same downlink message with different TMST - for RX1 and RX2 window.
        #   Here we send first one to ran-routing, and reply with ack to rest of downlinks.
        # TODO: Send all downlinks to ran-routing, when it supports multiple TxWindow's.
        # EXTRA: https://github.com/brocaar/chirpstack-api/blob/master/protobuf/gw/gw.proto#L380
        ran_downlinks = self._create_ran_downlinks(downlink_frame)

        for idx, downlink in enumerate(ran_downlinks):
            if idx > 0:
                # Here we just counting all downlinks after first one, to send correct TxAck back to chirpstack, when
                # Ack will have been received from ran-routing.
                current_count: int = self._ignored_downlinks_count.get(downlink.downlink_id, 0)  # type: ignore
                self._ignored_downlinks_count.set(downlink.downlink_id, current_count + 1)
                continue
            device_ctx = await self._fetch_downlink_device_context(downlink)
            if device_ctx is not None:
                # Setting device context, required for ran router
                downlink.device_ctx = device_ctx
                logger.debug("Downlink message assembled", downlink=repr(downlink))
                await self._downlinks_from_chirpstack.put(downlink)
            else:
                logger.warning("Missed device context for downlink", downlink_id=downlink.downlink_id)

    async def run(self, stop_event: asyncio.Event):
        chirpstack_downlink_topic = "gateway/{}/command/down".format(self.gateway_mac)
        await self.chirpstack_mqtt_client.subscribe(chirpstack_downlink_topic)
        async with self.chirpstack_mqtt_client.listen(chirpstack_downlink_topic) as downlink_queue:
            while not stop_event.is_set():
                try:
                    payload = None
                    with suppress(asyncio.TimeoutError):
                        _, payload = await asyncio.wait_for(downlink_queue.get(), 0.1)
                    if not payload:
                        continue

                    downlink_frame = gw_pb2.DownlinkFrame()
                    downlink_frame.ParseFromString(payload)
                    logger.debug(
                        "Downlink message received from Chirpstack",
                        chirpstack_downlink=lazy_protobuf_fmt(downlink_frame),
                    )
                    await self._process_downlink_frame(downlink_frame)

                except Exception:
                    logger.exception("Unhandled exception in in chirpstack listening loop")
