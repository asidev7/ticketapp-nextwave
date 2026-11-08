/* Mobile hamburger menu. Without JS the navigation stays visible as a scrollable row. */
(function () {
  "use strict";

  var toggle = document.querySelector("[data-menu-toggle]");
  var menu = document.getElementById("site-nav");
  if (!toggle || !menu) return;

  function isOpen() { return menu.classList.contains("is-open"); }

  function setOpen(open) {
    menu.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? toggle.getAttribute("data-label-close") : toggle.getAttribute("data-label-open"));
  }

  toggle.addEventListener("click", function () { setOpen(!isOpen()); });

  // Close after choosing a section, on Escape, or when tapping outside.
  menu.addEventListener("click", function (event) {
    if (event.target.closest("a")) setOpen(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && isOpen()) {
      setOpen(false);
      toggle.focus();
    }
  });
  document.addEventListener("click", function (event) {
    if (isOpen() && !menu.contains(event.target) && !toggle.contains(event.target)) setOpen(false);
  });

  // Back to the desktop layout: reset the mobile state.
  if (window.matchMedia) {
    var desktop = window.matchMedia("(min-width: 900px)");
    var reset = function (query) { if (query.matches) setOpen(false); };
    if (desktop.addEventListener) desktop.addEventListener("change", reset);
    else if (desktop.addListener) desktop.addListener(reset);
  }
})();
