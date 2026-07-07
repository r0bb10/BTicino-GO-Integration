class BTicinoGoIntercomCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = undefined;
    this._pc = undefined;
    this._remoteStream = undefined;
    this._localStream = undefined;
    this._micTrack = undefined;
    this._sessionId = undefined;
    this._connected = false;
    this._starting = false;
    this._talking = false;
    this._error = "";
  }

  setConfig(config) {
    if (!config.camera) throw new Error("camera is required");
    if (!config.entrypoint_id) throw new Error("entrypoint_id is required");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._pc) {
      this._updateDynamicParts();
      return;
    }
    this._render();
  }

  disconnectedCallback() {
    this._stop(false);
  }

  getCardSize() {
    return 6;
  }

  _state(entityId) {
    return entityId && this._hass ? this._hass.states[entityId] : undefined;
  }

  _stateText(entityId, fallback = "unknown") {
    return this._state(entityId)?.state ?? fallback;
  }

  _friendly(entityId, fallback) {
    return this._state(entityId)?.attributes?.friendly_name || fallback || entityId || "BTicino";
  }

  async _start() {
    if (this._starting || this._pc || !this._hass) return;
    this._starting = true;
    this._error = "";
    this._connected = false;
    this._talking = false;
    this._render();

    try {
      this._sessionId = `bticino-card-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      this._remoteStream = new MediaStream();
      try {
        this._localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        this._micTrack = this._localStream.getAudioTracks()[0];
        if (this._micTrack) this._micTrack.enabled = false;
      } catch (err) {
        this._localStream = undefined;
        this._micTrack = undefined;
        this._error = `Microphone unavailable; starting view-only (${err?.message || err})`;
      }

      const pc = new RTCPeerConnection();
      this._pc = pc;

      pc.ontrack = (event) => {
        for (const track of event.streams?.[0]?.getTracks?.() || [event.track]) {
          if (!this._remoteStream.getTracks().some((existing) => existing.id === track.id)) {
            this._remoteStream.addTrack(track);
          }
        }
        this._attachRemoteStream();
      };

      pc.onconnectionstatechange = () => {
        this._connected = ["connected", "completed"].includes(pc.connectionState);
        if (["failed", "closed", "disconnected"].includes(pc.connectionState)) this._talking = false;
        this._updateDynamicParts();
      };
      pc.oniceconnectionstatechange = () => this._updateDynamicParts();

      if (this._micTrack) {
        pc.addTrack(this._micTrack, this._localStream);
      } else {
        pc.addTransceiver("audio", { direction: "recvonly" });
      }
      pc.addTransceiver("video", { direction: "recvonly" });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await this._waitForIceGathering(pc);

      const response = await this._hass.connection.sendMessagePromise({
        type: "bticino_companion/webrtc_offer",
        entry_id: this._config.entry_id,
        entrypoint_id: this._config.entrypoint_id,
        session_id: this._sessionId,
        offer_sdp: pc.localDescription.sdp,
      });
      const answer = response?.answer_sdp;
      if (!answer) throw new Error("Companion returned no WebRTC answer");
      await pc.setRemoteDescription({ type: "answer", sdp: answer });
      this._attachRemoteStream();
    } catch (err) {
      this._error = err?.message || String(err);
      await this._stop(false);
    } finally {
      this._starting = false;
      this._updateDynamicParts();
    }
  }

  _waitForIceGathering(pc) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      const timeout = window.setTimeout(done, 5000);
      function done() {
        window.clearTimeout(timeout);
        pc.removeEventListener("icegatheringstatechange", onStateChange);
        resolve();
      }
      function onStateChange() {
        if (pc.iceGatheringState === "complete") done();
      }
      pc.addEventListener("icegatheringstatechange", onStateChange);
    });
  }

  _attachRemoteStream() {
    const video = this.shadowRoot?.querySelector("video");
    if (!video || !this._remoteStream) return;
    if (video.srcObject !== this._remoteStream) video.srcObject = this._remoteStream;
    video.muted = false;
    video.volume = 1;
    video.play?.().catch(() => undefined);
  }

  async _stop(callHangup = false) {
    const sessionId = this._sessionId;
    this._sessionId = undefined;
    this._connected = false;
    this._starting = false;
    this._talking = false;

    if (this._micTrack) this._micTrack.enabled = false;
    this._localStream?.getTracks?.().forEach((track) => track.stop());
    this._localStream = undefined;
    this._micTrack = undefined;

    this._pc?.close?.();
    this._pc = undefined;
    this._remoteStream = undefined;

    if (sessionId && this._hass?.connection) {
      this._hass.connection
        .sendMessagePromise({
          type: "bticino_companion/webrtc_close",
          entry_id: this._config.entry_id,
          session_id: sessionId,
        })
        .catch(() => undefined);
    }
    if (callHangup) await this._callService("bticino_companion", "call_hangup", {});
    this._render();
  }

  _setTalk(enabled) {
    if (!this._micTrack || !this._pc) return;
    this._micTrack.enabled = !!enabled;
    this._talking = !!enabled;
    this._updateDynamicParts();
  }

  async _unlock() {
    if (this._config.unlock_entity) {
      await this._callService("button", "press", {}, { entity_id: this._config.unlock_entity });
      return;
    }
    await this._callService("bticino_companion", "entrypoint_unlock", {
      entry_id: this._config.entry_id,
      entrypoint_id: this._config.entrypoint_id,
    });
  }

  async _callService(domain, service, data = {}, target = undefined) {
    if (!this._hass) return;
    try {
      await this._hass.callService(domain, service, data, target);
    } catch (err) {
      this._error = err?.message || String(err);
      this._updateDynamicParts();
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const cfg = this._config || {};
    const title = cfg.name || this._friendly(cfg.camera, "BTicino Intercom");
    const callState = this._stateText(cfg.call_state, "-");
    const streamState = this._stateText(cfg.stream_state, "-");
    const ringing = this._stateText(cfg.ringing, "off");
    const active = this._stateText(cfg.active_entrypoint, "none");
    const pcState = this._pc?.connectionState || "idle";
    const iceState = this._pc?.iceConnectionState || "idle";
    const startDisabled = this._starting || !!this._pc;
    const stopDisabled = !this._starting && !this._pc;
    const talkDisabled = !this._connected || !this._micTrack;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; background: var(--ha-card-background, var(--card-background-color)); }
        .wrap { display: grid; gap: 12px; padding: 14px; }
        .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .title { font-size: 18px; font-weight: 650; letter-spacing: .01em; }
        .sub { color: var(--secondary-text-color); font-size: 12px; }
        .viewer { position: relative; background: #05070a; border-radius: 16px; overflow: hidden; aspect-ratio: 16 / 10; min-height: 210px; }
        video { width: 100%; height: 100%; object-fit: cover; background: #05070a; display: block; }
        .placeholder { position: absolute; inset: 0; display: grid; place-items: center; color: #9aa4b2; text-align: center; padding: 24px; }
        .badge-row { display: flex; gap: 8px; flex-wrap: wrap; }
        .badge { border: 1px solid var(--divider-color); border-radius: 999px; padding: 5px 9px; font-size: 12px; color: var(--secondary-text-color); background: rgba(127,127,127,.08); }
        .badge.live { color: #9be28f; border-color: rgba(155,226,143,.35); }
        .badge.ring { color: #ffd166; border-color: rgba(255,209,102,.4); }
        .controls { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
        button { border: 0; border-radius: 14px; padding: 12px 10px; font: inherit; cursor: pointer; color: var(--primary-text-color); background: rgba(127,127,127,.14); }
        button:disabled { opacity: .45; cursor: not-allowed; }
        .primary { background: var(--primary-color); color: var(--text-primary-color, white); }
        .danger { background: #b3261e; color: white; }
        .talk { background: ${this._talking ? "#0f8b4c" : "rgba(127,127,127,.14)"}; color: ${this._talking ? "white" : "var(--primary-text-color)"}; }
        .unlock { background: #d99000; color: #111; }
        .error { color: var(--error-color, #db4437); font-size: 13px; }
        @media (max-width: 520px) { .controls { grid-template-columns: repeat(2, minmax(0, 1fr)); } .viewer { min-height: 180px; } }
      </style>
      <ha-card>
        <div class="wrap">
          <div class="head">
            <div>
              <div class="title">${this._escape(title)}</div>
              <div class="sub">${this._escape(cfg.entrypoint_id || "")}</div>
            </div>
            <div class="badge ${this._connected ? "live" : ""}" data-role="pc-state">${this._escape(pcState)} / ${this._escape(iceState)}</div>
          </div>
          <div class="viewer">
            <video playsinline autoplay></video>
            <div class="placeholder" data-role="placeholder" style="display: ${this._pc ? "none" : "grid"}">Press Start to open WebRTC video/audio. Hold Talk to send microphone audio.</div>
          </div>
          <div class="badge-row">
            <span class="badge ${callState === "active" ? "live" : ""}" data-role="call-state">call: ${this._escape(callState)}</span>
            <span class="badge ${streamState === "active" ? "live" : streamState === "preview" ? "ring" : ""}" data-role="stream-state">stream: ${this._escape(streamState)}</span>
            <span class="badge ${ringing === "on" ? "ring" : ""}" data-role="ringing">ringing: ${this._escape(ringing)}</span>
            <span class="badge" data-role="active-entrypoint">entry: ${this._escape(active)}</span>
          </div>
          <div class="error" data-role="error" style="display: ${this._error ? "block" : "none"}">${this._escape(this._error)}</div>
          <div class="controls">
            <button class="primary" ${startDisabled ? "disabled" : ""} data-action="start">${this._starting ? "Starting..." : "Start"}</button>
            <button class="talk" ${talkDisabled ? "disabled" : ""} data-action="talk">${this._talking ? "Talking" : "Hold Talk"}</button>
            <button class="unlock" data-action="unlock">Unlock</button>
            <button class="danger" ${stopDisabled ? "disabled" : ""} data-action="stop">Stop</button>
          </div>
        </div>
      </ha-card>
    `;

    this._attachRemoteStream();
    this._bindEvents();
  }

  _bindEvents() {
    const start = this.shadowRoot.querySelector('[data-action="start"]');
    const stop = this.shadowRoot.querySelector('[data-action="stop"]');
    const unlock = this.shadowRoot.querySelector('[data-action="unlock"]');
    const talk = this.shadowRoot.querySelector('[data-action="talk"]');
    start?.addEventListener("click", () => this._start());
    stop?.addEventListener("click", () => this._stop(false));
    unlock?.addEventListener("click", () => this._unlock());
    talk?.addEventListener("pointerdown", (ev) => { ev.preventDefault(); this._setTalk(true); });
    talk?.addEventListener("pointerup", () => this._setTalk(false));
    talk?.addEventListener("pointerleave", () => this._setTalk(false));
    talk?.addEventListener("pointercancel", () => this._setTalk(false));
  }

  _updateDynamicParts() {
    if (!this.shadowRoot?.querySelector("ha-card")) {
      this._render();
      return;
    }

    const cfg = this._config || {};
    const callState = this._stateText(cfg.call_state, "-");
    const streamState = this._stateText(cfg.stream_state, "-");
    const ringing = this._stateText(cfg.ringing, "off");
    const active = this._stateText(cfg.active_entrypoint, "none");
    const pcState = this._pc?.connectionState || "idle";
    const iceState = this._pc?.iceConnectionState || "idle";
    const startDisabled = this._starting || !!this._pc;
    const stopDisabled = !this._starting && !this._pc;
    const talkDisabled = !this._connected || !this._micTrack;

    this._setRoleText("pc-state", `${pcState} / ${iceState}`, this._connected ? "live" : "");
    this._setRoleText("call-state", `call: ${callState}`, callState === "active" ? "live" : "");
    this._setRoleText("stream-state", `stream: ${streamState}`, streamState === "active" ? "live" : streamState === "preview" ? "ring" : "");
    this._setRoleText("ringing", `ringing: ${ringing}`, ringing === "on" ? "ring" : "");
    this._setRoleText("active-entrypoint", `entry: ${active}`, "");

    const error = this.shadowRoot.querySelector('[data-role="error"]');
    if (error) {
      error.textContent = this._error || "";
      error.style.display = this._error ? "block" : "none";
    }
    const placeholder = this.shadowRoot.querySelector('[data-role="placeholder"]');
    if (placeholder) placeholder.style.display = this._pc ? "none" : "grid";

    const start = this.shadowRoot.querySelector('[data-action="start"]');
    const stop = this.shadowRoot.querySelector('[data-action="stop"]');
    const talk = this.shadowRoot.querySelector('[data-action="talk"]');
    if (start) {
      start.disabled = startDisabled;
      start.textContent = this._starting ? "Starting..." : "Start";
    }
    if (stop) stop.disabled = stopDisabled;
    if (talk) {
      talk.disabled = talkDisabled;
      talk.textContent = this._talking ? "Talking" : this._micTrack ? "Hold Talk" : "No Mic";
      talk.classList.toggle("talking", this._talking);
      talk.style.background = this._talking ? "#0f8b4c" : "rgba(127,127,127,.14)";
      talk.style.color = this._talking ? "white" : "var(--primary-text-color)";
    }
    this._attachRemoteStream();
  }

  _setRoleText(role, text, stateClass) {
    const el = this.shadowRoot.querySelector(`[data-role="${role}"]`);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("live", stateClass === "live");
    el.classList.toggle("ring", stateClass === "ring");
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    }[char]));
  }
}

customElements.define("bticino-go-intercom-card", BTicinoGoIntercomCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "bticino-go-intercom-card",
  name: "BTicino Go Intercom Card",
  description: "BTicino Companion WebRTC intercom test card with push-to-talk audio.",
});
