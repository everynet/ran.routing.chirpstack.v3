#!/usr/bin/env python

import argparse
import asyncio

from lib import chirpstack


async def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--name", type=str, help="Access token name", default="Chirpstack-RAN bridge", required=False)
    parser.add_argument("--username", type=str, help="Username", default="admin", required=False)
    parser.add_argument("--password", type=str, help="Password", default="admin", required=False)
    parser.add_argument("--admin", help="Admin flag", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--application-id", type=int, help="Application id", default=0)
    parser.add_argument("--organization-id", type=int, help="Organization id", default=0)
    parser.add_argument(
        "--as-addr",
        type=str,
        help="Chirpstack application server URL",
        default="http://chirpstack-application-server:8080/",
        required=False,
    )
    args = parser.parse_args()

    chirpstack_api = chirpstack.ChirpStackInternalAPI(args.as_addr, args.username, args.password)
    await chirpstack_api.authenticate()

    new_api_key = await chirpstack_api.create_api_key(
        name=args.name,
        is_admin=args.admin,
        organization_id=args.organization_id,
        application_id=args.application_id,
    )
    print(f'CHIRPSTACK_API_TOKEN="{new_api_key["jwtToken"]}"')


if __name__ == "__main__":
    asyncio.run(main())
