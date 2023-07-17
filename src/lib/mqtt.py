import asyncio
import ssl
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any, AsyncGenerator, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

import structlog
from asyncio_mqtt import Client as AsyncioClient
from asyncio_mqtt import MqttError
from paho.mqtt.matcher import MQTTMatcher
from paho.mqtt.properties import Properties
from paho.mqtt.subscribeoptions import SubscribeOptions

logger = structlog.getLogger(__name__)


class MQTTClient:
    """Represent an MQTT client."""

    def __init__(self, uri: str, **client_options: Any) -> None:
        """Set up client."""
        self._uri = uri
        self._client_options = client_options
        self._client: AsyncioClient = None  # type: ignore

        self._reconnect_interval = 1
        self._connection_established = asyncio.Event()
        self._connection_failed = asyncio.Event()
        self._exception: Exception | None = None

        self._listeners = MQTTMatcher()

        self._create_client()

    async def wait_for_connection(self, timeout=None) -> bool:
        try:
            _, pending = await asyncio.wait_for(
                asyncio.wait(
                    {
                        asyncio.create_task(self._connection_established.wait()),
                        asyncio.create_task(self._connection_failed.wait()),
                    },
                    return_when=asyncio.FIRST_COMPLETED,
                ),
                timeout,
            )

            # If listener stopped then cancel waiter and raise exception
            pending_task = pending.pop()
            pending_task.cancel()

            # Waiting until task cancelled
            try:
                await pending_task
            except asyncio.CancelledError:
                pass

        except asyncio.TimeoutError:
            return False

        if self._exception:
            raise Exception("MQTT connection not established") from self._exception

        return True

    def _get_mqtt_default_port(self, scheme: str) -> int:
        if scheme == "ws":
            return 80
        elif scheme == "wss":
            return 443
        elif scheme in ["mqtts", "ssl"]:
            return 8883
        elif scheme in ["mqtt", "tcp"]:
            return 1883
        raise ValueError("Unknown URI scheme: {}".format(scheme))

    def _create_client(self) -> None:
        """Create the asyncio client."""
        logger.debug("Creating MQTT client", uri=self._uri)

        uri_parsed = urlparse(self._uri)
        client_options = self._client_options.copy()

        client_options["hostname"] = uri_parsed.hostname
        client_options["port"] = uri_parsed.port or self._get_mqtt_default_port(uri_parsed.scheme)

        if uri_parsed.scheme in ["wss", "mqtts", "ssl"]:
            client_options["tls_context"] = ssl.create_default_context()

        if uri_parsed.username is not None:
            password = uri_parsed.password if uri_parsed.password else ""
            client_options["username"] = unquote(uri_parsed.username)
            client_options["password"] = unquote(password)

        client = AsyncioClient(**client_options)

        if uri_parsed.scheme in ["ws", "wss"]:
            client._client._transport = "websockets"

            ws_path = uri_parsed.path
            if uri_parsed.query:
                ws_path += "?" + uri_parsed.query
            if not ws_path:
                ws_path = "/mqtt"

            client._client.ws_set_options(path=ws_path)

        self._connection_established.clear()
        self._connection_failed.clear()
        self._exception = None
        self._client = client

    async def publish(
        self,
        topic: str,
        payload: Optional[Union[bytes, str]] = None,
        retain: bool = False,
        qos: int = 0,
        properties: Optional[Properties] = None,
        timeout: float = 10,
    ) -> None:
        """Publish to topic.
        Can raise asyncio_mqtt.MqttError.
        """
        logger.debug("Sending message", topic=topic, payload=payload)
        await self._client.publish(
            topic, qos=qos, payload=payload, retain=retain, properties=properties, timeout=timeout
        )

    async def subscribe(
        self,
        topic: str,
        qos: int = 0,
        options: Optional[SubscribeOptions] = None,
        properties: Optional[Properties] = None,
        timeout: float = 10,
    ) -> None:
        """Subscribe to topic.
        Can raise asyncio_mqtt.MqttError.
        """
        await self._client.subscribe(topic, qos=qos, options=options, properties=properties, timeout=timeout)

    async def unsubscribe(self, topic: str, properties: Optional[Properties] = None, timeout: float = 10) -> None:
        """Unsubscribe from topic.
        Can raise asyncio_mqtt.MqttError.
        """
        await self._client.unsubscribe(topic, properties=properties, timeout=timeout)

    async def on_connect(self):
        pass

    async def on_disconnect(self):
        pass

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run the MQTT client worker."""
        # Reconnect automatically until the client is stopped.
        logger.info("Starting MQTT client")

        async def disconnect_on_stop():
            await stop_event.wait()
            if not self._connection_established.is_set():
                return
            logger.debug("Stop signal received, closing MQTT client")
            try:
                await self._client.disconnect()
                logger.debug("MQTT client disconnected normally")
            except MqttError as err:
                logger.debug("MQTT client abnormal disconnect", error=err)
            finally:
                return None

        disconnect_task = asyncio.create_task(disconnect_on_stop())

        while not stop_event.is_set():
            try:
                await self._subscribe_worker()
            except MqttError as err:
                self._connection_established.clear()
                self._connection_failed.set()
                self._exception = err

                # Also breaking here, because "_subscribe_worker" may block execution forever until error.
                if stop_event.is_set():
                    break

                self._reconnect_interval = min(self._reconnect_interval * 2, 900)
                logger.error("MQTT error", error=err)
                await self.on_disconnect()

                logger.warning("Next MQTT client reconnect attempt scheduled", after=self._reconnect_interval)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=self._reconnect_interval)

                logger.warning("Reconnecting to MQTT...")
                self._create_client()  # reset connect/reconnect futures

        await disconnect_task
        logger.debug("MQTT main loop exited")

    async def _subscribe_worker(self) -> None:
        """Connect and manage receive tasks."""
        async with AsyncExitStack() as stack:
            # Connect to the MQTT broker.
            await stack.enter_async_context(self._client)
            # Reset the reconnect interval after successful connection.
            self._reconnect_interval = 1

            # Messages that doesn't match a filter will get logged and handled here.
            messages = await stack.enter_async_context(self._client.unfiltered_messages())

            if not self._connection_established.is_set():
                self._connection_established.set()
                logger.info("Connection established")
                await self.on_connect()

            async for message in messages:
                logger.debug("Received message", topic=message.topic, payload=message.payload)

                for listeners in self._listeners.iter_match(message.topic):
                    for listener in listeners:
                        await listener.put([message.topic, message.payload])

    @asynccontextmanager
    async def listen(self, topic_filter=None) -> AsyncGenerator[asyncio.Queue[Tuple[str, bytes]], None]:
        if topic_filter is None:
            topic_filter = "#"

        listeners = set()
        queue: asyncio.Queue[Tuple[str, bytes]] = asyncio.Queue()

        try:
            listeners = self._listeners[topic_filter]
        except KeyError:
            self._listeners[topic_filter] = listeners

        listeners.add(queue)

        try:
            yield queue
        finally:
            listeners.remove(queue)

            # Clean up empty set
            if len(listeners) == 0:
                del self._listeners[topic_filter]
