/* Order page: poll the status every 3 s while pending; auto-download tickets once paid. */
(function () {
  "use strict";

  // ---------- Pending: poll /api/order/<ref>/status/ ----------
  var pending = document.querySelector("[data-poll]");
  if (pending) {
    var url = pending.getAttribute("data-poll");
    var started = Date.now();
    var MAX_MS = 20 * 60 * 1000;
    var poll = function () {
      if (Date.now() - started > MAX_MS) return;
      fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (data && data.status && data.status !== "pending") {
            window.location.reload();
            return;
          }
          window.setTimeout(poll, 3000);
        })
        .catch(function () { window.setTimeout(poll, 5000); });
    };
    window.setTimeout(poll, 3000);
  }

  // ---------- Paid: download the ticket automatically (once) ----------
  var auto = document.getElementById("auto-download");
  if (!auto) return;

  var key = "nwp-dl-" + auto.getAttribute("data-ref");
  var already = false;
  try { already = window.sessionStorage.getItem(key) === "1"; } catch (e) { already = false; }

  var iosOverlay = document.querySelector("[data-ios-save]");
  var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function showIOSOverlay() {
    if (!iosOverlay) return;
    iosOverlay.querySelector("[data-ios-image]").src = auto.getAttribute("data-image");
    iosOverlay.hidden = false;
    iosOverlay.querySelector("[data-ios-close]").focus();
  }
  if (iosOverlay) {
    iosOverlay.querySelector("[data-ios-close]").addEventListener("click", function () { iosOverlay.hidden = true; });
  }

  if (already) return;
  try { window.sessionStorage.setItem(key, "1"); } catch (e) { /* private mode */ }

  window.setTimeout(function () {
    if (isIOS) {
      // iOS Safari ignores <a download> for images: show it full screen instead.
      showIOSOverlay();
      return;
    }
    var link = document.createElement("a");
    link.href = auto.getAttribute("data-url");
    link.setAttribute("download", "");
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }, 800);
})();
