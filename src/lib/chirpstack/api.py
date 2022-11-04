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

    # @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    # async def get_device(self, dev_eui: str) -> device_pb2.Device:
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.GetDeviceRequest()
    #     req.dev_eui = dev_eui

    #     res = await client.Get(req, metadata=self._auth_token)
    #     return res.device

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device_keys(self, dev_eui: str):
        client = api.DeviceServiceStub(self._channel)

        req = api.DeviceKeys()
        req.dev_eui = dev_eui

        res = await client.GetKeys(req, metadata=self._auth_token)
        return res.device_keys

    # async def create_device_keys(self, dev_eui: str, nwk_key: str, app_key: Optional[str] = None) -> None:
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.CreateDeviceKeysRequest()
    #     req.device_keys.dev_eui = dev_eui
    #     req.device_keys.nwk_key = nwk_key
    #     req.device_keys.app_key = app_key
    #     # req.device_keys.gen_app_key = ...

    #     await client.CreateKeys(req, metadata=self._auth_token)

    # async def activate_device(self, dev_eui: str, **kwargs) -> None:
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.ActivateDeviceRequest()
    #     device_activation = req.device_activation

    #     device_activation.dev_eui = dev_eui
    #     device_activation.dev_addr = kwargs.get("dev_addr", secrets.token_hex(4))
    #     device_activation.app_s_key = kwargs.get("app_s_key", secrets.token_hex(16))
    #     device_activation.nwk_s_enc_key = kwargs.get("nwk_s_enc_key", secrets.token_hex(16))

    #     device_activation.s_nwk_s_int_key = kwargs.get("s_nwk_s_int_key", device_activation.nwk_s_enc_key)
    #     device_activation.f_nwk_s_int_key = kwargs.get("f_nwk_s_int_key", device_activation.nwk_s_enc_key)

    #     device_activation.f_cnt_up = kwargs.get("f_cnt_up", 0)
    #     device_activation.n_f_cnt_down = kwargs.get("n_f_cnt_down", 0)
    #     device_activation.a_f_cnt_down = kwargs.get("a_f_cnt_down", 0)

    #     await client.Activate(req, metadata=self._auth_token)

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device_activation(self, dev_eui: str) -> device_pb2.DeviceActivation:
        client = api.DeviceServiceStub(self._channel)

        req = api.DeviceActivation()
        req.dev_eui = dev_eui

        res = await client.GetActivation(req, metadata=self._auth_token)
        return res.device_activation

    # @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    # async def deactivate_device(self, dev_eui: str) -> None:
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.DeactivateDeviceRequest()
    #     req.dev_eui = dev_eui

    #     await client.Deactivate(req, metadata=self._auth_token)

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

    # async def delete_device_profile(self, device_profile_id: str) -> None:
    #     client = api.DeviceProfileServiceStub(self._channel)

    #     req = api.DeleteDeviceProfileRequest()
    #     req.id = device_profile_id

    #     await client.Delete(req, metadata=self._auth_token)

    # async def create_device_profile(self, **kwargs):
    #     client = api.DeviceProfileServiceStub(self._channel)

    #     req = api.CreateDeviceProfileRequest()
    #     device_profile = req.device_profile
    #     device_profile.name = kwargs["name"]
    #     device_profile.organization_id = kwargs["organization_id"]
    #     device_profile.network_server_id = kwargs["network_server_id"]
    #     device_profile.supports_class_b = kwargs.get("supports_class_b", False)
    #     device_profile.class_b_timeout = kwargs.get("class_b_timeout", 0)
    #     device_profile.ping_slot_period = kwargs.get("ping_slot_period", 0)
    #     device_profile.ping_slot_dr = kwargs.get("ping_slot_dr", 0)
    #     device_profile.ping_slot_freq = kwargs.get("ping_slot_freq", 0)
    #     device_profile.supports_class_c = kwargs.get("supports_class_c", False)
    #     device_profile.class_c_timeout = kwargs.get("class_c_timeout", 0)
    #     device_profile.mac_version = kwargs.get("mac_version", "1.0.3")
    #     device_profile.reg_params_revision = kwargs.get("reg_params_revision", "a")
    #     device_profile.rx_delay_1 = kwargs.get("rx_delay_1", 1)
    #     device_profile.rx_dr_offset_1 = kwargs.get("rx_dr_offset_1", 0)
    #     device_profile.rx_datarate_2 = kwargs.get("rx_datarate_2", 0)
    #     device_profile.rx_freq_2 = kwargs.get("rx_freq_2", 869525000)  # 869.525 - eu band
    #     device_profile.factory_preset_freqs[:] = kwargs.get("factory_preset_freqs", [])
    #     device_profile.max_eirp = kwargs.get("max_eirp", 0)
    #     device_profile.max_duty_cycle = kwargs.get("max_duty_cycle", 0)
    #     device_profile.supports_join = kwargs.get("supports_join", False)
    #     device_profile.rf_region = kwargs.get("rf_region", "eu868")
    #     device_profile.supports_32bit_f_cnt = kwargs.get("supports_32bit_f_cnt", False)
    #     device_profile.payload_codec = kwargs.get("payload_codec", "")
    #     device_profile.payload_encoder_script = kwargs.get("payload_encoder_script", "")
    #     device_profile.payload_decoder_script = kwargs.get("payload_decoder_script", "")
    #     device_profile.geoloc_buffer_ttl = kwargs.get("geoloc_buffer_ttl", 0)
    #     device_profile.geoloc_min_buffer_size = kwargs.get("geoloc_min_buffer_size", 0)

    #     device_profile.tags.clear()
    #     device_profile.tags.update(kwargs.get("tags", {}))

    #     device_profile.uplink_interval.FromMilliseconds(kwargs.get("uplink_interval", 1000 * 60 * 5))
    #     device_profile.adr_algorithm_id = kwargs.get("adr_algorithm_id", "default")

    #     res = await client.Create(req, metadata=self._auth_token)
    #     return res.id

    # async def create_device(self, **kwargs) -> str:
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.CreateDeviceRequest()
    #     req.device.dev_eui = kwargs["dev_eui"]
    #     req.device.name = kwargs["name"]
    #     req.device.application_id = kwargs["application_id"]
    #     req.device.description = kwargs.get("description", "")
    #     req.device.device_profile_id = kwargs["device_profile_id"]
    #     req.device.is_disabled = kwargs.get("is_disabled", False)
    #     req.device.skip_f_cnt_check = kwargs.get("skip_f_cnt_check", True)
    #     req.device.tags.update(kwargs.get("tags", {}))

    #     await client.Create(req, metadata=self._auth_token)
    #     return kwargs["dev_eui"]

    # async def delete_device(self, dev_eui):
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.DeleteDeviceRequest()
    #     req.dev_eui = dev_eui

    #     return await client.Delete(req, metadata=self._auth_token)

    # async def get_network_servers(self, organization_id: Optional[int] = None, batch_size: int = 20):
    #     client = api.NetworkServerServiceStub(self._channel)

    #     req = api.ListNetworkServerRequest()
    #     if organization_id is not None:
    #         req.organization_id = organization_id
    #     req.limit = batch_size
    #     req.offset = 0

    #     async for network_server in self._get_paginated_data(req, client.List, batch_size):
    #         yield network_server

    # async def create_network_server(self, **kwargs) -> int:
    #     client = api.NetworkServerServiceStub(self._channel)

    #     req = api.CreateNetworkServerRequest()
    #     network_server = req.network_server
    #     network_server.name = kwargs["name"]
    #     network_server.server = kwargs.get("server", "chirpstack-network-server:8000")  # docker-compose default
    #     network_server.ca_cert = kwargs.get("ca_cert", "")
    #     network_server.tls_cert = kwargs.get("tls_cert", "")
    #     network_server.tls_key = kwargs.get("tls_key", "")
    #     network_server.routing_profile_ca_cert = kwargs.get("routing_profile_ca_cert", "")
    #     network_server.routing_profile_tls_cert = kwargs.get("routing_profile_tls_cert", "")
    #     network_server.routing_profile_tls_key = kwargs.get("routing_profile_tls_key", "")
    #     network_server.gateway_discovery_enabled = kwargs.get("gateway_discovery_enabled", False)
    #     network_server.gateway_discovery_interval = kwargs.get("gateway_discovery_interval", 0)
    #     network_server.gateway_discovery_tx_frequency = kwargs.get("gateway_discovery_tx_frequency", 0)
    #     network_server.gateway_discovery_dr = kwargs.get("gateway_discovery_dr", 0)

    #     response = await client.Create(req, metadata=self._auth_token)
    #     return response.id

    # @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    # async def get_network_server(self, network_server_id: int):
    #     client = api.NetworkServerServiceStub(self._channel)

    #     req = api.GetNetworkServerRequest()
    #     req.id = network_server_id

    #     res = await client.Get(req, metadata=self._auth_token)
    #     return res.network_server

    # async def delete_network_server(self, network_server_id: int):
    #     client = api.NetworkServerServiceStub(self._channel)

    #     req = api.DeleteDeviceRequest()
    #     req.id = network_server_id

    #     return await client.Delete(req, metadata=self._auth_token)

    async def create_application(self, **kwargs) -> int:
        client = api.ApplicationServiceStub(self._channel)

        req = api.CreateApplicationRequest()
        application = req.application
        application.name = kwargs["name"]
        application.description = kwargs.get("", "-")
        application.organization_id = kwargs["organization_id"]
        application.service_profile_id = kwargs["service_profile_id"]
        application.payload_codec = kwargs.get("payload_codec", "")
        application.payload_encoder_script = kwargs.get("payload_encoder_script", "")
        application.payload_decoder_script = kwargs.get("payload_decoder_script", "")

        response = await client.Create(req, metadata=self._auth_token)
        return response.id

    async def get_applications(self, organization_id: int, batch_size=20):
        client = api.ApplicationServiceStub(self._channel)

        req = api.ListApplicationRequest()
        req.organization_id = organization_id
        req.limit = batch_size
        req.offset = 0

        async for application in self._get_paginated_data(req, client.List, batch_size):
            yield application

    # @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    # async def get_application(self, application_id: int):
    #     client = api.ApplicationServiceStub(self._channel)

    #     req = api.GetApplicationRequest()
    #     req.id = application_id

    #     res = await client.Get(req, metadata=self._auth_token)
    #     return res.application

    # async def delete_application(self, application_id: int):
    #     client = api.ApplicationServiceStub(self._channel)

    #     req = api.DeleteApplicationRequest()
    #     req.id = application_id

    #     return await client.Delete(req, metadata=self._auth_token)

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

    # async def get_service_profiles(self, organization_id: int, network_server_id: int, batch_size=20):
    #     client = api.ServiceProfileServiceStub(self._channel)

    #     req = api.ListApplicationRequest()
    #     req.organization_id = organization_id
    #     req.network_server_id = network_server_id
    #     req.limit = batch_size
    #     req.offset = 0

    #     async for service_profile in self._get_paginated_data(req, client.List, batch_size):
    #         yield service_profile

    # @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    # async def get_service_profile(self, service_profile_id: str):
    #     client = api.ServiceProfileServiceStub(self._channel)

    #     req = api.GetServiceProfileRequest()
    #     req.id = service_profile_id

    #     res = await client.Get(req, metadata=self._auth_token)
    #     return res.service_profile

    # async def delete_service_profile(self, service_profile_id: str):
    #     client = api.ServiceProfileServiceStub(self._channel)

    #     req = api.DeleteServiceProfileRequest()
    #     req.id = service_profile_id

    #     return await client.Delete(req, metadata=self._auth_token)

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
    async def get_gateway(self, gateway_id):
        client = api.GatewayServiceStub(self._channel)

        req = api.GetGatewayRequest()
        req.id = gateway_id

        return await client.Get(req, metadata=self._auth_token)

    # async def delete_gateway(self, gateway_id):
    #     client = api.GatewayServiceStub(self._channel)

    #     req = api.DeleteServiceProfileRequest()
    #     req.id = gateway_id

    #     return await client.Delete(req, metadata=self._auth_token)

    # async def stream_frame_logs(self, dev_eui, timeout=None):
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.StreamDeviceFrameLogsRequest()
    #     req.dev_eui = dev_eui

    #     frame_logs = client.StreamFrameLogs(req, metadata=self._auth_token)
    #     frame_logs_iter = frame_logs.__aiter__()

    #     while True:
    #         message = await asyncio.wait_for(frame_logs_iter.__anext__(), timeout=timeout)
    #         yield message

    # async def stream_event_logs(self, dev_eui, timeout=None):
    #     client = api.DeviceServiceStub(self._channel)

    #     req = api.StreamDeviceEventLogsRequest()
    #     req.dev_eui = dev_eui

    #     event_logs = client.StreamEventLogs(req, metadata=self._auth_token)
    #     event_logs_iter = event_logs.__aiter__()

    #     while True:
    #         message = await asyncio.wait_for(event_logs_iter.__anext__(), timeout=timeout)
    #         yield message

    async def get_multicast_groups(
        self,
        application_id: int,
        organization_id: Optional[int] = None,
        batch_size: int = 20,
    ):
        client = api.MulticastGroupServiceStub(self._channel)
        req = api.ListMulticastGroupRequest()

        req.limit = batch_size
        req.application_id = application_id
        req.offset = 0

        if organization_id is not None:
            req.organization_id = organization_id

        async for multicast_group in self._get_paginated_data(req, client.List, batch_size):
            yield multicast_group

    async def get_multicast_group(self, group_id: str):  # group_id is str formatted UUID
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
