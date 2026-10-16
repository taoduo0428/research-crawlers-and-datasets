# Tasks: System Maintenance and Optimization Loop

**Input**: `spec.md`, `plan.md`
**Tests**: Required for every confirmed bug fix; real-platform checks remain separate gates.

## Phase 1: Baseline and Safety

- [x] T001 Record current Git status, HEAD, affected files, active processes/ports/devices and existing configuration ownership without resetting anything.
- [x] T002 Record the current architecture/data flow and classify historical, current-static, deterministic-test, local-contract and real-platform evidence in `specs/001-system-maintenance/`.
- [x] T003 Run the feasible local unittest suite and classify each failure as code, environment or platform-only; preserve raw output.
- [x] T004 Run Python compile/import, PowerShell AST, Node syntax, dependency and release-file leakage checks for changed surfaces.

## Phase 2: Boundary and Correctness Repairs (P1)

- [x] T005 Verify API/UI request framing, Host/Origin handling, backend authority and loopback/private-tunnel assumptions with dummy local servers only.
- [x] T006 Verify Facebook hostname/authority normalization across API, Android and share-resolution paths for all supported URL families.
- [x] T007 Verify result-completion invariants for empty, partial, query-not-triggered, source-mismatch, rate-limited and open-tail captures.
- [x] T008 Add or repair regression tests for each confirmed boundary/correctness issue without changing public API field names or error semantics unnecessarily.
- [x] T009 Review artifact paths, manifest recovery and release whitelist for traversal, sensitive data and stale-state risks; fix only confirmed issues.

## Phase 3: Runtime Reliability (P1)

- [x] T010 Verify single-worker queue, primary/standby takeover, process identity, cancellation, cleanup and capture lease transitions with isolated fixtures.
- [x] T011 Verify that failure, timeout, restart and uncertain cleanup remain fail-closed and do not reuse a worker prematurely.
- [ ] T012 Check Windows and macOS implementations of shared runtime contracts; add platform-specific regressions only where behavior differs.

## Phase 4: Evidence-Based Optimization (P2)

- [ ] T013 Establish same-condition measurements for queue, device preparation, network wait, UI wait, local analysis, output writing, CPU, memory and failure rate.
- [ ] T014 Optimize only the measured bottleneck; do not shorten stable waits or remove completeness checks without a new acceptance matrix.
- [ ] T015 Re-run before/after benchmarks and verify target/source/completeness/terminal/visibility invariants and rollback behavior.

## Phase 5: Independent Review and Convergence

- [ ] T016 Perform independent diff/code review focused on scope, API compatibility, URL families, worker semantics, platform parity and sensitive-data boundaries.
- [ ] T017 Run deterministic regression, CI-equivalent checks and any available real Windows/macOS/Android/Facebook acceptance; report unavailable gates explicitly.
- [x] T018 Run Spec Kit converge and append remaining work to this task file without deleting unresolved evidence.
- [ ] T019 Update maintenance report and mark this feature complete only when each requirement has authoritative evidence.

## Execution Order

T001–T004 are prerequisites. T005–T009 can proceed in bounded slices after the baseline. T010–T012 require no active capture service and must preserve existing device state. T013–T015 are blocked until a measurable bottleneck exists. T016–T019 are mandatory before claiming completion.
# Update task checkboxes after evidence collected in the 2026-10-02 maintenance pass.


- [x] T020 Add and verify non-destructive tools/maintenance_gate.py as the repeatable local maintenance entrypoint.


## Phase 6: Convergence — 2026-10-02 verification audit

Earlier checked tasks record the prior pass only; they do not prove whole-system acceptance.
Current source review found a maintenance gate that silently filtered missing required files,
reported skipped tools as PASS, wrote pycache through py_compile, and used the invoking
interpreter rather than the project virtual environment. The new tests below establish
those result semantics without executing real capture work.

- [x] T021 [US1] Make tools/maintenance_gate.py fail for missing required files and release path defects, distinguish passed/failed/partial, and use .venv for unittest/dependency checks (FR-001, FR-004, SC-003; tests/test_maintenance_gate.py).
- [x] T022 [US2] Execute the web_v2 artifact renderer with HTML-like dummy metadata; prove literal text and unchanged preview/download links rather than relying only on source string assertions (FR-004; tests/test_frontend_security.py).
- [x] T023 [US1] Record current interpreter, dependency, static and regression evidence with explicit limitations in specs/001-system-maintenance/verification.md; ensure AGENTS.md links the project Constitution/workflow for subsequent tasks (FR-001, FR-004, SC-001).
- [ ] T024 [US1] Verify gate negative cases on both Windows/POSIX path conventions and preserve no-network/no-real-device fixtures; native macOS execution remains unverified (FR-007, SC-003).

No convergence task authorizes commits, pushes, deployment, service restart or user-state cleanup.


## Phase 7: Convergence — shared cleanup ownership

Source review of T010/T011 found that lease release and worker restoration considered
other jobs' process evidence but omitted their unresolved transport cleanup. On restart,
two failed/cancelled manifests with stopped processes could lose the global block after
only one cleanup succeeded. Eight same/different-worker and cleanup-order fixtures now
check that the remaining manifest still blocks dispatch until its own cleanup succeeds.

