// The commander choice (P20 F2): the second field offers only the cards that
// may share the command zone with the first one chosen, and hides itself when
// none may. An enhancement only - without it every second is listed and the
// server refuses a pair that may not lead together.
document.addEventListener("DOMContentLoaded", () => {
  for (const form of document.querySelectorAll("[data-commander-form]")) {
    const lead = form.querySelector("[data-commander-lead]");
    const second = form.querySelector("[data-commander-second]");
    if (!lead || !second) continue;
    const field = second.closest("[data-commander-second-field]") || second;
    const update = () => {
      let any = false;
      for (const option of second.options) {
        if (!option.value) continue;
        const pairs = (option.dataset.leads || "").split(" ").includes(lead.value);
        option.hidden = !pairs;
        option.disabled = !pairs;
        any = any || pairs;
        if (!pairs && option.selected) second.value = "";
      }
      field.hidden = !any;
    };
    lead.addEventListener("change", update);
    update();
  }
});
