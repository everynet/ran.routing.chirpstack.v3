from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional, Protocol

from .api import ChirpStackAPI


@dataclass()
class Device:
    _devices: DeviceList

    # Device EUI
    dev_eui: str
    # Device address (HEX encoded).
    dev_addr: Optional[str] = field(default=None)
    # Application session key (HEX encoded).
    app_s_key: Optional[str] = field(default=None)
    # Network session encryption key (HEX encoded).
    nwk_s_enc_key: Optional[str] = field(default=None)
    # Network root key (HEX encoded).
    # Note: For LoRaWAN 1.0.x, use this field for the LoRaWAN 1.0.x 'AppKey`!
    nwk_key: Optional[str] = field(default=None)
    # Application root key (HEX encoded).
    # Note: This field only needs to be set for LoRaWAN 1.1.x devices!
    app_key: Optional[str] = field(default=None)

    def __eq__(self, other) -> bool:
        if type(self) != type(other):
            return False
        return (
            self.dev_eui == other.dev_eui
            and self.dev_addr == other.dev_addr
            and self.app_s_key == other.app_s_key
            and self.nwk_s_enc_key == other.nwk_s_enc_key
            and self.nwk_key == other.nwk_key
            and self.app_key == other.app_key
        )

    async def sync_from_remote(self, update_local_list: bool = True, trigger_update_callback: bool = False):
        remote_device: Device = await self._devices._pull_device_from_remote(self.dev_eui)
        if self.__eq__(remote_device):
            return

        if trigger_update_callback:
            await self._devices.update_hook.on_device_updated(self, remote_device)

        if update_local_list:
            self._devices._update_local_device(remote_device)

        self.dev_addr = remote_device.dev_addr
        self.app_s_key = remote_device.app_s_key
        self.nwk_s_enc_key = remote_device.nwk_s_enc_key
        self.nwk_key = remote_device.nwk_key
        self.app_key = remote_device.app_key


class BaseUpdateHook(Protocol):
    # Update callbacks, must be redefined in subclasses
    @abstractmethod
    async def on_device_updated(self, old_device: Device, new_device: Device) -> None:
        pass

    @abstractmethod
    async def on_device_add(self, device: Device) -> None:
        pass

    @abstractmethod
    async def on_device_remove(self, device: Device) -> None:
        pass


class _EmptyUpdateHook(BaseUpdateHook):
    async def on_device_updated(self, old_device: Device, new_device: Device) -> None:
        pass

    async def on_device_add(self, device: Device) -> None:
        pass

    async def on_device_remove(self, device: Device) -> None:
        pass


class DeviceList(Protocol):
    @abstractmethod
    def get_device_by_dev_eui(self, dev_eui: str) -> Optional[Device]:
        pass

    @abstractmethod
    def get_device_by_dev_addr(self, dev_addr: str) -> Optional[Device]:
        pass

    @abstractmethod
    def get_all_devices(self) -> list[Device]:
        pass

    # This method must sync devices with remote
    @abstractmethod
    async def sync_from_remote(self):
        pass

    # Internal methods, required for "Device" interaction
    @abstractmethod
    async def _pull_device_from_remote(self, dev_eui: str) -> Device:
        pass

    @abstractmethod
    def _update_local_device(self, device: Device):
        pass

    @property
    @abstractmethod
    def update_hook(self) -> BaseUpdateHook:
        pass

    @update_hook.setter
    def update_hook(self, hook) -> None:
        pass


class BaseChirpstackDeviceList(DeviceList):
    @property
    def update_hook(self) -> BaseUpdateHook:
        return self._update_hook

    @update_hook.setter
    def update_hook(self, hook) -> None:
        self._update_hook = hook

    def __init__(self, chirpstack_api: ChirpStackAPI, update_hook: None | BaseUpdateHook = None) -> None:
        self._api = chirpstack_api
        self._update_hook = update_hook if update_hook is not None else _EmptyUpdateHook()

    async def _pull_device_from_remote(self, dev_eui) -> Device:
        device = Device(_devices=self, dev_eui=dev_eui)
        device_activation = await self._api.get_device_activation(dev_eui)
        if device_activation:
            device.dev_addr = device_activation.dev_addr
            device.app_s_key = device_activation.app_s_key
            device.nwk_s_enc_key = device_activation.nwk_s_enc_key

        device_keys = await self._api.get_device_keys(dev_eui)
        if device_keys:
            device.nwk_key = device_keys.nwk_key
            device.app_key = device_keys.app_key
        return device


