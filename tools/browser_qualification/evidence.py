#!/usr/bin/env python3
"""Run the portable REL-01 matrix and retain fail-closed JSON evidence.

This runner never upgrades a portable browser result into the complete REL-01
release gate. Physical iPhone Safari, installed mode, VoiceOver, wall output,
controller/receiver performance, and electrical evidence remain separate gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.browser_qualification.source_identity import fixture_release_id


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("rel01_manifest.json")
PLAYWRIGHT_PROBE = Path(__file__).with_name("playwright_probe.mjs")
EVIDENCE_SCHEMA = "ledgrid.rel01-browser-qualification-evidence"
EVIDENCE_SCHEMA_VERSION = 1
MANIFEST_SCHEMA = "ledgrid.rel01-browser-qualification-manifest"
REQUIRED_ENGINES = ("chromium", "firefox", "webkit")
ENGINE_PROCESS_MIN_TIMEOUT_SECONDS = 180


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unsupported browser qualification manifest schema")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported browser qualification manifest version")
    if tuple(payload.get("required_engines", ())) != REQUIRED_ENGINES:
        raise ValueError("manifest must require Chromium, Firefox, and WebKit exactly")
    if payload.get("offline_strategies") != {
        "chromium": "native_network_offline",
        "firefox": "fixture_origin_outage",
        "webkit": "fixture_origin_outage",
    }:
        raise ValueError("manifest must declare the exact per-engine offline strategies")
    journeys = payload.get("journeys")
    if not isinstance(journeys, dict) or set(journeys) != {
        "core_no_mutation",
        "offline_reconnect",
        "worker_recovery",
        "responsive_layouts",
        "keyboard_only_desktop",
        "global_controls",
        "profile_masks",
        "python_native_clock",
    }:
        raise ValueError("manifest must define the complete REL-01 portable journey set")
    if not isinstance(payload.get("playwright_version"), str):
        raise ValueError("manifest must pin the Playwright version")
    for journey_id, journey in journeys.items():
        viewport = journey.get("viewport")
        assertions = journey.get("required_assertions")
        viewports = journey.get("viewports")
        if viewport is not None:
            if not isinstance(viewport, dict) or not all(
                isinstance(viewport.get(field), int) and viewport[field] > 0
                for field in ("width", "height")
            ):
                raise ValueError(f"{journey_id} has an invalid viewport")
        elif viewports is not None:
            if (
                not isinstance(viewports, list)
                or not viewports
                or len({item.get("name") for item in viewports}) != len(viewports)
                or not all(
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and item["name"]
                    and isinstance(item.get("width"), int)
                    and item["width"] > 0
                    and isinstance(item.get("height"), int)
                    and item["height"] > 0
                    for item in viewports
                )
            ):
                raise ValueError(f"{journey_id} has invalid viewports")
            viewport_assertions = journey.get("required_viewport_assertions")
            if (
                not isinstance(viewport_assertions, list)
                or not viewport_assertions
                or len(viewport_assertions) != len(set(viewport_assertions))
            ):
                raise ValueError(f"{journey_id} has invalid viewport assertions")
        else:
            raise ValueError(f"{journey_id} must define viewport evidence")
        if (
            not isinstance(assertions, list)
            or not assertions
            or len(assertions) != len(set(assertions))
            or not all(isinstance(item, str) and item for item in assertions)
        ):
            raise ValueError(f"{journey_id} has invalid required assertions")
    mask_offsets = journeys["profile_masks"].get("engine_led_offsets")
    if (
        not isinstance(mask_offsets, dict)
        or set(mask_offsets) != set(REQUIRED_ENGINES)
        or any(
            type(mask_offsets[engine]) is not int
            or not 0 <= mask_offsets[engine] < 138
            for engine in REQUIRED_ENGINES
        )
        or len(set(mask_offsets.values())) != len(REQUIRED_ENGINES)
    ):
        raise ValueError("profile mask coordinates must be distinct for every engine")
    native_reason = journeys["python_native_clock"].get(
        "managed_native_ineligibility_reason"
    )
    if not isinstance(native_reason, str) or not native_reason.strip():
        raise ValueError("managed native host ineligibility reason must be declared")
    for journey_id, field in (
        ("core_no_mutation", "background_name"),
        ("offline_reconnect", "background_name"),
        ("worker_recovery", "background_name"),
        ("python_native_clock", "python_background_name"),
        ("python_native_clock", "managed_native_background_name"),
    ):
        value = journeys[journey_id].get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{journey_id} must pin {field}")
    exclusions = set(payload.get("excluded_claims", ()))
    required_exclusions = {
        "physical_iphone_safari",
        "ios_installed_standalone",
        "voiceover",
        "physical_wall_output",
        "controller_or_receiver_performance",
        "electrical_safety",
    }
    if not required_exclusions.issubset(exclusions):
        raise ValueError("manifest conflates portable browser and external evidence")
    return payload


def git_source(root: Path = ROOT) -> dict[str, Any]:
    commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=normal"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return {"commit": commit, "working_tree_dirty": dirty}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def validate_engine_result(
    result: Mapping[str, Any],
    engine: str,
    manifest: Mapping[str, Any],
) -> list[str]:
    """Return every reason this engine result cannot count as a pass."""
    errors: list[str] = []
    if result.get("requested_engine") != engine:
        errors.append("requested_engine_mismatch")
    if result.get("reported_engine") != engine:
        errors.append("reported_engine_mismatch")
    if result.get("executed") is not True:
        errors.append("engine_not_executed")
    version = result.get("browser_version")
    if not isinstance(version, str) or not version.strip():
        errors.append("browser_version_missing")
    if result.get("playwright_version") != manifest.get("playwright_version"):
        errors.append("playwright_version_mismatch")
    if result.get("offline_strategy") != manifest["offline_strategies"][engine]:
        errors.append("offline_strategy_mismatch")
    fixture_status = result.get("fixture_status")
    if not isinstance(fixture_status, Mapping):
        errors.append("fixture_status_missing")
    else:
        if fixture_status.get("schema") != "ledgrid.browser-qualification-fixture-status":
            errors.append("fixture_status_schema_invalid")
        if fixture_status.get("schema_version") != 2:
            errors.append("fixture_status_version_invalid")
        if fixture_status.get("wall_consumer_attached") is not False:
            errors.append("fixture_wall_consumer_attached")
        if fixture_status.get("wall_mutation_attempts") != 0:
            errors.append("fixture_wall_mutation_attempted")
        if fixture_status.get("native_plugin_id") != "aurora_curtains_native":
            errors.append("fixture_native_plugin_mismatch")
        outage_blocks = fixture_status.get("network_outage_blocks")
        if type(outage_blocks) is not int or outage_blocks < 0:
            errors.append("fixture_network_outage_count_invalid")
        if engine == "webkit" and (
            not isinstance(fixture_status.get("network_outage_paths"), list)
            or outage_blocks is None
            or outage_blocks < 1
            or "/composer" not in fixture_status["network_outage_paths"]
        ):
            errors.append("fixture_webkit_origin_outage_unproven")
        for field in (
            "profile_digest",
            "native_bundle_digest",
            "native_payload_digest",
            "release_id",
            "controller_release_id",
        ):
            if re.fullmatch(r"[0-9a-f]{64}", str(fixture_status.get(field) or "")) is None:
                errors.append(f"fixture_{field}_invalid")
        if fixture_status.get("release_consistent") is not True:
            errors.append("fixture_release_inconsistent")
        if fixture_status.get("release_id") != fixture_status.get(
            "controller_release_id"
        ):
            errors.append("fixture_release_identity_mismatch")
        if re.fullmatch(
            r"[0-9a-f]{40}", str(fixture_status.get("source_commit") or "")
        ) is None:
            errors.append("fixture_source_commit_invalid")
        else:
            expected_release_id = fixture_release_id(
                fixture_status["source_commit"]
            )
            if fixture_status.get("release_id") != expected_release_id:
                errors.append("fixture_release_id_not_source_bound")
    started = _parse_timestamp(result.get("started_at"))
    completed = _parse_timestamp(result.get("completed_at"))
    if started is None or completed is None or completed < started:
        errors.append("engine_timestamps_invalid")

    journeys = result.get("journeys")
    if not isinstance(journeys, list):
        return errors + ["journeys_missing"]
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in journeys:
        if not isinstance(item, Mapping) or not isinstance(item.get("journey_id"), str):
            errors.append("journey_record_invalid")
            continue
        journey_id = item["journey_id"]
        if journey_id in by_id:
            errors.append(f"duplicate_journey:{journey_id}")
        by_id[journey_id] = item

    for journey_id, contract in manifest["journeys"].items():
        journey = by_id.get(journey_id)
        if journey is None:
            errors.append(f"missing_journey:{journey_id}")
            continue
        if "viewport" in contract:
            if journey.get("viewport") != contract["viewport"]:
                errors.append(f"viewport_mismatch:{journey_id}")
        else:
            if journey.get("viewports") != contract["viewports"]:
                errors.append(f"viewport_matrix_mismatch:{journey_id}")
            observations = journey.get("viewport_observations")
            if not isinstance(observations, list):
                errors.append(f"viewport_observations_missing:{journey_id}")
            else:
                observations_by_name = {
                    item.get("name"): item
                    for item in observations
                    if isinstance(item, Mapping) and isinstance(item.get("name"), str)
                }
                for expected in contract["viewports"]:
                    name = expected["name"]
                    observation = observations_by_name.get(name)
                    if observation is None:
                        errors.append(f"viewport_observation_missing:{journey_id}:{name}")
                        continue
                    if any(observation.get(field) != expected[field] for field in ("name", "width", "height")):
                        errors.append(f"viewport_identity_mismatch:{journey_id}:{name}")
                    if observation.get("outcome") != "PASS":
                        errors.append(f"viewport_not_passed:{journey_id}:{name}")
                    viewport_assertions = observation.get("assertions")
                    viewport_by_id = {}
                    if isinstance(viewport_assertions, list):
                        viewport_by_id = {
                            item.get("assertion_id"): item
                            for item in viewport_assertions
                            if isinstance(item, Mapping)
                            and isinstance(item.get("assertion_id"), str)
                        }
                    for assertion_id in contract["required_viewport_assertions"]:
                        assertion = viewport_by_id.get(assertion_id)
                        if assertion is None:
                            errors.append(
                                f"viewport_assertion_missing:{journey_id}:{name}:{assertion_id}"
                            )
                        elif assertion.get("passed") is not True:
                            errors.append(
                                f"viewport_assertion_failed:{journey_id}:{name}:{assertion_id}"
                            )
                if set(observations_by_name) != {
                    item["name"] for item in contract["viewports"]
                }:
                    errors.append(f"viewport_observation_set_mismatch:{journey_id}")
        assertions = journey.get("assertions")
        if not isinstance(assertions, list):
            errors.append(f"assertions_missing:{journey_id}")
            continue
        assertions_by_id: dict[str, Mapping[str, Any]] = {}
        for assertion in assertions:
            if not isinstance(assertion, Mapping) or not isinstance(
                assertion.get("assertion_id"), str
            ):
                errors.append(f"assertion_record_invalid:{journey_id}")
                continue
            assertion_id = assertion["assertion_id"]
            if assertion_id in assertions_by_id:
                errors.append(f"duplicate_assertion:{journey_id}:{assertion_id}")
            assertions_by_id[assertion_id] = assertion
        for assertion_id in contract["required_assertions"]:
            assertion = assertions_by_id.get(assertion_id)
            if assertion is None:
                errors.append(f"missing_assertion:{journey_id}:{assertion_id}")
            elif assertion.get("passed") is not True:
                errors.append(f"failed_assertion:{journey_id}:{assertion_id}")
        if journey.get("outcome") != "PASS":
            errors.append(f"journey_not_passed:{journey_id}")
    if set(by_id) != set(manifest["journeys"]):
        errors.append("journey_set_mismatch")
    if result.get("outcome") != "PASS":
        errors.append("engine_outcome_not_passed")
    return errors


def aggregate_evidence(
    *,
    manifest: Mapping[str, Any],
    source: Mapping[str, Any],
    engine_results: Mapping[str, Mapping[str, Any]],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    validations: dict[str, list[str]] = {}
    retained_results: list[dict[str, Any]] = []
    for engine in manifest["required_engines"]:
        result = dict(engine_results.get(engine, {}))
        validations[engine] = validate_engine_result(result, engine, manifest)
        fixture_status = result.get("fixture_status")
        if (
            isinstance(fixture_status, Mapping)
            and fixture_status.get("source_commit") != source.get("commit")
        ):
            validations[engine].append("fixture_source_commit_mismatch")
        result["validation_errors"] = validations[engine]
        retained_results.append(result)

    source_errors: list[str] = []
    commit = source.get("commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        source_errors.append("git_commit_invalid")
    if source.get("working_tree_dirty") is not False:
        source_errors.append("working_tree_not_clean")
    portable_pass = not source_errors and not any(validations.values())
    portable_outcome = "PASS" if portable_pass else "FAIL"
    rel01_outcome = (
        "PENDING_EXTERNAL_EVIDENCE"
        if portable_pass
        else "PENDING_PORTABLE_AND_EXTERNAL_EVIDENCE"
    )
    manifest_digest = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    return {
        "schema": EVIDENCE_SCHEMA,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "gate": manifest["gate"],
        "evidence_class": manifest["evidence_class"],
        "manifest_sha256": manifest_digest,
        "git_commit": commit,
        "working_tree_dirty": source.get("working_tree_dirty"),
        "started_at": started_at,
        "completed_at": completed_at,
        "outcomes": {
            "portable_browser_matrix": portable_outcome,
            "rel01_release_gate": rel01_outcome,
        },
        "release_gate_satisfied": False,
        "source_validation_errors": source_errors,
        "required_engines": list(manifest["required_engines"]),
        "results": retained_results,
        "excluded_claims": list(manifest["excluded_claims"]),
    }


def _missing_engine_result(engine: str, reason: str) -> dict[str, Any]:
    timestamp = _utc_now()
    return {
        "requested_engine": engine,
        "reported_engine": None,
        "browser_version": None,
        "playwright_version": None,
        "executed": False,
        "started_at": timestamp,
        "completed_at": timestamp,
        "outcome": "FAIL",
        "journeys": [],
        "fixture_status": None,
        "runner": {"exit_code": None, "error": reason},
    }


def resolve_playwright_module(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit.resolve() if explicit.is_dir() else None
    candidates: list[Path] = []
    configured = os.environ.get("LEDGRID_PLAYWRIGHT_MODULE")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        (
            Path(__file__).with_name("node_modules") / "playwright",
            ROOT / "node_modules" / "playwright",
        )
    )
    return next((path.resolve() for path in candidates if path.is_dir()), None)


def execute_playwright_engine(
    *,
    engine: str,
    base_url: str,
    manifest_path: Path,
    playwright_module: Path,
    output_path: Path,
    timeout_ms: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        (
            "node",
            os.fspath(PLAYWRIGHT_PROBE),
            "--engine",
            engine,
            "--base-url",
            base_url,
            "--manifest",
            os.fspath(manifest_path),
            "--playwright-module",
            os.fspath(playwright_module),
            "--output",
            os.fspath(output_path),
            "--timeout-ms",
            str(timeout_ms),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(
            ENGINE_PROCESS_MIN_TIMEOUT_SECONDS,
            timeout_ms // 1000 * 6,
        ),
    )
    if not output_path.is_file():
        reason = "Playwright probe did not produce an evidence result"
        if completed.stderr.strip():
            reason = f"{reason}: {completed.stderr.strip()[-1000:]}"
        return _missing_engine_result(engine, reason)
    try:
        result = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _missing_engine_result(engine, f"invalid probe result: {exc}")
    result["runner"] = {
        "exit_code": completed.returncode,
        "stderr": completed.stderr.strip()[-2000:] or None,
    }
    return result


def run_qualification(
    *,
    base_url: str,
    output_path: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    playwright_module: Path | None = None,
    timeout_ms: int = 180_000,
    source_provider: Callable[[], Mapping[str, Any]] = git_source,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    started_at = _utc_now()
    resolved_module = resolve_playwright_module(playwright_module)
    results: dict[str, Mapping[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="ledgrid-rel01-") as temporary:
        temporary_path = Path(temporary)
        for engine in manifest["required_engines"]:
            if resolved_module is None:
                results[engine] = _missing_engine_result(
                    engine,
                    "Playwright is unavailable; install the pinned tooling dependency or set LEDGRID_PLAYWRIGHT_MODULE",
                )
                continue
            results[engine] = execute_playwright_engine(
                engine=engine,
                base_url=base_url,
                manifest_path=manifest_path,
                playwright_module=resolved_module,
                output_path=temporary_path / f"{engine}.json",
                timeout_ms=timeout_ms,
            )
    evidence = aggregate_evidence(
        manifest=manifest,
        source=source_provider(),
        engine_results=results,
        started_at=started_at,
        completed_at=_utc_now(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary_output.replace(output_path)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retain fail-closed Chromium/Firefox/WebKit REL-01 evidence."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--playwright-module", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=180_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    evidence = run_qualification(
        base_url=args.base_url,
        output_path=args.output,
        manifest_path=args.manifest,
        playwright_module=args.playwright_module,
        timeout_ms=args.timeout_ms,
    )
    print(json.dumps(evidence["outcomes"], sort_keys=True))
    return 0 if evidence["outcomes"]["portable_browser_matrix"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
