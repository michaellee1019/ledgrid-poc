"""Behavioral acceptance for the deployed Raspberry Pi SPI reconciler."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/deployment/configure_spi.sh"


def _bash(body: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", body, "configure-spi-test", str(SCRIPT), *args),
        check=False,
        text=True,
        capture_output=True,
    )


SOURCE_FUNCTIONS = r'''
sudo() {
  if [ "$1" = sed ] && [ "$2" = -i ]; then
    shift 2
    if sed --version >/dev/null 2>&1; then
      command sed -i "$@"
    else
      command sed -i '' "$@"
    fi
  else
    # Bash 3.2 + set -e aborts a function whose last command fails, even when
    # the caller invoked it from `if`. Preserve grep -q "no match" as return 1.
    command "$@" || return $?
  fi
}
# Command substitution waits for awk to finish. On macOS Bash 3.2, sourcing a
# process substitution can race its producer when the repository has a long
# worktree path, leaving the test shell with none of the functions below.
eval "$(awk '/^find_config_files$/{exit} {print}' "$1")"
# The sourced script enables set -e. Bash 3.2 still exits the test shell when a
# function called from `if` runs a failing grep; keep the driver non-fatal.
set +e
'''


class ConfigureSpiBehaviorTests(unittest.TestCase):
    def test_wall_profile_requires_spi02_and_spi0_extra_cs_overlay(self) -> None:
        completed = _bash(
            "export LEDGRID_HAT=0\n"
            + SOURCE_FUNCTIONS
            + r'''
printf '%s\n' "${REQUIRED_DEVICES[@]}"
printf 'LINES\n'
desired_spi_lines
'''
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        devices, lines = completed.stdout.split("LINES\n", 1)
        self.assertEqual(
            devices.splitlines(),
            [
                "/dev/spidev0.0",
                "/dev/spidev0.1",
                "/dev/spidev0.3",
                "/dev/spidev1.0",
                "/dev/spidev1.1",
            ],
        )
        self.assertEqual(
            lines.splitlines(),
            [
                "dtparam=spi=on",
                "dtoverlay=spi0-4cs",
                "dtoverlay=spi1-2cs,cs0_pin=22",
            ],
        )
        overlay = ROOT / "tools/deployment/overlays/spi0-4cs-overlay.dts"
        text = overlay.read_text(encoding="utf-8")
        self.assertIn("<&gpio 8 1>", text)
        self.assertIn("<&gpio 7 1>", text)
        self.assertIn("<&gpio 5 1>", text)
        self.assertIn("<&gpio 6 1>", text)

    def test_hat_profile_requires_spi1_ce0_ce1(self) -> None:
        completed = _bash(
            "export LEDGRID_HAT=1\n"
            + SOURCE_FUNCTIONS
            + r'''
printf '%s\n' "${REQUIRED_DEVICES[@]}"
printf 'LINES\n'
desired_spi_lines
'''
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        devices, lines = completed.stdout.split("LINES\n", 1)
        self.assertEqual(devices.splitlines(), ["/dev/spidev1.0", "/dev/spidev1.1"])
        self.assertEqual(
            lines.splitlines(),
            ["dtparam=spi=on", "dtoverlay=spi1-2cs,cs0_pin=22"],
        )

    def test_conflicting_overlay_is_rejected_and_atomically_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = Path(temporary_dir) / "config.txt"
            config.write_text(
                "# preserve me\n"
                "dtparam=spi=off\n"
                "dtoverlay=spi1-2cs\n"
                "dtoverlay=spi1-3cs,cs0_pin=22,cs2_pin=24\n",
                encoding="utf-8",
            )
            completed = _bash(
                "export LEDGRID_HAT=0\n"
                + SOURCE_FUNCTIONS
                + r'''
if config_is_correct "$2"; then
  echo 'unexpectedly-correct' >&2
  exit 9
fi
apply_boot_config "$2"
config_is_correct "$2"
''',
                str(config),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                config.read_text(encoding="utf-8").splitlines(),
                [
                    "# preserve me",
                    "dtparam=spi=on",
                    "dtoverlay=spi0-4cs",
                    "dtoverlay=spi1-2cs,cs0_pin=22",
                ],
            )
            self.assertTrue(config.with_suffix(".txt.ledgrid.bak").is_file())

    def test_duplicate_or_suffix_modified_overlay_never_counts_as_correct(self) -> None:
        cases = (
            "dtparam=spi=on\n"
            "dtoverlay=spi0-4cs\n"
            "dtoverlay=spi1-2cs,cs0_pin=22\n"
            "dtoverlay=spi1-3cs,cs2_pin=24\n",
            "dtparam=spi=on\n"
            "dtoverlay=spi0-4cs,unexpected=1\n"
            "dtoverlay=spi1-2cs,cs0_pin=22\n",
            "dtparam=spi=on\n"
            "dtoverlay=spi1-2cs,cs0_pin=22\n",
        )
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary_dir:
                config = Path(temporary_dir) / "config.txt"
                config.write_text(payload, encoding="utf-8")
                completed = _bash(
                    "export LEDGRID_HAT=0\n"
                    + SOURCE_FUNCTIONS
                    + r'''
if config_is_correct "$2"; then exit 7; fi
''',
                    str(config),
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
