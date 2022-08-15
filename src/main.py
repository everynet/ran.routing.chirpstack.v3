import asyncio
import uuid
import signal

import grpc
import uvloop
import structlog
from ran.routing.core import Core as RANCore
from ran.routing.core import domains as ran_domains
from ran.routing.core.routing_table.exceptions import ApiDeviceAlreadyExistsError

from lib.logging_conf import configure_logging
from lib import mqtt
from lib import chirpstack
from lib import healthcheck
from lib.traffic_handler import LoRaWANTrafficHandler
from lib.traffic_manager import TrafficManager

import settings


logger = structlog.getLogger(__name__)


class ChirpStackDevices(chirpstack.FlatDeviceList):
    def __init__(
        self,
        ran_core: RANCore,
        chirpstack_api: chirpstack.ChirpStackAPI,
        tags: dict = ...,
    ) -> None:
        self.ran_core = ran_core
        super().__init__(chirpstack_api, tags)

    async def on_device_add(self, application_id: int, device: chirpstack.Device) -> None:
        try:
            logger.info("Adding device", dev_eui=device.dev_eui)

            dev_eui = int(device.dev_eui, 16)
            dev_addr = None
            if device.dev_addr:
                dev_addr = int(device.dev_addr, 16)

            await self.ran_core.routing_table.insert(dev_eui=dev_eui, join_eui=dev_eui, dev_addr=dev_addr)

        except ApiDeviceAlreadyExistsError as e:
            logger.warning("Device already exists. Updating device", dev_eui=device.dev_eui)
            # Resync device. Let's suppose devaddr changed
            await self.on_device_updated(application_id, device, ["dev_addr"])

    async def on_device_remove(self, application_id: int, device: chirpstack.Device) -> None:
        logger.info("Removing device", dev_eui=device.dev_eui)
        dev_eui = int(device.dev_eui, 16)
        await self.ran_core.routing_table.delete([dev_eui])

    async def on_device_updated(
        self, application_id: int, device: chirpstack.Device, changed_fields: list[str]
    ) -> None:
        if "dev_addr" in changed_fields:
            logger.info("Updating device", dev_eui=device.dev_eui)
            dev_eui = int(device.dev_eui, 16)
            dev_addr = None
            if device.dev_addr:
                dev_addr = int(device.dev_addr, 16)

            await self.ran_core.routing_table.delete([dev_eui])
            await self.ran_core.routing_table.insert(dev_eui=dev_eui, join_eui=dev_eui, dev_addr=dev_addr)


def get_grpc_channel(host: str, port: str, secure: bool = True, cert_path: str = None):
    target_addr = f"{host}:{port}"
    channel = None

    if secure:
        if cert_path is not None:
            with open(cert_path, "rb") as f:
                credentials = grpc.ssl_channel_credentials(f.read())
        else:
            credentials = grpc.ssl_channel_credentials()

        channel = grpc.aio.secure_channel(target_addr, credentials)
    else:
        channel = grpc.aio.insecure_channel(target_addr)

    return channel


def get_tags(value: str) -> dict:
    if not value:
        return {}

    parts = value.split("=", 1)
    if len(parts) > 1:
        return {parts[0]: parts[1]}

    return {parts[0]: ""}


async def get_gateway(chirpstack_api: chirpstack.ChirpStackAPI, gateway_id: str):
    gateway = await chirpstack_api.get_gateway(gateway_id)
    if not gateway:
        raise Exception("Gateway not found: {}".format(gateway_id))

    return gateway.gateway


async def update_chirpstack_device_list_task(
    chirpstack_dev_list: chirpstack.DeviceList, refresh_period: int
) -> None:
    min_refresh_period = 30
    retry = 0

    while True:
        try:
            await chirpstack_dev_list.refresh()
            logger.info("ChirpStack device list updated")
            retry = 0
        except Exception as e:
            logger.exception("Exception while refreshing ChirpStack list of devices")
            retry += 1
        await asyncio.sleep(max(min_refresh_period, refresh_period - 2**retry))


async def healthcheck_live(context):
    pass


async def healthcheck_ready(context):
    pass


async def main():
    configure_logging(log_level=settings.LOG_LEVEL, console_colors=True)

    grpc_channel = get_grpc_channel(
        settings.CHIRPSTACK_API_GRPC_HOST,
        settings.CHIRPSTACK_API_GRPC_PORT,
        settings.CHIRPSTACK_API_GRPC_SECURE,
        settings.CHIRPSTACK_API_GRPC_CERT_PATH,
    )

    tags = get_tags(settings.CHIRPSTACK_MATCH_TAGS)
    logger.info("Chirpstack object selector: ", tags=tags)

    chirpstack_api = chirpstack.ChirpStackAPI(grpc_channel, settings.CHIRPSTACK_API_TOKEN)

    ran_coverage_domain = ran_domains.Coverage[settings.RAN_COVERAGE_DOMAIN.upper()]
    ran_core = RANCore(access_token=settings.RAN_TOKEN, coverage=ran_coverage_domain)
    await ran_core.connect()

    logger.info("Cleanup RAN device list")
    await ran_core.routing_table.delete_all()

    logger.info("Loading ChirpStack devices list")
    chirpstack_devices = ChirpStackDevices(ran_core, chirpstack_api, tags)

    stop_event = asyncio.Event()

    def stop_all() -> None:
        stop_event.set()
        logger.warning("Shutting down service!")

    loop.add_signal_handler(signal.SIGHUP, stop_all)
    loop.add_signal_handler(signal.SIGINT, stop_all)
    loop.add_signal_handler(signal.SIGTERM, stop_all)

    tasks = set()
    tasks.add(
        asyncio.create_task(
            update_chirpstack_device_list_task(chirpstack_devices, settings.CHIRPSTACK_DEVICES_REFRESH_PERIOD),
            name="update_chirpstack_device_list",
        )
    )

    chirpstack_mqtt_client = mqtt.MQTTClient(settings.CHIRPSTACK_MQTT_SERVER_URI, client_id=uuid.uuid4().hex)
    tasks.add(asyncio.create_task(chirpstack_mqtt_client.run(), name="chirpstack_mqtt_client"))

    gateway = await get_gateway(chirpstack_api, settings.CHIRPSTACK_GATEWAY_ID)
    logger.info("Using gateway mac", mac=gateway.id)
    lorawan_traffic_handler = LoRaWANTrafficHandler(
        gateway.id, chirpstack_mqtt_client=chirpstack_mqtt_client, devices=chirpstack_devices
    )

    traffic_manager = TrafficManager(ran_core, lorawan_traffic_handler)
    tasks.add(asyncio.create_task(traffic_manager.run(), name="traffic_manager"))

    healthcheck_server = healthcheck.HealthcheckServer(healthcheck_live, healthcheck_ready, None)
    tasks.add(
        asyncio.create_task(
            healthcheck_server.run(settings.HEALTHCHECK_SERVER_HOST, settings.HEALTHCHECK_SERVER_PORT), name="aa"
        )
    )

    # TODO: better stop_event handling
    tasks.add(asyncio.create_task(stop_event.wait(), name="stop_event"))

    finished, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finished_task = finished.pop()
    logger.info("Task stopped: ", task=finished_task.get_name())
    for task in pending:
        task.cancel()


if __name__ == "__main__":
    loop = uvloop.new_event_loop()
    loop.run_until_complete(main())
