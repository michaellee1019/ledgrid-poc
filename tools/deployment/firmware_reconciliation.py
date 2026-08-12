"""Pure desired/observed reconciliation for host and receiver deployment.

The coordinator owns execution and receipts; this module owns planning and the
small persistent state machines needed around reboot and partial flashing.  No
function here opens a device, invokes SSH, flashes firmware, or activates an app.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Sequence, Tuple


class PlanReason(str, Enum):
    PACKAGE_CHANGE = "package_change"
    SPI_CHANGE = "spi_change"
    UNIT_CHANGE = "unit_change"
    DEPENDENCY_CHANGE = "dependency_change"
    APP_CHANGE = "app_change"
    FIRMWARE_CHANGE = "firmware_change"
    LOGICAL_IDENTITY_CHANGE = "logical_identity_change"


class HostAction(str, Enum):
    PROVISION_PACKAGES = "provision_packages"
    PROVISION_SPI = "provision_spi"
    PROVISION_UNITS = "provision_units"
    INSTALL_DEPENDENCIES = "install_dependencies"
    REBOOT = "reboot"
    ACTIVATE_APP = "activate_app"


@dataclass(frozen=True)
class ReceiverDesired:
    """Stable physical port binding plus desired provisioned logical identity."""

    physical_id: str
    logical_id: int
    firmware_identity: str
    configuration_identity: str

    def __post_init__(self) -> None:
        _validate_receiver_fields(
            self.physical_id,
            self.logical_id,
            self.firmware_identity,
            self.configuration_identity,
        )


@dataclass(frozen=True)
class ReceiverObserved:
    physical_id: str
    logical_id: int
    firmware_identity: str
    configuration_identity: str
    ready: bool = True

    def __post_init__(self) -> None:
        _validate_receiver_fields(
            self.physical_id,
            self.logical_id,
            self.firmware_identity,
            self.configuration_identity,
        )


def _validate_receiver_fields(
    physical_id: str,
    logical_id: int,
    firmware_identity: str,
    configuration_identity: str,
) -> None:
    if not physical_id or not firmware_identity or not configuration_identity:
        raise ValueError("receiver identities must not be empty")
    if (
        not isinstance(logical_id, int)
        or isinstance(logical_id, bool)
        or logical_id < 0
    ):
        raise ValueError("logical_id must be a non-negative integer")


@dataclass(frozen=True)
class DesiredHostState:
    package_identity: str
    spi_identity: str
    unit_identity: str
    dependency_identity: str
    app_release_identity: str
    receivers: Tuple[ReceiverDesired, ...]

    def __post_init__(self) -> None:
        _validate_host_identities(
            self.package_identity,
            self.spi_identity,
            self.unit_identity,
            self.dependency_identity,
            self.app_release_identity,
        )
        validate_desired_topology(self.receivers)


@dataclass(frozen=True)
class ObservedHostState:
    package_identity: str
    spi_identity: str
    unit_identity: str
    dependency_identity: str
    app_release_identity: str
    receivers: Tuple[ReceiverObserved, ...]

    def __post_init__(self) -> None:
        _validate_host_identities(
            self.package_identity,
            self.spi_identity,
            self.unit_identity,
            self.dependency_identity,
            self.app_release_identity,
        )


def _validate_host_identities(*identities: str) -> None:
    if not all(identity for identity in identities):
        raise ValueError("host state identities must not be empty")


def validate_desired_topology(receivers: Sequence[ReceiverDesired]) -> None:
    if not receivers:
        raise ValueError("desired receiver topology must not be empty")
    physical_ids = [receiver.physical_id for receiver in receivers]
    logical_ids = [receiver.logical_id for receiver in receivers]
    if len(physical_ids) != len(set(physical_ids)):
        raise ValueError("desired receiver topology has duplicate physical IDs")
    if len(logical_ids) != len(set(logical_ids)):
        raise ValueError("desired receiver topology has duplicate logical IDs")
    expected = list(range(len(receivers)))
    if sorted(logical_ids) != expected:
        actual = sorted(logical_ids)
        raise ValueError(
            f"desired logical IDs must be contiguous {expected}; got {actual}"
        )


def index_observed_topology(
    desired: Sequence[ReceiverDesired],
    observed: Sequence[ReceiverObserved],
) -> Dict[str, ReceiverObserved]:
    """Return physical-ID mapping or fail closed on any topology anomaly."""

    validate_desired_topology(desired)
    expected_physical = {receiver.physical_id for receiver in desired}
    expected_logical = {receiver.logical_id for receiver in desired}
    observed_physical = [receiver.physical_id for receiver in observed]
    observed_logical = [receiver.logical_id for receiver in observed]
    errors = []
    duplicate_physical = sorted(
        physical_id
        for physical_id in set(observed_physical)
        if observed_physical.count(physical_id) > 1
    )
    duplicate_logical = sorted(
        logical_id
        for logical_id in set(observed_logical)
        if observed_logical.count(logical_id) > 1
    )
    missing = sorted(expected_physical - set(observed_physical))
    unexpected = sorted(set(observed_physical) - expected_physical)
    unexpected_logical = sorted(set(observed_logical) - expected_logical)
    failed = sorted(receiver.physical_id for receiver in observed if not receiver.ready)
    if duplicate_physical:
        errors.append(f"duplicate physical receivers: {duplicate_physical}")
    if duplicate_logical:
        errors.append(f"duplicate logical receivers: {duplicate_logical}")
    if missing:
        errors.append(f"missing receivers: {missing}")
    if unexpected:
        errors.append(f"unexpected receivers: {unexpected}")
    if unexpected_logical:
        errors.append(f"unexpected logical IDs: {unexpected_logical}")
    if failed:
        errors.append(f"failed receivers: {failed}")
    if errors:
        raise TopologyError("; ".join(errors))
    return {receiver.physical_id: receiver for receiver in observed}


class TopologyError(ValueError):
    """Observed hardware is not the exact safe-to-reconcile topology."""


@dataclass(frozen=True)
class FlashTarget:
    physical_id: str
    logical_id: int
    common_firmware_identity: str
    configuration_identity: str
    reasons: Tuple[PlanReason, ...]


@dataclass(frozen=True)
class ReceiverConfigurationTarget:
    physical_id: str
    logical_id: int
    configuration_identity: str


@dataclass(frozen=True)
class ReconciliationPlan:
    reasons: Tuple[PlanReason, ...]
    host_actions: Tuple[HostAction, ...]
    receiver_configuration_targets: Tuple[ReceiverConfigurationTarget, ...]
    flash_targets: Tuple[FlashTarget, ...]
    activate_app_after_firmware: bool

    @property
    def unchanged(self) -> bool:
        return (
            not self.reasons
            and not self.host_actions
            and not self.receiver_configuration_targets
            and not self.flash_targets
        )

    @property
    def requires_provisioning(self) -> bool:
        provisioning = {
            HostAction.PROVISION_PACKAGES,
            HostAction.PROVISION_SPI,
            HostAction.PROVISION_UNITS,
        }
        return any(action in provisioning for action in self.host_actions)

    @property
    def requires_reboot(self) -> bool:
        return HostAction.REBOOT in self.host_actions

    @property
    def requires_dependency_work(self) -> bool:
        return HostAction.INSTALL_DEPENDENCIES in self.host_actions


def reconcile(
    desired: DesiredHostState, observed: ObservedHostState
) -> ReconciliationPlan:
    """Produce a deterministic plan while validating the entire topology first."""

    observed_by_physical = index_observed_topology(
        desired.receivers, observed.receivers
    )
    reasons = []
    actions = []

    def changed(
        desired_identity: str,
        observed_identity: str,
        reason: PlanReason,
        action: HostAction,
    ) -> None:
        if desired_identity != observed_identity:
            reasons.append(reason)
            actions.append(action)

    changed(
        desired.package_identity,
        observed.package_identity,
        PlanReason.PACKAGE_CHANGE,
        HostAction.PROVISION_PACKAGES,
    )
    changed(
        desired.spi_identity,
        observed.spi_identity,
        PlanReason.SPI_CHANGE,
        HostAction.PROVISION_SPI,
    )
    changed(
        desired.unit_identity,
        observed.unit_identity,
        PlanReason.UNIT_CHANGE,
        HostAction.PROVISION_UNITS,
    )
    changed(
        desired.dependency_identity,
        observed.dependency_identity,
        PlanReason.DEPENDENCY_CHANGE,
        HostAction.INSTALL_DEPENDENCIES,
    )
    app_changed = desired.app_release_identity != observed.app_release_identity
    if app_changed:
        reasons.append(PlanReason.APP_CHANGE)

    receiver_configuration_targets = []
    flash_targets = []
    for receiver in sorted(desired.receivers, key=lambda item: item.logical_id):
        actual = observed_by_physical[receiver.physical_id]
        configuration_changed = (
            receiver.logical_id != actual.logical_id
            or receiver.configuration_identity != actual.configuration_identity
        )
        if configuration_changed:
            receiver_configuration_targets.append(
                ReceiverConfigurationTarget(
                    physical_id=receiver.physical_id,
                    logical_id=receiver.logical_id,
                    configuration_identity=receiver.configuration_identity,
                )
            )
            reasons.append(PlanReason.LOGICAL_IDENTITY_CHANGE)
        if receiver.firmware_identity != actual.firmware_identity:
            flash_targets.append(
                FlashTarget(
                    physical_id=receiver.physical_id,
                    logical_id=receiver.logical_id,
                    common_firmware_identity=receiver.firmware_identity,
                    configuration_identity=receiver.configuration_identity,
                    reasons=(PlanReason.FIRMWARE_CHANGE,),
                )
            )
            reasons.append(PlanReason.FIRMWARE_CHANGE)

    # Package/SPI/unit changes are immutable host provisioning differences and
    # require exactly one bounded reboot. Dependency and app changes do not.
    if any(
        action
        in {
            HostAction.PROVISION_PACKAGES,
            HostAction.PROVISION_SPI,
            HostAction.PROVISION_UNITS,
        }
        for action in actions
    ):
        actions.append(HostAction.REBOOT)

    if app_changed:
        actions.append(HostAction.ACTIVATE_APP)

    # Preserve first occurrence for concise, stable receipt causality.
    unique_reasons = tuple(dict.fromkeys(reasons))
    return ReconciliationPlan(
        reasons=unique_reasons,
        host_actions=tuple(actions),
        receiver_configuration_targets=tuple(receiver_configuration_targets),
        flash_targets=tuple(flash_targets),
        activate_app_after_firmware=app_changed,
    )


class RebootPhase(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    REQUESTED = "requested"
    RESUMED = "resumed"


@dataclass(frozen=True)
class RebootResumeState:
    phase: RebootPhase
    resume_count: int = 0
    max_resume_count: int = 1

    def __post_init__(self) -> None:
        if self.resume_count < 0 or self.max_resume_count < 0:
            raise ValueError("reboot resume counts must be non-negative")
        if self.resume_count > self.max_resume_count:
            raise ValueError("reboot resume state exceeds its strict bound")

    @classmethod
    def for_plan(cls, plan: ReconciliationPlan) -> "RebootResumeState":
        return cls(
            RebootPhase.REQUIRED if plan.requires_reboot else RebootPhase.NOT_REQUIRED
        )

    def mark_requested(self) -> "RebootResumeState":
        if self.phase is not RebootPhase.REQUIRED:
            raise RebootResumeError(
                f"cannot request reboot from phase {self.phase.value}"
            )
        return RebootResumeState(
            RebootPhase.REQUESTED, self.resume_count, self.max_resume_count
        )

    def resume_after_rediscovery(self) -> "RebootResumeState":
        if self.phase is not RebootPhase.REQUESTED:
            raise RebootResumeError(
                f"cannot resume reboot from phase {self.phase.value}"
            )
        next_count = self.resume_count + 1
        if next_count > self.max_resume_count:
            raise RebootResumeError("automatic reboot resume bound exhausted")
        return RebootResumeState(RebootPhase.RESUMED, next_count, self.max_resume_count)

    def assert_no_second_reboot_needed(self, plan: ReconciliationPlan) -> None:
        if self.phase is RebootPhase.RESUMED and plan.requires_reboot:
            raise RebootResumeError(
                "provisioning still requires reboot after bounded rediscovery"
            )


class RebootResumeError(RuntimeError):
    pass


class FlashOutcome(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class DeviceFlashEvidence:
    physical_id: str
    logical_id: int
    desired_firmware_identity: str
    desired_configuration_identity: str
    outcome: FlashOutcome
    observed_firmware_identity: Optional[str] = None
    observed_configuration_identity: Optional[str] = None
    log_reference: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.physical_id or not self.desired_firmware_identity:
            raise ValueError("device flash evidence identities must not be empty")
        if self.outcome is FlashOutcome.SUCCEEDED:
            if (
                self.observed_firmware_identity != self.desired_firmware_identity
                or self.observed_configuration_identity
                != self.desired_configuration_identity
            ):
                raise ValueError(
                    "successful flash evidence must verify desired identities"
                )
            if self.error:
                raise ValueError("successful flash evidence cannot contain an error")
        elif self.outcome is FlashOutcome.FAILED and not self.error:
            raise ValueError("failed flash evidence requires an error")


@dataclass(frozen=True)
class FirmwareRecovery:
    required: bool
    service_must_remain_stopped: bool
    candidate_app_activation_allowed: bool
    succeeded_devices: Tuple[str, ...]
    failed_devices: Tuple[str, ...]
    pending_devices: Tuple[str, ...]
    evidence: Tuple[DeviceFlashEvidence, ...]


def evaluate_flash_evidence(
    targets: Sequence[FlashTarget],
    evidence: Sequence[DeviceFlashEvidence],
) -> FirmwareRecovery:
    """Validate per-device evidence and make partial-flash recovery explicit."""

    if not targets:
        raise ValueError("flash evidence requires at least one planned target")
    expected = {target.physical_id: target for target in targets}
    actual = {item.physical_id: item for item in evidence}
    if len(actual) != len(evidence):
        raise ValueError("duplicate per-device flash evidence")
    unexpected = sorted(set(actual) - set(expected))
    if unexpected:
        raise ValueError(f"unexpected flash evidence: {unexpected}")
    normalized = []
    for target in targets:
        item = actual.get(target.physical_id)
        if item is None:
            item = DeviceFlashEvidence(
                physical_id=target.physical_id,
                logical_id=target.logical_id,
                desired_firmware_identity=target.common_firmware_identity,
                desired_configuration_identity=target.configuration_identity,
                outcome=FlashOutcome.PENDING,
            )
        if (
            item.logical_id != target.logical_id
            or item.desired_firmware_identity != target.common_firmware_identity
            or item.desired_configuration_identity != target.configuration_identity
        ):
            raise ValueError(
                f"flash evidence does not match target {target.physical_id}"
            )
        normalized.append(item)

    succeeded = tuple(
        item.physical_id
        for item in normalized
        if item.outcome is FlashOutcome.SUCCEEDED
    )
    failed = tuple(
        item.physical_id for item in normalized if item.outcome is FlashOutcome.FAILED
    )
    pending = tuple(
        item.physical_id for item in normalized if item.outcome is FlashOutcome.PENDING
    )
    recovery_required = bool(failed or pending)
    return FirmwareRecovery(
        required=recovery_required,
        service_must_remain_stopped=recovery_required,
        candidate_app_activation_allowed=not recovery_required,
        succeeded_devices=succeeded,
        failed_devices=failed,
        pending_devices=pending,
        evidence=tuple(normalized),
    )


def app_activation_allowed(
    plan: ReconciliationPlan, recovery: Optional[FirmwareRecovery]
) -> bool:
    """Gate app activation until firmware and provisioned identity reconcile.

    Receiver configuration work is verified by rediscovery and a fresh
    ``reconcile`` call.  A stale pre-configuration plan can therefore never be
    used as evidence that stable logical identity is ready.
    """

    if not plan.activate_app_after_firmware:
        return False
    if plan.receiver_configuration_targets:
        return False
    if not plan.flash_targets:
        return True
    return recovery is not None and recovery.candidate_app_activation_allowed
