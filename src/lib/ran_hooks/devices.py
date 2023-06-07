import structlog
from ran.routing.core import Core as RANCore
from ran.routing.core.routing_table.exceptions import ApiDeviceAlreadyExistsError

from lib import chirpstack

logger = structlog.getLogger(__name__)


class RanDevicesSyncHook(chirpstack.DevicesUpdateHook):
    def __init__(self, ran_core: RANCore) -> None:
        self.ran_core = ran_core

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
                _devices=device._devices,
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
