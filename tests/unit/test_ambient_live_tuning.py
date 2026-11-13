"""Opted-in artistic edits retain running state and refresh resolved pixels."""

from copy import deepcopy

import numpy as np
import pytest

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.ocean_waves import OceanWavesAnimation
from animation.plugins.sparkle import SparkleAnimation


CONTROLS = (
    (SparkleAnimation, "density", 1.0),
    (SparkleAnimation, "linger", .05),
    (SparkleAnimation, "twinkle", 0.0),
    (SparkleAnimation, "night", .55),
    (AuroraCurtainsAnimation, "curtain_density", 1.0),
    (AuroraCurtainsAnimation, "fold_depth", 0.0),
    (AuroraCurtainsAnimation, "glow_intensity", 0.0),
    (OceanWavesAnimation, "swell", .15),
    (OceanWavesAnimation, "chop", 1.0),
    (OceanWavesAnimation, "foam", 0.0),
)


def render(instance, elapsed):
    context = ResolvedScene(
        canonical_scene={"plants": {"effects": {"version": 1, "active": [], "strengths": {}}}},
        canonical_bytes=b"live-tuning", digest="live-tuning",
        descriptor=instance.component_descriptor(), parameters=dict(instance.params),
        palette={"palette_id": "ember"}, phase_time=elapsed, plant_inputs={},
    )
    frame = instance.render_resolved_scene(context)
    return (frame.pixels if hasattr(frame, "pixels") else frame).copy()


@pytest.mark.parametrize("renderer,key,value", CONTROLS)
def test_live_control_retains_state_and_refreshes_resolved_output(renderer, key, value):
    controller = PreviewLEDController(33, 138)
    tuned, control = renderer(controller), renderer(controller)
    # Warm sequentially so Sparkle has persistent stars, rather than an empty
    # first-call field. Both renderers start with identical RNG and phase state.
    for tick in range(61):
        elapsed = tick / 120
        np.testing.assert_array_equal(render(tuned, elapsed), render(control, elapsed))
    semantic = dict(tuned.semantic_snapshot())
    cadence = dict(tuned.cadence_snapshot())
    sparkle_state = None
    if renderer is SparkleAnimation:
        assert np.count_nonzero(tuned._spark) > 0
        sparkle_state = (
            tuned._spark.copy(), tuned._spark_hue.copy(),
            deepcopy(tuned._rng.bit_generator.state),
            deepcopy(tuned._hue_rng.bit_generator.state),
        )

    assert key in renderer.LIVE_TUNABLE_PARAMETERS
    tuned.update_parameters({key: value})
    assert tuned.params[key] == value
    assert dict(tuned.semantic_snapshot()) == semantic
    assert dict(tuned.cadence_snapshot()) == cadence
    if sparkle_state is not None:
        np.testing.assert_array_equal(tuned._spark, sparkle_state[0])
        np.testing.assert_array_equal(tuned._spark_hue, sparkle_state[1])
        assert tuned._rng.bit_generator.state == sparkle_state[2]
        assert tuned._hue_rng.bit_generator.state == sparkle_state[3]

    # The same resolved tick is repainted from the updated parameters; density
    # and linger intentionally affect the next simulation tick instead.
    changed = render(tuned, .5)
    unchanged = render(control, .5)
    assert dict(tuned.semantic_snapshot()) == semantic
    if key in {"density", "linger"}:
        np.testing.assert_array_equal(changed, unchanged)
    else:
        assert not np.array_equal(changed, unchanged)
    assert not tuned.generate_frame(.5, 100).changed
    next_time = .5 + 1 / 24
    changed, unchanged = render(tuned, next_time), render(control, next_time)
    assert changed.shape == (33 * 138, 3) and changed.dtype == np.uint8
    assert not np.array_equal(changed, unchanged)


@pytest.mark.parametrize("renderer", (SparkleAnimation, AuroraCurtainsAnimation, OceanWavesAnimation))
def test_live_opt_in_excludes_reseed_and_phase_changing_controls(renderer):
    accepted = {key for candidate, key, _ in CONTROLS if candidate is renderer}
    assert renderer.LIVE_TUNABLE_PARAMETERS == frozenset(accepted)
    assert not renderer.LIVE_TUNABLE_PARAMETERS & {"seed", "source_fps", "travel"}
