# Feature Specification: System Maintenance and Optimization Loop

**Feature Branch**: `001-system-maintenance`
**Created**: 2026-10-02
**Status**: In Progress
**Input**: User request to use the new engineering framework to inspect and safely improve the existing Facebook system.

## Scope

This feature is a bounded maintenance pass over the existing system. It does not redesign the product, change the capture algorithm, shorten waits, change public API behavior, reset Android state, or replace the current single-worker architecture.

## User Scenarios & Testing

### User Story 1 - Establish a trustworthy baseline (Priority: P1)

As a maintainer, I need current source, Git, test, runtime and platform evidence separated so that later changes are based on the actual worktree rather than historical claims.

**Independent Test**: A maintenance report identifies the current dirty state, affected modules, available deterministic tests, runtime gates, and unavailable real-platform checks without starting services or touching account/device state.

**Acceptance Scenarios**:
1. Given an existing dirty worktree, when the maintenance pass starts, then all unrelated changes remain untouched and are listed as pre-existing.
2. Given Windows/macOS/Android-specific code, when evidence is collected, then static, CI, local-contract and real-platform results are reported separately.

### User Story 2 - Preserve security and boundary invariants (Priority: P1)

As an operator, I need local API/UI, URL, artifact, process and data boundaries checked and repaired without weakening target/source/completeness or cleanup guards.

**Independent Test**: Focused security and contract tests reject malformed or cross-origin dummy requests, spoofed Facebook authorities, unsafe framing and invalid artifact access while valid same-origin and CLI paths remain usable.

**Acceptance Scenarios**:
1. Given a malformed, cross-origin or absolute-form request, when it reaches the local API/UI fixture, then it is rejected before state-changing work or backend rebinding.
2. Given a Facebook URL from a supported URL family, when it is normalized, then it remains accepted only when its hostname and authority are valid.
3. Given an empty or partial result, when completion is calculated, then it cannot be promoted to full completion without terminal and visibility/completeness evidence.

### User Story 3 - Optimize only from evidence (Priority: P2)

As a maintainer, I need performance and reliability work to be measurable and reversible so that speed improvements do not trade away correctness or user state.

**Independent Test**: A future optimization records before/after measurements under the same conditions, includes completeness and failure-rate checks, and can be rolled back without changing login, AVD, ADB, Frida, certificate or proxy state.

**Acceptance Scenarios**:
1. Given no comparable baseline, when an optimization is proposed, then the system produces measurement tasks rather than silently changing stable waits or limits.
2. Given a regression or cleanup uncertainty, when recovery is exercised, then the worker remains blocked or safely reusable according to verified process and lease state.

## Edge Cases

- Existing uncommitted source moves or test relocation must not be overwritten or reset.
- A CI pass without Android/Facebook evidence must remain a partial acceptance.
- `query-not-triggered`, source mismatch, rate limit, session restriction, open pagination tail and unverified cleanup remain failures or protected states.
- Windows and macOS may have different native tools and permissions; unsupported checks must be recorded, not guessed.
- A local HTTP service is loopback-bound by design; deployment through a private tunnel requires its own identity and source validation.

## Requirements

- **FR-001**: The maintenance workflow MUST inspect current instructions, baseline, entrypoints, tests, CI and Git state before editing.
- **FR-002**: The workflow MUST preserve pre-existing uncommitted changes and must not use forceful Spec Kit initialization or repository-wide reset.
- **FR-003**: The system MUST preserve the existing API contract, five URL families, single active worker semantics, target/source/completeness/terminal/visibility checks and cleanup/lease guards.
- **FR-004**: The workflow MUST add regression tests for every confirmed bug fix and report exact commands, exit codes and unavailable evidence.
- **FR-005**: Performance changes MUST include comparable before/after measurements and MUST NOT shorten stable waits or weaken validation without evidence.
- **FR-006**: Security review MUST distinguish confirmed, needs-validation and rejected claims and must not test production, real accounts or shared devices.
- **FR-007**: Windows/macOS changes MUST inspect paired implementations, CI and documentation before acceptance.
- **FR-008**: No task in this feature may commit, push, publish, reset the AVD, log out Facebook, delete user data or modify global Codex configuration without separate authorization.

## Success Criteria

- **SC-001**: A current maintenance report identifies all changed files, deterministic test scope, runtime gates and remaining validation gaps.
- **SC-002**: Focused security/boundary regression tests pass without changes to login, AVD, ADB, Frida, certificate, proxy or capture-completion semantics.
- **SC-003**: The full feasible local test suite is run once after the maintenance changes; every failure is classified as code failure, environment limitation or platform-only gap.
- **SC-004**: No new false-complete result is introduced for empty, partial, source-mismatched, rate-limited or non-terminal evidence.
- **SC-005**: Every optimization claim has a same-condition baseline or remains explicitly unoptimized and deferred.