- [x] T025 [US2] Preserve all other jobs' pending transport cleanup in _release_capture_lease_locked and _restore_worker_after_capture_cleanup_locked; prove first-cleanup-blocked/last-cleanup-reusable transitions with temporary manifests and mocked cleanup (FR-003, FR-004, SC-004; tests/test_facebook_capture_api.py).
- [ ] T026 [US1] Independently review the two changed predicates, assess persisted-state compatibility, and separate isolated state-machine proof from real Android proxy/lease acceptance (FR-007, SC-001).

This is a correctness repair; stable waits, queue concurrency, API fields and dependency
versions are unchanged. T010/T011 remain open until all material paths are audited.


## Phase 8: Convergence — transactional Worker configuration

T010/T011 state-path review found that set_worker_enabled published its candidate
in memory before the atomic configuration write, and reload_workers did not apply
the existing busy-active-worker disable guard. Four temporary-file negative cases
reproduced partial live-state changes and bypassed rejection. The repair preserves
old live state on write failure and validates a reload fully before publishing it.

- [x] T027 [US2] Persist a validated set_worker_enabled candidate before committing live state; retain memory/config/active Worker/thread/wake state on atomic-write failure (FR-003, FR-004; tests/test_facebook_capture_api.py).
- [x] T028 [US2] Apply the busy active Worker disable guard to reload_workers; prove rejected reload has no partial runtime addition and idle switching/non-disabling busy updates remain supported (FR-003, FR-007; tests/test_facebook_capture_api.py).
- [ ] T029 [US1] Independently review configuration write/reload concurrency, including state snapshots and HTTP error reporting; retain real-platform acceptance as a separate requirement (FR-004, SC-001).

Temporary test configuration changes are not edits to the user's worker registry.
This increment does not change queue parallelism, waiting constants or API field schema.


## Phase 9: Convergence — reload/setter stale snapshot

T029 concurrency review reproduced a stale configuration snapshot: reload read disk
outside the manager lock, an enable/disable setter committed a newer registry, and
reload then published its older snapshot over memory. A deterministic event-controlled
interleaving checks both directions without sleeps or retry-based race reproduction.

- [x] T030 [US2] Serialize reload read/validate/publish with enable/disable writes using the existing manager lock; prove disk/live-state consistency for both standby enable and disable (FR-003, FR-004, SC-003; tests/test_facebook_capture_api.py).
- [ ] T031 [US1] Review lock duration and HTTP persistence error reporting after the concurrency correction; native operator-file edits and independent review remain separate acceptance gates (T029, FR-007, SC-001).

The lock coordinates API operations within this manager, not independent external
editors or multiple API instances. No distributed/multi-tenant semantics are introduced.


## Phase 10: Convergence — Worker HTTP storage errors

T029/T031 review reproduced dropped API connections on Worker configuration read/write
OSError. Via the v2 proxy, these local faults were instead presented as unreachable
backend. Manager failures now map to sanitized JSON 503 for Worker management only;
normal 200, missing-worker 404 and conflict 409 remain unchanged. Response transmission
is outside the I/O catch so a client disconnect is not mislabeled or replied to twice.

- [x] T032 [US2] Return sanitized Worker I/O errors over direct API and v2 proxy while preserving live/disk state on failed config reads/writes; prove subsequent health still responds (FR-003, FR-004; tests/test_facebook_capture_api.py).
- [ ] T033 [US1] Verify the HTTP error catch covers manager operations, not response writes; review remaining job/manifest I/O failure handling separately and retain independent/platform acceptance (T029, T031, FR-004).

503 uses the existing error-body field rather than a new success/error schema.
No automatic retry, global exception suppression or production fault injection is added.


## Phase 11: Convergence — operator-visible scheduler claims

Current API source reports primary-standby-serial and holds a single active job, but
web_v2/index.html still labels batching as parallel capture/testing. This is a source-
visible operator guidance inconsistency, not evidence to change the scheduler.

- [x] T034 [US1] Align the v2 page's explicit Chinese parallel-capture claims with the actual serial active-worker/standby model; preserve selectors, UI behavior and batch input, and add an operator-facing wording regression (FR-003, SC-001; web_v2/index.html, tests/).

Keep branding separate from capability claims; do not introduce parallel Workers to
make the label true or treat wording checks as runtime acceptance.

## Phase 12: Convergence — committed submission before dispatch

Submission persistence review reproduced in-memory queued ghosts after a manifest
write failed. Batch failures could publish children before the parent/derived output
commit, and partial queued child manifests could auto-run on restart. The repair
uses the existing version-1 batch manifest as a commit marker; no schema migration
or automatic POST retry is introduced, and uncertain process/cleanup guards remain.

