// Keys on the playtest board (Paket 4, PR 18): D draws, N deals a new hand,
// T starts the next turn, Ctrl+Z (Cmd+Z on a Mac) takes a move back. Each key
// clicks the board's own button that carries it in `data-key`, so a key does
// exactly what the click does - the same form, the same htmx swap - and a
// board without that button (a strict game has no New hand) ignores it.
//
// Never while typing: a key pressed in a field, a select or anything
// editable is the field's. A disabled button is not clicked.
//
// keyFor is the rule, kept pure so tests/test_playtest_free.py runs it under Node.
(() => {
  "use strict";

  const TYPING = new Set(["INPUT", "SELECT", "TEXTAREA"]);

  // {key, ctrlKey, metaKey, altKey, shiftKey, tag, editable} -> "d" | "ctrl+z" | null
  function keyFor(press) {
    if (TYPING.has(press.tag) || press.editable || press.altKey) return null;
    const key = String(press.key || "").toLowerCase();
    if (press.ctrlKey || press.metaKey) {
      return key === "z" && !press.shiftKey ? "ctrl+z" : null;
    }
    if (press.shiftKey) return null;
    return ["d", "n", "t"].includes(key) ? key : null;
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { keyFor };
    return;
  }

  document.addEventListener("keydown", (event) => {
    if (event.repeat || event.defaultPrevented) return;
    const target = event.target;
    const name = keyFor({
      key: event.key,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      shiftKey: event.shiftKey,
      tag: target && target.tagName,
      editable: Boolean(target && target.isContentEditable),
    });
    if (!name) return;
    const button = document.querySelector(`#board [data-key="${name}"]`);
    if (!button || button.disabled) return;
    event.preventDefault();
    button.click();
  });
})();
