import asyncio
from typing import Any, AsyncIterator, Dict, Optional

import grpc
from chirpstack_api.as_pb.external import api
from chirpstack_api.as_pb.external.api import device_pb2, deviceProfile_pb2, profiles_pb2
from chirpstack_api.common import common_pb2

from lib.chirpstack.api import ChirpStackAPI, suppress_rpc_error


class ChirpStackExtendedApi(ChirpStackAPI):
    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_device(self, dev_eui: str) -> device_pb2.Device:
        client = api.DeviceServiceStub(self._channel)

        req = api.GetDeviceRequest()
        req.dev_eui = dev_eui

        res = await client.Get(req, metadata=self._auth_token)
        return res.device

    async def create_device_keys(self, dev_eui: str, nwk_key: str, app_key: Optional[str] = None) -> None:
        client = api.DeviceServiceStub(self._channel)

        req = api.CreateDeviceKeysRequest()
        req.device_keys.dev_eui = dev_eui
        req.device_keys.nwk_key = nwk_key
        req.device_keys.app_key = app_key
        # req.device_keys.gen_app_key = ...

        await client.CreateKeys(req, metadata=self._auth_token)

    async def activate_device(self, dev_eui: str, **kwargs) -> None:
        import secrets

        client = api.DeviceServiceStub(self._channel)

        req = api.ActivateDeviceRequest()
        device_activation = req.device_activation

        device_activation.dev_eui = dev_eui
        device_activation.dev_addr = kwargs.get("dev_addr", secrets.token_hex(4))
        device_activation.app_s_key = kwargs.get("app_s_key", secrets.token_hex(16))
        device_activation.nwk_s_enc_key = kwargs.get("nwk_s_enc_key", secrets.token_hex(16))

        device_activation.s_nwk_s_int_key = kwargs.get("s_nwk_s_int_key", device_activation.nwk_s_enc_key)
        device_activation.f_nwk_s_int_key = kwargs.get("f_nwk_s_int_key", device_activation.nwk_s_enc_key)

        device_activation.f_cnt_up = kwargs.get("f_cnt_up", 0)
        device_activation.n_f_cnt_down = kwargs.get("n_f_cnt_down", 0)
        device_activation.a_f_cnt_down = kwargs.get("a_f_cnt_down", 0)

        await client.Activate(req, metadata=self._auth_token)

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def deactivate_device(self, dev_eui: str) -> None:
        client = api.DeviceServiceStub(self._channel)

        req = api.DeactivateDeviceRequest()
        req.dev_eui = dev_eui

        await client.Deactivate(req, metadata=self._auth_token)

    async def delete_device_profile(self, device_profile_id: str) -> None:
        client = api.DeviceProfileServiceStub(self._channel)

        req = api.DeleteDeviceProfileRequest()
        req.id = device_profile_id

        await client.Delete(req, metadata=self._auth_token)

    async def create_device_profile(self, **kwargs):
        client = api.DeviceProfileServiceStub(self._channel)

        req = api.CreateDeviceProfileRequest()
        device_profile = req.device_profile
        device_profile.name = kwargs["name"]
        device_profile.organization_id = kwargs["organization_id"]
        device_profile.network_server_id = kwargs["network_server_id"]
        device_profile.supports_class_b = kwargs.get("supports_class_b", False)
        device_profile.class_b_timeout = kwargs.get("class_b_timeout", 0)
        device_profile.ping_slot_period = kwargs.get("ping_slot_period", 0)
        device_profile.ping_slot_dr = kwargs.get("ping_slot_dr", 0)
        device_profile.ping_slot_freq = kwargs.get("ping_slot_freq", 0)
        device_profile.supports_class_c = kwargs.get("supports_class_c", False)
        device_profile.class_c_timeout = kwargs.get("class_c_timeout", 0)
        device_profile.mac_version = kwargs.get("mac_version", "1.0.3")
        device_profile.reg_params_revision = kwargs.get("reg_params_revision", "a")
        device_profile.rx_delay_1 = kwargs.get("rx_delay_1", 1)
        device_profile.rx_dr_offset_1 = kwargs.get("rx_dr_offset_1", 0)
        device_profile.rx_datarate_2 = kwargs.get("rx_datarate_2", 0)
        device_profile.rx_freq_2 = kwargs.get("rx_freq_2", 869525000)  # 869.525 - eu band
        device_profile.factory_preset_freqs[:] = kwargs.get("factory_preset_freqs", [])
        device_profile.max_eirp = kwargs.get("max_eirp", 0)
        device_profile.max_duty_cycle = kwargs.get("max_duty_cycle", 0)
        device_profile.supports_join = kwargs.get("supports_join", False)
        device_profile.rf_region = kwargs.get("rf_region", "eu868")
        device_profile.supports_32bit_f_cnt = kwargs.get("supports_32bit_f_cnt", False)
        device_profile.payload_codec = kwargs.get("payload_codec", "")
        device_profile.payload_encoder_script = kwargs.get("payload_encoder_script", "")
        device_profile.payload_decoder_script = kwargs.get("payload_decoder_script", "")
        device_profile.geoloc_buffer_ttl = kwargs.get("geoloc_buffer_ttl", 0)
        device_profile.geoloc_min_buffer_size = kwargs.get("geoloc_min_buffer_size", 0)

        device_profile.tags.clear()
        device_profile.tags.update(kwargs.get("tags", {}))

        device_profile.uplink_interval.FromMilliseconds(kwargs.get("uplink_interval", 1000 * 60 * 5))
        device_profile.adr_algorithm_id = kwargs.get("adr_algorithm_id", "default")

        res = await client.Create(req, metadata=self._auth_token)
        return res.id

    async def create_device(self, **kwargs) -> str:
        client = api.DeviceServiceStub(self._channel)

        req = api.CreateDeviceRequest()
        req.device.dev_eui = kwargs["dev_eui"]
        req.device.name = kwargs["name"]
        req.device.application_id = kwargs["application_id"]
        req.device.description = kwargs.get("description", "")
        req.device.device_profile_id = kwargs["device_profile_id"]
        req.device.is_disabled = kwargs.get("is_disabled", False)
        req.device.skip_f_cnt_check = kwargs.get("skip_f_cnt_check", True)
        req.device.tags.update(kwargs.get("tags", {}))

        await client.Create(req, metadata=self._auth_token)
        return kwargs["dev_eui"]

    async def delete_device(self, dev_eui):
        client = api.DeviceServiceStub(self._channel)

        req = api.DeleteDeviceRequest()
        req.dev_eui = dev_eui

        return await client.Delete(req, metadata=self._auth_token)

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_network_server(self, network_server_id: int):
        client = api.NetworkServerServiceStub(self._channel)

        req = api.GetNetworkServerRequest()
        req.id = network_server_id

        res = await client.Get(req, metadata=self._auth_token)
        return res.network_server

    async def delete_network_server(self, network_server_id: int):
        client = api.NetworkServerServiceStub(self._channel)

        req = api.DeleteDeviceRequest()
        req.id = network_server_id

        return await client.Delete(req, metadata=self._auth_token)

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

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_application(self, application_id: int):
        client = api.ApplicationServiceStub(self._channel)

        req = api.GetApplicationRequest()
        req.id = application_id

        res = await client.Get(req, metadata=self._auth_token)
        return res.application

    async def delete_application(self, application_id: int):
        client = api.ApplicationServiceStub(self._channel)

        req = api.DeleteApplicationRequest()
        req.id = application_id

        return await client.Delete(req, metadata=self._auth_token)

    async def get_service_profiles(self, organization_id: int, network_server_id: int, batch_size=20):
        client = api.ServiceProfileServiceStub(self._channel)

        req = api.ListApplicationRequest()
        req.organization_id = organization_id
        req.network_server_id = network_server_id
        req.limit = batch_size
        req.offset = 0

        async for service_profile in self._get_paginated_data(req, client.List, batch_size):
            yield service_profile

    @suppress_rpc_error([grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED])
    async def get_service_profile(self, service_profile_id: str):
        client = api.ServiceProfileServiceStub(self._channel)

        req = api.GetServiceProfileRequest()
        req.id = service_profile_id

        res = await client.Get(req, metadata=self._auth_token)
        return res.service_profile

    async def delete_service_profile(self, service_profile_id: str):
        client = api.ServiceProfileServiceStub(self._channel)

        req = api.DeleteServiceProfileRequest()
        req.id = service_profile_id

        return await client.Delete(req, metadata=self._auth_token)

    async def stream_frame_logs(self, dev_eui, timeout=None):
        client = api.DeviceServiceStub(self._channel)

        req = api.StreamDeviceFrameLogsRequest()
        req.dev_eui = dev_eui

        frame_logs = client.StreamFrameLogs(req, metadata=self._auth_token)
        frame_logs_iter = frame_logs.__aiter__()

        while True:
            message = await asyncio.wait_for(frame_logs_iter.__anext__(), timeout=timeout)
            yield message

    async def stream_event_logs(self, dev_eui, timeout=None):
        client = api.DeviceServiceStub(self._channel)

        req = api.StreamDeviceEventLogsRequest()
        req.dev_eui = dev_eui

        event_logs = client.StreamEventLogs(req, metadata=self._auth_token)
        event_logs_iter = event_logs.__aiter__()

        while True:
            message = await asyncio.wait_for(event_logs_iter.__anext__(), timeout=timeout)
            yield message

    async def enqueue_downlink(self, dev_eui, data: bytes, confirmed: bool = False, f_port: int = 2):
        client = api.DeviceQueueServiceStub(self._channel)

        req = api.EnqueueDeviceQueueItemRequest()
        req.device_queue_item.confirmed = confirmed
        req.device_queue_item.data = data
        req.device_queue_item.dev_eui = dev_eui
        req.device_queue_item.f_port = f_port

        return await client.Enqueue(req, metadata=self._auth_token)

    async def create_multicast_group(self, **kwargs) -> str:
        client = api.MulticastGroupServiceStub(self._channel)

        mc = api.MulticastGroup()
        mc.name = kwargs["name"]
        mc.application_id = kwargs["application_id"]
        mc.mc_addr = kwargs["mc_addr"]
        mc.mc_nwk_s_key = kwargs["mc_nwk_s_key"]
        mc.mc_app_s_key = kwargs["mc_app_s_key"]
        mc.f_cnt = kwargs.get("f_cnt", 0)
        mc.group_type = api.MulticastGroupType.Value(kwargs["group_type"])
        mc.dr = kwargs.get("dr", 0)
        mc.frequency = kwargs["frequency"]

        req = api.CreateMulticastGroupRequest()
        req.multicast_group.MergeFrom(mc)
        response = await client.Create(req, metadata=self._auth_token)
        return response.id

    async def add_device_to_multicast_group(self, group_id: str, dev_eui: str):
        client = api.MulticastGroupServiceStub(self._channel)

        req = api.AddDeviceToMulticastGroupRequest()
        req.multicast_group_id = group_id
        req.dev_eui = dev_eui

        return await client.AddDevice(req, metadata=self._auth_token)

    async def delete_multicast_group(self, group_id: str):
        client = api.MulticastGroupServiceStub(self._channel)
        req = api.DeleteMulticastGroupRequest()
        req.id = group_id

        return await client.Delete(req, metadata=self._auth_token)
