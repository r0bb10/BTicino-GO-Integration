class BTicinoGoIntercomCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._pc = undefined;
    this._localStream = undefined;
    this._micTrack = undefined;
    this._remoteStream = undefined;
    this._sessionId = undefined;
    this._queuedCandidates = [];
    this._offerAccepted = false;
    this._connecting = false;
    this._connected = false;
    this._muted = false;
    this._error = "";
  }

  setConfig(config) {
    if (!config.camera) throw new Error("camera entity is required");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    this._attachVideo();
  }

  disconnectedCallback() {
    this._cleanup();
  }

  getCardSize() {
    return 5;
  }

  _cameraState() {
    return this._hass?.states[this._config.camera];
  }

  _snapshotUrl() {
    const picture = this._cameraState()?.attributes?.entity_picture;
    return picture ? this._hass.hassUrl(picture) : "";
  }

  _entrypointLabel() {
    const attributes = this._cameraState()?.attributes;
    return attributes?.bticino_entrypoint_label || this._config.name || attributes?.friendly_name || this._config.camera || "Intercom";
  }

  _callState() {
    return this._cameraState()?.attributes?.bticino_call_state || "idle";
  }

  _isRinging() {
    return this._cameraState()?.attributes?.bticino_is_ringing === true;
  }

  _send(type, payload) {
    if (!this._hass) throw new Error("Home Assistant is not connected");
    return this._hass.connection.sendMessagePromise({
      type,
      camera_entity_id: this._config.camera,
      ...payload,
    });
  }

  _candidatePayload(candidate) {
    return {
      candidate: candidate.candidate,
      sdpMid: candidate.sdpMid,
      sdpMLineIndex: candidate.sdpMLineIndex,
      usernameFragment: candidate.usernameFragment,
    };
  }

  _newSessionId() {
    if (globalThis.crypto?.randomUUID) return `bticino-card-${globalThis.crypto.randomUUID()}`;
    if (globalThis.crypto?.getRandomValues) {
      const values = new Uint32Array(2);
      globalThis.crypto.getRandomValues(values);
      return `bticino-card-${Date.now()}-${values[0].toString(16)}${values[1].toString(16)}`;
    }
    return `bticino-card-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  _sendCandidate(candidate) {
    if (!this._sessionId) return Promise.resolve();
    return this._send("bticino_companion/card_webrtc_candidate", {
      session_id: this._sessionId,
      candidate,
    });
  }

  async _start() {
    if (this._pc || this._connecting) return;
    this._connecting = true;
    this._error = "";
    this._render();
    try {
      this._sessionId = this._newSessionId();
      this._remoteStream = new MediaStream();
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Microphone access requires HTTPS or a trusted local browser context.");
      }
      this._localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this._micTrack = this._localStream.getAudioTracks()[0];
      this._micTrack.enabled = false;

      const pc = new RTCPeerConnection();
      this._pc = pc;
      pc.ontrack = (event) => {
        for (const track of event.streams[0]?.getTracks() || [event.track]) {
          if (!this._remoteStream.getTracks().some((current) => current.id === track.id)) {
            this._remoteStream.addTrack(track);
          }
        }
        this._attachVideo();
      };
      pc.onicecandidate = ({ candidate }) => {
        if (!candidate) return;
        const payload = this._candidatePayload(candidate);
        if (this._offerAccepted) this._sendCandidate(payload).catch(() => {});
        else this._queuedCandidates.push(payload);
      };
      pc.onconnectionstatechange = () => {
        this._connected = pc.connectionState === "connected";
        if (this._connected && this._micTrack) this._micTrack.enabled = !this._muted;
        if (["failed", "disconnected", "closed"].includes(pc.connectionState)) this._connected = false;
        this._render();
      };
      pc.addTrack(this._micTrack, this._localStream);
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const response = await this._send("bticino_companion/card_webrtc_offer", {
        session_id: this._sessionId,
        offer_sdp: pc.localDescription.sdp,
      });
      if (!response?.answer_sdp) throw new Error("Camera returned no WebRTC answer");
      this._offerAccepted = true;
      await pc.setRemoteDescription({ type: "answer", sdp: response.answer_sdp });
      await Promise.all(this._queuedCandidates.splice(0).map((candidate) => this._sendCandidate(candidate)));
      this._attachVideo();
    } catch (err) {
      if (err?.name === "NotFoundError") {
        this._error = "No microphone is available to this browser.";
      } else if (err?.name === "NotAllowedError") {
        this._error = "Microphone permission was denied.";
      } else {
        this._error = err?.message || String(err);
      }
      this._cleanup();
    }
    this._connecting = false;
    this._render();
  }

  _attachVideo() {
    const video = this.shadowRoot?.querySelector("video");
    if (video && this._remoteStream && video.srcObject !== this._remoteStream) {
      video.srcObject = this._remoteStream;
      video.play().catch(() => {});
    }
  }

  _toggleMute() {
    if (!this._micTrack || !this._connected) return;
    this._muted = !this._muted;
    this._micTrack.enabled = !this._muted;
    this._render();
  }

  async _unlock() {
    await this._send("bticino_companion/card_unlock", {});
  }

  _cleanup() {
    const sessionId = this._sessionId;
    this._sessionId = undefined;
    this._offerAccepted = false;
    this._queuedCandidates = [];
    this._connected = false;
    this._connecting = false;
    this._muted = false;
    this._micTrack?.stop();
    this._localStream?.getTracks().forEach((track) => track.stop());
    this._localStream = undefined;
    this._micTrack = undefined;
    this._pc?.close();
    this._pc = undefined;
    this._remoteStream = undefined;
    if (sessionId && this._hass) {
      this._send("bticino_companion/card_webrtc_close", { session_id: sessionId }).catch(() => {});
    }
  }

  _end() {
    this._cleanup();
    this._render();
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  _render() {
    if (!this.shadowRoot) return;
    const name = this._config.name || this._cameraState()?.attributes?.friendly_name || this._config.camera || "Intercom";
    const entrypoint = this._entrypointLabel();
    const snapshot = this._snapshotUrl();
    const ringing = this._isRinging();
    const active = this._connected || this._connecting;
    const callState = this._callState();
    const remoteActive = callState === "active" || callState === "preview";
    const status = this._connecting
      ? ["Connecting", "connecting"]
      : this._connected
        ? [this._muted ? "Muted" : "Connected", "active"]
        : ringing
          ? ["Ringing", "ringing"]
          : remoteActive
            ? [callState === "preview" ? "Preview" : "Active", "active"]
            : ["Idle", "idle"];
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; } ha-card { overflow: hidden; }
        .title-bar { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 0; }
        .title { font-size: 16px; font-weight: 600; } .subtitle { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
        .status { align-items: center; color: var(--secondary-text-color); display: flex; font-size: 12px; gap: 6px; }
        .dot { border-radius: 50%; height: 8px; width: 8px; } .dot-idle { background: rgba(127,127,127,.4); } .dot-active { background: #2e7d32; }
        .dot-ringing, .dot-connecting { animation: pulse 1.2s ease-in-out infinite; } .dot-ringing { background: #f9a825; } .dot-connecting { background: #90caf9; }
        @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .5; transform: scale(1.3); } }
        .viewer { position: relative; background: #111; aspect-ratio: 16 / 10; display: grid; place-items: center; } img, video { width: 100%; height: 100%; object-fit: cover; } .placeholder { color: #aaa; } .hidden { display: none !important; }
        .ring-overlay { align-items: center; background: rgba(0,0,0,.55); color: #fff; display: flex; flex-direction: column; gap: 8px; inset: 0; justify-content: center; position: absolute; }
        .ring-pulse { animation: pulse 1.2s ease-in-out infinite; background: #f9a825; border-radius: 50%; height: 20px; width: 20px; } .ring-label { font-size: 16px; font-weight: 600; }
        .controls { display: flex; justify-content: center; gap: 16px; padding: 14px; } button { align-items: center; border: 0; border-radius: 50%; color: #fff; cursor: pointer; display: flex; height: 52px; justify-content: center; width: 52px; } button:disabled { cursor: not-allowed; opacity: .4; } button svg { fill: currentColor; height: 26px; width: 26px; }
        .call { background: #2e7d32; } .end { background: #c62828; } .mute { background: #546e7a; } .unlock { background: #6d6d6d; } .error { color: var(--error-color); padding: 0 14px 12px; font-size: 13px; }
      </style>
      <ha-card><div class="title-bar"><div><div class="title">${this._escape(name)}</div><div class="subtitle">${this._escape(entrypoint)}</div></div><div class="status"><span class="dot dot-${status[1]}"></span>${status[0]}</div></div><div class="viewer">
        <img class="${active || !snapshot ? "hidden" : ""}" src="${this._escape(snapshot)}" alt="${this._escape(name)}" />
        <div class="placeholder ${active || snapshot ? "hidden" : ""}">No snapshot available</div>
        <video class="${this._connected ? "" : "hidden"}" playsinline autoplay></video>
        <div class="ring-overlay ${ringing && !active ? "" : "hidden"}"><div class="ring-pulse"></div><div class="ring-label">${this._escape(entrypoint)}</div><div>Incoming call</div></div>
      </div><div class="controls">
        <button class="call ${active || ringing ? "hidden" : ""}" title="Start live view"><svg viewBox="0 0 24 24"><path d="M6.62,10.79C8.06,13.62 10.38,15.94 13.21,17.38L15.41,15.18C15.69,14.9 16.08,14.82 16.43,14.93C17.55,15.3 18.75,15.5 20,15.5A1,1 0 0,1 21,16.5V20A1,1 0 0,1 20,21A17,17 0 0,1 3,4A1,1 0 0,1 4,3H7.5A1,1 0 0,1 8.5,4C8.5,5.25 8.7,6.45 9.07,7.57C9.18,7.92 9.1,8.31 8.82,8.59L6.62,10.79Z"/></svg></button>
        <button class="end ${active ? "" : "hidden"}" title="End live view"><svg viewBox="0 0 24 24"><path d="M12,9C10.4,9 8.85,9.25 7.4,9.72V12.82C7.4,13.22 7.17,13.56 6.84,13.72C5.86,14.21 4.97,14.84 4.17,15.57C4,15.75 3.75,15.86 3.5,15.86C3.2,15.86 2.95,15.74 2.8,15.55L0.29,13.04C0.11,12.86 0,12.61 0,12.31C0,12 0.11,11.76 0.29,11.58C3.34,8.5 7.46,6.5 12,6.5C16.54,6.5 20.66,8.5 23.71,11.58C23.89,11.76 24,12 24,12.31C24,12.61 23.89,12.86 23.71,13.04L21.2,15.55C21.05,15.74 20.8,15.86 20.5,15.86C20.25,15.86 20,15.75 19.82,15.57C19.03,14.84 18.14,14.21 17.16,13.72C16.83,13.56 16.6,13.22 16.6,12.82V9.72C15.15,9.25 13.6,9 12,9Z"/></svg></button>
        <button class="mute ${this._connected ? "" : "hidden"}" title="${this._muted ? "Unmute" : "Mute"}"><svg viewBox="0 0 24 24"><path d="M12,14C13.66,14 15,12.66 15,11V5C15,3.34 13.66,2 12,2S9,3.34 9,5V11C9,12.66 10.34,14 12,14M17.3,11C17.3,14 14.76,16.1 12,16.1S6.7,14 6.7,11H5C5,14.41 7.72,17.23 11,17.72V21H13V17.72C16.28,17.23 19,14.41 19,11H17.3Z"/></svg></button>
        <button class="unlock" title="Unlock"><svg viewBox="0 0 24 24"><path d="M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10A2,2 0 0,1 6,8H15V6A3,3 0 0,0 12,3A3,3 0 0,0 9,6H7A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18Z"/></svg></button>
       </div><div class="error ${this._error ? "" : "hidden"}">${this._escape(this._error)}</div></ha-card>`;
    this.shadowRoot.querySelector(".call")?.addEventListener("click", () => this._start());
    this.shadowRoot.querySelector(".end")?.addEventListener("click", () => this._end());
    this.shadowRoot.querySelector(".mute")?.addEventListener("click", () => this._toggleMute());
    this.shadowRoot.querySelector(".unlock")?.addEventListener("click", () => this._unlock().catch((err) => { this._error = err.message; this._render(); }));
    if (this._connected) this._attachVideo();
  }
}

customElements.define("bticino-go-intercom-card", BTicinoGoIntercomCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "bticino-go-intercom-card", name: "BTicino Go Intercom", description: "Single-gate outgoing WebRTC intercom card." });
