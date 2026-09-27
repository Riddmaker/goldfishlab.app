"""The boundary between the Django application and the simulation engine.

`adapter.py` is the ONLY module in the codebase that imports from both
`simulation/` and Django. `runner.py` sits beside it and imports the engine
only - no Django - which is what lets the Celery tasks drive a simulation
without widening that rule. Everything else stays on one side:

* `simulation/` never imports Django - it is a plain Python package that can be
  run, profiled and reasoned about without a database.
* `cards/` and `decks/` never import `simulation` - they describe cards and
  decks, not how they play.

If a change makes it tempting to widen this, the boundary has leaked. Fix the
boundary rather than the symptom: the four vendored engine test files are
byte-identical to their originals precisely because that separation held.
"""
