"""The production log formatter.

Worth testing for one reason: it runs in production and nowhere else, so the
first time it is exercised for real is the first time something has already
gone wrong. A formatter that raises while formatting an exception turns one
incident into two, and the second one has no traceback.
"""

import json
import logging

from core.logging import JSONFormatter


def _record(**kwargs):
    """A LogRecord shaped the way `logging` makes them."""
    defaults = {
        "name": "goldfishlab.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello",
        "args": None,
        "exc_info": None,
    }
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)


def test_every_line_is_one_json_object():
    line = JSONFormatter().format(_record())
    assert json.loads(line)["message"] == "hello"
    assert "\n" not in line


def test_the_four_fields_an_aggregator_needs_are_present():
    payload = json.loads(JSONFormatter().format(_record()))
    assert set(payload) >= {"time", "level", "logger", "message"}
    assert payload["level"] == "INFO"
    assert payload["logger"] == "goldfishlab.test"


def test_printf_style_arguments_are_rendered():
    """`logger.info("ran %s games", 10)` must not log the template."""
    record = _record(msg="ran %s games", args=(10,))
    assert json.loads(JSONFormatter().format(record))["message"] == "ran 10 games"


def test_a_traceback_is_one_field_and_not_forty_lines():
    """The entire reason to format as JSON rather than leave the default.

    A traceback spread over forty physical lines is forty unrelated entries to
    anything that reads a log by line, and the one that mattered is the one
    that got split off from its message.
    """
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(exc_info=sys.exc_info(), msg="chunk failed")

    line = JSONFormatter().format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"] == "chunk failed"
    assert "ValueError: boom" in payload["exception"]


def test_extra_fields_survive_and_the_noise_does_not():
    """`extra=` is how a run id reaches the log; `LogRecord` internals are not.

    A formatter that merges the whole `__dict__` buries the four fields that
    matter under thirty attributes of `logging` bookkeeping.
    """
    record = _record()
    record.run_id = "abc-123"
    payload = json.loads(JSONFormatter().format(record))
    assert payload["run_id"] == "abc-123"
    assert "pathname" not in payload
    assert "levelno" not in payload


def test_a_value_json_cannot_serialise_does_not_raise():
    """The worst possible moment to discover a TypeError is mid-incident.

    Somebody will eventually log a UUID or a Decimal in `extra`, and the
    formatter must degrade to `str` rather than take the handler down with it.
    """
    import uuid
    from decimal import Decimal

    record = _record()
    record.run_id = uuid.uuid4()
    record.price = Decimal("1.50")
    payload = json.loads(JSONFormatter().format(record))
    assert payload["price"] == "1.50"
    assert isinstance(payload["run_id"], str)