- [x] T035 [US2] Persist single-job metadata before publishing it in _jobs; prove failed writes preserve existing jobs, disk records and queue counts (FR-003, FR-004; tests/test_facebook_capture_api.py).
- [x] T036 [US2] Prepare batch outputs/child metadata and commit the parent before publishing/enqueuing; reject uncommitted queued children on restart while preserving committed v1 recovery and uncertain-process lease protection (FR-003, SC-004; tests/test_facebook_capture_api.py).
- [x] T037 [US2] Return sanitized submission I/O 503 through API/proxy; keep body-timeout 408, invalid-input 400, accepted 202 and single-response behavior (FR-004; tests/test_facebook_capture_api.py).
- [ ] T038 [US1] Independently review submit/assignment/finalization manifest failure paths and classify native restart/cleanup evidence; this increment does not close all storage/runtime/performance gates (T010, T011, T033, FR-007).

Partially staged failure records/output remain available; no old run, private data or
configuration is removed. Batch commit coordination is scoped to the existing manager,
not separate processes or a new distributed queue.


## Phase 13: Convergence — persist execution ownership before capture

Assignment review reproduced that failing the running manifest write had already
mutated job attempts and active Worker state. The worker loop then classified this
pre-capture storage error using device/process recovery. The private assignment
candidate now commits before publication; scoped dispatch I/O failure requeues
without capture/process callbacks or device recovery and uses a bounded event wait.

- [x] T039 [US2] Preserve queued job, disk manifest, attempt count and Worker ownership on assignment write failure; prove transient storage recovery leads to exactly one capture attempt (T010, T011, T038; FR-003, FR-004).
- [ ] T040 [US1] Review dispatch persistence retry observability and completed/failed/final callback storage failures; do not treat current local state-machine regression as real device acceptance (FR-007, SC-001).

The dispatch pause is not a shortened capture/navigation wait and does not retry POST.


## Phase 14: Convergence — finalization I/O must retain dispatch safety

T040 final-callback review reproduced that OSError escapes before queue task_done
and active-job cleanup. Finalization persistence failure now retains a global lease
block/orphan marker and lets the scheduler finish local accounting without starting
another job. It does not pretend transport restoration or durability was successful.

- [x] T041 [US2] Preserve queue accounting and clear local active ownership while blocking dispatch after final callback I/O failure; verify callback failure and internal persist failure with dummy capture (T038, T040; FR-003, FR-004).
- [ ] T042 [US1] Independently review finalization durability and operator recovery from blocked storage faults; classify completion-path exceptions separately from real-device cleanup (FR-007, SC-001).

No automatic device retry, unknown-process kill, cleanup-state reset or user-data
removal is authorized by a metadata-write failure.


## Phase 15: Convergence — recovery can itself fail to persist

Failure-path review found that an OSError from device-recovery metadata, checkpoint
or failed-job persistence escaped the exception handler. Final callbacks could also
clear stopped-process ownership without proving those recovery records durable.

- [x] T043 [US2] Contain only recovery-stage storage exceptions, balance existing queue accounting, retain global dispatch blocking after final callbacks, and prevent failover requeue while recovery metadata is unconfirmed; test recovery/checkpoint/failed-manifest faults (T038, T040, T042; FR-003, FR-004).
- [ ] T044 [US1] Independently review double-fault restart behavior and operator recovery; retain native/platform/E2E performance gates rather than treating fixture success as full acceptance (FR-007, SC-001).

Storage uncertainty is not transport success. This task adds no unknown-process kill,
account/device reset, distributed queue, automatic POST replay or dependency upgrade.


## Phase 16: Convergence — measured local schema derivation

Local provenance schema construction repeatedly searched an ordered list for each
field of every row. A same-interpreter dummy benchmark alternates legacy/optimized
execution for six repetitions on identical 10k-row fixtures (15/60 extra fields).
The repair uses a membership set but retains the existing ordered output list.

- [x] T045 [US3] Measure and optimize _reactor_fieldnames membership while preserving header ordering, normalized key behavior, CSV values and provenance; record time and allocation tradeoff (FR-004, FR-005; tests/test_facebook_capture_api.py).
- [ ] T046 [US1] Retain benchmark reproducibility and independent review; do not extrapolate function-only dummy metrics to queue/UI/device/Facebook E2E performance (T013–T015, FR-007, SC-005).

Stable waits, reaction limits, parsing/source/visibility/terminal checks and post-user
dedupe semantics are unchanged. Full performance requirements remain open.


## Phase 17: Convergence — measured batch export schema scans

The two persisted batch CSV schema loops repeated ordered-list membership searches
for every exported row. A paired actual-batch-function benchmark preserves dummy
inputs/clock/writer capture and alternates execution six times for 5x1k-row fixtures.
Membership sets now retain the same ordered header lists and CSV output values.

- [x] T047 [US3] Measure/optimize batch-reactor and user-post-relation field membership; require exact captured export equality and real temporary CSV regressions for header order, provenance and per-post identities (FR-004, FR-005; tests/test_facebook_capture_api.py).
- [ ] T048 [US1] Independently review export/data contracts and compare actual I/O/device workloads; do not extrapolate in-memory captured-writer benchmarks to E2E capture (T009, T013–T016, FR-007, SC-005).

