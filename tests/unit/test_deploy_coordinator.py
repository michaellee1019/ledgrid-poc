"""Failure-injection and safety coverage for the thin deploy coordinator."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.deployment.deploy_coordinator import (
    Artifact,
    AtomicJSONReceiptStore,
    CommandResult,
    DeployContext,
    DeployCoordinator,
    DeploymentInterrupted,
    FreshHealthChecker,
    FULL_STEP_ORDER,
    HealthCheckFailed,
    HealthExpectation,
    HealthSample,
    OperationResult,
    PYTHON_STEP_ORDER,
    REDACTED,
    Redactor,
    SSHRunner,
    Step,
    SubprocessRunner,
    build_steps,
)


class _Clock:
    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append((tuple(args), kwargs))
        return CommandResult(tuple(args), 0, "", "", 0.01)


class _FailingSink:
    def persist(self, receipt, redactor):
        raise OSError("disk unavailable")


class DeployCoordinatorTests(unittest.TestCase):
    def test_progress_reports_step_expectations_outcomes_and_total(self):
        messages = []
        context = DeployContext(
            target="wall",
            mode="test",
            source_identity={},
            progress=messages.append,
        )
        receipt = DeployCoordinator().run(
            context,
            [
                Step(
                    "tests.run",
                    False,
                    lambda _context: OperationResult(outcome="skipped"),
                ),
            ],
        )

        self.assertEqual(receipt.outcome, "success")
        self.assertIn("[deploy 01/01] START tests.run; normally 1-2m", messages[0])
        self.assertIn("DONE tests.run (skipped", messages[1])
        self.assertRegex(messages[-1], r"^\[deploy\] SUCCESS in ")

    def test_each_failure_is_fail_fast_and_persists_atomic_receipt(self):
        for failing_index in range(4):
            with self.subTest(failing_index=failing_index), tempfile.TemporaryDirectory() as temp:
                calls = []

                def operation(index):
                    def run(_context):
                        calls.append(index)
                        if index == failing_index:
                            raise RuntimeError(f"boom {index}")
                        return OperationResult()

                    return run

                steps = [Step(f"test.step_{index}", bool(index), operation(index)) for index in range(4)]
                context = DeployContext(
                    target="wall",
                    mode="test",
                    source_identity={"revision": "abc"},
                    attempt_id=f"attempt{failing_index}",
                    receipt_sinks=(AtomicJSONReceiptStore(Path(temp)),),
                )
                receipt = DeployCoordinator().run(context, steps)

                self.assertEqual(calls, list(range(failing_index + 1)))
                self.assertEqual(receipt.outcome, "failure")
                self.assertEqual(receipt.steps[-1].outcome, "failed")
                receipt_path = Path(temp) / f"attempt{failing_index}.json"
                payload = json.loads(receipt_path.read_text())
                self.assertEqual(payload["outcome"], "failure")
                self.assertEqual(len(payload["steps"]), failing_index + 1)
                self.assertFalse(list(Path(temp).glob("*.tmp")))

    def test_success_and_interruption_receipts_are_durable(self):
        for exception, expected in ((None, "success"), (DeploymentInterrupted("stop"), "interrupted")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                def operation(_context):
                    if exception is not None:
                        raise exception
                    return OperationResult(details={"ok": True})

                context = DeployContext(
                    target="wall",
                    mode="python",
                    source_identity={"revision": "abc"},
                    attempt_id=expected,
                    receipt_sinks=(AtomicJSONReceiptStore(Path(temp)),),
                )
                receipt = DeployCoordinator().run(
                    context, [Step("test.execute", True, operation)],
                )
                self.assertEqual(receipt.outcome, expected)
                payload = json.loads((Path(temp) / f"{expected}.json").read_text())
                self.assertEqual(payload["outcome"], expected)

    def test_receipt_store_is_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            context = DeployContext(
                target="wall",
                mode="test",
                source_identity={},
                attempt_id="same",
                receipt_sinks=(AtomicJSONReceiptStore(Path(temp)),),
            )
            coordinator = DeployCoordinator()
            first = coordinator.run(context, [Step("test.one", False, lambda _: None)])
            second = coordinator.run(context, [Step("test.one", False, lambda _: None)])
            self.assertEqual(first.outcome, "success")
            self.assertEqual(second.outcome, "success")
            self.assertTrue(second.persistence_errors)
            self.assertEqual(json.loads((Path(temp) / "same.json").read_text())["outcome"], "success")

    def test_partial_receipt_sink_failure_does_not_rewrite_deployment_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            messages = []
            context = DeployContext(
                target="wall",
                mode="test",
                source_identity={},
                attempt_id="partial",
                receipt_sinks=(AtomicJSONReceiptStore(Path(temp)), _FailingSink()),
                progress=messages.append,
            )
            receipt = DeployCoordinator().run(
                context, [Step("test.one", False, lambda _: None)],
            )
            self.assertEqual(receipt.outcome, "success")
            self.assertEqual(len(receipt.persistence_errors), 1)
            self.assertEqual(json.loads((Path(temp) / "partial.json").read_text())["outcome"], "success")
            self.assertRegex(
                messages[-1],
                r"^\[deploy\] FAILURE in .*; receipt persistence failed$",
            )

    def test_redaction_covers_commands_logs_receipts_artifacts_and_secret_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            secret = "ultra-secret-value"
            key_path = directory / "private" / "id_ed25519"
            log_path = directory / "command.log"
            redactor = Redactor(
                secret_names=("DEPLOY_TOKEN",),
                secret_paths=(key_path,),
                secret_values=(secret,),
            )
            runner = SubprocessRunner(redactor)
            result = runner.run(
                (
                    "python3",
                    "-c",
                    "import sys; print(sys.argv[1]); print(sys.argv[2], file=sys.stderr)",
                    secret,
                    f"DEPLOY_TOKEN={secret}",
                    "--identity-file",
                    str(key_path),
                ),
                log_path=log_path,
            )
            self.assertNotIn(secret, " ".join(result.args) + result.stdout + result.stderr)
            self.assertNotIn(str(key_path), " ".join(result.args))

            context = DeployContext(
                target=f"wall-{secret}",
                mode="test",
                source_identity={"DEPLOY_TOKEN": secret, "key": str(key_path)},
                redactor=redactor,
                attempt_id="redacted",
                receipt_sinks=(AtomicJSONReceiptStore(directory / "receipts"),),
            )
            receipt = DeployCoordinator().run(
                context,
                [
                    Step(
                        "test.redact",
                        False,
                        lambda _: OperationResult(
                            artifacts=(Artifact("app", str(key_path), secret, "1", secret),),
                            details={"command": ["--token", secret], "DEPLOY_TOKEN": secret},
                            log_reference=str(key_path),
                        ),
                    ),
                ],
            )
            raw = (directory / "receipts" / "redacted.json").read_text()
            self.assertEqual(receipt.outcome, "success")
            self.assertNotIn(secret, raw)
            self.assertNotIn(str(key_path), raw)
            self.assertIn(REDACTED, raw)
            self.assertNotIn(secret, log_path.read_text())

    def test_ssh_runner_uses_injected_argument_array(self):
        fake = _FakeRunner()
        ssh = SSHRunner(fake, "user@wall", ssh_options=("-o", "BatchMode=yes"))
        ssh.run(("bash", "helper.sh", "argument with spaces"))
        args, _kwargs = fake.calls[0]
        self.assertEqual(args[:5], ("ssh", "-o", "BatchMode=yes", "user@wall", "--"))
        self.assertEqual(args[5], "bash helper.sh 'argument with spaces'")

    def test_mode_order_is_stable_and_firmware_build_precedes_first_downtime(self):
        full_ids = [item[0] for item in FULL_STEP_ORDER]
        self.assertEqual(
            full_ids[:8],
            [
                "source.validate",
                "tests.run",
                "target.connect",
                "app.stage",
                "app.bootstrap_legacy",
                "receiver.firmware_build",
                "state.capture",
                "host.provision",
            ],
        )
        self.assertGreater(
            full_ids.index("receiver.topology_migrate"),
            full_ids.index("health.readiness"),
        )
        self.assertLess(full_ids.index("receiver.firmware_build"), full_ids.index("host.provision"))
        self.assertLess(full_ids.index("receiver.firmware_build"), full_ids.index("receiver.firmware_flash"))
        self.assertLess(full_ids.index("state.capture"), full_ids.index("host.provision"))
        self.assertLess(full_ids.index("state.capture"), full_ids.index("receiver.firmware_flash"))
        python_ids = [item[0] for item in PYTHON_STEP_ORDER]
        self.assertNotIn("host.provision", python_ids)
        self.assertNotIn("receiver.firmware_flash", python_ids)
        operations = {step_id: (lambda _: None) for step_id in python_ids}
        self.assertEqual([step.id for step in build_steps("python", operations)], python_ids)


class FreshHealthTests(unittest.TestCase):
    def expectation(self, **overrides):
        values = dict(
            desired_release="a" * 64,
            restart_started_at=99.0,
            strip_count=32,
            leds_per_strip=138,
            receiver_count=4,
            stable_samples=2,
        )
        values.update(overrides)
        return HealthExpectation(**values)

    def sample(self, updated_at, **overrides):
        values = dict(
            sampled_at=updated_at + 0.1,
            systemd_active=True,
            controller_updated_at=updated_at,
            release_id="a" * 64,
            strip_count=32,
            leds_per_strip=138,
            receiver_count=4,
            ready=True,
        )
        values.update(overrides)
        return HealthSample(**values)

    def run_samples(self, samples, expectation=None):
        clock = _Clock(100.0)
        iterator = iter(samples)
        last = samples[-1]

        def reader():
            nonlocal last
            try:
                last = next(iterator)
            except StopIteration:
                pass
            return last

        checker = FreshHealthChecker(reader, sleep=clock.sleep, clock=clock)
        return checker.wait(
            expectation or self.expectation(),
            timeout_seconds=0.3,
            poll_interval_seconds=0.1,
        )

    def test_requires_advancing_stable_post_restart_samples(self):
        result = self.run_samples([self.sample(100.0), self.sample(100.2)])
        self.assertEqual(result["stable_samples"], 2)

    def test_identical_stuck_snapshot_never_passes(self):
        with self.assertRaisesRegex(HealthCheckFailed, "did not advance"):
            self.run_samples([self.sample(100.0)])

    def test_rejects_stale_wrong_release_geometry_topology_and_future_time(self):
        cases = (
            (self.sample(98.0), "predates restart"),
            (self.sample(100.0, release_id="b" * 64), "observed release"),
            (self.sample(100.0, strip_count=31), "geometry"),
            (self.sample(100.0, receiver_count=3), "topology"),
            (self.sample(110.0, sampled_at=100.0), "future"),
            (self.sample(100.0, sampled_at=105.0), "stale"),
            (self.sample(100.0, systemd_active=False), "systemd"),
        )
        for sample, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(HealthCheckFailed, message):
                self.run_samples([sample])


if __name__ == "__main__":
    unittest.main()
