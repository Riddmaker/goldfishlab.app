"""The one and only Mistral client (phase 10 H).

Nothing outside this module talks to Mistral, for the reason `combos/spellbook.py`
gives: one choke point is where the timeout, the retry and the size limit are
kept, and the only place the key is read.

The request is Mistral's chat completions endpoint in JSON mode, as its API
reference describes it (checked 2026-10-02): `POST /v1/chat/completions` with
`model`, `messages`, `temperature`, `max_tokens` and
`response_format: {"type": "json_object"}`; the answer is
`choices[0].message.content`, and `usage` counts `prompt_tokens` and
`completion_tokens`. A reasoning model (Mistral Large 4, checked 2026-10-21 in
its reasoning guide) answers `content` as a list of chunks, `thinking` ones and
`text` ones; the text chunks are the answer. Greedy sampling (temperature 0)
needs `top_p` 1 there, so it is sent with it. `reasoning_effort` ("none" or
"high", checked 2026-10-21 in the adjustable reasoning guide) is sent when
`MISTRAL_REASONING_EFFORT` names one: Large 4 reasons by default, and its
thinking took the whole answer budget. JSON mode makes the answer
parse; what it says is checked by `simulations.summary.parse`, never trusted.

**The key is never logged, printed or put in an exception.** An error says what
went wrong with the request, not what was in its headers.

Why `urllib` and not Mistral's SDK: one verb, no new dependency in a 128 MiB
container, and the same shape as the Spellbook client beside it.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings

ENDPOINT = "https://api.mistral.ai/v1/chat/completions"

#: One attempt's socket timeout. The call runs in a Celery task, not a web
#: request, so it may take longer than Spellbook's. Two attempts and the
#: pause between them must end well inside the summary task's soft time limit
#: (`tasks.SUMMARY_SOFT_TIME_LIMIT`): when they did not, a slow answer left the
#: summary "being written" for good (P1). Large 4 without reasoning took 39 s
#: at its slowest on 21 decks (2026-10-21), hence twice that.
TIMEOUT_SECONDS = 75

#: One retry, on the statuses that mean "try again later", after this pause.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_WAIT = 3.0

#: A summary is a few hundred words. Anything far past this is not one.
MAX_RESPONSE_BYTES = 256 * 1024


class MistralError(RuntimeError):
    """Mistral could not be reached, or answered unusably."""


@dataclass(frozen=True)
class Completion:
    """What came back: the model's text and what it cost in tokens."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


def is_configured() -> bool:
    return bool(getattr(settings, "MISTRAL_API_KEY", ""))


def model() -> str:
    return settings.MISTRAL_MODEL


def reasoning_effort() -> str:
    """The configured reasoning effort; blank sends none, the model's default."""
    return getattr(settings, "MISTRAL_REASONING_EFFORT", "")


def complete(messages: list[dict], *, max_tokens: int, temperature: float = 0.3,
             model_name: str | None = None, effort: str | None = None) -> Completion:
    """One chat completion in JSON mode.

    Raises:
        MistralError: not configured, unreachable after one retry, or an
            answer without the fields the API reference promises.
    """
    if not is_configured():
        raise MistralError("Mistral is not configured")
    effort = reasoning_effort() if effort is None else effort
    body = json.dumps({
        "model": model_name or model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **({"top_p": 1} if temperature == 0 else {}),
        **({"reasoning_effort": effort} if effort else {}),
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    for attempt in (1, 2):
        request = urllib.request.Request(  # noqa: S310 - a fixed https URL
            ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt == 1:
                time.sleep(RETRY_WAIT)
                continue
            raise MistralError(f"Mistral answered HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 1:
                time.sleep(RETRY_WAIT)
                continue
            raise MistralError(f"Mistral could not be reached ({type(exc).__name__})") from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise MistralError("Mistral's answer was larger than a summary can be")
    try:
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage") or {}
        return Completion(
            content=_text(content),
            model=str(payload.get("model") or model_name or model()),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
    except (ValueError, KeyError, IndexError, TypeError):
        raise MistralError("Mistral's answer did not have the expected shape") from None


def _text(content) -> str:
    """The answer of `content`: the string itself, or a reasoning model's
    ``text`` chunks joined, its ``thinking`` chunks left out."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(chunk.get("text", "") for chunk in content
                       if isinstance(chunk, dict) and chunk.get("type") == "text")
    return ""


MODELS_ENDPOINT = "https://api.mistral.ai/v1/models"


def available_models() -> set[str]:
    """Every model id and alias the key may use (`GET /v1/models`: a list
    object whose ``data`` entries carry ``id`` and ``aliases``), so a
    comparison names models that exist rather than guessed ids.

    Raises:
        MistralError: not configured, unreachable, or an unexpected answer.
    """
    if not is_configured():
        raise MistralError("Mistral is not configured")
    request = urllib.request.Request(  # noqa: S310 - a fixed https URL
        MODELS_ENDPOINT, method="GET",
        headers={"Accept": "application/json",
                 "Authorization": f"Bearer {settings.MISTRAL_API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.loads(response.read(MAX_RESPONSE_BYTES * 8))
        found = set()
        for entry in payload["data"]:
            found.add(str(entry["id"]))
            found.update(str(alias) for alias in entry.get("aliases") or ())
        return found
    except urllib.error.HTTPError as exc:
        raise MistralError(f"Mistral answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise MistralError("Mistral could not be reached") from None
    except (ValueError, KeyError, TypeError):
        raise MistralError("Mistral's model list did not have the expected shape") from None


__all__ = ["Completion", "MistralError", "available_models", "complete", "is_configured",
           "model"]