class ApplicationDeviceList(BaseChirpstackDeviceList):
    def __init__(
        self,
        chirpstack_api: ChirpStackAPI,
        application_id: int,
        org_id: int = 0,
        tags: Optional[Dict[str, str]] = None,
        update_hook: None | BaseUpdateHook = None,
    ) -> None:
        super().__init__(chirpstack_api, update_hook=update_hook)
        self._application_id = application_id
        self._org_id = (org_id,)
        self._tags = tags if tags is not None else {}
        self._dev_eui_to_device: Dict[str, Device] = {}
        self._dev_addr_to_dev_eui: Dict[str, str] = {}

    def get_all_devices(self) -> list[Device]:
        return list(self._dev_eui_to_device.values())

    async def _get_matched_device_profiles(self) -> AsyncIterator[Any]:
        async for device_profile in await self._api.get_device_profiles(application_id=self._application_id):
            device_profile = await self._api.get_device_profile(device_profile.id)
            for key, value in self._tags.items():
                if key in device_profile.tags:
                    if not value or device_profile.tags[key] == value:
                        yield device_profile

    async def _get_devices_by_device_profile(self, device_profile_ids: list) -> AsyncIterator[Device]:
        async for device in self._api.get_devices(self._application_id):
            if device.device_profile_id in device_profile_ids:
                yield device

    async def sync_from_remote(self) -> None:
        dev_eui_to_device: Dict[str, Device] = {}
        dev_addr_to_dev_eui: Dict[str, str] = {}

        matched_devices_profile_ids = [mdp.id async for mdp in self._get_matched_device_profiles()]
        async for device in self._get_devices_by_device_profile(matched_devices_profile_ids):
            dev_eui_to_device[device.dev_eui] = await self._pull_device_from_remote(device.dev_eui)

        async for device in self._api.get_devices(self._application_id, self._tags):
            dev_eui_to_device[device.dev_eui] = await self._pull_device_from_remote(device.dev_eui)

        for dev_eui, device in dev_eui_to_device.items():
            if device.dev_addr:
                dev_addr_to_dev_eui[device.dev_addr] = device.dev_eui

            existed_device = self._dev_eui_to_device.get(dev_eui)
            if not existed_device:
                await self.update_hook.on_device_add(device)
                continue

            if existed_device != device:
                await self.update_hook.on_device_updated(existed_device, device)

        for dev_eui, device in self._dev_eui_to_device.items():
            if dev_eui not in dev_eui_to_device:
                await self.update_hook.on_device_remove(device)

        self._dev_eui_to_device = dev_eui_to_device
        self._dev_addr_to_dev_eui = dev_addr_to_dev_eui

    def _update_local_device(self, device: Device) -> None:
        """
        This method adds device changes into local state

        :param device: Updated device
        :type device: Device
        """
        if device.dev_eui not in self._dev_eui_to_device:
            self._dev_eui_to_device[device.dev_eui] = device
            if device.dev_addr:
                self._dev_addr_to_dev_eui[device.dev_addr] = device.dev_eui
            return

        dev_addr = self._dev_eui_to_device.get(device.dev_eui).dev_addr  # type: ignore # it will already exist in dict
        if dev_addr != device.dev_addr:
            self._dev_addr_to_dev_eui.pop(dev_addr, None)  # type: ignore
            if device.dev_addr:
                self._dev_addr_to_dev_eui[device.dev_addr] = device.dev_eui

    def get_device_by_dev_eui(self, dev_eui: str) -> Optional[Device]:
        return self._dev_eui_to_device.get(dev_eui)

    def get_device_by_dev_addr(self, dev_addr: str) -> Optional[Device]:
        return self._dev_eui_to_device.get(self._dev_addr_to_dev_eui.get(dev_addr, None), None)  # type: ignore


# class MultiApplicationDeviceList(BaseChirpstackDeviceList):
#     def __init__(
#         self,
#         chirpstack_api: ChirpStackAPI,
#         tags: Optional[Dict[str, str]] = None,
#         org_id: int = 0,
#         update_hook: None | BaseUpdateHook = None,
#     ) -> None:
#         super().__init__(chirpstack_api, update_hook=update_hook)
#         self._applications: Dict[int, ApplicationDeviceList] = {}
#         self._tags = tags if tags is not None else {}
#         self._org_id = org_id

#     async def sync_from_remote(self) -> None:
#         application_ids = set()

#         async for application in self._api.get_applications(organization_id=self._org_id):
#             if application.id not in self._applications:
#                 app_dev_list = ApplicationDeviceList(
#                     chirpstack_api=self._api,
#                     application_id=application.id,
#                     org_id=self._org_id,
#                     tags=self._tags,
#                     update_hook=self.update_hook,
#                 )
#                 self._applications[application.id] = app_dev_list

