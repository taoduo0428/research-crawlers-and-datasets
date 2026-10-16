# Real-platform acceptance matrix

Date: 2026-10-02
Scope: follow-up acceptance for the System Maintenance and Optimization Loop.
This is a runbook/checklist, not evidence that any real-platform step has passed.

## Safety preconditions

- Confirm the current Git status and preserve unrelated dirty changes.
- Record owner and command line for API/UI, ADB, emulator, proxy, Frida and capture workers.
- Do not reset the AVD, uninstall Facebook, log out the account, rotate certificates, or delete runtime data.
- Use a disposable task/job and a known permitted Facebook target. Keep raw logs and redacted summaries outside the repository.
- Stop before any action that would require credentials, a new APK, an unavailable Mac host, or user confirmation.

## Windows runtime

Evidence required:

1. Start the configured API/UI through the existing launcher and record the exact command, PID, ports and config source.
2. Verify `/health`, worker identity, active/standby state and proxy ownership without changing the account or device.
3. Run one permitted disposable job through the normal client path and capture:
   - accepted request and job ID;
   - target/source match;
   - process tree and worker ownership;
   - transport/proxy state before and after;
   - terminal/completeness/visibility result;
   - output manifest and artifact hashes.
4. Exercise cancellation and one controlled failure using the existing fixture or an authorized disposable target. Confirm process-stop evidence, transport restoration, lease release/blocking and no unintended requeue.
5. If a persisted `failed-result-persistence` diagnostic exists, inspect job/health/storage first and use the explicit acknowledgment route only once. Do not automatically retry the POST.

Acceptance is per job and per state transition; HTTP status alone is insufficient.

## Android/ADB/Facebook

Evidence required:

- `adb devices -l`, device serial, model/API/ABI and package identity before the job;
- no unexpected APK reinstall/downgrade or account-state change;
- Facebook app version and permitted target URL family;
- UI XML/screenshot or equivalent visible state before and after the capture action;
- network/Frida evidence correlated with the same job ID and target;
- result pagination terminality, source/target match, visibility and completeness;
- cleanup evidence for process, proxy, certificate and ADB reverse state.

A count, HTTP 200, or `completed` manifest does not close this gate without the correlated visible and terminal evidence.

## macOS

On a real authorized Mac host, run the paired macOS setup/readiness/cleanup checks and one disposable job with the same evidence fields as Windows. Record architecture, Python/venv path, ADB/Frida/native runner versions, permissions, signal behavior and cleanup status. CI on `macos-latest` is separate from real-user permissions, device state and Facebook login acceptance.

## Performance

Only after a normal job and failure/cancel path are accepted, measure under the same target, account state, device, network and worker configuration:

- queue wait;
- device preparation;
- network/UI wait;
- local analysis/output writing;
- CPU, memory and disk I/O;
- result completeness and failure rate.

Do not shorten stable waits or claim an optimization from fixture-only measurements.

## Completion decision

Mark the maintenance objective complete only after each requirement in `specs/001-system-maintenance/spec.md` has current authoritative evidence. Keep native, hosted-CI, crash-consistency, security-history and performance items explicitly open when their required environment is unavailable.
