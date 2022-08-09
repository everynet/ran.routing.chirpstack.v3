import uuid
import asyncio
from time import time
from typing import Optional

import pylorawan
import structlog
from chirpstack_api.common import common_pb2
from chirpstack_api.gw import gw_pb2

from .traffic_manager import RejectUplink
from .traffic_manager import Downlink
from .traffic_manager import DownlinkRadioParams, UplinkRadioParams
from .traffic_manager import LoRaModulation
from .traffic_manager import LoRaWANTrafficHandler as TrafficHandler

from . import chirpstack
from . import mqtt


logger = structlog.getLogger(__name__)


class LoRaWANTrafficHandler(TrafficHandler):
    def __init__(self, gateway_mac: str, chirpstack_mqtt_client: mqtt.MQTTClient, devices: chirpstack.DeviceList):
        self.gateway_mac = gateway_mac
        self.devices = devices
        self.chirpstack_mqtt_client = chirpstack_mqtt_client

    def create_chirpstack_uplink(self, lorawan_message: bytes, radio_params: UplinkRadioParams) -> gw_pb2.UplinkFrame:
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
        rx_info.context = uuid.uuid4().bytes
        rx_info.uplink_id = uuid.uuid4().bytes
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

        uplink = gw_pb2.UplinkFrame()
        uplink.phy_payload = lorawan_message
        uplink.rx_info.MergeFrom(rx_info)
        uplink.tx_info.MergeFrom(tx_info)

        return uplink

    def create_downlink(
        self, chirpstack_downlink_frame: gw_pb2.DownlinkFrame, dev_addr: Optional[str] = None
    ) -> Downlink:
        # TODO: handle chirpstack_downlink_frame.items. chirpstack_downlink_frame.tx_info - depricated
        if chirpstack_downlink_frame.tx_info.timing != gw_pb2.DownlinkTiming.DELAY:
            raise Exception(f"Unsupported DownlinkTiming: {chirpstack_downlink_frame.tx_info.timing}")

        if chirpstack_downlink_frame.tx_info.modulation != common_pb2.Modulation.LORA:
            raise Exception("Supported only LoRa modulation")

        lora_modulation = LoRaModulation(
            bandwidth=chirpstack_downlink_frame.tx_info.lora_modulation_info.bandwidth * 1000,
            spreading=chirpstack_downlink_frame.tx_info.lora_modulation_info.spreading_factor,
        )

        downlink_radio_params = DownlinkRadioParams(
            frequency=chirpstack_downlink_frame.tx_info.frequency, lora=lora_modulation
        )

        downlink = Downlink(
            target_dev_addr=dev_addr,
            payload=chirpstack_downlink_frame.phy_payload,
            radio=downlink_radio_params,
            delay=chirpstack_downlink_frame.tx_info.delay_timing_info.delay.seconds,
        )

        return downlink

    def _check_mic(self, phy_payload: pylorawan.message.PHYPayload, nwk_key: bytes) -> bool:
        if not pylorawan.common.verify_mic_phy_payload(phy_payload, nwk_key):
            raise RejectUplink()

    async def chirpstack_send_and_receive(
        self, phy_payload: pylorawan.message.PHYPayload, radio: UplinkRadioParams, down_timeout=16
    ) -> Optional[gw_pb2.DownlinkFrame]:
        chirpstack_uplink_topic = "gateway/{0}/event/up".format(self.gateway_mac)
        chirpstack_downlink_topic = "gateway/{}/command/down".format(self.gateway_mac)

        chirpstack_uplink = self.create_chirpstack_uplink(phy_payload.generate(), radio)

        await self.chirpstack_mqtt_client.subscribe(chirpstack_downlink_topic)
        async with self.chirpstack_mqtt_client.listen(chirpstack_downlink_topic) as downlink_queue:
            await self.chirpstack_mqtt_client.publish(chirpstack_uplink_topic, chirpstack_uplink.SerializeToString())

            start_time = time()
            while True:
                try:
                    remaining_time = down_timeout - (time() - start_time)
                    if remaining_time <= 0:
                        break

                    _, downlink_frame_raw = await asyncio.wait_for(downlink_queue.get(), remaining_time)

                    chirpstack_downlink = gw_pb2.DownlinkFrame()
                    chirpstack_downlink.ParseFromString(downlink_frame_raw)
                    if chirpstack_downlink.tx_info.context != chirpstack_uplink.rx_info.context:
                        continue

                    logger.info(
                        "Down message from chirpstack received", phy_payload=chirpstack_downlink.phy_payload.hex()
                    )

                    return chirpstack_downlink
                except asyncio.exceptions.TimeoutError:
                    logger.info("chirpstack down message read timeout")
                    break

        return None

    async def handle_uplink(
        self, phy_payload: pylorawan.message.PHYPayload, radio: UplinkRadioParams
    ) -> Optional[Downlink]:
        dev_addr = hex(phy_payload.payload.fhdr.dev_addr)[2:].zfill(8)

        device = self.devices.get_device(dev_addr)
        if not device:
            logger.warning("handle_uplink: device not found", dev_addr=dev_addr)
            return None

        self._check_mic(phy_payload, bytes.fromhex(device.nwk_s_enc_key))

        with structlog.contextvars.bound_contextvars(dev_eui=device.dev_eui):
            logger.info("Uplink received", decoded=phy_payload.as_dict(), raw=phy_payload.generate().hex())
            chirpstack_downlink_frame = await self.chirpstack_send_and_receive(phy_payload, radio, 16)
            if chirpstack_downlink_frame:
                return self.create_downlink(chirpstack_downlink_frame)

        return None

    async def handle_join_request(
        self, phy_payload: pylorawan.message.PHYPayload, radio: UplinkRadioParams
    ) -> Optional[Downlink]:
        dev_eui = hex(phy_payload.payload.dev_eui)[2:].zfill(16)

        device = self.devices.get_device(dev_eui)
        if not device:
            logger.warning("handle_join_request: device not found", dev_eui=dev_eui)
            return None

        self._check_mic(phy_payload, bytes.fromhex(device.nwk_key))

        with structlog.contextvars.bound_contextvars(dev_eui=device.dev_eui):
            logger.info("Join received", decoded=phy_payload.as_dict(), raw=phy_payload.generate().hex())
            chirpstack_downlink_frame = await self.chirpstack_send_and_receive(phy_payload, radio, 16)
            if not chirpstack_downlink_frame:
                return None

            mhdr = pylorawan.message.MHDR.parse(chirpstack_downlink_frame.phy_payload[:1])
            if mhdr.mtype != pylorawan.message.MType.JoinAccept:
                raise Exception(f"Recived message MHDR: {mhdr}, expected type JoinAccept (0x01)")

            await device.refresh()
            self.devices.merge_device(device)

            return self.create_downlink(chirpstack_downlink_frame, device.dev_addr)
