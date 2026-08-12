"""Desired/observed deployment and firmware reconciliation acceptance tests."""

from __future__ import annotations

import unittest
from dataclasses import replace

from tools.deployment.firmware_reconciliation import (
    DesiredHostState,
    DeviceFlashEvidence,
    FlashOutcome,
    HostAction,
    ObservedHostState,
    PlanReason,
    RebootPhase,
    RebootResumeError,
    RebootResumeState,
    ReceiverDesired,
    ReceiverObserved,
    TopologyError,
    app_activation_allowed,
    evaluate_flash_evidence,
    reconcile,
)

PHYSICAL_IDS = tuple(f"usb-receiver-{index}" for index in range(4))


def desired_receivers(
    firmware: str = "firmware-v2",
) -> tuple[ReceiverDesired, ...]:
    return tuple(
        ReceiverDesired(
            physical_id=physical_id,
            logical_id=index,
            firmware_identity=firmware,
            configuration_identity=f"config-{index}-v2",
        )
        for index, physical_id in enumerate(PHYSICAL_IDS)
    )


def observed_receivers(
    firmware: str = "firmware-v2",
) -> tuple[ReceiverObserved, ...]:
    return tuple(
        ReceiverObserved(
            physical_id=physical_id,
            logical_id=index,
            firmware_identity=firmware,
            configuration_identity=f"config-{index}-v2",
        )
        for index, physical_id in enumerate(PHYSICAL_IDS)
    )


def desired_state(**changes: object) -> DesiredHostState:
    values = {
        "package_identity": "packages-v2",
        "spi_identity": "spi-v2",
        "unit_identity": "units-v2",
        "dependency_identity": "deps-v2",
        "app_release_identity": "app-v2",
        "receivers": desired_receivers(),
    }
    values.update(changes)
    return DesiredHostState(**values)  # type: ignore[arg-type]


def observed_state(**changes: object) -> ObservedHostState:
    values = {
        "package_identity": "packages-v2",
        "spi_identity": "spi-v2",
        "unit_identity": "units-v2",
        "dependency_identity": "deps-v2",
        "app_release_identity": "app-v2",
        "receivers": observed_receivers(),
    }
    values.update(changes)
    return ObservedHostState(**values)  # type: ignore[arg-type]


class ReconciliationPlanTests(unittest.TestCase):
    def test_unchanged_plan_has_no_work_in_any_domain(self):
        plan = reconcile(desired_state(), observed_state())

        self.assertTrue(plan.unchanged)
        self.assertFalse(plan.requires_provisioning)
        self.assertFalse(plan.requires_reboot)
        self.assertFalse(plan.requires_dependency_work)
        self.assertEqual(plan.host_actions, ())
        self.assertEqual(plan.receiver_configuration_targets, ())
        self.assertEqual(plan.flash_targets, ())
        self.assertFalse(plan.activate_app_after_firmware)

    def test_changes_have_distinct_plan_causality(self):
        fields = (
            (
                "package_identity",
                PlanReason.PACKAGE_CHANGE,
                HostAction.PROVISION_PACKAGES,
            ),
            ("spi_identity", PlanReason.SPI_CHANGE, HostAction.PROVISION_SPI),
            ("unit_identity", PlanReason.UNIT_CHANGE, HostAction.PROVISION_UNITS),
            (
                "dependency_identity",
                PlanReason.DEPENDENCY_CHANGE,
                HostAction.INSTALL_DEPENDENCIES,
            ),
            ("app_release_identity", PlanReason.APP_CHANGE, HostAction.ACTIVATE_APP),
        )
        for field, reason, action in fields:
            with self.subTest(field=field):
                plan = reconcile(
                    desired_state(), observed_state(**{field: f"old-{field}"})
                )
                self.assertEqual(plan.reasons, (reason,))
                self.assertIn(action, plan.host_actions)
                self.assertEqual(plan.flash_targets, ())

    def test_only_immutable_host_provisioning_requires_reboot(self):
        package_plan = reconcile(
            desired_state(), observed_state(package_identity="packages-v1")
        )
        dependency_plan = reconcile(
            desired_state(), observed_state(dependency_identity="deps-v1")
        )
        app_plan = reconcile(
            desired_state(), observed_state(app_release_identity="app-v1")
        )

        self.assertTrue(package_plan.requires_reboot)
        self.assertFalse(dependency_plan.requires_reboot)
        self.assertFalse(app_plan.requires_reboot)

    def test_selectively_flashes_common_image_only_where_identity_differs(self):
        receivers = list(observed_receivers())
        receivers[1] = replace(receivers[1], firmware_identity="firmware-v1")
        receivers[3] = replace(receivers[3], firmware_identity="firmware-v1")

        plan = reconcile(desired_state(), observed_state(receivers=tuple(receivers)))

        self.assertEqual(
            [target.physical_id for target in plan.flash_targets],
            [PHYSICAL_IDS[1], PHYSICAL_IDS[3]],
        )
        self.assertEqual(
            {target.common_firmware_identity for target in plan.flash_targets},
            {"firmware-v2"},
        )
        self.assertEqual(plan.reasons, (PlanReason.FIRMWARE_CHANGE,))

    def test_logical_identity_is_provisioned_separately_from_common_image(self):
        receivers = list(observed_receivers())
        receivers[2] = replace(
            receivers[2],
            logical_id=1,
            configuration_identity="config-1-v2",
        )
        # Keep topology unique but wrong: swap two stable physical receivers.
        receivers[1] = replace(
            receivers[1],
            logical_id=2,
            configuration_identity="config-2-v2",
        )

        plan = reconcile(desired_state(), observed_state(receivers=tuple(receivers)))

        self.assertEqual(plan.flash_targets, ())
        self.assertEqual(
            [target.physical_id for target in plan.receiver_configuration_targets],
            [PHYSICAL_IDS[1], PHYSICAL_IDS[2]],
        )

    def test_firmware_and_logical_identity_produce_distinct_device_work(self):
        receivers = list(observed_receivers())
        receivers[0] = replace(
            receivers[0],
            firmware_identity="firmware-v1",
            configuration_identity="config-0-v1",
        )

        plan = reconcile(desired_state(), observed_state(receivers=tuple(receivers)))

        self.assertEqual(len(plan.flash_targets), 1)
        self.assertEqual(len(plan.receiver_configuration_targets), 1)
        self.assertEqual(
            plan.flash_targets[0].reasons,
            (PlanReason.FIRMWARE_CHANGE,),
        )


