# REL-01 supplementary evidence boundary — 2026-08-27

These are historical observations, not a current multi-engine command or a
release recipe. A simulator, desktop WebKit, or macOS accessibility process is
not physical iPhone Safari, installed standalone mode, or iPhone VoiceOver
evidence.

## Physical iPhone status

The Mac reported `RyPhone 16 Pro Max` on iOS 26.0.1, but both Xcode and
`devicectl` reported the device unavailable. Its Continuity Camera presence did
not expose the device to Xcode, Safari Web Inspector, or the browser
qualification tooling.

The operator explicitly waived the physical iPhone Safari,
installed-standalone, and VoiceOver gate for this run. Each is recorded as
`OPERATOR_WAIVED`, not `PASS`, and receives no qualification credit.

## Historical simulator observations

An iPhone 17 Pro Max Simulator running iOS 26.4.1
(`CC7E466D-DC5A-45AD-B1C6-2B7DB12765BB`) loaded the real Composer in Safari.
The same origin was added to the Home Screen with **Open as Web App** enabled
and launched as the installed `Wall Composer` app. The retained standalone
capture has no Safari address or navigation chrome; the Safari capture retains
that chrome. Both show the same reachable 51-renderer catalog and six-item
mobile navigation without horizontal clipping.

- `run_state/browser_qualification/evidence/rel01-ios-simulator-safari.png`
  — SHA-256 `a6b4d09c11a280daf92d3285aeb42f94abe039e0c16228849cd04736bcc89936`
- `run_state/browser_qualification/evidence/rel01-ios-simulator-installed-standalone.png`
  — SHA-256 `7a3a2bbd30ae501d6a250df8a6723840484db6794f46fc08f632a906282cf190`

The iOS Simulator Accessibility settings did not expose VoiceOver. Starting
macOS VoiceOver did not make the simulator's web content appear in the Mac
accessibility tree, so that attempt is recorded as `NOT_EQUIVALENT`, receives no
qualification credit, and is not represented as an iPhone VoiceOver journey.

## Historical keyboard observations

The retained keyboard observations do not replace a physical-device run. Firefox
and mobile-WebKit are reserved for an engine-specific regression investigation;
they are not current documentation validation.

- `run_state/browser_qualification/evidence/rel01-keyboard-chromium.json`
  — SHA-256 `cded18cc16a2cacd7032e2bb1c5dbf31ade2555b10b337298a2f17c755faa747`
- `run_state/browser_qualification/evidence/rel01-keyboard-firefox.json`
  — SHA-256 `898d67e798a5bddf067f88124d47af4fb4b7187324f75b27774a53653710a3de`
- `run_state/browser_qualification/evidence/rel01-keyboard-webkit.json`
  — SHA-256 `7b1503e427b590717db2efeb95174e3882d0f3e1001be9384588087934ecb841`

The companion machine-readable summary is retained at
`run_state/browser_qualification/evidence/rel01-supplementary-evidence-2026-08-27.json`.

The retained tooling is a post-squash safeguard. It has no routine Justfile
entrypoint and does not change wall output.
