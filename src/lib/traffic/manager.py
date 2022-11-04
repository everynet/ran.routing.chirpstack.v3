import asyncio
import logging
from contextlib import suppress
from typing import Optional

from .async_pool import Pool
from .chirpstack import ChirpstackTrafficRouter
from .ran import RanTrafficRouter

logger = logging.getLogger(__name__)


class PipelineNode:
    def __init__(
        self, fn, fn_args, fn_kwargs, pool_size: int, rx: asyncio.Queue, tx: Optional[asyncio.Queue] = None
    ) -> None:
        self.fn = fn
        self.fn_args = fn_args
        self.fn_kwargs = fn_kwargs

        self.rx = rx
        self.tx = tx
        self._pool = Pool(pool_size)

    async def _handle_item(self, item):
        try:
            result = await self.fn(item, *self.fn_args, **self.fn_kwargs)
        except Exception:
            logger.exception(f"Unhandled exception in {self.fn.__qualname__}")
        else:
            if self.tx:
                await self.tx.put(result)

    async def run(self, stop_event: asyncio.Event):
        while True:
            item = None
            with suppress(asyncio.TimeoutError):
                item = await asyncio.wait_for(self.rx.get(), timeout=0.1)
            if not item:
                if stop_event.is_set():
                    break
                continue

            await self._pool.add(self._handle_item(item))
            self.rx.task_done()
        await self._pool.join()


class Pipeline:
    def __init__(self, source: asyncio.Queue, step_pool_size: int = 64) -> None:
        self._nodes: list[PipelineNode] = []
        self._source: asyncio.Queue = source
        self._step_pool_size = step_pool_size

    def chain(self, method, *args, **kwargs):
        chain_len = len(self._nodes)
        if chain_len == 0:
            self._nodes.append(PipelineNode(method, args, kwargs, self._step_pool_size, rx=self._source))
        else:
            prev_tx = asyncio.Queue()
            self._nodes[-1].tx = prev_tx
            self._nodes.append(PipelineNode(method, args, kwargs, self._step_pool_size, rx=prev_tx))
        return self

    async def run(self, stop_event: asyncio.Event):
        tasks = set()
        for node in self._nodes:
            task = asyncio.create_task(node.run(stop_event))
            tasks.add(task)

        await stop_event.wait()
        return await asyncio.gather(*tasks)


class TrafficManager:
    def __init__(self, chirpstack: ChirpstackTrafficRouter, ran: RanTrafficRouter) -> None:
        self.chirpstack = chirpstack
        self.ran = ran

    async def run(self, stop_event: asyncio.Event):
        tasks = set()
        upstream = (
            Pipeline(self.ran.uplinks_rx)
            .chain(self.chirpstack.handle_upstream)
            .chain(self.ran.handle_upstream_ack_or_reject)
        )
        tasks.add(asyncio.create_task(upstream.run(stop_event), name="upstream"))
        logger.info('"ran -[Upstream]-> chirpstack -[UpstreamAck]-> ran" forwarding routine started')

        downstream = Pipeline(self.chirpstack.downstream_rx).chain(self.ran.handle_downstream)
        tasks.add(asyncio.create_task(downstream.run(stop_event), name="downstream"))
        logger.info('"chirpstack -[Downstream]-> ran" forwarding routine started')

        downstream_ack = Pipeline(self.ran.downlink_results_rx).chain(self.chirpstack.handle_downstream_result)
        tasks.add(asyncio.create_task(downstream_ack.run(stop_event), name="downstream_result"))
        logger.info('"ran -[DownstreamResult]-> chirpstack" forwarding routine started')

        return await asyncio.gather(*tasks)
