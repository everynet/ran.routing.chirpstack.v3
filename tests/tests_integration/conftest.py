import asyncio
import secrets
import uuid
from typing import Any

import grpc
import pytest

from lib.chirpstack import ChirpStackInternalAPI
from lib.chirpstack.devices import ApplicationDeviceList
from lib.chirpstack.multicast_groups import ApplicationMulticastGroupList
from lib.mqtt import MQTTClient
from lib.traffic.chirpstack import ChirpstackTrafficRouter

from .ext_chirpstack_api import ChirpStackExtendedApi
from .lorawan import make_uplink  # required fixture  # noqa


REGION_CONFIGS: dict[str, Any] = {
    "eu868": {
        "region_name": "eu868",
        "region_topic": "eu868",
        "region_common_name": "EU868",
        "uplink": dict(spreading=12, bandwidth=125000, frequency=867100000),
        "multicast": dict(dr=0, frequency=869525000),
    },
    "as923": {
        "region_name": "as923",
        "region_topic": "as923",
        "region_common_name": "AS923",
        "uplink": dict(spreading=12, bandwidth=125000, frequency=923200000),
        "multicast": dict(dr=2, frequency=923200000),
    },
    "as923_2": {
        "region_name": "as923_2",
        "region_topic": "as923_2",
        "region_common_name": "AS923_2",
        "uplink": dict(spreading=12, bandwidth=125000, frequency=921400000),
        "multicast": dict(dr=0, frequency=921400000),
    },
    "us915": {
        "region_name": "us915",
        "region_topic": "us915",
        "region_common_name": "US915",
        "uplink": dict(spreading=10, bandwidth=125000, frequency=906300000),
        "multicast": dict(dr=8, frequency=923300000),
    },
    "us915_a": {
        "region_name": "us915_a",
        "region_topic": "us915_a",
        "region_common_name": "US915",
        "uplink": dict(spreading=10, bandwidth=125000, frequency=902300000),
        "multicast": dict(dr=8, frequency=923300000),
    },
    "au915_a": {
        "region_name": "au915_a",
        "region_topic": "au915_a",
        "region_common_name": "AU915",
        "uplink": dict(spreading=12, bandwidth=125000, frequency=915200000),
        "multicast": dict(dr=2, frequency=923200000),
    },
}


def pytest_generate_tests(metafunc):
    if "current_region" in metafunc.fixturenames:
        current_region = metafunc.config.getoption("region")
        if not current_region:
            raise Exception("Please, provide current chirpstack region with '--region' flag")
        current_region = current_region.lower()
        if current_region not in REGION_CONFIGS.keys():
            raise Exception(
                f"Has no test config for region {current_region!r}. Available: {', '.join(REGION_CONFIGS.keys())}"
            )
        metafunc.parametrize("current_region", [current_region])


# You can remove "pytest_generate_tests" and replace it with fixture, if you don't wan't configuring region by flag:
# @pytest.fixture(
#     params=[
#         "eu868",
#         # "as923",
#         # "as923_2",
#         # "us915",
#         # "us915_a",
#         # "au915_a",
#     ]
# )
# def current_region(request) -> str:
#     return request.param


@pytest.fixture
def region_params(current_region) -> dict[str, Any]:
    return REGION_CONFIGS[current_region]


@pytest.fixture
async def chirpstack_internal_api() -> ChirpStackInternalAPI:
    try:
        api = ChirpStackInternalAPI("http://localhost:8080", "admin", "admin")
        await api.authenticate()
    except Exception as e:
        return pytest.exit(f"Could not connect to internal api: {e}")
    return api


@pytest.fixture
async def mqtt_client() -> MQTTClient:
    # TODO: shutdown tests if could not connect to mqtt
    mqtt_client = MQTTClient("mqtt://localhost:1883", client_id=uuid.uuid4().hex)
    stop = asyncio.Event()
    stop.clear()

    client_task = asyncio.create_task(mqtt_client.run(stop))
    await mqtt_client.wait_for_connection()

    yield mqtt_client

    stop.set()
    await client_task


@pytest.fixture
async def chirpstack_api(chirpstack_internal_api) -> ChirpStackExtendedApi:
    try:
        grpc_channel = grpc.aio.insecure_channel("localhost:8080")
        chirpstack_api = ChirpStackExtendedApi(grpc_channel, chirpstack_internal_api._jwt_token)
    except Exception as e:
        return pytest.exit(f"Could not connect to grpc api: {e}")
    return chirpstack_api


@pytest.fixture
async def network_server(chirpstack_api: ChirpStackExtendedApi):
    params = dict(name="test-chirpstack-api", server="chirpstack-network-server:8000")

    ns_id = None
    async for network_server in chirpstack_api.get_network_servers(None):
        if network_server.server.lower() == params["server"].lower():
            ns_id = network_server.id
            break
    else:
        ns_id = await chirpstack_api.create_network_server(**params)

    params["id"] = ns_id
    yield params

    # TODO: Terdown NS?


@pytest.fixture()
async def organization_id(chirpstack_internal_api: ChirpStackInternalAPI):
    user_profile = await chirpstack_internal_api.profile()
    organization_id = int(user_profile["organizations"][0]["organizationID"])
    yield organization_id


@pytest.fixture
async def service_profile(chirpstack_api: ChirpStackExtendedApi, organization_id, network_server):
    params = dict(
        organization_id=organization_id,
        name="test-chirpstack-service-profile",
        network_server_id=network_server["id"],
    )
    service_profile_id = await chirpstack_api.create_service_profile(**params)
    params["id"] = service_profile_id

    yield params

    await chirpstack_api.delete_service_profile(service_profile_id)


