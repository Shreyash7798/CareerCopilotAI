import app.discovery_schedule as sched


def test_interval_scales_with_company_count():
    # Few companies: still ~3 runs/day (every ~8h), capped by max_interval.
    few = sched.compute_discovery_interval_minutes(
        5, max_per_run=25, target_polls_per_day=3, min_interval=60, max_interval=360
    )
    assert few == 360

    # Many companies: more frequent runs, down to hourly floor.
    many = sched.compute_discovery_interval_minutes(
        69, max_per_run=25, target_polls_per_day=3, min_interval=60, max_interval=360
    )
    assert many == 160  # 9 runs/day -> ~160 min

    huge = sched.compute_discovery_interval_minutes(
        120, max_per_run=25, target_polls_per_day=3, min_interval=60, max_interval=360
    )
    assert huge == 96  # 15 runs/day

    at_hourly_floor = sched.compute_discovery_interval_minutes(
        200, max_per_run=25, target_polls_per_day=3, min_interval=60, max_interval=360
    )
    assert at_hourly_floor == 60


def test_manual_interval_when_auto_disabled(monkeypatch):
    monkeypatch.setattr(
        sched,
        "get_settings",
        lambda refresh=False: {
            "scheduler": {
                "discovery_auto_interval": False,
                "discovery_interval_minutes": 42,
            },
            "pipeline": {},
        },
    )
    assert sched.compute_discovery_interval_minutes(99) == 42


def test_zero_companies_uses_max_interval():
    assert sched.compute_discovery_interval_minutes(0, max_interval=360) == 360
