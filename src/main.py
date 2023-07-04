import asyncio
import signal
import sys
import uuid
from contextlib import suppress
from typing import Optional

import grpc
import structlog
import uvloop
from grpc.aio._channel import Channel
from ran.routing.core import Core as RANCore

import settings
from lib import chirpstack, healthcheck, mqtt
from lib.logging_conf import configure_logging
from lib.ran_hooks import RanDevicesSyncHook, RanMulticastGroupsSyncHook
from lib.traffic.chirpstack import ChirpstackTrafficRouter
from lib.traffic.manager import TrafficManager
from lib.traffic.ran import RanTrafficRouter
from lib.utils import Periodic

STOP_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)

logger = structlog.getLogger(__name__)


def get_grpc_channel(host: str, port: str, secure: bool = True, cert_path: Optional[str] = None) -> Channel:
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


async def main(loop):
    configure_logging(log_level=settings.LOG_LEVEL, console_colors=settings.LOG_COLORS)

    # Global stop event to stop 'em all!
    stop_event = asyncio.Event()

    # Event to signal when service is ready
    service_ready = asyncio.Event()

    async def healthcheck_live(context):
        if stop_event.is_set():
            return False
        return True

    async def healthcheck_ready(context):
        if service_ready.is_set():
            return True
        return False

    # App termination handler
    def stop_all() -> None:
        stop_event.set()
        logger.warning("Shutting down service! Press ^C again to terminate")

        def terminate():
            sys.exit("\nTerminated!\n")

        for sig in STOP_SIGNALS:
            loop.remove_signal_handler(sig)
            loop.add_signal_handler(sig, terminate)

    for sig in STOP_SIGNALS:
        loop.add_signal_handler(sig, stop_all)

    healthcheck_server = healthcheck.HealthcheckServer(healthcheck_live, healthcheck_ready, context=None)
    tasks = set()
    tasks.add(
        asyncio.create_task(
            healthcheck_server.run(stop_event, settings.HEALTHCHECK_SERVER_HOST, settings.HEALTHCHECK_SERVER_PORT),
            name="health_check",
        )
    )
    logger.info("HealthCheck server task created", task_name="health_check")

    grpc_channel = get_grpc_channel(
        settings.CHIRPSTACK_API_GRPC_HOST,
        settings.CHIRPSTACK_API_GRPC_PORT,
        settings.CHIRPSTACK_API_GRPC_SECURE,
        settings.CHIRPSTACK_API_GRPC_CERT_PATH,
    )

    tags = get_tags(settings.CHIRPSTACK_MATCH_TAGS)
    logger.info("Chirpstack object selector: ", tags=tags)

    chirpstack_api = chirpstack.ChirpStackAPI(grpc_channel, settings.CHIRPSTACK_API_TOKEN)

    logger.info("Using RAN API url: ", coverage=settings.RAN_API_URL)
    ran_core = RANCore(access_token=settings.RAN_TOKEN, url=settings.RAN_API_URL)
    await ran_core.connect()

    logger.info("Performing initial ChirpStack devices list sync")

    ran_devices_sync_hook = RanDevicesSyncHook(ran_core=ran_core)
    ran_multicast_groups_sync_hook = RanMulticastGroupsSyncHook(ran_core=ran_core)

    if settings.CHIRPSTACK_ORGANIZATION_ID == 0:
        if not await chirpstack_api.has_global_api_token():
            logger.error("Invalid api token type")
            print(
                "\nCHIRPSTACK_API_TOKEN you use has lack of 'list organizations' permissions. "
                "This error can happen if you are trying to use organization token for multi-organization mode."
                "\n  - If you want to use ran-bridge for multiple organizations, specify global api key as "
                "CHIRPSTACK_API_TOKEN."
                "\n  - If you want to use ran-bridge for specific organizations with this api key, specify "
                "CHIRPSTACK_ORGANIZATION_ID.\n",
                flush=True,
            )
            await ran_core.close()
            return

        logger.warning(
            "CHIRPSTACK_ORGANIZATION_ID is set to 0. Starting in multi-organization mode",
            handling_organizations=[org.name async for org in chirpstack_api.get_organizations()],
        )
        ran_chirpstack_devices = chirpstack.MultiOrgDeviceList(
            chirpstack_api=chirpstack_api,
            tags=tags,
            update_hook=ran_devices_sync_hook,
        )
        ran_chirpstack_multicast_groups = chirpstack.MultiOrgMulticastGroupList(
            chirpstack_api=chirpstack_api,
            update_hook=ran_multicast_groups_sync_hook,
        )
    else:
        chirpstack_org = await chirpstack_api.get_organization(org_id=settings.CHIRPSTACK_ORGANIZATION_ID)
        if chirpstack_org is None:
            logger.error(
                "ChirpStack organization with this id not found."
                "Ensure you provide correct CHIRPSTACK_ORGANIZATION_ID.",
                org_id=settings.CHIRPSTACK_ORGANIZATION_ID
            )
            await ran_core.close()
            return

        logger.warning("Starting in single-organization mode", handling_org=[chirpstack_org.name])
        if settings.CHIRPSTACK_APPLICATION_ID == 0:
            logger.warning(
                "CHIRPSTACK_APPLICATION_ID is set to 0. Starting in multi-application mode",
                handling_applications=[app.name async for app in chirpstack_api.get_applications(organization_id=settings.CHIRPSTACK_ORGANIZATION_ID)],
            )
            ran_chirpstack_devices = chirpstack.MultiApplicationDeviceList(
                chirpstack_api=chirpstack_api,
                tags=tags,
                org_id=settings.CHIRPSTACK_ORGANIZATION_ID,
                update_hook=ran_devices_sync_hook,
            )
            ran_chirpstack_multicast_groups = chirpstack.MultiApplicationMulticastGroupList(
                chirpstack_api=chirpstack_api,
                org_id=settings.CHIRPSTACK_ORGANIZATION_ID,
                update_hook=ran_multicast_groups_sync_hook,
            )
        else:
            chirpstack_app = await chirpstack_api.get_application(app_id=settings.CHIRPSTACK_APPLICATION_ID)
            if chirpstack_app is None:
                logger.error(
                    "ChirpStack application with this id not found."
                    "Ensure you provide correct CHIRPSTACK_APPLICATION_ID.",
                    app_id=settings.CHIRPSTACK_APPLICATION_ID
                )
                await ran_core.close()
                return

            logger.warning("Starting in single-application mode", handling_app=[chirpstack_app.name])
            ran_chirpstack_devices = chirpstack.ApplicationDeviceList(
                chirpstack_api=chirpstack_api,
                tags=tags,
                org_id=settings.CHIRPSTACK_ORGANIZATION_ID,
                application_id=settings.CHIRPSTACK_APPLICATION_ID,
                update_hook=ran_devices_sync_hook,
            )
            ran_chirpstack_multicast_groups = chirpstack.ApplicationMulticastGroupList(
                chirpstack_api=chirpstack_api,
                org_id=settings.CHIRPSTACK_ORGANIZATION_ID,
                application_id=settings.CHIRPSTACK_APPLICATION_ID,
                update_hook=ran_multicast_groups_sync_hook,
            )

    logger.info("Cleanup RAN device list")
    await ran_core.routing_table.delete_all()
    logger.info("Cleanup done")

    logger.info("Cleanup RAN multicast groups")
    mcg = await ran_core.multicast_groups.get_multicast_groups()
    await ran_core.multicast_groups.delete_multicast_groups([group.addr for group in mcg])
    logger.info("Cleanup done")

    logger.info("Performing initial ChirpStack devices list sync")
    await ran_chirpstack_devices.sync_from_remote()
    logger.info("Devices synced")

    logger.info("Performing initial ChirpStack multicast groups list sync")
    await ran_chirpstack_multicast_groups.sync_from_remote()
    logger.info("Multicast groups synced")

    tasks.add(
        Periodic(ran_chirpstack_devices.sync_from_remote).create_task(
            stop_event,
            interval=settings.CHIRPSTACK_DEVICES_REFRESH_PERIOD,
            task_name="update_chirpstack_device_list",
        )
    )
    logger.info("Periodic devices list sync scheduled", task_name="update_chirpstack_device_list")

    tasks.add(
        Periodic(ran_chirpstack_multicast_groups.sync_from_remote).create_task(
            stop_event,
            interval=settings.CHIRPSTACK_DEVICES_REFRESH_PERIOD,
            task_name="update_chirpstack_multicast_groups_list",
        )
    )
    logger.info("Periodic multicast groups list sync scheduled", task_name="update_chirpstack_multicast_groups_list")

    chirpstack_mqtt_client = mqtt.MQTTClient(settings.CHIRPSTACK_MQTT_SERVER_URI, client_id=uuid.uuid4().hex)
    tasks.add(asyncio.create_task(chirpstack_mqtt_client.run(stop_event), name="chirpstack_mqtt_client"))
    logger.info("MQTT client task started", task_name="chirpstack_mqtt_client")

    gateway = await get_gateway(chirpstack_api, settings.CHIRPSTACK_GATEWAY_ID)
    logger.info("Using gateway mac", mac=gateway.id)

    chirpstack_router = ChirpstackTrafficRouter(
        gateway.id,
        chirpstack_mqtt_client,
        settings.CHIRPSTACK_UPLINK_TOPIC_TEMPLATE,
        settings.CHIRPSTACK_DOWNLINK_TOPIC_TEMPLATE,
        settings.CHIRPSTACK_DOWNLINK_ACK_TOPIC_TEMPLATE,
        devices=ran_chirpstack_devices,
        multicast_groups=ran_chirpstack_multicast_groups,
    )
    tasks.add(asyncio.create_task(chirpstack_router.run(stop_event), name="chirpstack_traffic_router"))
    logger.info("Chirpstack traffic router started", task_name="chirpstack_traffic_router")

    ran_router = RanTrafficRouter(ran_core)
    tasks.add(asyncio.create_task(ran_router.run(stop_event), name="ran_traffic_router"))
    logger.info("Ran traffic router started", task_name="ran_traffic_router")

    manager = TrafficManager(chirpstack=chirpstack_router, ran=ran_router)
    tasks.add(asyncio.create_task(manager.run(stop_event), name="traffic_manager"))
    logger.info("TrafficManager started", task_name="traffic_manager")

    tasks.add(asyncio.create_task(stop_event.wait(), name="stop_event_wait"))

    service_ready.set()
    finished, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    service_ready.clear()

    finished_task = finished.pop()
    logger.warning(f"Task {finished_task.get_name()!r} exited, shutting down gracefully")

    graceful_shutdown_max_time = 20  # seconds
    for task in pending:
        with suppress(asyncio.TimeoutError):
            logger.debug(f"Waiting task {task.get_name()!r} to shutdown gracefully")
            await asyncio.wait_for(task, graceful_shutdown_max_time / len(tasks))
            logger.debug(f"Task {task.get_name()!r} exited")

    # If tasks not exited gracefully, terminate them by cancelling
    for task in pending:
        if not task.done():
            task.cancel()

    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            logger.warning(f"Task {task.get_name()!r} terminated")

    logger.info("Bye!")


if __name__ == "__main__":
    if sys.version_info >= (3, 11):
        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(main(runner.get_loop()))
    else:
        event_loop = uvloop.new_event_loop()
        event_loop.run_until_complete(main(event_loop))
