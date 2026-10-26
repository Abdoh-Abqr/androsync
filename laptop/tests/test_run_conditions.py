"""Run conditions, the speed limiter and the agent's phone state (Phase T3)."""

from __future__ import annotations

import time
from datetime import datetime, time as dtime
from pathlib import Path

import pytest

from androsync.config import ConfigError, load_config
from androsync.daemon.auto import AutoSync
from androsync.run_conditions import RunConditions, allowed, in_schedule, parse, parse_battery, parse_route
from androsync.throttle import MAX_CHUNK, MIN_CHUNK, Throttle


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute)


# --- parsing ------------------------------------------------------------------------------------------------


def test_nothing_set_means_no_conditions():
    assert parse(None, "p") == RunConditions()
    assert parse({}, "p").any is False


def test_all_three_conditions_are_read():
    cond = parse({"wifi_only": True, "charging_only": True, "schedule": "22:00-07:00"}, "p")
    assert cond.wifi_only and cond.charging_only and cond.schedule == (dtime(22, 0), dtime(7, 0)) and cond.any


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"colour": "red"}, "may only have"),
        ("wifi", "may only have"),
        ({"schedule": "25:00-07:00"}, "real clock times"),
        ({"schedule": "night"}, "22:00-07:00"),
        ({"schedule": "22:00"}, "22:00-07:00"),
    ],
)
def test_bad_conditions_name_the_pair(raw, message):
    with pytest.raises(ConfigError, match=f"pair p: .*{message}"):
        parse(raw, "p")


def test_the_pair_config_carries_its_conditions(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "device: auto\nservice:\n  bandwidth_kb_s: 300\npairs:\n  - name: a\n    local: /x\n    remote: /sdcard/a\n    direction: two-way\n"
        "    run_conditions: {wifi_only: true, schedule: '08:00-18:30'}\n"
    )
    config = load_config(path)
    assert config.pairs[0].run_conditions.wifi_only and config.pairs[0].run_conditions.schedule == (dtime(8), dtime(18, 30))
    assert config.service.bandwidth_kb_s == 300


# --- deciding ----------------------------------------------------------------------------------------------


def test_the_schedule_includes_the_start_and_excludes_the_end_and_may_cross_midnight():
    day = (dtime(8, 0), dtime(18, 0))
    assert in_schedule(day, dtime(8, 0)) and in_schedule(day, dtime(17, 59))
    assert not in_schedule(day, dtime(18, 0)) and not in_schedule(day, dtime(7, 59))
    night = (dtime(22, 0), dtime(7, 0))
    assert in_schedule(night, dtime(23, 30)) and in_schedule(night, dtime(2, 0)) and in_schedule(night, dtime(22, 0))
    assert not in_schedule(night, dtime(7, 0)) and not in_schedule(night, dtime(12, 0))


def test_conditions_hold_back_on_mobile_data_discharging_and_outside_the_hours():
    cond = RunConditions(wifi_only=True, charging_only=True, schedule=(dtime(22, 0), dtime(7, 0)))
    ok_phone = {"net": "wifi", "charging": True}
    assert allowed(cond, at(23), ok_phone) == (True, "")
    assert allowed(cond, at(12), ok_phone)[1].startswith("outside the allowed hours 22:00-07:00")
    assert allowed(cond, at(23), {"net": "mobile", "charging": True}) == (False, "waiting for Wi-Fi")
    assert allowed(cond, at(23), {"net": "none", "charging": True}) == (False, "waiting for Wi-Fi")
    assert allowed(cond, at(23), {"net": "wifi", "charging": False}) == (False, "waiting for the phone to charge")


def test_what_is_not_known_never_blocks():
    cond = RunConditions(wifi_only=True, charging_only=True)
    assert allowed(cond, at(12), {}) == (True, "")
    assert allowed(cond, at(12), {"net": None, "charging": None}) == (True, "")
    assert allowed(RunConditions(), at(3), {"net": "mobile", "charging": False}) == (True, "")


# --- reading an adb phone ------------------------------------------------------------------------------------

BATTERY_CHARGING = "Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  Wireless powered: false\n  status: 2\n"
BATTERY_ON_BATTERY = "Current Battery Service state:\n  AC powered: false\n  USB powered: false\n  Wireless powered: false\n  status: 3\n"


def test_charging_is_read_from_dumpsys_battery():
    assert parse_battery(BATTERY_CHARGING) is True
    assert parse_battery(BATTERY_ON_BATTERY) is False
    assert parse_battery("") is None and parse_battery("Can't find service: battery") is None


