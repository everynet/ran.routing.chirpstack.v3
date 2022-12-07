# Everynet RAN to ChirpStack Bridge

It is an early stage beta product. Please refer to the known limitations section.

## Introduction

Everynet operates a Neutral-Host Cloud RAN, which is agnostic to the LoRaWAN Network Server. Everynet's main product is carrier-grade coverage that can be connected to any LNS available on the market and ChirpStack in particular.

Everynet coverage is available via Everynet RAN Routing API that let customers control message routing table (subscribe to devices). It also allows to send and receive LoRaWAN messages.

This integration is designed to simplify the connection between the Everynet RAN Routing API and ChirpStack installation. 

## Functionality 

With this software, you can connect your [ChirpStack Application]([https://www.chirpstack.io/application-server/use/applications/]) to Everynet RAN coverage.

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
| CHIRPSTACK_API_TOKEN              | Yes      |                  | You can generate an API Token using the corresponding menu item in the Сhirpstack Application Server Web UI                                                                                    |
| CHIRPSTACK_API_GRPC_HOST          | Yes      |                  | ChirpStack host name (IP address can also be used). This address is used by the ran-chirpstack-bridge to make gRPC calls to the ChirpStack Application. e.g. my-chirpstack-server.com          |
| CHIRPSTACK_API_GRPC_PORT          |          | 433              | ChirpStack gRPC API port                                                                                                                                                                       |
| CHIRPSTACK_API_GRPC_SECURE        |          | True             | ChirpStack gRPC API connection secure on not                                                                                                                                                   |
| CHIRPSTACK_API_GRPC_CERT_PATH     |          |                  | If you are using custom certificates for a secure connection, you must specify certificate path here                                                                                           |
| CHIRPSTACK_MQTT_SERVER_URI        | Yes      |                  | ChirpStack MQTT server URI e.g. mqtt://my-chirpstack-server.com. URI support username, password and secure connecton  e.g. mqtts://admin:pass@my-chirpstack-server.com                         |
| CHIRPSTACK_APPLICATION_ID         | Yes      |                  | ChirpStack application ID that could be found in the UI.                                                                                                                                       |
| CHIRPSTACK_MATCH_TAGS             |          | everynet=true    | Mark devices (or device profiles) with the "everynet" tag to connect them to Everynet coverage. Here you need to set these tags. e.g. ran-device=yes tag.                                      |
| CHIRPSTACK_GATEWAY_ID             | Yes      | 000000000000C0DE | MAC address of the virtual gateway from which messages will be arriving to the ChripStack                                                                                                      |
| CHIRPSTACK_DEVICES_REFRESH_PERIOD |          | 300              | Period in seconds to fetch device list from the ChirpStack and sync it with Everynet RAN                                                                                                       |
| RAN_TOKEN                         | Yes      |                  | RAN Routing API token                                                                                                                                                                          |
| RAN_API_URL                       | Yes      |                  | RAN Routing API endpoint URL                                                                                                                                                                   |
| HEALTHCHECK_SERVER_HOST           |          | 0.0.0.0          | Internal healthcheck http server bind host                                                                                                                                                     |
| HEALTHCHECK_SERVER_PORT           |          | 9090             | Internal healtcheck http server port http://[host]:[port]/health/live http://[host]:[port]/health/ready                                                                                        |
| LOG_LEVEL                         |          | info             | Logging level. Allowed values: info, warning, error, debug                                                                                                                                     |


## Deploying ChirpStack and Ran-Bridge with docker-compose

For now it is only possible to deploy this bridge using docker and docker-compose.
If you don't have any installation of ChirpStack first you need to deploy it. For reference you can use docker-compose files from this repository.

### 1. Build chirpstack-ran-bridge docker image

```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml build
```

### 2. Start chirpstack

```
docker-compose -f docker-compose.chirpstack.yml up -d
```

### 3. Create Chirpstack API token

You can create api-token manually under "API keys" section of chirpstack UI, or use utility script:

```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml \
    run chirpstack-ran-bridge python create-token.py
```

Set `CHIRPSTACK_API_TOKEN` variable with obtained token in .env file.

### 4. Create gateway

You need to create new gateway in ChirpStack. You can do it under "Gateways" section of UI, or use utility script:

```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml \
    run chirpstack-ran-bridge python create-gateway.py --gateway-id 000000000000c0de
```

Set `CHIRPSTACK_GATEWAY_ID` variable with identifier of created gateway in .env file.


### 5. Configure Access to RAN

Edit .env file and set your RAN token in `RAN_TOKEN` variable and api URL in `RAN_API_URL` variable.
You can obtain this values from RAN cloud UI.

### 6. Start chirpstack-ran-bridge

On this step, your `.env` file must contain several required values, example:

```env
CHIRPSTACK_API_TOKEN="<...>"
CHIRPSTACK_GATEWAY_ID="000000000000c0de"
RAN_TOKEN="<...>"
RAN_API_URL="https://dev.cloud.everynet.io/api/v1"
```

Now, you can run configured RAN-bridge.

```
docker-compose -f docker-compose.chirpstack.yml -f docker-compose.chirpstack-ran-bridge.yml up -d
```

ChirpStack with ran-chirpstack-bridge will be available at `http://<YOUR DOMAIN>:8080`


## Known limitations

These are the known limitations that are going to be fixed in the next versions of this software:

- neither FSK, nor LR-FHSS modulations are supported