@pytest.fixture
async def gateway(
    chirpstack_api: ChirpStackExtendedApi,
    organization_id,
    network_server,
    service_profile,
):

    gateway_id = secrets.token_hex(8)
    params = dict(
        gateway_id=gateway_id,
        name="pytest-gw-" + gateway_id,
        description="pytest gateway",
        network_server_id=network_server["id"],
        organization_id=organization_id,
        service_profile_id=service_profile["id"],
    )
    await chirpstack_api.create_gateway(**params)

    yield params

    await chirpstack_api.delete_gateway(gateway_id)


@pytest.fixture
async def application(request, chirpstack_api: ChirpStackExtendedApi, service_profile, organization_id):
    """
    Use this fixture like this:
    @pytest.mark.parametrize("application", [{"name": "test-application"}], indirect=True)
    """
    if hasattr(request, "param"):
        params = request.param.copy()
    else:
        params = {}

    if "name" not in params:
        params["name"] = "bridge-test-app-" + service_profile["id"]

    params.update({"organization_id": organization_id, "service_profile_id": service_profile["id"]})

    application_id = await chirpstack_api.create_application(**params)
    params.update({"id": application_id})

    yield params

    await chirpstack_api.delete_application(application_id)


@pytest.fixture()
async def device_profile(request, chirpstack_api: ChirpStackExtendedApi, organization_id, network_server):
    """
    Use this fixture like this:
    @pytest.mark.parametrize(
        "device_profile",
        [{"name": "test-otaa-class-c-profile", "supports_join": True, "supports_class_c": True}],
        indirect=True,
    )
    """
    if hasattr(request, "param"):
        params = request.param.copy()
    else:
        params = {}

    if "name" not in params:
        params["name"] = "bridge-test-device-profile"

    params.update(
        {"organization_id": organization_id, "network_server_id": network_server["id"], "tags": {"ran": "yes"}}
    )
    device_profile_id = await chirpstack_api.create_device_profile(**params)
    params.update({"id": device_profile_id})

    yield params

    await chirpstack_api.delete_device_profile(device_profile_id)


@pytest.fixture
async def device_otaa(chirpstack_api: ChirpStackExtendedApi, application, device_profile):
    device_params = {
        "app_eui": "0" * 16,
        "dev_eui": secrets.token_hex(8),
        "name": "test-uplink-dev-abp",
        "application_id": application["id"],
        "device_profile_id": device_profile["id"],
        "skip_fcnt_check": True,
        "tags": {},
    }
    dev_eui = await chirpstack_api.create_device(**device_params)

    device_keys = {
        "dev_eui": dev_eui,
        "nwk_key": secrets.token_hex(16),
        "app_key": "0" * 32,
    }
    await chirpstack_api.create_device_keys(**device_keys)

    yield dict(list(device_params.items()) + list(device_keys.items()))

    await chirpstack_api.delete_device(dev_eui)


@pytest.fixture
async def device_abp(chirpstack_api: ChirpStackExtendedApi, application, device_profile):
    device_params = {
        "dev_eui": secrets.token_hex(8),
        "name": "test-abp-uplink-dev-abp",
        "application_id": application["id"],
        "device_profile_id": device_profile["id"],
        "skip_fcnt_check": True,
        "tags": {},
    }
    dev_eui = await chirpstack_api.create_device(**device_params)

    device_keys = {
        "dev_eui": dev_eui,
        "dev_addr": secrets.token_hex(4),
        "app_s_key": secrets.token_hex(16),
        "nwk_s_enc_key": secrets.token_hex(16),
    }
    await chirpstack_api.activate_device(**device_keys)

    yield dict(list(device_params.items()) + list(device_keys.items()))

    await chirpstack_api.delete_device(dev_eui)


@pytest.fixture
async def multicast_group(chirpstack_api: ChirpStackExtendedApi, application, region_params):
    params = dict(
        name="test-multicast-group",
        application_id=application["id"],
        region=region_params["region_common_name"],
        mc_addr=secrets.token_hex(4),
        mc_nwk_s_key=secrets.token_hex(16),
        mc_app_s_key=secrets.token_hex(16),
        group_type="CLASS_C",
        dr=region_params["multicast"]["dr"],
        frequency=region_params["multicast"]["frequency"],
    )
    mc_id = await chirpstack_api.create_multicast_group(**params)
    params.update({"id": mc_id})

    yield params

    await chirpstack_api.delete_multicast_group(mc_id)


@pytest.fixture
async def chirpstack_router(chirpstack_api: ChirpStackExtendedApi, mqtt_client: MQTTClient, gateway, application):
    app_id = application["id"]
    gw_id = gateway["gateway_id"]

    devices = ApplicationDeviceList(chirpstack_api, application_id=app_id, tags={"ran": "yes"})
    await devices.sync_from_remote()

    multicast_groups = ApplicationMulticastGroupList(chirpstack_api, application_id=app_id)
    await multicast_groups.sync_from_remote()

    chirpstack_router = ChirpstackTrafficRouter(
        gateway_mac=gw_id,
        chirpstack_mqtt_client=mqtt_client,
        chirpstack_uplink_topic_template="gateway/{}/event/up",
        chirpstack_downlink_topic_template="gateway/{}/command/down",
        chirpstack_downlink_ack_topic_template="gateway/{}/event/ack",
        devices=devices,
        multicast_groups=multicast_groups,
    )

    async def force_sync():
        await devices.sync_from_remote()
        await multicast_groups.sync_from_remote()

    # This is extra method, which can be used to sync devices/multicast groups from chirpstack without periodic update
    chirpstack_router.force_sync = force_sync

    yield chirpstack_router
