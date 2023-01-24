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

---

## Deploying ChirpStack and Ran-Bridge with docker-compose

For now it is only possible to deploy this bridge using docker and docker-compose.
If you don't have any installation of ChirpStack first you need to deploy it. For reference you can use docker-compose files from this repository.

Docker-compose files from this repo will configure EU868 region network settings for ChirpStack network server.
If you want to change this behavior, check [Bands and regional parameters](#bands-and-regional-parameters) section.

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

---

## Deploying Ran-Bridge with docker

Ran-Bridge has [pre-builded docker images](https://hub.docker.com/r/everynethub/ran.routing.chirpstack.v3).
You must have an existing ChirpStack v3 installation or you can install ChirpStack using docker-compose from section above.

To run Ran-Bridge you must specify the required parameters listed above.
Example command below shows the main required parameters and their typical values:

```
docker run -d --name=ran-bridge --restart=always \
    -e CHIRPSTACK_API_TOKEN="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJhdWQiOi<...>CI6ImtleSJ9.HF2DwQL9jgUyXG0e5TfgvHpUteguSapeSsIvppIfRDE" \
    -e CHIRPSTACK_API_GRPC_HOST=mydomain.com \
    -e CHIRPSTACK_API_GRPC_PORT=8080 \
    -e CHIRPSTACK_MQTT_SERVER_URI=mqtt://mydomain.com \
    -e CHIRPSTACK_GATEWAY_ID=000000000000C0DE \
    -e RAN_TOKEN="<...>" \
    -e RAN_API_URL="https://dev.cloud.everynet.io/api/v1" \
    everynethub/ran.routing.chirpstack.v3
```

---

## Bands and regional parameters

Everynet Ran-Routing coverage uses some custom parameters for gateway's bands.

If you want to use Everynet coverage with "Ran-Routing to ChirpStack bridge", ensure your ChirpStack is configured properly.

This repository provides proper ChirpStack configurations for bands, used by Everynet:

- AS923
- AS923_2
- AU915_A
- EU868
- US915
- US915_A
- US915_AB

Those configurations are stored in [docker-data/chirpstack-network-server/examples](./docker-data/chirpstack-network-server/examples) folder, and can be used as reference for ChirpStack configuration.

If you are deploying ChirpStack with [docker-compose files](#deploying-chirpstack-and-ran-bridge-with-docker-compose) from repository, you can choose desired region configuration by replacing contents of
`docker-data/chirpstack-network-server/chirpstack-network-server.toml` with contents from one of [examples](./docker-data/chirpstack-network-server/examples) before deployment.

If you are using your own ChirpStack deployment and deploying bridge with [pre-built docker images](#deploying-ran-bridge-with-docker), check your ChirpStack regions configuration.

More information about ChirpStack configuration may be found on official website:

- https://www.chirpstack.io/network-server/install/config/

Sections below describes changes, applied to each band, used in Everynet's coverage.

### EU868

#### Default channels

| Frequency in Hz | Datarates | Description |
| --------------- | --------- | ----------- |
| 868100000       | 0..5      |             |
| 868300000       | 0..5      |             |
| 868500000       | 0..5      |             |

#### Extra channels

| Frequency in Hz | Datarates | Description |
| --------------- | --------- | ----------- |
| 867100000       | 0..5      |             |
| 867300000       | 0..6      |             |
| 867500000       | 0..5      |             |
| 867700000       | 0..5      |             |
| 867900000       | 0..5      |             |

### US915

The same as the LoRaWAN regional parameters.

### US915A

The first 8 channels, the same as the LoRaWAN regional parameters.

| Index of channel | Frequency in Hz | Datarates | Description |
| ---------------- | --------------- | --------- | ----------- |
| 0                | 902300000       | 0..3      |             |
| 1                | 902500000       | 0..3      |             |
| 2                | 902700000       | 0..3      |             |
| 3                | 902900000       | 0..3      |             |
| 4                | 903100000       | 0..3      |             |
| 5                | 903300000       | 0..3      |             |
| 6                | 903500000       | 0..3      |             |
| 7                | 903700000       | 0..3      |             |

### US915AB

| Index of channel | Frequency in Hz | Datarates | Description |
| ---------------- | --------------- | --------- | ----------- |
| 2                | 902700000       | 0..3      |             |
| 3                | 902900000       | 0..3      |             |
| 4                | 903100000       | 0..3      |             |
| 5                | 903300000       | 0..3      |             |
| 10               | 904300000       | 0..3      |             |
| 11               | 904500000       | 0..3      |             |
| 12               | 904700000       | 0..3      |             |
| 13               | 904900000       | 0..3      |             |
| 64               | 903000000       | 4         |             |

### AS923

#### Default channels

| Frequency in Hz | Datarates | Description |
| --------------- | --------- | ----------- |
| 923200000       | 0..5      |             |
| 923400000       | 0..5      |             |

#### Extra Channels

| Frequency in Hz | Datarates | Description    |
| --------------- | --------- | -------------- |
| 923000000       | 0..5      |                |
| 923600000       | 0..6      |                |
| 923800000       | 0..5      |                |
| 924000000       | 6         |                |
| 924200000       | 0..5      |                |
| 924400000       | 7         | Modulation FSK |

### AS923-2

| Frequency in Hz | Datarates | Description |
| --------------- | --------- | ----------- |
| 921400000       | 0..5      |             |
| 921600000       | 0..5      |             |

#### Extra Channels

| Frequency in Hz | Datarates | Description    |
| --------------- | --------- | -------------- |
| 921200000       | 0..5      |                |
| 921800000       | 0..6      |                |
| 922000000       | 0..5      |                |
| 922200000       | 0..6      |                |
| 922400000       | 0..5      |                |
| 922600000       | 7         | Modulation FSK |

### AU915A

The first 8 channels, the same as the LoRaWAN regional parameters.

| Index of channel | Frequency in Hz | Datarates | Description |
| ---------------- | --------------- | --------- | ----------- |
| 0                | 915200000       | 0..5      |             |
| 1                | 915400000       | 0..5      |             |
| 2                | 915600000       | 0..5      |             |
| 3                | 915800000       | 0..5      |             |
| 4                | 916000000       | 0..5      |             |
| 5                | 916200000       | 0..5      |             |
| 6                | 916400000       | 0..5      |             |
| 7                | 916600000       | 0..5      |             |

---

## Known limitations

These are the known limitations that are going to be fixed in the next versions of this software:

- neither FSK, nor LR-FHSS modulations are supported