No source/terminal/completeness/visibility guard, cross-post identity contract,
waiting constant or worker concurrency is changed by the schema optimization.


## Phase 18: Convergence — independent export review and CI coverage

Fresh read-only export review found no normal-string schema-order regression but
identified missing provenance-value and two-layer identity assertions. Those are
expanded using dummy cross-job/source/reaction rows and actual temporary checkpoint
merge/CSV roundtrip. Review also revalidates output scope rather than E2E speed.

Current CI module lists omitted maintenance-gate and frontend-security tests that
local discovery ran. Both Windows/macOS lists now include them next to HTTP guards;
a layout regression initially failed on both platforms and now checks this mapping.

- [x] T049 [US1] Reconcile independent field/export review gaps with explicit provenance, input-preservation, cross-job/source/reaction and actual checkpoint-merge assertions; retain static review/runtime scope limits (T046, T048; FR-004, SC-001).
- [x] T050 [US1] Include engineering/frontend regressions in both platform CI commands and prevent omission with a layout guard; remote CI/native acceptance remains separate (FR-007, SC-003).
- [x] T051 [US1] Maintain a requirement-level completion audit with missing runtime/platform/performance/independent evidence and correct stale task-completion assumptions; do not mark whole objective complete from local gates (FR-001, SC-001, SC-005).

CI source wiring is not hosted CI execution. Out-of-contract malformed key/hash objects
are not newly supported by the ordered-string-field optimization.


## Phase 19: Convergence — completion evidence corrections

Earlier T003/T004 evidence was reopened during the audit because the original requirements also covered raw-output retention and leakage investigation. The fresh T071 gate now closes the execution portion again; the separate independent content/history limitation remains tracked by T053/T057.

- [x] T052 [US1] Preserve full private deterministic-check outputs with explicit truncation/timeout handling before closing raw-output evidence; avoid treating a green tail as full investigative proof (T003, FR-004, SC-003).
- [x] T053 [US1] Complete bounded release-content and Git-history leakage review separately from path hygiene; redact findings, never delete user credentials/data or publish audit logs (T004, FR-006, SC-001).

Independent export review is bounded; T016 and Worker/recovery/native/E2E review stay
open. No retrospective performance or hosted-CI success is claimed.

## Phase 20: Convergence — retained full local gate streams

T052 evidence review found that bounded gate summaries lost original outputs and
TimeoutExpired discarded partial diagnostics. Optional --log-dir now reserves a new
external run directory, opens exclusive stream files before execution, and retains
original bytes with bounded decoded tails. Report and I/O failures remain failures.

- [x] T054 [US1] Implement/test optional full private stdout/stderr capture, external/new-directory protection, unique commands, failure/start/timeout diagnostics and report-save failure semantics; retain tail-only default limits (T003, T052; FR-004, SC-003).
- [ ] T055 [US1] Independently review log destinations/privacy/platform limitations; do not claim Windows ACL, unknown-descendant cleanup, security-sandbox or historical logs are proven by this feature (FR-006, FR-007, SC-001).

No log cleanup, global configuration change, account/device operation or publication
is included. New check outputs stay outside release/source paths.


## Phase 21: Convergence — bounded redacted release/history content audit

A one-shot parent-owned scanner examines current release whitelist UTF-8 text and
local --all reachable Git blobs, without fetching remotes, reading ignored runtime/
account data, validating credential usability, editing history or printing matches.
Six signature rules produce path/line/rule/length/SHA256 only. All current candidates
are individually checked against source context and historical match fingerprints.

- [x] T056 [US1] Complete bounded release content/local reachable Git-blob audit, verify no read/size/count skips, classify fixture/placeholder hits without exposing values, and preserve exact input hashes plus redacted report outside source (T004, T053; FR-006, SC-001).
- [ ] T057 [US1] Independently review candidate classifications and explicitly retain signature-only/unreachable/unfetched/index-only history limitations; no exhaustive absence claim or automatic data/history deletion (FR-006, FR-008).

No additional overlapping security skill/tool is installed. A no-candidate/classified
signature result is not a complete security or publication approval gate.


## Phase 22: Convergence — result commit before completed publication

Current-source review and bounded fixtures reproduced completed shared memory before
checkpoint/manifest commit, and post-capture local output OSError entering device
recovery. A private completed candidate now commits before live publication. After
capture returns, result-I/O failure retains a global block without recapture/failover;
a failure diagnostic is attempted, not assumed durable if storage remains unavailable.

- [x] T058 [US2] Stage completed status/result privately through checkpoint and manifest commit; verify merge/build/checkpoint/commit/persistent write faults cannot expose completed/result or start a second capture (T038–T044; FR-003, FR-004, SC-004).
- [ ] T059 [US1] Independently review result-I/O vs device-failure classification and late/duplicate callback/operator recovery/restart durability; keep in-loop protection distinct from system-wide assurance (FR-007, SC-001).

Early red fixture lacked a bounded second-queue read; only its own confirmed test
processes were terminated, then the fixture received deterministic exit bounds.
No live capture, device/network probe or account/configuration modification occurred.


## Phase 23: Convergence — independent restart/callback durability findings

