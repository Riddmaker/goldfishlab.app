// The card shop (B-16): a small point-and-click game. The rooms, their spots
// and what happens there are data from core/shop.py, handed over with
// json_script; this file only plays them.
//
// A spot is a button twice: over the picture for the mouse and the finger
// (out of the tab order, hidden from screen readers), and in the list under
// it for the keyboard and screen readers. Either one acts the same.
//
// What is carried and what happened lives in the tab's sessionStorage, so a
// reload keeps the game and nothing leaves the browser. Without this file
// the page shows the shop front and says the game needs JavaScript.
//
// allows, act and visible are the rules, kept pure so tests/test_shop.py can
// check them under Node without a browser.
(() => {
  "use strict";

  const KEY = "goldfishlab-shop";

  function fresh(game) {
    return { room: game.start, items: [], flags: [], ended: false };
  }

  // Does `state` meet `condition` (all of has, lacks, flag, unflag that it names)?
  function allows(state, condition) {
    if (!condition) return true;
    if (condition.has && !state.items.includes(condition.has)) return false;
    if (condition.lacks && state.items.includes(condition.lacks)) return false;
    if (condition.flag && !state.flags.includes(condition.flag)) return false;
    if (condition.unflag && state.flags.includes(condition.unflag)) return false;
    return true;
  }

  // What happens when `spot` is used in `state`: the first step whose
  // condition holds. Returns the new state and the line to show; `state` is
  // left as it was.
  function act(state, spot) {
    const step = spot.steps.find((candidate) => allows(state, candidate.if));
    if (!step) return { state, say: "" };
    let items = state.items.filter((item) => item !== step.take);
    if (step.give && !items.includes(step.give)) items = items.concat(step.give);
    const flags = step.set && !state.flags.includes(step.set) ? state.flags.concat(step.set) : state.flags;
    return {
      state: { room: step.go || state.room, items, flags, ended: state.ended || Boolean(step.end) },
      say: step.say,
    };
  }

  function visible(state, prop) {
    return allows(state, prop.if);
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { fresh, allows, act, visible };
  }
  if (typeof document === "undefined") return;

  function load(game) {
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(KEY));
      if (saved && game.rooms[saved.room] && Array.isArray(saved.items) && Array.isArray(saved.flags)) {
        return saved;
      }
    } catch {
      // Storage off or unreadable: start afresh.
    }
    return fresh(game);
  }

  function save(state) {
    try {
      window.sessionStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      // Storage off: the game still plays, it just forgets on reload.
    }
  }

  function percent(value, whole) {
    return `${(value / whole) * 100}%`;
  }

  function start() {
    const root = document.querySelector("[data-shop]");
    const data = document.getElementById("shop-game");
    if (!root || !data) return;
    const game = JSON.parse(data.textContent);
    const stage = root.querySelector("[data-shop-stage]");
    const picture = stage.querySelector("img");
    const list = root.querySelector("[data-shop-spots]");
    const line = root.querySelector("[data-shop-line]");
    const carried = root.querySelector("[data-shop-items]");
    const nothing = root.querySelector("[data-shop-nothing]");
    const ending = root.querySelector("[data-shop-end]");
    const again = root.querySelector("[data-shop-again]");
    let state = load(game);

    function place(element, box) {
      element.style.left = percent(box.x, game.width);
      element.style.top = percent(box.y, game.height);
      element.style.width = percent(box.w, game.width);
      element.style.height = percent(box.h, game.height);
    }

    function use(spot) {
      const result = act(state, spot);
      state = result.state;
      save(state);
      draw();
      line.textContent = result.say;
    }

    function draw() {
      const room = game.rooms[state.room];
      picture.src = room.picture;
      picture.alt = room.name;
      stage.querySelectorAll("[data-shop-prop], [data-shop-hotspot]").forEach((old) => old.remove());
      list.replaceChildren();
      for (const prop of room.props || []) {
        if (!visible(state, prop)) continue;
        const image = document.createElement("img");
        image.src = prop.picture;
        image.alt = "";
        image.dataset.shopProp = prop.id;
        image.className = "shop-prop";
        place(image, prop);
        stage.append(image);
      }
      for (const spot of room.spots) {
        const hotspot = document.createElement("button");
        hotspot.type = "button";
        hotspot.className = "shop-hotspot";
        hotspot.dataset.shopHotspot = spot.id;
        hotspot.tabIndex = -1;
        hotspot.setAttribute("aria-hidden", "true");
        hotspot.title = spot.name;
        place(hotspot, spot);
        hotspot.addEventListener("click", () => use(spot));
        stage.append(hotspot);

        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "shop-spot";
        button.dataset.shopSpot = spot.id;
        button.textContent = spot.name;
        button.addEventListener("click", () => use(spot));
        item.append(button);
        list.append(item);
      }
      carried.replaceChildren(...state.items.map((id) => {
        const item = document.createElement("li");
        item.textContent = game.items[id];
        return item;
      }));
      nothing.hidden = state.items.length > 0;
      ending.hidden = !state.ended;
    }

    again.addEventListener("click", () => {
      state = fresh(game);
      save(state);
      draw();
      line.textContent = line.dataset.intro;
      list.querySelector("button")?.focus();
    });

    root.querySelector("[data-shop-nojs]").hidden = true;
    root.querySelector("[data-shop-game]").hidden = false;
    draw();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
