/* Hero carousel: autoplay 5 s, arrows, dots, swipe, pause on hover/focus. */
(function () {
  "use strict";

  var root = document.querySelector("[data-carousel]");
  if (!root) return;
  var slides = Array.prototype.slice.call(root.querySelectorAll(".hero__slide"));
  if (slides.length < 2) return;

  var dots = Array.prototype.slice.call(root.querySelectorAll("[data-dot]"));
  var caption = root.querySelector("[data-hero-caption]");
  var DELAY = 5000;
  var index = 0;
  var timer = null;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function show(target) {
    index = (target + slides.length) % slides.length;
    slides.forEach(function (slide, i) {
      var active = i === index;
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", active ? "false" : "true");
      if (active) {
        var img = slide.querySelector("img[loading='lazy']");
        if (img) img.loading = "eager";
      }
    });
    dots.forEach(function (dot, i) {
      dot.setAttribute("aria-selected", i === index ? "true" : "false");
    });
    if (caption) {
      var title = slides[index].getAttribute("data-title") || "";
      var subtitle = slides[index].getAttribute("data-subtitle") || "";
      caption.textContent = title + (title && subtitle ? " — " : "") + subtitle;
    }
  }

  function stop() {
    if (timer) window.clearInterval(timer);
    timer = null;
  }

  function start() {
    if (reduceMotion) return;
    stop();
    timer = window.setInterval(function () { show(index + 1); }, DELAY);
  }

  var prev = root.querySelector("[data-prev]");
  var next = root.querySelector("[data-next]");
  if (prev) prev.addEventListener("click", function () { show(index - 1); start(); });
  if (next) next.addEventListener("click", function () { show(index + 1); start(); });
  dots.forEach(function (dot) {
    dot.addEventListener("click", function () {
      show(parseInt(dot.getAttribute("data-dot"), 10));
      start();
    });
  });

  root.addEventListener("mouseenter", stop);
  root.addEventListener("mouseleave", start);
  root.addEventListener("focusin", stop);
  root.addEventListener("focusout", start);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else start();
  });

  // Swipe on touch screens
  var startX = null;
  var startY = null;
  root.addEventListener("touchstart", function (event) {
    startX = event.touches[0].clientX;
    startY = event.touches[0].clientY;
  }, { passive: true });
  root.addEventListener("touchend", function (event) {
    if (startX === null) return;
    var dx = event.changedTouches[0].clientX - startX;
    var dy = event.changedTouches[0].clientY - startY;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      show(index + (dx < 0 ? 1 : -1));
      start();
    }
    startX = startY = null;
  }, { passive: true });

  root.addEventListener("keydown", function (event) {
    if (event.key === "ArrowLeft") { show(index - 1); }
    if (event.key === "ArrowRight") { show(index + 1); }
  });

  start();
})();
