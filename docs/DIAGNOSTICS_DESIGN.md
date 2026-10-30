# Device Control Center — Hybrid Diagnostic Log (v0.1.11)

Status: **candidate, not installed or field-accepted**. Baseline: field-accepted v0.1.10 (commit 6554ba0), successful CI run 40957129512 and issue #7 closed after human Wayland validation (29/10/2026).

## Scope and constraints

- The JSONL recorder is OFF by default. Only clicking «بدء التسجيل» in the desktop app starts a session. Closing GTK or clicking «إيقاف التسجيل» ends it. No systemd unit, daemon, autostart, persistent preference, outbound connection, microphone capture, Bluetooth scanning or device command is added.
- The recorder reuses the existing core.snapshot() result gathered by the regular GTK 12-second refresh. No independent polling, bluetoothctl, pactl, wpctl, nmcli, or GNOME extension connections are made by the recorder.
- GTK remains responsive: a bounded queue delivers observations to a non-daemon worker which performs all JSONL writes and bounded final flushing. Queue overflow or I/O errors terminate recording; hardware display/controls continue.
- The existing GNOME 46 extension, SBC audio profile, Gtk.MenuButton/Gtk.Popover selectors and wheel-handling code are left untouched.

## Data model, timestamps and epistemic status

Each line is an independently parseable UTF-8 JSON object containing schema_version: 1, a session-specific random ID, type, and observed_at in UTC ISO 8601. Records include:

- session_start: schema/session metadata and configured checkpoint interval.
- checkpoint: sanitized state at the first processed observation and approximately every 300 seconds of active observations. If refreshes fail or stop, no fabricated measurements are emitted.
- state_change: old/new sanitized values for connection, battery, RSSI, selected audio device, volume/mute, active Bluetooth audio profile/codec, Wi-Fi connection/signal and collection failures.
- read_error: classified source error; never raw stderr.
- session_end: graceful stop after queued records are flushed. A crash, disk failure, full queue or exceeded session budget may omit this record.

observed_at is the recorder's time of processing an application observation, not the headset's battery measurement time. The field source_reported_at is always null for Battery1 because a trustworthy originating-device timestamp is not available. Changes are only bracketed by consecutive observations, not proven to have happened at one precise instant. No value smoothing, inferred physics, inferred battery reporting protocol or distance estimate is introduced.

Values preserve explicit available, unavailable, stale and error states; a collection error does not imply device disconnect. A successful Bluetooth query returning no devices can indicate device removal; a failed query cannot. The data model keeps BlueZ and UPower sources distinct when reported. Wi-Fi SIGNAL is stored as percentage; Bluetooth RSSI as optional dBm, without conflating them.

## Privacy and storage

The recorder constructs a strict allowlist of fields before serializing:

- Bluetooth MAC, alias/name, Wi-Fi SSID, audio device names, raw command output, raw exception messages and stable identifiers are never saved.
- Per-session identifiers such as bt-1, sink-1 and audio-1 live only in memory and cannot be used to link sessions directly. This is pseudonymization within a session, not a guarantee against all indirect reidentification through distinctive measurements.
- Only bounded numeric readings, enumerated status, bounded validated profile/codec tokens and event categories are stored. No audio samples, microphone input or network telemetry.
- Files are written exclusively in $XDG_STATE_HOME/device-control-center/diagnostics/, falling back to ~/.local/state/device-control-center/diagnostics/. The managed directories must be private, owned by the current user and must not be symlinks. A new file uses exclusive creation, no-follow and 0600 mode.
- The session limit is 5 MiB (stop rather than rotate a live session); the aggregate budget is 25 MiB. On starting a new session, expired managed logs older than seven days are removed; if necessary, older managed logs are removed to reserve room for the next session. Retention cleanup runs on session start, not continuously. Only files matching dcc-*.jsonl are considered and nonregular files are ignored.
- If the worker cannot create a private directory or cannot write/flush, it fails closed and reports its status to GTK without impacting device control. Buffered records may be lost after abrupt process termination.

Existing «تصدير تقرير JSON» remains distinct: it exports the application's full snapshot, which can include MAC/SSID; the privacy-preserving recorder does not call it. A future redacted JSON export would require separate consent and tests.

## User operation

1. Open the application and observe the ordinary device snapshot.
2. Click «بدء التسجيل». Only at that moment is the private log directory/file created. The first successful regular refresh after starting becomes the first checkpoint; a pre-existing cached GTK snapshot is not relabeled as a new observation.
3. While the window is open, the existing 12-second refresh provides observations; the recorder saves only changes and periodic checkpoints. A recording status label is visible in the fixed footer.
4. Click «إيقاف التسجيل»; inspect the local JSONL file after «اكتمل حفظ السجل». Closing the window requests a graceful stop. A non-daemon writer finishes flushing before the process exits; it does not keep a GTK window or daemon running.
5. Export or analyze the JSONL locally, without uploading it. The file path appears in a tooltip for the status label.

## Regression and testing plan

- Python unit tests use synthetic readings for 100→90→missing→100, absent battery, source failure vs. disconnect, Wi-Fi query failure, privacy canaries, file mode, session budget, symlink rejection, XDG path, and retention.
- GTK tests under Xvfb use synthetic snapshots and patch all hardware writes. A new test exercises the actual click-to-start/stop workflow and verifies private JSONL contents.
- Existing XTEST wheel and audio codec regression tests must remain green. The CI builds DEB and verifies package version 0.1.11; GNOME extension is unchanged.
- Do not infer field acceptance from CI. Once the candidate's automated tests pass, manual Ubuntu 24.04 GNOME 46 Wayland validation on HP 630 should compare idle and active CPU/RAM/disk load, confirm A2DP/SBC/a2dp-sink unchanged, sound stable, full two-direction wheel scrolling, initial/current battery accuracy and GNOME top-bar battery indicator, and finally check the produced JSONL privacy and retention behavior.
- No installer should be applied to the user's computer until explicit separate approval to do so.
