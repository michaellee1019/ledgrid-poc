#!/usr/bin/env python3
"""Small, testable deployment coordinator.

This module deliberately owns orchestration policy only.  Rsync, systemd,
firmware and provisioning remain leaf commands supplied as argument arrays by
the caller.  That keeps the existing shell helpers usable while making order,
failure handling, redaction, receipts and health acceptance deterministic.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
import uuid


REDACTED = "<redacted>"
SENSITIVE_ARGUMENTS = frozenset(
    {
        "-i",
        "--identity-file",
        "--key-file",
        "--private-key",
        "--secret",
        "--token",
        "--password",
    }
)
SECRET_NAME_PATTERN = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|ACCESS_KEY|AUTH)",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    """Return a compact JSON-safe value without inventing a general database."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class Artifact:
    kind: str
    id: str
    digest: str
    version: str
    target_id: Optional[str] = None

    def to_dict(self, redactor: Optional["Redactor"] = None) -> Dict[str, str]:
        sanitize = redactor.text if redactor is not None else str
        payload = {
            "kind": sanitize(self.kind),
            "id": sanitize(self.id),
            "digest": sanitize(self.digest),
            "version": sanitize(self.version),
        }
        if self.target_id is not None:
            payload["target_id"] = sanitize(self.target_id)
        return payload


class Redactor:
    """Redact secrets from commands, diagnostics, logs and receipts.

    Environment *values* are intentionally never accepted as receipt data.
    Configured secret values are used only for replacement in memory.
    """

    def __init__(
        self,
        *,
        secret_names: Iterable[str] = (),
        secret_paths: Iterable[os.PathLike[str] | str] = (),
        secret_values: Iterable[str] = (),
    ) -> None:
        self.secret_names = frozenset(name for name in secret_names if name)
        values = [os.fspath(path) for path in secret_paths]
        values.extend(value for value in secret_values if value)
        self._values = tuple(sorted(set(values), key=len, reverse=True))

    def _is_secret_name(self, name: str) -> bool:
        return name in self.secret_names or bool(SECRET_NAME_PATTERN.search(name))

    def text(self, value: Any) -> str:
        result = str(value)
        for secret in self._values:
            result = result.replace(secret, REDACTED)
        # Redact common NAME=value forms even when the value was not registered.
        for name in self.secret_names:
            result = re.sub(
                rf"(?<![A-Za-z0-9_])({re.escape(name)})=([^\s]+)",
                rf"\1={REDACTED}",
                result,
            )
        result = re.sub(
            r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|ACCESS_KEY|AUTH)[A-Za-z0-9_]*)=([^\s]+)",
            rf"\1={REDACTED}",
            result,
            flags=re.IGNORECASE,
        )
        return result

    def command(self, args: Sequence[os.PathLike[str] | str]) -> Tuple[str, ...]:
        redacted: List[str] = []
        hide_next = False
        for raw_arg in args:
            arg = os.fspath(raw_arg)
            if hide_next:
                redacted.append(REDACTED)
                hide_next = False
                continue

            name, separator, value = arg.partition("=")
            if arg in SENSITIVE_ARGUMENTS:
                redacted.append(arg)
                hide_next = True
            elif separator and (name in SENSITIVE_ARGUMENTS or self._is_secret_name(name)):
                redacted.append(f"{name}={REDACTED}")
            else:
                redacted.append(self.text(arg))
        return tuple(redacted)


@dataclass(frozen=True)
class CommandResult:
    args: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


class CommandFailed(RuntimeError):
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        super().__init__(f"command exited {result.returncode}: {detail}")


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[os.PathLike[str] | str],
        *,
        cwd: Optional[os.PathLike[str] | str] = None,
        env: Optional[Mapping[str, str]] = None,
        input_bytes: Optional[bytes] = None,
        timeout: Optional[float] = None,
        check: bool = True,
        log_path: Optional[Path] = None,
    ) -> CommandResult:
        ...


