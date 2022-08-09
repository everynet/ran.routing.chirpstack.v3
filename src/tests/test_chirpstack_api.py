import structlog
import secrets
import pytest
import pylorawan


logger = structlog.getLogger(__name__)


async def get_or_create_ns(chirpstack_api, name, server):
    async for network_server in chirpstack_api.get_network_servers(None):
        if network_server.server.lower() == server.lower():
            return network_server.id
    return await chirpstack_api.create_network_server(name=name, server=server)


async def test_application(chirpstack_api):
    apps = chirpstack_api.get_applications(0)


async def test_gateway(chirpstack_internal_api, chirpstack_api):
    user_profile = await chirpstack_internal_api.profile()
    organization_id = int(user_profile["organizations"][0]["organizationID"])

    network_server_id = await get_or_create_ns(chirpstack_api, "test-chirpstack-api", "chirpstack-network-server:8000")
    service_profile_id = await chirpstack_api.create_service_profile(
        name="test-chirpstack-service-profile", organization_id=organization_id, network_server_id=network_server_id
    )

    gateway_id = secrets.token_hex(8)
    await chirpstack_api.create_gateway(
        gateway_id,
        "test-gw-" + gateway_id,
        "pytest gateway",
        network_server_id,
        organization_id=organization_id,
        service_profile_id=service_profile_id,
    )

    gateway = await chirpstack_api.get_gateway(gateway_id)
    assert gateway.gateway.id == gateway_id

    await chirpstack_api.delete_gateway(gateway_id)
    await chirpstack_api.delete_service_profile(service_profile_id)
