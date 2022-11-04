import asyncio
from contextlib import suppress

from structlog import getLogger

logger = getLogger(__name__)


class Periodic:
    """
    This class helps to execute some task periodically.
    It add generic exception handling, graceful shutdown and watchdog timer tracking.
    This helper does not track task execution time, and just add sleep between calls.

    Usage:
    .. code-block:: python

        # Blocks forever, until "global_stop" is not set.
        await Periodic(some_awaitable_func, fn_arg, fn_kwarg=...).run(global_stop, interval=1.0, task_name="some_name")
    """

    def __init__(self, awaitable_fn, *args, **kwargs):
        self.awaitable_fn = awaitable_fn
        self._fn_args = args
        self._fn_kwargs = kwargs

    async def run(self, stop_event: asyncio.Event, interval: float = 30.0, task_name: str | None = None):
        retry = 0
        task_name = task_name or self.awaitable_fn.__qualname__
        while not stop_event.is_set():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=min(interval, max(0, interval - 2**retry)))
                # If event is set - just exit loop, else an exception will be raised on pervious line
                return

            try:
                logger.debug(f"Starting periodic task {task_name!r}")
                await self.awaitable_fn(*self._fn_args, **self._fn_kwargs)
                logger.debug(f"Periodic task {task_name!r} finished")
            except Exception:
                logger.exception(f"Error has occurred in periodic task {task_name}:")
                retry += 1
            else:
                retry = 0

    def create_task(
        self, stop_event: asyncio.Event, interval: float = 30.0, task_name: str | None = None
    ) -> asyncio.Task:
        return asyncio.create_task(self.run(stop_event, interval, task_name), name=task_name)
