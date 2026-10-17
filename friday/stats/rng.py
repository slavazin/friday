"""Counter-based randomness: every draw is a pure function of (seed, purpose, keys).

Adding a draw in one place never shifts the draws anywhere else, so an exposure coin for
(round 5, category c) is the same no matter what else the run drew before it.
"""

from __future__ import annotations

import hashlib
import random


def derive(seed: int, *keys: object) -> random.Random:
    """Returns a Random seeded from `seed` and `keys`; same inputs give the same stream."""
    material = "\x1f".join([str(seed), *map(str, keys)]).encode()
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))


def coin(seed: int, *keys: object) -> int:
    """A fair coin in {0, 1} for `keys`."""
    return derive(seed, "coin", *keys).randrange(2)


def bernoulli(p: float, seed: int, *keys: object) -> bool:
    """True with probability `p` for `keys`."""
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p out of [0, 1]: {p}")
    return derive(seed, "bernoulli", *keys).random() < p
