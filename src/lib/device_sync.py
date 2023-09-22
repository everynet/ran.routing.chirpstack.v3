from dataclasses import dataclass, field

import structlog
from ran.routing.core import Core as RANCore
from ran.routing.core.domains import Device as RanDevice

from lib.chirpstack import Device as ChirpstackDevice
from lib.chirpstack import DeviceList

logger = structlog.getLogger(__name__)


def is_all_none(*args):
    return all(arg is None for arg in args)


@dataclass()
class Counters:
    activation: int = field(default=0)
    reactivation: int = field(default=0)
    deactivation: int = field(default=0)
    update: int = field(default=0)
    noop: int = field(default=0)

    def reset(self):
        self.activation = 0
        self.reactivation = 0
        self.deactivation = 0
        self.update = 0
        self.noop = 0

    def as_dict(self):
        return {
            "activation": self.activation,
            "reactivation": self.reactivation,
            "deactivation": self.deactivation,
            "update": self.update,
            "noop": self.noop,
        }


# DeviceSync algorithm:
# - Fetch all active devices from RAN and ChirpStack, and check them by 3 following criteria
# - If device.dev_eui exist in both ChirpStack and RAN:
#     - If device.join_eui is different - reactivate* device.
#     - If device.join_eui and device.dev_addr matches - do nothing
#       - (Clarification) If both ChirpStack and RAN device.dev_addr are None - do nothing
#     - If device.join_eui are same, but device.dev_addr is different:
#         - If ChirpStack device.dev_addr is None, but RAN device.dev_addr is not - reactivate* device with empty active_dev_addr/target_dev_addr
#         - If device has join_eui field - perform update with "RoutingTable.update" method, by setting "active_dev_addr" and nulling "target_dev_addr"
#         - If device does not have join_eui field - reactivate* device
# - If device.dev_eui exist in ChirpStack, but not in RAN:
#     - Add device to ChirpStack with "RoutingTable.insert"
# - If device.dev_eui exist in RAN, but not in ChirpStack:
#     - Delete device from ChirpStack with "RoutingTable.delete" (TODO: ensure this is correct behavior)
# * Reactivation is sequential call of "RoutingTable.delete" and "RoutingTable.insert"
class DeviceSync:
    def __init__(self, ran: RANCore, device_list: DeviceList) -> None:
        self.ran = ran
        self.device_list = device_list
        self.cnt = Counters()

    async def _fetch_chirpstack_devices(self) -> list[ChirpstackDevice]:
        logger.info("Fetching devices from ChirpStack...")
        await self.device_list.sync_from_remote(trigger_update_hook=False)
        devices = self.device_list.get_all_devices()
        logger.info(f"Obtained {len(devices)} devices from ChirpStack")
        return devices

    async def _fetch_ran_devices(self) -> list[RanDevice]:
        logger.info("Fetching devices from RAN...")
        # TODO: this is not the best way to select all devices
        devices = await self.ran.routing_table.select()
        logger.info(f"Obtained {len(devices)} devices from RAN")
        return devices

    async def _handle_both_exist(self, cs_device: ChirpstackDevice, ran_device: RanDevice) -> None:
        cs_dev_addr = None
        if cs_device.dev_addr is not None:
            cs_dev_addr = int(cs_device.dev_addr, 16)

        # TODO: handle real join_eui after removing "dev_eui as join_eui" hack
        if ran_device.join_eui is not None and int(cs_device.dev_eui, 16) != ran_device.join_eui:
            await self.ran.routing_table.delete([ran_device.dev_eui])
            await self.ran.routing_table.insert(
                dev_eui=ran_device.dev_eui, join_eui=int(cs_device.dev_eui, 16), dev_addr=cs_dev_addr
            )
            self.cnt.reactivation += 1
            logger.info(
                "[Reactivation] JoinEui not match",
                dev_eui=cs_device.dev_eui,
                dev_addr=cs_device.dev_addr,
            )
            return

        if cs_dev_addr is None and is_all_none(ran_device.target_dev_addr, ran_device.active_dev_addr):
            self.cnt.noop += 1
            logger.info("[Noop] Nothing to update - no dev_addr set", dev_eui=cs_device.dev_eui)
            return

        if cs_dev_addr is None and not is_all_none(ran_device.target_dev_addr, ran_device.active_dev_addr):
            await self.ran.routing_table.delete([ran_device.dev_eui])
            await self.ran.routing_table.insert(
                dev_eui=ran_device.dev_eui, join_eui=ran_device.join_eui, dev_addr=cs_dev_addr
            )
            self.cnt.reactivation += 1
            logger.info(
                "[Reactivation] CS device has no dev_addr, but RAN device has",
                dev_eui=cs_device.dev_eui,
                dev_addr=cs_device.dev_addr,
            )
            return

        if cs_dev_addr is not None and (
            (cs_dev_addr == ran_device.active_dev_addr and ran_device.target_dev_addr is None)
            or (cs_dev_addr == ran_device.target_dev_addr and ran_device.active_dev_addr is None)
            # This case is not reachable under regular conditions, but added to make this branch logically complete.
            or (cs_dev_addr == ran_device.target_dev_addr and cs_dev_addr == ran_device.active_dev_addr)
        ):
            self.cnt.noop += 1
            logger.info(
                "[Noop] Nothing to update - dev_addr match",
                dev_eui=cs_device.dev_eui,
                dev_addr=cs_device.dev_addr,
            )
            return
        else:
            if ran_device.join_eui:
                await self.ran.routing_table.update(
                    dev_eui=ran_device.dev_eui,
                    join_eui=ran_device.join_eui,
                    active_dev_addr=cs_dev_addr,
                    target_dev_addr=None,
                )
                self.cnt.update += 1
                logger.info(
                    "[Update] Device updated",
                    dev_eui=cs_device.dev_eui,
                    dev_addr=cs_device.dev_addr,
                )
            else:
                await self.ran.routing_table.delete([ran_device.dev_eui])
                await self.ran.routing_table.insert(dev_eui=int(cs_device.dev_eui, 16), dev_addr=cs_dev_addr)
                self.cnt.reactivation += 1
                logger.info(
                    "[Reactivation] Device reactivated",
                    dev_eui=cs_device.dev_eui,
                    dev_addr=cs_device.dev_addr,
                )

    async def _handle_only_in_chirpstack(self, cs_device: ChirpstackDevice) -> None:
        # TODO: ChirpStack does not provide real join_eui in simple way, so we using devices "dev_eui" as "join_eui".
        # This hack is supported by ran-routing, but must be removed in future.
        dev_eui = int(cs_device.dev_eui, 16)
        dev_addr = None
        if cs_device.dev_addr is not None:
            dev_addr = int(cs_device.dev_addr, 16)
        join_eui = int(cs_device.dev_eui, 16)

        await self.ran.routing_table.insert(dev_eui=dev_eui, dev_addr=dev_addr, join_eui=join_eui)
        self.cnt.activation += 1
        logger.info("[Activation] Device added to RAN", dev_eui=cs_device.dev_eui, dev_addr=cs_device.dev_addr)

    async def _handle_only_in_ran(self, ran_device: RanDevice) -> None:
        await self.ran.routing_table.delete([ran_device.dev_eui])
        self.cnt.deactivation += 1
        logger.info(
            "[Deactivation] Device deleted from RAN",
            dev_eui=hex(ran_device.dev_eui).lstrip("0x"),
            dev_addr=ran_device.active_dev_addr,
        )

    async def perform_full_sync(self):
        cs_devices = await self._fetch_chirpstack_devices()
        ran_devices = await self._fetch_ran_devices()

        cs_devices_map: dict[str, ChirpstackDevice] = {dev.dev_eui: dev for dev in cs_devices}
        ran_devices_map: dict[str, RanDevice] = {hex(dev.dev_eui).lstrip("0x"): dev for dev in ran_devices}

        cs_dev_euis: set[str] = set(cs_devices_map.keys())
        ran_dev_euis: set[str] = set(ran_devices_map.keys())

        # Devices in both ChirpStack and RAN
        for common_dev_eui in cs_dev_euis.intersection(ran_dev_euis):
            # logger.debug("Device exist in both RAN and ChirpStack, performing sync", dev_eui=common_dev_eui)
            await self._handle_both_exist(cs_devices_map[common_dev_eui], ran_devices_map[common_dev_eui])

        # Devices only in ChirpStack
        for only_cs_dev_eui in cs_dev_euis - ran_dev_euis:
            # logger.debug("ChirpStack device not found in RAN, adding device", dev_eui=only_cs_dev_eui)
            await self._handle_only_in_chirpstack(cs_devices_map[only_cs_dev_eui])

        # Devices only in RAN
        for only_ran_dev_eui in ran_dev_euis - cs_dev_euis:
            # logger.debug("Device in RAN is not exist in ChirpStack, removing device", dev_eui=only_ran_dev_eui)
            await self._handle_only_in_ran(ran_devices_map[only_ran_dev_eui])

        logger.warning("Devices synced, summary: ", **self.cnt.as_dict())
        self.cnt.reset()
