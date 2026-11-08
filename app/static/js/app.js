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
