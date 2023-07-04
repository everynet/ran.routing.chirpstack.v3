import asyncio
from typing import Any, AsyncIterator, Dict, Optional

import aiohttp
import grpc
from chirpstack_api.as_pb.external import api
from chirpstack_api.as_pb.external.api import device_pb2, deviceProfile_pb2, profiles_pb2
from chirpstack_api.common import common_pb2
from grpc.aio._channel import Channel
from yarl import URL


def suppress_rpc_error(codes: Optional[list] = None):
    codes = codes if codes is not None else []

    def wrapped(func):
        async def wrapped(*args, **kwargs):
            try:
                value = func(*args, **kwargs)
                if asyncio.iscoroutine(value):
                    return await value
                return value
            except grpc.aio.AioRpcError as e:
                if e.code() in codes:
                    return None
                raise e

        return wrapped

    return wrapped


class ChirpStackInternalAPI:
    def __init__(self, url: str, user: str, password: str):
        self._base_url = URL(url) / "api/internal"
        self._user = user
        self._password = password
        self._jwt_token = None

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        request_url = str(self._base_url / path.lstrip("/"))
        headers = kwargs.pop("headers", {})

        if self._jwt_token:
            headers["Grpc-Metadata-Authorization"] = "Bearer {}".format(self._jwt_token)

        async with aiohttp.ClientSession() as session:
            async with session.request(method, request_url, headers=headers, **kwargs) as response:
                response.raise_for_status()
                return await response.json()

    async def authenticate(self):
        auth_data = await self._request("post", "login", json={"email": self._user, "password": self._password})
        self._jwt_token = auth_data["jwt"]
        return auth_data

    async def profile(self):
        return await self._request("get", "profile")

    async def _get_paginated_data(self, method: str, path: str, batch_size: int = 20, **kwargs) -> AsyncIterator[Any]:
        query_params = kwargs.pop("params", {})
        query_params.update({"limit": batch_size, "offset": 0})

        while True:
            response_json = await self._request(method, path, params=query_params, **kwargs)

            if not response_json["result"]:
                break

            for item in response_json["result"]:
                yield item

            query_params["offset"] += batch_size

    async def get_api_keys(self, batch_size=100):
        async for api_key in self._get_paginated_data("get", "api-keys", batch_size=batch_size):
            yield api_key

    async def create_api_key(self, name: str, is_admin: bool, organization_id: int = 0, application_id: int = 0):
        payload = {
            "apiKey": {
                "name": name,
                "isAdmin": is_admin,
                "organizationID": organization_id,
                "applicationID": application_id,
            }
        }
        return await self._request("post", "api-keys", json=payload)


