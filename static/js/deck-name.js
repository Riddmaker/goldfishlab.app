// Renaming a deck in place (E1.73, E2.5): the pencil beside its name opens a
// field there, on the deck page and in the list. On the deck page a single
// click on the heading opens it too, with the caret where the click was -
// but a double click or a drag still selects text, as anywhere else, so the
// click waits out the double-click time and gives way to a selection.
// Enter or Save saves, Escape or Cancel does not; leaving the field leaves it
// open, as any form - only those two say what was meant, in every browser.
// An enhancement only: without this file the pencil is a link to a page
// with the same form (decks/rename.html).
(() => {
  // A double click's second click comes within this; long enough for most
  // system settings without making the single click feel slow.
  const DOUBLE_CLICK_MS = 300;

  // The caret position in `node`'s text at a point, or null.
  const offsetAt = (node, x, y) => {
    if (document.caretPositionFromPoint) {
      const position = document.caretPositionFromPoint(x, y);
      if (position && node.contains(position.offsetNode)) return position.offset;
    } else if (document.caretRangeFromPoint) {
      const range = document.caretRangeFromPoint(x, y);
      if (range && node.contains(range.startContainer)) return range.startOffset;
    }
    return null;
  };

  const enhance = (box) => {
    const text = box.querySelector("[data-deck-name-text]");
    const pencil = box.querySelector("[data-deck-name-edit]");
    const form = box.querySelector("[data-deck-name-form]");
    const field = form.querySelector("input[name=name]");
    const status = box.querySelector("[data-deck-name-status]");
    let saving = false;
    // The deck page keeps its <h1> for a screen reader while the field is
    // open - a page without one is a page without a title (axe). The list's
    // link goes, or the keyboard would stop on it unseen.
    const away = (gone) => {
      if (text.tagName === "H1") text.classList.toggle("sr-only", gone);
      else text.hidden = gone;
    };

    const say = (words) => {
      status.textContent = words;
      if (words) setTimeout(() => { if (status.textContent === words) status.textContent = ""; }, 4000);
    };

    const open = (caret) => {
      field.value = text.textContent;
      away(true);
      pencil.hidden = true;
      form.hidden = false;
      box.dataset.editing = "";
      say("");
      field.focus();
      if (caret === null) field.select();
      else field.setSelectionRange(caret, caret);
    };

    const close = () => {
      // Back to the pencil, unless the focus has gone somewhere else already.
      const inside = form.contains(document.activeElement);
      form.hidden = true;
      away(false);
      pencil.hidden = false;
      delete box.dataset.editing;
      if (inside) pencil.focus({ preventScroll: true });
    };

    const save = async () => {
      const name = field.value.trim();
      if (saving) return;
      if (!name || name === text.textContent) { close(); return; }
      saving = true;
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const answer = await response.json();
        if (!response.ok) { say(answer.error || box.dataset.failed); field.focus(); return; }
        const old = text.textContent;
        text.textContent = answer.name;
        if (box.hasAttribute("data-click-edits") && document.title.startsWith(old)) {
          document.title = answer.name + document.title.slice(old.length);
        }
        close();
        say(box.dataset.saved);
      } catch {
        say(box.dataset.failed);
        field.focus();
      } finally {
        saving = false;
      }
    };

    pencil.addEventListener("click", (event) => {
      event.preventDefault();
      open(null);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      save();
    });
    form.querySelector("[data-deck-name-cancel]").addEventListener("click", close);
    field.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
    });

    if (!box.hasAttribute("data-click-edits")) return;
    let waiting = null;
    text.style.cursor = "text";
    text.addEventListener("click", (event) => {
      clearTimeout(waiting);
      if (event.detail > 1) return; // a double or triple click selects
      const { clientX: x, clientY: y } = event;
      waiting = setTimeout(() => {
        if (!document.getSelection().isCollapsed) return; // a drag selected text
        open(offsetAt(text, x, y));
      }, DOUBLE_CLICK_MS);
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    for (const box of document.querySelectorAll("[data-deck-name]")) enhance(box);
  });
})();
