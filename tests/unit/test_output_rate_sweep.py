"""The installed wall rate gate requires a comparable protected interval."""
from copy import deepcopy

from tools.benchmarks.output_rate_sweep import ERROR_COUNTERS, summarize


def observations():
    first = {
        "controller": {
            "updated_at": 100.0,
            "actual_fps": 200.0,
            "target_fps": 200,
            "is_running": True,
            "controller_session_id": "same-process",
        },
        "diagnostics": {
            "driver_stats": {
                "devices": [
                    {
                        "device_index": index,
                        "spi_route": route,
                        "spi_speed_hz": 20_000_000,
                        "receiver_connected": True,
                        "receiver_status_integrity_verified": True,
                        "receiver_status_observed_at": 99.5,
                        "receiver_frames_displayed": 2000,
                        "receiver_frames_superseded": 10,
                        **dict.fromkeys(ERROR_COUNTERS, 0),
                    }
                    for index, route in enumerate(((1, 0), (1, 1), (0, 0), (0, 1), (0, 3)))
                ],
            },
        },
    }
    last = deepcopy(first)
    last["controller"]["updated_at"] += 11
    for device in last["diagnostics"]["driver_stats"]["devices"]:
        device["receiver_status_observed_at"] += 10
        device["receiver_frames_displayed"] += 1500
    return first, last


def test_gate_uses_per_receiver_observation_times_and_checks_every_route():
    first, last = observations()
    result = summarize([first, last], min_fps=150)
    assert result["passed"]
    assert result["controller"]["mean_tick_fps"] == 200
    assert [item["display_counter_fps"] for item in result["receiver_rates"]] == [150] * 5

    last["diagnostics"]["driver_stats"]["devices"][3]["receiver_crc_errors"] += 1
    result = summarize([first, last], min_fps=150)
    assert not result["passed"]
    assert result["receiver_rates"][3]["error_deltas"]["receiver_crc_errors"] == 1


def test_invalid_protected_counter_interval_cannot_pass():
    first, last = observations()
    devices = last["diagnostics"]["driver_stats"]["devices"]
    devices[0]["receiver_status_observed_at"] -= 10
    devices[1]["receiver_status_integrity_errors"] += 1
    devices[2]["receiver_frames_displayed"] = 5
    devices.pop()
    result = summarize([first, last], min_fps=150)
    assert not result["complete"]
    assert not result["passed"]
    assert [item["display_counter_fps"] for item in result["receiver_rates"]] == [
        None, None, None, 150, None,
    ]


def test_controller_restart_or_scene_switch_invalidates_window():
    first, last = observations()
    last["controller"]["controller_state_revision"] = 42
    result = summarize([first, last])
    assert not result["complete"]
    assert result["controller"]["mean_tick_fps"] is None
    assert result["passed"] is None
