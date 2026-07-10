class BTicinoGoIntercomCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = {};
    this._pc = undefined;
    this._remoteStream = undefined;
    this._localStream = undefined;
    this._micTrack = undefined;
    this._sessionId = undefined;
    this._connected = false;
    this._connecting = false;
    this._talking = false;
    this._error = "";
  }

  setConfig(config) {
    if (!config.camera) throw new Error("camera entity is required");
    if (!config.entrypoint_id) throw new Error("entrypoint_id is required");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this.shadowRoot?.querySelector("ha-card")) {
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

  _state(eid) {
    return eid && this._hass ? this._hass.states[eid] : undefined;
  }

  _stateText(eid, fallback = "") {
    const s = this._state(eid);
    return s ? String(s.state) : fallback;
  }

  _friendly(eid, fallback) {
    const s = this._state(eid);
    return s?.attributes?.friendly_name || fallback || eid || "";
  }

  _isRinging() {
    return this._stateText(this._config.ringing, "off") === "on" && this._stateText(this._config.call_state, "idle") === "ringing" && this._matchesEntrypoint();
  }

  _matchesEntrypoint() {
    return this._stateText(this._config.active_entrypoint, "none") === this._config.entrypoint_id;
  }

  _entrypointName() {
    const id = this._config.entrypoint_id || "";
    const names = { gate1: "Cancelletto", gate2: "Principale", gate3: "Secondario" };
    return names[id] || id;
  }

  _cameraSrc() {
    const st = this._state(this._config.camera);
    if (!st?.attributes?.entity_picture) return null;
    return this._hass.hassUrl(st.attributes.entity_picture);
  }

  async _callService(domain, service, data = {}, target = undefined) {
    if (!this._hass) throw new Error("HA not connected");
    return this._hass.callService(domain, service, data, target);
  }

  _waitForIce(pc) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      const tid = window.setTimeout(done, 5000);
      function done() {
        window.clearTimeout(tid);
        pc.removeEventListener("icegatheringstatechange", onIce);
        resolve();
      }
      function onIce() {
        if (pc.iceGatheringState === "complete") done();
      }
      pc.addEventListener("icegatheringstatechange", onIce);
    });
  }

  async _startWebRTC() {
    this._sessionId = `bticino-card-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    this._remoteStream = new MediaStream();
    try {
      this._localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this._micTrack = this._localStream.getAudioTracks()[0];
      if (this._micTrack) this._micTrack.enabled = false;
    } catch {
      this._localStream = undefined;
      this._micTrack = undefined;
    }
    const pc = new RTCPeerConnection();
    this._pc = pc;
    pc.ontrack = (event) => {
      for (const t of event.streams?.[0]?.getTracks() || [event.track]) {
        if (!this._remoteStream.getTracks().some((x) => x.id === t.id)) {
          this._remoteStream.addTrack(t);
        }
      }
      this._attachVideo();
    };
    pc.onconnectionstatechange = () => {
      this._connected = ["connected", "completed"].includes(pc.connectionState);
      if (this._connected && this._micTrack) this._micTrack.enabled = true;
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
    await this._waitForIce(pc);
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
    this._attachVideo();
    this._connected = true;
    if (this._micTrack) this._micTrack.enabled = true;
    this._connecting = false;
    this._error = "";
    this._updateDynamicParts();
  }

  _attachVideo() {
    const video = this.shadowRoot?.querySelector("#video");
    if (!video || !this._remoteStream) return;
    if (video.srcObject !== this._remoteStream) video.srcObject = this._remoteStream;
    video.muted = false;
    video.volume = 1;
    video.play?.().catch(() => {});
  }

  async _start() {
    if (this._connecting || this._pc) return;
    this._connecting = true;
    this._error = "";
    this._render();
    try {
      await this._startWebRTC();
    } catch (err) {
      this._error = err?.message || String(err);
      this._cleanup();
    }
    this._connecting = false;
    this._updateDynamicParts();
  }

  async _answer() {
    if (this._connecting || this._pc) return;
    this._connecting = true;
    this._error = "";
    this._render();
    try {
      await this._callService("bticino_companion", "call_answer", {});
      await this._startWebRTC();
    } catch (err) {
      this._error = err?.message || String(err);
      this._cleanup();
    }
    this._connecting = false;
    this._updateDynamicParts();
  }

  async _decline() {
    try {
      await this._callService("bticino_companion", "call_hangup", {});
    } catch {}
    this._cleanup();
    this._render();
  }

  async _hangup() {
    try {
      await this._callService("bticino_companion", "call_hangup", {});
    } catch {}
    this._cleanup();
    this._render();
  }

  async _stop(callHangup = false) {
    this._cleanup();
    if (callHangup) {
      await this._callService("bticino_companion", "call_hangup", {});
    }
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
    } else {
      await this._callService("bticino_companion", "entrypoint_unlock", {
        entry_id: this._config.entry_id,
        entrypoint_id: this._config.entrypoint_id,
      });
    }
  }

  _cleanup() {
    const sessionId = this._sessionId;
    this._sessionId = undefined;
    this._connected = false;
    this._connecting = false;
    this._talking = false;
    if (this._micTrack) this._micTrack.enabled = false;
    this._localStream?.getTracks().forEach((t) => t.stop());
    this._localStream = undefined;
    this._micTrack = undefined;
    if (this._pc) {
      this._pc.close();
      this._pc = undefined;
    }
    this._remoteStream = undefined;
    if (sessionId && this._hass) {
      this._hass.connection.sendMessagePromise({
        type: "bticino_companion/webrtc_close",
        entry_id: this._config.entry_id,
        session_id: sessionId,
      }).catch(() => {});
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; background: var(--ha-card-background, var(--card-background-color)); }
        .wrap { display: grid; gap: 0; }
        .viewer { position: relative; background: #111; min-height: 200px; aspect-ratio: 16/10; display: flex; align-items: center; justify-content: center; overflow: hidden; }
        .viewer img, .viewer video { width: 100%; height: 100%; object-fit: cover; display: block; }
        .viewer .placeholder { color: #555; font-size: 14px; }
        .hidden { display: none !important; }
        .ring-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; background: rgba(0,0,0,0.55); }
        .ring-overlay .ring-label { color: #fff; font-size: 16px; font-weight: 600; letter-spacing: 0.02em; text-align: center; }
        .ring-overlay .ring-sub { color: rgba(255,255,255,0.7); font-size: 13px; }
        .ring-pulse { width: 20px; height: 20px; border-radius: 50%; background: #2e7d32; animation: pulse 1.2s ease-in-out infinite; }
        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(1.3); } 100% { opacity: 1; transform: scale(1); } }
        .controls { display: flex; justify-content: center; gap: 16px; padding: 16px 14px; background: var(--ha-card-background); }
        .btn { width: 56px; height: 56px; border-radius: 50%; border: 0; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: background 0.15s, opacity 0.15s; }
        .btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .btn svg { width: 26px; height: 26px; fill: currentColor; }
        .btn-call { background: #2e7d32; color: #fff; }
        .btn-call:hover:not(:disabled) { background: #1b5e20; }
        .btn-hangup { background: #c62828; color: #fff; }
        .btn-hangup:hover:not(:disabled) { background: #b71c1c; }
        .btn-pickup { background: #2e7d32; color: #fff; }
        .btn-pickup:hover:not(:disabled) { background: #1b5e20; }
        .btn-decline { background: #c62828; color: #fff; }
        .btn-decline:hover:not(:disabled) { background: #b71c1c; }
        .btn-unlock { background: rgba(127,127,127,0.15); color: var(--primary-text-color); }
        .btn-unlock:hover:not(:disabled) { background: rgba(127,127,127,0.3); }
        .title-bar { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 0; }
        .title-text { font-size: 16px; font-weight: 600; }
        .title-sub { font-size: 12px; color: var(--secondary-text-color); }
        .error-bar { color: var(--error-color, #db4437); font-size: 13px; padding: 0 14px 8px; }
        .spinner { width: 32px; height: 32px; border: 3px solid rgba(255,255,255,0.2); border-top-color: #fff; border-radius: 50%; animation: spin 0.7s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .status { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary-text-color); }
        .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .dot-active { background: #2e7d32; }
        .dot-ringing { background: #f9a825; animation: pulse 1.2s ease-in-out infinite; }
        .dot-connecting { background: #90caf9; animation: pulse 1.2s ease-in-out infinite; }
        .dot-idle { background: rgba(127,127,127,0.3); }
      </style>
      <ha-card>
        <div class="title-bar">
          <div>
            <div class="title-text" data-role="title"></div>
            <div class="title-sub" data-role="subtitle"></div>
          </div>
          <div class="status"><span class="dot" data-role="status-dot"></span><span data-role="status-label"></span></div>
        </div>
        <div class="viewer">
          <img data-role="idle-image" alt="" />
          <div class="placeholder" data-role="placeholder"></div>
          <video id="video" playsinline autoplay></video>
          <div class="ring-overlay" data-role="ring-overlay">
            <div class="ring-pulse"></div>
            <div class="ring-label" data-role="ring-label"></div>
            <div class="ring-sub">Incoming call...</div>
          </div>
          <div data-role="connecting-overlay" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5)">
            <div class="spinner"></div>
          </div>
        </div>
        <div class="error-bar" data-role="error"></div>
        <div class="controls">
          <button class="btn btn-call" data-action="call" title="Call">
            <svg viewBox="0 0 24 24"><path d="M6.62,10.79C8.06,13.62 10.38,15.94 13.21,17.38L15.41,15.18C15.69,14.9 16.08,14.82 16.43,14.93C17.55,15.3 18.75,15.5 20,15.5A1,1 0 0,1 21,16.5V20A1,1 0 0,1 20,21A17,17 0 0,1 3,4A1,1 0 0,1 4,3H7.5A1,1 0 0,1 8.5,4C8.5,5.25 8.7,6.45 9.07,7.57C9.18,7.92 9.1,8.31 8.82,8.59L6.62,10.79Z"/></svg>
          </button>
          <button class="btn btn-decline" data-action="decline" title="Decline">
            <svg viewBox="0 0 24 24"><path d="M12,9C10.4,9 8.85,9.25 7.4,9.72V12.82C7.4,13.22 7.17,13.56 6.84,13.72C5.86,14.21 4.97,14.84 4.17,15.57C4,15.75 3.75,15.86 3.5,15.86C3.2,15.86 2.95,15.74 2.8,15.55L0.29,13.04C0.11,12.86 0,12.61 0,12.31C0,12 0.11,11.76 0.29,11.58C3.34,8.5 7.46,6.5 12,6.5C16.54,6.5 20.66,8.5 23.71,11.58C23.89,11.76 24,12 24,12.31C24,12.61 23.89,12.86 23.71,13.04L21.2,15.55C21.05,15.74 20.8,15.86 20.5,15.86C20.25,15.86 20,15.75 19.82,15.57C19.03,14.84 18.14,14.21 17.16,13.72C16.83,13.56 16.6,13.22 16.6,12.82V9.72C15.15,9.25 13.6,9 12,9Z"/></svg>
          </button>
          <button class="btn btn-pickup" data-action="answer" title="Answer">
            <svg viewBox="0 0 24 24"><path d="M6.62,10.79C8.06,13.62 10.38,15.94 13.21,17.38L15.41,15.18C15.69,14.9 16.08,14.82 16.43,14.93C17.55,15.3 18.75,15.5 20,15.5A1,1 0 0,1 21,16.5V20A1,1 0 0,1 20,21A17,17 0 0,1 3,4A1,1 0 0,1 4,3H7.5A1,1 0 0,1 8.5,4C8.5,5.25 8.7,6.45 9.07,7.57C9.18,7.92 9.1,8.31 8.82,8.59L6.62,10.79Z"/></svg>
          </button>
          <button class="btn btn-hangup" data-action="hangup" title="Hang up">
            <svg viewBox="0 0 24 24"><path d="M12,9C10.4,9 8.85,9.25 7.4,9.72V12.82C7.4,13.22 7.17,13.56 6.84,13.72C5.86,14.21 4.97,14.84 4.17,15.57C4,15.75 3.75,15.86 3.5,15.86C3.2,15.86 2.95,15.74 2.8,15.55L0.29,13.04C0.11,12.86 0,12.61 0,12.31C0,12 0.11,11.76 0.29,11.58C3.34,8.5 7.46,6.5 12,6.5C16.54,6.5 20.66,8.5 23.71,11.58C23.89,11.76 24,12 24,12.31C24,12.61 23.89,12.86 23.71,13.04L21.2,15.55C21.05,15.74 20.8,15.86 20.5,15.86C20.25,15.86 20,15.75 19.82,15.57C19.03,14.84 18.14,14.21 17.16,13.72C16.83,13.56 16.6,13.22 16.6,12.82V9.72C15.15,9.25 13.6,9 12,9Z"/></svg>
          </button>
          <button class="btn btn-unlock" data-action="unlock" title="Unlock">
            <svg viewBox="0 0 24 24"><path d="M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10A2,2 0 0,1 6,8H15V6A3,3 0 0,0 12,3A3,3 0 0,0 9,6H7A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18Z"/></svg>
          </button>
        </div>
      </ha-card>
    `;
    this._bindEvents();
    this._updateDynamicParts();
  }

  _updateDynamicParts() {
    if (!this.shadowRoot?.querySelector("ha-card")) return;

    const cfg = this._config || {};
    const title = cfg.name || this._friendly(cfg.camera, "BTicino");
    const epName = this._entrypointName();
    const callState = this._stateText(cfg.call_state, "idle");
    const ringing = this._isRinging();
    const active = callState === "active";
    const webrtcActive = !!this._pc && this._connected;
    const cameraSrc = this._cameraSrc();

    const showRinging = ringing && !webrtcActive && !this._connecting;
    const showActive = webrtcActive || (active && this._pc && !this._connecting);
    const showConnecting = this._connecting;
    const showIdle = !showRinging && !showActive && !showConnecting;

    let statusDot = "dot-idle";
    let statusLabel = "Idle";
    if (active) {
      statusDot = "dot-active";
      statusLabel = "Active";
    } else if (callState === "ringing" && this._matchesEntrypoint()) {
      statusDot = "dot-ringing";
      statusLabel = "Ringing";
    }
    if (showConnecting) {
      statusDot = "dot-connecting";
      statusLabel = "Connecting\u2026";
    }

    this._setText("title", title);
    this._setText("subtitle", epName);
    this._setText("status-label", statusLabel);
    this._setText("placeholder", epName);
    this._setText("ring-label", epName);

    const dot = this.shadowRoot.querySelector('[data-role="status-dot"]');
    if (dot) dot.className = `dot ${statusDot}`;

    const image = this.shadowRoot.querySelector('[data-role="idle-image"]');
    if (image) {
      if (cameraSrc) image.setAttribute("src", cameraSrc);
      else image.removeAttribute("src");
    }

    this._setHidden("idle-image", !showIdle || !cameraSrc);
    this._setHidden("placeholder", !showIdle || !!cameraSrc);
    this._setHidden("video", !showActive);
    this._setHidden("ring-overlay", !showRinging);
    this._setHidden("connecting-overlay", !showConnecting);

    const error = this.shadowRoot.querySelector('[data-role="error"]');
    if (error) {
      error.textContent = this._error || "";
      error.classList.toggle("hidden", !this._error);
    }

    this._setActionVisible("call", showIdle);
    this._setActionVisible("decline", showRinging);
    this._setActionVisible("answer", showRinging);
    this._setActionVisible("hangup", showActive);
    this._setActionVisible("unlock", showIdle || showActive);
    this._setActionDisabled("call", this._connecting || !!this._pc);
    this._setActionDisabled("answer", this._connecting || !!this._pc);
    this._setActionDisabled("decline", this._connecting);
    this._setActionDisabled("hangup", this._connecting);

    if (showActive) this._attachVideo();
  }

  _setText(role, text) {
    const el = this.shadowRoot?.querySelector(`[data-role="${role}"]`);
    if (el) el.textContent = text || "";
  }

  _setHidden(role, hidden) {
    const el = role === "video" ? this.shadowRoot?.querySelector("#video") : this.shadowRoot?.querySelector(`[data-role="${role}"]`);
    if (el) el.classList.toggle("hidden", !!hidden);
  }

  _setActionVisible(action, visible) {
    const el = this.shadowRoot?.querySelector(`[data-action="${action}"]`);
    if (el) el.classList.toggle("hidden", !visible);
  }

  _setActionDisabled(action, disabled) {
    const el = this.shadowRoot?.querySelector(`[data-action="${action}"]`);
    if (el) el.disabled = !!disabled;
  }

  _bindEvents() {
    this.shadowRoot.querySelector('[data-action="call"]')?.addEventListener("click", () => this._start());
    this.shadowRoot.querySelector('[data-action="answer"]')?.addEventListener("click", () => this._answer());
    this.shadowRoot.querySelector('[data-action="decline"]')?.addEventListener("click", () => this._decline());
    this.shadowRoot.querySelector('[data-action="hangup"]')?.addEventListener("click", () => this._hangup());
    this.shadowRoot.querySelector('[data-action="unlock"]')?.addEventListener("click", () => this._unlock());
  }

  _esc(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    }[ch]));
  }
}

customElements.define("bticino-go-intercom-card", BTicinoGoIntercomCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "bticino-go-intercom-card",
  name: "BTicino Go Intercom Card",
  description: "BTicino Companion WebRTC intercom card with incoming ring pickup and hands-free audio.",
});