class SubprocessRunner:
    """Argument-array subprocess execution with redacted durable logs."""

    def __init__(self, redactor: Optional[Redactor] = None) -> None:
        self.redactor = redactor or Redactor()

    def run(
        self,
        args: Sequence[os.PathLike[str] | str],
        *,
        cwd: Optional[os.PathLike[str] | str] = None,
        env: Optional[Mapping[str, str]] = None,
        input_bytes: Optional[bytes] = None,
        timeout: Optional[float] = None,
        check: bool = True,
        log_path: Optional[Path] = None,
    ) -> CommandResult:
        normalized = tuple(os.fspath(arg) for arg in args)
        started = time.monotonic()
        completed = subprocess.run(
            normalized,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        duration = time.monotonic() - started
        stdout = self.redactor.text(completed.stdout.decode("utf-8", "replace"))
        stderr = self.redactor.text(completed.stderr.decode("utf-8", "replace"))
        result = CommandResult(
            args=self.redactor.command(normalized),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
        )
        if log_path is not None:
            _append_command_log(log_path, result)
        if check and result.returncode != 0:
            raise CommandFailed(result)
        return result


class SSHRunner:
    """Run a remote leaf command through OpenSSH without local shell parsing."""

    def __init__(
        self,
        runner: CommandRunner,
        target: str,
        *,
        ssh_options: Sequence[str] = (),
    ) -> None:
        self.runner = runner
        self.target = target
        self.ssh_options = tuple(ssh_options)

    def run(
        self,
        args: Sequence[os.PathLike[str] | str],
        **kwargs: Any,
    ) -> CommandResult:
        # OpenSSH accepts one remote command string. shlex.join makes every leaf
        # argument data; no local shell is involved.
        remote_command = shlex.join([os.fspath(arg) for arg in args])
        return self.runner.run(
            ("ssh", *self.ssh_options, self.target, "--", remote_command),
            **kwargs,
        )


def _append_command_log(path: Path, result: CommandResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"$ {shlex.join(result.args)}\n")
        if result.stdout:
            stream.write(result.stdout)
            if not result.stdout.endswith("\n"):
                stream.write("\n")
        if result.stderr:
            stream.write(result.stderr)
            if not result.stderr.endswith("\n"):
                stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class OperationResult:
    artifacts: Tuple[Artifact, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)
    log_reference: Optional[str] = None
    outcome: str = "executed"


Operation = Callable[["DeployContext"], Optional[OperationResult]]


@dataclass(frozen=True)
class Step:
    id: str
    mutating: bool
    operation: Operation
    description: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", self.id):
            raise ValueError(f"invalid namespaced deployment step ID: {self.id!r}")


@dataclass(frozen=True)
class StepResult:
    step_id: str
    mutating: bool
    started_at: str
    finished_at: str
    duration_seconds: float
    outcome: str
    log_reference: Optional[str] = None
    artifacts: Tuple[Artifact, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self, redactor: Redactor) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.step_id,
            "mutating": self.mutating,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 6),
            "outcome": self.outcome,
            "artifacts": [artifact.to_dict(redactor) for artifact in self.artifacts],
            "details": _redact_value(self.details, redactor),
        }
        if self.log_reference is not None:
            payload["log_reference"] = redactor.text(self.log_reference)
        if self.error is not None:
            payload["error"] = redactor.text(self.error)
        return payload


def _redact_value(value: Any, redactor: Redactor) -> Any:
    if isinstance(value, Mapping):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if redactor._is_secret_name(name):
                redacted[name] = REDACTED
            else:
                redacted[name] = _redact_value(item, redactor)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, redactor) for item in value]
    if isinstance(value, str):
        return redactor.text(value)
    return _json_safe(value)


@dataclass(frozen=True)
class HealthExpectation:
    desired_release: str
    restart_started_at: float
    strip_count: int
    leds_per_strip: int
    receiver_count: int
    stable_samples: int = 2
    maximum_sample_age_seconds: float = 3.0
    maximum_future_skew_seconds: float = 1.0


@dataclass(frozen=True)
class HealthSample:
    sampled_at: float
    systemd_active: bool
    controller_updated_at: float
    release_id: str
    strip_count: int
    leds_per_strip: int
    receiver_count: int
    ready: bool = True
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


HealthReader = Callable[[], HealthSample]


class HealthCheckFailed(RuntimeError):
    pass


