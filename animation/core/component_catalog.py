"""Normalized component descriptors for the host animation catalog.

Manifest discovery deliberately stops at JSON metadata.  Python classes are
only inspected by :func:`bind_python_implementation` after the plugin loader has
already imported a selected implementation.  This keeps catalog discovery
side-effect free while still exposing the parameter schema needed by the live
host once loading is complete.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Type

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT

from .base import AnimationBase, StatefulAnimationBase
from .presentation_contracts import (
    COMPONENT_DESCRIPTOR_SCHEMA,
    COMPONENT_DESCRIPTOR_VERSION,
    NEXT_DEADLINE_SEMANTICS,
    CadenceContract,
    ComponentDescriptor,
    ComponentProvider,
    ComponentRole,
    TimingAdapter,
    VIBE_CAPABILITIES,
    VIBE_COLOR_POLICIES,
)


_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PYTHON_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_EXPLICIT_COMPONENT_FIELDS = frozenset(
    {"provider", "role", "entrypoint", "cadence"}
)
_IMPLEMENTATION_OWNED_FIELDS = frozenset(
    {"parameter_schema", "defaults", "controls"}
)


def validate_and_normalize_manifest(
    payload: Dict[str, Any], manifest_path: Path, plugin_id: str
) -> Dict[str, Any]:
    """Validate the versioned Python component subset without importing code.

    Legacy manifests omit every explicit component field and are normalized to
    descriptor version 1.  Once any component field is authored, version 1 and
    the complete provider/role/entrypoint/cadence set are required.
    """
    if not _PLUGIN_ID.fullmatch(plugin_id):
        raise ValueError(f"invalid plugin package ID {plugin_id!r}: {manifest_path}")
    if payload.get("plugin_id") != plugin_id:
        raise ValueError(
            f"manifest plugin_id must match package directory {plugin_id!r}: "
            f"{manifest_path}"
        )

    class_name = payload.get("class")
    if not isinstance(class_name, str) or not _PYTHON_CLASS.fullmatch(class_name):
        raise ValueError(
            f"manifest class must be a Python class identifier: {manifest_path}"
        )
    icon = payload.get("icon")
    if not isinstance(icon, str) or not icon.strip():
        raise ValueError(f"manifest icon must be a non-empty string: {manifest_path}")
    if payload.get("gallery", "show") not in {"show", "test"}:
        raise ValueError(f"manifest gallery must be 'show' or 'test': {manifest_path}")

    implementation_owned = _IMPLEMENTATION_OWNED_FIELDS.intersection(payload)
    if implementation_owned:
        raise ValueError(
            "manifest component controls must be declared by the Python "
            f"implementation, not {sorted(implementation_owned)}: {manifest_path}"
        )

    authored_fields = _EXPLICIT_COMPONENT_FIELDS.intersection(payload)
    manifest_version = payload.get("manifest_version")
    if authored_fields:
        missing = _EXPLICIT_COMPONENT_FIELDS.difference(payload)
        if missing:
            raise ValueError(
                "explicit component manifest must declare manifest_version and "
                f"provider/role/entrypoint/cadence; missing {sorted(missing)}: "
                f"{manifest_path}"
            )
    elif manifest_version is not None:
        raise ValueError(
            "manifest_version is only valid with explicit "
            f"provider/role/entrypoint/cadence: {manifest_path}"
        )

    if authored_fields:
        if payload["provider"] != ComponentProvider.PYTHON.value:
            raise ValueError(
                "manifest provider must be 'python' in the host component loader: "
                f"{manifest_path}"
            )
        if payload["role"] not in {role.value for role in ComponentRole}:
            raise ValueError(
                "manifest role must be background, overlay, or full_scene: "
                f"{manifest_path}"
            )
        expected_entrypoint = f"animation.plugins.{plugin_id}:{class_name}"
        if payload["entrypoint"] != expected_entrypoint:
            raise ValueError(
                f"manifest entrypoint must be {expected_entrypoint!r}: {manifest_path}"
            )
        payload["cadence"] = normalize_cadence(payload["cadence"], manifest_path)
        if manifest_version != COMPONENT_DESCRIPTOR_VERSION or isinstance(
            manifest_version, bool
        ):
            raise ValueError(
                "explicit component manifest_version must be 1: "
                f"{manifest_path}"
            )

    # Copying here prevents later loader binding from mutating caller-owned JSON.
    normalized = _json_copy(payload, f"manifest {manifest_path}")
    normalized["manifest_version"] = COMPONENT_DESCRIPTOR_VERSION
    normalized["_legacy_component_manifest"] = not bool(authored_fields)
    return normalized


def normalize_cadence(value: Any, manifest_path: Path) -> Dict[str, Any]:
    """Validate the bounded v1 fixed-FPS or event-driven cadence shape."""
    if not isinstance(value, dict):
        raise ValueError(f"manifest cadence must be an object: {manifest_path}")
    allowed = {"mode", "preferred_fps", "next_deadline_semantics"}
    unknown = set(value).difference(allowed)
    if unknown:
        raise ValueError(
            f"manifest cadence has unsupported keys {sorted(unknown)}: {manifest_path}"
        )
    mode = value.get("mode")
    preferred_fps = value.get("preferred_fps")
    deadline = value.get("next_deadline_semantics", NEXT_DEADLINE_SEMANTICS)
    try:
        contract = CadenceContract(
            mode=mode,
            preferred_fps=preferred_fps,
            next_deadline_semantics=deadline,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid manifest cadence: {manifest_path}: {exc}") from exc
    normalized = {
        "mode": contract.mode.value,
        "next_deadline_semantics": contract.next_deadline_semantics,
    }
    if contract.preferred_fps is not None:
        normalized["preferred_fps"] = contract.preferred_fps
    return normalized


def scanned_descriptor(
    plugin_id: str,
    manifest: Optional[Mapping[str, Any]],
    *,
    flat_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create conservative safe metadata for a scanned package or flat plugin."""
    payload = dict(manifest or {})
    legacy = True if manifest is None else bool(payload.get("_legacy_component_manifest"))
    class_name = payload.get("class")
    explicit = not legacy
    role = payload.get("role", "full_scene" if plugin_id == "clock" else "background")
    entrypoint = payload.get("entrypoint")
    if not entrypoint:
        if class_name:
            entrypoint = f"animation.plugins.{plugin_id}:{class_name}"
        else:
            entrypoint = f"{plugin_id}:<implementation-unloaded>"
    cadence_payload = payload.get("cadence") or {
        "mode": "event_driven",
        "next_deadline_semantics": NEXT_DEADLINE_SEMANTICS,
    }
    vibe = payload.get("vibe") if isinstance(payload.get("vibe"), dict) else {}

    if flat_file is not None:
        classification = "external_flat_plugin"
        diagnostic = "External Python implementation has not been classified."
    elif plugin_id == "clock":
        classification = "legacy_clock"
        diagnostic = "Compatibility Clock owns the complete scene; use clock_overlay for composition."
    elif explicit:
        classification = "declared_component"
        diagnostic = "Declared Python component is awaiting implementation binding."
    else:
        classification = "legacy_animation"
        diagnostic = "Legacy Python component is awaiting implementation classification."

    descriptor = _descriptor_dict(
        plugin_id=plugin_id,
        name=_display_name(plugin_id),
        description="Python animation component",
        icon=payload.get("icon", "✨"),
        gallery=payload.get("gallery", "show"),
        provider=payload.get("provider", "python"),
        role=role,
        entrypoint=entrypoint,
        parameter_schema={},
        defaults={},
        cadence=cadence_payload,
        timing_adapter=vibe.get("timing_adapter", TimingAdapter.LEGACY_SPEED_PARAM.value),
        vibe_color_policy=vibe.get("color_policy", "preserve"),
        vibe_capabilities=tuple(vibe.get("capabilities", ())),
        preview=payload.get("preview", {}),
    )
    descriptor["compatibility"] = {
        "legacy_manifest": legacy,
        "classification": classification,
        # No implementation is assumed composable merely from directory JSON.
        "composable": False,
        "implementation_loaded": False,
        "parameter_metadata": "implementation_not_loaded",
        "diagnostic": diagnostic,
    }
    return descriptor


