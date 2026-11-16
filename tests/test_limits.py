import pytest

from mapagent.limits import Limited, Limiter


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_per_client_limit_rolls_over_after_an_hour():
    clock = Clock()
    limiter = Limiter(per_client=2, per_day=100, clock=clock)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(Limited) as refused:
        limiter.check("a")
    assert 0 < refused.value.retry_after <= 3600
    limiter.check("b")  # other visitors are unaffected
    clock.now += 3601
    limiter.check("a")


def test_daily_limit_covers_all_clients_and_resets_at_utc_midnight():
    clock = Clock(1_800_000_000.0 - 1_800_000_000.0 % 86400 + 3600)  # 01:00 UTC
    limiter = Limiter(per_client=100, per_day=3, clock=clock)
    for client in "abc":
        limiter.check(client)
    with pytest.raises(Limited) as refused:
        limiter.check("d")
    assert refused.value.retry_after == 23 * 3600
    clock.now += 23 * 3600
    limiter.check("d")


def test_refused_questions_are_not_counted():
    clock = Clock()
    limiter = Limiter(per_client=1, per_day=2, clock=clock)
    limiter.check("a")
    for _ in range(5):
        with pytest.raises(Limited):
            limiter.check("a")
    limiter.check("b")
