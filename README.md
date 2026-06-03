# BTicino GO Integration for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/r0bb10/BTicino-GO-Integration)](https://github.com/r0bb10/BTicino-GO-Integration/releases/latest)
[![Release](https://github.com/r0bb10/BTicino-GO-Integration/actions/workflows/release.yml/badge.svg)](https://github.com/r0bb10/BTicino-GO-Integration/actions/workflows/release.yml)
[![GitHub Issues](https://img.shields.io/github/issues/r0bb10/BTicino-GO-Integration)](https://github.com/r0bb10/BTicino-GO-Integration/issues)
[![License](https://img.shields.io/github/license/r0bb10/BTicino-GO-Integration)](LICENSE)
[![Top Language](https://img.shields.io/github/languages/top/r0bb10/BTicino-GO-Integration)](https://www.python.org/)
[![State Sync](https://img.shields.io/badge/state-SSE%20first%20%2B%20polling-blue)](custom_components/bticino_companion/coordinator.py)
[![IoT Class](https://img.shields.io/badge/IoT-local_push-blue)](custom_components/bticino_companion/manifest.json)

Home Assistant custom integration for [BTicino GO Companion](https://github.com/r0bb10/BTicino-GO-Companion).

This integration talks to the local Go companion service running on the intercom, consumes its `/api/v2` API, subscribes to its server-sent event streams, and turns that into native Home Assistant entities and services.

## Table of Contents

- [Architecture](#architecture)
- [Requirements](#requirements)
- [Feature Overview](#feature-overview)
- [Entities](#entities)
- [Services](#services)
- [State Sync](#state-sync)
- [Camera and WebRTC](#camera-and-webrtc)
- [Pairing and Authentication](#pairing-and-authentication)
- [Installation](#installation)
- [Setup](#setup)
- [Options and Reconfiguration](#options-and-reconfiguration)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Architecture

The project is split into two repositories that work together:

- [BTicino GO Companion](https://github.com/r0bb10/BTicino-GO-Companion) is the on-device Go service.
- BTicino GO Integration is the Home Assistant custom integration.

The companion owns the intercom-local protocols and media handling:

- OpenWebNet control and event parsing.
- SIP call and stream lifecycle.
- RTSP/RTP media handling.
- Native WebRTC offer/candidate/session handling.
- Bidirectional audio bridge.
- Snapshots, voicemail, diagnostics, auth, updates, and service controls.

The integration owns the Home Assistant side:

- Zeroconf discovery.
- Config flow and pairing.
- Local push state updates through SSE.
- Native entities and services.
- Camera stream source and WebRTC session forwarding.
- OpenWebNet trace relay into Home Assistant events.
- Reauth, reconfigure, options, and repair flows.

## Requirements

- Home Assistant with custom integrations enabled.
- HACS, or manual custom component installation.
- A rooted BTicino Classe 300X or Classe 100X running BTicino GO Companion.
- The companion reachable from Home Assistant, usually at `http://<intercom-ip>:8080`.
- A companion claim code or a valid companion access token.
- SIP/media features require the intercom to have valid Flexisip users and routing, usually provisioned by pairing the official BTicino Door Entry mobile app once.

The companion must be installed first. See [BTicino GO Companion](https://github.com/r0bb10/BTicino-GO-Companion) for device-side installation and configuration.

## Feature Overview

- Local-only Home Assistant integration for the companion API.
- `local_push` behavior through companion SSE events.
- Polling fallback and periodic snapshot refresh.
- Zeroconf discovery through `_bticomp._tcp.local.`.
- Claim-code pairing for unclaimed companions.
- Bearer-token authentication for protected companion APIs.
- Automatic persistence of returned auth state.
- Reauthentication and reconfiguration flows.
- Repair flow for claim recovery when credentials become invalid.
- Native sensors for call state, active entrypoint, IP, MAC, and Wi-Fi strength.
- Native ringing binary sensor.
- Native switches for mute and voicemail when supported.
- Native buttons for entrypoint unlock, system reboot, and exposed service restart.
- Native camera entities for stream-capable entrypoints.
- RTSP stream source generation from companion entrypoint metadata.
- Native Home Assistant WebRTC camera flow backed by companion WebRTC endpoints.
- Camera still image from the companion latest snapshot endpoint.
- Update entity for companion firmware/update service when exposed by the companion.
- OpenWebNet trace event entity and Home Assistant bus events.
- Integration services for common companion commands.

## Entities

### Sensors

| Entity | Purpose |
| --- | --- |
| `Call State` | Canonical companion call state: `idle`, `ringing`, or `active`. |
| `Active Entrypoint` | Current active companion entrypoint or `none`. |
| `IP Address` | Network IP reported by companion diagnostics. |
| `Mac Address` | Network MAC reported by companion diagnostics. |
| `WiFi Strength` | Wi-Fi strength percentage from companion diagnostics. |

### Binary Sensors

| Entity | Purpose |
| --- | --- |
| `Ringing` | On when companion call state is `ringing`. |

### Switches

| Entity | Purpose |
| --- | --- |
| `Mute` | Mute/unmute through companion audio controls. |
| `Voicemail` | Enable/disable voicemail when the model and companion capabilities support it. |

### Buttons

| Entity | Purpose |
| --- | --- |
| `Unlock <entrypoint>` | Unlock a companion entrypoint that has unlock enabled. |
| `System Reboot` | Reboot the companion host when enabled. |
| `Restart <service>` | Restart a companion-exposed system service, such as `dropbear`. |

### Cameras

The integration creates one camera entity per stream-capable companion entrypoint.

Each camera supports:

- Home Assistant stream source through RTSP.
- Native Home Assistant WebRTC offer/candidate handling.
- Preview/still image from the companion latest snapshot endpoint.
- `entrypoint_id` and `devaddr` attributes.
- `is_streaming` state based on companion canonical state.

### Event Entity

| Entity | Purpose |
| --- | --- |
| `OpenWebNet Trace` | Diagnostic event entity carrying the latest OpenWebNet trace frame. |

Event types are `rx`, `tx`, `info`, `error`, and `unknown`.

To inspect raw trace events in Home Assistant:

1. Open `Developer Tools > Events`.
2. In `Listen to events`, enter `bticino_companion_openwebnet_frame`.
3. Click `Start listening`.
4. Trigger an intercom action such as ring, unlock, mute, unmute, or stream start.
5. Inspect the event payload fields such as `direction`, `transport`, `frame`, `mapped`, and `decoded_event_type`.

### Update Entity

| Entity | Purpose |
| --- | --- |
| `Companion` | Firmware/update entity backed by the companion update service. |

The update entity is available only when companion update control is enabled and exposed. It reports current version, latest version, update stage, rollback availability, last errors, timestamps, and artifact metadata.

When Home Assistant checks or installs an update, the integration delegates the work to the companion:

- Update detection calls the companion update check endpoint.
- The companion can detect available updates from a local manifest or GitHub latest release metadata.
- Installing from Home Assistant calls the companion update apply endpoint.
- The companion downloads or uses the selected artifact, verifies SHA256 when available, replaces its own binary, restarts itself, and verifies health after restart.
- If the companion reports rollback support, rollback state is exposed as update entity attributes.

## Services

The integration registers these Home Assistant services under the `bticino_companion` domain.

| Service | Purpose |
| --- | --- |
| `bticino_companion.refresh` | Force a full snapshot refresh from Companion. |
| `bticino_companion.call_answer` | Answer an incoming call. |
| `bticino_companion.call_hangup` | Hang up or reject a call. |
| `bticino_companion.audio_mute` | Mute companion audio. |
| `bticino_companion.audio_unmute` | Unmute companion audio. |
| `bticino_companion.voicemail_enable` | Enable voicemail. |
| `bticino_companion.voicemail_disable` | Disable voicemail. |
| `bticino_companion.entrypoint_unlock` | Unlock a specific entrypoint. |
| `bticino_companion.system_reboot` | Reboot the companion host when enabled. |

Most services accept an optional `entry_id` when multiple companion hubs are configured. `entrypoint_unlock` also requires `entrypoint_id`.

Example service call:

```yaml
service: bticino_companion.entrypoint_unlock
data:
  entrypoint_id: main
```

## State Sync

The integration is SSE-first.

On startup and periodic refresh, it reads:

- `/api/v2/health`
- `/api/v2/auth/status`
- `/api/v2/state`
- `/api/v2/entrypoints`
- `/api/v2/capabilities`

After setup, it opens `/api/v2/events` and keeps Home Assistant state updated from server-sent events.

The coordinator tracks:

- SSE connected/disconnected state.
- Last event id.
- Last activity age.
- Stale stream detection.
- Reconnect attempts.
- Availability grace after disconnects.

The companion publishes heartbeat events, and the integration uses them to keep connection health current without exposing heartbeats as user-facing state changes.

This is why the integration declares `iot_class: local_push`: events are pushed locally from the companion to Home Assistant over a persistent local SSE connection.

## Camera and WebRTC

RTSP camera source:

1. The integration reads entrypoint metadata from `/api/v2/entrypoints`.
2. It extracts `rtsp_path` and `rtsp_port` for each stream-capable entrypoint.
3. It builds an RTSP URL using the configured companion host.
4. Home Assistant can use that as the camera stream source.

WebRTC camera flow:

1. Home Assistant creates a WebRTC offer for the camera entity.
2. The integration sends it to `/api/v2/webrtc/offer` with the camera entrypoint id and session id.
3. The companion returns an answer SDP and any gathered ICE candidates.
4. Home Assistant receives the answer and candidates through its camera WebRTC API.
5. Additional ICE candidates are forwarded to `/api/v2/webrtc/candidate`.
6. When the session closes, the integration calls `/api/v2/webrtc/close`.

Camera preview images are read from `/api/v2/entrypoints/{id}/snapshot/latest.jpg`.

Camera, WebRTC, RTSP, and two-way audio depend on the companion's SIP/media stack. If the official Door Entry app has never been paired, `/etc/flexisip/users/users.db.txt` may be empty on the intercom and media features can fail until Flexisip users and routes are provisioned.

## Pairing and Authentication

The companion starts with a generated claim code when it is not yet claimed.

During setup, the integration can use either:

- A claim code from the companion config.
- An existing access token.

Claim-code pairing works as follows:

1. The integration checks companion health.
2. It reads auth status.
3. If `needs_claim` is true, it requests a pairing challenge.
4. It submits challenge id, nonce, and claim code.
5. The companion returns an access token and key id.
6. The integration stores the token and uses it for protected API calls.

If the companion is already claimed, setup requires a valid access token unless claim recovery is performed.

## Installation

### HACS

This repository is HACS-compatible.

Prerequisite: [HACS](https://hacs.xyz/) must already be installed in Home Assistant.

Add this repository to HACS:

[![Add Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=r0bb10&repository=BTicino-GO-Integration&category=integration)

Manual custom repository details:

- Repository: `https://github.com/r0bb10/BTicino-GO-Integration`
- Category: `Integration`

After adding the repository in HACS, install `BTicino Companion` and restart Home Assistant.

After restart, start the Home Assistant config flow:

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=bticino_companion)

### Manual

Copy the integration directory into Home Assistant:

```text
custom_components/bticino_companion
```

Restart Home Assistant after copying or updating the files.

## Setup

Before adding the integration:

1. Install and start BTicino GO Companion on the intercom.
2. Confirm the companion health endpoint responds at `http://<intercom-ip>:8080/api/v2/health`.
3. Get the companion claim code from `/home/bticino/cfg/extra/companion/config.json`, or prepare an existing access token.

Add the integration in Home Assistant:

1. Go to `Settings > Devices & services`.
2. Add `BTicino Companion`.
3. If discovered through zeroconf, confirm the discovered device.
4. Enter the claim code or access token.
5. Submit the flow.

If zeroconf discovery does not appear, use manual setup with the companion URL, for example:

```text
http://192.168.1.50:8080
```

## Options and Reconfiguration

The integration supports options and reconfiguration for:

- Companion URL.
- Access token.
- SSL verification.
- Request timeout.

Use reconfiguration if the intercom IP changes, the companion URL changes, or credentials need to be replaced.

If stored credentials are rejected, Home Assistant raises a repair issue. The repair flow can issue a temporary repair code through the companion, reset claim state, request a new claim, store the new token, and reload the entry.

## Troubleshooting

### Companion Is Not Discovered

- Confirm the companion is running.
- Confirm Home Assistant can reach `http://<intercom-ip>:8080/api/v2/health`.
- Confirm UDP mDNS traffic is allowed between Home Assistant and the intercom.
- Use manual setup with the companion URL if zeroconf is unavailable.

### Invalid Auth

- If the companion is unclaimed, use the claim code from the companion config.
- If the companion is already claimed, use the current access token.
- If the token is stale, use the Home Assistant repair flow or reset claim state on the companion.

### Entities Are Unavailable

- Confirm `/api/v2/events` can stay connected.
- Confirm companion health and state endpoints respond.
- Check companion logs for OpenWebNet, SIP, or media readiness errors.

### Camera Does Not Stream

- Confirm the companion exposes RTSP metadata in `/api/v2/entrypoints`.
- Confirm TCP port `8554` is reachable from Home Assistant.
- Confirm the companion can start the stream through SIP/OpenWebNet.
- For WebRTC, confirm UDP port `8555` is reachable where needed.
- Confirm the official BTicino Door Entry app has been paired at least once so the intercom has Flexisip users and routes for media.
- On the intercom, an empty `/etc/flexisip/users/users.db.txt` usually means SIP/media provisioning is missing.

### OpenWebNet Trace Is Empty

- Confirm the companion OpenWebNet trace stream is available.
- Confirm the intercom is producing OpenWebNet multicast frames.
- Trigger a ring, unlock, mute, or stream action and watch for `bticino_companion_openwebnet_frame` events.

## Development

The integration is a standard Home Assistant custom component under:

```text
custom_components/bticino_companion
```

Important files:

- `manifest.json` declares the integration domain, local push class, zeroconf type, and metadata.
- `config_flow.py` handles user setup, zeroconf, reauth, and reconfigure.
- `api.py` is the async client for companion `/api/v2`.
- `coordinator.py` owns polling, SSE, runtime connection state, and state transitions.
- `camera.py` owns RTSP stream source and WebRTC session forwarding.
- `trace_relay.py` keeps the OpenWebNet trace SSE stream alive.
- `services.yaml` documents Home Assistant services.
- Platform files create sensors, binary sensors, buttons, switches, events, cameras, and update entities.

This integration should be developed together with [BTicino GO Companion](https://github.com/r0bb10/BTicino-GO-Companion), because most features are direct consumers of companion API capabilities.
