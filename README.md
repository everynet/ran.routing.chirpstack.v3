# Everynet RAN to ChirpStack Bridge

This is an early stage beta product. Please refer to the known limitations section.

## Introduction

Everynet operates a Neutral-Host Cloud RAN, which is agnostic to the LoRaWAN Network Server. Everynet's main product is carrier-grade coverage that can be connected to any LNS available on the market and ChirpStack in particular.

Everynet coverage is available via Everynet RAN Routing API that let customers control message routing table (subscribe to devices). It also allows to send and receive LoRaWAN messages.

This integration is designed to simplify the connection between the Everynet RAN Routing API and ChirpStack installation. 

## Functionality 

With this software, it is possible to connect your [ChirpStack Application]([https://www.chirpstack.io/application-server/use/applications/]) to Everynet RAN coverage.

Before we start it is important to mention that Everynet RAN main functionality is LoRaWAN traffic routing, while ChirpStack is doing the rest of the job: device and key management, payload parsing and so on...

**Everynet RAN does not store any device-related cryptographic keys and is not capable of decrypting customer traffic.** 


## How it works

This software works similar to the standard ChirpStack gateway bridge component which receives packets from real gateways.

Everynet RAN to ChirpStack bridge receives messages from RAN Routing API and translates it to a virtual gateway messages that are then served to the ChirpStack installation.

![Diagram](./res/diagram.png)

The integration periodically fetches devices from the ChirpStack API and add these device to the Everynet RAN routing table (subscribe to these devices).


## Configuration

Before using this software you should configure the following parameters. Make sure that you correctly set all the required parameters.

Parameters are read from environment variables and/or **settings.cfg** and **.env** files.

| Parameter                         | Required | Default value    | Description                                                                                                                                                                                    |
|-----------------------------------|----------|------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| CHIRPSTACK_API_TOKEN              | Yes      |                  | You can generate an API Token using the corresponding menu item in the Сhirpstack Application Server Web UI                                                                                       |
| CHIRPSTACK_API_GRPC_HOST          | Yes      |                  | ChirpStack host name (IP address can also be used). This address is used by the ran-chirpstack-bridge to make gRPC calls to the ChirpStack Application. e.g. my-chirpstack-server.com                |
| CHIRPSTACK_API_GRPC_PORT          |          | 433              | ChirpStack gRPC API port                                                                                                                                                                       |
| CHIRPSTACK_API_GRPC_SECURE        |          | True             | ChirpStack gRPC API connection secure on not                                                                                                                                                   |
| CHIRPSTACK_API_GRPC_CERT_PATH     |          |                  | If you are using custom certificates for a secure connection, you must specify certificate path here                                                                                                      |
| CHIRPSTACK_MQTT_SERVER_URI        | Yes      |                  | ChirpStack MQTT server URI e.g. mqtt://my-chirpstack-server.com.  URI support username, password and secure connecton  e.g. mqtts://admin:pass@my-chirpstack-server.com                        |
| CHIRPSTACK_APPLICATION_ID         | Yes      |                  | ChirpStack application ID that could be found in the UI.                                                                                                                                                                |
| CHIRPSTACK_MATCH_TAGS             |          | everynet=true   | Mark devices (or device profiles) with the "everynet" tag to connect them to Everynet coverage. Here you need to set these tags. e.g. everynet=true tag.  |
| CHIRPSTACK_GATEWAY_ID             | Yes      | 000000000000C0DE | MAC address of the virtual gateway from which messages will be arriving to the ChripStack                                                                                                                      |
| CHIRPSTACK_DEVICES_REFRESH_PERIOD |          | 300              | Period in seconds to fetch device list from the ChirpStack and sync it with Everynet RAN |
| RAN_TOKEN                         | Yes      |                  | RAN Routing API token                                                                                                                                                                                  |
| RAN_COVERAGE_DOMAIN               | Yes      |                  | RAN coverage. e.g. EU                                                                                                                                                                          |
| HEALTHCHECK_SERVER_HOST           |          | 0.0.0.0          | Internal healthcheck http server bind host                                                                                                                                                     |
| HEALTHCHECK_SERVER_PORT           |          | 9090             | Internal healtcheck http server port http://[host]:[port]/health/live http://[host]:[port]/health/ready                                                                                        |
| LOG_LEVEL                         |          | info             | Logging level. Allowed values: info, warning, error, debug                                                                                                                                     |


## Deploy

For now it is only possible to deploy this bridge using docker and docker-compose.

If you don't have any installation of ChirpStack first you need to deploy it. For reference you can use our docker-compose file.


### Deploy Chirpstack and chirpstack-ran-bridge

1. Build chirpstack-ran-bridge docker image
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml build
```

2. Start chirpstack.
```
docker-compose -f docker-compose.chirpstack.yml up -d
```

2. Create Chirpstack API token and set `CHIRPSTACK_API_TOKEN` variable in .env file with output value.
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml \
    run chirpstack-ran-bridge python create-token.py
```

3. Create gateway and set `CHIRPSTACK_GATEWAY_ID` variable in .env file with output value
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml \
    run chirpstack-ran-bridge python create-gateway.py --gateway-id 000000000000c0de
```

4. Edit .env file and set your RAN token in `RAN_TOKEN` variable and coverage domain in `RAN_COVERAGE_DOMAIN` variable.

5. Start chirpstack-ran-bridge.
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml up -d
```

Chirpstack with ran-chirpstack-bridge will be available at `http://<YOUR DOMAIN>:8080`


### Deploy Chirpstack and chirpstack-ran-bridge with HTTPS

1. Edit .env file and set `TRAEFIK_ACME_EMAIL` variable with your contact email.
```
docker-compose -f docker-compose.traefik.yml up -d
```

2. Edit .env file and set `CHIRPSTACK_DOMAIN` variable, it must be equal to domain you plan to use.
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-https.yml up -d
```

Now your chirpstack installation with https up and running at address: https://<CHIRPSTACK_DOMAIN>

3. Build chirpstack-ran-bridge docker image
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-https.yml -f docker-compose.chirpstack-ran-bridge.yml build
```

4. Create Chirpstack API token and set `CHIRPSTACK_API_TOKEN` variable in .env file with output value.
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-https.yml -f docker-compose.chirpstack-ran-bridge.yml \
    run chirpstack-ran-bridge python create-token.py
```

5. Create gateway and set `CHIRPSTACK_GATEWAY_ID` variable in .env file with output value
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-https.yml -f docker-compose.chirpstack-ran-bridge.yml \
    run chirpstack-ran-bridge python create-gateway.py --gateway-id 000000000000c0de
```

6. Edit .env file and set your RAN token in `RAN_TOKEN` variable and coverage domain in `RAN_COVERAGE_DOMAIN` variable.

7. Start chirpstack-ran-bridge.
```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-https.yml -f docker-compose.chirpstack-ran-bridge.yml up -d
```

Please check that you have blocked port 8080 by firewall.

## Known limitations

These are the known limitations that are going to be fixed in the next versions of this software:
- neither FSK, nor LR-FHSS modulations are supported
