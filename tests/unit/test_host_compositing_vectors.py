"""Fixed host math vectors retained from the retired pipeline fixture.

Expected values are literals, independent of the implementation under test.
Receiver protocol transactions are intentionally no longer represented here.
"""

import pytest

from animation.core.compositing import (
    canvas_to_logical_flat_index,
    logical_flat_index,
    receiver_local_index,
    scale_premultiplied_rgba,
    source_over_rgb,
    source_over_rgba,
    union_dirty_ranges,
)


@pytest.mark.parametrize(
    "base_rgb, overlay_rgba, expected",
    [
        pytest.param((12, 34, 56), (0, 0, 0, 0), (12, 34, 56), id="transparent_black"),
        pytest.param((12, 34, 56), (0, 0, 0, 255), (0, 0, 0), id="opaque_black"),
        pytest.param(
            (250, 200, 150), (17, 31, 47, 255), (17, 31, 47), id="opaque_color"
        ),
        pytest.param((1, 2, 3), (0, 0, 0, 127), (1, 1, 2), id="half_up_rounding"),
        pytest.param(
            (255, 255, 255),
            (254, 253, 252, 254),
            (255, 254, 253),
            id="channel_saturation",
        ),
        pytest.param(
            (241, 17, 99), (32, 64, 16, 128), (152, 72, 65), id="mixed_channels"
        ),
    ],
)
def test_blend_vectors(base_rgb, overlay_rgba, expected):
    assert source_over_rgb(base_rgb, overlay_rgba) == expected


@pytest.mark.parametrize(
    "input_rgba, opacity, expected",
    [
        pytest.param((99, 66, 33, 128), 0, (0, 0, 0, 0), id="zero_endpoint"),
        pytest.param((99, 66, 33, 128), 255, (99, 66, 33, 128), id="opaque_endpoint"),
        pytest.param((1, 1, 1, 1), 128, (1, 1, 1, 1), id="half_up_to_one"),
        pytest.param((1, 1, 1, 1), 127, (0, 0, 0, 0), id="half_down_to_zero"),
        pytest.param((120, 60, 30, 128), 200, (94, 47, 24, 100), id="mixed_opacity"),
    ],
)
def test_opacity_vectors(input_rgba, opacity, expected):
    assert scale_premultiplied_rgba(input_rgba, opacity) == expected


@pytest.mark.parametrize(
    "bottom_rgba, top_rgba, expected",
    [
        pytest.param(
            (80, 40, 20, 128), (0, 0, 0, 0), (80, 40, 20, 128), id="transparent_top"
        ),
        pytest.param(
            (80, 40, 20, 128), (5, 7, 9, 255), (5, 7, 9, 255), id="opaque_top"
        ),
        pytest.param(
            (80, 20, 10, 128), (5, 60, 15, 96), (55, 72, 21, 176), id="ordered_overlap"
        ),
        pytest.param(
            (1, 1, 1, 1), (1, 0, 0, 128), (1, 0, 0, 128), id="round_each_fold"
        ),
    ],
)
def test_overlay_fold_vectors(bottom_rgba, top_rgba, expected):
    assert source_over_rgba(bottom_rgba, top_rgba) == expected


@pytest.mark.parametrize(
    "previous_coverage, next_coverage, pixel_count, expected",
    [
        pytest.param(((10, 13),), ((20, 23),), 64, ((10, 13), (20, 23)), id="movement"),
        pytest.param(
            ((10, 14), (30, 34)),
            ((13, 18), (34, 36)),
            64,
            ((10, 18), (30, 36)),
            id="overlap_and_adjacency",
        ),
        pytest.param(
            ((4, 9), (40, 44)), (), 64, ((4, 9), (40, 44)), id="complete_clear"
        ),
        pytest.param((), (), 64, (), id="empty"),
    ],
)
def test_dirty_range_vectors(previous_coverage, next_coverage, pixel_count, expected):
    assert (
        union_dirty_ranges(previous_coverage, next_coverage, pixel_count=pixel_count)
        == expected
    )


@pytest.mark.parametrize(
    "canvas_row, canvas_column, global_strips, leds_per_strip, expected",
    [
        pytest.param(0, 0, 33, 138, 0, id="canvas_top_left"),
        pytest.param(0, 32, 33, 138, 4416, id="canvas_first_row_last_strip"),
        pytest.param(137, 0, 33, 138, 137, id="canvas_last_row_first_strip"),
        pytest.param(137, 32, 33, 138, 4553, id="canvas_bottom_right"),
        pytest.param(73, 18, 33, 138, 2557, id="canvas_interior"),
    ],
)
def test_canvas_to_logical_vectors(
    canvas_row, canvas_column, global_strips, leds_per_strip, expected
):
    assert (
        canvas_to_logical_flat_index(
            canvas_row,
            canvas_column,
            strip_count=global_strips,
            leds_per_strip=leds_per_strip,
        )
        == expected
    )


@pytest.mark.parametrize(
    "strip, led, offset, width, global_index, local_index",
    [
        pytest.param(0, 0, 0, 8, 0, 0, id="wall_origin"),
        pytest.param(7, 137, 0, 8, 1103, 1103, id="receiver_0_last"),
        pytest.param(8, 0, 8, 8, 1104, 0, id="receiver_1_first"),
        pytest.param(15, 137, 8, 8, 2207, 1103, id="receiver_1_last"),
        pytest.param(16, 0, 16, 8, 2208, 0, id="receiver_2_first"),
        pytest.param(23, 137, 16, 8, 3311, 1103, id="receiver_2_last"),
        pytest.param(24, 0, 24, 8, 3312, 0, id="receiver_3_first"),
        pytest.param(31, 137, 24, 8, 4415, 1103, id="receiver_3_last"),
        pytest.param(32, 0, 32, 1, 4416, 0, id="tail_first"),
        pytest.param(32, 137, 32, 1, 4553, 137, id="wall_last"),
        pytest.param(7, 137, 8, 8, 1103, None, id="before_local_offset"),
        pytest.param(16, 0, 8, 8, 2208, None, id="after_local_range"),
        pytest.param(33, 0, 32, 1, None, None, id="after_tail_range"),
        pytest.param(24, 138, 24, 8, None, None, id="led_out_of_range"),
        pytest.param(33, 0, 24, 8, None, None, id="strip_out_of_range"),
    ],
)
def test_coordinate_vectors(strip, led, offset, width, global_index, local_index):
    if global_index is None:
        with pytest.raises(ValueError):
            logical_flat_index(strip, led, strip_count=33, leds_per_strip=138)
    else:
        assert (
            logical_flat_index(strip, led, strip_count=33, leds_per_strip=138)
            == global_index
        )
    if local_index is None:
        with pytest.raises(ValueError):
            receiver_local_index(
                strip,
                led,
                global_strip_offset=offset,
                local_strip_count=width,
                leds_per_strip=138,
            )
    else:
        assert (
            receiver_local_index(
                strip,
                led,
                global_strip_offset=offset,
                local_strip_count=width,
                leds_per_strip=138,
            )
            == local_index
        )
