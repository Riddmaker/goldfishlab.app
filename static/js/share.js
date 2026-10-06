// "Copy link" and "Copy text" on a shared report (P4). An enhancement only:
// the buttons stay hidden without this file, and the link and the text are
// plain read-only fields to select by hand. A button names the field it
// copies (`data-copy`) and the word to show once it has (`data-copied`).
(() => {
  const copy = async (button) => {
    const field = document.getElementById(button.dataset.copy);
    if (!field) return;
    try {
      await navigator.clipboard.writeText(field.value);
    } catch {
      // No clipboard (an old browser, an insecure origin): select it instead.
      field.select();
      return;
    }
    const label = button.textContent;
    button.textContent = button.dataset.copied;
    setTimeout(() => { button.textContent = label; }, 2000);
  };

  document.addEventListener("DOMContentLoaded", () => {
    if (!navigator.clipboard) return;
    for (const button of document.querySelectorAll("button[data-copy]")) {
      button.hidden = false;
      button.addEventListener("click", () => copy(button));
    }
  });
})();
