/* Home page: countdown, artist modal, lightbox, currency toggle, teaser video. */
(function () {
  "use strict";

  function $(selector, scope) { return (scope || document).querySelector(selector); }
  function $$(selector, scope) { return Array.prototype.slice.call((scope || document).querySelectorAll(selector)); }
  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }

  // ---------- Countdown ----------
  var countdown = $("[data-countdown]");
  if (countdown) {
    var target = new Date(countdown.getAttribute("data-countdown")).getTime();
    var units = {
      days: $("[data-unit='days']", countdown),
      hours: $("[data-unit='hours']", countdown),
      minutes: $("[data-unit='minutes']", countdown),
      seconds: $("[data-unit='seconds']", countdown)
    };
    var done = $("[data-countdown-done]");
    var pad = function (n) { return n < 10 ? "0" + n : String(n); };
    var tick = function () {
      var diff = Math.max(0, target - Date.now());
      if (!diff) {
        countdown.hidden = true;
        if (done) done.hidden = false;
        return false;
      }
      var s = Math.floor(diff / 1000);
      units.days.textContent = String(Math.floor(s / 86400));
      units.hours.textContent = pad(Math.floor((s % 86400) / 3600));
      units.minutes.textContent = pad(Math.floor((s % 3600) / 60));
      units.seconds.textContent = pad(s % 60);
      return true;
    };
    if (!isNaN(target) && tick()) {
      var interval = window.setInterval(function () { if (!tick()) window.clearInterval(interval); }, 1000);
    }
  }

  // ---------- Dialogs ----------
  function openDialog(dialog) {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }
  function closeDialog(dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }
  $$("dialog").forEach(function (dialog) {
    $$("[data-close]", dialog).forEach(function (btn) {
      btn.addEventListener("click", function () { closeDialog(dialog); });
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeDialog(dialog); // click on backdrop
    });
  });

  var artistDialog = $("#artist-dialog");
  $$("[data-artist]").forEach(function (button) {
    button.addEventListener("click", function () {
      var tpl = document.getElementById("artist-" + button.getAttribute("data-artist"));
      if (!tpl || !artistDialog) return;
      var body = $("[data-dialog-body]", artistDialog);
      body.innerHTML = "";
      body.appendChild(tpl.content.cloneNode(true));
      openDialog(artistDialog);
    });
  });

  var lightbox = $("#lightbox");
  $$("[data-lightbox]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
      if (!lightbox) return;
      var img = $("[data-lightbox-img]", lightbox);
      img.src = trigger.getAttribute("data-lightbox");
      img.alt = trigger.getAttribute("data-caption") || "";
      $("[data-lightbox-caption]", lightbox).textContent = trigger.getAttribute("data-caption") || "";
      openDialog(lightbox);
    });
  });

  // ---------- Currency toggle (XOF / NGN) ----------
  var ticketsSection = $("[data-tickets]");
  if (ticketsSection) {
    var buttons = $$("[data-set-currency]", ticketsSection);
    var applyCurrency = function (currency) {
      var button = $("[data-set-currency='" + currency + "']", ticketsSection);
      if (!button) return;
      var country = button.getAttribute("data-country");
      ticketsSection.setAttribute("data-currency", currency);
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", b === button ? "true" : "false"); });
      $$("[data-select]", ticketsSection).forEach(function (link) {
        // A ticket not sold in this currency keeps the other one.
        var sold = +link.getAttribute(currency === "XOF" ? "data-xof" : "data-ngn") > 0;
        var target = sold ? country : (country === "BJ" ? "NG" : "BJ");
        link.href = link.href.replace(/country=[A-Z]{2}/, "country=" + target);
      });
      store("nwp-currency", currency);
    };
    buttons.forEach(function (b) {
      b.addEventListener("click", function () { applyCurrency(b.getAttribute("data-set-currency")); });
    });
    var saved = store("nwp-currency");
    if (saved === "NGN" || saved === "XOF") applyCurrency(saved);
  }

  // ---------- Teaser video (load on click) ----------
  $$("[data-video]").forEach(function (frame) {
    var play = $("[data-play]", frame);
    if (!play) return;
    play.addEventListener("click", function () {
      var embed = frame.getAttribute("data-embed");
      var video = $("video", frame);
      if (embed) {
        var iframe = document.createElement("iframe");
        iframe.src = embed;
        iframe.title = play.getAttribute("aria-label") || "Video";
        iframe.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
        iframe.allowFullscreen = true;
        frame.appendChild(iframe);
      } else if (video) {
        video.play().catch(function () {});
      }
      frame.classList.add("is-playing");
    });
  });
})();