class TopologyValidationTests(unittest.TestCase):
    def test_logical_identity_must_be_a_non_negative_integer(self):
        for invalid in (True, -1, "0", 0.0):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(ValueError, "non-negative integer"),
            ):
                ReceiverDesired(
                    physical_id="usb-receiver-0",
                    logical_id=invalid,  # type: ignore[arg-type]
                    firmware_identity="firmware-v2",
                    configuration_identity="config-0-v2",
                )

    def test_fails_closed_on_missing_unexpected_duplicate_or_failed_receiver(self):
        cases = {}
        receivers = list(observed_receivers())
        cases["missing"] = receivers[:-1]
        unexpected = receivers[:-1] + [
            replace(receivers[-1], physical_id="unexpected-usb")
        ]
        cases["unexpected"] = unexpected
        duplicate_physical = list(receivers)
        duplicate_physical[-1] = replace(
            duplicate_physical[-1], physical_id=PHYSICAL_IDS[0]
        )
        cases["duplicate physical"] = duplicate_physical
        duplicate_logical = list(receivers)
        duplicate_logical[-1] = replace(duplicate_logical[-1], logical_id=0)
        cases["duplicate logical"] = duplicate_logical
        failed = list(receivers)
        failed[2] = replace(failed[2], ready=False)
        cases["failed"] = failed

        for expected, actual in cases.items():
            with (
                self.subTest(expected=expected),
                self.assertRaisesRegex(TopologyError, expected),
            ):
                reconcile(desired_state(), observed_state(receivers=tuple(actual)))

    def test_desired_topology_requires_contiguous_stable_logical_ids(self):
        desired = list(desired_receivers())
        desired[-1] = replace(desired[-1], logical_id=4)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            desired_state(receivers=tuple(desired))


class RebootResumeTests(unittest.TestCase):
    def test_automatic_reboot_resumes_once_after_rediscovery(self):
        plan = reconcile(desired_state(), observed_state(spi_identity="spi-v1"))

        state = RebootResumeState.for_plan(plan)
        self.assertEqual(state.phase, RebootPhase.REQUIRED)
        state = state.mark_requested().resume_after_rediscovery()

        self.assertEqual(state.phase, RebootPhase.RESUMED)
        self.assertEqual(state.resume_count, 1)
        state.assert_no_second_reboot_needed(
            reconcile(desired_state(), observed_state())
        )

    def test_reboot_does_not_loop_when_rediscovery_still_sees_difference(self):
        plan = reconcile(desired_state(), observed_state(unit_identity="units-v1"))
        state = (
            RebootResumeState.for_plan(plan).mark_requested().resume_after_rediscovery()
        )

        with self.assertRaisesRegex(RebootResumeError, "still requires reboot"):
            state.assert_no_second_reboot_needed(plan)
        with self.assertRaises(RebootResumeError):
            state.mark_requested()


