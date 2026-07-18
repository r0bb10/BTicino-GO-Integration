# V3 Intercom Card Plan

## Goal

Provide a compact Lovelace intercom card for one V3 Companion entrypoint. It
keeps the established V2 layout and controls while using the V3 native camera
entity, Companion's Pion WebRTC service, and the exclusive StreamCoordinator.

The card supports outgoing calls, incoming call answer/decline, local
microphone mute, unlock, and passive snapshots.

## Non-Goals

- Do not port V2's direct Companion API or bearer-token use into JavaScript.
- Do not add go2rtc, AlexxIT/WebRTC, Advanced Camera Card, or another media
  relay.
- Do not create a second source owner or bypass StreamCoordinator leases.
- Do not use Companion-side audio mute for the card's microphone toggle.

## Card Configuration

Each card targets one native V3 camera entity, for example:

```text
camera.bticino_companion_gate1
```

The card resolves the Companion entrypoint from that camera. An unlock button
entity may be supplied explicitly or resolved from the same entrypoint.

## User Interaction

| State | Green phone button | Red phone button | Other controls |
| --- | --- | --- | --- |
| Idle | Start an outgoing WebRTC session with microphone enabled | Hidden | Unlock |
| Ringing for this gate | Answer the SIP call, then open WebRTC with microphone enabled | Decline the SIP call | Unlock |
| Connected | Hidden | Close the WebRTC session and end the active call | Local mute/unmute, unlock |

The green icon stays the same for Call and Answer. The red icon stays the same
for Decline and End. Labels, disabled state, and status text reflect the active
operation.

Mute only changes the browser microphone track's `enabled` property. Incoming
audio and video remain active, and Companion's intercom-side mute state is not
changed.

## Browser WebRTC

The card owns a small browser `RTCPeerConnection` because browser microphone
audio must be included in the initial SDP offer.

- Request microphone permission only from a green-button user gesture.
- Add the local audio track as `sendrecv` and request remote video/audio.
- Enable the microphone after a successful connection by default.
- Attach remote tracks to the card's video element.
- Close the peer connection, stop local media tracks, and request session close
  when the card disconnects, the user ends a call, or setup fails.
- Require HTTPS or a browser-trusted local context for microphone access.

The card must not call Companion directly. It must not contain a bearer token.

## Home Assistant Integration

The native V3 camera entity remains authoritative for entrypoint identity and
WebRTC session ownership.

1. Check whether Home Assistant exposes a stable generic camera-WebRTC
   signaling interface usable by a custom card.
2. If it does, use that interface for offer, ICE candidate, and close messages.
3. Otherwise add a minimal integration WebSocket bridge that accepts a camera
   entity ID and delegates offer, candidate, and close handling to that V3
   camera entity's existing WebRTC methods.
4. Add card-specific camera attributes that identify whether that entrypoint is
   ringing or connected. Do not restore the removed global Call State sensor.
5. Add Home Assistant services or equivalent internal commands for answer and
   hangup. The card calls Home Assistant, never Companion directly.
6. Bundle the card under the V3 integration's static frontend route and provide
   the required Lovelace resource setup through the Home Assistant UI.

## Companion V3 Work

Restore protected call-control endpoints omitted from the V3 API:

```text
POST /api/v3/call/answer
POST /api/v3/call/hangup
```

They delegate to the existing signaling manager:

- `Answer()` succeeds only when an incoming call is present.
- `Hangup()` declines an incoming call or ends an active call.
- Invalid lifecycle requests return a conflict response.
- Existing state projection and WebSocket broadcasts publish lifecycle changes.
- A card answering an incoming call sends its WebRTC offer only after answer
  succeeds.

No new Companion media transport is needed. The existing backchannel path is:

```text
Browser Opus -> Pion WebRTC -> AudioBridge -> intercom Speex
```

The existing StreamCoordinator remains the sole source owner. An outgoing card
session acquires its normal source lease; closing that session releases it.

## Reusable Reference Ideas

- AlexxIT/WebRTC confirms that microphone media must be part of the initial
  WebRTC negotiation and that browser microphone access requires HTTPS.
- Advanced Camera Card provides useful local microphone state concepts:
  disconnected, connected, muted, and unmuted.

Neither project is a runtime dependency or transport layer for V3.

## Verification

- Companion API tests cover answer, decline, active hangup, and invalid states.
- Integration tests cover call controls, the signaling bridge if needed, and
  camera call-state attributes.
- Browser tests cover outgoing call, incoming answer, decline, red end,
  microphone enabled by default, local mute/unmute, and cleanup.
- Physical tests verify video, incoming audio, microphone backchannel, unlock,
  and behavior when a second stream attempt conflicts with the active lease.