class FreshHealthChecker:
    """Require stable post-restart health for the exact desired release."""

    def __init__(
        self,
        reader: HealthReader,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.reader = reader
        self.sleep = sleep
        self.clock = clock

    def wait(
        self,
        expectation: HealthExpectation,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
    ) -> Mapping[str, Any]:
        deadline = self.clock() + timeout_seconds
        accepted: List[HealthSample] = []
        last_reason = "no sample"
        while self.clock() <= deadline:
            sample = self.reader()
            reason = self._rejection_reason(sample, expectation)
            if reason is None:
                if accepted and sample.controller_updated_at <= accepted[-1].controller_updated_at:
                    accepted.clear()
                    last_reason = "controller status did not advance between stable samples"
                    self.sleep(poll_interval_seconds)
                    continue
                accepted.append(sample)
                if len(accepted) >= expectation.stable_samples:
                    return {
                        "desired_release": expectation.desired_release,
                        "stable_samples": len(accepted),
                        "last_controller_updated_at": sample.controller_updated_at,
                        "geometry": {
                            "strip_count": sample.strip_count,
                            "leds_per_strip": sample.leds_per_strip,
                        },
                        "receiver_count": sample.receiver_count,
                    }
            else:
                accepted.clear()
                last_reason = reason
            self.sleep(poll_interval_seconds)
        raise HealthCheckFailed(f"fresh readiness timed out: {last_reason}")

    def _rejection_reason(
        self,
        sample: HealthSample,
        expectation: HealthExpectation,
    ) -> Optional[str]:
        if not sample.systemd_active:
            return "systemd service is not active"
        if not sample.ready:
            return "controller did not report ready"
        if sample.release_id != expectation.desired_release:
            return (
                f"observed release {sample.release_id!r}, expected "
                f"{expectation.desired_release!r}"
            )
        if sample.controller_updated_at <= expectation.restart_started_at:
            return "controller status predates restart boundary"
        if sample.controller_updated_at > sample.sampled_at + expectation.maximum_future_skew_seconds:
            return "controller status timestamp is implausibly in the future"
        if sample.sampled_at - sample.controller_updated_at > expectation.maximum_sample_age_seconds:
            return "controller status is stale"
        if (sample.strip_count, sample.leds_per_strip) != (
            expectation.strip_count,
            expectation.leds_per_strip,
        ):
            return "controller geometry does not match desired geometry"
        if sample.receiver_count != expectation.receiver_count:
            return "receiver topology does not match desired topology"
        return None


@dataclass
class DeployContext:
    target: str
    mode: str
    source_identity: Mapping[str, Any]
    source_policy: str = "clean"
    flags: Mapping[str, Any] = field(default_factory=dict)
    paths: Mapping[str, Path] = field(default_factory=dict)
    redactor: Redactor = field(default_factory=Redactor)
    command_runner: Optional[CommandRunner] = None
    ssh_runner: Optional[CommandRunner] = None
    receipt_sinks: Tuple["ReceiptSink", ...] = ()
    progress: Optional[Callable[[str], None]] = None
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: Dict[str, Any] = field(default_factory=dict)

    def command(self, args: Sequence[str], **kwargs: Any) -> CommandResult:
        if self.command_runner is None:
            raise RuntimeError("no local command runner configured")
        return self.command_runner.run(args, **kwargs)

    def ssh(self, args: Sequence[str], **kwargs: Any) -> CommandResult:
        if self.ssh_runner is None:
            raise RuntimeError("no SSH command runner configured")
        return self.ssh_runner.run(args, **kwargs)

    def report(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)


@dataclass
class DeployReceipt:
    deployment_id: str
    started_at: str
    target: str
    mode: str
    source_policy: str
    source_identity: Mapping[str, Any]
    outcome: str = "running"
    finished_at: Optional[str] = None
    steps: List[StepResult] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    health: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None
    # Sink failures describe receipt durability, not whether the app/wall
    # operation itself succeeded. This field is populated after persistence and
    # excluded from the immutable on-disk payload so all successful sinks agree.
    persistence_errors: Tuple[str, ...] = ()

    def to_dict(self, redactor: Redactor) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "deployment_id": self.deployment_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "target": redactor.text(self.target),
            "mode": self.mode,
            "source_policy": self.source_policy,
            "source_identity": _redact_value(self.source_identity, redactor),
            "outcome": self.outcome,
            "steps": [step.to_dict(redactor) for step in self.steps],
            "artifacts": [artifact.to_dict(redactor) for artifact in self.artifacts],
            "health": _redact_value(self.health, redactor),
        }
        if self.error is not None:
            payload["error"] = redactor.text(self.error)
        return payload