class ChirpStackAPI:
    def __init__(self, grpc_channel: Channel, api_token: str) -> None:
        self._auth_token = [("authorization", "Bearer %s" % api_token)]
        self._channel = grpc_channel

    async def _get_paginated_data(self, request: Any, method: Any, batch_size=20) -> AsyncIterator[Any]:
        while True:
            response = await method(request, metadata=self._auth_token)
            request.offset += batch_size
            if not response.result:
                break
            for result in response.result:
                yield result

    async def get_devices(
        self,
        application_id: Optional[int] = None,
        tags: Optional[Dict[str, str]] = None,
        service_profile_id: Optional[str] = None,
        multicast_group_id: Optional[str] = None,
        batch_size: int = 20,
    ) -> AsyncIterator[device_pb2.DeviceListItem]:
        tags = tags if tags is not None else {}
        client = api.DeviceServiceStub(self._channel)

        req = api.ListDeviceRequest()
        req.limit = batch_size
        req.offset = 0

        if application_id is not None:
            req.application_id = application_id

        if service_profile_id is not None:
            req.service_profile_id = service_profile_id

        if multicast_group_id is not None:
            req.multicast_group_id = multicast_group_id

        if tags:
            for key, value in tags.items():
                req.tags[key] = value

        async for device in self._get_paginated_data(req, client.List, batch_size):
            yield device

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device_keys(self, dev_eui: str) -> api.GetDeviceKeysResponse:
        client = api.DeviceServiceStub(self._channel)

        req = api.DeviceKeys()
        req.dev_eui = dev_eui

        res = await client.GetKeys(req, metadata=self._auth_token)
        return res.device_keys

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device_activation(self, dev_eui: str) -> device_pb2.GetDeviceActivationResponse:
        client = api.DeviceServiceStub(self._channel)

        req = api.DeviceActivation()
        req.dev_eui = dev_eui

        res = await client.GetActivation(req, metadata=self._auth_token)
        return res.device_activation

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device_profiles(
        self, application_id: int, batch_size: int = 20
    ) -> AsyncIterator[deviceProfile_pb2.DeviceProfileListItem]:
        client = api.DeviceProfileServiceStub(self._channel)

        req = api.ListDeviceProfileRequest()
        req.application_id = application_id
        req.limit = batch_size
        req.offset = 0

        async for device_profile in self._get_paginated_data(req, client.List, batch_size):
            yield device_profile

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device_profile(self, device_profile_id: str) -> profiles_pb2.DeviceProfile:
        client = api.DeviceProfileServiceStub(self._channel)

        req = api.GetDeviceProfileRequest()
        req.id = device_profile_id

        res = await client.Get(req, metadata=self._auth_token)
        return res.device_profile

    async def get_network_servers(
        self, organization_id: Optional[int] = None, batch_size: int = 20
    ) -> AsyncIterator[api.NetworkServerListItem]:
        client = api.NetworkServerServiceStub(self._channel)

        req = api.ListNetworkServerRequest()
        if organization_id is not None:
            req.organization_id = organization_id
        req.limit = batch_size
        req.offset = 0

        async for network_server in self._get_paginated_data(req, client.List, batch_size):
            yield network_server

    async def create_network_server(self, **kwargs) -> int:
        client = api.NetworkServerServiceStub(self._channel)

        req = api.CreateNetworkServerRequest()
        network_server = req.network_server
        network_server.name = kwargs["name"]
        network_server.server = kwargs.get("server", "chirpstack-network-server:8000")  # docker-compose default
        network_server.ca_cert = kwargs.get("ca_cert", "")
        network_server.tls_cert = kwargs.get("tls_cert", "")
        network_server.tls_key = kwargs.get("tls_key", "")
        network_server.routing_profile_ca_cert = kwargs.get("routing_profile_ca_cert", "")
        network_server.routing_profile_tls_cert = kwargs.get("routing_profile_tls_cert", "")
        network_server.routing_profile_tls_key = kwargs.get("routing_profile_tls_key", "")
        network_server.gateway_discovery_enabled = kwargs.get("gateway_discovery_enabled", False)
        network_server.gateway_discovery_interval = kwargs.get("gateway_discovery_interval", 0)
        network_server.gateway_discovery_tx_frequency = kwargs.get("gateway_discovery_tx_frequency", 0)
        network_server.gateway_discovery_dr = kwargs.get("gateway_discovery_dr", 0)

        response = await client.Create(req, metadata=self._auth_token)
        return response.id

    async def has_global_api_token(self) -> bool:
        try:
            [_ async for _ in self.get_organizations()]
        except grpc.aio.AioRpcError as e:
            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                return False
            else:
                raise
        else:
            return True

    async def get_organizations(self, batch_size: int = 20) -> AsyncIterator[api.OrganizationListItem]:
        client = api.OrganizationServiceStub(self._channel)
        
        req = api.ListOrganizationRequest()
        req.limit = batch_size
        req.offset = 0

        async for org in self._get_paginated_data(req, client.List, batch_size):
            yield org

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_organization(self, org_id: int) -> api.Organization:
        client = api.OrganizationServiceStub(self._channel)

        req = api.GetOrganizationRequest()
        req.id = org_id
        
        return (await client.Get(req, metadata=self._auth_token)).organization

    async def get_applications(self, organization_id: int, batch_size=20) -> AsyncIterator[api.ApplicationListItem]:
        client = api.ApplicationServiceStub(self._channel)

        req = api.ListApplicationRequest()
        req.organization_id = organization_id
        req.limit = batch_size
        req.offset = 0

        async for application in self._get_paginated_data(req, client.List, batch_size):
            yield application

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_application(self, app_id: int) -> api.Application:
        client = api.ApplicationServiceStub(self._channel)

        req = api.GetApplicationRequest()
        req.id = app_id

        return (await client.Get(req, metadata=self._auth_token)).application

    async def create_service_profile(self, **kwargs) -> str:
        client = api.ServiceProfileServiceStub(self._channel)

        req = api.CreateServiceProfileRequest()
        req.service_profile.name = kwargs["name"]
        req.service_profile.organization_id = kwargs["organization_id"]
        req.service_profile.network_server_id = kwargs["network_server_id"]
        req.service_profile.ul_rate = kwargs.get("ul_rate", 0)
        req.service_profile.ul_bucket_size = kwargs.get("ul_bucket_size", 0)
        req.service_profile.ul_rate_policy = kwargs.get("ul_rate_policy", api.RatePolicy.DROP)
        req.service_profile.dl_rate = kwargs.get("dl_rate", 0)
        req.service_profile.dl_bucket_size = kwargs.get("dl_bucket_size", 0)
        req.service_profile.dl_rate_policy = kwargs.get("dl_rate_policy", api.RatePolicy.DROP)
        req.service_profile.add_gw_metadata = kwargs.get("add_gw_metadata", True)
        req.service_profile.dev_status_req_freq = kwargs.get("dev_status_req_freq", 0)
        req.service_profile.report_dev_status_battery = kwargs.get("report_dev_status_battery", False)
        req.service_profile.report_dev_status_margin = kwargs.get("report_dev_status_margin", False)
        req.service_profile.dr_min = kwargs.get("dr_min", 0)
        req.service_profile.dr_max = kwargs.get("dr_max", 0)

        channel_mask = kwargs.get("channel_mask", None)
        if channel_mask is not None:
            req.service_profile.channel_mask = kwargs.get("channel_mask", None)

        req.service_profile.pr_allowed = kwargs.get("pr_allowed", False)
        req.service_profile.hr_allowed = kwargs.get("hr_allowed", False)
        req.service_profile.ra_allowed = kwargs.get("ra_allowed", False)
        req.service_profile.nwk_geo_loc = kwargs.get("nwk_geo_loc", False)
        req.service_profile.target_per = kwargs.get("target_per", 0)
        req.service_profile.min_gw_diversity = kwargs.get("min_gw_diversity", 0)
        req.service_profile.gws_private = kwargs.get("gws_private", False)

        response = await client.Create(req, metadata=self._auth_token)
        return response.id

    async def create_gateway(
        self,
        gateway_id: str,
        name: str,
        description: str,
        network_server_id: int,
        tags: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        tags = tags if tags is not None else {}
        metadata = metadata if metadata is not None else {}
        client = api.GatewayServiceStub(self._channel)

        location = common_pb2.Location()
        location.latitude = kwargs.get("location", {}).get("latitude", 0.0)
        location.longitude = kwargs.get("location", {}).get("longitude", 0.0)
        location.altitude = kwargs.get("location", {}).get("altitude", 0.0)
        location.accuracy = kwargs.get("location", {}).get("accuracy", 0)
        location.source = getattr(
            common_pb2.LocationSource, kwargs.get("location", {}).get("source", "UNKNOWN").upper()
        )

        req = api.CreateGatewayRequest()
        req.gateway.id = gateway_id
        req.gateway.name = name
        req.gateway.description = description
        req.gateway.network_server_id = network_server_id
        req.gateway.location.MergeFrom(location)
        req.gateway.organization_id = kwargs.get("organization_id", 0)
        req.gateway.discovery_enabled = kwargs.get("discovery_enabled", False)
        req.gateway.gateway_profile_id = kwargs.get("gateway_profile_id", "")
        req.gateway.service_profile_id = kwargs.get("service_profile_id", "")

        if tags:
            for key, value in tags.items():
                req.tags[key] = value

        if metadata:
            for key, value in metadata.items():
                req.metadata[key] = value

        return await client.Create(req, metadata=self._auth_token)

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def delete_gateway(self, gateway_id):
        client = api.GatewayServiceStub(self._channel)

        req = api.DeleteServiceProfileRequest()
        req.id = gateway_id

        return await client.Delete(req, metadata=self._auth_token)

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_gateway(self, gateway_id) -> api.GetGatewayResponse:
        client = api.GatewayServiceStub(self._channel)

        req = api.GetGatewayRequest()
        req.id = gateway_id

        return await client.Get(req, metadata=self._auth_token)

    async def get_multicast_groups(
        self,
        application_id: int,
        organization_id: Optional[int] = None,
        batch_size: int = 20,
    ) -> AsyncIterator[api.MulticastGroupListItem]:
        client = api.MulticastGroupServiceStub(self._channel)
        req = api.ListMulticastGroupRequest()

        req.limit = batch_size
        req.application_id = application_id
        req.offset = 0

        if organization_id is not None:
            req.organization_id = organization_id

        async for multicast_group in self._get_paginated_data(req, client.List, batch_size):
            yield multicast_group

    # NOTE: group_id is str formatted UUID
    async def get_multicast_group(self, group_id: str) -> api.GetMulticastGroupResponse:
        client = api.MulticastGroupServiceStub(self._channel)
        req = api.GetMulticastGroupRequest()

        req.id = group_id

        return await client.Get(req, metadata=self._auth_token)

    async def enqueue_multicast_downlink(self, group_id: str, f_port: int, data: bytes, f_cnt: Optional[int] = None):
        from base64 import b64encode

        client = api.MulticastGroupServiceStub(self._channel)

        multicast = api.MulticastQueueItem()
        multicast.multicast_group_id = group_id
        if f_cnt is not None:
            multicast.f_cnt = f_cnt
        multicast.f_port = f_port
        multicast.data = b64encode(data)

        req = api.EnqueueMulticastQueueItemRequest(multicast_queue_item=multicast)

        return await client.Enqueue(req, metadata=self._auth_token)

    async def flush_multicast_queue(self, group_id: str):
        client = api.MulticastGroupServiceStub(self._channel)
        req = api.FlushMulticastGroupQueueItemsRequest(multicast_group_id=group_id)

        return await client.FlushQueue(req, metadata=self._auth_token)
