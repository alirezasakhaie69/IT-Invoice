document.body.addEventListener("htmx:configRequest", (event) => {
  const token = document.body.dataset.csrf || "";
  if (token) event.detail.headers["X-CSRF-Token"] = token;
});

const timers = new WeakMap();

async function reformat(el) {
  const previous = el.value;
  const body = new URLSearchParams({ value: previous });
  const response = await fetch("/format/number", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-CSRF-Token": document.body.dataset.csrf || "",
    },
    body,
  });
  if (!response.ok || el.value !== previous) return;
  const text = await response.text();
  el.value = text;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

document.addEventListener("input", (event) => {
  const el = event.target;
  if (!(el instanceof HTMLInputElement) || !el.classList.contains("grouped")) return;
  clearTimeout(timers.get(el));
  timers.set(el, setTimeout(() => reformat(el), 200));
});

document.addEventListener("focusout", (event) => {
  const el = event.target;
  if (el instanceof HTMLInputElement && el.classList.contains("grouped")) reformat(el);
});

function pickDate(button) {
  const field = button.closest(".date-field");
  field.querySelector(".date-iso").value = button.dataset.iso;
  field.querySelector(".date-display").value = button.dataset.label;
  const pop = field.querySelector(".calendar");
  if (pop) pop.innerHTML = "";
}

function clearDate(button) {
  const field = button.closest(".date-field");
  field.querySelector(".date-iso").value = "";
  field.querySelector(".date-display").value = "";
  const pop = field.querySelector(".calendar");
  if (pop) pop.innerHTML = "";
}

function setupCustomerCombos(root) {
  root.querySelectorAll("[data-customer-combo]").forEach((combo) => {
    if (combo.dataset.ready) return;
    combo.dataset.ready = "1";
    const search = combo.querySelector(".combo-search");
    const hidden = combo.querySelector('input[name="customer_id"]');
    const list = combo.querySelector(".combo-list");
    const items = [...list.querySelectorAll(".combo-item")];

    function visibleItems() {
      return items.filter((item) => !item.hidden);
    }

    function render(query) {
      const q = query.trim().toLowerCase();
      items.forEach((item) => {
        const name = (item.dataset.name || "").toLowerCase();
        const phone = (item.dataset.phone || "").toLowerCase();
        item.hidden = Boolean(q) && !name.includes(q) && !phone.includes(q);
        item.classList.remove("active");
      });
      const shown = visibleItems();
      list.hidden = shown.length === 0;
      if (shown[0]) shown[0].classList.add("active");
    }

    function choose(item) {
      hidden.value = item.dataset.id;
      search.value = item.dataset.name;
      list.hidden = true;
    }

    search.addEventListener("focus", () => render(search.value));
    search.addEventListener("input", () => {
      hidden.value = "";
      render(search.value);
    });
    search.addEventListener("keydown", (event) => {
      const shown = visibleItems();
      const current = shown.findIndex((item) => item.classList.contains("active"));
      if (event.key === "Escape") {
        list.hidden = true;
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!shown.length) return;
        list.hidden = false;
        const step = event.key === "ArrowDown" ? 1 : -1;
        const next = shown[(current + step + shown.length) % shown.length];
        shown.forEach((item) => item.classList.remove("active"));
        next.classList.add("active");
        next.scrollIntoView({ block: "nearest" });
      }
      if (event.key === "Enter") {
        const active = shown.find((item) => item.classList.contains("active"));
        if (active && !list.hidden) {
          event.preventDefault();
          choose(active);
        }
      }
    });
    search.addEventListener("blur", () => {
      const typed = search.value.trim();
      const exact = items.filter((item) => (item.dataset.name || "").trim() === typed);
      if (exact.length === 1) hidden.value = exact[0].dataset.id;
      else if (!typed) hidden.value = "";
    });
    list.addEventListener("mousedown", (event) => {
      const item = event.target.closest(".combo-item");
      if (!item) return;
      event.preventDefault();
      choose(item);
    });
    document.addEventListener("click", (event) => {
      if (!combo.contains(event.target)) list.hidden = true;
    });
  });
}

document.addEventListener("DOMContentLoaded", () => setupCustomerCombos(document));

const persianDigits = "۰۱۲۳۴۵۶۷۸۹";
const arabicDigits = "٠١٢٣٤٥٦٧٨٩";

function plainInt(value) {
  let text = String(value ?? "").trim();
  for (let i = 0; i < 10; i += 1) {
    text = text.replaceAll(persianDigits[i], String(i)).replaceAll(arabicDigits[i], String(i));
  }
  text = text.replace(/[^\d-]/g, "");
  if (!text || text === "-") return null;
  const number = Number(text);
  return Number.isInteger(number) ? number : null;
}

