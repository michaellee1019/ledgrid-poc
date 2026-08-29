#!/usr/bin/env python3
"""Validate and retain fail-closed physical-iPhone REL-01 evidence.

The portable Playwright runner deliberately excludes physical iPhone Safari,
installed Home Screen mode, and VoiceOver.  This module validates the separate
operator-captured bundle.  It does not drive a device and never promotes the
combined REL-01 release gate by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from tools.browser_qualification.evidence import (
    DEFAULT_MANIFEST,
    git_source,
    load_manifest,
)
from tools.browser_qualification.source_identity import fixture_release_id


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOTSTRAP = ROOT / "web/static/generated/composer/bootstrap.v1.json"
CAPTURE_SCHEMA = "ledgrid.rel01-external-iphone-capture"
RETAINED_SCHEMA = "ledgrid.rel01-external-iphone-evidence"
SCHEMA_VERSION = 1
REQUIRED_SESSIONS = ("safari", "installed_standalone", "voiceover")
REQUIRED_VOICEOVER_OBSERVATIONS = (
    "actionable_controls_have_name_role_state",
    "selection_and_toggle_state_announced",
    "status_and_live_region_updates_announced",
    "navigation_order_is_logical",
    "modal_focus_is_contained",
    "modal_cancel_returns_focus",
    "no_unlabeled_actionable_controls",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
MODEL_PATTERN = re.compile(r"iPhone\d+,\d+")
UDID_PATTERN = re.compile(r"(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"
)
MAX_EVIDENCE_AGE = timedelta(days=7)
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_SESSION_DURATION = timedelta(hours=6)
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUTHORING_MUTATION_PATHS = {
    "/api/v1/composer/presets",
    "/api/v1/scene-presets",
    "/api/v1/scene/checks",
}
PROFILE_AUTHORING_PATTERN = re.compile(
    r"^/api/v1/installation-profiles/[0-9a-f]{64}/(?:draft|publish)$"
)
CAPTURE_KEYS = {
    "schema",
    "schema_version",
    "gate",
    "disposition",
    "qualification_credit_requested",
    "source_binding",
    "runtime_bindings",
    "device",
    "sessions",
}
SOURCE_BINDING_KEYS = {"git_commit", "release_id", "manifest_sha256"}
RUNTIME_BINDING_KEYS = {
    "profile_digest",
    "python_runtime_digest",
    "managed_native_runtime_digest",
}
DEVICE_KEYS = {
    "device_class",
    "simulator",
    "continuity_camera_only",
    "available",
    "paired",
    "safari_web_inspector_target",
    "coredevice_state",
    "connection_transport",
    "name",
    "model_name",
    "model_identifier",
    "udid",
    "ios_version",
    "ios_build",
    "safari_version",
    "webkit_version",
}
SESSION_KEYS = {
    "session_id",
    "started_at",
    "completed_at",
    "outcome",
    "navigator_standalone",
    "safari_chrome_visible",
    "home_screen_install_present",
    "voiceover",
    "runtime_bindings",
    "console_results",
    "network_results",
    "fixture_status_before",
    "fixture_status_after",
    "journeys",
    "media",
}
FIXTURE_STATUS_KEYS = {
    "schema",
    "schema_version",
    "profile_digest",
    "native_plugin_id",
    "native_bundle_digest",
    "native_payload_digest",
    "source_commit",
    "release_id",
    "controller_release_id",
    "release_consistent",
    "network_outage_blocks",
    "network_outage_paths",
    "wall_mutation_attempts",
    "wall_consumer_attached",
}


class DuplicateKeyError(ValueError):
    """Raised when evidence JSON contains ambiguous duplicate object keys."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_keys(
    value: Mapping[str, Any], expected: set[str], prefix: str
) -> list[str]:
    errors: list[str] = []
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        errors.append(f"{prefix}:missing_fields:{','.join(missing)}")
    if extra:
        errors.append(f"{prefix}:unexpected_fields:{','.join(extra)}")
    return errors