Fresh independent Worker result-commit review confirms the local fixes but identifies
remaining false-recapture risk across full-write-failure restart and reason-block
loss on later duplicate callbacks. Local in-loop blocking cannot prove these paths.

- [x] T060 [US2] Reproduce failed-diagnostic-persisted vs all-writes-failed restart and late/duplicate callback lease/orphan behavior with temporary manifests; retain operator recovery and crash-time durability gates instead of claiming exactly-once (T042, T044, T059; FR-003, SC-004).

Do not delete staged output or force-clear a lease to close this task. Independent
review scope/current local output persistence is not native Android/whole-system proof.

## Phase 24: Convergence — stopped callbacks are not persistence acknowledgment

Result/recovery/finalization fault fixtures showed repeated stopped callbacks clearing
the global lease reason. A distinct in-memory per-job persistence-reason registry now
prevents self/other clean callbacks or Worker restoration from erasing this evidence.
Three fault classes, duplicate callbacks, standby switching and separate clean-owner
completion are tested without real capture/device/registry state.

- [x] T061 [US2] Retain persistence reasons independently of process/transport stop proofs; verify repeated callbacks and other clean-owner release cannot clear the global block or orphan Worker (T059/T060, FR-003, SC-004).
- [ ] T062 [US1] Independently review new registry/helper coupling and prove native/operator/restart persistence acknowledgment separately; current memory-only registry is not disk durability or exactly-once (FR-007, SC-001).

No automatic storage-block clearing, new HTTP endpoint, distributed Worker model,
user-state reset or unknown-process termination is introduced by this slice.


## Phase 25: Convergence — rebuild persisted result-error reason on restart

New Manager initialization did not reconstruct the known failed-result-persistence
reason from an existing version-1 job diagnostic. Four transport-state fixtures
reproduced absent registry/block despite stopped process. Loader now restores the
reason before scheduling, without changing manifest version or normal failed behavior.

- [x] T063 [US2] Reconstruct the known persisted result-storage reason and verify two Manager reloads, delayed release/restore and standby assignment rejection; prove normal terminal diagnostics still dispatch normally and actual failure-path restart is blocked (T060/T062; FR-003, SC-004).
- [ ] T064 [US1] Review explicit operator reconciliation and all-write-failure/crash boundary; a reconstructed existing marker does not guarantee marker creation on unavailable storage (FR-007, SC-001).

No auto-clear/retry API, device cleanup or false durability claim is introduced.


## Phase 26: Convergence — explicit result-failure storage acknowledgment

Safety blocks alone are not operational recovery. Introduce an internal Manager-only
explicit acknowledgment primitive, not automatic enable/cleanup or a new HTTP route.
It acknowledges that the operator reviewed the failed result/output, never revives
failed work as completed or requeues it. Reconciliation is allowed only for failed
result-persistence diagnostics, stopped/absent-owned-process, explicit clean transport,
no active job and no unrelated device-failure marker. Save the reconciliation diagnostic
before removing only this job's registry entry; retain all other blockers.

- [x] T065 [US2] Implement/test acknowledge-required result-persistence reconciliation, stopped/transport/active/device guards, write-failure rollback, other-job blocker isolation and no recapture; verify acknowledged failed diagnostic remains failed across restart (FR-003, FR-004, SC-001).
- [ ] T066 [US1] Independently review primitive and design a separately guarded operator entry/UX; do not advertise a callable HTTP/CLI endpoint or disk-full/crash durability guarantee before it exists (FR-007, SC-001).

No real stored job is reconciled during development. Full-write-failure and non-result
fault reconciliation remain distinct requirements; failed-result acknowledgment is not
result validation, visibility/completeness proof or authorization to delete artifacts.


## Phase 27: Convergence — explicit guarded operator HTTP entry

Internal acknowledgment alone is not usable operator recovery. Add one explicit POST
with exact JSON acknowledgment and existing browser/framing protections, preserving
Manager guards, state audit and failed-task semantics. This is incremental API addition,
not a change to existing submit/cancel/result/Worker behavior or automatic retry.

- [x] T067 [US2] Add guarded reconcile-result-persistence route; reject absent/false/truthy/duplicate/extra acknowledgment, malformed JSON/encoding/framing and cross-origin requests before Manager delegation; verify direct/proxy behavior (FR-003, FR-004).
- [ ] T068 [US1] Prove temp actual Manager success/failure safety, nonmutating GET, sanitized storage503 and single-response behavior; document review-first/no-auto-POST-retry operator usage and independently review the incremental contract (T066, FR-007, SC-001).

No real job is acknowledged during verification. Recovery/finalization-only faults,
unknown PID, incomplete transport and disk-full/no-marker recovery remain out of this
route's eligibility; unsafe force clearing/reset is not a fallback.

## Phase 28: Continuation — sibling process/transport isolation

- [x] T069 [US2] Verify result acknowledgment preserves unrelated process-only, registered-handle-only and transport-only blockers for same/different workers and either lease owner; fix only a reproduced unsafe release, with queue/no-capture and persisted-failed-state controls (T066/T068, FR-003, SC-004).
- [x] T070 [US1] Re-read the guarded HTTP/source/framing/proxy contract and record fresh focused/full-stream local evidence; keep native platform/full-store-crash/E2E gates open and preserve all existing work and configuration (FR-004, FR-007, SC-001).

