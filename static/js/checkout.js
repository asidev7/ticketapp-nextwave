/* Checkout: country → currency, phone prefix & gateways; live total;
   extra ticket holder names; no double submit. */
(function () {
  "use strict";

  var form = document.getElementById("checkout-form");
  if (!form) return;

  var data = form.dataset;
  var fmt = new Intl.NumberFormat("en-US");

  var prices = { BJ: { cur: "XOF", price: +data.priceXof, fee: +data.feeXof }, NG: { cur: "NGN", price: +data.priceNgn, fee: +data.feeNgn } };
  var prefixes = { BJ: data.prefixBj, NG: data.prefixNg };

  var countryInputs = Array.prototype.slice.call(form.querySelectorAll("input[name='country']"));
  var gatewayOptions = Array.prototype.slice.call(form.querySelectorAll("[data-gateway-country]"));
  var quantity = form.querySelector("[data-quantity]");
  var payButton = form.querySelector("[data-pay-button]");
  var noGateway = form.querySelector("[data-no-gateway]");

  function money(amount, currency) { return currency + " " + fmt.format(amount); }
  function country() {
    var checked = form.querySelector("input[name='country']:checked");
    return checked ? checked.value : "BJ";
  }

  function update() {
    var c = country();
    var p = prices[c];
    var qty = parseInt(quantity.value, 10) || 1;
    var total = p.price * qty + p.fee;

    form.querySelector("[data-phone-prefix]").textContent = prefixes[c];
    form.querySelector("[data-sum-qty]").textContent = String(qty);
    form.querySelector("[data-sum-unit]").textContent = money(p.price, p.cur);
    form.querySelector("[data-sum-fees]").textContent = money(p.fee, p.cur);
    form.querySelector("[data-fee-row]").hidden = !p.fee;
    form.querySelector("[data-sum-total]").textContent = money(total, p.cur);
    payButton.textContent = data.labelPay + " " + money(total, p.cur);

    // Only the gateways of the selected country
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
    buildHolders(qty);
  }

  // ---------- Extra holders (tickets 2…N) ----------
  var holdersBox = form.querySelector("[data-extra-holders]");
  function buildHolders(qty) {
    var existing = holdersBox.querySelectorAll(".extra-holder").length;
    for (var i = existing + 2; i <= qty; i += 1) {
      var box = document.createElement("div");
      box.className = "extra-holder";
      box.setAttribute("data-index", String(i));
      box.innerHTML =
        '<p class="extra-holder__title"></p>' +
        '<div class="field"><label></label><input type="text" maxlength="120" autocomplete="off"></div>';
      box.querySelector(".extra-holder__title").textContent = data.labelHolder + " " + i;
      var nameId = "holder_name_" + i;
      var label = box.querySelector("label");
      label.textContent = data.labelName;
      label.htmlFor = nameId;
      var nameInput = box.querySelector("input[type='text']");
      nameInput.name = nameInput.id = nameId;
      holdersBox.appendChild(box);
    }
    Array.prototype.slice.call(holdersBox.querySelectorAll(".extra-holder")).forEach(function (box) {
      var idx = parseInt(box.getAttribute("data-index"), 10);
      var active = idx <= qty;
      box.hidden = !active;
      Array.prototype.slice.call(box.querySelectorAll("input")).forEach(function (input) { input.disabled = !active; });
    });
  }

  countryInputs.forEach(function (input) { input.addEventListener("change", update); });
  quantity.addEventListener("change", update);

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