def test_the_network_kind_is_read_from_the_route_interface():
    assert parse_route("1.1.1.1 via 192.0.2.1 dev wlan0 src 192.0.2.77 uid 2000") == "wifi"
    assert parse_route("1.1.1.1 dev rmnet_data1 src 10.1.2.3 uid 2000") == "mobile"
    assert parse_route("1.1.1.1 dev ccmni0 src 10.9.9.9") == "mobile"
    assert parse_route("1.1.1.1 dev tun0 src 10.0.0.2") is None
    assert parse_route("RTNETLINK answers: Network is unreachable") is None


# --- the speed limiter ----------------------------------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


def test_no_limit_never_waits():
    t = FakeTime()
    throttle = Throttle(0, t.clock, t.sleep)
    throttle.wait(10_000_000)
    assert t.slept == 0 and not throttle.limited and throttle.chunk_size() == MAX_CHUNK


def test_one_megabyte_at_256_kilobytes_a_second_takes_about_four_seconds():
    t = FakeTime()
    throttle = Throttle(256 * 1024, t.clock, t.sleep)
    for _ in range(0, 1024 * 1024, throttle.chunk_size()):
        throttle.wait(throttle.chunk_size())
    assert 3.9 <= t.now <= 4.1


def test_chunks_shrink_when_the_limit_is_low():
    assert Throttle(1024).chunk_size() == MIN_CHUNK
    assert Throttle(1 << 30).chunk_size() == MAX_CHUNK
    assert MIN_CHUNK < Throttle(256 * 1024).chunk_size() < MAX_CHUNK


def test_time_already_spent_counts_towards_the_limit():
    t = FakeTime()
    throttle = Throttle(1000, t.clock, t.sleep)
    t.now += 2  # the caller was slow anyway
    throttle.wait(1000)  # 1 s worth of data after 2 s of work: no extra wait needed
    assert t.slept == 0


# --- the agent's state and a limited transfer through the real server ---------------------------------------------


def test_the_agent_reports_its_phone_state_with_each_poll_and_the_laptop_remembers_it(tmp_path):
    from test_agent_transport import Env

    env = Env(tmp_path / "env", start_agent=False)
    try:
        assert env.broker.phone_state() == {}
        env.get("/v1/agent/next?wait=0&net=mobile&battery=discharging")
        assert env.broker.phone_state() == {"net": "mobile", "charging": False}
        env.get("/v1/agent/next?wait=0&net=wifi&battery=charging")
        assert env.broker.phone_state() == {"net": "wifi", "charging": True}
        env.get("/v1/agent/next?wait=0&net=garbage&battery=garbage")  # unknown words are ignored
        assert env.broker.phone_state() == {"net": "wifi", "charging": True}
    finally:
        env.close()


def test_the_agent_state_goes_stale():
    from androsync.transport.agent import AgentBroker

    now = [0.0]
    broker = AgentBroker(clock=lambda: now[0])
    broker.report_state("wifi", "charging")
    assert broker.phone_state()["net"] == "wifi"
    now[0] += 500
    assert broker.phone_state() == {}


def test_a_file_body_through_the_server_is_slowed_to_the_configured_speed(tmp_path):
    from test_agent_transport import Env, put

    from androsync.model import Side
    from androsync.transport.agent import AgentTransport

    env = Env(tmp_path / "env", config_text="device: auto\nservice:\n  port: 8765\n  bandwidth_kb_s: 300\npairs: []\n")
    try:
        transport = AgentTransport(env.broker, Side.REMOTE, check_root=lambda r: r)
        src = tmp_path / "big.bin"
        src.write_bytes(b"x" * (600 * 1024))
        started = time.monotonic()
        transport.store(src, str(env.phone), "big.bin", 1_700_000_000)
        elapsed = time.monotonic() - started
        assert (env.phone / "big.bin").stat().st_size == 600 * 1024
        assert elapsed >= 1.5, elapsed  # 600 KB at 300 KB/s is about two seconds (it would take far under one without a limit)
    finally:
        env.close()


# --- automatic sync holds a due sync back until the conditions allow it ------------------------------------------


def test_a_due_automatic_sync_waits_for_wifi_and_then_runs(tmp_path):
    from test_auto import Clock, FakeRunner, _NoWatcher, write_config

    path = write_config(tmp_path, extra="    run_conditions: {wifi_only: true}\n")
    clock, runner = Clock(), FakeRunner()
    phone = {"net": "mobile"}
    auto = AutoSync(
        lambda: load_config(path), runner, clock=clock, watcher_factory=lambda r, cb: _NoWatcher(),
        first_run_done=lambda p: True, phone_state=lambda p: dict(phone), now_fn=lambda: at(12),
    )
    auto.tick()
    auto.mark_dirty("notes")
    clock.advance(20)
    auto.tick()
    assert runner.submitted == []
    status = auto.status("notes")
    assert status["state"] == "waiting" and status["reason"] == "waiting for Wi-Fi"
    phone["net"] = "wifi"
    clock.advance(1)
    auto.tick()
    assert len(runner.submitted) == 1
