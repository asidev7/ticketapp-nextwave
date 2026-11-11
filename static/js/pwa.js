/* Scanner as an installable app (PWA): registers the service worker and shows
   "Install" (Android, desktop Chrome/Edge) or the iPhone steps (Safari). */
(function () {
  "use strict";

  var script = document.currentScript;
  if ("serviceWorker" in navigator && script) {
    navigator.serviceWorker.register(script.getAttribute("data-sw"), { scope: "/scan/" }).catch(function () {});
  }

  var box = document.querySelector("[data-install]");
  if (!box) return;
  var standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  if (standalone) return; // already installed

  var button = box.querySelector("[data-install-button]");
  var deferred = null;
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferred = event;
    box.hidden = false;
    button.hidden = false;
  });
  button.addEventListener("click", function () {
    if (!deferred) return;
    deferred.prompt();
    deferred.userChoice.then(function () { deferred = null; box.hidden = true; });
  });
  window.addEventListener("appinstalled", function () { box.hidden = true; });

  var ua = window.navigator.userAgent;
  var iOS = /iPhone|iPad|iPod/.test(ua) || (ua.indexOf("Macintosh") > -1 && "ontouchend" in document);
  if (iOS) {
    box.hidden = false;
    box.querySelector("[data-install-ios]").hidden = false;
  }
})();
