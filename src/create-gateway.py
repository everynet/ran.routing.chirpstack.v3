#!/usr/bin/env python

import argparse
import asyncio

import grpc
from yarl import URL

from lib import chirpstack


def get_grpc_channel(host: str, port: str, secure: bool = True, cert_path: str = None):
    target_addr = f"{host}:{port}"
    channel = None

    if secure:
        if cert_path is not None:
            with open(cert_path, "rb") as f:
                credentials = grpc.ssl_channel_credentials(f.read())
        else:
            credentials = grpc.ssl_channel_credentials()

        channel = grpc.aio.secure_channel(target_addr, credentials)
    else:
        channel = grpc.aio.insecure_channel(target_addr)

    return channel


async def get_or_create_ns(chirpstack_api, name, server):
    async for network_server in chirpstack_api.get_network_servers(None):
        if network_server.server.lower() == server.lower():
            return network_server.id
    return await chirpstack_api.create_network_server(name=name, server=server)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", type=str, help="Username", default="admin", required=False)
    parser.add_argument("--password", type=str, help="Password", default="admin", required=False)
    parser.add_argument("--gateway-id", help="Gateway id (mac)", default="000000000000C0DE", required=False)
    parser.add_argument("--name", type=str, help="Gateway unique name", default="chirpstck-ran-bridge", required=False)
    parser.add_argument("--description", type=str, help="Description", default="Chirpstack RAN gateway", required=False)
    parser.add_argument("--ns-name", type=str, help="Network server name", default="chirpstack")
    parser.add_argument("--ns-addr", type=str, help="Network server address", default="chirpstack-network-server:8000")
    parser.add_argument("--organization-id", type=int, help="Org id", default=0)
    parser.add_argument(
        "--chirpstack-url",
        type=str,
        help="Chirpstack app server URL",
        default="http://chirpstack-application-server:8080",
        required=False,
    )
    args = parser.parse_args()

    chirpstack_internal_api = chirpstack.ChirpStackInternalAPI(args.chirpstack_url, args.username, args.password)
    await chirpstack_internal_api.authenticate()

    url = URL(args.chirpstack_url)
    grpc_channel = get_grpc_channel(url.host, url.port, url.scheme == "https")
    chirpstack_api = chirpstack.ChirpStackAPI(
        grpc_channel, chirpstack_internal_api._jwt_token
    )

    existed_gateway = await chirpstack_api.get_gateway(args.gateway_id)
    if existed_gateway:
        print(existed_gateway.gateway.id)
        return

    organization_id = args.organization_id
    if not organization_id:
        user_profile = await chirpstack_internal_api.profile()
        organization_id = int(user_profile["organizations"][0]["organizationID"])

    network_server_id = await get_or_create_ns(chirpstack_api, args.ns_name, args.ns_addr)
    service_profile_id = await chirpstack_api.create_service_profile(
        name="chirpstack-ran-service-profile", organization_id=organization_id, network_server_id=network_server_id
    )
    await chirpstack_api.create_gateway(
        args.gateway_id,
        "gw-" + args.gateway_id,
        "Autogen",
        network_server_id,
        organization_id=organization_id,
        service_profile_id=service_profile_id,
    )

    print(args.gateway_id)

if __name__ == "__main__":
    asyncio.run(main())
