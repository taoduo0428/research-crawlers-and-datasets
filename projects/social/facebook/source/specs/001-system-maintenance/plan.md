# Implementation Plan: System Maintenance and Optimization Loop

**Branch**: `001-system-maintenance` | **Date**: 2026-10-02 | **Spec**: `spec.md`

## Summary

Use the new Spec Kit lifecycle to audit and improve the current Facebook capture system in bounded slices. First preserve and classify the existing dirty worktree, then close verified boundary regressions, add deterministic checks, and leave platform-only items explicitly gated. No product redesign or blanket refactor is planned.

## Technical Context

**Language/Version**: Python 3.12/3.13, PowerShell 5.1, JavaScript/Node 22 checks, Android helper Java.
**Primary Dependencies**: stdlib Python, mitmproxy 12.2.3, frida 17.17.0, frida-tools 14.10.4, requests 2.32.5, ADB/Android SDK.
**Storage**: JSON job/batch manifests, run directories, CSV/JSON artifacts, local configuration; sensitive runtime data is outside the release allowlist.
**Testing**: `unittest`, PowerShell AST/fixture checks, Node syntax checks, GitHub Windows/macOS CI, local API/UI dummy-server contracts, real Android/Facebook acceptance when available.
**Target Platform**: Windows primary; macOS paired implementation; Android emulator as external runtime.
**Project Type**: Local asynchronous capture service with web UI, platform runners and analysis pipeline.
**Performance Goals**: No unmeasured speed claim. Preserve current waits and completeness thresholds until same-condition evidence exists.
**Constraints**: Single active worker, strict target/source/completeness/terminal semantics, no credential/data cleanup, no external production probes.
**Scale/Scope**: Current dirty worktree; bounded maintenance slices rather than whole-system rewrite.

## Constitution Check

- PASS: Scope is maintenance and repair, not a product redesign.
- PASS: Existing API, URL families, single-worker semantics and result guards are explicit non-regression constraints.
- PASS: Every repair gets a regression test and rollback record.
- PASS: Platform checks are split between static/CI/local/real-platform evidence.
- PASS: No commit, push, publish, service restart, AVD reset or user-data cleanup is included.

## Phase 0 Research and Baseline

- Read current instructions and baseline; classify pre-existing dirty changes.
- Map API → JobManager → runner → ADB/mitmproxy/Frida → analyzer → artifacts.
- Run the feasible local suite once and classify failures; do not treat historical results as current.
- Run static checks for Python imports/compile, PowerShell AST, Node syntax, dependency and release-list leakage.

## Phase 1 Design

- Keep shared API security and request-boundary logic in a narrow helper.
- Keep URL validation consistent across API, Android and share-resolution paths.
- Keep Windows remote-shell quoting platform-specific and keep Python/macOS argv behavior unchanged.
- Preserve all completion/visibility/source guards and worker/lease state transitions.
- Add or update only tests that prove the changed invariant.

## Phase 2 Implementation

- Apply only verified, low-risk fixes in the affected modules.
- Add regression tests before or together with each repair.
- Do not change waits, capture commands, API field names or device configuration unless a separate feature is specified.

## Phase 3 Verification and Review

- Run focused tests after each slice.
- Run the full feasible local suite once; classify non-code failures.
- Run PowerShell AST and Node syntax checks.
- Review current diff independently for scope drift and cross-platform parity.
- Run real Windows/macOS/Android/Facebook checks only when the configured environment is available and without resetting user state.

## Project Structure

```text
specs/001-system-maintenance/
├── spec.md
├── plan.md
└── tasks.md
.specify/
├── memory/constitution.md
├── templates/
├── scripts/powershell/
└── workflows/
.agents/
├── rules/context_and_standards.md
├── rules/spec-driven-workflow.md
└── skills/speckit-*/
```

Existing application modules remain at the repository root; `tests/` remains the deterministic test boundary. No new application layer or parallel worker subsystem is introduced.

## Complexity Tracking

No constitution violations. No new service, database, queue, worker pool or cross-platform abstraction is introduced by this plan.

## Additive guarded operator route