def evidence_for(target, outcome: FlashOutcome, **changes: object):
    values = {
        "physical_id": target.physical_id,
        "logical_id": target.logical_id,
        "desired_firmware_identity": target.common_firmware_identity,
        "desired_configuration_identity": target.configuration_identity,
        "outcome": outcome,
    }
    if outcome is FlashOutcome.SUCCEEDED:
        values.update(
            observed_firmware_identity=target.common_firmware_identity,
            observed_configuration_identity=target.configuration_identity,
            log_reference=f"logs/{target.physical_id}.log",
        )
    elif outcome is FlashOutcome.FAILED:
        values.update(error="serial write failed", log_reference="logs/failure.log")
    values.update(changes)
    return DeviceFlashEvidence(**values)  # type: ignore[arg-type]


class PartialFlashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        receivers = list(observed_receivers("firmware-v1"))
        self.plan = reconcile(
            desired_state(app_release_identity="app-v3"),
            observed_state(app_release_identity="app-v2", receivers=tuple(receivers)),
        )

    def test_partial_failure_preserves_per_device_evidence_and_blocks_app(self):
        first, second, third, fourth = self.plan.flash_targets
        evidence = (
            evidence_for(first, FlashOutcome.SUCCEEDED),
            evidence_for(second, FlashOutcome.FAILED),
            evidence_for(third, FlashOutcome.SUCCEEDED),
            # fourth is an interrupted/pending target, omitted intentionally.
        )

        recovery = evaluate_flash_evidence(self.plan.flash_targets, evidence)

        self.assertTrue(recovery.required)
        self.assertTrue(recovery.service_must_remain_stopped)
        self.assertFalse(recovery.candidate_app_activation_allowed)
        self.assertEqual(recovery.failed_devices, (second.physical_id,))
        self.assertEqual(recovery.pending_devices, (fourth.physical_id,))
        self.assertEqual(
            recovery.succeeded_devices, (first.physical_id, third.physical_id)
        )
        self.assertFalse(app_activation_allowed(self.plan, recovery))
        self.assertEqual(len(recovery.evidence), 4)

    def test_app_activation_allowed_only_after_every_firmware_target_verifies(self):
        self.assertFalse(app_activation_allowed(self.plan, None))
        recovery = evaluate_flash_evidence(
            self.plan.flash_targets,
            tuple(
                evidence_for(target, FlashOutcome.SUCCEEDED)
                for target in self.plan.flash_targets
            ),
        )

        self.assertFalse(recovery.required)
        self.assertTrue(recovery.candidate_app_activation_allowed)
        self.assertTrue(app_activation_allowed(self.plan, recovery))

    def test_success_evidence_must_verify_both_image_and_configuration(self):
        target = self.plan.flash_targets[0]
        with self.assertRaisesRegex(ValueError, "verify desired identities"):
            evidence_for(
                target,
                FlashOutcome.SUCCEEDED,
                observed_configuration_identity="wrong-config",
            )

    def test_no_firmware_change_allows_app_only_activation_without_flash_evidence(self):
        app_plan = reconcile(
            desired_state(app_release_identity="app-v3"),
            observed_state(app_release_identity="app-v2"),
        )

        self.assertEqual(app_plan.flash_targets, ())
        self.assertTrue(app_activation_allowed(app_plan, None))

    def test_logical_identity_repair_requires_rediscovery_before_app_activation(self):
        receivers = list(observed_receivers())
        receivers[1] = replace(
            receivers[1], logical_id=2, configuration_identity="config-2-v2"
        )
        receivers[2] = replace(
            receivers[2], logical_id=1, configuration_identity="config-1-v2"
        )
        stale_plan = reconcile(
            desired_state(app_release_identity="app-v3"),
            observed_state(app_release_identity="app-v2", receivers=tuple(receivers)),
        )

        self.assertEqual(stale_plan.flash_targets, ())
        self.assertEqual(len(stale_plan.receiver_configuration_targets), 2)
        self.assertFalse(app_activation_allowed(stale_plan, None))

        rediscovered_plan = reconcile(
            desired_state(app_release_identity="app-v3"),
            observed_state(app_release_identity="app-v2"),
        )
        self.assertEqual(rediscovered_plan.receiver_configuration_targets, ())
        self.assertTrue(app_activation_allowed(rediscovered_plan, None))

    def test_duplicate_or_wrong_target_evidence_is_rejected(self):
        target = self.plan.flash_targets[0]
        item = evidence_for(target, FlashOutcome.SUCCEEDED)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate_flash_evidence(self.plan.flash_targets, (item, item))
        with self.assertRaisesRegex(ValueError, "does not match target"):
            evaluate_flash_evidence(
                self.plan.flash_targets,
                (replace(item, logical_id=3),),
            )

    def test_app_only_plan_does_not_fabricate_empty_flash_success(self):
        with self.assertRaisesRegex(ValueError, "at least one planned target"):
            evaluate_flash_evidence((), ())


if __name__ == "__main__":
    unittest.main()
