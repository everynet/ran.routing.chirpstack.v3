import asyncio
import hashlib
import itertools
from collections import defaultdict
from contextlib import suppress
from time import time
from typing import Optional
from uuid import UUID

import pylorawan
import structlog
from ran.routing.core import Core as RANCore
from ran.routing.core.domains import (
    DownstreamAckMessage,
    DownstreamMessage,
    DownstreamResultCode,
    DownstreamResultMessage,
    MulticastDownstreamMessage,
    TransmissionWindow,
    UpstreamMessage,
    UpstreamRejectResultCode,
)
from ran.routing.core.downstream import DownstreamConnection
from ran.routing.core.upstream import UpstreamConnection

from ..cache import Cache
from .async_pool import Pool
from .models import (
    Downlink,
    DownlinkDeviceContext,
    DownlinkRadioParams,
    DownlinkResult,
    DownlinkResultStatus,
    DownlinkTiming,
    DownstreamRadio,
    Uplink,
    UplinkAck,
    UplinkRadioParams,
    UplinkReject,
    UplinkRejectReason,
)

logger = structlog.getLogger(__name__)


def as_transmission_window(radio: DownlinkRadioParams, timing: DownlinkTiming.TimingBase) -> TransmissionWindow:
    if isinstance(timing, DownlinkTiming.Delay):
        return TransmissionWindow(radio=DownstreamRadio.parse_obj(radio), delay=timing.seconds)
    elif isinstance(timing, DownlinkTiming.GpsTime):
        return TransmissionWindow(radio=DownstreamRadio.parse_obj(radio), tmms=[timing.tmms])
    elif isinstance(timing, DownlinkTiming.Immediately):
        # TODO: Set deadline as "send not before" time, when ran supports it.
        return TransmissionWindow(radio=DownstreamRadio.parse_obj(radio), deadline=1)
    else:
        raise TypeError(f"Unknown timing type {repr(type(timing))}")


