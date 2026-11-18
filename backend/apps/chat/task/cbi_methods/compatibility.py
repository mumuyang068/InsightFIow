from .binding import BindingSnapshot, FIXTURE_VERSION


def fixture(id_, label, start, end, observation, window, allowed, *, dates=None,
            observations=(), test_only=False, reading="observed_events", channels=False):
    return BindingSnapshot(
        id=id_, label=label, version=FIXTURE_VERSION, store="A", entry_start=start, entry_end=end,
        observation_asof=observation, window_hours=window, allowed_windows=allowed,
        method="ordered_attempt_funnel" if reading == "observed_events" else "fixed_window_repeat",
        epoch=dates, observations=observations or (observation,), test_only=test_only,
        reading=reading, channels=channels,
    )


COMPATIBILITY = {
    item.id: item for item in (
        fixture("r11_funnel", "R11 购买转化", "2026-10-01", "2026-10-02", "2026-10-05", 24, (24, 48), dates="2026-10-01"),
        fixture("c01", "C01 同秒顺序", "0", "86400", "172800", 24, (24,), test_only=True),
        fixture("c02", "C02 观察推进", "0", "86400", "86400", 48, (48,), observations=("86400", "172800"), test_only=True),
        fixture("r11_repeat", "R11 固定窗复购", "2026-10-01", "2026-11-06", "2026-11-10", 720, (720,),
                reading="fixed_2026-11-10_snapshot", channels=True),
        fixture("e03", "E03 首购迁移", "2026-10-01", "2026-10-10", "2026-11-10", 720, (720,),
                reading="refunds_known_by_observation", test_only=True),
    )
}
