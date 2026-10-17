"""A crash inside the round close loses none of the round's observations to pooling (review finding, s11)."""

import pytest

from friday.app.round_close import RoundClose
from tests.unit.test_runner import World, plan_reply


def _pooled(w):
    return sorted((o.id, o.pool_id is not None) for o in w.stores.observations.observations())


def test_a_crash_in_pooling_then_a_restart_pools_every_observation_of_the_round(tmp_path, monkeypatch):
    fresh = World(tmp_path / "fresh", coordinator_replies=[plan_reply()])
    fresh.runner().run(stop_after=1)
    want = _pooled(fresh)
    assert want and all(pooled for _, pooled in want)

    w = World(tmp_path / "crash", coordinator_replies=[plan_reply()])
    real = RoundClose._pool

    def boom(self, kept, new_obs):
        raise RuntimeError("pooling call died")

    monkeypatch.setattr(RoundClose, "_pool", boom)
    with pytest.raises(RuntimeError):
        w.runner().run(stop_after=1)
    monkeypatch.setattr(RoundClose, "_pool", real)
    w.runner(coordinator=False).run(stop_after=1)
    assert _pooled(w) == want