def _exact_value(actual: Any, expected: Any) -> bool:
    return type(actual) is type(expected) and actual == expected


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == timezone.utc else None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _contains_operator_waiver(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() == "OPERATOR_WAIVED"
    if isinstance(value, Mapping):
        return any(_contains_operator_waiver(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_operator_waiver(item) for item in value)
    return False


def _runtime_bindings(bootstrap_path: Path) -> dict[str, str]:
    payload = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    profile_digest = payload.get("installation_profile", {}).get("digest")
    by_name = {
        item.get("name"): item
        for item in payload.get("components", [])
        if isinstance(item, Mapping)
    }
    bindings = {
        "profile_digest": profile_digest,
        "python_runtime_digest": by_name.get("Color Gradient", {})
        .get("browser_runtime", {})
        .get("digest"),
        "managed_native_runtime_digest": by_name.get("Aurora Curtains (Native)", {})
        .get("browser_runtime", {})
        .get("digest"),
    }
    if any(not _is_sha256(value) for value in bindings.values()):
        raise ValueError(
            "checked-in Composer bootstrap lacks required runtime bindings"
        )
    return {key: str(value) for key, value in bindings.items()}


def _validate_fixture_status(
    status: Any,
    *,
    prefix: str,
    commit: str,
    release_id: str,
    bindings: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(status, Mapping):
        return [f"{prefix}:fixture_status_missing"]
    errors.extend(_strict_keys(status, FIXTURE_STATUS_KEYS, f"{prefix}:fixture"))
    exact = {
        "schema": "ledgrid.browser-qualification-fixture-status",
        "schema_version": 2,
        "source_commit": commit,
        "release_id": release_id,
        "controller_release_id": release_id,
        "release_consistent": True,
        "wall_consumer_attached": False,
        "wall_mutation_attempts": 0,
        "profile_digest": bindings["profile_digest"],
        "native_plugin_id": "aurora_curtains_native",
    }
    for field, expected in exact.items():
        if not _exact_value(status.get(field), expected):
            errors.append(f"{prefix}:fixture_{field}_mismatch")
    for field in ("native_bundle_digest", "native_payload_digest"):
        if not _is_sha256(status.get(field)):
            errors.append(f"{prefix}:fixture_{field}_invalid")
    outage_blocks = status.get("network_outage_blocks")
    if type(outage_blocks) is not int or outage_blocks < 0:
        errors.append(f"{prefix}:fixture_network_outage_blocks_invalid")
    outage_paths = status.get("network_outage_paths")
    if (
        not isinstance(outage_paths, list)
        or any(not isinstance(item, str) or not item for item in outage_paths)
        or len(outage_paths) != len(set(outage_paths))
    ):
        errors.append(f"{prefix}:fixture_network_outage_paths_invalid")
    return errors


def _validate_assertions(
    assertions: Any,
    required: list[str],
    *,
    prefix: str,
) -> list[str]:
    if not isinstance(assertions, list):
        return [f"{prefix}:assertions_missing"]
    errors: list[str] = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for assertion in assertions:
        if not isinstance(assertion, Mapping) or not isinstance(
            assertion.get("assertion_id"), str
        ):
            errors.append(f"{prefix}:assertion_invalid")
            continue
        errors.extend(
            _strict_keys(
                assertion,
                {"assertion_id", "passed", "detail"},
                f"{prefix}:assertion:{assertion.get('assertion_id')}",
            )
        )
        assertion_id = assertion["assertion_id"]
        if assertion_id in by_id:
            errors.append(f"{prefix}:duplicate_assertion:{assertion_id}")
        by_id[assertion_id] = assertion
    if set(by_id) != set(required):
        errors.append(f"{prefix}:assertion_set_mismatch")
    for assertion_id in required:
        assertion = by_id.get(assertion_id)
        if assertion is None:
            errors.append(f"{prefix}:missing_assertion:{assertion_id}")
        elif assertion.get("passed") is not True:
            errors.append(f"{prefix}:failed_assertion:{assertion_id}")
        elif (
            not isinstance(assertion.get("detail"), str)
            or not assertion["detail"].strip()
        ):
            errors.append(f"{prefix}:assertion_detail_missing:{assertion_id}")
    return errors


def _validate_journeys(
    journeys: Any,
    manifest: Mapping[str, Any],
    *,
    prefix: str,
) -> list[str]:
    if not isinstance(journeys, list):
        return [f"{prefix}:journeys_missing"]
    errors: list[str] = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for journey in journeys:
        if not isinstance(journey, Mapping) or not isinstance(
            journey.get("journey_id"), str
        ):
            errors.append(f"{prefix}:journey_invalid")
            continue
        journey_id = journey["journey_id"]
        contract = manifest["journeys"].get(journey_id)
        expected_keys = {"journey_id", "outcome", "assertions"}
        if isinstance(contract, Mapping):
            expected_keys.add("viewport" if "viewport" in contract else "viewports")
            if "viewport" not in contract:
                expected_keys.add("viewport_observations")
        errors.extend(
            _strict_keys(journey, expected_keys, f"{prefix}:{journey_id}:journey")
        )
        if journey_id in by_id:
            errors.append(f"{prefix}:duplicate_journey:{journey_id}")
        by_id[journey_id] = journey
    if set(by_id) != set(manifest["journeys"]):
        errors.append(f"{prefix}:journey_set_mismatch")
    for journey_id, contract in manifest["journeys"].items():
        journey = by_id.get(journey_id)
        if journey is None:
            errors.append(f"{prefix}:missing_journey:{journey_id}")
            continue
        if journey.get("outcome") != "PASS":
            errors.append(f"{prefix}:journey_not_passed:{journey_id}")
        errors.extend(
            _validate_assertions(
                journey.get("assertions"),
                contract["required_assertions"],
                prefix=f"{prefix}:{journey_id}",
            )
        )
        if "viewport" in contract:
            if journey.get("viewport") != contract["viewport"]:
                errors.append(f"{prefix}:{journey_id}:viewport_mismatch")
        else:
            if journey.get("viewports") != contract["viewports"]:
                errors.append(f"{prefix}:{journey_id}:viewport_matrix_mismatch")
            observations = journey.get("viewport_observations")
            expected_viewports = {item["name"]: item for item in contract["viewports"]}
            if not isinstance(observations, list):
                errors.append(f"{prefix}:{journey_id}:viewport_observations_missing")
                continue
            observed = {
                item.get("name"): item
                for item in observations
                if isinstance(item, Mapping) and isinstance(item.get("name"), str)
            }
            if len(observed) != len(observations):
                errors.append(f"{prefix}:{journey_id}:duplicate_or_invalid_viewport")
            if set(observed) != set(expected_viewports):
                errors.append(
                    f"{prefix}:{journey_id}:viewport_observation_set_mismatch"
                )
            for name, expected in expected_viewports.items():
                item = observed.get(name)
                if item is None:
                    continue
                errors.extend(
                    _strict_keys(
                        item,
                        {"name", "width", "height", "outcome", "assertions"},
                        f"{prefix}:{journey_id}:{name}:viewport",
                    )
                )
                if any(
                    item.get(field) != expected[field]
                    for field in ("name", "width", "height")
                ):
                    errors.append(f"{prefix}:{journey_id}:{name}:viewport_mismatch")
                if item.get("outcome") != "PASS":
                    errors.append(f"{prefix}:{journey_id}:{name}:viewport_not_passed")
                errors.extend(
                    _validate_assertions(
                        item.get("assertions"),
                        contract["required_viewport_assertions"],
                        prefix=f"{prefix}:{journey_id}:{name}",
                    )
                )
    return errors


def _artifact_path(value: Any, artifact_base: Path) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    base = artifact_base.resolve()
    path = (base / value).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


def _probe_media(path: Path, media_type: str) -> tuple[str, set[str]]:
    if media_type == "screenshot":
        try:
            with Image.open(path) as image:
                image.verify()
                if image.width < 1 or image.height < 1 or image.format is None:
                    raise ValueError("image has no pixels or format")
                return image.format.lower(), {"image"}
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ValueError(f"unparseable image: {exc}") from exc
    if media_type == "audio_recording" and path.suffix.lower() == ".wav":
        try:
            payload = path.read_bytes()
            if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
                raise ValueError("invalid RIFF/WAVE header")
            offset = 12
            valid_format = False
            audio_bytes = 0
            while offset + 8 <= len(payload):
                chunk_id = payload[offset : offset + 4]
                chunk_size = struct.unpack_from("<I", payload, offset + 4)[0]
                chunk_start = offset + 8
                chunk_end = chunk_start + chunk_size
                if chunk_end > len(payload):
                    raise ValueError("truncated WAV chunk")
                if chunk_id == b"fmt " and chunk_size >= 16:
                    audio_format, channels, sample_rate = struct.unpack_from(
                        "<HHI", payload, chunk_start
                    )
                    valid_format = (
                        audio_format in {1, 3, 0xFFFE}
                        and channels > 0
                        and sample_rate > 0
                    )
                elif chunk_id == b"data":
                    audio_bytes += chunk_size
                offset = chunk_end + (chunk_size % 2)
            if not valid_format or audio_bytes < 1:
                raise ValueError("WAV contains no supported audio stream")
        except (OSError, EOFError, ValueError, struct.error) as exc:
            raise ValueError(f"unparseable WAV: {exc}") from exc
        return "wav", {"audio"}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name:stream=codec_type,width,height",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(
            result.stdout, object_pairs_hook=_object_without_duplicates
        )
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise ValueError(f"unparseable media: {exc}") from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    format_record = payload.get("format") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list) or not isinstance(format_record, Mapping):
        raise ValueError("media probe omitted streams or format")
    stream_types = {
        item.get("codec_type") for item in streams if isinstance(item, Mapping)
    }
    if media_type == "screen_recording":
        video_streams = [
            item
            for item in streams
            if isinstance(item, Mapping) and item.get("codec_type") == "video"
        ]
        if not video_streams or any(
            type(item.get("width")) is not int
            or item["width"] < 1
            or type(item.get("height")) is not int
            or item["height"] < 1
            for item in video_streams
        ):
            raise ValueError("screen recording has no valid video stream")
    elif media_type == "audio_recording" and "audio" not in stream_types:
        raise ValueError("audio recording has no audio stream")
    names = str(format_record.get("format_name") or "").split(",")
    suffix_format = {
        ".mov": "mov",
        ".mp4": "mp4",
        ".m4a": "m4a",
    }.get(path.suffix.lower())
    if suffix_format is None or not ({"mov", "mp4", "m4a"} & set(names)):
        raise ValueError("unsupported media container")
    return suffix_format, {str(item) for item in stream_types}


def _validate_media(
    media: Any,
    *,
    prefix: str,
    session_id: str,
    started: datetime | None,
    completed: datetime | None,
    artifact_base: Path,
    require_audio: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(media, list) or not media:
        return [f"{prefix}:media_missing"], []
    errors: list[str] = []
    has_audio_capture = False
    has_visual_capture = False
    derived: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_digests: set[str] = set()
    for index, item in enumerate(media):
        item_prefix = f"{prefix}:media:{index}"
        if not isinstance(item, Mapping):
            errors.append(f"{item_prefix}:invalid")
            continue
        errors.extend(
            _strict_keys(
                item,
                {
                    "session_id",
                    "path",
                    "media_type",
                    "format",
                    "captured_at",
                    "byte_count",
                    "sha256",
                },
                item_prefix,
            )
        )
        if item.get("session_id") != session_id:
            errors.append(f"{item_prefix}:session_binding_mismatch")
        media_type = item.get("media_type")
        if media_type not in {"screenshot", "screen_recording", "audio_recording"}:
            errors.append(f"{item_prefix}:media_type_invalid")
        captured_at = _timestamp(item.get("captured_at"))
        if captured_at is None:
            errors.append(f"{item_prefix}:timestamp_invalid")
        elif (
            started is None
            or completed is None
            or not started <= captured_at <= completed
        ):
            errors.append(f"{item_prefix}:timestamp_outside_session")
        path = _artifact_path(item.get("path"), artifact_base)
        if path is None:
            errors.append(f"{item_prefix}:path_invalid")
            continue
        normalized_path = str(path)
        if normalized_path in seen_paths:
            errors.append(f"{item_prefix}:duplicate_path")
        seen_paths.add(normalized_path)
        if not path.is_file():
            errors.append(f"{item_prefix}:file_missing")
            continue
        byte_count = path.stat().st_size
        if (
            type(item.get("byte_count")) is not int
            or item.get("byte_count") != byte_count
            or byte_count <= 0
        ):
            errors.append(f"{item_prefix}:byte_count_mismatch")
        digest = _sha256_file(path)
        if digest in seen_digests:
            errors.append(f"{item_prefix}:duplicate_sha256")
        seen_digests.add(digest)
        if item.get("sha256") != digest:
            errors.append(f"{item_prefix}:sha256_mismatch")
        if not _is_sha256(item.get("sha256")):
            errors.append(f"{item_prefix}:sha256_invalid")
        try:
            detected_format, stream_types = _probe_media(path, str(media_type))
        except ValueError:
            errors.append(f"{item_prefix}:media_parse_failed")
            continue
        if item.get("format") != detected_format:
            errors.append(f"{item_prefix}:format_mismatch")
        if "audio" in stream_types:
            has_audio_capture = True
        if stream_types & {"image", "video"}:
            has_visual_capture = True
        derived.append(
            {
                "path": item.get("path"),
                "sha256": digest,
                "format": detected_format,
                "stream_types": sorted(stream_types),
            }
        )
    if not has_visual_capture:
        errors.append(f"{prefix}:visual_capture_missing")
    if require_audio and not has_audio_capture:
        errors.append(f"{prefix}:voiceover_audio_capture_missing")
    return errors, derived


def _is_forbidden_mutation(method: str, url: str) -> bool:
    if method.upper() not in MUTATING_METHODS:
        return False
    pathname = urlsplit(url).path
    if pathname in AUTHORING_MUTATION_PATHS:
        return False
    if PROFILE_AUTHORING_PATTERN.fullmatch(pathname) is not None:
        return False
    return pathname.startswith("/api/")


def _validate_network(
    network: Any,
    *,
    prefix: str,
    session_id: str,
    artifact_base: Path,
    started: datetime | None,
    completed: datetime | None,
) -> tuple[list[str], dict[str, Any]]:
    if not isinstance(network, Mapping):
        return [f"{prefix}:network_results_missing"], {}
    errors = _strict_keys(
        network,
        {
            "session_id",
            "trace_path",
            "trace_format",
            "trace_byte_count",
            "trace_sha256",
        },
        f"{prefix}:network_results",
    )
    if network.get("session_id") != session_id:
        errors.append(f"{prefix}:network_trace_session_binding_mismatch")
    if network.get("trace_format") != "har":
        errors.append(f"{prefix}:network_trace_format_invalid")
    path = _artifact_path(network.get("trace_path"), artifact_base)
    if path is None:
        errors.append(f"{prefix}:network_trace_path_invalid")
        return errors, {}
    if not path.is_file():
        errors.append(f"{prefix}:network_trace_missing")
        return errors, {}
    byte_count = path.stat().st_size
    if (
        byte_count <= 0
        or type(network.get("trace_byte_count")) is not int
        or network.get("trace_byte_count") != byte_count
    ):
        errors.append(f"{prefix}:network_trace_byte_count_mismatch")
    digest = _sha256_file(path)
    if network.get("trace_sha256") != digest:
        errors.append(f"{prefix}:network_trace_sha256_mismatch")
    if not _is_sha256(network.get("trace_sha256")):
        errors.append(f"{prefix}:network_trace_digest_invalid")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
        errors.append(f"{prefix}:network_trace_parse_failed:{type(exc).__name__}")
        return errors, {}
    log = payload.get("log") if isinstance(payload, Mapping) else None
    entries = log.get("entries") if isinstance(log, Mapping) else None
    if not isinstance(entries, list) or not entries:
        errors.append(f"{prefix}:network_trace_entries_missing")
        return errors, {}
    forbidden: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            errors.append(f"{prefix}:network_trace_entry_invalid:{index}")
            continue
        request = entry.get("request")
        when = _timestamp(entry.get("startedDateTime"))
        if (
            when is None
            or started is None
            or completed is None
            or not started <= when <= completed
        ):
            errors.append(f"{prefix}:network_trace_timestamp_invalid:{index}")
        if not isinstance(request, Mapping):
            errors.append(f"{prefix}:network_trace_request_invalid:{index}")
            continue
        method = request.get("method")
        url = request.get("url")
        parsed_url = urlsplit(url) if isinstance(url, str) else None
        if (
            not isinstance(method, str)
            or parsed_url is None
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
        ):
            errors.append(f"{prefix}:network_trace_request_invalid:{index}")
            continue
        if _is_forbidden_mutation(method, url):
            forbidden.append(f"{method.upper()} {urlsplit(url).path}")
    if forbidden:
        errors.append(f"{prefix}:forbidden_network_request_observed")
    return errors, {
        "path": network.get("trace_path"),
        "sha256": digest,
        "request_count": len(entries),
        "forbidden_mutation_requests": forbidden,
    }


def _validate_session(
    session: Mapping[str, Any],
    *,
    session_id: str,
    manifest: Mapping[str, Any],
    commit: str,
    release_id: str,
    bindings: Mapping[str, str],
    artifact_base: Path,
    now: datetime,
) -> tuple[list[str], dict[str, Any]]:
    prefix = f"session:{session_id}"
    errors: list[str] = []
    expected_session_keys = set(SESSION_KEYS)
    if session_id == "voiceover":
        expected_session_keys.add("surface")
    errors.extend(_strict_keys(session, expected_session_keys, prefix))
    started = _timestamp(session.get("started_at"))
    completed = _timestamp(session.get("completed_at"))
    if (
        started is None
        or completed is None
        or completed <= started
        or completed - started > MAX_SESSION_DURATION
    ):
        errors.append(f"{prefix}:timestamps_invalid")
    elif completed < now - MAX_EVIDENCE_AGE:
        errors.append(f"{prefix}:evidence_stale")
    elif started > now + MAX_FUTURE_SKEW or completed > now + MAX_FUTURE_SKEW:
        errors.append(f"{prefix}:timestamp_in_future")
    if session.get("outcome") != "PASS":
        errors.append(f"{prefix}:outcome_not_passed")
    expected_modes = {
        "safari": (False, True, False),
        "installed_standalone": (True, False, False),
    }
    if session_id in expected_modes:
        standalone, chrome, voiceover = expected_modes[session_id]
        if session.get("navigator_standalone") is not standalone:
            errors.append(f"{prefix}:navigator_standalone_mismatch")
        if session.get("safari_chrome_visible") is not chrome:
            errors.append(f"{prefix}:safari_chrome_visibility_mismatch")
        if session.get("home_screen_install_present") is not standalone:
            errors.append(f"{prefix}:home_screen_install_state_mismatch")
        voiceover_record = session.get("voiceover")
        if not isinstance(voiceover_record, Mapping):
            errors.append(f"{prefix}:voiceover_state_mismatch")
        else:
            errors.extend(
                _strict_keys(
                    voiceover_record,
                    {"enabled", "observations"},
                    f"{prefix}:voiceover",
                )
            )
            if voiceover_record.get("enabled") is not voiceover:
                errors.append(f"{prefix}:voiceover_state_mismatch")
            if voiceover_record.get("observations") != []:
                errors.append(f"{prefix}:voiceover_observations_unexpected")
        if (
            session_id == "installed_standalone"
            and session.get("home_screen_install_present") is not True
        ):
            errors.append(f"{prefix}:home_screen_install_unproven")
    else:
        if session.get("surface") not in {"safari", "installed_standalone"}:
            errors.append(f"{prefix}:surface_invalid")
        expected_standalone = session.get("surface") == "installed_standalone"
        if session.get("navigator_standalone") is not expected_standalone:
            errors.append(f"{prefix}:navigator_standalone_mismatch")
        if session.get("safari_chrome_visible") is not (not expected_standalone):
            errors.append(f"{prefix}:safari_chrome_visibility_mismatch")
        if (
            expected_standalone
            and session.get("home_screen_install_present") is not True
        ):
            errors.append(f"{prefix}:home_screen_install_unproven")
        if (
            not expected_standalone
            and session.get("home_screen_install_present") is not False
        ):
            errors.append(f"{prefix}:home_screen_install_state_mismatch")
        voiceover = session.get("voiceover")
        if not isinstance(voiceover, Mapping):
            errors.append(f"{prefix}:voiceover_not_enabled")
        else:
            errors.extend(
                _strict_keys(
                    voiceover,
                    {"enabled", "observations"},
                    f"{prefix}:voiceover",
                )
            )
            if voiceover.get("enabled") is not True:
                errors.append(f"{prefix}:voiceover_not_enabled")
            errors.extend(
                _validate_assertions(
                    voiceover.get("observations"),
                    list(REQUIRED_VOICEOVER_OBSERVATIONS),
                    prefix=f"{prefix}:voiceover",
                )
            )
    runtime = session.get("runtime_bindings")
    if not isinstance(runtime, Mapping):
        errors.append(f"{prefix}:runtime_bindings_missing")
    else:
        errors.extend(
            _strict_keys(runtime, RUNTIME_BINDING_KEYS, f"{prefix}:runtime_bindings")
        )
        for field, expected in bindings.items():
            if runtime.get(field) != expected:
                errors.append(f"{prefix}:{field}_mismatch")
    console = session.get("console_results")
    if not isinstance(console, Mapping):
        errors.append(f"{prefix}:console_not_clean")
    else:
        errors.extend(
            _strict_keys(console, {"unexpected_errors"}, f"{prefix}:console_results")
        )
        if console.get("unexpected_errors") != []:
            errors.append(f"{prefix}:console_not_clean")
    network_errors, derived_network = _validate_network(
        session.get("network_results"),
        prefix=prefix,
        session_id=session_id,
        artifact_base=artifact_base,
        started=started,
        completed=completed,
    )
    errors.extend(network_errors)
    errors.extend(
        _validate_fixture_status(
            session.get("fixture_status_before"),
            prefix=f"{prefix}:before",
            commit=commit,
            release_id=release_id,
            bindings=bindings,
        )
    )
    errors.extend(
        _validate_fixture_status(
            session.get("fixture_status_after"),
            prefix=f"{prefix}:after",
            commit=commit,
            release_id=release_id,
            bindings=bindings,
        )
    )
    before = session.get("fixture_status_before")
    after = session.get("fixture_status_after")
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for field in ("native_bundle_digest", "native_payload_digest"):
            if before.get(field) != after.get(field):
                errors.append(f"{prefix}:fixture_{field}_changed")
    errors.extend(_validate_journeys(session.get("journeys"), manifest, prefix=prefix))
    media_errors, derived_media = _validate_media(
        session.get("media"),
        prefix=prefix,
        session_id=session_id,
        started=started,
        completed=completed,
        artifact_base=artifact_base,
        require_audio=session_id == "voiceover",
    )
    errors.extend(media_errors)
    return errors, {"network": derived_network, "media": derived_media}


def validate_capture(
    capture: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    source: Mapping[str, Any],
    artifact_base: Path,
    bootstrap_path: Path = DEFAULT_BOOTSTRAP,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a retained record whose pass/fail is derived only from validation."""
    errors: list[str] = []
    derived_sessions: dict[str, Any] = {}
    validation_now = now or datetime.now(timezone.utc)
    if validation_now.tzinfo is None:
        raise ValueError("validation time must be timezone-aware")
    validation_now = validation_now.astimezone(timezone.utc)
    errors.extend(_strict_keys(capture, CAPTURE_KEYS, "capture"))
    manifest_sha256 = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    bindings = _runtime_bindings(bootstrap_path)
    commit = source.get("commit")
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        errors.append("git_commit_invalid")
        commit = ""
    if source.get("working_tree_dirty") is not False:
        errors.append("working_tree_not_clean")
    release_id = fixture_release_id(commit) if commit else ""
    if capture.get("schema") != CAPTURE_SCHEMA:
        errors.append("capture_schema_invalid")
    if not _exact_value(capture.get("schema_version"), SCHEMA_VERSION):
        errors.append("capture_schema_version_invalid")
    if capture.get("gate") != "REL-01":
        errors.append("capture_gate_invalid")
    if capture.get("disposition") != "EXECUTED":
        errors.append("capture_not_executed")
    if _contains_operator_waiver(capture):
        errors.append("operator_waiver_present")
    if capture.get("qualification_credit_requested") is not True:
        errors.append("qualification_credit_not_requested")
    source_binding = capture.get("source_binding")
    expected_source = {
        "git_commit": commit,
        "release_id": release_id,
        "manifest_sha256": manifest_sha256,
    }
    if not isinstance(source_binding, Mapping):
        errors.append("source_binding_missing")
    else:
        errors.extend(
            _strict_keys(source_binding, SOURCE_BINDING_KEYS, "source_binding")
        )
        for field, expected in expected_source.items():
            if source_binding.get(field) != expected:
                errors.append(f"source_{field}_mismatch")
    captured_bindings = capture.get("runtime_bindings")
    if not isinstance(captured_bindings, Mapping):
        errors.append("runtime_bindings_missing")
    else:
        errors.extend(
            _strict_keys(captured_bindings, RUNTIME_BINDING_KEYS, "runtime_bindings")
        )
        for field, expected in bindings.items():
            if captured_bindings.get(field) != expected:
                errors.append(f"{field}_mismatch")
    device = capture.get("device")
    if not isinstance(device, Mapping):
        errors.append("device_missing")
    else:
        errors.extend(_strict_keys(device, DEVICE_KEYS, "device"))
        exact_device = {
            "device_class": "physical_iphone",
            "simulator": False,
            "continuity_camera_only": False,
            "available": True,
            "paired": True,
            "safari_web_inspector_target": True,
            "coredevice_state": "available",
        }
        for field, expected in exact_device.items():
            if not _exact_value(device.get(field), expected):
                errors.append(f"device_{field}_invalid")
        if not isinstance(device.get("name"), str) or not device["name"].strip():
            errors.append("device_name_missing")
        if (
            not isinstance(device.get("model_name"), str)
            or "iPhone" not in device["model_name"]
        ):
            errors.append("device_model_name_invalid")
        if (
            not isinstance(device.get("model_identifier"), str)
            or MODEL_PATTERN.fullmatch(device["model_identifier"]) is None
        ):
            errors.append("device_model_identifier_invalid")
        if (
            not isinstance(device.get("udid"), str)
            or UDID_PATTERN.fullmatch(device["udid"]) is None
        ):
            errors.append("device_udid_invalid")
        if device.get("connection_transport") not in {"usb", "network"}:
            errors.append("device_connection_transport_invalid")
        for field in ("ios_version", "ios_build", "safari_version", "webkit_version"):
            if not isinstance(device.get(field), str) or not device[field].strip():
                errors.append(f"device_{field}_missing")
    sessions = capture.get("sessions")
    if not isinstance(sessions, list):
        errors.append("sessions_missing")
    else:
        by_id: dict[str, Mapping[str, Any]] = {}
        for session in sessions:
            if not isinstance(session, Mapping) or not isinstance(
                session.get("session_id"), str
            ):
                errors.append("session_invalid")
                continue
            session_id = session["session_id"]
            if session_id in by_id:
                errors.append(f"duplicate_session:{session_id}")
            by_id[session_id] = session
        if set(by_id) != set(REQUIRED_SESSIONS):
            errors.append("session_set_mismatch")
        listed_ids = [
            item.get("session_id") for item in sessions if isinstance(item, Mapping)
        ]
        if listed_ids != list(REQUIRED_SESSIONS):
            errors.append("session_order_invalid")
        if commit:
            prior_completed: datetime | None = None
            for session_id in REQUIRED_SESSIONS:
                session = by_id.get(session_id)
                if session is not None:
                    session_errors, derived = _validate_session(
                        session,
                        session_id=session_id,
                        manifest=manifest,
                        commit=commit,
                        release_id=release_id,
                        bindings=bindings,
                        artifact_base=artifact_base,
                        now=validation_now,
                    )
                    errors.extend(session_errors)
                    derived_sessions[session_id] = derived
                    started = _timestamp(session.get("started_at"))
                    completed = _timestamp(session.get("completed_at"))
                    if (
                        prior_completed is not None
                        and started is not None
                        and started < prior_completed
                    ):
                        errors.append(f"session:{session_id}:overlaps_previous_session")
                    if completed is not None:
                        prior_completed = completed
            artifact_owners: dict[tuple[str, str], str] = {}
            for session_id in REQUIRED_SESSIONS:
                session = by_id.get(session_id)
                if not isinstance(session, Mapping):
                    continue
                network = session.get("network_results")
                candidates: list[tuple[Any, Any]] = []
                if isinstance(network, Mapping):
                    candidates.append(
                        (network.get("trace_path"), network.get("trace_sha256"))
                    )
                media = session.get("media")
                if isinstance(media, list):
                    candidates.extend(
                        (item.get("path"), item.get("sha256"))
                        for item in media
                        if isinstance(item, Mapping)
                    )
                for raw_path, raw_digest in candidates:
                    for kind, value in (("path", raw_path), ("sha256", raw_digest)):
                        if not isinstance(value, str):
                            continue
                        identity = (kind, value)
                        owner = artifact_owners.get(identity)
                        if owner is not None and owner != session_id:
                            errors.append(
                                f"session:{session_id}:artifact_reused_from:{owner}:{kind}"
                            )
                        else:
                            artifact_owners[identity] = session_id
    passed = not errors
    return {
        "schema": RETAINED_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "gate": "REL-01",
        "evidence_class": "external_physical_iphone",
        "manifest_sha256": manifest_sha256,
        "git_commit": commit or None,
        "working_tree_dirty": source.get("working_tree_dirty"),
        "release_id": release_id or None,
        "runtime_bindings": bindings,
        "outcome": "PASS" if passed else "FAIL",
        "external_evidence_satisfied": passed,
        "release_gate_satisfied": False,
        "validation_errors": errors,
        "derived_session_artifacts": derived_sessions,
        "capture": dict(capture),
    }


def capture_template(
    *,
    manifest: Mapping[str, Any],
    source: Mapping[str, Any],
    bootstrap_path: Path = DEFAULT_BOOTSTRAP,
) -> dict[str, Any]:
    """Return a manifest-shaped, deliberately non-passing operator worksheet."""
    commit = source.get("commit")
    release_id = (
        fixture_release_id(commit)
        if isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit)
        else None
    )
    manifest_sha256 = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    bindings = _runtime_bindings(bootstrap_path)

    journeys: list[dict[str, Any]] = []
    for journey_id, contract in manifest["journeys"].items():
        journey: dict[str, Any] = {
            "journey_id": journey_id,
            "outcome": "NOT_EXECUTED",
            "assertions": [
                {"assertion_id": item, "passed": False, "detail": ""}
                for item in contract["required_assertions"]
            ],
        }
        if "viewport" in contract:
            journey["viewport"] = deepcopy(contract["viewport"])
        else:
            journey["viewports"] = deepcopy(contract["viewports"])
            journey["viewport_observations"] = [
                {
                    **deepcopy(viewport),
                    "outcome": "NOT_EXECUTED",
                    "assertions": [
                        {"assertion_id": item, "passed": False, "detail": ""}
                        for item in contract["required_viewport_assertions"]
                    ],
                }
                for viewport in contract["viewports"]
            ]
        journeys.append(journey)

    def session(session_id: str) -> dict[str, Any]:
        result = {
            "session_id": session_id,
            "started_at": None,
            "completed_at": None,
            "outcome": "NOT_EXECUTED",
            "navigator_standalone": None,
            "safari_chrome_visible": None,
            "home_screen_install_present": None,
            "voiceover": {
                "enabled": False,
                "observations": (
                    [
                        {"assertion_id": item, "passed": False, "detail": ""}
                        for item in REQUIRED_VOICEOVER_OBSERVATIONS
                    ]
                    if session_id == "voiceover"
                    else []
                ),
            },
            "runtime_bindings": deepcopy(bindings),
            "console_results": {"unexpected_errors": []},
            "network_results": {
                "session_id": session_id,
                "trace_path": None,
                "trace_format": "har",
                "trace_byte_count": 0,
                "trace_sha256": None,
            },
            "fixture_status_before": None,
            "fixture_status_after": None,
            "journeys": deepcopy(journeys),
            "media": [],
        }
        if session_id == "voiceover":
            result["surface"] = "safari"
        return result

    return {
        "schema": CAPTURE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "gate": "REL-01",
        "disposition": "NOT_EXECUTED",
        "qualification_credit_requested": False,
        "source_binding": {
            "git_commit": commit,
            "release_id": release_id,
            "manifest_sha256": manifest_sha256,
        },
        "runtime_bindings": bindings,
        "device": {
            "device_class": "physical_iphone",
            "simulator": None,
            "continuity_camera_only": None,
            "available": None,
            "paired": None,
            "safari_web_inspector_target": None,
            "coredevice_state": None,
            "connection_transport": None,
            "name": None,
            "model_name": None,
            "model_identifier": None,
            "udid": None,
            "ios_version": None,
            "ios_build": None,
            "safari_version": None,
            "webkit_version": None,
        },
        "sessions": [session(item) for item in REQUIRED_SESSIONS],
    }


def retain_capture(
    *,
    input_path: Path,
    output_path: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    bootstrap_path: Path = DEFAULT_BOOTSTRAP,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    try:
        capture = json.loads(
            input_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
        if not isinstance(capture, Mapping):
            raise ValueError("capture root must be an object")
        retained = validate_capture(
            capture,
            manifest=manifest,
            source=git_source(),
            artifact_base=input_path.resolve().parent,
            bootstrap_path=bootstrap_path,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        retained = {
            "schema": RETAINED_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "gate": "REL-01",
            "evidence_class": "external_physical_iphone",
            "outcome": "FAIL",
            "external_evidence_satisfied": False,
            "release_gate_satisfied": False,
            "validation_errors": [f"capture_unreadable:{exc}"],
            "derived_session_artifacts": {},
            "capture": None,
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(retained, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output_path)
    return retained


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and retain fail-closed physical-iPhone REL-01 evidence."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--input", type=Path)
    action.add_argument("--write-template", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, type=Path)
    parser.add_argument("--bootstrap", default=DEFAULT_BOOTSTRAP, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.write_template is not None:
        template = capture_template(
            manifest=load_manifest(args.manifest),
            source=git_source(),
            bootstrap_path=args.bootstrap,
        )
        args.write_template.parent.mkdir(parents=True, exist_ok=True)
        args.write_template.write_text(
            json.dumps(template, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(template, indent=2, sort_keys=True))
        return 0
    if args.output is None:
        _parser().error("--output is required with --input")
    assert args.input is not None
    retained = retain_capture(
        input_path=args.input,
        output_path=args.output,
        manifest_path=args.manifest,
        bootstrap_path=args.bootstrap,
    )
    print(json.dumps(retained, indent=2, sort_keys=True))
    return 0 if retained.get("external_evidence_satisfied") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
