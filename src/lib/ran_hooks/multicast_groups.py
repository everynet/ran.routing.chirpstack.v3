import structlog
from ran.routing.core import Core as RANCore
from ran.routing.core.multicast_groups.exceptions import ApiMulticastGroupAlreadyExistsError

from lib import chirpstack

logger = structlog.getLogger(__name__)


class RanMulticastGroupsSyncHook(chirpstack.MulticastGroupsUpdateHook):
    def __init__(self, ran_core: RANCore) -> None:
        self.ran_core: RANCore = ran_core

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
                _groups=group._groups,
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