function roundestAdjustedPrice(unitPrice, percent) {
  if (unitPrice === 0) return { price: 0, applied: 0, step: 0 };
  const exact = unitPrice * (1 + percent / 100);
  const low = percent - 2;
  const high = percent + 2;
  const minTarget = Math.min(unitPrice * (1 + low / 100), unitPrice * (1 + high / 100));
  const maxTarget = Math.max(unitPrice * (1 + low / 100), unitPrice * (1 + high / 100));
  const steps = [1000, 500, 100, 50, 10, 5, 1].filter((step) => step <= Math.abs(unitPrice) || step === 1);
  for (const step of steps) {
    const start = Math.ceil((Math.ceil(minTarget - 1e-9)) / step) * step;
    const end = Math.floor(maxTarget + 1e-9);
    let best = null;
    let bestDistance = Infinity;
    for (let candidate = start; candidate <= end; candidate += step) {
      if (candidate < 0) continue;
      const applied = (candidate / unitPrice - 1) * 100;
      if (applied < low - 1e-6 || applied > high + 1e-6) continue;
      const distance = Math.abs(applied - percent);
      if (
        best === null ||
        distance < bestDistance - 1e-9 ||
        (Math.abs(distance - bestDistance) < 1e-9 && Math.abs(candidate - exact) < Math.abs(best - exact))
      ) {
        best = candidate;
        bestDistance = distance;
      }
    }
    if (best !== null) return { price: best, applied: (best / unitPrice - 1) * 100, step };
  }
  const rounded = Math.max(0, Math.round(exact));
  return { price: rounded, applied: (rounded / unitPrice - 1) * 100, step: 1 };
}

let lastPriceSnapshot = null;

function lineRows() {
  return [...document.querySelectorAll("#lines .line-row")];
}

function refreshLineNumbers() {
  lineRows().forEach((row, index) => {
    const number = row.querySelector(".line-no");
    if (number) number.textContent = String(index + 1);
  });
}

function moveRow(row, toIndex) {
  const rows = lineRows();
  const from = rows.indexOf(row);
  if (from < 0) return;
  const target = Math.max(0, Math.min(rows.length - 1, toIndex));
  const parent = row.parentElement;
  if (target === from) return;
  const sibling = rows[target];
  if (target < from) parent.insertBefore(row, sibling);
  else parent.insertBefore(row, sibling.nextSibling);
  refreshLineNumbers();
  document.querySelector("#invoice-form")?.dispatchEvent(new Event("input", { bubbles: true }));
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("#lines [data-move], #lines [data-jump], #lines-all, #lines-none, #adjust-apply, #adjust-undo");
  if (!button) return;
  if (button.id === "lines-all" || button.id === "lines-none") {
    document.querySelectorAll("#lines .line-check").forEach((box) => {
      box.checked = button.id === "lines-all";
    });
    return;
  }
  if (button.id === "adjust-undo") {
    if (!lastPriceSnapshot) return;
    lineRows().forEach((row) => {
      const price = row.querySelector('[name="unit_price"]');
      const key = price?.dataset.undoKey;
      if (price && key && Object.prototype.hasOwnProperty.call(lastPriceSnapshot, key)) {
        price.value = lastPriceSnapshot[key];
        price.dispatchEvent(new Event("input", { bubbles: true }));
        price.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const note = row.querySelector(".applied-note");
      if (note) note.textContent = "";
    });
    lastPriceSnapshot = null;
    button.disabled = true;
    return;
  }
  if (button.id === "adjust-apply") {
    const raw = document.querySelector("#adjust-percent")?.value.trim() ?? "";
    const msg = document.querySelector("#adjust-msg");
    const normalized = raw.replace(",", ".");
    if (!/^[+-]?\d+(\.\d)?$/.test(normalized)) {
      if (msg) msg.textContent = "Enter one number with at most one decimal.";
      return;
    }
    const percent = Number(normalized);
    const checked = lineRows().filter((row) => row.querySelector(".line-check")?.checked);
    if (!checked.length) {
      if (msg) msg.textContent = "Tick at least one row.";
      return;
    }
    const snapshot = {};
    checked.forEach((row, index) => {
      const price = row.querySelector('[name="unit_price"]');
      const current = plainInt(price.value);
      const note = row.querySelector(".applied-note");
      if (current === null || current < 0) {
        if (note) note.textContent = "Unit price is not a whole number.";
        return;
      }
      price.dataset.undoKey = String(index);
      snapshot[String(index)] = price.value;
      const result = roundestAdjustedPrice(current, percent);
      price.value = String(result.price);
      price.dispatchEvent(new Event("input", { bubbles: true }));
      price.dispatchEvent(new Event("change", { bubbles: true }));
      if (note) note.textContent = `Applied ${result.applied.toFixed(2)}% (${result.price})`;
    });
    lastPriceSnapshot = snapshot;
    const undo = document.querySelector("#adjust-undo");
    if (undo) undo.disabled = false;
    if (msg) msg.textContent = "";
    return;
  }
  const row = button.closest(".line-row");
  if (button.dataset.move) {
    const rows = lineRows();
    moveRow(row, rows.indexOf(row) + Number(button.dataset.move));
  }
  if (button.hasAttribute("data-jump")) {
    const wanted = plainInt(row.querySelector(".line-jump")?.value);
    if (wanted === null || wanted < 1) return;
    moveRow(row, wanted - 1);
  }
});

document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target?.id === "lines") refreshLineNumbers();
});
document.addEventListener("DOMContentLoaded", refreshLineNumbers);
refreshLineNumbers();
