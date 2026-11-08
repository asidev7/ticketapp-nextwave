/* NextWave entrance scanner (PWA).
   Camera via html5-qrcode (rear camera), manual code entry, full-screen result
   for 1.5 s with sound + vibration, per-device history of the last 20 scans. */
(function () {
  "use strict";

  var cfg = document.getElementById("scanner-config").dataset;
  var resultBox = document.querySelector("[data-result]");
  var statusEl = document.querySelector("[data-cam-status]");
  var startBtn = document.querySelector("[data-start-camera]");
  var historyList = document.querySelector("[data-history]");
  var fmt = new Intl.NumberFormat("en-US");
  var HISTORY_KEY = "nwp-scan-history";
  var RESULT_MS = 1500;

  var busy = false;
  var lastValue = "";
  var lastAt = 0;
  var scanner = null;
  var audio = null;

  // ---------- Service worker (installable PWA) ----------
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(cfg.sw, { scope: "/scan/" }).catch(function () {});
  }

  // Keep the screen awake while scanning.
  function keepAwake() {
    if ("wakeLock" in navigator) {
      navigator.wakeLock.request("screen").catch(function () {});
    }
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) keepAwake(); });
  keepAwake();

  // ---------- Sound & vibration ----------
  function unlockAudio() {
    if (audio) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audio = new Ctx();
  }
  document.addEventListener("pointerdown", unlockAudio, { once: true });

  function beep(freq, duration, delay) {
    if (!audio) return;
    var start = audio.currentTime + (delay || 0);
    var osc = audio.createOscillator();
    var gain = audio.createGain();
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.25, start);
    gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
    osc.connect(gain).connect(audio.destination);
    osc.start(start);
    osc.stop(start + duration);
  }

  function feedback(result) {
    if (result === "accepted") {
      beep(1046, 0.18);
      if (navigator.vibrate) navigator.vibrate(120);
    } else if (result === "already_used") {
      beep(523, 0.15); beep(523, 0.15, 0.22);
      if (navigator.vibrate) navigator.vibrate([150, 80, 150]);
    } else {
      beep(196, 0.5);
      if (navigator.vibrate) navigator.vibrate(500);
    }
  }

  // ---------- History (this device only) ----------
  function loadHistory() {
    try { return JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "[]"); } catch (e) { return []; }
  }
  function saveHistory(items) {
    try { window.localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 20))); } catch (e) { /* storage blocked */ }
  }
  function renderHistory() {
    historyList.innerHTML = "";
    loadHistory().forEach(function (item) {
      var li = document.createElement("li");
      li.className = "r-" + item.result;
      var text = document.createElement("span");
      text.textContent = item.time + " · " + (item.name || item.code || "—");
      var badge = document.createElement("strong");
      badge.textContent = item.label;
      li.appendChild(text);
      li.appendChild(badge);
      historyList.appendChild(li);
    });
  }

  // ---------- Result screen ----------
  function label(result) { return cfg["l-" + result] || cfg[camel("l-" + result)] || result.toUpperCase(); }
  function camel(s) { return s.replace(/-([a-z_])/g, function (_, c) { return c.toUpperCase(); }); }

  function showResult(data) {
    var ticket = data.ticket || {};
    var result = data.result;
    resultBox.className = "result r-" + result;
    resultBox.querySelector("[data-result-status]").textContent = (result === "accepted" ? "✓ " : "✕ ") + label(result);
    var photo = resultBox.querySelector("[data-result-photo]");
    if (ticket.photo_url && result !== "invalid") {
      photo.src = ticket.photo_url;
      photo.hidden = false;
    } else {
      photo.hidden = true;
      photo.removeAttribute("src");
    }
    resultBox.querySelector("[data-result-name]").textContent = ticket.holder_name || "";
    resultBox.querySelector("[data-result-detail]").textContent =
      [ticket.category, ticket.zone].filter(Boolean).join(" · ");
    var extra = "";
    if (result === "already_used") {
      extra = cfg.lFirstScan + " " + (data.first_scan_at || "") + (data.first_scanned_by ? " — " + cfg.lBy + " " + data.first_scanned_by : "");
    } else if (result === "invalid") {
      extra = cfg.lUnknown;
    } else if (ticket.code) {
      extra = ticket.code;
    }
    resultBox.querySelector("[data-result-extra]").textContent = extra;
    resultBox.hidden = false;
    feedback(result);

    if (typeof data.checked_in === "number") {
      document.querySelector("[data-checked]").textContent = fmt.format(data.checked_in);
      document.querySelector("[data-total]").textContent = fmt.format(data.total);
    }

    var items = loadHistory();
    items.unshift({
      result: result,
      label: label(result),
      code: ticket.code || "",
      name: ticket.holder_name || "",
      time: new Date().toLocaleTimeString("en-GB")
    });
    saveHistory(items);
    renderHistory();

    window.setTimeout(function () {
      resultBox.hidden = true;
      busy = false;
      resume();
    }, RESULT_MS);
  }

  function showError(message) {
    resultBox.className = "result r-invalid";
    resultBox.querySelector("[data-result-status]").textContent = "⚠";
    resultBox.querySelector("[data-result-photo]").hidden = true;
    resultBox.querySelector("[data-result-name]").textContent = message;
    resultBox.querySelector("[data-result-detail]").textContent = "";
    resultBox.querySelector("[data-result-extra]").textContent = "";
    resultBox.hidden = false;
    window.setTimeout(function () { resultBox.hidden = true; busy = false; resume(); }, RESULT_MS);
  }

  // ---------- API ----------
  function submit(value) {
    value = (value || "").trim();
    if (!value || busy) return;
    var now = Date.now();
    if (value === lastValue && now - lastAt < 3000) return; // same QR still in front of the camera
    lastValue = value;
    lastAt = now;
    busy = true;
    pause();

    fetch(cfg.api, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrf, "Accept": "application/json" },
      body: JSON.stringify({ value: value })
    })
      .then(function (response) {
        if (response.status === 403) { window.location.href = "/scan/login/?next=/scan/"; return null; }
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) { if (data) showResult(data); })
      .catch(function () { showError(cfg.lNetwork); });
  }

  // ---------- Camera ----------
  function pause() {
    try { if (scanner && scanner.getState && scanner.getState() === 2) scanner.pause(true); } catch (e) { /* ignore */ }
  }
  function resume() {
    try { if (scanner && scanner.getState && scanner.getState() === 3) scanner.resume(); } catch (e) { /* ignore */ }
  }

  function startCamera() {
    if (!window.Html5Qrcode) {
      statusEl.textContent = cfg.lCameraError;
      startBtn.hidden = false;
      return;
    }
    startBtn.hidden = true;
    var formats = window.Html5QrcodeSupportedFormats ? [window.Html5QrcodeSupportedFormats.QR_CODE] : undefined;
    scanner = scanner || new window.Html5Qrcode("reader", { formatsToSupport: formats, verbose: false });
    scanner.start(
      { facingMode: "environment" },
      {
        fps: 12,
        aspectRatio: 1,
        qrbox: function (w, h) {
          var size = Math.floor(Math.min(w, h) * 0.75);
          return { width: size, height: size };
        }
      },
      function onDecoded(text) { submit(text); },
      function onNoMatch() { /* frame without QR: ignore */ }
    ).then(function () {
      statusEl.textContent = cfg.lScanning;
    }).catch(function () {
      statusEl.textContent = cfg.lCameraError;
      startBtn.hidden = false;
    });
  }

  startBtn.addEventListener("click", function () { unlockAudio(); startCamera(); });

  // ---------- Manual entry ----------
  var manual = document.querySelector("[data-manual-form]");
  manual.addEventListener("submit", function (event) {
    event.preventDefault();
    unlockAudio();
    var input = manual.querySelector("input");
    lastValue = "";
    submit(input.value);
    input.value = "";
    input.blur();
  });

  renderHistory();
  window.addEventListener("load", startCamera);
})();
