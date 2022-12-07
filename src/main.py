import asyncio
from contextlib import suppress
import signal
import uuid
from typing import Dict, Optional

import grpc
import structlog
import uvloop
from grpc.aio._channel import Channel
from ran.routing.core import Core as RANCore
from ran.routing.core.multicast_groups.exceptions import ApiMulticastGroupAlreadyExistsError
from ran.routing.core.routing_table.exceptions import ApiDeviceAlreadyExistsError

import settings
from lib import chirpstack, healthcheck, mqtt
from lib.logging_conf import configure_logging
from lib.traffic.chirpstack import ChirpstackTrafficRouter
from lib.traffic.manager import TrafficManager
from lib.traffic.ran import RanTrafficRouter
from lib.utils import Periodic

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


class RanSyncDevices(chirpstack.MultiApplicationDeviceList):
    def __init__(
        self,
        ran_core: RANCore,
        chirpstack_api: chirpstack.ChirpStackAPI,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        self.ran_core = ran_core
        super().__init__(chirpstack_api, tags)

    async def on_device_add(self, device: chirpstack.Device) -> None:
        try:
            dev_eui = int(device.dev_eui, 16)
            dev_addr = None
            if device.dev_addr:
                dev_addr = int(device.dev_addr, 16)
            logger.info("Adding device", dev_eui=device.dev_eui, dev_addr=dev_addr)
            await self.ran_core.routing_table.insert(dev_eui=dev_eui, join_eui=dev_eui, dev_addr=dev_addr)

        except ApiDeviceAlreadyExistsError:
            logger.warning("Device already exists. Attempting to update device.", dev_eui=device.dev_eui)
            # Resync device. Fetch data from remote and use it as old device in sync.
            ran_device = (await self.ran_core.routing_table.select([int(device.dev_eui, 16)]))[0]
            old_device = chirpstack.Device(
                _devices=self,
                dev_eui=device.dev_eui,
                dev_addr=f"{ran_device.active_dev_addr:08x}" if ran_device.active_dev_addr is not None else None,
            )
            await self.on_device_updated(old_device, device)

    async def on_device_remove(self, device: chirpstack.Device) -> None:
        logger.info("Removing device", dev_eui=device.dev_eui)
        dev_eui = int(device.dev_eui, 16)
        await self.ran_core.routing_table.delete([dev_eui])

    async def on_device_updated(self, old_device: chirpstack.Device, new_device: chirpstack.Device) -> None:
        if old_device == new_device:
            logger.info("Device already synced", dev_eui=new_device.dev_eui)
        # if old_device.dev_eui != new_device.dev_eui:
        #     logger.info("Device dev_eui changed, recreating device", old=old_device.dev_eui, new=new_device.dev_eui)
        #     old_dev_eui = int(old_device.dev_eui, 16)
        #     new_dev_eui = int(new_device.dev_eui, 16)
        #     new_dev_addr = None
        #     if new_device.dev_addr:
        #         new_dev_addr = int(new_device.dev_addr, 16)

        #     await self.ran_core.routing_table.delete([old_dev_eui])
        #     await self.ran_core.routing_table.insert(dev_eui=new_dev_eui, join_eui=new_dev_eui, dev_addr=new_dev_addr)
        #     # If we are recreating device, we also set new dev_addr, so we don't want to update it again.
        #     old_device.dev_addr = new_device.dev_addr

        if old_device.dev_addr != new_device.dev_addr:
            dev_eui = int(new_device.dev_eui, 16)
            dev_addr = None
            if new_device.dev_addr:
                dev_addr = int(new_device.dev_addr, 16)

            if dev_addr is None:
                logger.info("No new dev_addr specified for device", dev_eui=dev_eui)
                return

            logger.info(
                "Updating device's dev_addr",
                dev_eui=new_device.dev_eui,
                old_dev_addr=old_device.dev_addr,
                new_dev_addr=new_device.dev_addr,
            )
            # TODO: better update sequence
            await self.ran_core.routing_table.update(dev_eui=dev_eui, join_eui=dev_eui, active_dev_addr=dev_addr)
            # await self.ran_core.routing_table.delete([dev_eui])
            # await self.ran_core.routing_table.insert(dev_eui=dev_eui, join_eui=dev_eui, dev_addr=dev_addr)
            logger.info("Device dev_addr synced", dev_eui=new_device.dev_eui)


class RanSyncMulticastGroups(chirpstack.MultiApplicationMulticastGroupList):
    def __init__(
        self,
        ran_core: RANCore,
        chirpstack_api: chirpstack.ChirpStackAPI,
    ) -> None:
        self.ran_core: RANCore = ran_core
        super().__init__(chirpstack_api)

    async def on_group_add(self, group: chirpstack.MulticastGroup) -> None:
        if not group.addr:
            logger.warning(
                "Multicast group not synced with ran, because it has no assigned 'Addr'",
                group_id=group.id,
            )
            return

        group_addr = int(group.addr, 16)
        try:
            await self.ran_core.multicast_groups.create_multicast_group(name=group.name, addr=group_addr)
            logger.info("Multicast group added", addr=group.addr, name=group.name)
        except ApiMulticastGroupAlreadyExistsError:
            logger.warning("Multicast group already exists. Attempting to sync multicast group.", addr=group.addr)
            # If group already exists, fetch data from remote and perform update, assuming something was changed
            ran_group = (await self.ran_core.multicast_groups.get_multicast_groups(int(group.addr, 16)))[0]
            old_group = chirpstack.MulticastGroup(
                _groups=self,
                id=group.id,
                addr=group.addr,
                name=ran_group.name,
                devices=set(f"{d:016x}" for d in ran_group.devices),
            )
            return await self.on_group_updated(old_group=old_group, new_group=group)

        existed_devices = await self.ran_core.routing_table.select(
            dev_euis=list(int(dev_eui, 16) for dev_eui in group.devices)
        )
        existed_dev_euis = set(f"{device.dev_eui:016x}" for device in existed_devices)
        for device_eui in group.devices:
            if device_eui not in existed_dev_euis:
                logger.warning(
                    "Device was not added to multicast group, because it not present in routing table",
                    addr=group.addr,
                    dev_eui=device_eui,
                )
                continue
            await self.ran_core.multicast_groups.add_device_to_multicast_group(
                addr=group_addr, dev_eui=int(device_eui, 16)
            )
            logger.info("Device added to multicast group", addr=group.addr, dev_eui=device_eui)

        logger.info("Multicast group synced", addr=group.addr)

    async def on_group_remove(self, group: chirpstack.MulticastGroup) -> None:
        if not group.addr:
            logger.warning(
                f"Multicast group with id {group.id!r} not synced with ran, because no dev_addr assigned to it"
            )
            return

        group_addr = int(group.addr, 16)
        await self.ran_core.multicast_groups.delete_multicast_groups([group_addr])
        logger.info("Multicast group removed", addr=group.addr, name=group.name)

    async def on_group_updated(
        self, old_group: chirpstack.MulticastGroup, new_group: chirpstack.MulticastGroup
    ) -> None:
        if old_group == new_group:
            logger.info("Multicast group already in sync", addr=new_group.addr)
            return

        if old_group.addr != new_group.addr or old_group.name != new_group.name:
            if old_group.addr != new_group.addr:
                logger.info(
                    f"Updating multicast group addr: {old_group.addr!r} -> {new_group.addr!r}",
                    old_addr=old_group.addr,
                    new_addr=new_group.addr,
                )
            if old_group.name != new_group.name:
                logger.info(
                    f"Updating multicast group name: {old_group.name!r} -> {new_group.name!r}",
                    addr=new_group.addr,
                    old_name=old_group.name,
                    new_name=new_group.name,
                )

            old_group_addr = int(old_group.addr, 16)  # type: ignore
            new_group_addr = int(new_group.addr, 16)  # type: ignore
            new_name = new_group.name
            await self.ran_core.multicast_groups.update_multicast_group(
                addr=old_group_addr, new_addr=new_group_addr, new_name=new_name
            )
            logger.info("Multicast group updated", addr=new_group.addr)

        if old_group.devices != new_group.devices:
            for dev_to_remove in old_group.devices - new_group.devices:
                device_removed = await self.ran_core.multicast_groups.remove_device_from_multicast_group(
                    addr=int(new_group.addr, 16), dev_eui=int(dev_to_remove, 16)  # type: ignore
                )
                if device_removed:
                    logger.info("Device removed from multicast group", addr=new_group.addr, dev_eui=dev_to_remove)

            devices_to_add = new_group.devices - old_group.devices
            if len(devices_to_add) > 0:
                existed_devices = await self.ran_core.routing_table.select(
                    dev_euis=list(int(dev_eui, 16) for dev_eui in devices_to_add)
                )
                existed_dev_euis = set(f"{device.dev_eui:016x}" for device in existed_devices)
                for dev_eui_to_add in devices_to_add:
                    if dev_eui_to_add not in existed_dev_euis:
                        logger.warning(
                            "Device was not added to multicast group, because it not present in routing table",
                            addr=new_group.addr,
                            dev_eui=dev_eui_to_add,
                        )
                        continue
                    await self.ran_core.multicast_groups.add_device_to_multicast_group(
                        addr=int(new_group.addr, 16), dev_eui=int(dev_eui_to_add, 16)  # type: ignore
                    )
                    logger.info("Device added to multicast group", addr=new_group.addr, dev_eui=dev_eui_to_add)
            logger.info("Multicast group devices synced", addr=new_group.addr)


async def get_gateway(chirpstack_api: chirpstack.ChirpStackAPI, gateway_id: str):
    gateway = await chirpstack_api.get_gateway(gateway_id)
    if not gateway:
        raise Exception("Gateway not found: {}".format(gateway_id))

    return gateway.gateway


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

    logger.info("Using RAN API url: ", coverage=settings.RAN_API_URL)
    ran_core = RANCore(access_token=settings.RAN_TOKEN, url=settings.RAN_API_URL)
    await ran_core.connect()

    logger.info("Cleanup RAN device list")
    await ran_core.routing_table.delete_all()
    logger.info("Cleanup done")

    logger.info("Cleanup RAN multicast groups")
    mcg = await ran_core.multicast_groups.get_multicast_groups()
    await ran_core.multicast_groups.delete_multicast_groups([group.addr for group in mcg])
    logger.info("Cleanup done")

    logger.info("Performing initial ChirpStack devices list sync")
    ran_chirpstack_devices = RanSyncDevices(ran_core, chirpstack_api, tags)
    await ran_chirpstack_devices.sync_from_remote()
    logger.info("Devices synced")

    logger.info("Performing initial ChirpStack multicast groups list sync")
    ran_chirpstack_multicast_groups = RanSyncMulticastGroups(ran_core, chirpstack_api)
    await ran_chirpstack_multicast_groups.sync_from_remote()
    logger.info("Multicast groups synced")

    # Global stop event to stop 'em all!
    stop_event = asyncio.Event()

    def stop_all() -> None:
        stop_event.set()
        logger.warning("Shutting down service!")

    loop.add_signal_handler(signal.SIGHUP, stop_all)
    loop.add_signal_handler(signal.SIGINT, stop_all)
    loop.add_signal_handler(signal.SIGTERM, stop_all)

    tasks = set()
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
    logger.info("MQTT client started", task_name="chirpstack_mqtt_client")

    gateway = await get_gateway(chirpstack_api, settings.CHIRPSTACK_GATEWAY_ID)
    logger.info("Using gateway mac", mac=gateway.id)

    chirpstack_router = ChirpstackTrafficRouter(
        gateway.id,
        chirpstack_mqtt_client,
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

    healthcheck_server = healthcheck.HealthcheckServer(healthcheck_live, healthcheck_ready, None)
    tasks.add(
        asyncio.create_task(
            healthcheck_server.run(stop_event, settings.HEALTHCHECK_SERVER_HOST, settings.HEALTHCHECK_SERVER_PORT),
            name="health_check",
        )
    )
    logger.info("HealthCheck server started", task_name="health_check")

    tasks.add(asyncio.create_task(stop_event.wait(), name="stop_event_wait"))

    finished, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
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
    loop = uvloop.new_event_loop()
    loop.run_until_complete(main())
