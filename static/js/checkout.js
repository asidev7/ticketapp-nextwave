/* Checkout: currency → phone prefix & gateways; quantity stepper; live total;
   optional names for the other tickets; no double submit. */
(function () {
  "use strict";

  var form = document.getElementById("checkout-form");
  if (!form) return;

  var data = form.dataset;
  var fmt = new Intl.NumberFormat("en-US");
  var prices = { BJ: { cur: "XOF", price: +data.priceXof, fee: +data.feeXof }, NG: { cur: "NGN", price: +data.priceNgn, fee: +data.feeNgn } };
  var prefixes = { BJ: data.prefixBj, NG: data.prefixNg };
  var max = parseInt(data.max, 10) || 1;

  var countryInputs = Array.prototype.slice.call(form.querySelectorAll("input[name='country']"));
  var gatewayOptions = Array.prototype.slice.call(form.querySelectorAll("[data-gateway-country]"));
  var quantity = form.querySelector("[data-quantity]");
  var payButton = form.querySelector("[data-pay-button]");
  var noGateway = form.querySelector("[data-no-gateway]");

  function money(amount, currency) { return currency === "NGN" ? "₦" + fmt.format(amount) : fmt.format(amount) + " CFA"; }
  function country() {
    var checked = form.querySelector("input[name='country']:checked");
    return checked ? checked.value : (countryInputs[0] ? countryInputs[0].value : "BJ");
  }
  function qty() {
    var n = parseInt(quantity.value, 10);
    return isNaN(n) ? 1 : Math.min(Math.max(n, 1), max);
  }

  function update() {
    var c = country();
    var p = prices[c];
    var n = qty();
    var total = p.price * n + p.fee;

    form.querySelector("[data-phone-prefix]").textContent = prefixes[c];
    form.querySelector("[data-sum-total]").textContent = money(total, p.cur);
    form.querySelector("[data-sum-detail]").textContent =
      n + " × " + money(p.price, p.cur) + (p.fee ? " + " + money(p.fee, p.cur) : "");
    payButton.textContent = data.labelPay + " " + money(total, p.cur);

    // Only the gateways of the selected currency
    var visible = 0;
    gatewayOptions.forEach(function (option) {
      var show = option.getAttribute("data-gateway-country") === c;
      var input = option.querySelector("input");
      option.hidden = !show;
      input.disabled = !show;
      if (!show) input.checked = false;
      if (show) visible += 1;
    });
    var firstVisible = gatewayOptions.filter(function (o) { return !o.hidden; })[0];
    if (firstVisible && !form.querySelector("input[name='gateway']:checked")) {
      firstVisible.querySelector("input").checked = true;
    }
    noGateway.hidden = visible > 0;
    payButton.disabled = visible === 0;

    form.querySelectorAll("[data-step]").forEach(function (button) {
      var step = parseInt(button.getAttribute("data-step"), 10);
      button.disabled = (step < 0 && n <= 1) || (step > 0 && n >= max);
    });
    buildHolders(n);
  }

  // ---------- Names for tickets 2…N (optional) ----------
  var wrap = form.querySelector("[data-extra-wrap]");
  var holdersBox = form.querySelector("[data-extra-holders]");
  function buildHolders(n) {
    var existing = holdersBox.querySelectorAll(".extra-holder").length;
    for (var i = existing + 2; i <= n; i += 1) {
      var box = document.createElement("div");
      box.className = "field extra-holder";
      box.setAttribute("data-index", String(i));
      box.innerHTML = '<label></label><input type="text" maxlength="120" autocomplete="off">';
      var id = "holder_name_" + i;
      var label = box.querySelector("label");
      label.textContent = data.labelHolder + " " + i;
      label.htmlFor = id;
      var input = box.querySelector("input");
      input.name = input.id = id;
      holdersBox.appendChild(box);
    }
    Array.prototype.slice.call(holdersBox.querySelectorAll(".extra-holder")).forEach(function (box) {
      var active = parseInt(box.getAttribute("data-index"), 10) <= n;
      box.hidden = !active;
      box.querySelector("input").disabled = !active;
    });
    wrap.hidden = n < 2;
  }

  form.querySelectorAll("[data-step]").forEach(function (button) {
    button.addEventListener("click", function () {
      quantity.value = String(Math.min(Math.max(qty() + parseInt(button.getAttribute("data-step"), 10), 1), max));
      update();
    });
  });
  quantity.addEventListener("input", update);
  quantity.addEventListener("change", function () { quantity.value = String(qty()); update(); });
  countryInputs.forEach(function (input) { input.addEventListener("change", update); });

  // ---------- Submit once ----------
  form.addEventListener("submit", function (event) {
    if (form.dataset.submitting) { event.preventDefault(); return; }
    if (typeof form.checkValidity === "function" && !form.checkValidity()) return;
    form.dataset.submitting = "1";
    payButton.disabled = true;
    payButton.textContent = data.labelProcessing;
  });
  window.addEventListener("pageshow", function () {
    delete form.dataset.submitting;
    update();
  });

  update();
})();
