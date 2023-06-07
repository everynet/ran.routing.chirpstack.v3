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


async def get_or_create_ns(chirpstack_api: chirpstack.ChirpStackAPI, name, server):
    async for network_server in chirpstack_api.get_network_servers(None):
        if network_server.server.lower() == server.lower():
            return network_server.id
    return await chirpstack_api.create_network_server(name=name, server=server)


async def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--username", type=str, help="Username", default="admin", required=False)
    parser.add_argument("--password", type=str, help="Password", default="admin", required=False)
    parser.add_argument(
        "--as-addr",
        type=str,
        help="Chirpstack application server URL",
        default="http://chirpstack-application-server:8080/",
        required=False,
    )
    args = parser.parse_args()

    chirpstack_internal_api = chirpstack.ChirpStackInternalAPI(args.as_addr, args.username, args.password)
    await chirpstack_internal_api.authenticate()

    url = URL(args.as_addr)
    grpc_channel = get_grpc_channel(url.host, url.port, url.scheme == "https")
    chirpstack_api = chirpstack.ChirpStackAPI(grpc_channel, chirpstack_internal_api._jwt_token)
    orgs = [org async for org in chirpstack_api.get_organizations()]

    labels = ["org id", "name", "display name", "can have gateways"]
    max_id_len = max([len(str(t.id)) for t in orgs] + [len(labels[0])])
    max_name_len = max([len(t.name) for t in orgs] + [len(labels[1])])
    max_displ_len = max([len(t.display_name) for t in orgs] + [len(labels[2])])

    tpl = f"|{{:^{max_id_len + 4}}}|{{:^{max_name_len + 4}}}|{{:^{max_displ_len + 4}}}|{{:^21}}"
    header = tpl.format(*labels)

    print("\n", flush=True)
    print(header, flush=True)
    print("-" * len(header), flush=True)
    for org in orgs:
        print(tpl.format(org.id, org.name, org.display_name, "yes" if org.can_have_gateways else "no"), flush=True)
    print("\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
