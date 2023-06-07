import asyncio
import typing
import functools

import structlog
from aiohttp import web


logger = structlog.getLogger(__name__)


class HealthcheckServer:
    def __init__(
        self, live_handler: typing.Callable, ready_handler: typing.Callable, context: typing.Any = None
    ) -> None:
        self._app = web.Application()

        self._live_handler = functools.partial(live_handler, context)
        self._ready_handler = functools.partial(ready_handler, context)

        self._app.add_routes([web.get("/health/live", functools.partial(self._handler, self._live_handler))])
        self._app.add_routes([web.get("/health/ready", functools.partial(self._handler, self._ready_handler))])

    async def _handler(self, handler: typing.Callable, request: web.Request) -> web.Response:
        response_data = {"status": None, "reasone": None}
        respoinse_status_code = 200

        try:
            await handler()
            logger.info("Healthcheck passed", path=request.path)
            response_data["status"] = "ok"
        except Exception as e:
            logger.exception("Healthcheck exception", path=request.path)
            response_data["status"] = "fail"
            response_data["reason"] = str(e)
            respoinse_status_code = 422

        response = web.json_response(response_data)
        response.set_status(respoinse_status_code)
        return response

    async def run(self, stop_event: asyncio.Event, host: str = "0.0.0.0", port: int = 9090) -> None:
        logger.info("Starting healthcheck server", host=host, port=port)

        runner = web.AppRunner(self._app)
        await runner.setup()

        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        await stop_event.wait()
        await site.stop()
        await site._server.wait_closed()
