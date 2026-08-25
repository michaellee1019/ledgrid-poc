import binascii
import json
import subprocess
import sys
from pathlib import Path

from tools.generate_animation_pipeline_golden import build_fixture, render_fixture
from tools.generate_firmware_animation_pipeline_golden import render_header


REPO_ROOT = Path(__file__).resolve().parents[2]
JSON_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "animation_pipeline_v1.json"
CPP_FIXTURE = (
    REPO_ROOT / "firmware" / "esp32" / "test" / "fixtures" / "animation_pipeline_v1.hpp"
)


def test_cpp_firmware_fixture_is_exact_derivative_of_json_authority():
    fixture = json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))
    assert CPP_FIXTURE.read_text(encoding="utf-8") == render_header(fixture)


def test_json_fixture_is_exact_derivative_of_generator_authority():
    assert JSON_FIXTURE.read_text(encoding="utf-8") == render_fixture()
    assert json.loads(JSON_FIXTURE.read_text(encoding="utf-8")) == build_fixture()


def test_json_fixture_check_command_passes():
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "generate_animation_pipeline_golden.py"),
            "--check",
        ],
        cwd=REPO_ROOT,
        check=True,
    )


def test_firmware_fixture_check_command_passes():
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "generate_firmware_animation_pipeline_golden.py"),
            "--check",
        ],
        cwd=REPO_ROOT,
        check=True,
    )


def test_wire_packets_are_complete_exact_and_crc_valid():
    protocol = build_fixture()["firmware_protocol"]
    vectors = {vector["id"]: vector for vector in protocol["wire_packet_vectors"]}
    assert set(vectors) == {
        "controller_session_begin",
        "overlay_begin_full_snapshot",
        "overlay_begin_delta_noop",
        "overlay_patch_maximum",
        "overlay_patch_tail",
        "overlay_patch_batch_multiple_sorted",
        "overlay_patch_batch_maximum_pixels",
        "overlay_patch_batch_maximum_spans",
        "overlay_patch_batch_full_snapshot_tail",
        "overlay_commit",
        "overlay_clear",
        "overlay_renew",
    }

    command_names = {
        "controller_session_begin": "controller_session_begin",
        "overlay_begin_full_snapshot": "overlay_begin",
        "overlay_begin_delta_noop": "overlay_begin",
        "overlay_patch_maximum": "overlay_patch",
        "overlay_patch_tail": "overlay_patch",
        "overlay_patch_batch_multiple_sorted": "overlay_patch_batch",
        "overlay_patch_batch_maximum_pixels": "overlay_patch_batch",
        "overlay_patch_batch_maximum_spans": "overlay_patch_batch",
        "overlay_patch_batch_full_snapshot_tail": "overlay_patch_batch",
        "overlay_commit": "overlay_commit",
        "overlay_clear": "overlay_clear",
        "overlay_renew": "overlay_renew",
    }
    for vector_id, vector in vectors.items():
        packet = bytes.fromhex(vector["packet_hex"])
        command_name = command_names[vector_id]
        assert len(packet) == vector["packet_bytes"]
        assert vector["header_bytes"] == protocol["header_bytes"][command_name]
        assert packet[0] == protocol["command_ids"][command_name]
        assert packet[1] == protocol["version"]
        assert int.from_bytes(packet[-2:], "big") == vector["expected_crc16"]
        assert binascii.crc_hqx(packet[:-2], 0xFFFF) == vector["expected_crc16"]

    maximum = vectors["overlay_patch_maximum"]
    assert maximum["packet_bytes"] == protocol["max_transaction_bytes"]
    assert maximum["fields"]["start"] == 0
    assert maximum["fields"]["count"] == protocol["max_rgba_pixels_per_patch"]
    tail = vectors["overlay_patch_tail"]
    assert tail["fields"]["start"] + tail["fields"]["count"] == protocol["local_pixels"]
    delta_noop = vectors["overlay_begin_delta_noop"]
    assert delta_noop["fields"]["update_kind"] == 2
    assert delta_noop["fields"]["expected_patches"] == 0

    for vector in (maximum, tail):
        packet = bytes.fromhex(vector["packet_hex"])
        payload = packet[vector["header_bytes"] : -protocol["crc_bytes"]]
        assert len(payload) == vector["fields"]["count"] * 4
        assert all(
            red <= alpha and green <= alpha and blue <= alpha
            for red, green, blue, alpha in zip(
                payload[0::4], payload[1::4], payload[2::4], payload[3::4]
            )
        )

    batch_ids = (
        "overlay_patch_batch_multiple_sorted",
        "overlay_patch_batch_maximum_pixels",
        "overlay_patch_batch_maximum_spans",
        "overlay_patch_batch_full_snapshot_tail",
    )
    for vector_id in batch_ids:
        vector = vectors[vector_id]
        packet = bytes.fromhex(vector["packet_hex"])
        cursor = vector["header_bytes"]
        spans = []
        for _ in range(vector["fields"]["span_count"]):
            start = int.from_bytes(packet[cursor : cursor + 2], "big")
            count = int.from_bytes(packet[cursor + 2 : cursor + 4], "big")
            cursor += protocol["batch_span_descriptor_bytes"]
            rgba = packet[cursor : cursor + count * 4]
            cursor += len(rgba)
            assert len(rgba) == count * 4
            assert all(
                red <= alpha and green <= alpha and blue <= alpha
                for red, green, blue, alpha in zip(
                    rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4]
                )
            )
            spans.append({"start": start, "count": count})
        assert cursor == len(packet) - protocol["crc_bytes"]
        assert spans == vector["fields"]["spans"]

    multi = vectors["overlay_patch_batch_multiple_sorted"]
    assert multi["packet_bytes"] == 66
    maximum_pixels = vectors["overlay_patch_batch_maximum_pixels"]
    assert maximum_pixels["packet_bytes"] == 4094
    assert maximum_pixels["fields"]["spans"][0]["count"] == 1015
    maximum_spans = vectors["overlay_patch_batch_maximum_spans"]
    assert maximum_spans["packet_bytes"] == 4094
    assert maximum_spans["fields"]["span_count"] == 508
    batch_tail = vectors["overlay_patch_batch_full_snapshot_tail"]
    assert batch_tail["fields"]["spans"] == [{"start": 1015, "count": 89}]


