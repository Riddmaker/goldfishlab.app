// The deck in your hand (B-11, PR 3): the cards the page holds as on a table.
// Without this file the page shuffles and cuts on the server, from the seed in
// its address (decks/in_hand.py). With it the browser takes the same order
// (`#in-hand-cards`) and keeps it: pick the deck up and turn it (the bottom card
// shows), cut and look, put back or shuffle in, deal piles and stack them, draw,
// and put a card back on top or at the bottom. Rebuild puts every card back
// in the deck list's order. Nothing is sent anywhere; reloading starts again.
//
// The rules are pure and run under Node (tests/test_deck_hand.py).
(() => {
  "use strict";

  const PILES_MIN = 2;
  const PILES_MAX = 10;
  // One arrow key, and one pixel of a drag, turn the deck this far.
  const TURN_STEP = 30;
  const DRAG_DEG_PER_PX = 0.8;
  // A press that moves less than this is a tap, not a turn.
  const SLOP_PX = 6;

  // A copy of `cards` in random order (Fisher-Yates); `random` gives [0, 1).
  function shuffle(cards, random) {
    const order = cards.slice();
    for (let i = order.length - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
    return order;
  }

  // The top `n` cards and the rest; n is held to 0..cards.length.
  function cut(cards, n) {
    const take = Math.max(0, Math.min(Math.floor(n) || 0, cards.length));
    return { top: cards.slice(0, take), rest: cards.slice(take) };
  }

  // Dealt from the top, one card to each pile in turn, each on top of its
  // pile, as at a table: piles[k][0] is the top of pile k.
  function deal(cards, count) {
    const piles = Array.from({ length: Math.max(1, count) }, () => []);
    cards.forEach((card, i) => piles[i % piles.length].unshift(card));
    return piles;
  }

  // The piles stacked again: the first pile on top of the second, and so on.
  function gather(piles) {
    return piles.flat();
  }

  // `card` back into `cards`, on the top or at the bottom.
  function place(cards, card, where) {
    return where === "bottom" ? [...cards, card] : [card, ...cards];
  }

  // Every card in the deck list's order: by name, copies together.
  function rebuild(cards) {
    return cards.slice().sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  }

  // Which side of the deck faces the viewer at this turn: "back" is the top
  // card's sleeve, "front" the bottom card's face.
  function facing(angle) {
    const turned = ((angle % 360) + 360) % 360;
    return turned > 90 && turned < 270 ? "front" : "back";
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { shuffle, cut, deal, gather, place, rebuild, facing,
                       PILES_MIN, PILES_MAX };
    return;
  }

  // A fair [0, 1) from the browser's own randomness.
  function random() {
    const value = new Uint32Array(1);
    crypto.getRandomValues(value);
    return value[0] / 2 ** 32;
  }

  function start(table) {
    const page = table.closest("main") || document;
    const data = document.getElementById("in-hand-cards");
    const js = page.querySelector("[data-in-hand-js]");
    if (!data || !js) return;
    const text = js.dataset;
    const cards = JSON.parse(data.textContent).map((card, id) => ({ ...card, id }));

    const library = table.querySelector(".in-hand-library");
    const stack = table.querySelector(".in-hand-stack");
    const count = table.querySelector(".in-hand-count");
    const bottom = document.createElement("img");
    bottom.className = "in-hand-bottom";
    bottom.alt = "";
    bottom.hidden = true;
    stack.append(bottom);
    const zones = {};
    table.querySelectorAll("[data-in-hand-zone]").forEach((zone) => {
      zones[zone.dataset.inHandZone] = zone;
    });
    const picked = table.querySelector("[data-in-hand-picked]");
    const pickedName = picked.querySelector("[data-in-hand-picked-name]");
    const said = page.querySelector("[data-in-hand-said]");
    const back = page.querySelector("[data-in-hand-back]");
    const cutForm = page.querySelector("[data-in-hand-cut]");
    const pilesInput = js.querySelector("[data-in-hand-piles]");

    // The server's face-up cards are the first `revealed` of its order.
    const serverRevealed = table.querySelector("[data-in-hand-revealed]");
    const shown = serverRevealed ? serverRevealed.children.length : 0;
    if (serverRevealed) serverRevealed.closest(".in-hand-zone").remove();
    const state = {
      library: cards.slice(shown), top: cards.slice(0, shown), hand: [], piles: null,
      picked: null, lifted: false, angle: 0,
    };

    function tile(card, where) {
      const figure = document.createElement("figure");
      figure.className = "card-tile";
      figure.tabIndex = 0;
      figure.setAttribute("role", "button");
      figure.setAttribute("aria-pressed", String(state.picked === card));
      figure.dataset.card = card.id;
      figure.dataset.where = where;
      if (state.picked === card) figure.classList.add("in-hand-is-picked");
      let face;
      if (card.image) {
        face = document.createElement("img");
        face.src = card.image;
        face.alt = card.name;
        face.width = 488;
        face.height = 680;
        face.decoding = "async";
      } else {
        face = document.createElement("span");
        face.textContent = card.name;
      }
      face.classList.add("card-tile-img");
      if (!card.image) face.classList.add("card-tile-text");
      figure.append(face);
      const li = document.createElement("li");
      li.append(figure);
      return li;
    }

    function fill(zone, list, where) {
      const line = zone.querySelector("ol");
      line.replaceChildren(...list.map((card) => tile(card, where)));
      zone.hidden = list.length === 0;
    }

    function render() {
      const left = state.library.length;
      library.style.setProperty("--left", left);
      count.textContent = left;
      stack.setAttribute("aria-valuenow", String(((state.angle % 360) + 360) % 360));
      stack.setAttribute("aria-valuetext", text.leftLabel.replace("%s", left));
      stack.style.setProperty("--turn", `${state.angle}deg`);
      stack.classList.toggle("in-hand-lifted", state.lifted);
      const front = facing(state.angle) === "front" && left > 0;
      stack.classList.toggle("in-hand-turned", front);
      bottom.hidden = !front;
      if (front) {
        const last = state.library[left - 1];
        bottom.src = last.image || "";
        bottom.alt = last.name;
        count.textContent = last.image ? "" : last.name;
      }
      js.querySelector('[data-act="lift"]').setAttribute("aria-pressed", String(state.lifted));
      fill(zones.top, state.top, "top");
      fill(zones.hand, state.hand, "hand");
      const piles = zones.piles.querySelector("[data-in-hand-pile-row]");
      piles.replaceChildren(...(state.piles || []).map((pile) => {
        const one = document.createElement("li");
        one.className = "in-hand-pile";
        one.style.setProperty("--left", pile.length);
        one.textContent = pile.length;
        return one;
      }));
      zones.piles.hidden = !state.piles;
      js.querySelector('[data-act="gather"]').hidden = !state.piles;
      js.querySelector('[data-act="deal"]').hidden = Boolean(state.piles);
      js.querySelector('[data-act="shuffle-in"]').hidden = state.top.length === 0;
      if (back) back.hidden = state.top.length === 0;
      picked.hidden = !state.picked;
      if (state.picked) {
        pickedName.textContent = state.picked.name;
        picked.querySelector('[data-act="draw"]').hidden = where(state.picked) === "hand";
      }
    }

    function where(card) {
      return state.hand.includes(card) ? "hand" : "top";
    }

    function say(words) {
      said.textContent = `${words} ${text.leftLabel.replace("%s", state.library.length)}`;
    }

    // Every card out of the hand, the face-up row and the piles, onto the
    // library (piles stacked first); used before a shuffle and a rebuild.
    function collect() {
      if (state.piles) state.library = gather(state.piles).concat(state.library);
      state.library = state.top.concat(state.library, state.hand);
      state.top = [];
      state.hand = [];
      state.piles = null;
      state.picked = null;
    }

    function take(card) {
      state.top = state.top.filter((other) => other !== card);
      state.hand = state.hand.filter((other) => other !== card);
    }

    const acts = {
      shuffle() {
        collect();
        state.library = shuffle(state.library, random);
      },
      cut() {
        const n = Number(cutForm.querySelector('[name="cut"]').value);
        state.library = state.top.concat(state.library);
        const split = cut(state.library, n);
        state.top = split.top;
        state.library = split.rest;
        state.picked = null;
      },
      back() {
        state.library = state.top.concat(state.library);
        state.top = [];
        state.picked = null;
      },
      "shuffle-in"() {
        state.library = shuffle(state.top.concat(state.library), random);
        state.top = [];
        state.picked = null;
      },
      "draw-top"() {
        const split = cut(state.library, 1);
        state.hand = state.hand.concat(split.top);
        state.library = split.rest;
      },
      deal() {
        const count = Math.min(PILES_MAX, Math.max(PILES_MIN, Number(pilesInput.value) || 7));
        if (!state.piles) state.piles = deal(state.library, count);
        state.library = [];
      },
      gather() {
        state.library = gather(state.piles || []).concat(state.library);
        state.piles = null;
      },
      lift() {
        state.lifted = !state.lifted;
      },
      turn() {
        state.angle += 180;
      },
      rebuild() {
        collect();
        state.library = rebuild(state.library);
        state.angle = 0;
      },
      draw() {
        take(state.picked);
        state.hand = state.hand.concat([state.picked]);
        state.picked = null;
      },
      top() {
        take(state.picked);
        state.library = place(state.library, state.picked, "top");
        state.picked = null;
      },
      bottom() {
        take(state.picked);
        state.library = place(state.library, state.picked, "bottom");
        state.picked = null;
      },
    };

    function act(name, label) {
      acts[name]();
      render();
      say(label);
    }

    page.addEventListener("click", (event) => {
      const button = event.target.closest("[data-act]");
      if (button && page.contains(button)) {
        event.preventDefault();
        act(button.dataset.act, button.textContent.trim());
        return;
      }
      if (event.target.closest("[data-in-hand-shuffle]")) {
        event.preventDefault();
        act("shuffle", event.target.closest("a").textContent.trim());
      } else if (event.target.closest("[data-in-hand-back]")) {
        event.preventDefault();
        act("back", back.textContent.trim());
      }
      const figure = event.target.closest("figure[data-card]");
      if (figure) {
        const card = cards[Number(figure.dataset.card)];
        state.picked = state.picked === card ? null : card;
        render();
        const again = table.querySelector(`figure[data-card="${card.id}"]`);
        if (again) again.focus();
        if (state.picked) said.textContent = card.name;
      }
    });

    page.addEventListener("keydown", (event) => {
      const figure = event.target.closest && event.target.closest("figure[data-card]");
      if (figure && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        figure.click();
      } else if (event.key === "Escape" && state.picked) {
        state.picked = null;
        render();
      }
    });

    cutForm.addEventListener("submit", (event) => {
      event.preventDefault();
      act("cut", cutForm.querySelector("button").textContent.trim());
    });

    // Turning the deck: arrow keys on it, or a drag across it.
    stack.tabIndex = 0;
    stack.setAttribute("role", "slider");
    stack.setAttribute("aria-label", text.turnLabel);
    stack.setAttribute("aria-valuemin", "0");
    stack.setAttribute("aria-valuemax", "359");
    stack.addEventListener("keydown", (event) => {
      const step = { ArrowRight: TURN_STEP, ArrowUp: TURN_STEP,
                     ArrowLeft: -TURN_STEP, ArrowDown: -TURN_STEP }[event.key];
      if (!step) return;
      event.preventDefault();
      state.angle += step;
      render();
    });
    let drag = null;
    stack.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, angle: state.angle, moved: false };
      stack.setPointerCapture(event.pointerId);
    });
    stack.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const dx = event.clientX - drag.x;
      if (Math.abs(dx) > SLOP_PX) drag.moved = true;
      if (!drag.moved) return;
      stack.classList.add("in-hand-dragging");
      state.angle = drag.angle + dx * DRAG_DEG_PER_PX;
      render();
    });
    const drop = () => {
      if (!drag) return;
      stack.classList.remove("in-hand-dragging");
      if (!drag.moved) act("lift", js.querySelector('[data-act="lift"]').textContent.trim());
      drag = null;
    };
    stack.addEventListener("pointerup", drop);
    stack.addEventListener("pointercancel", drop);

    js.hidden = false;
    const hint = page.querySelector("[data-in-hand-js-hint]");
    if (hint) hint.hidden = false;
    table.classList.add("in-hand-live");
    render();
  }

  const ready = () => document.querySelectorAll("[data-in-hand]").forEach(start);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
