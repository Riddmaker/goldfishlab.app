# Shared by the native run and the page: the same configs, the same digest.
import hashlib
import json
import time

from simulation import analysis, serial

CONFIGS = [("normal", 500, 10, False), ("stress", 200, 10, True)]


def bench(deck_data, configs=CONFIGS):
    deck = serial.load_deck(deck_data)
    out = []
    for name, games, turns, stress in configs:
        started = time.perf_counter()
        result = analysis.run_chunk(games, run_seed=1234, first_block=0, turns=turns,
                                    deck=deck, stress=stress)
        seconds = time.perf_counter() - started
        digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16]
        out.append({"name": name, "games": games, "ms": round(seconds / games * 1000, 2),
                    "digest": digest})
    return out
