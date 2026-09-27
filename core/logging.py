"""A JSON log formatter, used in production only.

Hand-written rather than pulled in as a dependency. The whole of it is below;
`python-json-logger` is a fine library and it would be a third package, a
fourth pin and another thing to audit, to produce these twenty lines.

Development keeps the human-readable formatter in `base.py`, because the thing
reading the log there is a person watching `docker compose logs` and a wall of
JSON is strictly worse for them. Production's reader is an aggregator, and for
an aggregator a line it has to regex is strictly worse.
"""

import json
import logging


class JSONFormatter(logging.Formatter):
    """One JSON object per line.

    The exception goes in under its own key rather than being appended to the
    message, which is the entire reason to do this: a traceback spread over
    forty physical lines is forty unrelated log entries to anything that reads
    by line, and the one entry that mattered is the one that got split.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything a caller passed as `extra=`. Skipped rather than merged
        # blindly: `LogRecord` carries thirty-odd attributes of its own and
        # dumping all of them would bury the four fields above.
        for key, value in getattr(record, "__dict__", {}).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        # `default=str` because a caller will eventually log a UUID, a Decimal
        # or a datetime in `extra`, and a logger that raises while formatting
        # an error is the worst possible time to find out.
        return json.dumps(payload, default=str)


#: Everything `logging` puts on a record itself. Anything else is ours.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}