class ReceiptSink(Protocol):
    def persist(self, receipt: DeployReceipt, redactor: Redactor) -> str:
        ...


class AtomicJSONReceiptStore:
    """Append-only receipt directory using fsync + atomic rename."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def persist(self, receipt: DeployReceipt, redactor: Redactor) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{receipt.deployment_id}.json"
        if destination.exists():
            raise FileExistsError(f"receipt already exists: {destination}")
        payload = json.dumps(
            receipt.to_dict(redactor), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{receipt.deployment_id}.", suffix=".tmp", dir=self.directory,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Link makes the no-overwrite append-only property atomic.
            os.link(temp_path, destination)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)
        return os.fspath(destination)


_REMOTE_RECEIPT_WRITER = r"""
import os, pathlib, sys, tempfile
directory = pathlib.Path(sys.argv[1])
name = sys.argv[2]
directory.mkdir(parents=True, exist_ok=True)
destination = directory / name
if destination.exists():
    raise SystemExit('receipt already exists')
payload = sys.stdin.buffer.read()
fd, temporary = tempfile.mkstemp(prefix='.' + name + '.', suffix='.tmp', dir=str(directory))
try:
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    os.link(temporary, destination)
    directory_fd = os.open(str(directory), os.O_RDONLY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)
finally:
    try: os.unlink(temporary)
    except FileNotFoundError: pass
