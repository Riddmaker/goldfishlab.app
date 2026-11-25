// What holds the deck together (B-02): light a card, its lines and its
// neighbours in the picture decks/_graph.html draws; everything else fades.
//
// * Mouse: hovering lights a card, a click keeps it lit until the next click.
// * Touch: a tap lights a card, a tap beside the cards clears it.
// * Keyboard: the picture is one tab stop (the commander, else the first
//   card); the arrow keys walk around the circle, Escape clears.
//
// The picture works without this file: the names and lines are drawn on the
// server. Only classes and tabindex change here; nothing is parsed as HTML.
(() => {
  const setUp = (svg) => {
    const nodes = [...svg.querySelectorAll('[data-node]')];
    const edges = [...svg.querySelectorAll('.graph-edge')];
    if (!nodes.length) return;
    const box = svg.viewBox.baseVal;
    const cx = box.width / 2;
    const cy = box.height / 2;
    const angle = (node) => {
      const dot = node.querySelector('circle');
      const x = dot.cx.baseVal.value - cx;
      const y = dot.cy.baseVal.value - cy;
      // The middle (the commander) first, then clockwise from the top.
      if (Math.hypot(x, y) < 1 || node.classList.contains('graph-node-commander')) return -10;
      return (Math.atan2(y, x) + Math.PI * 2.5) % (Math.PI * 2);
    };
    const ring = [...nodes].sort((a, b) => angle(a) - angle(b));
    let kept = null;

    const light = (node) => {
      const key = node.dataset.node;
      const near = new Set((node.dataset.near || '').split(' ').filter(Boolean));
      near.add(key);
      svg.classList.add('is-lit');
      nodes.forEach((one) => one.classList.toggle('is-near', near.has(one.dataset.node)));
      edges.forEach((edge) => edge.classList.toggle(
        'is-near', edge.dataset.a === key || edge.dataset.b === key));
    };
    const clear = () => {
      svg.classList.remove('is-lit');
      nodes.forEach((one) => one.classList.remove('is-near'));
      edges.forEach((edge) => edge.classList.remove('is-near'));
    };
    const rest = () => (kept ? light(kept) : clear());

    ring.forEach((node, i) => { node.tabIndex = i === 0 ? 0 : -1; });

    nodes.forEach((node) => {
      node.addEventListener('pointerenter', (event) => {
        if (event.pointerType === 'mouse') light(node);
      });
      node.addEventListener('pointerleave', (event) => {
        if (event.pointerType === 'mouse') rest();
      });
      node.addEventListener('click', (event) => {
        event.stopPropagation();
        kept = kept === node ? null : node;
        rest();
      });
      node.addEventListener('focus', () => light(node));
      node.addEventListener('blur', () => rest());
      node.addEventListener('keydown', (event) => {
        const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
        if (event.key === 'Escape') {
          kept = null;
          clear();
          return;
        }
        if (!step) return;
        event.preventDefault();
        const next = ring[(ring.indexOf(node) + step + ring.length) % ring.length];
        node.tabIndex = -1;
        next.tabIndex = 0;
        next.focus();
      });
    });
    svg.addEventListener('click', () => {
      kept = null;
      clear();
    });
  };

  document.querySelectorAll('[data-graph-svg]').forEach(setUp);
})();
