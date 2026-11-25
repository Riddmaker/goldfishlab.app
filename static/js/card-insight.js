// "More about this card" in a dialog (B-22, PR 3): a button with
// data-insight-for="<card name>" opens [data-insight-dialog] and loads the
// card's fragment from #card-insight-urls into it with htmx. The button sits
// beside the card, never inside it, so a click on the card still plays it.
(() => {
  "use strict";

  // Without this, CSS keeps the ⓘ hidden: a button that does nothing is worse
  // than none, and the card page has the same answer.
  document.documentElement.dataset.insightReady = "";

  function urls() {
    const data = document.getElementById("card-insight-urls");
    try {
      return data ? JSON.parse(data.textContent) : {};
    } catch (error) {
      return {};
    }
  }

  function open(name) {
    const dialog = document.querySelector("[data-insight-dialog]");
    const url = urls()[name];
    if (!dialog || !url || !window.htmx) return;
    const body = dialog.querySelector("[data-insight-body]");
    dialog.querySelector("[data-insight-title]").textContent = name;
    body.textContent = dialog.dataset.loading;
    if (!dialog.open) dialog.showModal();
    window.htmx.ajax("GET", url, { target: body, swap: "innerHTML" }).catch(() => {
      body.textContent = dialog.dataset.failed;
    });
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest && event.target.closest("[data-insight-for]");
    if (button) {
      event.preventDefault();
      open(button.dataset.insightFor);
      return;
    }
    // A click on the backdrop (the dialog itself, outside its content) closes it.
    const dialog = event.target.closest && event.target.closest("[data-insight-dialog]");
    if (dialog && event.target === dialog) {
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right
        && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) dialog.close();
    }
  });

  // An answer that failed (htmx swaps no 4xx/5xx) says so instead of loading forever.
  document.addEventListener("htmx:responseError", (event) => {
    const body = event.detail.target;
    const dialog = body && body.closest && body.closest("[data-insight-dialog]");
    if (dialog && body.matches("[data-insight-body]")) body.textContent = dialog.dataset.failed;
  });
})();