def test_malformed_batch_packets_are_crc_valid_and_pin_exact_rejections():
    protocol = build_fixture()["firmware_protocol"]
    vectors = {
        vector["id"]: vector
        for vector in protocol["malformed_batch_packet_vectors"]
    }
    assert set(vectors) == {
        "overlay_patch_batch_malformed_zero_spans",
        "overlay_patch_batch_malformed_truncated_rgba",
        "overlay_patch_batch_malformed_unsorted_spans",
        "overlay_patch_batch_malformed_overlapping_spans",
        "overlay_patch_batch_malformed_out_of_bounds",
        "overlay_patch_batch_malformed_nonpremultiplied",
        "overlay_patch_batch_malformed_over_capacity",
    }
    assert {vector["expected_result"] for vector in vectors.values()} == {
        4,
        5,
        6,
        12,
        13,
    }
    for vector in vectors.values():
        packet = bytes.fromhex(vector["packet_hex"])
        assert packet[0] == protocol["command_ids"]["overlay_patch_batch"]
        assert packet[1] == protocol["version"]
        assert int.from_bytes(packet[-2:], "big") == vector["expected_crc16"]
        assert binascii.crc_hqx(packet[:-2], 0xFFFF) == vector["expected_crc16"]
    assert vectors["overlay_patch_batch_malformed_over_capacity"]["packet_bytes"] == 4098


def test_receiver_slicing_vectors_cover_each_board_and_every_seam():
    protocol = build_fixture()["firmware_protocol"]
    vectors = protocol["receiver_slice_vectors"]
    full_receivers = vectors[:5]
    assert [
        vector["expected_slices"][0]["board_index"] for vector in full_receivers
    ] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert [
        vector["expected_slices"][0]["local_end"]
        for vector in full_receivers
    ] == [protocol["local_pixels"]] * 4 + [138]

    seams = [
        vector for vector in vectors
        if vector["id"].startswith("boundary_")
    ]
    assert [
        [item["board_index"] for item in vector["expected_slices"]] for vector in seams
    ] == [
        [0, 1],
        [1, 2],
        [2, 3],
        [3, 4],
    ]
    for vector in vectors:
        expected_size = vector["global_end"] - vector["global_start"]
        slices = vector["expected_slices"]
        assert (
            sum(item["local_end"] - item["local_start"] for item in slices)
            == expected_size
        )
        assert [item["source_offset"] for item in slices] == sorted(
            item["source_offset"] for item in slices
        )


def test_ordering_cas_and_lease_vectors_cover_success_retry_and_failure():
    protocol = build_fixture()["firmware_protocol"]
    assert {
        vector["expected_relation"] for vector in protocol["counter_order_vectors"]
    } == {
        -1,
        0,
        1,
    }
    assert {vector["id"] for vector in protocol["generation_begin_vectors"]} >= {
        "next_generation",
        "prior_generation_cas_mismatch",
        "staged_exact_retry",
        "staged_conflicting_retry",
        "counter_exhausted",
    }
    lease_by_id = {vector["id"]: vector for vector in protocol["lease_commit_vectors"]}
    assert lease_by_id["ready_before_expiry"]["expected_result"] == 1
    assert lease_by_id["ready_after_expiry"]["expected_result"] == 17
    assert lease_by_id["binding_precedes_expiry"]["expected_result"] == 15
    assert lease_by_id["interrupted_before_expiry"]["expected_result"] == 16

    schedule = protocol["commit_schedule_vectors"]
    assert [vector["should_present"] for vector in schedule] == [False, True, True]
    assert all(
        vector["should_present"]
        == (vector["current_scene_time"] >= vector["present_at_scene_time"])
        for vector in schedule
    )