def bind_python_implementation(
    descriptor: Mapping[str, Any], animation_class: Type[AnimationBase]
) -> Dict[str, Any]:
    """Enrich one scanned descriptor after its Python class has been loaded."""
    if not isinstance(animation_class, type) or not issubclass(
        animation_class, AnimationBase
    ):
        raise TypeError("component implementation must inherit AnimationBase")

    class _CatalogController:
        strip_count = DEFAULT_STRIP_COUNT
        leds_per_strip = DEFAULT_LEDS_PER_STRIP
        total_leds = strip_count * leds_per_strip
        debug = False

    instance = animation_class(_CatalogController())
    schema = _normalize_parameter_schema(instance.get_parameter_schema())
    authored_defaults = instance.authored_params_snapshot()
    # Existing plugins occasionally refined their constructor defaults without
    # updating the descriptive schema inherited from AnimationBase.  The loaded
    # descriptor is an execution contract, so its defaults must reproduce actual
    # no-config construction rather than silently changing behavior in a scene.
    for name, definition in schema.items():
        if name in authored_defaults:
            definition["default"] = _json_copy(
                authored_defaults[name], f"component default {name}"
            )
    defaults = {name: definition["default"] for name, definition in schema.items()}
    plugin_id = str(descriptor["plugin_id"])
    declared_role = str(descriptor["role"])
    is_stateful = issubclass(animation_class, StatefulAnimationBase)
    role = "full_scene" if is_stateful or plugin_id == "clock" else declared_role
    composable = role in {"background", "overlay"} and not is_stateful

    if is_stateful:
        classification = "stateful_animation"
        diagnostic = (
            "StatefulAnimationBase controls complete-output timing and cannot be "
            "used in a composed host scene."
        )
    elif role == "full_scene":
        classification = "full_scene_component"
        diagnostic = "Full-scene compatibility component cannot be composed."
    elif descriptor["compatibility"]["legacy_manifest"]:
        classification = "animation_base"
        diagnostic = "Legacy AnimationBase component classified through the Python adapter."
    else:
        classification = "declared_component"
        diagnostic = "Versioned Python component validated and bound."

    rebound = _json_copy(descriptor, f"descriptor {plugin_id}")
    rebound.update(
        {
            "name": str(getattr(animation_class, "ANIMATION_NAME", animation_class.__name__)),
            "description": str(
                getattr(animation_class, "ANIMATION_DESCRIPTION", "No description")
            ),
            "entrypoint": f"{animation_class.__module__}:{animation_class.__name__}",
            "role": role,
            "parameter_schema": schema,
            "defaults": defaults,
        }
    )
    rebound["compatibility"].update(
        {
            "classification": classification,
            "composable": composable,
            "implementation_loaded": True,
            "parameter_metadata": "loaded",
            "diagnostic": diagnostic,
        }
    )
    return rebound


