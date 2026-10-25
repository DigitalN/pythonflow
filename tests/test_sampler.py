"""Sampling rate and QoS: fast while the dashboard page is polling, slow and on the
efficiency cores otherwise (no thread is started)."""

import main
from main import (BACKGROUND_INTERVAL, DASHBOARD_IDLE_AFTER, DASHBOARD_INTERVAL, QOS_CLASS_BACKGROUND,
                  QOS_CLASS_UTILITY, Sampler)


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


def test_qos_follows_dashboard_polls(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    sampler = Sampler(reader=None, on_sample=None)
    assert sampler.qos() == QOS_CLASS_BACKGROUND
    sampler.dashboard_polled()
    assert sampler.qos() == QOS_CLASS_UTILITY
    now[0] += DASHBOARD_IDLE_AFTER
    assert sampler.qos() == QOS_CLASS_BACKGROUND


def test_late_wakeups_dont_stretch_the_interval(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    sampler = Sampler(reader=None, on_sample=None)
    due = now[0]
    for _ in range(10):
        due = sampler.next_due(due)
        now[0] = due + 0.09  # each wait ends 90 ms late, as at background QoS
    assert due == 1000.0 + 10 * BACKGROUND_INTERVAL

    now[0] += 60  # far behind (a slow sample): no burst of catch-up samples
    assert sampler.next_due(due) == now[0] + 0.05
