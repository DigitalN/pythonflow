"""Sampling rate: fast while the dashboard page is polling, slow otherwise (no thread is started)."""

import main
from main import BACKGROUND_INTERVAL, DASHBOARD_IDLE_AFTER, DASHBOARD_INTERVAL, Sampler


def test_rate_follows_dashboard_polls(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    sampler = Sampler(reader=None, on_sample=None)
    assert sampler.interval() == BACKGROUND_INTERVAL

    sampler.dashboard_polled()
    assert sampler.interval() == DASHBOARD_INTERVAL
    assert sampler._wake.is_set()  # woken so the dashboard gets a fresh sample straight away

    sampler._wake.clear()
    now[0] += DASHBOARD_INTERVAL
    sampler.dashboard_polled()
    assert not sampler._wake.is_set()  # already fast: a poll doesn't trigger extra samples

    now[0] += DASHBOARD_IDLE_AFTER
    assert sampler.interval() == BACKGROUND_INTERVAL