def painter_descriptor() -> Dict[str, Any]:
    """Return the catalog-only descriptor for the existing painter output mode."""
    descriptor = _descriptor_dict(
        plugin_id="painter",
        name="Painter",
        description="Direct manually-authored complete-frame output",
        icon="🎨",
        gallery="test",
        provider="python",
        role="full_scene",
        entrypoint="compatibility:painter",
        parameter_schema={},
        defaults={},
        cadence={
            "mode": "event_driven",
            "next_deadline_semantics": NEXT_DEADLINE_SEMANTICS,
        },
        timing_adapter=TimingAdapter.WALL_CLOCK.value,
        vibe_color_policy="preserve",
        vibe_capabilities=(),
        preview={},
    )
    descriptor["compatibility"] = {
        "legacy_manifest": True,
        "classification": "painter",
        "composable": False,
        "implementation_loaded": True,
        "parameter_metadata": "not_applicable",
        "diagnostic": "Painter owns complete output and is isolated from composed scenes.",
    }
    return descriptor


def filter_catalog(
    descriptors: Iterable[Mapping[str, Any]],
    *,
    provider: Optional[str] = None,
    role: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Return deterministic JSON copies matching optional provider and role."""
    if provider is not None:
        try:
            provider = ComponentProvider(provider).value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported component provider {provider!r}") from exc
    if role is not None:
        try:
            role = ComponentRole(role).value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported component role {role!r}") from exc
    return [
        _json_copy(item, f"descriptor {item.get('plugin_id', '?')}")
        for item in sorted(descriptors, key=lambda value: str(value["plugin_id"]))
        if (provider is None or item["provider"] == provider)
        and (role is None or item["role"] == role)
    ]


def color_policy_inventory(
    descriptors: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Generate a deterministic policy audit from a unified component catalog."""
    components = []
    identities = set()
    counts = {policy: 0 for policy in sorted(VIBE_COLOR_POLICIES)}
    for descriptor in sorted(
        descriptors,
        key=lambda value: (
            str(value.get("provider", "")),
            str(value.get("plugin_id", "")),
        ),
    ):
        identity = (descriptor.get("provider"), descriptor.get("plugin_id"))
        if identity in identities:
            raise ValueError(f"duplicate component descriptor identity {identity!r}")
        identities.add(identity)
        policy = descriptor.get("vibe_color_policy")
        if policy not in VIBE_COLOR_POLICIES:
            raise ValueError(
                f"component {identity!r} lacks an explicit canonical color policy"
            )
        capabilities = tuple(sorted(descriptor.get("vibe_capabilities", ())))
        unsupported = set(capabilities).difference(VIBE_CAPABILITIES)
        if unsupported:
            raise ValueError(
                f"component {identity!r} has unsupported vibe capabilities "
                f"{sorted(unsupported)}"
            )
        if policy == "semantic" and "palette_roles" not in capabilities:
            raise ValueError(
                f"semantic component {identity!r} must claim palette_roles"
            )
        if policy == "preserve" and "palette_roles" in capabilities:
            raise ValueError(
                f"preserve component {identity!r} cannot claim palette_roles"
            )
        counts[policy] += 1
        components.append({
            "plugin_id": identity[1],
            "provider": identity[0],
            "role": descriptor.get("role"),
            "color_policy": policy,
            "vibe_capabilities": list(capabilities),
            "vibe_enabled": bool(capabilities),
        })
    return {
        "schema": "ledgrid.component-color-policy-inventory",
        "component_count": len(components),
        "counts": counts,
        "components": components,
    }


def validate_parameter_overrides(
    descriptor: Mapping[str, Any], values: Mapping[str, Any]
) -> Dict[str, Any]:
    """Reject component controls that its loaded implementation did not declare."""
    if not isinstance(values, Mapping):
        raise TypeError("component controls must be an object")
    compatibility = descriptor.get("compatibility", {})
    if compatibility.get("parameter_metadata") != "loaded":
        raise ValueError(
            f"component {descriptor.get('plugin_id')!r} has no loaded parameter schema"
        )
    schema = descriptor.get("parameter_schema", {})
    unknown = set(values).difference(schema)
    if unknown:
        raise ValueError(
            f"component controls contain undeclared parameters {sorted(unknown)}"
        )
    return _json_copy(dict(values), "component controls")


def _descriptor_dict(
    *,
    plugin_id: str,
    name: str,
    description: str,
    icon: str,
    gallery: str,
    provider: str,
    role: str,
    entrypoint: str,
    parameter_schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
    cadence: Mapping[str, Any],
    timing_adapter: str,
    vibe_color_policy: str,
    vibe_capabilities: tuple[str, ...],
    preview: Mapping[str, Any],
) -> Dict[str, Any]:
    cadence_contract = CadenceContract(
        cadence["mode"],
        preferred_fps=cadence.get("preferred_fps"),
        next_deadline_semantics=cadence.get(
            "next_deadline_semantics", NEXT_DEADLINE_SEMANTICS
        ),
    )
    validated = ComponentDescriptor(
        manifest_version=COMPONENT_DESCRIPTOR_VERSION,
        plugin_id=plugin_id,
        name=name,
        description=description,
        icon=icon,
        gallery=gallery,
        provider=provider,
        role=role,
        entrypoint=entrypoint,
        parameter_schema=parameter_schema,
        defaults=defaults,
        cadence=cadence_contract,
        timing_adapter=timing_adapter,
        vibe_color_policy=vibe_color_policy,
        vibe_capabilities=vibe_capabilities,
        installation_profile_requirements=(),
        preview=preview,
        build={},
    )
    return {
        "schema": COMPONENT_DESCRIPTOR_SCHEMA,
        "manifest_version": validated.manifest_version,
        "plugin_id": validated.plugin_id,
        "name": validated.name,
        "description": validated.description,
        "icon": validated.icon,
        "gallery": validated.gallery,
        "provider": validated.provider.value,
        "role": validated.role.value,
        "entrypoint": validated.entrypoint,
        "parameter_schema": _json_copy(parameter_schema, "parameter_schema"),
        "defaults": _json_copy(defaults, "defaults"),
        "cadence": {
            "mode": cadence_contract.mode.value,
            **(
                {"preferred_fps": cadence_contract.preferred_fps}
                if cadence_contract.preferred_fps is not None
                else {}
            ),
            "next_deadline_semantics": cadence_contract.next_deadline_semantics,
        },
        "timing_adapter": validated.timing_adapter.value,
        "vibe_color_policy": validated.vibe_color_policy,
        "vibe_capabilities": list(validated.vibe_capabilities),
        "installation_profile_requirements": [],
        "preview": _json_copy(preview, "preview"),
        "build": {},
    }


def _normalize_parameter_schema(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("component parameter schema must be an object")
    normalized: Dict[str, Dict[str, Any]] = {}
    for name, definition in value.items():
        if not isinstance(name, str) or not _PARAMETER_ID.fullmatch(name):
            raise ValueError(f"component parameter name must be an identifier: {name!r}")
        if not isinstance(definition, Mapping):
            raise TypeError(f"component parameter {name!r} schema must be an object")
        if "default" not in definition:
            raise ValueError(f"component parameter {name!r} must declare a default")
        normalized[name] = _json_copy(dict(definition), f"parameter_schema.{name}")
    return normalized


def _json_copy(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON-compatible values") from exc


def _display_name(plugin_id: str) -> str:
    return " ".join(part.capitalize() for part in plugin_id.split("_"))
