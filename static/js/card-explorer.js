// The card explorer on the deck page (deck page v2, PR F, decision D3).
//
// 1. Chips: with a mouse a plain click chooses that chip alone and
//    Ctrl/Cmd-click adds it to the others; a second plain click on the only
//    chosen chip clears it. On a touch screen, and with the keyboard, every
//    chip stays a switch. Either way the form's own change event lets htmx
//    swap the grid (decks/detail.html).
// 2. The curve: the library from #explorer-cards (JSON the server wrote, never
//    run) is shuffled 10,000 times with a fixed seed, so the same deck and the
//    same chips always draw the same curve. A hit is a card of each chosen
//    chip ("each") or of any ("any") among the cards seen by a turn: seven,
//    then one more a turn from turn 2. With tutors, a tutor seen stands in for
//    one missing chip. Hovering or focusing a turn shows its share; under it,
//    the turn of the first hit, with mean and standard deviation.
//
// Text goes in with textContent only; nothing from the page is parsed as HTML.
(() => {
  const SHUFFLES = 10000;
  const HAND = 7;
  const TURNS = 8;
  const SEEN = HAND + TURNS - 1;
  const SEED = 0x5eed;
  const TUTOR = "role:tutor";
  const SVG = "http://www.w3.org/2000/svg";
  // The geometry of simulations/charts.py, so both kinds of chart match.
  const WIDTH = 320;
  const HEIGHT = 160;
  const LEFT = 30;
  const RIGHT = 8;
  const TOP = 8;
  const BOTTOM = 22;
  const DEFAULT_TURN = 4;

  const pointer = window.matchMedia("(hover: hover) and (pointer: fine)");

  // A small seeded generator (mulberry32): the same shuffles every time.
  const generator = (seed) => {
    let state = seed >>> 0;
    return () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let value = state;
      value = Math.imul(value ^ (value >>> 15), value | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  };

  const bits = (mask) => {
    let count = 0;
    for (let rest = mask; rest; rest &= rest - 1) count += 1;
    return count;
  };

  // The first turn of a hit in each shuffle, per way of counting: 1..8, or
  // 9 for "not by turn 8". Exported on window for the browser check only.
  const simulate = (cards, chosen) => {
    const masks = [];
    const tutors = [];
    for (const card of cards) {
      let mask = 0;
      chosen.forEach((key, index) => {
        if (card.k.includes(key)) mask |= 1 << index;
      });
      const tutor = mask === 0 && card.k.includes(TUTOR);
      for (let copy = 0; copy < card.q; copy += 1) {
        masks.push(mask);
        tutors.push(tutor);
      }
    }
    const size = masks.length;
    const all = chosen.length >= 31 ? -1 : (1 << chosen.length) - 1;
    const order = new Int32Array(size);
    const first = {each: new Uint8Array(SHUFFLES), any: new Uint8Array(SHUFFLES),
                   eachTutors: new Uint8Array(SHUFFLES), anyTutors: new Uint8Array(SHUFFLES)};
    const random = generator(SEED);
    const seen = Math.min(SEEN, size);
    for (let game = 0; game < SHUFFLES; game += 1) {
      for (let index = 0; index < size; index += 1) order[index] = index;
      let have = 0;
      let found = 0;
      const turn = {each: 9, any: 9, eachTutors: 9, anyTutors: 9};
      for (let position = 0; position < seen; position += 1) {
        const pick = position + Math.floor(random() * (size - position));
        const card = order[pick];
        order[pick] = order[position];
        order[position] = card;
        have |= masks[card];
        if (tutors[card]) found += 1;
        const now = Math.max(1, position - HAND + 2);
        if (turn.each === 9 && (have & all) === all) turn.each = now;
        if (turn.any === 9 && have) turn.any = now;
        if (turn.eachTutors === 9 && bits(all & ~have) <= found) turn.eachTutors = now;
        if (turn.anyTutors === 9 && (have || found)) turn.anyTutors = now;
      }
      for (const way of Object.keys(first)) first[way][game] = turn[way];
    }
    return first;
  };

  // From first turns: the share by each turn, the share first on each turn,
  // and mean ± SD of the first turn over the games that see one by turn 8.
  const summarise = (turns) => {
    const on = new Array(TURNS + 2).fill(0);
    for (const turn of turns) on[turn] += 1;
    const by = [];
    let running = 0;
    for (let turn = 1; turn <= TURNS; turn += 1) {
      running += on[turn];
      by.push(running / turns.length);
    }
    let sum = 0;
    let squares = 0;
    for (let turn = 1; turn <= TURNS; turn += 1) {
      sum += turn * on[turn];
      squares += turn * turn * on[turn];
    }
    const mean = running ? sum / running : 0;
    const sd = running ? Math.sqrt(Math.max(0, squares / running - mean * mean)) : 0;
    return {by, on: on.slice(1).map((count) => count / turns.length), mean, sd,
            share: running / turns.length};
  };

  const element = (name, attributes, parent) => {
    const node = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (parent) parent.appendChild(node);
    return node;
  };

  const fill = (template, values) =>
    template.replace(/\{(\w+)\}/g, (_, key) => (key in values ? values[key] : ""));

  const start = (box) => {
    const source = document.getElementById("explorer-cards");
    const form = document.querySelector("#cards form");
    if (!source || !form) return;
    let cards;
    try {
      cards = JSON.parse(source.textContent);
    } catch (error) {
      return;
    }
    const text = box.dataset;
    const locale = document.documentElement.lang || undefined;
    const percent = new Intl.NumberFormat(locale, {style: "percent", maximumFractionDigits: 0});
    const decimal = new Intl.NumberFormat(locale, {maximumFractionDigits: 1,
                                                   minimumFractionDigits: 1});
    const empty = box.querySelector("[data-explorer-empty]");
    const body = box.querySelector("[data-explorer-body]");
    const chart = box.querySelector("[data-explorer-chart]");
    const spread = box.querySelector("[data-explorer-spread]");
    const detail = box.querySelector("[data-explorer-detail]");
    const tutorsBox = box.querySelector("[data-explorer-tutors]");
    const tutorSwitch = box.querySelector("[data-explorer-tutor-switch]");
    const modes = box.querySelector("[data-explorer-modes]");
    const tutorCount = cards.filter((card) => card.k.includes(TUTOR))
      .reduce((total, card) => total + card.q, 0);
    box.querySelector("[data-explorer-tutor-count]").textContent = `(${tutorCount})`;
    tutorSwitch.hidden = tutorCount === 0;
    chart.setAttribute("aria-label", text.tChart);
    spread.setAttribute("aria-label", text.tSpread);
    box.hidden = false;

    let result = null;
    let lastKey = "";
    let shown = DEFAULT_TURN;

    const x = (turn) => LEFT + ((turn - 1) / (TURNS - 1)) * (WIDTH - LEFT - RIGHT);
    const y = (share) => TOP + (1 - share) * (HEIGHT - TOP - BOTTOM);

    const way = () => (box.querySelector("input[name=explorer-mode]:checked") || {}).value || "each";

    const describe = (turn) => {
      shown = turn;
      const mode = way();
      const main = result[mode];
      const withTutors = tutorsBox.checked && tutorCount ? result[`${mode}Tutors`] : null;
      let line = fill(text.tBy, {turn, share: percent.format(main.by[turn - 1])});
      if (withTutors) line += ` · ${fill(text.tByTutors, {share: percent.format(withTutors.by[turn - 1])})}`;
      const which = withTutors || main;
      line += which.share
        ? ` · ${fill(text.tFirst, {mean: decimal.format(which.mean), sd: decimal.format(which.sd),
                                   share: percent.format(which.share)})}`
        : ` · ${text.tNone}`;
      detail.textContent = line;
      drawSpread(which, turn);
      chart.querySelectorAll(".explorer-column").forEach((column) => {
        column.classList.toggle("is-shown", Number(column.dataset.turn) === turn);
      });
    };

    const drawSpread = (summary, turn) => {
      spread.replaceChildren();
      const highest = Math.max(...summary.on, 0.0001);
      const slot = (WIDTH - LEFT - RIGHT) / (TURNS + 1);
      summary.on.forEach((share, index) => {
        const height = (share / highest) * 34;
        const bar = element("rect", {x: LEFT + index * slot + 2, y: 40 - height,
                                     width: slot - 4, height,
                                     class: `explorer-bar${index + 1 === turn ? " is-shown" : ""}`},
                            spread);
        element("title", {}, bar).textContent =
          `${index < TURNS ? `${text.tTurn} ${index + 1}` : text.tLater}: ${percent.format(share)}`;
        element("text", {x: LEFT + index * slot + slot / 2, y: 54, "text-anchor": "middle",
                         class: "seen-axis"}, spread)
          .textContent = index < TURNS ? String(index + 1) : text.tLater;
      });
    };

    const draw = () => {
      const mode = way();
      chart.replaceChildren();
      [0, 0.25, 0.5, 0.75, 1].forEach((share) => {
        element("line", {x1: LEFT, x2: WIDTH - RIGHT, y1: y(share), y2: y(share),
                         class: "seen-grid"}, chart);
        element("text", {x: LEFT - 4, y: y(share), "text-anchor": "end",
                         "dominant-baseline": "middle", class: "seen-axis", "aria-hidden": "true"},
                chart).textContent = percent.format(share);
      });
      const step = (WIDTH - LEFT - RIGHT) / (TURNS - 1);
      for (let turn = 1; turn <= TURNS; turn += 1) {
        element("text", {x: x(turn), y: HEIGHT - 6, "text-anchor": "middle", class: "seen-axis",
                         "aria-hidden": "true"}, chart).textContent = String(turn);
        const column = element("g", {class: "explorer-column", tabindex: 0, "data-turn": turn,
                                     role: "img",
                                     "aria-label": fill(text.tBy, {turn,
                                       share: percent.format(result[mode].by[turn - 1])})},
                               chart);
        element("rect", {x: x(turn) - step / 2, y: TOP, width: step,
                         height: HEIGHT - TOP - BOTTOM, class: "explorer-hit"}, column);
        column.addEventListener("mouseenter", () => describe(turn));
        column.addEventListener("focus", () => describe(turn));
      }
      // The lines and dots over the columns, so a shown column never pales them.
      const series = [[result[mode], "seen-line seen-line-0"]];
      if (tutorsBox.checked && tutorCount) {
        series.push([result[`${mode}Tutors`], "seen-line seen-line-1 explorer-line-tutors"]);
      }
      for (const [summary, name] of series) {
        element("polyline", {points: summary.by.map((share, index) =>
          `${x(index + 1).toFixed(1)},${y(share).toFixed(1)}`).join(" "), class: name}, chart);
      }
      result[mode].by.forEach((share, index) => {
        element("circle", {cx: x(index + 1), cy: y(share), r: 3, class: "explorer-dot"}, chart);
      });
      describe(shown);
    };

    const update = () => {
      const chosen = [...form.querySelectorAll(".explorer-chip:checked")]
        .map((input) => input.dataset.chip);
      empty.hidden = chosen.length > 0;
      body.hidden = chosen.length === 0;
      modes.hidden = chosen.length < 2;
      if (!chosen.length) return;
      const key = chosen.join("|");
      if (key !== lastKey) {
        const first = simulate(cards, chosen);
        result = Object.fromEntries(Object.entries(first)
          .map(([name, turns]) => [name, summarise(turns)]));
        lastKey = key;
      }
      draw();
    };

    form.addEventListener("change", update);
    box.addEventListener("change", draw);
    chart.addEventListener("mouseleave", () => describe(DEFAULT_TURN));
    update();
    window.goldfishExplorer = {simulate, summarise};
  };

  // A plain click on a chip, with a mouse: that chip alone.
  document.addEventListener("click", (event) => {
    const label = event.target.closest("#cards form label.seen-chip");
    if (!label || !pointer.matches || event.ctrlKey || event.metaKey) return;
    const input = document.getElementById(label.htmlFor);
    if (!input || !input.classList.contains("explorer-chip")) return;
    const chips = [...input.form.querySelectorAll(".explorer-chip")];
    const others = chips.filter((chip) => chip !== input && chip.checked);
    if (!others.length) return;
    event.preventDefault();
    others.forEach((chip) => {
      chip.checked = false;
    });
    input.checked = true;
    input.dispatchEvent(new Event("change", {bubbles: true}));
  });

  const ready = () => document.querySelectorAll("[data-card-explorer]").forEach(start);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
