#!/usr/bin/env python

import argparse
import asyncio

from lib import chirpstack


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, help="name", default="Chirpstack-RAN bridge", required=False)
    parser.add_argument("--username", type=str, help="Username", default="admin", required=False)
    parser.add_argument("--password", type=str, help="Password", default="admin", required=False)
    parser.add_argument("--not-admin", help="Admin flag", action="store_false")
    parser.add_argument("--application-id", type=int, help="Applicaiton id", default=0)
    parser.add_argument("--organization-id", type=int, help="Org id", default=0)
    parser.add_argument(
        "--chirpstack-url",
        type=str,
        help="Chirpstack app server URL",
        default="http://chirpstack-application-server:8080",
        required=False,
    )
    args = parser.parse_args()

    chirpstack_api = chirpstack.ChirpStackInternalAPI(args.chirpstack_url, args.username, args.password)
    await chirpstack_api.authenticate()
    new_api_key = await chirpstack_api.create_api_key(
        name=args.name, is_admin=args.not_admin, organization_id=args.organization_id, application_id=args.application_id
    )
    print(new_api_key["jwtToken"])


if __name__ == "__main__":
    asyncio.run(main())
