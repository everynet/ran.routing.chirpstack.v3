import asyncio
import itertools
from time import time
from typing import Optional
from enum import Enum

import pylorawan
import structlog
from pydantic import BaseModel
from ran.routing.core.domains import *
from ran.routing.core import Core as RANCore
from ran.routing.core.upstream import UpstreamConnection
from ran.routing.core.downstream import DownstreamConnection

from . import lorawan
from .cache import Cache


logger = structlog.getLogger(__name__)


class RejectUplink(Exception):
    pass


class UplinkRadioParams(UpstreamRadio):
    pass


class DownlinkRadioParams(BaseRadio):
    pass


class Uplink(BaseModel):
    payload: bytes
    radio: UplinkRadioParams


class Downlink(BaseModel):
    target_dev_addr: Optional[str]
    payload: bytes
    radio: DownlinkRadioParams
    delay: int

    def __hash__(self):
        return id(self)


class DownlinkResult(Enum):
    OK = 0
    ERROR = 1
    TOO_LATE = 2


class BaseTrafficHandler:
    async def handle_upstream_message(self, uplink: Uplink) -> Optional[Downlink]:
        pass

    async def handle_downstream_ack(self, downlink: Downlink) -> None:
        pass

    async def handle_dowstream_result(self, downlink: Downlink, result: DownlinkResult) -> None:
        pass


class LoRaWANTrafficHandler(BaseTrafficHandler):
    async def handle_upstream_message(self, uplink: Uplink) -> Optional[Downlink]:
        phy_payload = pylorawan.message.PHYPayload.parse(uplink.payload)
        downlink = None
        if phy_payload.mhdr.mtype == lorawan.MType.JoinRequest:
            downlink = await self.handle_join_request(phy_payload, uplink.radio)
        elif phy_payload.mhdr.mtype in (lorawan.MType.ConfirmedDataUp, lorawan.MType.UnconfirmedDataUp):
            downlink = await self.handle_uplink(phy_payload, uplink.radio)
        else:
            logger.error(f"Unknown message type: {phy_payload.mhdr.mtype!r}")
            return

        return downlink

    async def handle_join_request(
        self, join_request: lorawan.JoinRequest, radio: UplinkRadioParams
    ) -> Optional[Downlink]:
        pass

    async def handle_uplink(self, mac_payload: lorawan.MACPayload, radio: UplinkRadioParams) -> Optional[Downlink]:
        pass


