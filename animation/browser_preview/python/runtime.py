"""Runtime adapter for unchanged LED-grid Python plugins in Pyodide."""

from __future__ import annotations

import ast
import importlib
import gc
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, MethodType
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

from animation.core.installation_profile import (
    FORMAT_VERSION as INSTALLATION_PROFILE_FORMAT_VERSION,
    GLOBAL_STRIP_COUNT,
    LEDS_PER_STRIP,
    decode_installation_profile,
)
from animation.core.plant_awareness import (
    GLOBE_REGION_ORDER,
    LEGACY_PLANT_MASK_PATH_PARAMETERS,
    PlantMaskGeometry,
)


ENGINE = "python-pyodide-wasm"
DEFAULT_INSTANCE_ID = "primary"
MAX_RUNTIME_INSTANCES = 8
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MISSING = object()
_DIRECTIONAL_INPUTS = frozenset(
    {"left", "right", "down", "rotate-left", "rotate-right", "drop"}
)


@dataclass(frozen=True)
class PluginSpec:
    class_name: str
    role: str
    frame_format: str
    timing_adapter: str
    required_packages: Tuple[str, ...]


def _catalog_payload() -> Mapping[str, Any]:
    bundled = Path(__file__).with_name("ledgrid_browser_manifest.json")
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))

    # Development imports run from animation/browser_preview/python/runtime.py.
    repo_root = Path(__file__).resolve().parents[3]
    entries = []
    for manifest_path in sorted((repo_root / "animation/plugins").glob("*/manifest.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("provider", "python") != "python":
            continue
        plugin_id = manifest_path.parent.name
        role = payload.get("role", "full_scene" if plugin_id == "clock" else "background")
        vibe = payload.get("vibe") if isinstance(payload.get("vibe"), dict) else {}
        entries.append({
            "pluginId": plugin_id,
            "className": payload["class"],
            "role": role,
            "frameFormat": "premultiplied-rgba" if role == "overlay" else "rgb",
            "timingAdapter": vibe.get("timing_adapter", "legacy_speed_param"),
            "requiredPackages": ["pillow"] if plugin_id == "gif_animation" else [],
        })
    for source_path in sorted((repo_root / "animation/plugins").glob("*.py")):
        if source_path.name == "__init__.py":
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        class_names = [
            node.name for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name.endswith("Animation")
        ]
        if len(class_names) != 1:
            raise RuntimeError(
                f"flat browser plugin must define one animation class: {source_path}"
            )
        entries.append({
            "pluginId": source_path.stem,
            "className": class_names[0],
            "role": "background",
            "frameFormat": "rgb",
            "timingAdapter": "scaled_context",
            "requiredPackages": [],
        })
    entries.sort(key=lambda item: item["pluginId"])
    return {"plugins": entries}


def _load_specs() -> Mapping[str, PluginSpec]:
    specs = {}
    for item in _catalog_payload().get("plugins", ()):
        plugin_id = item["pluginId"]
        specs[plugin_id] = PluginSpec(
            class_name=item["className"],
            role=item["role"],
            frame_format=item["frameFormat"],
            timing_adapter=item.get("timingAdapter", "legacy_speed_param"),
            required_packages=tuple(item.get("requiredPackages", ())),
        )
    if not specs:
        raise RuntimeError("Python browser runtime catalog is empty")
    return MappingProxyType(specs)


PLUGIN_SPECS: Mapping[str, PluginSpec] = _load_specs()
SUPPORTED_PLUGINS: Mapping[str, str] = MappingProxyType({
    plugin_id: spec.class_name for plugin_id, spec in PLUGIN_SPECS.items()
})


@dataclass(frozen=True)
class PreviewController:
    """Geometry-only controller surface; intentionally exposes no device I/O."""

    strip_count: int
    leds_per_strip: int
    debug: bool = False

    @property
    def total_leds(self) -> int:
        return self.strip_count * self.leds_per_strip


@dataclass
class _RuntimeInstance:
    identity: Tuple[str, str, int, int, str]
    animation: Any
    controller: PreviewController
    spec: PluginSpec
    frame_bytes: bytes = b""


def _positive_dimension(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"geometry.{name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"geometry.{name} must be a positive integer") from exc
    if number <= 0 or number != value:
        raise ValueError(f"geometry.{name} must be a positive integer")
    return number


def normalize_geometry(geometry: Mapping[str, Any]) -> Tuple[int, int]:
    """Accept API, canvas, and protocol spellings without changing orientation."""
    if not isinstance(geometry, Mapping):
        raise ValueError("geometry must be an object")
    width = geometry.get("stripCount", geometry.get("strip_count", geometry.get("width")))
    height = geometry.get(
        "ledsPerStrip", geometry.get("leds_per_strip", geometry.get("height"))
    )
    return _positive_dimension(width, "stripCount"), _positive_dimension(
        height, "ledsPerStrip"
    )


def _validate_browser_parameters(params: Mapping[str, Any]) -> None:
    if not isinstance(params, Mapping):
        raise ValueError("params must be an object")
    legacy = LEGACY_PLANT_MASK_PATH_PARAMETERS & params.keys()
    if legacy:
        raise ValueError(
            "browser previews reject legacy plant-mask paths; "
            "use the managed installation-profile artifact"
        )


def _same_parameter_value(left: Any, right: Any) -> bool:
    """Compare JSON-shaped parameter values without NumPy coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_parameter_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_parameter_value(a, b) for a, b in zip(left, right)
        )
    return bool(left == right)


def _readonly(value: Any, dtype: np.dtype[Any]) -> np.ndarray:
    result = np.array(value, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def _profile_geometry(profile: Any) -> PlantMaskGeometry:
    """Convert one fully decoded LGIP into the browser's immutable mask view."""
    boolean = np.dtype(np.bool_)
    foliage = _readonly(profile.category == 1, boolean)
    globes = _readonly(profile.category == 2, boolean)
    obstacle = _readonly(profile.category != 0, boolean)
    clearance = _readonly(profile.clearance != 0, boolean)
    foliage_edge = _readonly(profile.foliage_edge != 0, boolean)
    globe_edge = _readonly(profile.globe_edge != 0, boolean)
    obstacle_edge = _readonly(profile.obstacle_edge != 0, boolean)
    distance = _readonly(profile.distance, np.dtype(np.float32))
    normal_x = _readonly(
        profile.normal_x.astype(np.float32) / np.float32(127.0),
        np.dtype(np.float32),
    )
    normal_y = _readonly(
        profile.normal_y.astype(np.float32) / np.float32(127.0),
        np.dtype(np.float32),
    )
    region_masks = MappingProxyType({
        name: _readonly(profile.globe_region == region_id, boolean)
        for region_id, name in enumerate(GLOBE_REGION_ORDER, start=1)
    })
    return PlantMaskGeometry(
        foliage=foliage,
        globes=globes,
        obstacle=obstacle,
        clearance=clearance,
        foliage_flat=foliage.reshape(-1),
        globes_flat=globes.reshape(-1),
        obstacle_flat=obstacle.reshape(-1),
        clearance_flat=clearance.reshape(-1),
        foliage_count=int(np.count_nonzero(foliage)),
        globe_count=int(np.count_nonzero(globes)),
        globe_regions=len(GLOBE_REGION_ORDER),
        foliage_edge=foliage_edge,
        globe_edge=globe_edge,
        obstacle_edge=obstacle_edge,
        distance=distance,
        normal_x=normal_x,
        normal_y=normal_y,
        globe_region_masks=region_masks,
        error="",
    )


def _validated_instance_id(value: Any) -> str:
    resolved = DEFAULT_INSTANCE_ID if value is None else value
    if not isinstance(resolved, str) or _INSTANCE_ID.fullmatch(resolved) is None:
        raise ValueError(
            "instanceId must be 1-64 letters, digits, dots, underscores, or hyphens"
        )
    return resolved


def _validated_wall_time(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("wallTime must be a non-negative finite Unix timestamp")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0.0:
        raise ValueError("wallTime must be a non-negative finite Unix timestamp")
    return resolved


def _validated_preview_coordinate(value: Any, name: str, maximum: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    resolved = float(value)
    if not math.isfinite(resolved) or not 0.0 <= resolved < maximum:
        raise ValueError(f"{name} is outside the browser preview")
    return resolved


class BrowserPreviewRuntime:
    """Keep multiple stateful animations inside one memory-conscious interpreter."""

    def __init__(self) -> None:
        self._instances: Dict[str, _RuntimeInstance] = {}
        self._last_instance_id = DEFAULT_INSTANCE_ID
        self._batch_frame_bytes = b""
        self._installation_profile_digest: Optional[str] = None
        self._installation_profile_geometry: Optional[PlantMaskGeometry] = None

    def bind_installation_profile_path(
        self, artifact_path: str, expected_digest: str
    ) -> Dict[str, Any]:
        """Decode and bind the exact worker-verified LGIP artifact."""
        if not isinstance(expected_digest, str) or _SHA256.fullmatch(expected_digest) is None:
            raise ValueError("expected installation-profile digest is invalid")
        if expected_digest == "0" * 64:
            raise ValueError("an empty installation profile cannot render in the browser")
        if not isinstance(artifact_path, str) or not artifact_path:
            raise ValueError("installation-profile artifact path is unavailable")
        encoded = Path(artifact_path).read_bytes()
        if len(encoded) < 100:
            raise ValueError("installation-profile artifact is truncated")
        embedded_digest = encoded[68:100].hex()
        digest_input = bytearray(encoded)
        digest_input[68:100] = bytes(32)
        computed_digest = hashlib.sha256(digest_input).hexdigest()
        if embedded_digest != expected_digest or computed_digest != expected_digest:
            raise ValueError(
                "installation-profile artifact does not match its selected content digest"
            )
        if self._instances and self._installation_profile_digest != expected_digest:
            raise RuntimeError(
                "cannot replace the installation profile while browser instances are active"
            )
        profile = decode_installation_profile(encoded)
        if (
            profile.global_strip_count != GLOBAL_STRIP_COUNT
            or profile.leds_per_strip != LEDS_PER_STRIP
            or profile.strip_origin != 0
            or profile.strip_count != GLOBAL_STRIP_COUNT
        ):
            raise ValueError(
                f"browser installation profile must cover {GLOBAL_STRIP_COUNT}x{LEDS_PER_STRIP}"
            )
        geometry = _profile_geometry(profile)
        self._installation_profile_digest = expected_digest
        self._installation_profile_geometry = geometry
        animation_module = importlib.import_module("animation")
        bind = getattr(animation_module, "bind_browser_installation_profile", None)
        if callable(bind):
            bind(expected_digest, geometry)
        return {
            "digest": expected_digest,
            "formatVersion": INSTALLATION_PROFILE_FORMAT_VERSION,
            "width": GLOBAL_STRIP_COUNT,
            "height": LEDS_PER_STRIP,
            "foliagePixels": geometry.foliage_count,
            "globePixels": geometry.globe_count,
            "globeRegions": list(geometry.globe_region_masks),
        }

    @property
    def animation(self) -> Any:
        instance = self._instances.get(DEFAULT_INSTANCE_ID)
        return None if instance is None else instance.animation

    @property
    def controller(self) -> Optional[PreviewController]:
        instance = self._instances.get(DEFAULT_INSTANCE_ID)
        return None if instance is None else instance.controller

    @property
    def frame_bytes(self) -> bytes:
        instance = self._instances.get(DEFAULT_INSTANCE_ID)
        return b"" if instance is None else instance.frame_bytes

    def frame_bytes_for(self, instance_id: Optional[str] = None) -> bytes:
        resolved_id = _validated_instance_id(instance_id)
        instance = self._instances.get(resolved_id)
        if instance is None:
            raise RuntimeError(f"Python browser preview instance {resolved_id!r} is not initialized")
        return instance.frame_bytes

    @property
    def batch_frame_bytes(self) -> bytes:
        return self._batch_frame_bytes

    def dispose_instance(self, instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Release one animation's buffers without tearing down Pyodide."""
        resolved_instance_id = _validated_instance_id(instance_id)
        instance = self._instances.pop(resolved_instance_id, None)
        disposed = instance is not None
        if disposed:
            cleanup = getattr(instance.animation, "cleanup", None)
            if callable(cleanup):
                cleanup()
            instance.frame_bytes = b""
            del instance
            # Explicit disposal is infrequent and exists specifically to make
            # large NumPy simulation planes reusable on memory-bound phones.
            gc.collect()
        return {
            "engine": ENGINE,
            "instanceId": resolved_instance_id,
            "disposed": disposed,
            "remainingInstances": len(self._instances),
            "maxInstances": MAX_RUNTIME_INSTANCES,
        }

    @staticmethod
    def _release_instance(instance: _RuntimeInstance) -> None:
        cleanup = getattr(instance.animation, "cleanup", None)
        if callable(cleanup):
            cleanup()
        instance.frame_bytes = b""

    def initialize(
        self,
        plugin_id: str,
        class_name: str,
        geometry: Mapping[str, Any],
        params: Optional[Mapping[str, Any]] = None,
        instance_id: Optional[str] = None,
        installation_profile_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_instance_id = _validated_instance_id(instance_id)
        spec = PLUGIN_SPECS.get(plugin_id)
        if spec is None:
            raise ValueError(
                f"Python browser preview does not support plugin {plugin_id!r}; "
                f"catalog plugins: {', '.join(SUPPORTED_PLUGINS)}"
            )
        if class_name != spec.class_name:
            raise ValueError(
                f"plugin {plugin_id!r} requires class {spec.class_name!r}, "
                f"not {class_name!r}"
            )
        strip_count, leds_per_strip = normalize_geometry(geometry)
        if (
            installation_profile_digest is None
            or installation_profile_digest != self._installation_profile_digest
            or self._installation_profile_geometry is None
        ):
            raise RuntimeError(
                "the exact managed installation-profile artifact must be verified "
                "and bound before browser renderer initialization"
            )
        resolved_params = dict(params or {})
        _validate_browser_parameters(resolved_params)
        identity = (
            plugin_id,
            class_name,
            strip_count,
            leds_per_strip,
            installation_profile_digest,
        )
        instance = self._instances.get(resolved_instance_id)
        reset = instance is None or identity != instance.identity
        if reset:
            if instance is None and len(self._instances) >= MAX_RUNTIME_INSTANCES:
                raise RuntimeError(
                    "Python browser preview instance limit reached "
                    f"({MAX_RUNTIME_INSTANCES}); dispose an inactive renderer first"
                )
            module = importlib.import_module(f"animation.plugins.{plugin_id}")
            animation_class = getattr(module, class_name)
            controller = PreviewController(strip_count, leds_per_strip)
            animation = animation_class(controller, resolved_params)
            animation._browser_installation_profile_geometry = (
                self._installation_profile_geometry
            )
            animation._browser_installation_profile_digest = (
                self._installation_profile_digest
            )
            exact_geometry = self._installation_profile_geometry

            def exact_profile_masks(_owner: Any, clearance: Any = None) -> PlantMaskGeometry:
                del clearance
                if exact_geometry is None:
                    raise RuntimeError(
                        "managed installation-profile geometry is unavailable"
                    )
                return exact_geometry

            animation.get_plant_masks = MethodType(exact_profile_masks, animation)
            replacement = _RuntimeInstance(identity, animation, controller, spec)
            if instance is not None:
                self._release_instance(instance)
                # Replacing a renderer can release multiple large NumPy planes.
                gc.collect()
            instance = replacement
            self._instances[resolved_instance_id] = replacement
        elif resolved_params:
            instance.animation.update_parameters(resolved_params)
        self._last_instance_id = resolved_instance_id
        return {
            "engine": ENGINE,
            "instanceId": resolved_instance_id,
            "pluginId": plugin_id,
            "className": class_name,
            "role": spec.role,
            "frameFormat": spec.frame_format,
            "width": strip_count,
            "height": leds_per_strip,
            "reset": reset,
            "supportedPlugins": list(SUPPORTED_PLUGINS),
            "supportsPlantModifiers": True,
            "supportsMultipleInstances": True,
            "instanceCount": len(self._instances),
            "maxInstances": MAX_RUNTIME_INSTANCES,
            "supportsFixedWallTime": True,
            "installationProfileDigest": self._installation_profile_digest,
            "requiredPackages": list(spec.required_packages),
        }

    @staticmethod
    def _generate(
        instance: _RuntimeInstance,
        elapsed: float,
        frame_index: int,
        wall_time: Optional[float],
    ) -> Any:
        animation = instance.animation
        if wall_time is None or not hasattr(animation, "_clock_now"):
            return animation.generate_frame(elapsed, frame_index)

        previous = animation.__dict__.get("_clock_now", _MISSING)

        def fixed_clock_now(owner: Any) -> datetime:
            local = datetime.fromtimestamp(wall_time).astimezone()
            apply_offset = getattr(owner, "_apply_clock_offset", None)
            return apply_offset(local) if apply_offset is not None else local

        animation._clock_now = MethodType(fixed_clock_now, animation)
        try:
            return animation.generate_frame(elapsed, frame_index)
        finally:
            if previous is _MISSING:
                del animation.__dict__["_clock_now"]
            else:
                animation.__dict__["_clock_now"] = previous

    def render(
        self,
        elapsed: Any,
        frame_index: Any,
        params: Optional[Mapping[str, Any]] = None,
        instance_id: Optional[str] = None,
        wall_time: Any = None,
    ) -> Dict[str, Any]:
        resolved_instance_id = _validated_instance_id(instance_id)
        instance = self._instances.get(resolved_instance_id)
        if instance is None:
            raise RuntimeError(
                f"Python browser preview instance {resolved_instance_id!r} is not initialized"
            )
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            raise ValueError("elapsed must be numeric")
        resolved_elapsed = float(elapsed)
        if not math.isfinite(resolved_elapsed) or resolved_elapsed < 0.0:
            raise ValueError("elapsed must be a non-negative finite number")
        if isinstance(frame_index, bool):
            raise ValueError("frameIndex must be a non-negative integer")
        try:
            resolved_frame_index = int(frame_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("frameIndex must be a non-negative integer") from exc
        if resolved_frame_index < 0 or resolved_frame_index != frame_index:
            raise ValueError("frameIndex must be a non-negative integer")
        live_params = dict(params or {})
        _validate_browser_parameters(live_params)
        live_updates = {
            name: value
            for name, value in live_params.items()
            if name not in instance.animation.params
            or not _same_parameter_value(instance.animation.params[name], value)
        }
        resolved_wall_time = _validated_wall_time(wall_time)

        started = time.perf_counter()
        if live_updates:
            instance.animation.update_parameters(live_updates)
        output = self._generate(
            instance, resolved_elapsed, resolved_frame_index, resolved_wall_time
        )
        changed = bool(getattr(output, "changed", True))
        pixels = getattr(output, "pixels", output)
        if instance.spec.frame_format == "rgb":
            refresh_pending = instance.animation.framework_plant_modifier_refresh_pending()
            pixels = instance.animation.apply_framework_plant_modifiers(
                pixels, changed=changed
            )
            if instance.animation.framework_plant_modifiers_active():
                changed = changed or refresh_pending
        canonical = np.asarray(pixels)
        channels = 4 if instance.spec.frame_format == "premultiplied-rgba" else 3
        expected_shape = (instance.controller.total_leds, channels)
        if canonical.shape != expected_shape:
            raise ValueError(
                f"plugin returned frame shape {canonical.shape}; expected {expected_shape} "
                f"for role {instance.spec.role!r}"
            )
        if canonical.dtype != np.uint8:
            raise ValueError(
                f"plugin returned frame dtype {canonical.dtype}; expected uint8"
            )
        if channels == 4 and np.any(canonical[:, :3] > canonical[:, 3:4]):
            raise ValueError("overlay plugin returned non-premultiplied RGBA pixels")
        if not canonical.flags.c_contiguous:
            canonical = np.ascontiguousarray(canonical)
        # No transpose or row flip: bytes stay canonical strip-major RGB/RGBA.
        instance.frame_bytes = canonical.tobytes(order="C")
        self._last_instance_id = resolved_instance_id
        render_ms = (time.perf_counter() - started) * 1000.0
        return {
            "engine": ENGINE,
            "instanceId": resolved_instance_id,
            "role": instance.spec.role,
            "frameFormat": instance.spec.frame_format,
            "width": instance.controller.strip_count,
            "height": instance.controller.leds_per_strip,
            "changed": changed,
            "renderMs": render_ms,
            "wallClockFixed": resolved_wall_time is not None,
        }

    def interact(
        self,
        kind: Any,
        *,
        instance_id: Optional[str] = None,
        x: Any = None,
        y: Any = None,
        strength: Any = 1.0,
        direction: Any = None,
    ) -> Dict[str, Any]:
        """Dispatch one explicit input to an isolated browser animation.

        This adapter owns no controller and deliberately has no path to the
        host command channel.  The method is intentionally narrow: point input
        maps only to an animation's declared ``primary`` interaction and
        directional input maps only to a declared local ``handle_input`` hook.
        """
        resolved_instance_id = _validated_instance_id(instance_id)
        instance = self._instances.get(resolved_instance_id)
        if instance is None:
            raise RuntimeError(
                f"Python browser preview instance {resolved_instance_id!r} is not initialized"
            )
        if kind == "point":
            supported = getattr(instance.animation, "INTERACTION_TYPES", frozenset())
            if "primary" not in supported:
                raise ValueError("this local preview does not support point input")
            resolved_x = _validated_preview_coordinate(
                x, "x", instance.controller.strip_count
            )
            resolved_y = _validated_preview_coordinate(
                y, "y", instance.controller.leds_per_strip
            )
            if isinstance(strength, bool) or not isinstance(strength, (int, float)):
                raise ValueError("strength must be numeric")
            resolved_strength = float(strength)
            if not math.isfinite(resolved_strength) or not 0.0 < resolved_strength <= 1.0:
                raise ValueError("strength must be finite and from 0 to 1")
            handler = getattr(instance.animation, "handle_interaction", None)
            if not callable(handler):
                raise ValueError("this local preview has no point-input handler")
            accepted = bool(handler("primary", resolved_x, resolved_y, resolved_strength))
            return {
                "engine": ENGINE,
                "instanceId": resolved_instance_id,
                "kind": "point",
                "accepted": accepted,
            }
        if kind == "direction":
            if not isinstance(direction, str) or direction not in _DIRECTIONAL_INPUTS:
                raise ValueError("direction is not supported")
            declared = getattr(instance.animation, "COMPOSER_INTERACTIONS", {})
            allowed = declared.get("directions", ()) if isinstance(declared, Mapping) else ()
            if direction not in allowed:
                raise ValueError("this local preview does not support that direction")
            handler = getattr(instance.animation, "handle_input", None)
            if not callable(handler):
                raise ValueError("this local preview has no directional-input handler")
            handler(direction)
            return {
                "engine": ENGINE,
                "instanceId": resolved_instance_id,
                "kind": "direction",
                "direction": direction,
                "accepted": True,
            }
        raise ValueError("unsupported local interaction kind")

    def initialize_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.initialize(
            payload.get("pluginId"),
            payload.get("className"),
            payload.get("geometry"),
            payload.get("params"),
            payload.get("instanceId"),
            payload.get("installationProfileDigest"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    def render_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.render(
            payload.get("elapsed"),
            payload.get("frameIndex"),
            payload.get("params"),
            payload.get("instanceId"),
            payload.get("wallTime"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    def interact_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.interact(
            payload.get("kind"),
            instance_id=payload.get("instanceId"),
            x=payload.get("x"),
            y=payload.get("y"),
            strength=payload.get("strength", 1.0),
            direction=payload.get("direction"),
        )
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    def render_batch_json(self, payload_json: str) -> str:
        """Render distinct instances through one bounded JSON/bytes bridge."""
        payload = json.loads(payload_json)
        renders = payload.get("renders") if isinstance(payload, Mapping) else None
        if (
            not isinstance(renders, list)
            or not renders
            or len(renders) > MAX_RUNTIME_INSTANCES
        ):
            raise ValueError(
                f"renders must contain 1-{MAX_RUNTIME_INSTANCES} render requests"
            )
        instance_ids = []
        for request in renders:
            if not isinstance(request, Mapping):
                raise ValueError("each batch render request must be an object")
            instance_ids.append(_validated_instance_id(request.get("instanceId")))
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("batch renders require distinct instance IDs")

        self._batch_frame_bytes = b""
        results = []
        frames = []
        offset = 0
        for request, instance_id in zip(renders, instance_ids):
            result = self.render(
                request.get("elapsed"),
                request.get("frameIndex"),
                request.get("params"),
                instance_id,
                request.get("wallTime"),
            )
            frame = self.frame_bytes_for(instance_id)
            result.update(byteOffset=offset, byteLength=len(frame))
            results.append(result)
            frames.append(frame)
            offset += len(frame)
        self._batch_frame_bytes = b"".join(frames)
        return json.dumps(results, separators=(",", ":"), sort_keys=True)

    def dispose_json(self, payload_json: str) -> str:
        payload = json.loads(payload_json)
        result = self.dispose_instance(payload.get("instanceId"))
        return json.dumps(result, separators=(",", ":"), sort_keys=True)


__all__ = [
    "BrowserPreviewRuntime", "DEFAULT_INSTANCE_ID", "ENGINE", "PLUGIN_SPECS",
    "MAX_RUNTIME_INSTANCES", "PreviewController", "SUPPORTED_PLUGINS",
    "normalize_geometry",
]