""".strip()


class SSHAtomicJSONReceiptStore:
    """Write the same append-only receipt on the target through injected SSH."""

    def __init__(self, ssh_runner: CommandRunner, directory: str) -> None:
        self.ssh_runner = ssh_runner
        self.directory = directory

    def persist(self, receipt: DeployReceipt, redactor: Redactor) -> str:
        payload = json.dumps(
            receipt.to_dict(redactor), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        name = f"{receipt.deployment_id}.json"
        self.ssh_runner.run(
            ("python3", "-c", _REMOTE_RECEIPT_WRITER, self.directory, name),
            input_bytes=payload,
        )
        return f"{self.directory}/{name}"


class DeploymentInterrupted(RuntimeError):
    pass


class DeployCoordinator:
    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.wall_clock = wall_clock
        self.monotonic = monotonic

    def plan(self, steps: Sequence[Step]) -> List[Mapping[str, Any]]:
        return [
            {"id": step.id, "mutating": step.mutating, "description": step.description}
            for step in steps
        ]

    def run(self, context: DeployContext, steps: Sequence[Step]) -> DeployReceipt:
        deployment_started = self.monotonic()
        receipt = DeployReceipt(
            deployment_id=context.attempt_id,
            started_at=_utc_now(),
            target=context.target,
            mode=context.mode,
            source_policy=context.source_policy,
            source_identity=context.source_identity,
        )
        try:
            for index, step in enumerate(steps, start=1):
                started_at = _utc_now()
                started = self.monotonic()
                expectation = STEP_TIMING_EXPECTATIONS.get(step.id)
                suffix = f"; {expectation}" if expectation else ""
                context.report(
                    f"[deploy {index:02d}/{len(steps):02d}] START {step.id}{suffix}"
                )
                try:
                    operation_result = step.operation(context) or OperationResult()
                except (KeyboardInterrupt, DeploymentInterrupted) as exc:
                    duration = self.monotonic() - started
                    result = StepResult(
                        step_id=step.id,
                        mutating=step.mutating,
                        started_at=started_at,
                        finished_at=_utc_now(),
                        duration_seconds=duration,
                        outcome="interrupted",
                        error=str(exc) or "deployment interrupted",
                    )
                    receipt.steps.append(result)
                    context.report(
                        f"[deploy {index:02d}/{len(steps):02d}] INTERRUPTED "
                        f"{step.id} ({_format_duration(duration)})"
                    )
                    raise
                except Exception as exc:
                    duration = self.monotonic() - started
                    result = StepResult(
                        step_id=step.id,
                        mutating=step.mutating,
                        started_at=started_at,
                        finished_at=_utc_now(),
                        duration_seconds=duration,
                        outcome="failed",
                        error=str(exc),
                        details=_exception_details(exc),
                    )
                    receipt.steps.append(result)
                    context.report(
                        f"[deploy {index:02d}/{len(steps):02d}] FAILED {step.id} "
                        f"({_format_duration(duration)}): {context.redactor.text(exc)}"
                    )
                    raise
                duration = self.monotonic() - started
                result = StepResult(
                    step_id=step.id,
                    mutating=step.mutating,
                    started_at=started_at,
                    finished_at=_utc_now(),
                    duration_seconds=duration,
                    outcome=operation_result.outcome,
                    log_reference=operation_result.log_reference,
                    artifacts=operation_result.artifacts,
                    details=operation_result.details,
                )
                receipt.steps.append(result)
                receipt.artifacts.extend(operation_result.artifacts)
                if step.id == "health.readiness":
                    receipt.health = dict(operation_result.details)
                context.report(
                    f"[deploy {index:02d}/{len(steps):02d}] DONE {step.id} "
                    f"({operation_result.outcome}, {_format_duration(duration)})"
                )
            receipt.outcome = "success"
        except (KeyboardInterrupt, DeploymentInterrupted) as exc:
            receipt.outcome = "interrupted"
            receipt.error = str(exc) or "deployment interrupted"
        except Exception as exc:
            receipt.outcome = "failure"
            receipt.error = str(exc)
        finally:
            receipt.finished_at = _utc_now()
            persist_errors: List[str] = []
            for sink in context.receipt_sinks:
                try:
                    sink.persist(receipt, context.redactor)
                except Exception as exc:
                    persist_errors.append(context.redactor.text(exc))
            receipt.persistence_errors = tuple(persist_errors)
            elapsed = _format_duration(self.monotonic() - deployment_started)
            if persist_errors:
                context.report(
                    f"[deploy] FAILURE in {elapsed}; receipt persistence failed"
                )
            else:
                context.report(f"[deploy] {receipt.outcome.upper()} in {elapsed}")
        return receipt


def _format_duration(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    minutes, remaining = divmod(rounded, 60)
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{seconds:.1f}s"


def _exception_details(exc: Exception) -> Mapping[str, Any]:
    if isinstance(exc, CommandFailed):
        return {
            "command": list(exc.result.args),
            "returncode": exc.result.returncode,
            "stdout_tail": exc.result.stdout[-2000:],
            "stderr_tail": exc.result.stderr[-2000:],
        }
    failure = getattr(exc, "failure", None)
    failure_to_dict = getattr(failure, "to_dict", None)
    if callable(failure_to_dict):
        return {"activation_failure": failure_to_dict()}
    return {}


FULL_STEP_ORDER: Tuple[Tuple[str, bool, str], ...] = (
    ("source.validate", False, "validate clean or explicit dirty source policy"),
    ("tests.run", False, "run the selected local regression gate"),
    ("target.connect", False, "verify SSH and deployment privileges"),
    ("app.stage", True, "stage the immutable application release"),
    ("receiver.firmware_build", False, "build or select receiver firmware"),
    ("host.provision", True, "reconcile host prerequisites and service definition"),
    ("receiver.firmware_flash", True, "reconcile receiver firmware"),
    ("app.validate", False, "validate staged imports and static structure"),
    ("state.capture", True, "preserve active operator settings"),
    ("app.activate", True, "atomically select the desired app release"),
    ("host.restart", True, "restart the app service"),
    ("state.restore", True, "restore preserved operator settings"),
    ("health.readiness", False, "require fresh desired-release readiness"),
    ("release.prune", True, "retain a bounded rollback-safe app release set"),
)

PYTHON_STEP_ORDER: Tuple[Tuple[str, bool, str], ...] = (
    ("source.validate", False, "validate clean or explicit dirty source policy"),
    ("tests.run", False, "run the selected local regression gate"),
    ("target.connect", False, "verify SSH and deployment privileges"),
    ("app.stage", True, "stage the immutable application release"),
    ("app.validate", False, "validate staged imports and static structure"),
    ("state.capture", True, "preserve active operator settings"),
    ("app.activate", True, "atomically select the desired app release"),
    ("host.restart", True, "restart the app service"),
    ("state.restore", True, "restore preserved operator settings"),
    ("health.readiness", False, "require fresh desired-release readiness"),
    ("release.prune", True, "retain a bounded rollback-safe app release set"),
)

ROLLBACK_STEP_ORDER: Tuple[Tuple[str, bool, str], ...] = (
    ("source.validate", False, "validate requested existing app release"),
    ("app.validate", False, "validate rollback release compatibility"),
    ("state.capture", True, "preserve active operator settings"),
    ("app.activate", True, "atomically select the rollback app release"),
    ("host.restart", True, "restart the app service"),
    ("state.restore", True, "restore preserved operator settings"),
    ("health.readiness", False, "require fresh rollback-release readiness"),
    ("release.prune", True, "retain a bounded rollback-safe app release set"),
)


STEP_TIMING_EXPECTATIONS: Mapping[str, str] = {
    "source.validate": "normally <1s",
    "tests.run": "normally 1-2m",
    "target.connect": "normally <5s",
    "app.stage": "normally 20-40s",
    "receiver.firmware_build": "cached ~1s; cold cache can take ~13m",
    "host.provision": "normally <5s unless a reboot is required",
    "receiver.firmware_flash": "skipped ~2s; four receivers ~1.5m",
    "app.validate": "normally 3-8s",
    "state.capture": "normally 1-3s",
    "app.activate": "normally <10s",
    "host.restart": "normally 1-3s",
    "state.restore": "normally 5-10s",
    "health.readiness": "normally 3-30s",
    "release.prune": "normally <2s after one-time backlog cleanup",
}


def build_steps(mode: str, operations: Mapping[str, Operation]) -> List[Step]:
    """Build a stable procedural deployment from injected leaf operations."""
    if mode == "full":
        definitions = FULL_STEP_ORDER
    elif mode == "python":
        definitions = PYTHON_STEP_ORDER
    elif mode == "rollback":
        definitions = ROLLBACK_STEP_ORDER
    else:
        raise ValueError(f"unknown deployment mode: {mode}")
    missing = [step_id for step_id, _, _ in definitions if step_id not in operations]
    if missing:
        raise ValueError(f"missing deployment operations: {', '.join(missing)}")
    return [
        Step(step_id, mutating, operations[step_id], description)
        for step_id, mutating, description in definitions
    ]


def command_operation(
    args: Sequence[str],
    *,
    remote: bool = False,
    log_reference: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> Operation:
    """Adapt one existing leaf helper command to a coordinator operation."""
    immutable_args = tuple(args)

    def operation(context: DeployContext) -> OperationResult:
        kwargs: Dict[str, Any] = {}
        if log_reference is not None:
            kwargs["log_path"] = Path(log_reference)
        result = context.ssh(immutable_args, **kwargs) if remote else context.command(
            immutable_args, **kwargs,
        )
        command_details = dict(details or {})
        command_details.update(
            {
                "command": list(result.args),
                "returncode": result.returncode,
                "duration_seconds": result.duration_seconds,
            }
        )
        return OperationResult(details=command_details, log_reference=log_reference)

    return operation


def _signal_interruption(signum: int, _frame: Any) -> None:
    raise DeploymentInterrupted(f"deployment interrupted by signal {signum}")


class interruption_signals:
    """Translate CLI termination signals into receipt-producing interruption."""

    def __enter__(self) -> "interruption_signals":
        self._previous: Dict[int, Any] = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _signal_interruption)
        return self

    def __exit__(self, *_args: Any) -> None:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="print stable coordinator steps")
    plan_parser.add_argument("--mode", choices=("full", "python", "rollback"), required=True)
    args = parser.parse_args(argv)

    if args.command == "plan":
        definitions = {
            "full": FULL_STEP_ORDER,
            "python": PYTHON_STEP_ORDER,
            "rollback": ROLLBACK_STEP_ORDER,
        }[args.mode]
        payload = [
            {"id": step_id, "mutating": mutating, "description": description}
            for step_id, mutating, description in definitions
        ]
        print(json.dumps({"mode": args.mode, "steps": payload}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
