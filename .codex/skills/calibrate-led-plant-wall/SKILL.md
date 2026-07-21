---
name: calibrate-led-plant-wall
description: Recalibrate the ledgrid-poc living plant wall from its attached webcam, including camera homography, physical 32x138 dimension verification, foliage occlusion mask, seven fixed-size globe masks, live illumination feedback, and photographed acceptance. Use after the webcam, plants, vines, or rooting globes move; after significant growth or pruning; when animations shine through foliage; or when asked to refresh plant calibration maps.
---

# Calibrate LED Plant Wall

## Overview

Rebuild the wall's semantic plant masks as a measured closed loop. Use fresh
webcam evidence, deploy dim feedback patterns, inspect resulting captures, and
iterate until masks align without asking the user to estimate pixel offsets.

Read [references/workflow.md](references/workflow.md) before running commands.
Also read the repository's `docs/PLANT_WALL_CALIBRATION.md` when present; it is
the authoritative command-level runbook.

## Workflow

1. Inspect repository status and preserve unrelated user changes.
2. Confirm the controller is healthy and reports 32 strips by 138 LEDs.
3. Capture a fresh low-room-light wall-off/orientation/full-white/dimension set.
4. Reacquire camera corners from full-white minus wall-off. Never reuse a moved
   camera's homography. Refuse automatic updates when a wall corner is clipped.
5. Capture a fresh ambient-lit wall-off frame for plant/glass recognition.
6. Rebuild the full-white occlusion candidate and start foliage refinement with
   `--fresh-baseline`; never union a prior final map after movement.
7. Rebuild and verify seven globe regions as fixed 8x8 circular footprints.
8. Deploy dim, one-layer or one-region feedback, photograph it, compute integer
   center corrections, and iterate to acceptance thresholds.
9. Rerun foliage after globe changes, then verify `Plant Glow` and telemetry.
10. Run proportional unit/plugin/benchmark checks and report evidence paths.

## Required judgments

- Use reduced room lighting for LED-response subtraction and normal ambient
  lighting for foliage/glass recognition. Outdoor windows are acceptable with
  immediately paired off captures.
- Treat the dimension probe as physical evidence. Do not crop away an apparent
  extra/missing row or change geometry to make a photo convenient.
- Do not infer globe size from illuminated area. Roots and glass bloom bias it.
  Hold size at 8x8 and translate only by whole logical pixels.
- Process globes by stable ID and left-to-right/top-to-bottom order. Use a dim
  single-globe outline/center pattern when identity or centroid is unclear.
- Right-edge circular masks may contain fewer than 52 pixels because physical
  clipping is correct.
- Prefer segmentation reruns from fresh evidence over hand-edited foliage
  indices. Require foliage/globe disjointness.

## Pause conditions

- If the camera moved and all four wall corners are not visible, ask for the
  camera to be repositioned; a clipped corner is not recoverable confidently.
- If power, controller, webcam, or network access is unavailable, report the
  failed check and preserve generated artifacts.
- If the dimension probe differs from 32x138, stop mask work and correct the
  host/firmware geometry mismatch first.

## Completion report

Report logical dimensions and counts, corner confidence and edge clipping,
mask overlap, per-globe region count and center tolerance, live animation FPS
and errors, tests run, and paths to the final wall-off, overlays, and Plant Glow
capture. Leave the verified animation running unless the user asks otherwise.
