/* Visual effects: glass header on scroll, blur-in scroll reveals, animated hero title, countdown tick.
   Purely decorative — every page works without this file. */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function $$(selector, scope) { return Array.prototype.slice.call((scope || document).querySelectorAll(selector)); }

  // ---------- Header shadow once the page scrolls ----------
  var header = document.querySelector("[data-header]");
  if (header) {
    var onScroll = function () { header.classList.toggle("is-scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ---------- Hero title: one span per letter, last word in the accent colour ----------
  $$("[data-split]").forEach(function (title) {
    var text = title.textContent.trim();
    var words = text.split(/\s+/);
    var index = 0;
    title.setAttribute("aria-label", text);
    title.textContent = "";
    words.forEach(function (word, w) {
      var wordEl = document.createElement("span");
      wordEl.className = "word" + (words.length > 1 && w === words.length - 1 ? " word--accent" : "");
      wordEl.setAttribute("aria-hidden", "true");
      Array.prototype.forEach.call(word, function (letter) {
        var charEl = document.createElement("span");
        charEl.className = "char";
        charEl.style.setProperty("--i", String(index));
        charEl.textContent = letter;
        wordEl.appendChild(charEl);
        index += 1;
      });
      title.appendChild(wordEl);
      if (w < words.length - 1) title.appendChild(document.createTextNode(" "));
    });
    if (!reduceMotion) {
      window.setTimeout(function () { title.classList.add("is-in"); }, index * 45 + 1000);
    }
  });

  // ---------- Countdown: small drop animation when a digit changes ----------
  if (!reduceMotion && window.MutationObserver) {
    $$("[data-countdown] [data-unit]").forEach(function (unit) {
      new MutationObserver(function () {
        unit.classList.remove("tick");
        void unit.offsetWidth; // restart the animation
        unit.classList.add("tick");
      }).observe(unit, { childList: true, characterData: true, subtree: true });
    });
  }

  // ---------- Blur-in reveal on scroll ----------
  var groups = [
    ".section-title", ".section-head .currency-toggle", ".tabs",
    ".key-info__item", ".artist-card", ".ticket-card", ".hall-map",
    ".media-grid > *", ".venue-grid > *", ".about-grid > *", ".outlet", ".faq__item",
    ".checkout-summary", ".step", ".result", ".ticket-preview"
  ];
  var targets = [];
  groups.forEach(function (selector) {
    $$(selector).forEach(function (el) {
      if (el.hasAttribute("data-reveal")) return;
      var siblings = el.parentElement ? Array.prototype.indexOf.call(el.parentElement.children, el) : 0;
      el.setAttribute("data-reveal", "");
      el.style.setProperty("--d", Math.min(siblings, 8) * 70 + "ms");
      targets.push(el);
    });
  });

  function settle(el) {
    // Once revealed, hand the element back to its own hover transitions.
    window.setTimeout(function () {
      el.removeAttribute("data-reveal");
      el.style.removeProperty("--d");
    }, 1400);
  }

  if (reduceMotion || !("IntersectionObserver" in window)) {
    targets.forEach(function (el) { el.removeAttribute("data-reveal"); });
    return;
  }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
      settle(entry.target);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  targets.forEach(function (el) { observer.observe(el); });
})();
