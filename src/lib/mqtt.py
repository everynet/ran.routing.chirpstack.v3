import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse, unquote
from contextlib import AsyncExitStack
from typing import Any, Callable, Optional, Set, Tuple, Union
from collections import defaultdict

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
        self._client: AsyncioClient = None
        self._create_client()

        self._reconnect_interval = 1
        self._connection_established = asyncio.Event()

        self._listeners = MQTTMatcher()

    async def wait_for_connection(self, timeout=None):
        await asyncio.wait_for(self._connection_established.wait(), timeout)

    def _get_mqtt_default_port(self, scheme):
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
            client_options["tls_context"] = True

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

    async def run(self) -> None:
        """Run the MQTT client worker."""
        # Reconnect automatically until the client is stopped.
        while True:
            try:
                await self._subscribe_worker()
            except MqttError as err:
                self._reconnect_interval = min(self._reconnect_interval * 2, 900)
                logger.error(
                    "MQTT error. Reconnecting...",
                    error=err,
                    reconnect_interval=self._reconnect_interval,
                )
                self._connection_established.clear()
                await self.on_disconnect()
                await asyncio.sleep(self._reconnect_interval)
                self._create_client()  # reset connect/reconnect futures

    async def _subscribe_worker(self) -> None:
        """Connect and manage receive tasks."""
        async with AsyncExitStack() as stack:
            tasks: Set[asyncio.Task] = set()
            # Connect to the MQTT broker.
            await stack.enter_async_context(self._client)
            # Reset the reconnect interval after successful connection.
            self._reconnect_interval = 1

            # Messages that doesn't match a filter will get logged and handled here.
            messages = await stack.enter_async_context(self._client.unfiltered_messages())

            if not self._connection_established.is_set():
                self._connection_established.set()
                await self.on_connect()

            async for message in messages:
                logger.info("Received message", topic=message.topic, payload=message.payload)

                for listeners in self._listeners.iter_match(message.topic):
                    for listener in listeners:
                        await listener.put([message.topic, message.payload])

    @asynccontextmanager
    async def listen(self, topic_filter=None) -> asyncio.Queue:
        if topic_filter is None:
            topic_filter = "#"

        listeners = set()
        queue = asyncio.Queue()

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