class UplinkAckSync:
    def __init__(self, ttl=60) -> None:
        self._cache: Cache[str, UplinkAck] = Cache(ttl=ttl)
        self._waiters: dict[str, set[asyncio.Future[UplinkAck]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    def set_ack(self, context_id, uplink_ack: UplinkAck):
        self._cache.set(context_id, uplink_ack)

    def has_ack(self, context_id: str) -> bool:
        return self._cache.get(context_id, None) is not None

    def get_ack(self, context_id: str) -> UplinkAck | None:
        return self._cache.get(context_id, None)

    def __loop_pass(self):
        need_removal = []
        for context_id, waiters in self._waiters.items():
            if not len(waiters):
                need_removal.append(context_id)
                continue
            uplink_ack = self._cache.get(context_id)
            if not uplink_ack:
                continue
            for waiter in waiters.copy():
                # Copy, because future's "done_callback" will call ".discard()" on result set
                waiter.set_result(uplink_ack)
        for remove_id in need_removal:
            del self._waiters[remove_id]

    async def run(self, stop_event: asyncio.Event):
        while not stop_event.is_set():
            async with self._lock:
                self.__loop_pass()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=0.1)

    async def make_ack_waiter(self, context_id: str) -> asyncio.Future[UplinkAck]:
        future: asyncio.Future[UplinkAck] = asyncio.Future()
        future.add_done_callback(self._waiters[context_id].discard)
        # Lock required, because waiters entries may be removed during loop pass. If someone asks for new waiter in
        # this moment, it can be added right before waiters removal and will be removed too.
        async with self._lock:
            self._waiters[context_id].add(future)
        return future


class RanTrafficRouter:
    def __init__(self, ran_core: RANCore) -> None:
        self.ran_core = ran_core
        self.upstream: Optional[UpstreamConnection] = None
        self.downstream: Optional[DownstreamConnection] = None
        self.downstream_transaction_id = itertools.cycle(range(1, 2**32))

        # Cache to prevent duplicates and track transaction id
        self._upstream_id_to_transaction: Cache[str, int] = Cache(ttl=60)
        self._downstream_transaction_to_id: Cache[int, str] = Cache(ttl=60)

        # Ack's for duplicated uplinks stuff
        self._ack_sync = UplinkAckSync(ttl=60)
        self._ack_submitters_pool = Pool(64)

        # Mic challenge state
        self._mic_tries_count: dict[str, int] = {}

        # Communication
        self._uplinks_from_ran: asyncio.Queue[Uplink] = asyncio.Queue()
        self._downlink_results_from_ran: asyncio.Queue[DownlinkResult] = asyncio.Queue()

    @property
    def uplinks_rx(self) -> asyncio.Queue[Uplink]:
        return self._uplinks_from_ran

    @property
    def downlink_results_rx(self) -> asyncio.Queue[DownlinkResult]:
        return self._downlink_results_from_ran

    @staticmethod
    def _populate_lora_messages(upstream_message: UpstreamMessage) -> list[tuple[int, bytearray]]:
        messages = []
        for mic_int in upstream_message.mic_challenge:
            mic = mic_int.to_bytes(4, byteorder="big")
            messages.append((mic_int, upstream_message.phy_payload_no_mic + mic))
        return messages

    async def _handle_multicast(self, downlink: Downlink) -> None:
        assert isinstance(downlink.device_ctx, DownlinkDeviceContext.Multicast)
        multicast_downstream = MulticastDownstreamMessage(
            protocol_version=1,
            transaction_id=next(self.downstream_transaction_id),
            addr=int(downlink.device_ctx.multicast_addr, 16),
            phy_payload=downlink.payload,
            tx_window=as_transmission_window(downlink.radio, downlink.timing),
        )
        self._downstream_transaction_to_id.set(multicast_downstream.transaction_id, downlink.downlink_id)
        if not self.downstream:
            logger.error("Downstream not ready for transmission!")
            return
        await self.downstream.send_multicast_downstream_object(multicast_downstream)
        logger.debug("Multicast downstream sent to RAN", downstream_message=repr(multicast_downstream))

    async def _handle_downlink(self, downlink: Downlink) -> None:
        assert isinstance(downlink.device_ctx, DownlinkDeviceContext.Regular)
        target_dev_addr = downlink.device_ctx.target_dev_addr
        if target_dev_addr:
            target_dev_addr = int(target_dev_addr, 16)  # type: ignore

        downstream_message = DownstreamMessage(
            protocol_version=1,
            transaction_id=next(self.downstream_transaction_id),
            dev_eui=int(downlink.device_ctx.dev_eui, 16),  # type: ignore
            target_dev_addr=target_dev_addr,
            phy_payload=downlink.payload,
            tx_window=as_transmission_window(downlink.radio, downlink.timing),
        )

        self._downstream_transaction_to_id.set(downstream_message.transaction_id, downlink.downlink_id)
        if not self.downstream:
            logger.error("Downstream not ready for transmission!")
            return
        await self.downstream.send_downstream_object(downstream_message)
        logger.debug("Downstream sent to RAN", downstream_message=repr(downstream_message))

    async def handle_downstream(self, downlink: Downlink) -> None:
        if isinstance(downlink.device_ctx, DownlinkDeviceContext.Regular):
            return await self._handle_downlink(downlink)
        elif isinstance(downlink.device_ctx, DownlinkDeviceContext.Multicast):
            return await self._handle_multicast(downlink)
        else:
            # Unreachable!
            raise TypeError(f"Unknown downlink device context type {type(downlink.device_ctx)}")

    async def handle_upstream_ack_or_reject(self, ack_or_reject: UplinkAck | UplinkReject) -> None:
        current_mic_try = self._mic_tries_count.get(ack_or_reject.uplink_id)
        if current_mic_try is None:
            return

        transaction_id = self._upstream_id_to_transaction.get(ack_or_reject.uplink_id, None)
        if not transaction_id:
            return

        log = logger.bind(transaction_id=transaction_id)
        if isinstance(ack_or_reject, UplinkAck):
            # Tracking upstream ack, which will be used for ack'ing duplicate
            self._ack_sync.set_ack(ack_or_reject.context_id, ack_or_reject)
            await self.upstream.send_upstream_ack(  # type: ignore
                transaction_id=transaction_id, dev_eui=int(ack_or_reject.dev_eui, 16), mic=ack_or_reject.mic
            )
            del self._mic_tries_count[ack_or_reject.uplink_id]
            logger.debug(
                "Mic challenge successful for upstream, ack sent",
                transaction_id=transaction_id,
                ack=repr(ack_or_reject),
            )
            return

        # In case, if this is UplinkReject message
        match ack_or_reject.reason:
            case UplinkRejectReason.MicChallengeFail:
                # Means this mic-challenge try is last try
                if current_mic_try == 1:
                    log.info("Mic challenge not solved for uplink")
                    await self.upstream.send_upstream_reject(  # type: ignore
                        transaction_id=transaction_id, result_code=UpstreamRejectResultCode.MICFailed
                    )
                    del self._mic_tries_count[ack_or_reject.uplink_id]
                else:
                    self._mic_tries_count[ack_or_reject.uplink_id] -= 1
                return
            case UplinkRejectReason.DeviceNotFound:
                log.info("No device found, rejecting MIC challenge")
                await self.upstream.send_upstream_reject(  # type: ignore
                    transaction_id=transaction_id, result_code=UpstreamRejectResultCode.Other
                )
                del self._mic_tries_count[ack_or_reject.uplink_id]
            case UplinkRejectReason.NotSupported:
                log.info("Chirpstack router not support message type, rejecting MIC challenge")
                await self.upstream.send_upstream_reject(  # type: ignore
                    transaction_id=transaction_id, result_code=UpstreamRejectResultCode.Other
                )
                del self._mic_tries_count[ack_or_reject.uplink_id]
            case UplinkRejectReason.InternalError:
                log.info("Some error happening during handling upstream")
                self._mic_tries_count[ack_or_reject.uplink_id] -= 1
            case _:
                log.error(f"Unknown reject reason {ack_or_reject.reason!r}")

    async def _submit_ack_when_available(
        self, transaction_id: int, uplink_ack_waiter: asyncio.Future[UplinkAck], timeout: float = 20.0
    ):
        try:
            uplink_ack = await asyncio.wait_for(uplink_ack_waiter, timeout)
        except asyncio.TimeoutError:
            if not self.upstream:
                return
            await self.upstream.send_upstream_reject(  # type: ignore
                transaction_id=transaction_id, result_code=UpstreamRejectResultCode.MICFailed
            )
            logger.warning(
                f"No solved mic challenge for upstream duplicate found after waiting {timeout}s., duplicate rejected"
            )
            return

        if not self.upstream:
            return
        await self.upstream.send_upstream_ack(  # type: ignore
            transaction_id=transaction_id, dev_eui=int(uplink_ack.dev_eui, 16), mic=uplink_ack.mic
        )
        logger.debug(
            "Correct mic challenge obtained from solver, ack submitted",
            transaction_id=transaction_id,
            dev_eui=uplink_ack.dev_eui,
            correct_mic=uplink_ack.mic,
        )

    async def _handle_upstream_message(self, upstream_message: UpstreamMessage):
        # uplink_id is unique for same phy-payload (without mic). Actually, this is not UUID, but md5 checksum,
        # represented as UUID. This solution used used for two purposes:
        # 1. Make upstream messages unique, based on their payload - required for deduplication
        # 2. Make this identifier compatible with chirpstack "uplink_id" field - it requires 16-bytes UUID's
        uplink_payload_checksum = hashlib.md5(bytes(upstream_message.phy_payload_no_mic), usedforsecurity=True).digest()
        uplink_id = str(UUID(bytes=uplink_payload_checksum))

        # This log record duplicates logging from sdk, disabled
        # logger.debug("upstream_message received from RAN", upstream_message=repr(upstream_message))

        # If we already process uplink with same "uplink_id", we will have some "transaction_id" stored in cache.
        if self._upstream_id_to_transaction.get(uplink_id) is not None:
            logger.debug(
                "Upstream message is duplicate, already answered with downlink. It will be not forwarded to chirpstack"
            )
            last_ack: UplinkAck | None = self._ack_sync.get_ack(uplink_id)
            if last_ack is not None:
                await self.upstream.send_upstream_ack(  # type: ignore
                    transaction_id=upstream_message.transaction_id, dev_eui=int(last_ack.dev_eui, 16), mic=last_ack.mic
                )
                logger.debug("Mic challenge successful for upstream duplicate (obtained from cache), ack sent")
                return
            logger.debug("No solved mic challenge cached. Ack send scheduled, until MIC is calculated")
            uplink_ack_waiter = await self._ack_sync.make_ack_waiter(uplink_id)
            await self._ack_submitters_pool.add(
                self._submit_ack_when_available(upstream_message.transaction_id, uplink_ack_waiter)
            )
            return

        self._upstream_id_to_transaction.set(uplink_id, upstream_message.transaction_id)
        total_challenges = len(upstream_message.mic_challenge)
        self._mic_tries_count[uplink_id] = total_challenges

        for mic, lora_message_bytes in self._populate_lora_messages(upstream_message):
            uplink = Uplink(
                uplink_id=uplink_id,
                used_mic=mic,
                payload=pylorawan.message.PHYPayload.parse(lora_message_bytes),
                radio=UplinkRadioParams.parse_obj(upstream_message.radio),
            )
            await self._uplinks_from_ran.put(uplink)

    async def _handle_downstream_result_message(self, downstream_message: DownstreamMessage):
        downlink_id = self._downstream_transaction_to_id.pop(downstream_message.transaction_id)
        if not downlink_id:
            logger.warning("Received DownstreamResult with unknown transaction_id")
            return

        downlink_status = DownlinkResultStatus.ERROR
        if downstream_message.result_code == DownstreamResultCode.Success:
            downlink_status = DownlinkResultStatus.OK
        elif downstream_message.result_code == DownstreamResultCode.TooLate:
            downlink_status = DownlinkResultStatus.TOO_LATE
        downlink_result = DownlinkResult(downlink_id=downlink_id, status=downlink_status)

        await self._downlink_results_from_ran.put(downlink_result)
        logger.debug(
            "DownlinkResult received from RAN",
            result=repr(downlink_result),
            transaction_id=downstream_message.transaction_id,
        )

    async def _run_upstream_loop(self) -> None:
        async with self.ran_core.upstream() as upstream_conn:
            self.upstream = upstream_conn
            try:
                async for upstream_message in upstream_conn.stream():
                    await self._handle_upstream_message(upstream_message)
            finally:
                self.upstream = None

    async def _run_downstream_loop(self):
        async with self.ran_core.downstream() as downstream_conn:
            self.downstream = downstream_conn

            try:
                async for downstream_message in downstream_conn.stream():
                    if isinstance(downstream_message, DownstreamAckMessage):
                        logger.debug(
                            "Received DownstreamAck from RAN", transaction_id=downstream_message.transaction_id
                        )
                    elif isinstance(downstream_message, DownstreamResultMessage):
                        await self._handle_downstream_result_message(downstream_message)
                    else:
                        logger.warning("Received unknown DownstreamResult format message")
            finally:
                self.downstream = None

    async def run(self, stop_event: asyncio.Event):
        max_delay = 30
        min_delay = 5
        delay = min_delay
        ack_sync_task = asyncio.create_task(self._ack_sync.run(stop_event))

        async def disconnect_on_stop():
            await stop_event.wait()
            # When core closed, all async iterators in connections will exit after processing all messages
            logger.debug("Stop signal received, closing RAN client")
            await self.ran_core.close()

        disconnect_task = asyncio.create_task(disconnect_on_stop())

        while not stop_event.is_set():
            start_time = time()
            run_upstream_loop_task = asyncio.create_task(self._run_upstream_loop())
            run_downstream_loop_task = asyncio.create_task(self._run_downstream_loop())
            done, pending = await asyncio.wait(
                {run_upstream_loop_task, run_downstream_loop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_event.is_set():
                await disconnect_task
                if len(pending):
                    for pending_task in pending:
                        await pending_task
                return

            logger.warning(f"Some of background tasks ended unexpectedly: {done}")

            # Extract pending task and cancel it
            if pending:
                task = pending.pop()
                task.cancel()

                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Are we crashing too fast?
            if time() - start_time < min_delay:
                delay = min(delay * 2, max_delay)
            else:
                delay = min_delay

            logger.info(f"Reconecting with delay: {delay} sec")
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
        await ack_sync_task