Evidence: a new 12-subcase matrix initially exposed that a registered in-memory process handle was omitted from sibling-job lease scans. The helper now accepts the job id and treats `_running_processes` handles as uncertain in both lease release and same-worker restoration. The sibling process/transport isolation test and the focused MultiWorkerManagerTests + ReconciliationHttpTests suite pass. No live service, device, account or transport was changed.




## Phase 29: Convergence — all-write-failure restart boundary

- [x] T071 [US1] Reproduce the `all-postcapture-writes` crash/storage boundary with a temporary manifest and a second Manager instance; determine whether the persisted pre-capture state safely blocks recapture or can be mistaken for fresh queued work. If unsafe, add the smallest durable marker/ordering repair; if storage is unavailable by definition, retain fail-closed semantics and document the exact unprovable boundary (T060/T064, FR-003, FR-007, SC-004).

Do not simulate disk-full against the live project directory. Use an isolated temporary BASE_DIR and preserve the existing no-recapture/failed-state contract.





## Phase 30: Real-platform acceptance handoff

- [x] T072 [US1] Record a bounded, reversible acceptance matrix for the unavailable Windows-runtime, Android/Facebook, macOS, performance and final evidence gates without starting external services or changing user state.
- [ ] T073 [US1] Execute the matrix only when an authorized real platform/device environment is available; preserve per-job evidence and keep skipped/unavailable gates explicit.


## Phase 31: Actual phase timing (2026-10-03)

- [x] T074 Add passive monotonic timing to existing Windows child runner/Python wrappers and transport cleanup; no argv/identity logging, wait/order/state changes, or timing-output failure propagated. Test success/nonzero/exception/output semantics and run relevant regression. Mac native runner remains a separate measurement surface.
- [x] T075 Run one known small real target with timing logs, preserve completeness/cleanup, and compare recorded phases before proposing optimization. No API restart needed: it launches the runner file per capture.


## Phase 32: Preparation attribution (2026-10-03)

- [x] T076 Measure disjoint runner initialization, device preparation, and network/capture preparation with the existing passive logger. Retain failure semantics and all waits; verify boundaries, AST, focused regression and actual known target. Missing markers on aborted phases remain unmeasured, not zero.


## Phase 33: Explicit Reel video/feedback namespace mapping

- [x] T077 Reproduce rejection of same-Story Relay video ID + equal Story/feedback post ID + coherent actor/owner. Implement only this explicit mapping, reject missing/contradictory/ambiguous proof and wrong expected ID. Keep canonical target wait/fallback unchanged; run analyzer/navigation regression and actual saved response replay.
- [x] T078 Verify known real Reel post remains terminal/source-bound with unchanged waits, record target verification attempts/timing; compare only as observational until repeated controlled evidence.


## Phase 34: Convergence

Assessed 2026-10-03: prerequisites resolved this feature successfully; no extensions.yml hooks registered. Source intent FR-001..FR-008/SC-001..SC-005, plan/runtime/report boundaries and Constitution govern. This is bounded convergence, NOT whole-system PASS or independent review. Recent real evidence supersedes absence claims in earlier dated snapshots, not outstanding platform/storage/performance proof. Existing tasks and historical evidence remain unchanged.

- [x] T079 HIGH Update current requirement-level completion-audit and acceptance summary with exact five-family Reaction jobs, requested Shares visible16/46, real cancel/cleanup and 748-test scope; keep old dated observations as history and distinguish not-requested Shares from failure per SC-001/FR-004 (partial; F1).
- [x] T080 HIGH Obtain an independently attributable bounded review of run_facebook_capture.ps1 timing wrappers and analyze_facebook_capture.py explicit Reel namespace mapping, negative tests, existing API and platform contracts; do not label main-agent reread as independent per plan: Phase3 review/T016/Constitution IV (partial; F2). Delegation requires current authorization.
- [ ] T081 HIGH Classify the recorded 94 platform scanner leads by file/adapter/paired implementation; execute available nativeMac/hostedCI checks only on a provided host/account, preserving live Windows services and documenting unavailable checks per FR-007/T012/T017 (partial; F3).
- [x] T082 MEDIUM Keep explicit namespace correction as correctness evidence, NOT proven E2E speedup. Define a finite same-target before/after acceptance with target/source/terminal/visibility and resource costs for any further timing optimization; defer rather than reduce waits without evidence per FR-005/SC-005/T013-T015 (partial; F4).
- [x] T083 HIGH Review storage reconciliation fault and restart boundaries in isolated temporary fixtures, including persisted versus unavailable diagnostic and sibling blockers; retain unsupported power-loss/native-disk/multiprocess guarantees and never force-clear live lease or inject storage failure into account/device environment per FR-003/FR-004/T042/T044/T064/T068 (partial; F5).

