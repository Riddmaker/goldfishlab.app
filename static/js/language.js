// The language switch in the footer: picking a language switches at once.
// An enhancement only: without this file the form keeps its "Change" button,
// which this file hides.
(() => {
  "use strict";

  const form = document.querySelector("[data-language-form]");
  const select = form && form.querySelector("select");
  const button = form && form.querySelector("[data-language-submit]");
  if (!select || !button) return;

  button.hidden = true;
  select.addEventListener("change", () => form.requestSubmit());
})();