One new acknowledgment POST route only; no existing submit/cancel/result schema change.
Parse exactly one acknowledge:true JSON field (reject duplicate keys/extra fields),
under shared 64KiB framing + Host/Origin guard. Body-read errors separate from Manager
I/O; response sending outside mutation catch avoids repeated responses. Manager performs
all reconciliation safety checks. Test directly/through v2 using temporary state and
fake devices, verify rejected inputs never delegate, write failure retains block, valid
ack retains failed status, and old GET is nonmutating. Rollback removes this route and
associated documentation/tests, leaving internal method and old interfaces intact.


## Scheduler-health diagnostic slice

Capture thread liveness under Manager lock in health(), expose only state/alive (no IDs/argv). Keep all prior keys/activity rules and no-network-thread probe; adb health remains existing code. Bound native liveness errors to unknown. Test absent/unstarted/running/stopped and failed probe preserving identity/block state; API/HTTP regression and local full gate before applying.


## Artifact boundary verification

Share a read-only helper for Manager job/batch lookup and artifact metadata. Resolve trusted root/candidate, reject child symbolic links and escaping junctions, keep logical path/name for API compatibility and root relocation. Map path resolution failures to existing availability errors. Temporary synthetic link fixtures only; test regular files/missing/empty/relocated root and job/batch/meta rejection, then full local gate and existing real CSV download. No user-data mutation.


## Health latency measurement slice

Lazy request-local ContextVar holder initialized/reset around health worker snapshots. Only real adb_snapshot consumes holder, preserving existing injected/mocked snapshot providers. Parse exact device-list header and unique supported states; invalid list falls back, no guessed healthy state. Tests compare output/call counts/freshness/proxy failures and context cleanup; alternate same-host/config old/current health measurements before any performance claim. No concurrent tenants/parallel probes or probe-budget changes.


## Mac pairing verification slice — 2026-10-04

Trace START.command/setup.sh -> shared local/API -> facebook_android_capture.py. Round-trip five URL-family argv through the real parser under a patched Darwin selection; assert serial/ports/output/artifacts/limit semantics. Exercise existing unsupported Shares/PCAP guards and cleanup failure accounting using mocks that prohibit real child processes/ADB. Run focused Mac setup/device/runtime and Android contracts, then local gate with retained logs. Preserve existing dirty source, services/config/device and remote branch; no merge based on simulated acceptance. Back up touched existing files to an external timestamped directory.


## Mac Shares + PCAP implementation plan — 2026-10-04

Extend facebook_android_capture.py in place with validated --collect, safe Shares selector/list guard, fresh source-bound navigation/reopen, query-driven pagination and shared analyzer/API acceptance. Preserve original Reaction diagnostics in combined mode and add readonly-ui-share diagnostics; retain all capture records for contamination checks. Default Reaction remains unchanged. PCAP is a narrow Android adapter: existing root/tcpdump probes, unique remote path/PID marker before start, /proc identity checks before signals, bounded SIGINT/SIGTERM waits, pull into new temp file, standard PCAP header + remote/local SHA256, atomic local publication and scoped cleanup. Retain remote evidence on uncertainty, record failed cleanup in existing runner status/lease protection. Update previous unsupported tests, add negative/source/phase/PCAP fixtures and replay analyzer/API through actual subprocess in temporary directories; run full gate and release export. Docs/CI pair both platforms; rollback from external timestamped snapshot, no running service intervention.


## PCAP premature exit repair — 2026-10-04
Reproduce valid PCAP/header/hash publication from a pre-stopped producer using the existing adapter fixture. Reject pre-stopped tcpdump before any signal/pull/publication/removal; keep existing bounded owned SIGINT/SIGTERM shutdown and normal hash validation. Validate negative red/green and execute cleanup/status, then affected/full local suite and test-branch CI; no real device/process fault injection.


## Mac API log handoff repair
Forward internal --api-log only from Darwin command builder. Add narrow output preparation guard: API mode requires sole entry capture.log, non-symlink regular file, same dev/inode as actual stdout via fstat; standalone retains new/empty. Verify actual run_capture_job opens/reuses stdout handle through a real child process with fake device boundaries and cancellation status, plus mismatch/reused output negatives. Preserve API process callbacks/result/pruning and existing runner status; no host setup or real ADB.
