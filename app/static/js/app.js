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
