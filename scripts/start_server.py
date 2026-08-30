#!/usr/bin/env python3
"""
LED Animation Server Startup Script

Supports running either the controller process (hardware + animation loop) or
the web/preview UI as separate Python processes that communicate via files.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Add repo root to Python path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animation.core.manager import AnimationManager
from animation.core.native_background_library import NativeBackgroundLibrary
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_runtime import (
    EMPTY_INSTALLATION_PROFILE_DIGEST,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
)
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import (
    ControllerActivationConflictError,
    ControllerActivationError,
    controller_activation_coordinator,
    restore_display_state as _restore_display_state,
    start_scene as _start_scene,
    update_scene_component as _update_scene_component,
)
from drivers.led_layout import (
    DEFAULT_STRIP_COUNT,
    DEFAULT_LEDS_PER_STRIP,
    STRIPS_PER_DEVICE,
    WALL_PHYSICAL_LANE_ORDER,
    WALL_PHYSICAL_OUTPUT_LANE_MASKS,
    WALL_RECEIVER_GLOBAL_STRIP_OFFSETS,
    WALL_RECEIVER_STRIP_COUNTS,
    WALL_RECEIVER_SPI_SPEEDS_HZ,
    WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER,
    WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
    default_strip_count,
    device_count_for_strips,
)
from web.app import create_app
from web.maintenance_api import MaintenanceRequest, MaintenanceRequestError, MaintenanceRunError
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from tools.deployment.preserve_deploy_settings import (
    load_saved_state,
    receiver_hybrid_canary_enabled,
    receiver_hybrid_provider_policy,
    RECEIVER_NATIVE_MODULES_CANARY_ENV,
    receiver_native_modules_canary_enabled,
    save_status,
)
from tools.deployment.receiver_identity_authority import (
    ReceiverIdentityAuthorityError,
    load_receiver_identity_authority,
)
try:
    from tools.deployment.receiver_hybrid_config import (
        DEFAULT_PHYSICAL_LANE_ORDER,
        DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
        DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS,
        DEFAULT_RECEIVER_STRIP_COUNTS,
        DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
        DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER,
        OFF_RECEIVER_HYBRID_CONFIG,
        resolve_receiver_hybrid_config,
    )
except ImportError:  # Compatibility with an older deployed helper lane.
    DEFAULT_PHYSICAL_LANE_ORDER = WALL_PHYSICAL_LANE_ORDER
    DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER = (
        WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER
    )
    DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER = (
        WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
    )
    DEFAULT_RECEIVER_STRIP_COUNTS = WALL_RECEIVER_STRIP_COUNTS
    DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS = WALL_RECEIVER_GLOBAL_STRIP_OFFSETS
    DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS = WALL_PHYSICAL_OUTPUT_LANE_MASKS
    OFF_RECEIVER_HYBRID_CONFIG = {
        "enabled": False,
        "transport_policy": "off",
        "firmware_environment": "esp32-s3-devkitc-1",
    }

    def resolve_receiver_hybrid_config(_root):
        return OFF_RECEIVER_HYBRID_CONFIG


RELEASE_METADATA = ".release.json"
RELEASE_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
FEC_RECEIVER_IDS_ENV = "LEDGRID_FEC_RECEIVER_IDS"


def resolve_active_release_id(project_root: Path) -> str | None:
    """Return the verified immutable release selected by ``current``.

    A checkout using the legacy root layout has no release metadata and returns
    ``None``. Once a release marker exists, startup fails closed unless the
    marker, content-addressed directory, and deployment root's ``current``
    symlink all identify the same release.
    """
    root = project_root.resolve()
    metadata_path = root / RELEASE_METADATA
    deploy_root = root.parent.parent if root.parent.name == "releases" else None
    current_path = deploy_root / "current" if deploy_root is not None else None

    if not metadata_path.exists():
        if current_path is not None and current_path.is_symlink():
            try:
                selected = current_path.resolve(strict=True)
            except OSError as exc:
                raise RuntimeError(f"active release symlink is invalid: {exc}") from exc
            if selected == root:
                raise RuntimeError(f"active release is missing {RELEASE_METADATA}")
        return None
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise RuntimeError(f"{RELEASE_METADATA} must be a regular file")

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read active release metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("active release metadata must be a JSON object")

    release_id = payload.get("id")
    digest = payload.get("digest")
    if (
        not isinstance(release_id, str)
        or RELEASE_ID_PATTERN.fullmatch(release_id) is None
        or digest != release_id
    ):
        raise RuntimeError("active release metadata has an invalid identity")
    if root.name != release_id or root.parent.name != "releases":
        raise RuntimeError("active release identity does not match its directory")

    assert current_path is not None
    if not current_path.is_symlink():
        raise RuntimeError("active release has no current symlink")
    try:
        selected = current_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"active release symlink is invalid: {exc}") from exc
    if selected != root:
        raise RuntimeError("release is not selected by current")
    return release_id

# Spread the eight I80 rising edges across the 3-sample WS2812 symbol. One
# phase is the original all-lanes-together waveform (kStaggerOff).
PRODUCTION_STAGGER_PHASES = 3

# Try to import the real LED controller, fall back to mock for testing
try:
    from drivers.multi_device import MultiDeviceLEDController as LEDController
except ImportError:
    try:
        from drivers.spi_controller import LEDController
    except ImportError:
        class LEDController:
            def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP, **kwargs):
                self.strip_count = strips
                self.leds_per_strip = leds_per_strip
                self.total_leds = strips * leds_per_strip
                self.debug = kwargs.get('debug', False)
                self.inline_show = True
                print(f"🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total")

            def set_all_pixels(self, *_args, **_kwargs):
                pass

            def show(self):
                pass

            def clear(self):
                pass

            def configure(self):
                pass

            def set_stagger_phases(self, phases):
                pass


def apply_production_stagger(controller, phases: int = PRODUCTION_STAGGER_PHASES) -> bool:
    """Enable WS2812 edge staggering on a live controller. Safe no-op on mocks."""
    if not hasattr(controller, "set_stagger_phases"):
        return False
    controller.set_stagger_phases(phases)
    return True


def _has_finalized_receiver_topology_authority(receiver_hybrid_config) -> bool:
    """Recognize current durable state and its pre-geometry canary shape."""

    if _receiver_hybrid_config_value(receiver_hybrid_config, "enabled", False) is True:
        return True
    expected_count = len(DEFAULT_RECEIVER_STRIP_COUNTS)
    for field in (
        "physical_lane_order",
        "reverse_strips_by_logical_receiver",
        "reverse_native_strips_by_logical_receiver",
    ):
        value = _receiver_hybrid_config_value(receiver_hybrid_config, field, None)
        if isinstance(value, (list, tuple)) and len(value) == expected_count:
            return True
    return False


def receiver_geometry_for_runtime(strip_count: int, receiver_hybrid_config):
    """Resolve semantic widths, logical offsets, and physical lane masks.

    The feature-off path retains derived legacy/HAT geometry. An explicit
    finalized receiver rollout uses the complete target-owned topology; lane
    masks are never inferred from logical width when that authority exists.
    """
    strips_per_device = 8
    num_devices = device_count_for_strips(strip_count, strips_per_device)
    widths = tuple(
        min(strips_per_device, strip_count - index * strips_per_device)
        for index in range(num_devices)
    )
    offsets = tuple(index * strips_per_device for index in range(num_devices))
    masks = tuple((1 << width) - 1 for width in widths)
    configured_widths = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config, "receiver_strip_counts", ()
    ))
    configured_offsets = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config, "receiver_global_strip_offsets", ()
    ))
    configured_masks = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config, "physical_output_lane_masks", ()
    ))
    configured_values_valid = (
        len(configured_widths)
        == len(configured_offsets)
        == len(configured_masks)
        == num_devices
        and all(type(width) is int and 1 <= width <= 8 for width in configured_widths)
        and all(type(offset) is int and offset >= 0 for offset in configured_offsets)
        and all(
            type(mask) is int
            and 1 <= mask <= 0xFF
            and mask.bit_count() >= width
            for mask, width in zip(configured_masks, configured_widths)
        )
        and sum(configured_widths) == strip_count
    )
    configured_ranges = set()
    if configured_values_valid:
        configured_ranges = {
            strip
            for offset, width in zip(configured_offsets, configured_widths)
            for strip in range(offset, offset + width)
        }
    exact_configured_topology = (
        configured_values_valid
        and configured_ranges == set(range(strip_count))
    )
    if exact_configured_topology:
        return (
            num_devices,
            configured_widths,
            configured_offsets,
            configured_masks,
        )
    finalized_defaults = (
        len(DEFAULT_RECEIVER_STRIP_COUNTS),
        DEFAULT_RECEIVER_STRIP_COUNTS,
        DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS,
        DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
    )
    if (
        strip_count == sum(DEFAULT_RECEIVER_STRIP_COUNTS)
        and num_devices == len(DEFAULT_RECEIVER_STRIP_COUNTS)
        and _has_finalized_receiver_topology_authority(receiver_hybrid_config)
    ):
        # Compatibility for the explicit pre-geometry canary shape, which
        # carried all independent wiring domains but not the geometry fields.
        return finalized_defaults
    return num_devices, widths, offsets, masks


def receiver_wiring_for_runtime(
    geometry, receiver_hybrid_config, installation_profile_topology
):
    """Use target-owned wiring only when its complete geometry was selected."""

    _count, widths, offsets, masks = geometry
    configured_geometry_selected = (
        widths
        == tuple(_receiver_hybrid_config_value(
            receiver_hybrid_config, "receiver_strip_counts", ()
        ))
        and offsets
        == tuple(_receiver_hybrid_config_value(
            receiver_hybrid_config, "receiver_global_strip_offsets", ()
        ))
        and masks
        == tuple(_receiver_hybrid_config_value(
            receiver_hybrid_config, "physical_output_lane_masks", ()
        ))
    )
    finalized_authority_selected = (
        geometry
        == (
            len(DEFAULT_RECEIVER_STRIP_COUNTS),
            DEFAULT_RECEIVER_STRIP_COUNTS,
            DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS,
            DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
        )
        and _has_finalized_receiver_topology_authority(receiver_hybrid_config)
    )
    return (
        installation_profile_topology
        if configured_geometry_selected or finalized_authority_selected
        else IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
    )


def receiver_identity_authority_for_startup(
    project_root: Path, *, logical_to_transport_routes
):
    """Load one frozen receiver roster before opening any SPI controller.

    The target-owned authority has already checked the current hybrid topology
    and firmware inventory.  Startup still compares its ordered routes with
    the graph it is about to construct, so a configuration drift cannot be
    hidden by controller enumeration.
    """

    try:
        authority = load_receiver_identity_authority(project_root)
    except ReceiverIdentityAuthorityError as exc:
        raise RuntimeError("receiver identity authority is unavailable") from exc
    expected_routes = tuple(tuple(route) for route in logical_to_transport_routes)
    observed_routes = tuple(identity.spi_route for identity in authority.identities)
    if observed_routes != expected_routes:
        raise RuntimeError(
            "receiver identity authority routes do not match controller topology"
        )
    return authority


def controller_status_payload(
    manager: AnimationManager,
    *,
    release_id: str | None,
    last_command_id: str | None,
    updated_at: float,
) -> dict:
    """Build one controller snapshot with its immutable release identity."""
    payload = manager.get_current_frame()
    payload.update(manager.get_current_status())
    flags = getattr(manager, "feature_flags", None)
    payload['feature_flags'] = (
        flags.to_dict()
        if isinstance(flags, AnimationPipelineFeatureFlags)
        else AnimationPipelineFeatureFlags().to_dict()
    )
    payload['release_id'] = release_id
    payload['last_command_id'] = last_command_id
    payload['updated_at'] = updated_at
    payload.update(controller_activation_coordinator(manager).controller_status())
    maintenance_identity = controller_maintenance_identity(manager.controller)
    if maintenance_identity is not None:
        payload.update(maintenance_identity)
    return payload


def controller_maintenance_identity(controller) -> dict | None:
    """Publish only the injected canonical authority needed by maintenance.

    This is deliberately derived from the controller's immutable startup
    binding, never from receiver discovery or a best-effort status sample.
    Missing or malformed identities are omitted so web callers fail closed.
    """
    identities = getattr(controller, "receiver_identities", None)
    authority_digest = getattr(controller, "receiver_identity_authority_digest", None)
    if (
        not isinstance(identities, tuple)
        or len(identities) != 5
        or not isinstance(authority_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", authority_digest) is None
    ):
        return None
    validator = getattr(controller, "_require_pinned_receipt_roster", None)
    if not callable(validator):
        return None
    try:
        if tuple(validator()) != identities:
            return None
    except Exception:
        return None
    roster = []
    for logical_device, identity in enumerate(identities):
        route = getattr(identity, "spi_route", None)
        serial = getattr(identity, "hardware_serial", None)
        firmware_sha256 = getattr(identity, "firmware_sha256", None)
        if (
            getattr(identity, "logical_device", None) != logical_device
            or not isinstance(route, tuple)
            or not route
            or any(type(item) is not int or item < 0 for item in route)
            or not isinstance(serial, str)
            or not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", serial)
            or not isinstance(firmware_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", firmware_sha256) is None
        ):
            return None
        roster.append({
            "logical_device": logical_device,
            "route": list(route),
            "hardware_serial": serial,
            "firmware_sha256": firmware_sha256,
        })
    return {
        "receiver_identity_authority_digest": authority_digest,
        "receiver_roster": roster,
    }


def persist_controller_restart_state(
    manager: AnimationManager,
    activation_coordinator,
    *,
    presets_dir: Path,
    state_path: Path,
):
    """Persist the exact controller-selected state after a serialized mutation."""

    status = manager.get_current_status()
    status.update(activation_coordinator.controller_status())
    flags = getattr(manager, "feature_flags", None)
    status["feature_flags"] = (
        flags.to_dict()
        if isinstance(flags, AnimationPipelineFeatureFlags)
        else AnimationPipelineFeatureFlags().to_dict()
    )
    return save_status(status, presets_dir, state_path)


def receiver_hybrid_feature_flags(
    enabled: bool, *, receiver_native_modules: bool = False
) -> AnimationPipelineFeatureFlags:
    """Map local and managed-native canaries to independent rollout gates."""

    if type(enabled) is not bool:
        raise TypeError("receiver hybrid canary state must be boolean")
    if type(receiver_native_modules) is not bool:
        raise TypeError("receiver native modules canary state must be boolean")
    if receiver_native_modules and not enabled:
        raise ValueError("receiver native modules require receiver hybrid mode")
    return AnimationPipelineFeatureFlags(
        receiver_local_background=enabled,
        receiver_sparse_overlay=enabled,
        receiver_geometry_profile=enabled,
        receiver_native_modules=receiver_native_modules,
    )


def _receiver_hybrid_config_value(config, name, default=None):
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def receiver_native_modules_for_runtime(config) -> bool:
    """Use durable current state unless an explicit environment override exists."""

    durable = bool(_receiver_hybrid_config_value(
        config, "native_modules_enabled", False
    ))
    if RECEIVER_NATIVE_MODULES_CANARY_ENV in os.environ:
        return receiver_native_modules_canary_enabled()
    return durable


def receiver_fec_ids_for_runtime(value: str | None = None) -> tuple[int, ...]:
    """Parse an explicit, fail-closed logical-receiver FEC allowlist."""
    if value is None:
        value = os.environ.get(FEC_RECEIVER_IDS_ENV, "")
    if not isinstance(value, str):
        raise TypeError(f"{FEC_RECEIVER_IDS_ENV} must be text")
    if value == "":
        return ()
    tokens = value.split(",")
    if any(not token.isdecimal() or str(int(token)) != token for token in tokens):
        raise ValueError(
            f"{FEC_RECEIVER_IDS_ENV} must be canonical comma-separated logical IDs"
        )
    receiver_ids = tuple(int(token) for token in tokens)
    if len(set(receiver_ids)) != len(receiver_ids):
        raise ValueError(f"{FEC_RECEIVER_IDS_ENV} cannot contain duplicate IDs")
    return receiver_ids


def resolve_receiver_hybrid_runtime_config(
    project_root: Path, *, explicit_enabled=None
):
    """Resolve durable rollout state with the legacy CLI/env as strict overrides."""

    durable = resolve_receiver_hybrid_config(project_root)
    explicit = explicit_enabled
    if explicit is None:
        return durable
    enabled = receiver_hybrid_canary_enabled(explicit)
    if not enabled:
        return OFF_RECEIVER_HYBRID_CONFIG
    if _receiver_hybrid_config_value(durable, "enabled", False) is True:
        return durable
    # Preserve the pre-durable-config one-receiver/all-readable canary.  It is
    # explicit but never selects the installed write-only exception.
    return {
        "enabled": True,
        "transport_policy": "strict_all_readable_v1",
        "firmware_environment": None,
        "native_modules_enabled": False,
        "physical_lane_order": DEFAULT_PHYSICAL_LANE_ORDER,
        "reverse_strips_by_logical_receiver": (
            DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
        ),
        "reverse_native_strips_by_logical_receiver": (
            DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
        ),
        "receiver_strip_counts": DEFAULT_RECEIVER_STRIP_COUNTS,
        "receiver_global_strip_offsets": DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS,
        "physical_output_lane_masks": DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
    }


def _receiver_hybrid_runtime_settings(args):
    config = getattr(args, "receiver_hybrid_config", None)
    if config is not None:
        return config
    return resolve_receiver_hybrid_runtime_config(
        Path(__file__).resolve().parents[1],
        explicit_enabled=getattr(args, "receiver_hybrid_canary", None),
    )


def installation_profile_topology_for_runtime(
    receiver_hybrid_config,
) -> InstallationProfileTopology:
    """Build profile topology without collapsing its four wiring domains."""

    identity = IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
    installed = INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
    physical_lane_order = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config,
        "physical_lane_order",
        identity.physical_lane_order,
    ))
    reverse_host_strips = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config,
        "reverse_strips_by_logical_receiver",
        identity.reverse_host_strips_by_logical_receiver,
    ))
    reverse_native_strips = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config,
        "reverse_native_strips_by_logical_receiver",
        identity.reverse_native_strips_by_logical_receiver,
    ))
    uses_installed_wiring = bool(_receiver_hybrid_config_value(
        receiver_hybrid_config, "enabled", False
    )) or (
        physical_lane_order != identity.physical_lane_order
        or reverse_host_strips != identity.reverse_host_strips_by_logical_receiver
        or reverse_native_strips
        != identity.reverse_native_strips_by_logical_receiver
    )
    default_transport_routes = (
        installed.logical_to_transport_routes
        if uses_installed_wiring
        else identity.logical_to_transport_routes
    )
    transport_routes = tuple(_receiver_hybrid_config_value(
        receiver_hybrid_config,
        "logical_to_transport_routes",
        default_transport_routes,
    ))
    return InstallationProfileTopology(
        # Transport routing remains an independently named hardware authority;
        # it is never copied from physical lane order or either direction map.
        logical_to_transport_routes=transport_routes,
        physical_lane_order=physical_lane_order,
        reverse_host_strips_by_logical_receiver=reverse_host_strips,
        reverse_native_strips_by_logical_receiver=reverse_native_strips,
    )


def installation_profile_startup_context(
    project_root: Path, receiver_hybrid_config, saved_state
):
    """Resolve persisted profile authority before controller construction."""

    topology = installation_profile_topology_for_runtime(receiver_hybrid_config)
    library = InstallationProfileLibrary(
        project_root / "installation_profile_library"
    )
    digest = (
        saved_state.get(
            "installation_profile_digest", EMPTY_INSTALLATION_PROFILE_DIGEST
        )
        if isinstance(saved_state, dict)
        else EMPTY_INSTALLATION_PROFILE_DIGEST
    )
    if digest != EMPTY_INSTALLATION_PROFILE_DIGEST:
        try:
            library.resolve(digest, topology)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(
                f"saved installation profile {digest!r} is unavailable or "
                f"invalid: {exc}"
            ) from exc
    return library, digest, topology


def select_receiver_hybrid_controller(controller, receiver_hybrid_config):
    """Apply an explicitly enabled transport policy to a controller."""

    enabled = bool(_receiver_hybrid_config_value(
        receiver_hybrid_config, "enabled", False
    ))
    policy = _receiver_hybrid_config_value(
        receiver_hybrid_config, "transport_policy", "off"
    )
    physical_lane_order = _receiver_hybrid_config_value(
        receiver_hybrid_config,
        "physical_lane_order",
        DEFAULT_PHYSICAL_LANE_ORDER,
    )
    reverse_strips = _receiver_hybrid_config_value(
        receiver_hybrid_config,
        "reverse_strips_by_logical_receiver",
        DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER,
    )
    reverse_native_strips = _receiver_hybrid_config_value(
        receiver_hybrid_config,
        "reverse_native_strips_by_logical_receiver",
        DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
    )
    if not enabled:
        return controller
    selector = getattr(
        controller, "with_receiver_hybrid_transport_policy", None
    )
    if callable(selector):
        return selector(
            policy,
            physical_lane_order=physical_lane_order,
            reverse_strips_by_logical_receiver=reverse_strips,
            reverse_native_strips_by_logical_receiver=reverse_native_strips,
        )
    if policy not in (None, "", "off", "strict_all_readable_v1"):
        raise RuntimeError(
            "selected receiver hybrid transport requires a multi-device "
            "controller policy facade"
        )
    return controller
def run_controller_mode(args):
    """Controller process: drives LEDs and writes status/frames to disk."""
    receiver_hybrid_config = _receiver_hybrid_runtime_settings(args)
    receiver_hybrid_canary = bool(_receiver_hybrid_config_value(
        receiver_hybrid_config, "enabled", False
    ))
    receiver_native_modules = receiver_native_modules_for_runtime(
        receiver_hybrid_config
    )
    feature_flags = receiver_hybrid_feature_flags(
        receiver_hybrid_canary,
        receiver_native_modules=receiver_native_modules,
    )
    saved_state = None
    try:
        saved_state = load_saved_state(
            Path(args.saved_state_file),
            provider_policy=receiver_hybrid_provider_policy(
                receiver_hybrid_canary,
                receiver_native_modules=receiver_native_modules,
            ),
        )
        print(f"💾 Restart default: {saved_state['animation']}/before-deploy")
    except RuntimeError as exc:
        print(f"ℹ️ No usable saved animation state: {exc}")

    project_root = Path(__file__).resolve().parents[1]
    installation_profile_library, installation_profile_digest, (
        installation_profile_topology
    ) = installation_profile_startup_context(
        project_root, receiver_hybrid_config, saved_state
    )

    # Determine if we're using multi-device or single-device controller
    # Multi-device controller expects total strips, single-device expects strips per device
    if hasattr(LEDController, '__name__') and 'Multi' in LEDController.__name__:
        # Multi-device controller - calculate number of devices from strip count
        strips_per_device = STRIPS_PER_DEVICE
        (
            num_devices,
            receiver_strip_counts,
            receiver_global_strip_offsets,
            receiver_lane_masks,
        ) = receiver_geometry_for_runtime(args.strips, receiver_hybrid_config)
        controller_topology = receiver_wiring_for_runtime(
            (
                num_devices,
                receiver_strip_counts,
                receiver_global_strip_offsets,
                receiver_lane_masks,
            ),
            receiver_hybrid_config,
            installation_profile_topology,
        )
        receiver_authority = receiver_identity_authority_for_startup(
            project_root,
            logical_to_transport_routes=(
                controller_topology.logical_to_transport_routes[:num_devices]
            ),
        )
        controller = LEDController(
            num_devices=num_devices,
            bus=args.bus,
            speed=args.spi_speed,
            mode=0,
            strips_per_device=strips_per_device,
            strip_count=args.strips,
            leds_per_strip=args.leds_per_strip,
            debug=args.controller_debug,
            parallel=True,
            receiver_geometry_profile=feature_flags.receiver_geometry_profile,
            receiver_native_modules=feature_flags.receiver_native_modules,
            receiver_strip_counts=receiver_strip_counts,
            receiver_global_strip_offsets=receiver_global_strip_offsets,
            receiver_lane_masks=receiver_lane_masks,
            receiver_spi_speeds_hz=WALL_RECEIVER_SPI_SPEEDS_HZ[:num_devices],
            device_map=list(
                controller_topology.logical_to_transport_routes[
                    :num_devices
                ]
            ),
            reverse_host_strips_by_logical_receiver=(
                controller_topology
                .reverse_host_strips_by_logical_receiver[:num_devices]
            ),
            reverse_native_strips_by_logical_receiver=(
                controller_topology
                .reverse_native_strips_by_logical_receiver[:num_devices]
            ),
            fec_receiver_ids=receiver_fec_ids_for_runtime(),
            receiver_identities=receiver_authority.identities,
            receiver_identity_authority_digest=receiver_authority.authority_digest,
        )
        controller = select_receiver_hybrid_controller(
            controller, receiver_hybrid_config
        )
    else:
        # Single-device or mock controller
        controller = LEDController(
            bus=args.bus,
            device=args.device,
            speed=args.spi_speed,
            mode=0,
            strips=args.strips,
            leds_per_strip=args.leds_per_strip,
            debug=args.controller_debug,
        )
    try:
        if apply_production_stagger(controller):
            print(f"  WS2812 stagger: {PRODUCTION_STAGGER_PHASES} phases")
    except Exception as exc:
        print(f"⚠️ Failed to set WS2812 stagger to {PRODUCTION_STAGGER_PHASES}: {exc}")

    startup_speed_scale = (
        saved_state.get('animation_speed_scale', args.animation_speed_scale)
        if saved_state else args.animation_speed_scale
    )
    startup_modifiers = saved_state.get('plant_modifiers') if saved_state else None
    startup_brightness = (
        saved_state.get('brightness', args.brightness) if saved_state else args.brightness
    )
    manager = AnimationManager(
        controller,
        plugins_dir=args.animations_dir,
        animation_speed_scale=startup_speed_scale,
        plant_aware=DEFAULT_PLANT_AWARE,
        plant_modifiers=startup_modifiers,
        vibe=saved_state.get('vibe') if saved_state else None,
        default_animation=saved_state.get('animation') if saved_state else None,
        default_animation_config=saved_state.get('params') if saved_state else None,
        default_animation_preset=(
            saved_state.get('current_preset') if saved_state else None
        ),
        feature_flags=feature_flags,
        installation_profile_library=installation_profile_library,
        installation_profile_digest=installation_profile_digest,
        installation_profile_topology=installation_profile_topology,
        native_background_library=NativeBackgroundLibrary(
            project_root / "receiver_library/native_backgrounds"
        ),
        auto_start=not bool(saved_state and saved_state.get('scene')),
    )
    manager.target_fps = int(saved_state.get('target_fps', args.target_fps)) if saved_state else args.target_fps

    try:
        manager.set_output_brightness(startup_brightness)
        print(f"  Brightness : {startup_brightness}")
    except (RuntimeError, ValueError) as exc:
        print(f"⚠️ Failed to set controller brightness to {startup_brightness}: {exc}")

    if saved_state and saved_state.get('scene'):
        try:
            if not _restore_display_state(manager, saved_state):
                raise RuntimeError("manager rejected the saved scene")
        except (RuntimeError, TypeError, ValueError) as exc:
            fallback = saved_state.get("fallback_scene")
            if fallback is None or not _start_scene(manager, fallback):
                raise RuntimeError(
                    f"saved scene and its recorded Python fallback were rejected: {exc}"
                ) from exc
            print(f"⚠️ Restored recorded Python fallback: {exc}")

    channel = FileControlChannel(control_path=args.control_file, status_path=args.status_file)
    activation_coordinator = controller_activation_coordinator(
        manager,
        status_sink=channel.write_activation_status,
        restored_selected_scene=(
            saved_state.get("scene")
            if isinstance(saved_state, dict) and saved_state.get("power") is False
            else None
        ),
        cancel_probe=lambda activation_id: (
            channel.read_activation_cancel(activation_id) is not None
        ),
    )
    activation_coordinator.set_commit_callback(lambda: (
        persist_controller_restart_state(
            manager,
            activation_coordinator,
            presets_dir=Path(args.presets_dir),
            state_path=Path(args.saved_state_file),
        )
    ))
    maintenance_authority_digest = getattr(
        controller, "receiver_identity_authority_digest", None
    )

    print("🎛️ Controller mode")
    print(f"  Control file: {args.control_file}")
    print(f"  Status file : {args.status_file}")
    print(f"  Poll every  : {args.poll_interval}s")
    print(f"  Status every: {args.status_interval}s")
    print()

    # Seed from any existing control file so stale commands aren't re-executed on restart
    stale_cmd = channel.read_control()
    last_command_id = stale_cmd.get('command_id') if stale_cmd else None
    last_status_time = 0.0

    try:
        while True:
            process_maintenance_requests(
                channel,
                manager,
                authority_digest=maintenance_authority_digest,
                controller_status=activation_coordinator.controller_status,
            )
            activation_mutations = process_activation_commands(
                channel, activation_coordinator
            )
            if activation_mutations:
                try:
                    persist_controller_restart_state(
                        manager,
                        activation_coordinator,
                        presets_dir=Path(args.presets_dir),
                        state_path=Path(args.saved_state_file),
                    )
                    print("💾 Saved guarded activation restart state")
                except Exception as exc:
                    print(f"⚠️ Failed to save guarded activation state: {exc}")

            cmd = channel.read_control()
            if cmd and cmd.get('command_id') != last_command_id:
                last_command_id = cmd.get('command_id')
                action = cmd.get('action')
                data = cmd.get('data') or {}
                if handle_command(manager, action, data):
                    try:
                        persist_controller_restart_state(
                            manager,
                            activation_coordinator,
                            presets_dir=Path(args.presets_dir),
                            state_path=Path(args.saved_state_file),
                        )
                        print(f"💾 Saved restart state: {manager.current_animation_name}/before-deploy")
                    except Exception as exc:
                        print(f"⚠️ Failed to save restart state: {exc}")

            now = time.time()
            if now - last_status_time >= args.status_interval:
                status_payload = controller_status_payload(
                    manager,
                    release_id=args.release_id,
                    last_command_id=last_command_id,
                    updated_at=now,
                )
                channel.write_status(status_payload)
                last_status_time = now

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\n👋 Controller stopped by user")
    finally:
        manager.stop_animation()
        if hasattr(controller, "close"):
            try:
                controller.close()
            except Exception:
                pass


_TERMINAL_ACTIVATION_PHASES = frozenset({
    "active", "rolled_back", "failed", "timed_out",
})


def process_maintenance_requests(
    channel,
    manager: AnimationManager,
    *,
    authority_digest: str | None,
    controller_status,
) -> int:
    """Execute only current queued maintenance requests, never restart replay.

    The durable status is deliberately consulted before parsing or presenting.
    A previous controller's ``running`` record has lost its in-memory capture,
    so it is made terminal without touching the wall after restart.
    """
    completed = 0
    for record in channel.list_maintenance_requests():
        if not isinstance(record, dict):
            continue
        command = record.get("command")
        request_id = command.get("request_id") if isinstance(command, dict) else None
        try:
            status = channel.read_maintenance_status(request_id)
            if not isinstance(status, dict) or status.get("phase") in {
                "restored", "safe_idle", "rejected", "failed"
            }:
                continue
            pinned = record.get("authority_digest")
            if not isinstance(authority_digest, str) or pinned != authority_digest:
                channel.write_maintenance_status(
                    request_id, phase="rejected", authority_digest=str(pinned),
                    error="authoritative receiver identity is unavailable or stale",
                )
                completed += 1
                continue
            if status.get("phase") != "queued":
                channel.write_maintenance_status(
                    request_id, phase="failed", authority_digest=authority_digest,
                    error="abandoned nonterminal maintenance request was not replayed after restart",
                )
                completed += 1
                continue
            request = MaintenanceRequest.from_mapping(command)
            observed = controller_status()
            if (
                observed.get("controller_session_id") != request.expected_identity.controller_session_id
                or observed.get("controller_state_revision") != request.expected_identity.controller_state_revision
            ):
                channel.write_maintenance_status(
                    request_id, phase="rejected", authority_digest=authority_digest,
                    error="controller session or state revision is stale",
                )
                completed += 1
                continue
            channel.write_maintenance_status(
                request_id, phase="running", authority_digest=authority_digest
            )
            result = manager.run_maintenance_transaction(
                request, authority_digest=authority_digest
            )
            channel.write_maintenance_status(
                request_id, phase="restored", authority_digest=authority_digest,
                result=result,
            )
            completed += 1
        except (MaintenanceRequestError, ValueError, TypeError) as exc:
            channel.write_maintenance_status(
                request_id, phase="rejected", authority_digest=record.get("authority_digest"),
                error=str(exc),
            )
            completed += 1
        except MaintenanceRunError as exc:
            # Manager has already attempted visible safe idle before it
            # returns this error; make that fact durable and terminal.
            channel.write_maintenance_status(
                request_id, phase="safe_idle", authority_digest=record.get("authority_digest"),
                error=str(exc),
            )
            completed += 1
    return completed


def _activation_request_result(channel, kind: str, activation_id: str):
    reader = getattr(channel, f"read_activation_{kind}_result", None)
    return reader(activation_id) if callable(reader) else None


def _write_activation_request_result(
    channel,
    kind: str,
    request: dict,
    *,
    outcome: str,
    status: dict,
    error: str | None = None,
) -> None:
    writer = getattr(channel, f"write_activation_{kind}_result", None)
    if not callable(writer):
        return
    writer(
        request["activation_id"],
        request_id=request["request_id"],
        outcome=outcome,
        status_phase=status.get("phase", "unknown"),
        error=error,
    )


def _settle_terminal_activation_requests(channel, activation_id: str, status: dict) -> None:
    """Close orphaned request lifecycles from a prior controller process."""

    cancel = channel.read_activation_cancel(activation_id)
    if cancel is not None and _activation_request_result(
        channel, "cancel", activation_id
    ) is None:
        cancelled = (
            status.get("phase") == "failed"
            and "cancelled before mutation" in str(status.get("error") or "")
        )
        _write_activation_request_result(
            channel,
            "cancel",
            cancel,
            outcome="succeeded" if cancelled else "rejected",
            status=status,
            error=None if cancelled else (
                f"activation is already {status.get('phase', 'terminal')}"
            ),
        )

    rollback = channel.read_activation_rollback(activation_id)
    if rollback is not None and _activation_request_result(
        channel, "rollback", activation_id
    ) is None:
        rollback_state = status.get("rollback")
        rollback_state = rollback_state if isinstance(rollback_state, dict) else {}
        succeeded = (
            status.get("phase") == "rolled_back"
            and rollback_state.get("result") == "succeeded"
        )
        failed = rollback_state.get("result") == "failed"
        _write_activation_request_result(
            channel,
            "rollback",
            rollback,
            outcome="succeeded" if succeeded else "failed" if failed else "rejected",
            status=status,
            error=None if succeeded else (
                rollback_state.get("error")
                if failed
                else f"activation is already {status.get('phase', 'terminal')}"
            ),
        )


def process_activation_commands(channel, activation_coordinator) -> int:
    """Process each durable activation once, including safe restart replay."""

    mutations = 0
    for activation_command in channel.list_activation_commands():
        activation_id = activation_command.get("activation_id")
        try:
            activation_status = activation_coordinator.get(activation_id)
            if activation_status is None:
                durable_status = channel.read_activation_status(activation_id)
                if (
                    isinstance(durable_status, dict)
                    and durable_status.get("phase") in _TERMINAL_ACTIVATION_PHASES
                ):
                    if (
                        durable_status.get("phase") == "active"
                        and durable_status.get("controller", {}).get("session_id")
                        != activation_coordinator.session_id
                        and not durable_status.get("rollback", {}).get("error")
                    ):
                        activation_coordinator.reconcile_durable_active(
                            activation_command, durable_status
                        )
                        durable_status = activation_coordinator.get(activation_id)
                    _settle_terminal_activation_requests(
                        channel, activation_id, durable_status
                    )
                    continue
                if isinstance(durable_status, dict):
                    if durable_status.get("phase") == "queued":
                        # The web process publishes the exact initial queued
                        # receipt before atomically enqueuing its command.  That
                        # is a current-controller handoff, not restart evidence.
                        # The coordinator compares the complete normalized
                        # receipt and current state before it may be adopted.
                        try:
                            activation_status = (
                                activation_coordinator.queue_durable_handoff(
                                    activation_command, durable_status
                                )
                            )
                        except ControllerActivationConflictError as exc:
                            activation_status = (
                                activation_coordinator.reject_durable_queued(
                                    activation_command,
                                    durable_status,
                                    error=(
                                        "durable queued activation rejected before "
                                        f"mutation: {exc}"
                                    ),
                                )
                            )
                            _settle_terminal_activation_requests(
                                channel, activation_id, activation_status
                            )
                            continue
                    if activation_status is None:
                        # Never replay a command left applying/observing by a
                        # dead controller process. The in-memory compensation
                        # snapshot died with that process, so close it as a
                        # correlated historical failure under the old session.
                        activation_status = (
                            activation_coordinator.reconcile_durable_nonterminal(
                                activation_command, durable_status
                            )
                        )
                        _settle_terminal_activation_requests(
                            channel, activation_id, activation_status
                        )
                        continue
                activation_status = activation_coordinator.queue(
                    activation_command
                )
            if activation_status.get("phase") in {
                "rolled_back", "failed", "timed_out"
            }:
                _settle_terminal_activation_requests(
                    channel, activation_id, activation_status
                )
                continue
            cancel = channel.read_activation_cancel(activation_id)
            if cancel is not None and _activation_request_result(
                channel, "cancel", activation_id
            ) is None:
                if activation_status["phase"] not in {"queued", "preflighting"}:
                    _write_activation_request_result(
                        channel,
                        "cancel",
                        cancel,
                        outcome="rejected",
                        status=activation_status,
                        error=(
                            "activation can be cancelled only while queued or "
                            "preflighting"
                        ),
                    )
                else:
                    activation_status = activation_coordinator.cancel(activation_id)
            if activation_status["phase"] == "queued":
                revision_before = activation_coordinator.state_revision
                activation_status = activation_coordinator.execute(activation_id)
                if activation_coordinator.state_revision > revision_before:
                    mutations += 1
            cancel = channel.read_activation_cancel(activation_id)
            if cancel is not None and _activation_request_result(
                channel, "cancel", activation_id
            ) is None:
                cancelled = (
                    activation_status.get("phase") == "failed"
                    and "cancelled before mutation"
                    in str(activation_status.get("error") or "")
                )
                _write_activation_request_result(
                    channel,
                    "cancel",
                    cancel,
                    outcome="succeeded" if cancelled else "rejected",
                    status=activation_status,
                    error=None if cancelled else (
                        "activation completed before cancellation was consumed"
                    ),
                )
            rollback = channel.read_activation_rollback(activation_id)
            if rollback is not None and _activation_request_result(
                channel, "rollback", activation_id
            ) is None:
                if activation_status["phase"] != "active":
                    _write_activation_request_result(
                        channel,
                        "rollback",
                        rollback,
                        outcome="rejected",
                        status=activation_status,
                        error="only an active activation can be rolled back",
                    )
                else:
                    revision_before = activation_coordinator.state_revision
                    try:
                        activation_status = activation_coordinator.rollback(
                            activation_id,
                            snapshot_id=rollback["snapshot_id"],
                            expected_session_id=rollback[
                                "expected_controller_session_id"
                            ],
                            expected_state_revision=rollback[
                                "expected_controller_state_revision"
                            ],
                        )
                    except (ControllerActivationError, KeyError, TypeError, ValueError) as exc:
                        _write_activation_request_result(
                            channel,
                            "rollback",
                            rollback,
                            outcome="rejected",
                            status=activation_status,
                            error=str(exc),
                        )
                    else:
                        if activation_coordinator.state_revision > revision_before:
                            mutations += 1
                        succeeded = activation_status.get("phase") == "rolled_back"
                        rollback_state = activation_status.get("rollback") or {}
                        _write_activation_request_result(
                            channel,
                            "rollback",
                            rollback,
                            outcome="succeeded" if succeeded else "failed",
                            status=activation_status,
                            error=None if succeeded else (
                                rollback_state.get("error")
                                or activation_status.get("error")
                                or "exact rollback failed"
                            ),
                        )
        except (ControllerActivationError, KeyError, TypeError, ValueError) as exc:
            print(f"⚠️ Activation {activation_id!r} rejected: {exc}")
    return mutations


def _dispatch_legacy_command(manager: AnimationManager, action: str, data: dict):
    """Dispatch a command and report whether restart state changed."""
    if action == 'start':
        animation = data.get('animation')
        config = data.get('config') or {}
        print(f"▶️  Start requested: {animation}")
        return manager.start_animation(animation, config, preset=data.get('preset'))
    elif action == 'start_scene':
        print("▶️  Scene start requested")
        try:
            return _start_scene(manager, data.get("scene"))
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid scene: {exc}")
            return False
    elif action == 'update_scene_component':
        try:
            return _update_scene_component(
                manager, data.get("target"), data.get("update") or {}
            )
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid scene update: {exc}")
            return False
    elif action == 'stop_scene':
        stopper = getattr(manager, "stop_scene", manager.stop_animation)
        stopper()
    elif action == 'recover_receiver_native':
        try:
            return bool(manager.recover_receiver_native())
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Receiver-native recovery rejected: {exc}")
            return False
    elif action in {
        'probe_native_background',
        'install_native_background',
        'clear_native_background_quarantine',
    }:
        bundle_digest = data.get('bundle_digest')
        operation = getattr(manager, action, None)
        if not callable(operation):
            return False
        try:
            result = operation(bundle_digest)
            print(f"📦 {action}: {result.get('state', 'complete')}")
            return False
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ {action} rejected: {exc}")
            return False
    elif action == 'restore_display_state':
        try:
            return _restore_display_state(manager, data.get("state"))
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Invalid desired display state: {exc}")
            return False
    elif action == 'stop':
        print("⏹️  Stop requested")
        manager.stop_animation()
    elif action == 'update_params':
        params = data.get('params') or {}
        if params:
            print(f"⚙️  Update params: {params}")
            return manager.update_animation_parameters(params)
    elif action == 'set_current_preset':
        preset = data.get('preset') or {}
        return manager.set_current_preset(preset)
    elif action == 'set_target_fps':
        requested = data.get('target_fps')
        try:
            applied = manager.set_target_fps(int(requested))
            print(f"🎚️ Target FPS: {applied}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid target FPS: {requested!r}")
    elif action == 'set_animation_speed_scale':
        requested = data.get('animation_speed_scale')
        try:
            applied = manager.set_animation_speed_scale(float(requested))
            print(f"🎚️ Animation speed scale: {applied:.3f}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid animation speed scale: {requested!r}")
    elif action == 'set_output_brightness':
        requested = data.get('brightness')
        try:
            applied = manager.set_output_brightness(requested)
            print(f"💡 Output brightness: {applied}")
            return bool(manager.is_running)
        except (RuntimeError, TypeError, ValueError):
            print(f"⚠️ Invalid output brightness: {requested!r}")
    elif action == 'set_device_state':
        try:
            applied = manager.apply_device_state(data)
            print(f"🏛️ Device state: {data}")
            return bool(applied and manager.is_running)
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Invalid device state: {exc}")
    elif action == 'set_plant_aware':
        requested = data.get('plant_aware')
        try:
            applied = manager.set_plant_aware(requested)
            print(f"🌿 Plant-aware mode: {'on' if applied else 'off'}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid plant-aware state: {requested!r}")
    elif action == 'set_plant_modifiers':
        requested = data.get('plant_modifiers')
        try:
            applied = manager.set_plant_modifiers(requested)
            print(f"🌿 Plant modifiers: {', '.join(applied['active']) or 'off'}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid plant modifier state: {requested!r}")
    elif action == 'set_vibe':
        requested = data.get('vibe', data.get('vibe_id'))
        if requested is None:
            print("⚠️ Invalid vibe: None")
            return False
        try:
            applied = manager.set_vibe(requested)
            state = applied.get('state', applied) if isinstance(applied, dict) else {}
            vibe_id = state.get('id', state.get('vibe_id', requested))
            print(f"🎨 Vibe: {vibe_id}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid vibe: {requested!r}")
    elif action == 'refresh_receiver_status':
        request_id = data.get('request_id')
        refresher = getattr(manager.controller, 'refresh_receiver_status', None)
        if not callable(refresher):
            print("⚠️ Receiver status refresh is unavailable")
            return False
        try:
            result = refresher(request_id)
            print(
                "📡 Receiver status refresh "
                f"{request_id}: {'complete' if result.get('passed') else 'failed'}"
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Receiver status refresh rejected: {exc}")
        return False
    elif action == 'refresh_plugins':
        animation = data.get('animation')
        if animation:
            print(f"🔄 Reload plugin: {animation}")
            manager.reload_animation(animation)
        else:
            print("🔄 Refresh all plugins")
            manager.refresh_plugins()
    elif action == 'puncture_hole':
        x = data.get('x')
        y = data.get('y')
        radius = data.get('radius')
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            print(f"💥 Hole requested at ({x:.1f}, {y:.1f})")
            manager.trigger_hole(float(x), float(y), radius)
        else:
            print("💥 Random hole requested")
            manager.trigger_random_hole()
    elif action == 'animation_interaction':
        try:
            return manager.dispatch_interaction(
                data.get('kind', 'primary'), data.get('x'), data.get('y'),
                data.get('strength', 1.0),
            )
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid animation interaction: {exc}")
            return False
    elif action == 'dpad':
        direction = (data.get('direction') or '').lower().replace('_', '-')
        if manager.current_animation and hasattr(manager.current_animation, 'handle_input'):
            manager.current_animation.handle_input(direction)
        else:
            print(f"⚠️ D-pad input ignored (no handler): {direction}")
    else:
        print(f"⚠️ Unknown action: {action}")
    return False


_LEGACY_CONTROLLER_MUTATIONS = frozenset({
    "start", "start_scene", "update_scene_component", "stop_scene",
    "recover_receiver_native", "probe_native_background",
    "install_native_background", "clear_native_background_quarantine",
    "restore_display_state", "stop", "update_params",
    "set_current_preset", "set_target_fps", "set_animation_speed_scale",
    "set_output_brightness", "set_device_state", "set_plant_aware",
    "set_plant_modifiers", "set_vibe", "refresh_plugins", "puncture_hole",
    "animation_interaction", "dpad",
})


def handle_command(manager: AnimationManager, action: str, data: dict):
    """Dispatch one command and maintain the controller-owned CAS revision."""

    coordinator = controller_activation_coordinator(manager)
    if action == "activate_scene":
        try:
            command = data.get("activation", data)
            status = coordinator.activate(command)
            return status["phase"] == "active"
        except (ControllerActivationError, TypeError, ValueError) as exc:
            print(f"⚠️ Guarded scene activation rejected: {exc}")
            return False
    if action == "cancel_activation":
        try:
            coordinator.cancel(data.get("activation_id"))
        except (ControllerActivationError, KeyError, TypeError, ValueError) as exc:
            print(f"⚠️ Activation cancellation rejected: {exc}")
        return False

    if action not in _LEGACY_CONTROLLER_MUTATIONS:
        return _dispatch_legacy_command(manager, action, data)
    with coordinator.legacy_mutation_guard():
        return _dispatch_legacy_command(manager, action, data)


def run_web_mode(args):
    """Web/preview process."""
    receiver_hybrid_config = _receiver_hybrid_runtime_settings(args)
    feature_flags = receiver_hybrid_feature_flags(
        bool(_receiver_hybrid_config_value(
            receiver_hybrid_config, "enabled", False
        )),
        receiver_native_modules=receiver_native_modules_for_runtime(
            receiver_hybrid_config
        ),
    )
    channel = FileControlChannel(control_path=args.control_file, status_path=args.status_file)
    web_interface = create_app(
        control_channel=channel,
        host=args.host,
        port=args.port,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        animations_dir=args.animations_dir,
        animation_speed_scale=args.animation_speed_scale,
        feature_flags=feature_flags,
        installation_profile_topology=installation_profile_topology_for_runtime(
            receiver_hybrid_config
        ),
        release_id=args.release_id,
    )

    print("🌐 Web/Preview mode")
    print(f"  Control file: {args.control_file}")
    print(f"  Status file : {args.status_file}")
    print(f"  URL: http://{args.host}:{args.port}")
    print(f"  Composer:  http://{args.host}:{args.port}/composer")
    print("  Root URL redirects to Composer")
    print()

    web_interface.run(debug=args.debug)


def main():
    parser = argparse.ArgumentParser(description='LED Animation Server')

    parser.add_argument('--mode', choices=['controller', 'web'], default='web',
                        help='Run as controller (hardware) or web/preview process')

    # Shared options
    default_plugins_dir = str((Path(__file__).resolve().parents[1] / "animation" / "plugins").resolve())
    parser.add_argument('--animations-dir', default=default_plugins_dir,
                        help=f'Directory containing animation plugins (default: {default_plugins_dir})')
    parser.add_argument('--control-file', default='run_state/control.json',
                        help='Path to control file (default: run_state/control.json)')
    parser.add_argument('--status-file', default='run_state/status.json',
                        help='Path to status file (default: run_state/status.json)')
    parser.add_argument('--presets-dir', default='presets/animations',
                        help='Directory for restart-state animation presets')
    parser.add_argument('--saved-state-file', default='run_state/before_deploy.json',
                        help='Path to the persisted restart animation state')
    parser.add_argument('--release-id', default=None,
                        help='Verified immutable release identity supplied by production startup')
    parser.add_argument(
        '--receiver-hybrid-canary',
        action='store_true',
        default=None,
        help=(
            'opt in to the receiver-native compiled-rainbow canary '
            '(durable run_state policy remains the service default)'
        ),
    )
    parser.add_argument('--strips', type=int, default=default_strip_count(),
                        help=f'Number of LED strips (default: {default_strip_count()})')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LEDS_PER_STRIP,
                        help=f'LEDs per strip (default: {DEFAULT_LEDS_PER_STRIP})')

    # Web options
    parser.add_argument('--host', default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port to listen on (default: 5000)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode for Flask')

    # Controller options
    parser.add_argument('--bus', type=int, default=0,
                        help='SPI bus number (default: 0)')
    parser.add_argument('--device', type=int, default=0,
                        help='SPI device number (default: 0)')
    parser.add_argument('--spi-speed', type=int, default=20000000,
                        help='SPI speed in Hz (default: 20000000)')
    parser.add_argument('--controller-debug', action='store_true',
                        help='Enable LED controller debug output')
    parser.add_argument('--target-fps', type=int, default=200,
                        help=f'Target animation FPS (default: 200; tuned for {DEFAULT_LEDS_PER_STRIP}-pixel WS2812 strips)')
    parser.add_argument('--brightness', type=int, default=50,
                        help='Global hardware brightness 0-255 (default: 50)')
    parser.add_argument('--animation-speed-scale', type=float, default=DEFAULT_ANIMATION_SPEED_SCALE,
                        help=f'Multiplier applied to animation speed parameters (default: {DEFAULT_ANIMATION_SPEED_SCALE})')
    parser.add_argument('--poll-interval', type=float, default=0.05,
                        help='Seconds between control-file polls (controller mode)')
    parser.add_argument('--status-interval', type=float, default=0.5,
                        help='Seconds between status writes (controller mode)')

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    try:
        args.receiver_hybrid_config = resolve_receiver_hybrid_runtime_config(
            project_root, explicit_enabled=args.receiver_hybrid_canary
        )
        args.receiver_hybrid_canary = bool(_receiver_hybrid_config_value(
            args.receiver_hybrid_config, "enabled", False
        ))
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    try:
        active_release_id = resolve_active_release_id(project_root)
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.release_id is not None:
        if RELEASE_ID_PATTERN.fullmatch(args.release_id) is None:
            parser.error('--release-id must be a lowercase SHA-256 digest')
        if args.release_id != active_release_id:
            parser.error('--release-id does not match the active current release')
    args.release_id = active_release_id

    print("🎨 LED Grid Animation Server")
    print("=" * 40)
    print(f"Mode: {args.mode}")
    print(f"Animations: {args.animations_dir}/")
    print(f"Layout: {args.strips} strips × {args.leds_per_strip} LEDs = {args.strips * args.leds_per_strip} total")
    print()

    try:
        if args.mode == 'controller':
            print(f"SPI: /dev/spidev{args.bus}.{args.device} @ {args.spi_speed/1000000:.1f} MHz")
            print(f"Target FPS: {args.target_fps}")
            print()
            run_controller_mode(args)
        else:
            run_web_mode(args)
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
