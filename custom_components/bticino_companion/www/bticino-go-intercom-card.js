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
    const snapshot = this._snapshotUrl();
    const active = this._connected || this._connecting;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; } ha-card { overflow: hidden; }
        .title { padding: 12px 14px; font-weight: 600; } .viewer { position: relative; background: #111; aspect-ratio: 16 / 10; display: grid; place-items: center; }
        img, video { width: 100%; height: 100%; object-fit: cover; } .placeholder { color: #aaa; } .hidden { display: none !important; }
        .status { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,.6); color: #fff; border-radius: 12px; padding: 4px 8px; font-size: 12px; }
        .controls { display: flex; justify-content: center; gap: 16px; padding: 14px; } button { width: 52px; height: 52px; border: 0; border-radius: 50%; color: #fff; cursor: pointer; font-size: 20px; }
        .call { background: #2e7d32; } .end { background: #c62828; } .mute { background: #546e7a; } .unlock { background: #6d6d6d; } .error { color: var(--error-color); padding: 0 14px 12px; font-size: 13px; }
      </style>
      <ha-card><div class="title">${this._escape(name)}</div><div class="viewer">
        <img class="${active || !snapshot ? "hidden" : ""}" src="${this._escape(snapshot)}" alt="${this._escape(name)}" />
        <div class="placeholder ${active || snapshot ? "hidden" : ""}">No snapshot available</div>
        <video class="${this._connected ? "" : "hidden"}" playsinline autoplay></video>
        <div class="status">${this._connecting ? "Connecting" : this._connected ? (this._muted ? "Muted" : "Connected") : "Idle"}</div>
      </div><div class="controls">
        <button class="call ${active ? "hidden" : ""}" title="Call">&#9742;</button>
        <button class="end ${active ? "" : "hidden"}" title="End">&#9742;</button>
        <button class="mute ${this._connected ? "" : "hidden"}" title="${this._muted ? "Unmute" : "Mute"}">${this._muted ? "&#128263;" : "&#127908;"}</button>
         <button class="unlock" title="Unlock">&#128273;</button>
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