class TrafficManager:
    def __init__(self, ran_core: RANCore, traffic_handler: BaseTrafficHandler) -> None:
        self.ran_core = ran_core
        self.traffic_handler = traffic_handler
        self.upstream: Optional[UpstreamConnection] = None
        self.downstream: Optional[DownstreamConnection] = None
        self.downstream_transaction_id = itertools.cycle(range(1, 2**32))
        self.cached_result = Cache(ttl=5)
        self.downlink_result = Cache(ttl=30)

    def populate_lora_messages(self, upstream_message: UpstreamMessage) -> list[tuple[int, bytearray]]:
        messages = []
        for mic_int in upstream_message.mic_challenge:
            mic = mic_int.to_bytes(4, byteorder="big")
            messages.append((mic_int, upstream_message.phy_payload_no_mic + mic))
        return messages

    async def handle_upstream_message(self, upstream_message: UpstreamMessage) -> Optional[DownstreamMessage]:
        # "handle_upstream_message" can fail with exception, but we want to create empty "downstream" message anyway.
        downlink = None
        # This variable is used to track correct MIC value.
        accepted_mic = None

        # Making sure all cached results are valid
        accepted_mic_future = self.cached_result.get(upstream_message.phy_payload_no_mic, None)

        if accepted_mic_future is None:
            accepted_mic_future = asyncio.Future()
            self.cached_result.set(upstream_message.phy_payload_no_mic, accepted_mic_future)

            # Client creates multiple messages with different MICs, obtained from MIC Challenge and pass each to Handler.
            # NS must raise an MicChallengeFailed exception if it can't verify message with this MIC.
            # Other exceptions are also treated as mic challenge error, and client will send REJECT for this message.
            # TODO: how we need to handle other exceptions? Now we just break the loop.
            for mic, lora_message_bytes in self.populate_lora_messages(upstream_message):
                try:
                    uplink = Uplink(payload=lora_message_bytes, radio=upstream_message.radio)
                    downlink = await self.traffic_handler.handle_upstream_message(uplink)

                except RejectUplink:
                    pass
                except Exception as e:
                    logger.exception(f"Exception in upstream handler: {e}")
                    break
                else:
                    accepted_mic = mic
                    break

            accepted_mic_future.set_result(accepted_mic)
        else:
            # TODO: better timeout value
            await asyncio.wait_for(accepted_mic_future, self.cached_result._ttl)
            accepted_mic = accepted_mic_future.result()

        # If we have no accepted MIC, we reject the message.
        if accepted_mic is None:
            logger.warning("Mic challenge failed, sending REJECT")
            await self.upstream.send_upstream_reject(
                transaction_id=upstream_message.transaction_id, result_code=UpstreamRejectResultCode.MICFailed
            )
            return None

        # If mic is accepted, we send an ACK for this message.
        logger.debug("Mic challenge successful, sending ACK")
        # TODO: get device eui somehow. NS must also return DevEUI for device, which send uplink message.
        dev_eui = upstream_message.dev_euis[0]

        await self.upstream.send_upstream_ack(
            transaction_id=upstream_message.transaction_id, dev_eui=dev_eui, mic=accepted_mic
        )

        # Convert Downlink to UpstreamMessage
        if not downlink:
            return None

        target_dev_addr = downlink.target_dev_addr
        if target_dev_addr:
            target_dev_addr = int(target_dev_addr, 16)

        tx_window = TransmissionWindow(delay=downlink.delay, radio=downlink.radio)
        downstream_message = DownstreamMessage(
            protocol_version=1,
            transaction_id=next(self.downstream_transaction_id),
            dev_eui=dev_eui,
            target_dev_addr=target_dev_addr,
            tx_window=tx_window,
            phy_payload=downlink.payload,
        )

        self.downlink_result.set(downstream_message.transaction_id, downlink)

        await self.downstream.send_downstream_object(downstream_message)

    async def _run_upstream_loop(self) -> None:
        tasks = set()

        async with self.ran_core.upstream() as upstream_conn:
            self.upstream = upstream_conn

            async for upstream_message in upstream_conn.stream():
                if len(tasks) > 1000:
                    logger.warning("Too many unfinished tasks. Dropping upstream message")
                    continue

                task = asyncio.create_task(self.handle_upstream_message(upstream_message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

    async def _run_downstream_loop(self):
        async with self.ran_core.downstream() as downstream_conn:
            self.downstream = downstream_conn

            async for downstream_message in downstream_conn.stream():
                if isinstance(downstream_message, DownstreamResultMessage):
                    downlink = self.downlink_result.pop(downstream_message.transaction_id, None)
                    if downlink:
                        downlink_result = DownlinkResult.ERROR
                        if downstream_message.result_code == DownstreamResultCode.Success:
                            downlink_result = DownlinkResult.OK
                        elif downstream_message.result_code == DownstreamResultCode.TooLate:
                            downlink_result = downlink_result.TOO_LATE

                        await self.traffic_handler.handle_dowstream_result(downlink, downlink_result)
                await asyncio.sleep(0)

    async def run(self):
        max_delay = 30
        min_delay = 5
        delay = min_delay

        while True:
            start_time = time()
            run_upstream_loop_task = asyncio.create_task(self._run_upstream_loop())
            run_downstream_loop_task = asyncio.create_task(self._run_downstream_loop())
            _, pending = await asyncio.wait(
                {run_upstream_loop_task, run_downstream_loop_task}, return_when=asyncio.FIRST_COMPLETED
            )

            # Extract pending task and cancel it
            if pending:
                task = pending.pop()
                task.cancel()

            # Are we crashing too fast?
            if time() - start_time < min_delay:
                delay = min(delay * 2, max_delay)
            else:
                delay = min_delay

            asyncio.sleep(delay)