Outcome: tasks_appended,5findings(allpartial;4HIGH/1MEDIUM),0cleancompletionclaim. Existing unchecked tasks remain unresolved where scope exceeds current proof; new tasks trace and consolidate remaining work rather than authorize deployment, restart, account changes or publication.


## Evidence ledger reconciliation — 2026-10-03

Closed only already-proven task scopes (not new implementation): T018 actual SpecKit prerequisite/converge/append-only task result;T053 parent bounded187release/279blob scan with caps/errors/classification, NOT independent T057;T060 persisted/allwritefailed restart and duplicate callbacks in existing tests1174/1285/T071, NOT disk-powerloss guarantee;T067 guarded HTTP route and ReconciliationHttpTests direct/proxy/body/framing no-delegation cases. These tests are included in saved749test full gate; source/tests not changed by checkbox reconciliation. T068/T080 independent review and fullgoal/native/platform/performance evidence stayopen. Unchecked items are mixed implementation/review/evidence aggregates, not count of missing features.


## Phase 35: Scheduler start failure isolation

- [x] T084 Reproduce unstarted Thread.start failure leaving a registered phantom scheduler; roll back only demonstrably unstarted owned thread, preserving original exception and preexisting state. Retain started/uncertain handle to prevent duplicate schedulers; isolated regression and full local gate, no live fault injection (FR-003/FR-004,T010/T011).


## Phase 36: Scheduler error HTTP contract

- [x] T085 Reproduce native scheduler startup RuntimeError escaping operator HTTP routes; distinguish scheduler errors from generic programming errors, return one sanitized503 with inspect-before-retry guidance only around Manager invocation. Preserve errorcause, registrationrollback, existing200/404/409 and response-disconnect semantics; no live fault injection (FR-003/FR-004,T033/T068).


## Windows isolation requirement closure — 2026-10-03

T010/T011 closed for their declared local/isolated state-machine verification scope, not universal fault tolerance: current tests directly assert one scheduler/two-worker standby, checkpoint takeover, non-device errors not causing takeover, stopped/unknown PID semantics, cancellation/transport cleanup and sibling blockers, queue accounting, failure/timeout/restart/all-postcapture-write boundaries, duplicate callbacks and scheduler launch rollback. Original source and test bodies inspected in preceding slices; indexed current cases in capture_api/cancel_transport/shutdown modules included in current755test gate. Real Windows start/capture/cancel/restoration separately recorded.

This does NOT close T012(nativeMac),T016(independentreview),T073(fullplatformmatrix),actual disk/powerloss or all unknown-descendant guarantees. Safe requeue of independently stopped running work remains intended recovery; NOT exactly-once. Two global skips retained, no claim that every possible OS fault was executed. No new tests/capture/sourcechange or service restart solely to alter checkboxes.


## Phase 37: Scheduler liveness observability

- [x] T086 Add backward-compatible scheduler state/alive health snapshot, distinguish HTTPready from dispatcher liveness, represent probe uncertainty without mutating handles/jobs/lease. Regress old health fields and state branches; no automaticrestart or live faults (FR-003/FR-004/T040).


## Phase 38: Artifact physical path boundary

- [x] T087 Reproduce job/batch artifact and metadata following a synthetic child link beyond run root; add minimal read-only confinement preserving regular files/logical names/trusted root relocation. Verify missing/empty/errors and platform parent links, retain hardlink/TOCTOU limitations, no live link tampering (FR-003/FR-006/T009).


## Phase 39: Request-local health ADB batching

- [x] T088 Reduce repeated device-state child processes within health() only, preserve output/fault/timeout contracts and no cross-request stale cache. Validate inventory ambiguity/fallback/context reset and same-condition old/current latency/resource-call comparison; normal API regression/full gate before applying (FR-003/FR-004/FR-005,T013).


T087 closed after reproducing and correcting the remaining batch-progress metadata call site: downloads and file-list availability now share physical artifact confinement. Temporary synthetic link, normal-file and sharing regressions pass; no live link mutation.


T080 closed 2026-10-03: independently attributable bounded static review returned scoped PASS after reproducing/fixing reload scheduler retry P2; current branch hosted CI also passed both Windows/macOS. This does not close native Mac or whole-system review.


## Phase 40: Mac pairing continuation — 2026-10-04

- [x] T089 Verify the current active Mac Reaction argv/parser across five URL families, unlimited/explicit caps, serial/ports/output binding, capability exclusions and failed cleanup; retain no-real-device fixtures and current CI provenance (FR-003/FR-004/FR-007,T012/T081).
- [x] T090 Record a source-grounded Windows/Mac capability and acceptance matrix distinguishing legacy simulation, active runner contracts, hosted CI, and unavailable native runtime. Preserve native Mac/performance/independent whole-review requirements and avoid claiming all-system completion (SC-001/SC-004,T019/T081).

T089/T090 closed 2026-10-04 for their scoped simulated contracts/matrix: focused65/skipped1 and current full776/skipped2, gateexit0 with original log readback. Native Mac, broad performance, whole-system independent review and overall completion remain unclosed.


## Phase 41: Mac Shares + Android PCAP integration — 2026-10-04