#             application_ids.add(application.id)
#             await self._applications[application.id].sync_from_remote()

#         # Removing applications lists, which was deleted
#         for application_id in list(self._applications.keys()):
#             if application_id not in application_ids:
#                 del self._applications[application_id]

#     def get_device_by_dev_eui(self, dev_eui: str) -> Optional[Device]:
#         for app_list in self._applications.values():
#             device = app_list.get_device_by_dev_eui(dev_eui)
#             if device:
#                 return device
#         return None

#     def get_device_by_dev_addr(self, dev_addr: str) -> Optional[Device]:
#         for app_list in self._applications.values():
#             device = app_list.get_device_by_dev_addr(dev_addr)
#             if device:
#                 return device
#         return None

#     def _update_local_device(self, device) -> None:
#         for app_list in self._applications.values():
#             app_device = app_list.get_device_by_dev_eui(device.dev_eui)
#             if app_device:
#                 app_list._update_local_device(device)
#                 break
#         return None

#     def get_all_devices(self):
#         all_devices = []
#         for app_list in self._applications.values():
#             all_devices.extend(app_list.get_all_devices())
#         return all_devices


class _BaseMultiListDeviceList(BaseChirpstackDeviceList):
    _children: dict[Any, BaseChirpstackDeviceList]  # Must be defined in inheritor

    def get_device_by_dev_eui(self, dev_eui: str) -> Optional[Device]:
        for children_list in self._children.values():
            device = children_list.get_device_by_dev_eui(dev_eui)
            if device:
                return device
        return None

    def get_device_by_dev_addr(self, dev_addr: str) -> Optional[Device]:
        for children_list in self._children.values():
            device = children_list.get_device_by_dev_addr(dev_addr)
            if device:
                return device
        return None

    def _update_local_device(self, device: Device) -> None:
        for children_list in self._children.values():
            app_device = children_list.get_device_by_dev_eui(device.dev_eui)
            if app_device:
                children_list._update_local_device(device)
                break
        return None

    def get_all_devices(self):
        all_devices = []
        for children_list in self._children.values():
            all_devices.extend(children_list.get_all_devices())
        return all_devices


class MultiApplicationDeviceList(_BaseMultiListDeviceList):
    def __init__(
        self,
        chirpstack_api: ChirpStackAPI,
        tags: Optional[Dict[str, str]] = None,
        org_id: int = 0,
        update_hook: None | BaseUpdateHook = None,
    ) -> None:
        super().__init__(chirpstack_api, update_hook=update_hook)
        self._children: Dict[int, ApplicationDeviceList] = {}  # type: ignore
        self._tags = tags if tags is not None else {}
        self._org_id = org_id

    async def sync_from_remote(self) -> None:
        application_ids = set()

        async for application in self._api.get_applications(organization_id=self._org_id):
            if application.id not in self._children:
                app_dev_list = ApplicationDeviceList(
                    chirpstack_api=self._api,
                    application_id=application.id,
                    org_id=self._org_id,
                    tags=self._tags,
                    update_hook=self.update_hook,
                )
                self._children[application.id] = app_dev_list

            application_ids.add(application.id)
            await self._children[application.id].sync_from_remote()

        # Removing applications lists, which was deleted
        for application_id in list(self._children.keys()):
            if application_id not in application_ids:
                del self._children[application_id]


class MultiOrgDeviceList(_BaseMultiListDeviceList):
    def __init__(
        self,
        chirpstack_api: ChirpStackAPI,
        tags: Optional[Dict[str, str]] = None,
        update_hook: None | BaseUpdateHook = None,
    ) -> None:
        super().__init__(chirpstack_api, update_hook=update_hook)
        self._children: Dict[str, MultiApplicationDeviceList] = {}  # type: ignore
        self._tags = tags if tags is not None else {}

    async def sync_from_remote(self) -> None:
        org_ids = set()

        async for org in self._api.get_organizations():
            if org.id not in self._children:
                multi_app_dev_list = MultiApplicationDeviceList(
                    chirpstack_api=self._api,
                    org_id=org.id,
                    update_hook=self._update_hook,
                    tags=self._tags,
                )
                self._children[org.id] = multi_app_dev_list

            org_ids.add(org.id)
            await self._children[org.id].sync_from_remote()

        # Removing applications lists, which was deleted
        for org_id in list(self._children.keys()):
            if org_id not in org_ids:
                del self._children[org_id]
