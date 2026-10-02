"""The one and only Mistral client (phase 10 H).

Nothing outside this module talks to Mistral, for the reason `combos/spellbook.py`
gives: one choke point is where the timeout, the retry and the size limit are
kept, and the only place the key is read.

The request is Mistral's chat completions endpoint in JSON mode, as its API
reference describes it (checked 2026-10-02): `POST /v1/chat/completions` with
`model`, `messages`, `temperature`, `max_tokens` and
`response_format: {"type": "json_object"}`; the answer is
`choices[0].message.content`, and `usage` counts `prompt_tokens` and
`completion_tokens`. JSON mode makes the answer parse; what it says is checked
by `simulations.summary.parse`, never trusted.

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
#: request, so it may take longer than Spellbook's; a summary that has not
#: come back in a minute is not coming.
TIMEOUT_SECONDS = 60

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


def complete(messages: list[dict], *, max_tokens: int, temperature: float = 0.3,
             model_name: str | None = None) -> Completion:
    """One chat completion in JSON mode.

    Raises:
        MistralError: not configured, unreachable after one retry, or an
            answer without the fields the API reference promises.
    """
    if not is_configured():
        raise MistralError("Mistral is not configured")
    body = json.dumps({
        "model": model_name or model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
            content=content if isinstance(content, str) else "",
            model=str(payload.get("model") or model_name or model()),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
    except (ValueError, KeyError, IndexError, TypeError):
        raise MistralError("Mistral's answer did not have the expected shape") from None


__all__ = ["Completion", "MistralError", "complete", "is_configured", "model"]