- [x] T091 Forward collect/pcap from Mac API; implement safe standalone/combined Share phase, fresh target/source boundary and shared terminal validation; preserve Windows/default Reaction contracts.
- [x] T092 Implement Android-owned PCAP start/stop/pull/hash with timeout/PID/permission/cancellation failure protection; no Mac host sudo or Windows binaries.
- [x] T093 Add deterministic end-to-end analyzer/API fixtures and negative UI/query/source/PCAP/cleanup cases; run relevant regressions/full local gate, paired CI and update documentation/release scope.
- [x] T094 Record unavailable native Mac/Android/Facebook acceptance and review findings; do not replace it with simulation or claim perfect native deployment.

T091/T092/T094 closed for implemented/fixture/local evidence and explicit native limitations only. T093 awaiting current test-branch CI; T081/native acceptance and T019/full goal remain open.

T093 closed 2026-10-04: focused122 tests and current local full gate792/2skip passed; source ZIP190 entries/189 hashes verified; pushed test branch ea522be0b9efa7fe3e5884f52e709d472d4e361b and hosted Windows/macOS CI run37168276769 success. No real platform acceptance implied.


## Phase 42: Correct erroneous review finding — 2026-10-04

- [x] T095 Retract the unsupported standalone Shares defect claim; restore ea522be run_collection semantics with red/green bounded-combined regression and three-revision replay. Keep standalone coverage; do not label this main-executor correction as independent review.
- [x] T096 Verify current local gate/hosted CI and refresh source handoff for165e358; keep native acceptance/whole-goal requirements separate.


Correction hosted CI completed: run37169028745 at commit165e35862d15ffa6914c7c00760072363465ca18 succeeded for both Windows/macOS; gh watch exit0 and final job status readback confirmed. Current local full gate794/2skip and source ZIP094855 are the correction evidence. T096 closed for those checks/handoff only. No independent reviewer/native Mac/real Facebook acceptance is implied; overall maintenance goal remains active.


## Phase 43: PCAP producer lifecycle

- [x] T097 Reproduce and reject premature remote tcpdump exit even with a valid PCAP/header/hash; preserve normal stop, evidence and transport cleanup (T092,FR-003,FR-004).
- [x] T098 Verify corrected adapter and completion status, local gate/current CI, source package and limitations; preserve native Mac acceptance gap.


## PCAP lifecycle CI closeout — 2026-10-04

Current test-branch804be917e4b9b0386e0df9096fd43990ca127f98 run37169519372 completed success on both Windows and macOS. Final gh job readback confirms Windows completed2026-10-04 10:00:36+08:00/macOS10:01:02+08:00; watch exit0. Current local full gate796/2skip and ZIP095810 verified as above. T098 closed only for these declared code/fixture/CI/source-package scopes. Port checks retain proxy7897 PID11904/ADB5037 PID25244, no8765/8766/8081 listener; isolated test clone clean; no main merge/release/device/service changes. Current runner finalization rejects premature tcpdump exit, but packet loss, permission behavior and actual ARM64 Android/Mac runtime remain unaccepted without real-platform evidence. Overall goal/T019 and independent whole-review not completed.


## Phase 44: Delivery and requirement-level reconciliation

- [x] T099 Compare release189 local/ZIP/test-branch artifacts using actual LF export policy; sync five dated feature records and refresh misleading top-level audit summary, preserving native/performance/independent-review gaps.

T099 source consistency and requirement matrix reconciled2026-10-04; five docs synchronized to isolated test clone for a documentation-only commit. Production scope unchanged; shipping ZIP verification remains a distinct artifact readback.


## Phase 45: Mac API/runner output ownership

- [x] T100 Reproduce API-created capture.log rejected by active Mac runner; repair explicit inherited-stdout ownership handoff without permitting arbitrary existing output.
- [x] T101 Verify actual API subprocess/log/status flow with mocked device work, negative directory/log cases, local gate/current hosted CI/source package; native business acceptance remains unproven.


## Mac API log handoff final verification — 2026-10-04

Current local gate800 tests/123.472s/OK skipped3/exit0, all original command streams and report read back at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-api-log-02/. Production API/runner fix c676363 plus timeout-fixture correction c7ba7d4af1366ae96288fdd4439ac85fff426498 passed both hosted Windows/macOS CI run37171037469 (Windows completed2026-10-04 10:29:29+08:00; Mac10:30:36+08:00). Test timeout cleanup failure from earlier run retained and no blanket retry/skip/production cleanup expansion used. New file-link guard test unavailable on local Windows but executed on macOS CI; three local skips not native business acceptance.

T100/T101 closed for actual API->Popen->stdout/runner/status fixture and declared local/CI/source handoff scope, not all-platform/capture success. These changes fix the default Mac Reaction entry too, not only new Shares. No real device/account/Frida/CA/bootstrap/version/proxy changes, main merge or release. Existing Windows command path is unchanged; gate production code unchanged. User-facing same API payload remains, internal --api-log is not a JSON API parameter. Whole independent review, broad performance matrix and native Mac business acceptance remain incomplete.
