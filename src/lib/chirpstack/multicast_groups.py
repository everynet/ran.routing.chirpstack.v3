from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Protocol, Set

from .api import ChirpStackAPI


@dataclass()
class MulticastGroup:
    _groups: MulticastGroupList

    # Multicast group id, UUID in string form
    id: str
    # Multicast group name
    name: Optional[str] = field(default=None)
    # Multicast group address (HEX encoded).
    addr: Optional[str] = field(default=None)
    # Set of devices DevEUI, attached to this group
    devices: Set[str] = field(default_factory=set)
    # Multicast network session key (HEX encoded AES128 key).
    mc_nwk_s_key: Optional[str] = field(default=None)
    # Multicast application session key (HEX encoded AES128 key).
    mc_app_s_key: Optional[str] = field(default=None)

    def __eq__(self, other) -> bool:
        if type(self) != type(other):
            return False
        return (
            self.id == other.id
            and self.name == other.name
            and self.addr == other.addr
            and self.devices == other.devices
            and self.mc_nwk_s_key == other.mc_nwk_s_key
            and self.mc_app_s_key == other.mc_app_s_key
        )

    async def sync_from_remote(self, update_local_list: bool = True, trigger_update_callback: bool = False):
        remote_group: MulticastGroup = await self._groups._pull_group_from_remote(self.id)
        if self.__eq__(remote_group):
            return

        if trigger_update_callback:
            await self._groups.update_hook.on_group_updated(old_group=self, new_group=remote_group)

        if update_local_list:
            self._groups._update_local_group(remote_group)

        self.name = remote_group.name
        self.addr = remote_group.addr
        self.devices = remote_group.devices.copy()
        self.mc_nwk_s_key = remote_group.mc_nwk_s_key
        self.mc_app_s_key = remote_group.mc_app_s_key


class BaseUpdateHook(Protocol):
    # Update callbacks, must be redefined in subclasses
    async def on_group_updated(self, old_group: MulticastGroup, new_group: MulticastGroup) -> None:
        pass

    async def on_group_add(self, group: MulticastGroup) -> None:
        pass

    async def on_group_remove(self, group: MulticastGroup) -> None:
        pass


class _EmptyUpdateHook(BaseUpdateHook):
    async def on_group_updated(self, old_group: MulticastGroup, new_group: MulticastGroup) -> None:
        pass

    async def on_group_add(self, group: MulticastGroup) -> None:
        pass

    async def on_group_remove(self, group: MulticastGroup) -> None:
        pass


class MulticastGroupList(Protocol):
    @abstractmethod
    def get_group_by_addr(self, addr: str) -> Optional[MulticastGroup]:
        pass

    @abstractmethod
    def get_group_by_id(self, group_id: str) -> Optional[MulticastGroup]:
        pass

    @abstractmethod
    async def sync_from_remote(self):
        pass

    @abstractmethod
    def get_all_groups(self) -> list[MulticastGroup]:
        pass

    # Internal methods, required for "Device" interaction
    @abstractmethod
    async def _pull_group_from_remote(self, id: str) -> MulticastGroup:
        pass

    @abstractmethod
    def _update_local_group(self, group: MulticastGroup):
        pass

    @property
    @abstractmethod
    def update_hook(self) -> BaseUpdateHook:
        pass

    @update_hook.setter
    def update_hook(self, hook) -> None:
        pass


class BaseChirpstackMulticastGroupList(MulticastGroupList):
    @property
    def update_hook(self) -> BaseUpdateHook:
        return self._update_hook

    @update_hook.setter
    def update_hook(self, hook) -> None:
        self._update_hook = hook

    def __init__(self, chirpstack_api: ChirpStackAPI, update_hook: None | BaseUpdateHook = None) -> None:
        self._api = chirpstack_api
        self._update_hook = update_hook if update_hook is not None else _EmptyUpdateHook()

    async def _pull_group_from_remote(self, group_id: str) -> MulticastGroup:
        group = MulticastGroup(self, group_id)
        api_group = await self._api.get_multicast_group(group_id)
        group.addr = api_group.multicast_group.mc_addr
        group.name = api_group.multicast_group.name
        group.mc_nwk_s_key = api_group.multicast_group.mc_nwk_s_key
        group.mc_app_s_key = api_group.multicast_group.mc_app_s_key

        async for dev in self._api.get_devices(multicast_group_id=group_id):
            group.devices.add(dev.dev_eui)
        return group


class ApplicationMulticastGroupList(BaseChirpstackMulticastGroupList):
    def __init__(
        self,
        chirpstack_api: ChirpStackAPI,
        application_id: int,
        org_id: int = 0,
        update_hook: None | BaseUpdateHook = None,
    ) -> None:
        super().__init__(chirpstack_api, update_hook=update_hook)
        self._application_id = application_id
        self._org_id = org_id
        self._id_to_group: Dict[str, MulticastGroup] = {}
        self._addr_to_id: Dict[str, str] = {}

    def get_all_groups(self) -> list[MulticastGroup]:
        return list(self._id_to_group.values())

    async def sync_from_remote(self) -> None:
        id_to_group: Dict[str, MulticastGroup] = {}
        addr_to_id: Dict[str, str] = {}

        async for api_group in self._api.get_multicast_groups(
            application_id=self._application_id, organization_id=self._org_id
        ):
            group = await self._pull_group_from_remote(api_group.id)
            id_to_group[group.id] = group
            if group.addr:
                addr_to_id[group.addr] = group.id

            existed_group = self._id_to_group.get(group.id)
            if not existed_group:
                await self.update_hook.on_group_add(group)
                continue

            if existed_group != group:
                await self.update_hook.on_group_updated(old_group=existed_group, new_group=group)

            for group_id, deleted_group in self._id_to_group.items():
                if group_id not in id_to_group:
                    await self.update_hook.on_group_remove(deleted_group)

        self._id_to_group = id_to_group
        self._addr_to_id = addr_to_id

    def _update_local_group(self, group: MulticastGroup):
        # If device not already added, add it now
        if group.id not in self._id_to_group:
            self._id_to_group[group.id] = group
            if group.addr:
                self._addr_to_id[group.addr] = group.id
            return

    def get_group_by_addr(self, addr: str) -> Optional[MulticastGroup]:
        return self._id_to_group.get(self._addr_to_id.get(addr, None), None)  # type: ignore

    def get_group_by_id(self, group_id: str) -> Optional[MulticastGroup]:
        return self._id_to_group.get(group_id, None)


