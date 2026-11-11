/* Light visual effects: header shadow on scroll and a short fade-in of the
   cards when they scroll into view. No blur. Every page works without it. */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function $$(selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); }

  var header = document.querySelector("[data-header]");
  if (header) {
    var onScroll = function () { header.classList.toggle("is-scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  if (reduceMotion || !("IntersectionObserver" in window)) return;
  var targets = $$(".info-card, .ticket-card, .steps li, .headliner, .faq__item, .ticket-preview");
  targets.forEach(function (el) {
    var index = el.parentElement ? Array.prototype.indexOf.call(el.parentElement.children, el) : 0;
    el.setAttribute("data-reveal", "");
    el.style.setProperty("--d", Math.min(index, 6) * 60 + "ms");
  });
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.1 });
  targets.forEach(function (el) { observer.observe(el); });
})();