## Assumptions

- The current worktree is the authoritative maintenance target even though it contains pre-existing uncommitted changes.
- Real Android/Facebook and real macOS acceptance require the existing configured environments and are not replaced by local unit tests.
- The feature directory records the maintenance pass; it is not a replacement for the complete product specification.

## Incremental operator acknowledgment contract — 2026-10-02

Additive POST /v1/jobs/{job_id}/reconcile-result-persistence accepts exactly a JSON
object with one acknowledge:true field after the operator reviewed failed output.
Host/Origin/body framing guards apply before any state action. The operation delegates
to the guarded Manager acknowledgment, never makes the old failed task successful or
requeues it, and preserves other blockers. Explicit transport/process/device/active
checks and persistence-before-release still govern. No automated client retry or browser
button automatically calls it. Unknown job404, state conflict409, malformed body400,
body-timeout408, local storage503, success200. Existing routes/response contracts unchanged.
This does not reconcile faults lacking a durably stored failed-result diagnostic.


## Additive scheduler-health diagnostics — 2026-10-03

GET /health retains HTTP readiness, worker activity, queue and lease fields. Append scheduler={state,alive} as a liveness snapshot only: running/true, not-started/false, stopped/false, unknown/null on a RuntimeError/OSError probe. Do not restart, discard uncertain thread handles, release lease or revive jobs from this observation. Neither service=ready nor scheduler.alive proves job completion or transport cleanup. Existing clients may ignore added field.


## Artifact physical-path robustness — 2026-10-03

Only known logical artifact names remain accepted. Additionally reject symbolic links below the trusted run root and directory links resolving outside that root, for job and batch downloads; metadata must not advertise those files as available. Preserve trusted root relocation/root symlink and normal parent-symlink platform paths. This is accidental/stale link robustness, not a demonstrated lower-trust exploit or hostile-local-writer sandbox; hardlink/TOCTOU guarantees not claimed. No live link tampering.


## Request-local ADB health batching — 2026-10-03

Within a single health() request, share one validated adb-devices state snapshot; no TTL or cross-request caching, no signature/key changes. Keep original boot/proxy queries, independent timeouts, offline and proxy-error semantics; malformed/error/duplicate-device inventory falls back to original individual state checks. Outside health(), adb_snapshot remains independent. Context is always reset even on failure. This targets health latency/native child calls, not Facebook capture speed.


## Mac pairing continuation — 2026-10-04

Current user permits continuation without a native Mac. Verify the existing active Mac Reaction runner with no-device deterministic fixtures, not the legacy transport-only simulator. Preserve API/URL/default unlimited traversal and cleanup guards. Explicitly report Shares and PCAP capability exclusions and native ABI/trust/login/terminal acceptance as separate boundaries. This increment adds tests/evidence only, not a new capture feature or proof of native execution.


## Mac Shares + Android PCAP feature — 2026-10-04

User explicitly requests full integration respecting OS differences. Mac API must forward collect=reactions/shares/both and pcap through the existing native Python runner; defaults and Windows argv remain compatible. Shares require safe count-only navigation (never composer), fresh per-phase query/source/window, terminal page and shared validate_share_result/API validation, visibility/gap preservation and phase-gated combined capture. PCAP uses Android tcpdump via serial-bound native Mac adb, no host sudo/pktmon/Windows tool. Require existing Android root/tool, run-specific remote path/PID identity, bounded graceful stop/readback/pull/header+hash verification, and fail-closed cleanup on cancellation, timeout, stale PID or transfer error. No SDK/Frida/APK upgrades, ADB server restart, account/AVD reset, hidden fallback or false full-visibility claims. Native Mac acceptance unavailable; simulation, local/CI and live acceptance remain distinct.


## PCAP premature exit contract — 2026-10-04
Requested PCAP must not finalize successfully when tcpdump is already stopped before the runner requests shutdown. A valid header and matching transport checksum do not establish capture duration. Retain remote evidence, report failure through existing cleanup protection and still restore owned transport. Do not change Windows capture, add device polling or claim packet-loss-free recording.


## Mac API log handoff contract — 2026-10-04
Mac API creates capture.log before launching runner. Allow only an explicitly API-launched capture.log proven identical to the inherited stdout regular-file descriptor, with no other output entries or links. Preserve standalone new/empty restriction and Windows argv. Reject directory/log identity mismatch before transport mutations; no arbitrary reused run, existing marker or evidence overwrite.