# class MultiApplicationMulticastGroupList(BaseChirpstackMulticastGroupList):
#     def __init__(
#         self, chirpstack_api: ChirpStackAPI, org_id: int = 0, update_hook: None | BaseUpdateHook = None
#     ) -> None:
#         super().__init__(chirpstack_api, update_hook=update_hook)
#         self._applications: Dict[int, ApplicationMulticastGroupList] = {}
#         self._org_id = org_id

#     def get_all_groups(self) -> list[MulticastGroup]:
#         groups = []
#         for application in self._applications.values():
#             groups.extend(application.get_all_groups())
#         return groups

#     async def sync_from_remote(self) -> None:
#         application_ids = set()

#         async for application in self._api.get_applications(self._org_id):
#             if application.id not in self._applications:
#                 app_dev_list = ApplicationMulticastGroupList(
#                     chirpstack_api=self._api,
#                     application_id=application.id,
#                     org_id=self._org_id,
#                     update_hook=self.update_hook,
#                 )
#                 self._applications[application.id] = app_dev_list

#             application_ids.add(application.id)
#             await self._applications[application.id].sync_from_remote()

#         # Removing applications lists, which was deleted
#         for application_id in list(self._applications.keys()):
#             if application_id not in application_ids:
#                 del self._applications[application_id]

#     def get_group_by_addr(self, addr: str) -> Optional[MulticastGroup]:
#         for app_list in self._applications.values():
#             group = app_list.get_group_by_addr(addr)
#             if group:
#                 return group
#         return None

#     def get_group_by_id(self, group_id: str) -> Optional[MulticastGroup]:
#         for app_list in self._applications.values():
#             group = app_list.get_group_by_id(group_id)
#             if group:
#                 return group
#         return None

#     def _update_local_group(self, group: MulticastGroup) -> None:
#         for app_list in self._applications.values():
#             app_group = app_list._id_to_group.get(group.id)
#             if app_group:
#                 app_list._update_local_group(group)
#                 break
#         return None


class _BaseMultiListMulticastGroupList(BaseChirpstackMulticastGroupList):
    _children: dict[Any, BaseChirpstackMulticastGroupList]  # Must be defined in inheritor

    def get_group_by_addr(self, addr: str) -> Optional[MulticastGroup]:
        for children_list in self._children.values():
            group = children_list.get_group_by_addr(addr)
            if group:
                return group
        return None

    def get_group_by_id(self, group_id: str) -> Optional[MulticastGroup]:
        for children_list in self._children.values():
            group = children_list.get_group_by_id(group_id)
            if group:
                return group
        return None

    def _update_local_group(self, group: MulticastGroup) -> None:
        for children_list in self._children.values():
            app_group = children_list.get_group_by_id(group.id)
            if app_group:
                children_list._update_local_group(group)
                break
        return None

    def get_all_groups(self) -> list[MulticastGroup]:
        groups = []
        for application in self._children.values():
            groups.extend(application.get_all_groups())
        return groups


class MultiApplicationMulticastGroupList(_BaseMultiListMulticastGroupList):
    def __init__(
        self,
        chirpstack_api: ChirpStackAPI,
        org_id: int = 0,
        update_hook: None | BaseUpdateHook = None,
    ) -> None:
        super().__init__(chirpstack_api, update_hook=update_hook)
        self._children: Dict[int, ApplicationMulticastGroupList] = {}  # type: ignore
        self._org_id = org_id

    async def sync_from_remote(self) -> None:
        application_ids = set()

        async for application in self._api.get_applications(self._org_id):
            if application.id not in self._children:
                app_dev_list = ApplicationMulticastGroupList(
                    chirpstack_api=self._api,
                    application_id=application.id,
                    org_id=self._org_id,
                    update_hook=self.update_hook,
                )
                self._children[application.id] = app_dev_list

            application_ids.add(application.id)
            await self._children[application.id].sync_from_remote()

        # Removing applications lists, which was deleted
        for application_id in list(self._children.keys()):
            if application_id not in application_ids:
                del self._children[application_id]


class MultiOrgMulticastGroupList(_BaseMultiListMulticastGroupList):
    def __init__(
        self,
        chirpstack_api: ChirpStackAPI,
        update_hook: None | BaseUpdateHook = None,
    ) -> None:
        super().__init__(chirpstack_api, update_hook=update_hook)
        self._children: Dict[str, MultiApplicationMulticastGroupList] = {}  # type: ignore

    async def sync_from_remote(self) -> None:
        org_ids = set()

        async for org in self._api.get_organizations():
            if org.id not in self._children:
                multi_app_dev_list = MultiApplicationMulticastGroupList(
                    chirpstack_api=self._api,
                    org_id=org.id,
                    update_hook=self._update_hook,
                )
                self._children[org.id] = multi_app_dev_list

            org_ids.add(org.id)
            await self._children[org.id].sync_from_remote()

        # Removing applications lists, which was deleted
        for org_id in list(self._children.keys()):
            if org_id not in org_ids:
                del self._children[org_id]
