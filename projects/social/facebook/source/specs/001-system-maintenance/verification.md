# Verification matrix — maintenance goal remains active

Date: 2026-10-02. This report concerns the current maintenance slice, not full acceptance.

## New evidence

- Executed artifact renderer using a deterministic Node VM / dummy DOM. HTML-like metadata
  is handled as literal text; preview and download routes preserve their prior values.
  This does not establish browser/platform exploitability or real UI acceptance.
- Maintenance gate regressions first failed (8 errors for missing enforcement helpers),
  then passed after implementation. Additional project-interpreter selection checks cover
  Windows/POSIX venv path conventions with temporary dummy files, not native Mac execution.
- Gate now reports partial (exit 2) when tools/tests are skipped, failed (exit 1) on errors,
  and passed (exit 0) only when its selected local gates pass. It does not silently omit
  missing mandatory files. Python syntax checks compile in memory without pycache writes.
- Release-path validation checks missing/duplicate/escaping/symlink/private runtime paths.
  This is path hygiene, not Git-history or file-content secret scanning.
- Global Python pip check exposed unrelated installed-tool dependency conflicts. No global
  packages were changed. Project .venv pip check returned exit 0 with no broken requirements.
  Project Python is 3.13.12; mitmproxy 12.2.3, frida 17.17.0, frida-tools 14.10.4,
  requests 2.32.5 currently match requirements.txt.
- Previous UI slice regression: 668 tests, OK (skipped=2), 105.090 seconds. That predates
  the new gate tests; it is not final acceptance for newer changes.

## Evidence boundaries and next requirements

| Requirement | Current proof | Remaining gate |
|---|---|---|
| Correct target/source/terminal/visibility | Existing local regressions executed in earlier pass | Current source audit and real five-URL acceptance |
| Single worker/standby/lease/cancellation | Extensive dummy regression cases exist and passed earlier | Trace every material state path and native device cleanup |
| API/private-tunnel compatibility | Local fake-manager tests | Actual authorized forwarding deployment |
| Platform pairing | Heuristic scan: 94 candidates, 78 files | Classify material candidates; native Mac and Android acceptance |
| Performance | No same-condition E2E benchmark yet | Establish baseline before touching waits/algorithms |
| Security | Narrow boundary and DOM repairs, local tests | Independent review and broader artifact/capture/CSV/content audit |
| Engineering gate | Negative-case regression and local execution | Final current-worktree full gate result below |

## Preservation and rollback

No production service, device, login, proxy, Frida or certificate setting is changed in
this slice. Current port snapshot: project API/UI are not listening; ADB and proxy retain
existing process ownership. No commit/push/release occurred.
Backups for pre-slice gate/tests/docs/manifests/AGENTS are in the timestamped temporary
facebook-maintenance-next directory printed by the tool. Restore only changed files after
checking for later edits; never reset the entire dirty checkout. Existing web_v2 edits are
preserved; DOM renderer changes were made in the previous atomic step of this run.

## Final current-slice gate

`python -B tools/maintenance_gate.py --json` selected the project's
`.venv/Scripts/python.exe` for pip check and unittest. It returned status=passed,
ok=true and exit code 0. Unittest ran 678 tests in 101.289 seconds: OK (skipped=2).
This proves the enumerated local gates only; real Windows capture, native macOS,
Android/Facebook, independent review and E2E performance remain incomplete.

The gate's shared syntax checks run without importing project business modules. A
missing tool or --skip-tests reports partial/exit 2; temporary-file tests verify
this distinction. The project venv dependency result does not repair or accept the
unrelated global Python dependency conflicts.

## Runtime slice — multi-manifest cleanup ownership (2026-10-02)

Current source review mapped restart manifest loading, cancel(), cleanup completion,
lease release, worker restoration and job assignment. The release scan considered
only other jobs' uncertain processes; stopped processes with unresolved transport
were omitted. Worker restoration had the same omission for jobs on the same worker.

Reproduction: two temporary failed/cancelled manifests, both stopped and no PID,
both with transport cleanup failed. Cleaning the last-loaded record incorrectly
cleared the global lease. The initial regression failed in four subcases. After
fixing the two predicates, eight same/different-worker and cleanup-order subcases
prove dispatch remains blocked after the first cleanup and becomes claimable only
after the second. All cleanup operations are mocked; no real ADB mutation occurs.

Changes are limited to facebook_capture_api.py lease-release/worker-restoration
predicates and diagnostic reason text, plus a test in tests/test_facebook_capture_api.py.
No API fields, waiting constants, scheduler concurrency, dependency versions or
capture algorithms changed. Backup and bounded 92-line slice diff are under
C:/Users/lenovo/AppData/Local/Temp/facebook-lease-backup-20261002-170327/.

Evidence:
- Focused MultiWorkerManagerTests + CancelTransportCleanupTests: 50 tests, OK.
- Project-venv maintenance gate: status=passed, exit 0; 679 tests in 101.087s,
  OK (skipped=2). Summary retained at the temporary facebook-maintenance-lease-current.json.
- git diff --check: exit 0. Test file's original LF encoding is preserved.
- Only-read runtime snapshot: API/UI not listening; existing ADB and proxy retain
  ownership; adb devices -l contains no attached device.

Acceptance remains partial: native Android proxy restoration and Facebook capture
cannot be established without a device, and independent review remains open.
T010/T011 overall state-path audit, T012 platform classification and T013–T019
performance/real-platform/review gates remain incomplete. Whole goal stays active.

## Runtime slice — atomic Worker configuration (2026-10-02)

Two reproducible defects were found in the live configuration boundary:
1. set_worker_enabled changed self.workers before the atomic file write; a locked/
   unwritable config left memory altered while disk and dispatch markers stayed old.
2. reload_workers did not apply set_worker_enabled's busy-active-worker disable guard.
   It could disable/switch the active worker and load extra runtime entries while a
   running/cancelling job still belonged to the prior worker.

Four negative temporary-registry cases failed before the repair: disabling idle primary
and enabling standby under PermissionError, plus running/cancelling active-worker
reloads. Persistence now precedes live-state publication. Reload validates the busy
worker guard before any configured dictionary/runtime/order change.

Success controls verify the candidate is not published before the writer commits,
idle switching still works, and busy non-disabling metadata updates remain accepted.
Rejected reload leaves externally edited disk untouched (no operator-file rollback).
No real worker registry, login/device configuration or global environment was changed.

Evidence:
- MultiWorkerManagerTests + CancelTransportCleanupTests: 54 tests, OK, 0.928s.
- Project-venv full maintenance gate: status=passed, ok=true, exit 0; 683 tests in
  98.598s, OK (skipped=2). Temporary summary: facebook-maintenance-worker-config-current.json.
- Bounded 140-line pre-slice diff and exact file hash snapshot retained under
  C:/Users/lenovo/AppData/Local/Temp/facebook-worker-config-backup-20261002-171648/.
- Tests preserve pre-slice line endings. git diff --check succeeds.

Remaining review: reload currently reads disk before acquiring the manager lock.
Source suggests a stale read could race a concurrent setter; this needs a deterministic
interleaving test before claiming the whole configuration path transactional.
Unexpected persistence errors at the HTTP boundary also require separate review.
Independent review and native device/platform/E2E performance gates remain open.

## Runtime slice — deterministic reload/setter race (2026-10-02)

The stale-read lead from the previous slice is now reproduced and repaired.
Before the change, reload read the old registry outside the manager lock; an
interleaved setter wrote the new enabled state to disk and memory; reload then
published the old snapshot over memory. The initial fixture failed because disk
showed standby enabled while the live configuration still showed it disabled.

The fixture uses Event-controlled reads and a nonblocking lock probe to enforce
the relevant ordering, not sleeps or repeated timing trials. Both enable and
disable directions now assert disk/live equality after the threads terminate.
Scheduler threads remain mocked, the registry is temporary, and adb_snapshot is
mocked. No real worker/device/registry or proxy is modified.

Repair: move reload's read and configured mapping inside the existing lock that
also protects setter persistence and live-state commit. Validation/identity/busy
worker guards and output field schema remain intact; there is no additional lock,
worker concurrency, waiting constant or dependency change.

Evidence:
- Initial deterministic enable fixture failed on disk/live configuration equality.
- Both-direction race fixture passed; combined worker/cancel module focus: 55 tests, OK.
- Project-venv maintenance gate: status=passed, ok=true, exit 0; 684 tests in
  100.840 seconds, OK (skipped=2).
- git diff --check passed. Original encoding/newline style preserved.
- Backup, bounded 103-line slice diff and current hash snapshot at
  C:/Users/lenovo/AppData/Local/Temp/facebook-worker-race-backup-20261002-173257/.
- Real facebook_workers.json and facebook.local.json retain their previous write dates.

This lock guarantees serialization for this manager's API operations, not external
file editors or separate API processes. Lock-duration measurements, HTTP disk-error
reporting, independent review, native Mac/device and E2E performance remain open;
T029/T031 are not closed by this local regression. Whole goal remains active.

## Runtime slice — Worker HTTP I/O failures (2026-10-02)

Two API entry paths dropped the response on configuration OSError: enable/disable
persistence and reload reads. Direct dummy clients saw RemoteDisconnected; through
v2 the same fault was incorrectly reported as backend unreachable. Four negative
subcases (read/write x direct/proxy) failed before the scoped HTTP repair.

Worker management now returns sanitized JSON 503 for manager I/O failures, preserving
normal 200, unknown-worker 404 and ApiError conflict 409 handling. The response schema
uses the existing error field; raw exception strings/local paths are not returned.
Success response transmission is in the else branch, outside the manager I/O catch,
so a closed client does not trigger a second/mislabeled configuration response.

Tests construct a JobManager with scheduler start mocked, temporary registry, mocked
ADB snapshot and ephemeral loopback servers. They verify live/disk state preservation
for read/write failure, no extra scheduler startup, sanitized responses through both
API and proxy, and health 200 after failure. A direct handler control checks that
BrokenPipeError on sending a successful reply is propagated after one reply attempt.
All dummy server threads terminate; no real configuration or device state is changed.

Evidence:
- Before repair: two RemoteDisconnected errors and two proxy-message assertion failures.
- Focused Worker HTTP + scheduling/cancellation/request-boundary regression: 68 tests, OK.
- Project-venv maintenance gate: passed, ok=true, exit 0; 687 tests in 100.901s,
  OK (skipped=2). Temporary summary: facebook-maintenance-worker-http-current.json.
- git diff --check passed; original encoding/newline style preserved.
- Backup, 152-line slice diff and hashes retained at
  C:/Users/lenovo/AppData/Local/Temp/facebook-worker-http-backup-20261002-174358/.

Scope limitation: the generic I/O response does not claim any operation was rolled back;
state preservation is specifically proven for failing config reads/atomic writes. Job/
batch manifest I/O, lock-duration measurement and independent review remain separate
open work. No real Android/Facebook or native Mac acceptance is established by the fixture.

## Submission slice — persist/commit before dispatch (2026-10-02)

Manifest failure review reproduced queued ghosts in memory after single-job writes
failed. Batch output/child/parent failures exposed partially published records. A
queued child manifest without a valid parent could also be requeued on restart even
though the batch submission had not committed.

Repair:
- Single job: persist before publishing the in-memory queued record.
- Batch: prepare derived outputs, persist children and commit the existing version-1
  parent manifest before publishing memory and enqueueing. No schema migration.
- Startup queued-child recovery: require a matching version-1 parent and job_id
  membership. Uncommitted children retain failed diagnostic records, not dispatch.
  Existing process/cleanup uncertainty and capture lease protection remain intact.
- Submission HTTP: sanitized local I/O 503, unchanged valid 202/invalid 400; actual
  request-body timeout remains 408. Storage TimeoutError is not mislabeled as body
  timeout. Response sends are outside the operation catch, preventing double replies.
- V2's explicit Chinese parallel-capture wording now explains single active Worker
  serial execution and standby takeover. Selectors, branding and behavior remain intact.

Evidence:
- Initial persistence/wording fixtures failed in ten subcases; initial HTTP submission
  fixtures showed dropped responses/proxy misdiagnosis; storage timeout controls also
  failed on 408-versus-503 classification before the read/submit error scopes were split.
- Four batch failure points (outputs, first/second child, parent commit) keep memory and
  queue clear; restart does not auto-run staged children. Positive committed-v1 recovery
  still queues all children; invalid version/id/membership/missing-parent cases are blocked.
- Unknown process evidence on an uncommitted child still blocks the lease; no unknown
  PID is killed. Temporary registries and fake scheduler/ADB isolate the tests.
- API/proxy checks cover jobs/batches x ordinary I/O/storage timeout; subsequent health
  succeeds and private dummy exception details are not exposed. Body-timeout 408 and
  successful reply/disconnect controls preserve classification and single response.
- UI checks verify truthful wording and preserved input selectors/SWARM section marker.
  This is source/fixture evidence, not a visual browser or native-device acceptance.
- Combined relevant HTTP/worker/cancel/UI focus: 87 tests, OK, 12.366s.
- Project-venv full maintenance gate: passed, ok=true, exit 0; 695 tests in 112.786s,
  OK (skipped=2). Temporary summary: facebook-maintenance-submission-current.json.

Backups and the exact bounded slice diff/hash snapshot are under
C:/Users/lenovo/AppData/Local/Temp/facebook-submission-backup-20261002-180519/.
No original run/data/configuration is deleted. Staged failure output remains on disk
for diagnosis. Original file encoding/newline style is preserved.

Scope: this is the current manager's submission/queued-restart path, not a distributed
transaction or all lifecycle writes. Assignment/finalization I/O, independent review,
native platform/device/Facebook acceptance and E2E performance remain open. No stable
wait, source/completeness/visibility/terminal guard or scheduler parallelism changed.

## Dispatch slice — ownership persistence before capture (2026-10-02)

Before repair, _assign_job_to_worker mutated queued job/attempt/active ownership
before its running manifest write. A storage fault left memory running but disk
queued. The worker loop treated this pre-capture fault as a runner/device failure.
Two regression fixtures reproduced mismatched live/disk state and queue ownership.

Assignment now builds a private candidate and persists it before publishing fields
and active ownership. The worker catches only assignment-stage OSError, retains
queued work and waits on the existing event for 0.5s before another dispatch attempt.
No runner, cleanup, final-process callback, batch refresh or device-failure recovery
runs for a dispatch that has not committed. Queue task accounting remains balanced.
This pause does not change navigation/capture waiting constants or retry POST.

Evidence:
- Two negative regressions failed before the repair; both now pass.
- A transient-write success control proves failed dispatch then successful persistence
  yields exactly one capture call/one attempt, completed fixture result, no device recovery,
  empty active ownership and zero unfinished queue tasks.
- Worker/cancellation focus: 63 tests, OK, 1.066s.
- Project-venv full maintenance gate: passed, ok=true, exit 0; 698 tests in
  115.763 seconds, OK (skipped=2). Summary: temporary facebook-maintenance-dispatch-current.json.
- Bounded 189-line slice diff and file hashes backed up under
  C:/Users/lenovo/AppData/Local/Temp/facebook-dispatch-backup-20261002-183857/.
- git diff --check and current syntax gates passed; original test/source encoding retained.

All registries/jobs were temporary, scheduler start mocked and capture a dummy callable.
Real operator registry/config write dates remain unchanged. Existing device/process/
proxy/lease protections, API field schema, dependencies and single Worker semantics
remain intact. No real capture, service restart, state cleanup or publication occurred.

Open: prolonged dispatch storage faults are currently retained/retried but need operator
observability assessment; completed/failed/final callback writes and independent review
remain T038/T040 work. Real platform/device/E2E performance acceptance is not established
by these fixtures. The full objective remains active, not completed.

## Finalization slice — storage fault after capture (2026-10-02)

The last callback in _worker_loop originally ran before active-job clearing and
queue.task_done without a persistence-failure boundary. A final callback OSError
escaped and skipped local scheduler accounting. The initial dummy fixture failed
with OSError instead of entering the safe blocked wait state.

Repair: catch only finalization-stage OSError, retain an uncertain global lease
and the worker orphan marker with a sanitized reason, then complete the existing
local active-job/queue accounting. The next queued job is retained but cannot claim
a worker. No metadata error is converted into device failure or transport success.

Both cases are tested: a direct final callback failure, and the actual callback's
internal _persist_job failure after it begins clearing process metadata. A second
queued job stays queued, queue.unfinished_tasks remains one, no second capture
occurs, and dispatch is blocked. The test exits through a controlled KeyboardInterrupt
at the next blocked wait; it does not run the real scheduler thread or capture.

- Relevant Worker/cancellation focus: 64 tests, OK, 1.076 seconds.
- Pre-change regression: OSError escaped before queue accounting.
- Bounded 81-line slice diff and current hashes retained with backup at
  C:/Users/lenovo/AppData/Local/Temp/facebook-finalization-backup-20261002-185334/.
- Original source/test encoding preserved; git diff --check passes.
- API/UI default ports remain not listening; existing ADB/proxy process ownership
  is unchanged. Operator registry/config remains untouched.

Limitations: the block records unresolved durability in the running manager; it is
not proof that failed disk writes became durable. Operator recovery, crash/restart
at every finalization instruction, normal completion-path I/O and independent review
remain open. These fixtures do not establish native Android/Mac/Facebook acceptance,
performance improvement or whole-system completion.

Final current-slice project-venv maintenance gate: status=passed, ok=true, exit0; 699 tests in 114.539 seconds, OK(skipped=2). Summary in temporary facebook-maintenance-finalization-current.json. This is the local gate only, not real-platform or whole-goal completion.

## Recovery slice — secondary persistence fault (2026-10-02)

Failure handling originally allowed a new OSError from _recover_after_worker_failure,
checkpoint or failed-job manifest writing to escape the worker loop. Three red fixture
subcases reproduced this after a dummy capture ApiError. The ordinary final callback
could clear stopped-process ownership despite the failed recovery metadata write.

Repair is scoped to recovery-stage OSError: record in-loop storage uncertainty, cancel
failover requeue, run existing final bookkeeping, then retain global lease blocking and
worker orphan state even when the stopped-process callback completes. No success/durable
write/transport-restored claim is inferred. Next queued work is preserved but unclaimable.

Evidence:
- Before repair: recovery/checkpoint/failed-manifest subcases all escaped with OSError.
- After repair: all three reach the controlled blocked wait; one capture call, no next
  dispatch, zero active-job/local Worker ownership and balanced remaining queue task.
- Worker/cancel focus: 65 tests, OK, 1.974 seconds.
- Raw dummy storage path is not exposed through the global lease reason.
- Backup/current hashes and bounded 126-line slice diff retained under
  C:/Users/lenovo/AppData/Local/Temp/facebook-recovery-io-backup-20261002-185922/.
- Original encoding/newlines retained; git diff --check passes.
- Project Worker/local registry write dates and ADB/proxy listener ownership remain unchanged;
  no production capture, device mutation, user-state cleanup, commit or publication occurred.

Limitations: retained in-memory blocking is not recovery of the failed disk write. Operator
recovery and restart after every instruction need independent review; normal result-commit
I/O and full native/Mac/device/performance acceptance remain open. No broad non-I/O
exception suppression or automatic capture/POST replay is introduced. Goal remains active.

Final current-slice project-venv maintenance gate passed: status=passed, ok=true, exit0; 700 tests in 109.408s, OK(skipped=2). Temporary summary: facebook-maintenance-recovery-io-current.json. This validates local gates only, not whole-goal completion.

## Local performance slice — provenance field schema (2026-10-02)

A measured local optimization replaces ordered-list membership scans in
_reactor_fieldnames with a membership set, retaining the existing list for header order.
No global caching or stale schema state is introduced; each call builds its own set.

Protocol: Windows 11, project Python 3.13.12. Identical 10,000-row dummy fixtures with
15 or 60 additional provenance fields; six repetitions each, alternating legacy/new
order, GC before each timing, perf_counter wall time. The baseline is the exact old
algorithm; post-change timings invoke the actual edited function. Outputs must equal
in order and values on every run. tracemalloc peaks measure function allocation only,
excluding the preconstructed fixture and not total process RSS. No real user data/network.

Post-change paired medians and traced peak allocations:
| Dummy workload | Legacy median | Current median | Legacy peak | Current peak |
|---|---:|---:|---:|---:|
| 10k rows + 15 fields | 26.046 ms | 7.704 ms | 336 B | 3,112 B |
| 10k rows + 60 fields | 245.745 ms | 28.772 ms | 720 B | 3,112 B |

Before editing, the paired prototype experiment also showed consistent improvement
(25.358 vs 7.534 ms; 233.214 vs 25.623 ms). Full timing arrays, script and raw benchmark
JSON are preserved alongside the source backup under
C:/Users/lenovo/AppData/Local/Temp/facebook-local-perf-backup-20261002-190735/.
The bounded source/test slice diff is 61 lines; application change is only three lines.

Regression verifies duplicate/empty/nonstring normalized field keys preserve legacy
ordering and actual temporary CSV headers/provenance values are unchanged. Two schema
checks plus relevant worker/cancel suite: 67 tests, OK, 1.102 seconds.

Tradeoff: about 2–3 KiB extra function-local set allocation in these fixtures; it scales
with unique fields, not rows. These are function-only dummy metrics, not proof that
schema work is the dominant E2E bottleneck or that Facebook/device capture is faster.
T013–T015 E2E measurement, independent review and real platforms remain open. Stable
waits, paging, source/completeness/visibility/terminal and post-user dedupe are untouched.
Existing real registry/local config timestamps and ADB/proxy ownership are unchanged;
no service restart, device mutation, data deletion, commit or publication occurred.

Final current-slice project-venv maintenance gate: passed/oktrue/exit0; 702 tests in 108.664s, OK(skipped=2). Temporary summary facebook-maintenance-local-perf-current.json. This is local regression only, not E2E speed or whole-goal acceptance.

## Batch performance slice — ordered export schema (2026-10-02)

Both batch CSV output paths used linear membership over their ordered field lists
for every exported row. The measured edit adds per-call membership sets while leaving
the ordered lists, fixed base fields, row sorting and post-scoped identities intact.

Protocol: project Python 3.13.12, Windows; five dummy jobs x 1,000 rows/job, either 15
or 60 additional provenance fields. Six alternated legacy/new repetitions on the same
fixtures and fixed utc timestamp, GC before timings. Actual write_batch_outputs runs
with captured writers and mocked source readers. This measures aggregation/schema,
not disk/network/device latency. Whole export metadata/rows/headers are SHA-256 compared
at each invocation, outside the timed portion. The expected export hashes match both
the prechange prototype experiment and postchange actual function experiment.

Postchange paired medians:
| Dummy workload | Legacy ms | Current ms | Legacy traced peak | Current traced peak |
|---|---:|---:|---:|---:|
| 5 x 1k rows, 15 fields | 65.407 | 42.608 | 10,021,484 B | 10,024,348 B |
| 5 x 1k rows, 60 fields | 335.085 | 104.031 | 18,661,484 B | 18,664,348 B |

tracemalloc covers aggregate call allocations and excludes preconstructed fixture
storage. Added peak is 2,864 bytes here; no row cache or long-lived global set exists.
Prechange prototype paired medians were 65.957/41.385 ms and 343.998/103.603 ms.
These figures do not establish E2E improvement or the main production bottleneck.

A real temporary CSV regression verifies exact legacy headers (derived from sorted
rows, not raw input order), base field order, appended provenance, shared user records
retained across different jobs, same-post duplicate accounting and post counts. The
initial test expectation was corrected against the unchanged baseline before editing
production code; no production behavior was changed to make that assertion pass.
Relevant schema/worker/cancel focus: 68 tests, OK, 1.130s.

Backup, 2 benchmark scripts, pre/post raw timing JSON, bounded diff and current hashes:
C:/Users/lenovo/AppData/Local/Temp/facebook-batch-perf-backup-20261002-191630/.
Source/test encoding is preserved. No actual user records/network/device were used;
registry/config/data and service ownership are untouched; no commit/push/publish.

This closes only the measured local schema slice. Independent review, actual I/O/E2E
baseline, native Mac/device/Facebook acceptance and the broader runtime/storage audit
remain open. Whole objective is active, not complete.

Final current-slice project-venv maintenance gate: statuspassed/oktrue/exit0; 703 tests in 108.839s, OK(skipped=2). Temporary summary facebook-maintenance-batch-perf-current.json. Whole local output equality/regression remains distinct from native/independent/E2E acceptance.

## Independent review / coverage slice — 2026-10-02

Fresh read-only reviewer found no normal-string field ordering regression but initially
reported provenance/assertion gaps. Actual tests now pin base fields/input content,
all batch provenance values, different source/reaction per-job identity, and real
checkpoint merging/CSV roundtrip stable bytes. Follow-up independently returned static
PASS for the specified gap closure; whole-system/native/E2E acceptance remains excluded.
Details: independent-export-review.md. Parent-executed focus: 21 tests, OK.

CI coverage guard first failed on both Windows/macOS for omitted maintenance/frontend
modules. Existing platform HTTP-regression commands now include both modules; layout
regression verifies both lists. Hosted CI is not run/published; local source wiring
is not a remotely green CI claim. Backup at temporary facebook-ci-coverage-backup-20261002-192723.
Additional test/evidence backups at temporary facebook-independent-review-backup-20261002-192934
and facebook-acceptance-audit-backup-20261002-192539.

Current project-venv maintenance gate: passed/oktrue/exit0; 706 tests in 108.521s,
OK(skipped=2); temporary summary facebook-maintenance-review-ci-current.json.
Raw gate stdout/stderr remains tail-limited, so full-log retention is still a gap.

Requirement-level completion-audit.md retains the original whole-system objective,
explicitly identifies missing normal/failure/native/private-tunnel/performance/secret
review proof and prevents interpreting local task ticks as overall achievement.
Current readonly ADB lists no device; API/UI absent, existing proxy/ADB ownership unchanged.
Code/fixture review can still progress; goal is active, not complete or fully blocked.

## Full-output evidence slice — 2026-10-02

Previously run() kept only 4000-character stream tails, lost timeout partial output,
and used in-memory capture even when full evidence was needed. Optional --log-dir
now reserves a new external CLI run directory and writes each command's stdout/stderr
directly as original bytes in exclusive files; JSON retains bounded decoded tails.
Default behavior remains tail-only and explicitly full_output_retained=false.

New result semantics track command_started; a launch failure's empty files never
prove execution or full output. log/open/read/report failure is failed even if the
command returned zero. Report writing is staged, flushed/fsynced/closed then atomically
published by exclusive hard link. Unsupported filesystems fail instead of unsafe
fallback; previous reports/intermediate collisions are never overwritten or removed.
Only the helper-owned temporary report is eligible for best-effort cleanup.

Evidence: initial five regression errors then working capture; expanded focused
maintenance-log tests: 26 tests, OK, 1.466s. Tests use temporary directories/dummy
Python commands only, covering normal truncation, original invalid bytes, 25k/22k
streams, nonzero/launch/timeout/second-open/read/report write/sync/link/close faults,
CLI no-overwrite and entire collection command routing. Independent follow-up static
PASS for specified gaps; see independent-log-review.md for remaining limitations.

Intermediate full gate ran 714 tests and passed, but predates the final expanded
controls/report publication edit; it is not final post-change acceptance.
Log implementation/previous test/docs backup at temporary facebook-check-log-backup-20261002-193912.
No user logs/data are deleted; new test/check evidence remains outside source/release.
Existing operator registry/config timestamps and ADB/proxy ownership remain unchanged.

Scope boundaries: CLI directory validation is not protection against adversarial concurrent
path substitution; mode0700 is not Windows ACL; logs may contain private local diagnostics.
This is an engineering check runner, not the security-audit OS sandbox. Timeout confirms
only direct command handling, not arbitrary descendant cleanup. Atomic publication is
not a power-loss filesystem durability guarantee. Native macOS/Android/Facebook, leakage
content/history, recovery and E2E performance remain open; full goal stays active.

Final post-change full gate: 723 tests in 110.256s, OK(skipped=2), passed/oktrue/exit0.
Report and original command stream files verified at
C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-full-log-final-194926/.
Parent read back report.json, checked every command started/retained stream path and
actual file, confirmed the full unittest stderr contains its exact count/OK result,
and generated stream-integrity.json with byte sizes/SHA256 without exposing raw logs.
No uncompleted report intermediate remains in this run. This verifies current Windows
filesystem publication/log retention only, not power-loss durability/native Mac/privacy.
Historical tail-only checks are not retroactively turned into full original records.

## Bounded release/history audit — 2026-10-02

182 release text files and all 279 blobs among 375 locally reachable Git objects
were inspected with six credential signature classes, capped at 5MiB/file and 2000
objects. No skips/decode/read errors. Input sizes/hash, HEAD/status/manifest hash,
local ref and object metadata retained outside project in run-20261002-release-history-195620.
13 matches (5 distinct fingerprints) were traced to fake-flow, certificate-rejection
fixtures or documented short-lived token placeholder. No live validation or values
in redacted reports; parent classification only. Independent reviewer did not read
candidate source due output-safety/tool limitation; no independent PASS is claimed.
See content-history-audit.md for exact scope and signature/history exclusions.

6 local fixture tests pass. No source/credential/history/data was edited or deleted,
no remote fetch/auth request and no new overlapping scanner skill installed.
This closes a bounded snapshot only, not exhaustive secrets or whole-system security.
Original code gate remains 723 tests; task/docs changes after this scan are not covered
retroactively by the snapshot. Native/platform/recovery/E2E/independent gates stay open.

## Result commit slice — no uncommitted completed publication (2026-10-02)

Red fixtures showed completed state could be observed before its checkpoint/manifest
commit and post-return local I/O fault entered device recovery. First red fixture was
unbounded when the old path immediately dispatched onward; only its two positively
identified own Python test processes were stopped. The fixture then used one bounded
queue.get and blocked-wait exit injection, producing four explicit negative failures.

Repair: stage completed fields/checkpoint in a private candidate, persist, then update
live job. A capture-returned marker limits the new local-I/O branch to post-return
merge/build/checkpoint/commit failures; no recover/device cleanup/failover is invoked.
A sanitized failed diagnostic is persisted before publication if possible, otherwise
uncommitted completion stays private. Existing finalization reasserts the uncertainty
block after stopped callbacks, preserving pending queue and orphan Worker protection.

Final fixtures cover merge, build, checkpoint, completed manifest and all postcapture
writes failing, with reentrant writer-time public status/result checks. Positive
control confirms successful completed state/result/disk manifest publication after
the first write succeeds, plus normal queue release. Existing normal failure and
cancel logic remains independently covered by prior tests; no waiting/parallelism
or source/visibility/terminal/completeness guard is weakened.

Evidence:
- Initial red reproduction: four failed scenarios, no false-green test relaxation.
- Worker/cancel focus after build and success controls: 67 tests, OK, 1.167s.
- Full project-venv gate with original streams: passed/oktrue/exit0; 725 tests in
  110.702s, OK(skipped=2). Private evidence at
  C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-result-commit-201039/.
- git diff --check and current syntax checks pass. Operator config/registry write dates
  and existing ADB/proxy ownership unchanged; no real capture or device operation.
- Exact bounded 254-line slice diff/hash and pre-edit files at
  C:/Users/lenovo/AppData/Local/Temp/facebook-result-commit-backup-20261002-200546/.
- Fresh read-only reviewer independently reread changed source and affirmed the local
  candidate/returned-I/O/finalization ordering corrections, with remaining durability
  and late-callback gaps. See independent-result-review.md; no all-system PASS.

Remaining: after all failed writes restart may still see the earlier running manifest
and requeue; an in-memory result-storage block is not a persistent reason-lock, and
late duplicate callbacks/operator recovery need explicit fixtures. Pre-return local
I/O/returned non-OSError, actual output-directory durability, real platform/Facebook
acceptance and E2E performance remain open. Overall objective stays active.

## Late callback slice — distinct in-memory persistence reasons (2026-10-02)

Red fixtures for result/recovery/finalization storage faults reproduced that a later
stopped callback cleared the global lease uncertainty. Introduced a per-job persistence
reason registry in this Manager; only finalization/result/recovery I/O boundaries register
these reasons. Existing non-storage stop/transport callbacks keep their prior defaults.

Release cannot erase its own persistence reason. A different clean owner finishing
transport retains another job's stored reason. Worker restoration cannot discard a
storage-blocked job's orphan or a same-Worker sibling persistence blocker. No automatic
reason removal or acknowledgment endpoint is added; stopped/transport proof is not
output persistence confirmation. Standby switch still cannot bypass the global block.

Evidence:
- All three delayed callback red subcases failed before the repair.
- Corrected fixtures execute two delayed final callbacks per fault class and explicit
  Worker restoration attempts, then switch disabled primary to standby and verify no claim.
- A separate clean-owner fixture preserves another job's reason and same-Worker orphan.
- Worker/cancel focus: 69 tests, OK, 1.207 seconds.
- Full project-venv gate with original output: passed/oktrue/exit0; 727 tests in 112.040s,
  OK(skipped=2). Full evidence at
  C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-late-callback-201925/.
- Parent read back the report and all 16 command streams, checked paths/retention/status,
  verified the original unittest result count and generated stream-integrity.json hashes.
- Exact bounded 176-line slice diff/source hashes and backups at
  C:/Users/lenovo/AppData/Local/Temp/facebook-late-callback-backup-20261002-201653/.
- Original source/test newline/encoding preserved; git diff --check passes.
- Operator registry/local config write timestamps and existing ADB/proxy ownership unchanged;
  no real device/capture operations, resets, cleanup, commit or publication occurred.

Scope: registry is memory only. A disk-full crash/restart still requires distinct durable
marker/old-manifest reconciliation and operator-acknowledgment design/tests. Holding the
block is fail-closed, not a complete recovery UX or an exactly-once proof. Current default
API/UI absent; no native Mac/device/Facebook acceptance/E2E speed is inferred.

Independent limited late-callback review confirmed local registry/release/restore coupling and non-storage path preservation by source, not independent test execution. See independent-late-callback-review.md. Reviewer specifically leaves restart reconstruction and explicit operator acknowledgment open; T060/T062 not closed by focus/full local evidence.

## Restart slice — existing result-storage marker reconstruction (2026-10-02)

Four red temporary-manifest cases showed a new Manager forgot the distinct result
storage blocker when failed-result-persistence was already saved, regardless of
None/restored/runner-owned/failed transport markers. Loader now reconstructs that
known persisted reason before scheduling, using the existing version-1 recovery_state
rather than a manifest schema migration or a new endpoint.

Additional red case: unknown process identity replaced the storage recovery_state
when persisting restart diagnostics. The narrow process-evidence branch now retains
failed-result-persistence so a second reconstruction keeps both storage and process
uncertainty. Unknown PID termination is never attempted in this fixture.

Positive controls: ordinary failed, checkpointed failure and completed diagnostics
still initialize an available Worker with no storage registry reason; fresh queued
work is claimable. Real postcapture error-path fixtures (where the failed diagnostic
write did succeed) are now rebuilt as a second Manager and remain blocked. Both
Manager startups, delayed release/restore and standby claims are tested against
stopped-process/transport variants with mocked scheduler, ADB and process operations.

- First red marker test failed in four cases; unknown-PID marker preservation failed
  separately before the second narrow correction.
- Final focused Worker/cancel regression: 72 tests, OK, 1.257 seconds.
- Intermediate full gate 729 tests/2 skipped passed, but predates the unknown-PID
  test/correction and is not final current-slice acceptance.
- Backup/slice diff/hash under
  C:/Users/lenovo/AppData/Local/Temp/facebook-restart-storage-backup-20261002-202930/.
- Original file encoding/newline preserved; git diff --check passes. Real registry
  and local configuration timestamps and ADB/proxy ownership are unchanged.

Precise scope: an already durably present result-failure marker is reconstructed.
The implementation cannot invent durable evidence when all writes fail. Recovery/
finalization faults without such a marker, interrupted output commits, full disk
crash/restart and explicit operator reconciliation still need separate design/tests.
Blocking is a safety property, not availability recovery or an exactly-once guarantee.
Native platform/device/Facebook, independent whole-contract and E2E performance gates
remain open. No state reset, data deletion, device command or publication occurs.

Final current-slice full gate: passed/oktrue/exit0; 730 tests in 110.370s,
OK(skipped=2). Original stdout/stderr and report saved and read back at
C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-restart-storage-final-203416/.
All 16 original command streams/status/path checked and hashed, exact unittest count
verified from original stderr; stream-integrity.json retained without raw log disclosure.
This proves current local regression/log retention, not device/Mac/full-disk durability.

Fresh independent bounded source review returned limited staticPASS for exactmarker loader reconstruction and terminal unknownprocess diagnostic preservation; distinct from parent-executed focus72/full730. See independent-restart-storage-review.md. Allwritesfailed/operator/damagedmanifest/filesystem/native/Mac/Facebook/exactlyonce remain unproven, T060/T062/T064 not closed.

## Internal acknowledgment slice — result-storage reconciliation (2026-10-02)

Added Manager-only reconcile_result_persistence(job_id, acknowledge=True), with no
HTTP/CLI/UI route. It acknowledges reviewed failed output rather than declaring it
valid/completed or retrying capture. True must be literal boolean True. Eligibility:
registered failed-result-persistence, no active task, explicit stopped process with no
persisted PID or registered process handle, explicit platform-clean transport/no error,
and no unresolved device-failure marker. Storage fault in writing the reconciliation
candidate preserves the job, disk, lease reason and Worker markers.

Confirmation saves the existing-version failed job's reconciled diagnostic before
publishing memory/removing only this registry reason. Other-job/global/same-Worker
uncertainty remains. The old task stays failed with no result revival; its original
failure reason/artifacts remain. After the final blocker is safely reconciled, an
existing/reconstructed scheduler is started/reused so other queued work can proceed;
there is no capture invocation as part of acknowledgment itself.

Evidence:
- Initial new-method regressions: missing method errors (11 guard/write/success cases).
- Expanded contradictory transport/wrong platform and missing scheduler-start controls
  failed before corrections; literal truthy1 acknowledgment is refused.
- Final focus: 77 Worker/cancel tests, OK, 1.448s. Windows/Darwin/Linux marker tables
  tested through mocked platform only, not native macOS/Linux runtime acceptance.
- Full project-venv gate with original streams: passed/oktrue/exit0; 735 tests in
  112.282 seconds, OK(skipped=2), private evidence at
  C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-reconcile-204624/.
- Two failed tasks plus queued third: first acknowledgment preserves other block;
  last acknowledgment enables only fresh work, no failed-task requeue. Restart sees
  reconciled failed diagnostics and queued unrelated work, not failed recapture.
- Backup/bounded 196-line slice diff/source hash at
  C:/Users/lenovo/AppData/Local/Temp/facebook-reconcile-backup-20261002-204223/.
- Original encoding/newlines preserved; git diff --check passed; real registry/local
  config timestamps and existing ADB/proxy ownership unchanged; no attached device.

Open: there is no supported external operator entry/UX yet. Do not advertise an API or
run ad-hoc imports against live state. Entire-storage-write failure, recovery/finalization
fault acknowledgment and all crash windows remain separate tasks. This scoped internal
primitive isn't a full availability recovery, result validator or exactly-once proof.
Native/Mac/Android/Facebook/E2E performance and broad independent acceptance remain open.

Independent read-only review affirmed the method's ack/failed-registry/process/transport/
device/persist-before-clear/other-block/restart-state conditions by source, with test
gaps rather than a confirmed method defect. Follow-up fixtures explicitly refuse
missing registry, cover Windows runner-owned, and reconstruct a Manager with two
failed blockers plus fresh queued job: first acknowledgment does not start scheduler,
last starts it once; old failed tasks never enter dispatch. Thread.start is still
mocked, so this proves invocation/state, not native scheduler/process consumption.
Relevant 77-test focus passed in 1.420s. No current-state user job was acknowledged.
Other process-only/transport-only sibling combinations and full operator entry remain
open independent acceptance, not hidden by the successful two-storage-block fixture.

Fresh independent limited source review affirmed internal guards/persist-before-clear/otherblock/no-recapture conditions; supplemental reread confirmed registrymissing/doubleblockstartup/winrunnerowned coverage fixes. This is static review, not independent runtime. See independent-reconcile-review.md. Operator-facing entry, process/transport sibling combinations and crash/native acknowledgment remain open T066.

Final post-review-supplement project-venv gate: passed/oktrue/exit0; 735 tests in
107.925s, OK(skipped=2). Original report/16 command streams read back and hashed at
C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-reconcile-final-205400/.
Latest bounded diff is 204 lines after assertion supplements. This is internal-method
and local-fixture proof, not an operator entrypoint, native startup consumption or
full-store/crash durability acceptance. No real failed job was reconciled in this pass.

## 2026-10-02 continuation: sibling process/transport isolation

A bounded matrix for result-storage acknowledgment covered process PID uncertainty, registered process-handle uncertainty and transport cleanup failure, across both worker identities and both possible lease-owner markers. The first run exposed a real local defect: sibling registered handles were not considered by lease scans because `_job_has_uncertain_process_locked` had no job-id context. The minimal repair passes the sibling id into the helper and treats a live `_running_processes` entry as uncertain. The repair keeps the sibling manifest, orphan marker and global lease block, rejects fresh queue assignment, and does not invoke capture, cleanup or worker startup.

- Focused matrix: 12 subcases, OK, exit 0.
- Focused `MultiWorkerManagerTests` + `ReconciliationHttpTests`: exit 0; raw output retained at `%TEMP%/facebook-continuation-focus-20261002.log`.
- Current local runtime remains unchanged: API/UI not listening, no ADB device attached, proxy/ADB listeners untouched. No real job was reconciled.
- Native macOS/Android/Facebook, full-store/crash durability, hosted CI and full requirement-level acceptance remain open. T070 is closed by the fresh full-stream gate and contract reread recorded below.

## 2026-10-02 continuation gate

The sibling process/transport isolation correction was verified against the current worktree. The focused matrix initially failed for registered process handles, proving the defect rather than merely adding a green fixture. After the minimal helper change, the bounded matrix passed 12 subcases and the focused `MultiWorkerManagerTests` + `ReconciliationHttpTests` suite passed 59 tests with exit code 0. Python compilation and targeted `git diff --check` also passed.

A fresh project-venv maintenance gate completed with exit code 0: **743 tests, OK, skipped=2**, with full original stdout/stderr retained under `C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-continuation-000556/`; the gate scope is local static/deterministic fixtures and explicitly does not claim real-platform acceptance. PowerShell AST and the gate's other static checks passed. No API/UI service was started, no ADB device was attached, no user job was reconciled, and no account, proxy, certificate, emulator or global Codex configuration was changed.

This closes the continuation's local sibling-isolation evidence, not the system-wide objective. Native macOS/Android/Facebook execution, full-store/crash durability, hosted CI, performance baseline, broad leakage review and final requirement-level convergence remain open.


## 2026-10-02 T071 all-write-failure restart boundary

The isolated restart experiment first failed when the existing test skipped the `all-postcapture-writes` restart assertion. A temporary test copy then created a second Manager from the last durable pre-capture manifest and observed: `status=failed`, `recovery_state=restart-process-identity-unverified-no-auto-resume`, `capture_lease_uncertain=True`, `active_worker_id=None`, no assignability for the next queued job, and no capture invocation. The persistence-reason registry is empty in this case because every post-capture write was intentionally unavailable; the global lease block and failed reconstruction still prevent automatic recapture. The production test now covers this branch explicitly and distinguishes it from cases where a failed-result diagnostic was durably written.

- Isolated matrix: 5 post-capture failure phases, including `all-postcapture-writes`, passed.
- The temporary test file was removed in-process; no live project manifest or runtime directory was used.
- This proves fail-closed restart behavior for the modeled pre-capture durable manifest. It does not prove recovery from physical media loss, power loss, or native filesystem crash consistency.

T071 final local gate: fresh project-venv maintenance gate passed with **743 tests, OK, skipped=2**, exit code 0. Full streams/report retained at `C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-t071-001741/`. The source/test/doc changes pass targeted `git diff --check`; no live runtime or device state was changed.

## Baseline gate closure — T003/T004

T003 and T004 are now closed as execution tasks, without upgrading their scope into whole-system acceptance. The fresh T071 gate retained complete original stdout/stderr and report data, ran the project `.venv` unittest discovery (**743 tests, OK, skipped=2**), Python syntax, Node syntax, PowerShell AST, `pip check`, required-file and release-path checks. The bounded release/history content audit separately examined current whitelisted text and locally reachable Git blobs; its independent classification limitation remains recorded under T057, so this closure does not claim exhaustive secret absence.

## Current security/release boundary focus

A fresh focused run of `tests.test_facebook_release_gate`, `tests.test_facebook_release_report` and `tests.test_facebook_http_security` passed **34 tests**, exit code 0. Raw output is retained at `C:/Users/lenovo/AppData/Local/Temp/facebook-security-boundary-focus-20261002.log`. A bounded current-tree signature search found only the already classified synthetic/private-key-header and fake-token fixture/document patterns; it did not establish exhaustive secret absence. The cross-platform scan remains heuristic and produced no confirmed defect by itself.

## Runtime reliability focus

A fresh local fixture run covering `tests.test_facebook_cancel_transport`, `tests.test_facebook_worker_bootstrap`, `tests.test_facebook_capture_orchestrator` and `tests.test_facebook_shutdown_safety` passed **172 tests**, exit code 0. Raw output is retained at `C:/Users/lenovo/AppData/Local/Temp/facebook-runtime-reliability-focus-20261002.log`. This provides current deterministic evidence for queue/worker/cancel/cleanup/lease branches only; it does not prove native process-tree, ADB, proxy, emulator or Facebook runtime restoration.

## macOS pairing fixture focus

A Windows-hosted deterministic pairing run covering `tests.test_facebook_mac_device`, `tests.test_facebook_macos` and selected POSIX/macOS cleanup cases passed **16 tests**, exit code 0. Raw output: `C:/Users/lenovo/AppData/Local/Temp/facebook-macos-pairing-focus-20261002.log`. This validates shared parsing/contract and mocked platform branches only; it is not evidence of native macOS permissions, subprocesses, ADB, emulator, certificate trust or Facebook E2E behavior.

## Setup/local runtime focus

A fresh fixture run covering `tests.test_facebook_setup`, `tests.test_facebook_local`, `tests.test_facebook_target_readiness` and `tests.test_facebook_transport_health` passed **56 tests**, **1 skipped**, exit code 0. Raw output: `C:/Users/lenovo/AppData/Local/Temp/facebook-setup-local-focus-20261002.log`. The skipped item and emitted manual-login text are fixture/reporting behavior; no real setup, account login, device, proxy or service was started.

## Current runtime ownership snapshot

Read-only Windows snapshot: API `8765` and UI `8766` are not listening; `127.0.0.1:5037` is owned by the project `platform-tools/adb.exe` (PID 36924); proxy `7897` is owned by `verge-mihomo.exe` (PID 29392); `adb devices -l` reports no attached device. No project API/UI process was identified on the checked ports. This is a dated observation, not a request to restart or clean any process.

## Final static review snapshot

The explicit `tools/maintenance_gate.py --skip-tests --json` run returned exit code 2 with status `partial`, as designed because unittest was intentionally skipped; all selected static checks in the report were successful. Separately, current `git diff --check` and Python bytecode-free compilation of `facebook_capture_api.py` and `tests/test_facebook_capture_api.py` passed with exit code 0. This partial run is not being counted as a replacement for the latest full 743-test gate.

## Real-platform acceptance handoff

Added `specs/001-system-maintenance/real-platform-acceptance-matrix.md`. It defines the safety preconditions and evidence fields for Windows runtime, Android/ADB/Facebook, macOS, performance and final completion decisions. It is a runbook only; no real-platform claim is made and no external environment was started.

## T073 precondition refresh

A fresh read-only preflight still finds only the existing proxy listener `7897` and ADB daemon `5037`; API/UI ports `8765/8766` are not listening and `adb devices -l` has no attached device. `facebook.local.json` and `facebook_workers.json` timestamps are unchanged from the prior snapshot. T073 remains unavailable; no launcher, service, device or configuration action was attempted.

## Final local gate refresh

A fresh project-venv `tools/maintenance_gate.py --json --log-dir` run completed with `status=passed`, `ok=True`, exit code 0. The full evidence directory is `C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-final-local-003848/`; the unittest stream again reports **743 tests, OK, skipped=2**. This is the current local/static/fixture baseline and still explicitly excludes real-platform acceptance.

## T073 real startup slice

With explicit user authorization, the existing `START.cmd` chain was executed without editing it. It started/reused the configured AVD `fb-x86_64-api35` as `emulator-5554`, reached ADB `device` and `boot_completed=true`, established proxy `127.0.0.1:7897` plus reverse capture/native transport, started/reused API `8765` and UI `8766`, and `/health` reported an idle available lease with active `worker-01`. Facebook package `com.facebook.katana` was present at version `555.0.0.49.59`; its `LoginActivity` process was brought to the foreground for inspection. No credentials were entered and no capture job or real target URL was submitted. The one-click startup emitted a stale-lock backup path under the configured AVD home; no AVD data wipe or uninstall was performed.

This is a real startup/health slice only. Login state, target visibility, result completeness and end-to-end capture remain unverified pending a permitted target and manual account readiness.

## Real Facebook session readiness

After the authorized startup, a direct ADB UI dump of `com.facebook.katana/.LoginActivity` showed feed/home controls and story/feed controls rather than a login form. This is evidence that a Facebook session is currently usable on the emulator for the next manual target step, not proof of target visibility or permission for any particular post. No account identifiers or feed content were retained in the report; no target URL was submitted.

## Real runtime idle-state refresh

The live API currently reports `service=ready`, `worker=idle`, `queued_jobs=0`, `cancelling_jobs=0`, `capture_lease.state=available`, serial `emulator-5554`, and no failover. The API does not expose a GET `/v1/jobs` or `/v1/batches` collection route (both returned 404), so no pending target was inferred or submitted. No capture action was performed without a user-approved target URL.


## Real numeric-post acceptance — 2026-10-03

Historical target located in FACEBOOK_BROWSER_SAMPLES.md (2026-09-08 sample), reused under current user authorization to continue with previous work-log targets. One POST only, default terminal semantics, no cap/wait/version/config change. Job fb_3a266090484a4e00 targeted numeric post 1691321539667513. Start 2026-10-03T03:32:34.418439Z; finish 03:35:09.557538Z (155.139s). Live result: completed/terminal, target matched by graphql-response-post-id, 39 successful pages, 975 raw reaction rows, 950 unique identities, 25 duplicates, reported total 950, gap 0, terminal page true, open tail false, rate-limit records 0. All group terminal; a separate filtered-reaction group remains open, so no claim that every filtered group was independently exhausted. Shares: query-not-triggered/0 users, NOT accepted as empty or complete.

After completion: process stopped, transport runner-owned, health worker idle/queue0/lease available; Android proxy 127.0.0.1:7897, reverse tcp7897 and tcp8081 present. UIAutomator during capture showed Facebook package controls (no account labels retained). Screenshot visual inspection was unavailable in this model; no screenshot-based acceptance is claimed. Run artifacts: C:/Users/lenovo/Desktop/facebook/runs/api-fb_3a266090484a4e00/attempt-001. This establishes one numeric-post Reaction run, not five URL families, Shares, native Mac, cancellation or same-condition before/after performance.


## Real share/p acceptance — 2026-10-03

One submission of historical share/p/1DVLR4FNo6/; job fb_87665d10370c407c. Started 03:37:48.509255Z, finished 03:40:31.685676Z, elapsed 163.176s. Completed/terminal, 950 unique reactions, 975 raw rows/25 duplicates, 39 successful pages, All terminal true/open-tail false; no rate-limit or HTTP auth error. Cleanup runner-owned, process stopped, worker idle, lease available.

Resolved URL equals prior numeric target 1691321539667513. Current target_verification uses external-post-link-author, matched_post_id=1691321539667513 but expected_post_id=null. Therefore binding strength differs from numeric-post graphql-response-post-id and needs source review before claiming identical verification. reported_total is null, so no count-gap proof is claimed. Shares0 not accepted as complete. 163.176s versus prior155.139s is observational only, not a controlled optimization benchmark. No waits/caps/versions changed. Direct extra UIAutomator observation returned no hierarchy; existing runner ui-ready/ui-settle artifacts remain available, no visual screenshot acceptance claimed.


## Real pfbid acceptance — 2026-10-03

Job fb_d80f9e1c654847a7 reused historical ATCTPE pfbid02qYWESeXuh3yj4oH6jHoy6xNKJLg8L777N5fFmjbZYNChhJ45ZE9S7CKtqKicdT7rl. One POST, unchanged default terminal/capture waits. Start03:43:18.672493Z finish03:46:13.989669Z (175.317s). Completed/terminal; target matched by pfbid-external-post-link-navigation, resolved numeric id1691321539667513 and author100063690504939/legacy story id match. 950 unique/975 raw/25 duplicate reactions; 39 successful pages, total950 gap0, terminaltrue/open-tailfalse, no limit records. Target-open verified in716ms; All pagination101636ms,76 attempts,37 growth events; 60 target guard matches. End workeridle/leaseavailable/processstopped/runner-owned cleanup. UI-ready XML61nodes/Facebook package; no screenshot visual acceptance.

Source review correction: share/p external-post-link-author path also restricts numeric post ids extracted from the resolved URL before author-set equality; expected_post_id null alone does not bypass numeric matching (analyze_facebook_capture.py:_external_post_link_identity). No confirmed guard defect and no code patch from that field difference. Three same-story URL runs are observational, not controlled optimization proof; group/Reel/Shares/nativeMac/failure-cancel remain open.


## Real group-post acceptance — 2026-10-03

Historical group1020765891821918/post2158908574674305 jobfb_85b05b426b414693, single POST/default terminal. Start03:48:07.173848Z finish03:49:58.735744Z (111.562s). completed/terminal; matched graphql-response-url-token in GroupFeedCsrQuery; target-open verified424ms. 308 unique/333raw/25duplicates,14successful pages; All13pages terminal/open-tailfalse, no rate-limit records. Reported_total unavailable: no count-gap proof claimed.

Runtime target_guard counters are zero/null and cannot independently prove source guarding; narrow raw-record check established all14 Reaction records have request_feedback_post_id=response_feedback_post_id=2158908574674305. Missing source_post_id top-level was not absence of binding (actual schema uses request/response_feedback_post_id). UI-ready XML61nodes/Facebook package retained, no screenshot visual verification claimed. End processstopped/cleanup runner-owned/workeridle/leaseavailable. Shares0 not accepted complete. Historical281 users superseded by current308 observation, no assumption of stable totals. Group/Reel overall matrix not complete until Reel verified; no optimization claim from unequal targets/times.


## Real Reel run — 2026-10-03 (video/story mapping review open)

Job fb_d5844207e1894a9d single POST share/r/1Dg59X6ysJ/ default terminal. Start03:52:17.366124Z finish03:53:39.719881Z (82.354s). API completed/terminal;2unique users,1successful page,raw2/duplicate0,terminaltrue/open-tailfalse,UI reported2/gap0,no rate-limit records. Endprocessstopped/cleanup runner-owned/workeridle/leaseavailable. ui-ready104/ui-settle81 nodes Facebook package; visual screenshot unavailable. No deep pagination run: initial response is terminal; no-pagination-run diagnostic alone is not failure when complete response already terminal.

Target redirect resolved reel1054985397317048; subsequent UI source discovery picked story122122050735339946. Target verifier graphql-response-post-id and sole reactor request/response both bind that Story. This does not yet independently prove Reel-video-to-Story relationship. A preliminary structural JSON walk found no matching nodes, potentially due to encoded response envelope; source/body-decoding and explicit mapping must be checked before claiming full Reel target acceptance. Preserve result as API-terminal, mapping-review-open, not five-family all-PASS. No retries or guard weakening.


## Reel mapping resolved and five-family Reaction matrix — 2026-10-03

Using the existing decode_stream GraphQL decoder, response line7 WatchDeeplinkFeedUnitSectionComponentQuery contains the SAME Story with canonical relay/media id1054985397317048 and feedback.legacy_api_post_id122122050735339946. This explicitly connects redirected video to feedback Story; reactor request/response feedback ids both match122122050735339946. The previous direct-JSON walk failed to decode the envelope and must not be treated as absent mapping. Helper _reel_video_story_mappings returns canonical story/media identity1054985397317048; feedback namespace is separately122122050735339946, not necessarily a contradiction. This resolves mapping-review-open for THIS saved run; it does not prove every Reel layout.

Five current Reaction runs: numeric950/155.139s;share-p950/163.176s;pfbid950/175.317s;group308/111.562s;reel2/82.354s. All completed/terminal/complete and processstopped/runner-ownedcleanup. Aggregate redacted identity-free matrix plus capture SHA256 at C:/Users/lenovo/AppData/Local/Temp/facebook-real-url-matrix-20261003-115819.json. All pagination76.331/82.513/101.636/35.042s respectively; Reel initial response already terminal. Remainder includes opening/setup/postprocessing/cleanup and cannot be assigned to one bottleneck without phase measurements. Observational timings across different routes are NOT before/after optimization proof. Shares, actual failure/cancel recovery, visual screenshot verification, nativeMac and full-system independent review still open. No code/config/wait changes performed.


## Real phase attribution and local result-build measurement — 2026-10-03

Saved target-open diagnostics: numeric16.259s,share-p30.516s,pfbid26.323s,group28.454s,Reel35.876s. Verification times are nested within target-open and MUST NOT be added separately. Saved pagination elapsed:76.331s,82.513s,101.636s,35.042s,none(Reelinitialterminal). Difference from job elapsed remains unallocated (setup/entry/writer/analyzer/cleanup), not proven wasted delay. No phase is shortened based on subtraction.

Read-only build_api_result experiment on each of five existing real result sets,5 consecutive warm-cache samples per set: median wall14.162/12.127/11.555/6.121/1.520ms respectively; all five result hashes equal per input and Reaction counts preserved. Exact summary C:/Users/lenovo/AppData/Local/Temp/facebook-result-build-baseline-20261003-120136.json. Process CPU readings show coarse15.625ms Windows granularity (some0), so no precise CPU claim. No raw users printed or result files rewritten. Result-building is not a material wall-time bottleneck on this workload; caching/refactoring it for E2E speed is rejected as unsupported. Does not measure analyzer decoding, checkpoint/merge, cold filesystem I/O, CPU/memory during live capture or throughput under load.

Next measured work: instrument/attribute setup, analyzer, merge/checkpoint and cleanup independently under unchanged default waits, with reversible source changes and regression checks if instrumentation required. No arbitrary wait reduction, unlimited retries or live service restart performed.


## Offline analyzer/materialization actual-input baseline — 2026-10-03

Existing numeric2,078,240byte and pfbid32,096,699byte inputs replayed three times each into NEW external TEMP subdirectories, no Facebook network/capture or original artifact overwrite. Analyzer CLI expected-post-id/expected-url/focus-source-id matches live request; all six exit0/targetmatched. Wall seconds numeric0.2505/0.2502/0.2501;pfbid0.5339/0.5424/0.5483. Per-input output CSV hashes repeat identically. Source SHA256 unchanged. Summary/full stdout+stderr retained C:/Users/lenovo/AppData/Local/Temp/facebook-analyzer-baseline-20261003-120408/.

Actual materialize_capture_attempts including merge, artifactcopy/checkpoint and reactorCSV into new external dirs,3x each: numeric0.0793/0.0752/0.0710s;pfbid0.8048/0.8106/0.8123s. Per-input CSV hashes identical across trials, sourcecapture unchanged. Summary at C:/Users/lenovo/AppData/Local/Temp/facebook-materialize-baseline-20261003-120444/. Synthetic equivalent is NOT substituted: these are actual recorded input bytes, but warm-cache replay is not live/cold-disk timing. Analyzer+materialization (<1.4s even32MB) and resultbuild(<15ms) do not explain tens of seconds remaining from real job duration. No data-processing/wait optimization implemented based on these observations; preparation, entry controls and transport cleanup need explicit elapsed measurement. Different CSV hashes across analyzer and materialization are not compared as equality: materialization adds checkpoint/provenance schema. Full semantic row equality between stages not newly claimed. No CPU/resource/E2Ebeforeafter claim.


## Passive runner timing and real sample — 2026-10-03

Added diagnostic-only Stopwatch markers to existing Windows PowerShell/Python child wrappers (try/finally retains exit/exception/output semantics) and end of successful transport cleanup. Only script basenames/stage/elapsedms logged, no argv/identity. Marker-output failure caught ONLY in helper, never suppresses operation exceptions. Cleanup elapsed marker is absent if cleanup throws before its existing end; absence is not success/zero. No timing file or public API/schema addition, no changed waits/ordering/version/state. Native macOS timings remain separate/unmeasured. Rollback source/test backups C:/Users/lenovo/AppData/Local/Temp/facebook-capture-timing-20261003-120815/.

New executable AST-isolated fixture validates success/nonzero7/childthrow/Pythonthrow/QuietStdout/no successstream pollution/no privateargv/leaked optional logging failure. PowerShell AST and diffcheck pass; relevant focused196tests pass (24.275s); fresh full gate744tests/2skip passed145.092s exit0 with originalstreams at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-timing-121247/. Main-agent second pass, NOT independent agent review.

Instrumented real Reel jobfb_5178efe3dcb34c4a completed/terminal,2users/reported2/gap0/1terminalresponse;processstopped/runner-ownedcleanup,workeridle/leaseavailable. Start04:10:12.708390Z finish04:12:01.076435Z=108.368s. Log markers: targetchild37.266s,explorer15.558s,reaction-analyzer0.577s,transportcleanup5.973s,final-analyzer0.743s,networkbuilder0.236s. Marker total60.353s;47~48s remains outside wrappers (setup/inprocessentry/grace/other operations), NOT waste proven. Previous sameReel82.354s versus108.368s is variable observational run, NOT speedup/regression causal proof. Need setup/grace breakdown before modifying waits. No API restart or new target occurred.


## Disjoint preparation timing validation — 2026-10-03

Passive preparation Stopwatch boundaries: runner-initialization ends before capture try;device-preparation covers watchdog/ADB/framework/UI-helper;network-capture-preparation then covers normal-network/reverse/proxy/mitm/Frida until target handling. Two restarts make those three intervals disjoint; only optional log additions, no wait/return/API/config/state change. T076 executable timing contract + new boundary fixture pass; selected navigation/orchestration/cancel/shutdown197tests pass23.675s. AST/diffcheck pass. Full744 gate is PRIOR timing version, not post-T076 proof; no new fullsuite claim. Backups C:/Users/lenovo/AppData/Local/Temp/facebook-preparation-timing-20261003-121814/. NativeMac unmeasured.

Known real Reel jobfb_5d63b0ca46f444e5: completed/terminal,2identities/report2/gap0/1terminalresponse;stopped/runner-ownedcleanup. Total73.731s;init0.198,device2.824,network-capture7.804,target35.091,explorer15.017,reactionanalyzer0.392,cleanup5.110,finalanalyzer0.421,networkbuilder0.167s. Marker sum67.024s;residual6.707s includes inprocess branches/grace/serviceoverhead, not assigned without more proof. Prior sameReel108.368s with only wrappers was not fixed48s overhead; currentprep10.826s shows variance, no before/after speedup claim. Targetopening remains largest measured subphase; inspect exact Reel verification retries using existing diagnostics before any adaptive-wait or guard change. All original artifacts and services preserved; no new account actions/APIrestart.


## Explicit Reel namespace mapping — 2026-10-03

Saved first navigation response exposes Story Relay media1054985397317048 alongside equal Story.post_id and feedback.legacy_api_post_id122122050735339946, same actor/feedbackowner/Relayauthor61590198406038. _reel_video_story_mappings previously discarded this namespace split as contradiction, forcing delayed/network-or-UI-discovery path. Minimal helper correction ONLY allows same-object exacttargetmedia+Relaymedia, identical numeric Story/feedbackpost, absent-or-same legacyPost, and coherent concrete actor/owner/Relayauthor. Multiplefeedback mappings/wrongexpected/missing/conflicting proof still rejected. No timeout/wait/fallback/source guard changes.

Initial fixture used invalid Relay layout; corrected to real observed VK layout before meaningful red reproduction. Positive then failed with old code; after fix positive +8negativecases+ambiguous/wrongexpected pass. Saved response replay first7records now matches feedbackpost atline7. Full analyzer regression passes; analyzer/navigation/API focus279tests pass37.130s. Full local gate748tests/2skip passed140.004s exit0, originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-reel-namespace-123042/. Source/test/task rollback snapshot C:/Users/lenovo/AppData/Local/Temp/facebook-reel-namespace-20261003-122524/. Main-agent secondpass only, independent review not established.

Actual sameReel jobfb_8bd3e2358d6c43a4 completed/terminal2users/report2/gap0/onepage,stopped/runner-ownedcleanup. Start04:27:57.255893Z finish04:29:07.878982Z=70.623s. Actual runtime FIRST navigation produced NO WatchDeeplinkFeedUnitSectionComponentQuery (only prefetch/ReelsVDD etc); strict firsttarget matching still timed out16.097s/8attempts and targetopen32.760s, then existing UI-source-discovery safely completed. Therefore no demonstrated live speedup from this exact mapping fix;70.623 versus prior73.731 is observational variance, NOT causal proof. Saved explicit mapping correctness improved; varying Reel response shapes and guarded detection remain next investigation. No broadened mapping, retries, login/config changes or APIrestart.


## Reel recommendation rejection and Shares-specific acceptance — 2026-10-03

Saved jobfb_8bd3e2358d6c43a4 ReelsVDDChainingQuery atline10 has requestseed1054985397317048 but decoded6documents contain recommendation videos, none target1054985397317048 and no explicit mapping. Seed is NOT proof of response-target identity; no further relaxation/shorterwait was applied. Retaining old bounded discovery is intentional.

Correction to earlier family-matrix wording: recorded numeric/Reel job manifests confirm collect=[reactions]. Shares0/querynottriggered is NOT-requested, not a Shares failure; previous notes only meant Shares was not accepted/proven by those runs. New explicit collect=[shares] jobfb_ad138ecdb11c4fd1 numeric1691321539667513 singlePOST default waits: start04:36:01.838251Z finish04:37:46.484227Z=104.646s;completed/terminal. Targetmatchedgraphql-postid;query2/pages2,terminaltrue/open-tailfalse;16uniquevisibleactors/16uniquesharestories/0duplicates/0missingactorids;reportedUI46,gap30,limitedtrue,visible-subset-terminal. This is terminal enumeration of CURRENT-account visible16, NOT all46, not empty result or a completeness failure. Entrystrict-target-photo-comments/listconfirmedtrue in actualdiagnostics. Endprocessstopped/cleanup runner-owned/workeridle/leaseavailable. UI-ready76nodesFacebook package, visual screenshot still unverified. Capture SHA256: f5b05bd27d562ccf61a4b15af2b479247377fc520c1c26a30ce17ca3c171cb59. No code/config/service restart, forcedcleanup, account/reset actions or test rerun required for evidence-only records. NativeMac, real cancel/failure and multi-family Shares remain open.


## Real running-job cancellation — 2026-10-03

Current-user-authorized disposable numeric-target jobfb_4a1224c9bd984084, freshidle/leaseavailable/queue0/cancelling0 before onePOST. Registered capture runnerPID33916 powershell.exe created2026-10-03T12:40:05.132172+08:00 observedrunning with transportpending, then ONE ordinary cancelPOST. Responsecancelledtrue/runningjobcancellationrequested. Subsequent authoritative jobcancelled/processstopped/transportrestored/noerror/persistedPIDnull; PID33916 absent; no jobpath or direct-childof33916 processes after excluding the observation PowerShell itself. This bounded process scan is not universal unknown-descendant proof. No captureport8081 listener; markerfilesabsent; API8765/UI8766 retain PIDs27712/44816. Androidproxybefore/after127.0.0.1:7897/reverse7897+8081unchanged. Healthworkeridle/leaseavailable/queue0/cancelling0. CancelledresultGET rejectsHTTP409. No manualkill/cleanup/servicerestart/AVDreset or accountmutation. Partialartifacts retained.

Redacted timestamped proof C:/Users/lenovo/AppData/Local/Temp/facebook-real-cancel-20261003-124252.json. Running-cancel and restoration are now real local evidence, not onlyfixtures. Failure-time storage/devicefault/crash/nativeMac and full-system independentreview remain open. No optimization claim from cancellation latency.


## T081 current platform triage — 2026-10-03

Refreshed scanner78files/no skips,94rows/235occurrences; per-row triage + exact sourcehash at C:/Users/lenovo/AppData/Local/Temp/facebook-platform-triage-20261003-125123.json. Classifiedcounts {"paired-ci-paths": 3, "counterpart-needs-validation": 16, "windows-adapter-syntax": 52, "macos-launcher-path": 2, "architecture-needs-validation": 13, "guarded-shared-path-review": 6, "documentation-reference": 1, "needs-source-review": 1}. ALL94rows inventoried but not all behavior/native parity resolved. CI Windows/macOS paths are explicit matrix adapters, not accidental mixed venv. facebook_target_readiness PowerShell hits are doc/comments, not execution. Windows PS1/CMD syntax expected; same-stem absence is NOT enough to claim missing support. START.command->setup.sh,CHECK.command->setup.sh--check,CONFIGURE.command->Macvenv+facebook_setup verified by source;facebook_macos_runtime enforcesdarwin and resolvesnative executables. Shared Python sys.platform/os.name branches exist at observed APIs/local/visible/setup/bootstrap sites. Architecture13rows and counterpart16rows still require exact behavior/host-device proof;no blanketclosed/inferredsupport. No nativeMac/hostedCI run or repo/system code/config edits. T081 remains partial.

Final single needs-source-review row inspected: validate_facebook_predeploy.py lines130-137 use source-text contract assertions, not a PowerShell launch. External triage now classifies it static-contract-literal(1); prior triage counts above are intermediate. Architecture/counterpart/native and independent gates remain open.


## T083 isolated storage-reconciliation boundary review — 2026-10-03

Existing restore/ack eligibility/persist-before-clear/sibling block and restart tests inspected alongside guarded Manager RLock. Added one bounded concurrency fixture(two subcases) using temporary BASE_DIR and mocked scheduler, no actual user jobs/device/network/storage injection. First acknowledgment pauses at writer while second starts; succeeds=>onepersist,oneack,secondApiError; fails=>diskbytes/liveoldstate/registryblock confirmed before OSError, second independent explicitack may later succeed. Bothfinalfailed/noresult/noqueue/noactualcapture,one successful scheduler-enable invocation; no automatic POST replay. Worker-start method mock count includes initial construction plus final acknowledgment only.

Initial fixture missing local queue import repaired (fixture error, not production defect). FocusMultiWorker+ReconciliationHttp60tests pass5.727s exit0 raw C:/Users/lenovo/AppData/Local/Temp/facebook-reconcile-concurrency-focus.log;concurrency test repeated10times(two subcases perrun)pass0.151s, alljoins bounded and no lingeringthreads;diffcheckpass. Only test/doc changes, production source unchanged. Latestfull748 gate predates this addedtest; no new full749claim. Testrollback C:/Users/lenovo/AppData/Local/Temp/facebook-concurrent-reconcile-20261003-125454.py.

T083 closes bounded isolation review, NOT powerloss/nativeDisk/multiprocess or operator recovery on actualfailedaccountjob. Previously persisted marker rebuilt; entirely unavailablepostcapturewrite reconstructs failed/uncertain/noautoresume; no invented durable marker. Main-agent review not independent (T080 remainsopen). Live APIready/workeridle/leaseavailable unchanged. NativeMac/fullhistory/hostedCI/performanceclaim remainopen, no forcedclear/livefault/delete/restart performed.


## T082 finite paired verification and claim boundary — 2026-10-03

Old/current analyzer loaded from original timestampedbackup/current source, same Python/same immutable recordedprefix, alternating order6trialsperversion each of2cases, one tracemalloc sample per version/case. Actual explicitmappingprefix7:oldnotmatched/newmatchedfeedback122122050735339946,median3.8198/2.3980ms,Pythonpeak336803/336511bytes. Actual recommendationprefix12:bothnotmatched,median6.0578/6.3510ms,peak601957/603052bytes. Inputcapturehashesunchanged, no account/network calls/outputidentitydump. Summary C:/Users/lenovo/AppData/Local/Temp/facebook-namespace-paired-proof-20261003-125835.json. Memoryfigures Pythonallocationsonly, not processRSS/CPU/disk/network or livesystem resources. Millisecond work difference is not E2Espeedup or broadresourceclaim. Correctness improvement and negativeguard preservation are proven on these exactprefixes.

Finite future E2E comparison acceptance: same permittedReel,sameaccount/device/network/collect/defaultwaits/caps,cleanidleavailable lease before each, at most2pairedold/currenttrials(4livejobs) and no automaticretry. Only narrowmapping variant selected/isolated, NEVER wholesale oldfile restoration in dirty workspace. Check targetvideo-feedback-authorbinding,source set,terminal/visibility,identitycounts/hashes scoped per same observation,proxy/cleanup/lease each time. Retain total/targetopen/verificationattempts/phaseCPU/memory/I/O evidence separately. If firstresponse shapes differ or contain onlyrecommendations, classify noncomparable/defer, do not repeat beyond cap or lowerwaits. Do not run destructive storage/devicefaults or changeplatform/dependencies/accounts. Requires reproduciblevariant execution design before anyliveA/B; currently NOT implemented/executed. Existing70.623/73.731s runs are not causalproof. T082 closes the finiteplan/explicitdefer obligation, not T013-T015 wholeE2Eproof.

Remaining current independentreviewT080 needs separately attributable reviewer; mainexecution/replays are not independent. NativeMac/hostedCI/architecture site acceptanceT081 remainspartial. No productioncodepatch/schedulerrestart/tasksubmission this pass;testonly lastsourcechange stillhas60focus/10repeatproof, full748gate predates addedconcurrencytest.


## T081 exact architecture-site review — 2026-10-03

Inspected all13 architecture finding sites and relevant guards/callers; updated external per-row matrix facebook-platform-triage-20261003-125123.json (prior snapshot preserved). Windows process filters/sessionlauncher/manager and primaryAVD defaults intentionally refer tox86_64 environment; no such stop/manage actions executed. DefaultAPI AVDname/example not generalMac configuration proof. Workerallocator only constructsname (does not selectABI); currently discovered sourcecaller Windows addworkerPS1, genericCLI parity not promised. Mac host_abi recognizesarm64/x86_64 andrejectsunknown, but overallsetup_run is darwin+AppleSilicon-only; recognition ofIntelABI is NOT fullIntelsetup support. prepare_device doctor requiresAndroidarm64-v8a and validate_elf checks ELF64 EM_AARCH64, rejecting Mac/Windows/x86 artifact. No ABI/version changes or download.

Architecture triage no longer merely raw13unreviewed signatures; it is source-classified WITH native/Intel/defaultconfiguration limitations. No newlyconfirmedwrong-ABI executionpath demonstrated. RemainingT081:16launcher-counterpart behavior checks + nativeMac/hostedCI acceptance. No allplatformPASS or inference that everyadapterequivalent. Source-level classifications do not establish live nativepermissions,Frida,device setup. Productionfiles unchanged so no redundant tests run.


## T081 functional counterparts — 2026-10-03

Inspected16 same-stem-absence findings and source/counterparts/docs, updated exact-row matrix at facebook-platform-triage-20261003-125123.json (prior snapshot retained). Shared capture core Darwin->facebook_android_capture.py/Windows->PS1; API starts via local.py onMac; nativeDarwin emulator helper enforcesplatform; v2PythonUIportableCLI exists. No assumed full equivalence: MacSTART.command/setup.sh starts sharedAPI, not Windowsfullboot+automaticv2 transaction. Windowsvisible-session/workeraddresume/feedoffsetbatch/filequeue/stagenativeABgate/whole-runtimeSTOP remain Windows-specific or nativeequivalenceunverified. MacCtrl-C API terminal is NOT proof of whole-runtime shutdown. Shared APIbatch/cancel does not establish identical PS1orchestration semantics. macosRunbook explicitly scopes newsetupAppleSilicon and distinguishes oldtransport adapter. No MacUIport/launcher addition, processstop/restart/config/install or sourcecodechange performed.

All94scannerrows now source-triaged (not94defects/resolutions). Platform functional differences/defaultABI limits retained and nativeMac/hostedCI proof absent, so T081 stillpartial. T080independentreviewer authorization stillnotreceived. No repeatedtests for documentation-only triage. Source classifiers require native platform acceptance to close FR-007/T017, not mere filename presence.


## Final local handoff gate — 2026-10-03

Original final-handoff131300 process/session vanished; reportempty,stderrunfinisheddots, no completion verdict. Original evidence retained and NOTPASS. One read-only fixture/static retry with a NEWexternal directory completed:749tests128.195s,OK(skipped2),exit0,statuspassed; savedreport and original commandstreams readback+SHA256 verified, integritymanifest at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-final-handoff-retry-01/stream-integrity-final.json. No repeatcapture/APIrestart/independentreview/nativeexecution claim. This includes the new concurrentack regression that predates this gate; previous748 result now historical.

External-statechange before gate: API8765/UI8766 no longer listening, emulator notrunning; oldUIpid44816 reused for cmd.exe created13:12:17, neverkill by stalepid. Adbdevices query automatically started daemon5037 (not a side-effect-free command), no attacheddevice; proxy7897 remains observed. No reason for service disappearance established; no automaticrepair/relaunch/cleanup performed. Earlier successful jobs/cancel prove those dated runs, NOTcurrentruntimehealth.

Residual original objective unmet: independentlyattributed T080 reviewer requires explicit delegationauthorization;T081 nativeMac/hostedCI unavailable (all94sourceleads triaged but not nativeaccepted). No new E2Espeedup claim: T082finitecomparison only correctness/negativeguards, live responsevaried. Broad earlier independent/history/platform requirements remain unclosed; goal NOTcomplete. Further unchanged-loop retests/document append are not meaningful progress. Preserve all dirtysource/rawartifacts/services/userconfig.


## Explicitly authorized environment restoration — 2026-10-03

User explicitly authorized reopening environment if accidentally closed during this goal. Fresh ownership scan before restore: onlyexistingproxy7897, noAPI/UI/capture/emulator processes or capturemarker; manifestscan669records(no caps/unreadable),0queued/running,1cancelling. The one cancellingfb-restored-error record has nullPID/stopped/restored; preserved as-is, no manualcancel/state/lease mutation. Startup APIreportsqueue0/cancelling0/idleavailable; rawrecord stillcancelling, so NOTclaim that persistedrecordwasfinalized or schedulerloadedthatrecord.

All4 API/UI previouslogs +local/workerconfig+singlependingmanifest copied to C:/Users/lenovo/AppData/Local/Temp/facebook-authorized-restore-20261003-132921/ BEFORE originalSTART.cmd invocation. Originalstartup finishedexit0, configuredfb-x86_64-api35/emulator5554 booted;staleAVDlocks moved byexistinghelper to avd-lock-backup-20261003-132928,no wipe/reset/login/installversionchange by assistant. Initialproxyprobeexit20 resolved withinhelper; defaultnetwork100 recoveredwithouttransportreset. APIready/workeridle/queue0/cancelling0/leaseavailable,devicebootcompleted,proxy127.0.0.1:7897,UIHTTP200. local/workerconfig byteSHA256 unchanged frombackup. No oldPIDkill or unrelatedprocessstop; no repeatcapture or test run.

Restorationauthorization applies to currentgoal's existing environment, not independentagent delegation, globalautomation/accountreset or Macaccess. Remainingnative/independentreview/performance acceptance boundaries unchanged; no overallgoalcomplete or newspeedclaim. Future accidentalclosure may be handled after ownership/statecheck under thisauthorization; not install a backgroundwatchdog.


## Current release/local-history signature refresh — 2026-10-03

Read-only parent scanner:187explicitwhitelistfiles,375localreachableobjects/279blobs,5MiBperinput/2000objectcap,0errors/0skips. Sixsignatureclassesreimplemented;not exact historicalregexequivalence,so11currentvs13prior candidates is NOTleakreductionproof.9full/valueSHA256matches oldfixture/placeholderfingerprints;2newcurrent/history matches are fixedfakebearer sentinel in test_atomic_snapshot_contains_no_sensitive_transport_fields. AST+valuehash verifies fixedmockshape +assertNotIn exclusiontests in BOTHcurrent/historical source, without candidate-value output or validitycheck. Firstclassification script stopped on case-sensitiveexactdummyassumption; corrected only after ASThash/strictmockshape verification, no silentautoclassification.

Reports/sourcehashes C:/Users/lenovo/AppData/Local/Temp/facebook-release-history-refresh-20261003-133607/. Parent-only0unresolvedsignaturecandidates,NOTindependentlyconfirmedabsence;T057remainsopen. No ignoredruntime/config/accountfile scannerread,imports/targetcode/testexecution/exportZIP/network/credentialvalidation/historyrewrite/deletion. Excludedunreachable/reflog/index-only/unfetchedremote/arbitrarycredentialformats. Docappendpostdatessnapshot;futurepublication mustrehash. No newconfirmedsecretfinding at boundedscope,NOTfullsecurityapproval.


## Confirmed unstarted scheduler rollback repair — 2026-10-03

StartWorker helper registered thread/startedIDs before Thread.start; RuntimeError/OSError on demonstrablyunstarted thread left phantom _worker, making later start return an unstartedhandle. Two redsubcases reproduced exactpointerdefect. Minimal repair serializes publication+start underexistingRLock and rollsback only exactownedthread with identNone; restores priorstartedIDs/removesthatregistration, rethrows originalexception. If identnonNone, handle retained and latercalls do NOT startanotherthread. No arbitrary deadworker autorestart, queue/concurrency/timing/visibility/config/API contract change. Native testThread target entersManagerlock successfully (boundedjoin), confirming no start/Managerlock deadlock. No live resourcefault injection.

Source/test/task backups C:/Users/lenovo/AppData/Local/Temp/facebook-worker-start-20261003-135210/. Initial focus91tests hit independentWindows10053 in HTTPsourcefixture: MockManager noStartWorker call, proxylogs403 before unreadbody close; originalfailure retained. Only Host/Origin source-fixtures changed to header-onlyContentLength20 (guard mustrejectBEFOREbodyread), no productionHTTP weakening/auto retry; finalfocus91pass7.992s. Native addedtest included in finalfull752tests129.518s,OK(skipped2),exit0;reports/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-worker-start-final-01/. This supersedes749fullbaseline for currentcode. Mainagent evidence, not independentlyreviewed.

Appliedcode via ownedIDLE API reload afterverifyinghealthidle/queue0/cancelling0/leaseavailable and Python listenerabsoluteprojectcmdline+creationtime. Only ownedAPIpid35836 stopped throughprocesshandle; no stalePIDkill/globalSTOP/devicefault/reset. API/UIlogs+configs copied BEFORE existingSTART.cmd to C:/Users/lenovo/AppData/Local/Temp/facebook-api-update-logs-20261003-140231/. STARTexit0,reuseddevice and healthyUI,creatednewAPI;posthealthready/idle/available/queue0,Androiddeviceboot/proxy127.0.0.1:7897,UI200. Configbytehashunchanged. Existingnumeric950terminal/Shares16terminal readable;cancelledresultHTTP409. No repeatFacebookcapture. API rarefailure rollback confirmed in isolatedfixture, not realresourceexhaustion; actualstartup validated nonfailurepath. T084complete within declaredscope;Mac/independentreview/actualE2Espeedup remainunproven.


## Typed scheduler error HTTP completion — 2026-10-03

Three red HTTPoperator cases directly called actual_start_worker with mocked nativeRuntimeError;exceptionescapedreload/enable/reconcile routes. Narrow SchedulerStartError(RuntimeError) now wraps ONLY native Thread.start RuntimeError with original__cause__;OSError unchanged;genericManagerRuntimeError is NOT caught. Three Managerinvoke boundaries maptypederror tosingle sanitized503/inspect-state-before-retry. Responsewriting remainsoutsideManagercatch; even SchedulerStartError in reply propagates, oneManagercall/onewriteattempt,noautoretry. Existing200/404/409/requestguard/statusbody contracts retained.

ActualtemporaryManager direct+v2proxy fixture demonstrates ackmanifest alreadydurablethenThreadfail=>503while jobremainsfailed/reconciled,registrationrolledback,noactualcapture;secondmanualnegativeack rejects409/startonlyonce. Thus503NOTfullrollback;API_USAGE documents inspectjob/queue/health first,noautomaticPOSTretry. Causeidentity/unrelatedlogic/replyerrors assertions covered. Focus71pass15.137s;finalfull755tests136.770s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-scheduler-http-final-01/. Source/test/doc/taskbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-http-20261003-140854/,boundedproductiondiff48lines. Mainagentreview,notindependent.

Applied by ownIDLE APIreload afterhealthidle/queue0/cancelling0/available pluslistenerPython/projectcmdline/creationtime verified. OnlyAPIpid40600 stopped throughprocesshandle,logs/configsbeforebackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-http-reload-20261003-141911/. STARTexit0/reusedexistingemulator+UI/newAPIready. Posthealthidle/available/device/proxy127.0.0.1:7897/UI200;existingReaction950/Share16preserved;cancelledresult409. ConfigbyteSHA256unchanged. No realthreadresourcefault/storagefault/duplicatecapture/accountreset/versionchanges. T085complete scope;nativeMac/independentreview/E2Espeedup remainnotclaimed.


## Additive scheduler health snapshot — 2026-10-03

Source health previously reportedready/idle basedonlyHTTP/jobactivity, evenabsent/dead scheduler. Added ONLY scheduler={state,alive} underManagerlock, statesrunning/true,not-started/false,stopped/false,unknown/null onRuntimeError/OSErrorprobe. No nativeIDs/argv/exceptiondetail; preserveoldservice/worker/mode/lease fields. Observerneverdropshandle,clearsjobs/lease,startsworkers or retriesPOST. Sampling is not guaranteed continuousliveness/jobcompletion/cleanup proof. ABI-independent stdthread observation, nativeMac notexecuted. API_USAGE/spec/plan recordadditivecontract.

Two redtests initially6missingfieldsubcases; fixedmissing/unstarted/running/stopped and twoprobeexceptions; ownership/uncertainlease/oldfields preserved. Focus73tests14.588s pass;full757tests130.313s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-scheduler-health-final-01/. Source/test/spec/plan/task/docbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-health-20261003-181137/. Current code now755pass/2skip, not old755runtotal. No claimed speedup or independentreview.

APIconnection failure was followedbyconfigport8765/listener+process ownershipchecks;confirmednoAPI/UI/captureprocess/marker/capture8081listener. Existingproxy7897/ADB5037 retained. Restoredclosed environment viauser's explicitreopenauthorization, BEFOREstartup4logs+configsbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-health-restore-20261003-182013/. STARTexit0,newAVDboot/newAPI/UIready;oldstaleAVDlocks movedbyexistinghelper to avd-lock-backup-20261003-182019,no wipe/userdata reset. PostactualGET /health schedulerunning/alivetrue,service ready/workeridle/queue0/cancelling0/leaseavailable,Androiddevice/proxy127.0.0.1:7897,UI200;retainedReaction950/Share16/cancelresult409. ConfigSHA256unchanged. No liveprobeexception/resourcefault injection,repeatcapture/agent/globalwatchdog/versionchange. T086complete currentdeclaredscope.


## Existing real artifact delivery boundary — 2026-10-03

Read-only authorized own numeric jobfb_3a266090484a4e00, API8765 +existingv2proxy8766. Selectedfacebook-reactor-users.csv download both200/178253bytes/SHA256equalondisk artifact/attachmentfilename/nosniff. DiskCSV950rows and exclusivelysource_post_id1691321539667513, matching knownresult. No usernames/actorIDs/contentprinted or saved in auditproof. Unknownalias/encodedrelativepath/encodedWindowsabsolutepath(3cases each route) all404; sourceartifact() usesconstantARTIFACT_PATHS lookup before filesystem reads. Bothroutespreservefixedalias handling, no arbitrary client filepath accepted. Redacted8case evidence C:/Users/lenovo/AppData/Local/Temp/facebook-live-artifact-boundary-20261003-182759.json.

This verifies selected current CSV delivery and malformedalias rejection, NOT everyartifact/symlink/hostmetadata scenario. run_dir originates trustedManager/localmanifests; no hostile symlink/localmanifest tampering in liveaccount workspace. No externallyreachable/multitenant threat claim or exhaustive securityapproval. T009 broad content/manifest/physicalfilesystem review remains scoped/open; independent classification T057 not replaced. No productioncode/config changes, repeatFacebookcapture, cleanup or redundantfullsuite run. Lastfull757 source/test gate unchanged.


## Artifact physical path robustness completion — 2026-10-03

Temporary ownfixture junction to sibling syntheticoutside directory reproduced oldartifact() returningallowedCSV beyonddeclaredrunroot (red). Minimal shared_checked_artifact_path resolvestrustedroot/candidate, rejects childsymboliclinks and escapingjunctions before file stat/read, preserveslogicalpath/name andtrustedrootrelocation. Job/batchdownloads and metadata use same check; blockedfiles0bytes/missingnotready. Existing availabilitylabels retained. Helper handlesresolutionOSError/RuntimeError withoutrawpath text. This is accidental/stale-link robustness,NOTconfirmedremote/lower-trustexploit,tenantpolicy or malicioushostwriter sandbox. Hardlinks/TOCTOU/hostilelocalmanifest authority expresslyunproven. API_USAGE/spec/plan detail intentionalchild-link incompatibility andcopyregularartifact guidance;validtrustedrootmove remains allowed.

NativeWindowsjunctionfixture onlyinside explicitlycheckedTEMPparent (target/linkparentcanonicalinside scratch); no actualjoblinks/userfilesaltered. Three tests cover job/batch/metaescape,regularfiles+rootrelocation,missing/empty/resolutionerrors. Initialred case exposedAPIreturn; fixture metadata assertion corrected to existingstate/size schema, no addedavailableflag. Focus9tests4.103s pass;full760tests126.323s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-artifact-confinement-final-01/. Code/test/spec/plan/task/docbackup C:/Users/lenovo/AppData/Local/Temp/facebook-artifact-confinement-20261003-184322/.

Applied afteridentity+idle/queue0/cancelling0/available checks:onlyownedAPIpid8148 stoppedthroughprocesshandle,logs/configsbeforebackup C:/Users/lenovo/AppData/Local/Temp/facebook-artifact-confinement-reload-20261003-185547/. ExistingSTARTexit0/reusedemulator+UI/newAPI;actualAPI8765/v2proxy8766 numericCSVboth200/178253byte/hash=ondisk. Posthealthready/schedulerrunning/workeridle/queue0/leaseavailable/configbytehashunchanged. No repeatcapture or livepathfaultinjection. T087declaredscopecomplete;T009source/localreviewclosed using logicalalias8case proof,physicalfixture,manifestrecoveryT060+T010/T011 andparent release/localhistoryT053 snapshot;NOTindependentT057/securityapproval/allOSfilesystem guarantee. Mainagentreview only, nativeMac/hostedCI/independentreview remainseparate.


## Request-local health ADB batching and measured benefit — 2026-10-03

Initial realHTTP6samples median445.709ms suggested health subprocess overhead; source3workers eachstatequery plus activeboot/proxy=>5ADbcalls. Added lazy ContextVar holder onlywithin health() worker-snapshot list, tokenresetfinally includingexceptions; realadb_snapshot usesonevalidatedunique device-list, boot/proxy queries/timeouts/errorsemantics unchanged. Directsnapshot outsidehealth independent. Injected/mocked snapshotprovider bypasseslazyinventory withoutrealdeviceprobes. Malformed/duplicate/unknownstate/header/exit/OSError/TimeoutExpiredinventory=>oldindividualstates, oneboundedextraquery cost onfallback (no inventedhealthystate). No TTL/persistentcache/paralleltenants/SDKversion/waitchange. Concurrentrequests ownscope, snapshots not continuousfreshness guarantee.

Tests initially3redbatchexpectations(noinventory);5newmethods cover fewercalls/freshnextrequest/directoutsideprobe,invalidlist+proxytimeout preservingdevice,failedinventory3cases,2concurrentrequests/barrierseparateinventories,exceptioncontextcleanup/noinjectedproviderprobe. Focus74tests3.852s plus5finalbatchtests pass;full765tests131.889s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-health-adb-batch-final-01/. Source/test/spec/plan/task/APIUsagebackup C:/Users/lenovo/AppData/Local/Temp/facebook-health-adb-batch-20261003-191920/. No independentlyattributedreview.

Samehost/config/SDKadb/binary/device,6alternatingold/current Managerhealth pairs readonly actualADB, fullreports equal EVERYpair: oldmedian475.905ms/5commands vsnew325.145ms/3commands (~31.7percent wall reduction on this measured method). Originaltoken-freeprivatebenchmarkrecords/hashes C:/Users/lenovo/AppData/Local/Temp/facebook-health-batch-paired-20261003-192350.json. Runtime/jobs empty in controlledManager so no capture/queue interaction; no CPU/RSS/totalprocessmemory metric or FacebookE2Espeedup claim.

Applied viaownidleAPIreload onlyPID46768 verifiedPython/projectpath/creationtime+queue0/cancelling0/available;logs/config backup C:/Users/lenovo/AppData/Local/Temp/facebook-health-adb-batch-reload-20261003-193739/. STARTexit0/reusedemulator/UI;actualHTTPpostapply6samples median312.0365ms, source C:/Users/lenovo/AppData/Local/Temp/facebook-health-batch-live-20261003-193830.json (observational notalternatingHTTPcontrol). Posthealthready/schedulerrunning/idle/queue0/leaseavailable;existingCSVproxyhashunchanged/cancelresult409/configbytehashunchanged. No duplicatecapture/livefault injection/accountreset/globalwatchdog. T088 complete endpointscope;T013-T015 broadercapture/resourcebeforeafter/native/independentacceptance remainseparate.


## Local source handoff and packaging command coverage — 2026-10-03

Reviewed tools/export_source.py (fixedwhitelist/textonly/no runtimetraversal/exclusiveZIP) and executed the same local packaging command foundinCI. Generated C:/Users/lenovo/Desktop/facebook/dist/facebook-source-20261003-194538.zip,839071bytes. Exact187whitelistfiles+SHA256SUMS=188uniqueZIPentries;CRCtestpass,everyentryhash/current-sourcebyteschecked (LFnormalizationonly.sh/.command),shellUnix755attributesverified. No runs/api-jobs/api-batches/exports/evidence/logs/venv/platform-tools/accountlocalconfigs/APK/MITM/PCAP/PEM/keyentries. SourceZIP SHA256dee75e45c24c170bf1e44bd49288abff4862b757c8ff6899b1814ce5252ca062. Redactedproof C:/Users/lenovo/AppData/Local/Temp/facebook-local-source-delivery-20261003-195042.json. This is datedimmutableLOCAL snapshot,not pushed/published/releaseapproved/no credentialabsenceguarantee. This subsequentdocappend is outside thatsnapshot; regenerateafterfuturefinalsource changes,donot overwritearchive.

Current765fullgate includeslocalunitdiscovery/pipcheck/Node/Python/PSAST checks, packaging nowexecuted. CIreferences36testmodulesallcurrentfilespresent. LocalPython3.13.12 versusGitHubworkflow3.12, so reportlocalcommandcoverage not exactCImachine/runtime/nativeMac equivalence. T017overallremainsopen pendingits broadernative/independentrequirements; no falseclosedcheckbox. No sourcecode/testchanges,duplicatecaptures/APIrestart/systeminstall/publication/historycleanup this step.


## CI runtime availability facts — 2026-10-03

Read-only `py -0p` lists registered Python3.13 and AstralCPython3.11.15; project.venv verified3.13.12. No registered3.12 observed; this is launcher inventory only,NOTexhaustive proof no portable/unregistered3.12 exists. No runtime/dependency install,version switch or globalCodex/config change. Workflowrequires3.12, so current765test gate + localpackaging remain command/supportedprojectruntime evidence,NOTsame-versionhostedCI. ExactCIruntime/nativeMac andindependentreview remainseparateunmetacceptance. Do not repeat3.13suite to manufacture3.12proof.


## Windows 单次真实资源基线（2026-10-03）

任务 `fb_80a78d0601db4a8a`：历史 Reel 样本，单次提交，无抽样上限或等待改动。采集耗时 112.593 秒；采样窗口约 113.84 秒、40 个快照。结果 2 人/报告总数 2、目标匹配、terminal、差额 0，进程 stopped、传输 runner-owned；观察脚本退出码 0。

| 观察范围 | 观察到的 CPU 秒 | 采样工作集峰值之和 MiB | I/O 读取传输 MiB | I/O 写入传输 MiB |
|---|---:|---:|---:|---:|
| api | 7.000 | 58.91 | 57.69 | 40.15 |
| capture-tree | 25.641 | 499.91 | 74.35 | 40.00 |
| emulator | 191.000 | 1817.88 | 204.73 | 40.38 |

限制：采样后等待 2 秒，加上查询时间，实际间隔并非严格 2 秒。短命进程和两次采样之间的峰值可能漏采；观察到 27 个捕获树进程，不是所有曾启动进程的全量证明。API/模拟器用窗口计数差值；新捕获树进程用最后观察到的累计值。工作集相加含共享页，不是系统物理内存总量；I/O transfer 包含文件、管道、网络，不能叫物理磁盘吞吐。模拟器指标含 Android 后台活动；API指标含观察脚本轮询和用户页面请求。多核 CPU 时间可超过墙钟时间。

这是一份单次资源基线，不是优化前后对照或失败率统计，也不足以完整关闭 T013–T015。不能据此减少模拟器 CPU/RAM、缩短稳定等待或宣称采集提速。生产代码和既有配置未改；未安装 psutil。未重复提交、中断/杀死采集、重启环境或做压力测试。

证据目录：`C:/Users/lenovo/AppData/Local/Temp/facebook-live-resource-20261003-212126/`，包含 sample.ps1、ownership.json、samples.jsonl、summary.json、observer.log；报告不保存用户姓名、账号凭证或命令行。当前 API ready、scheduler running、Worker idle、队列 0、lease available。


## T087 batch artifact metadata completion — 2026-10-03

Reproduced UI/API inconsistency: downloads rejected a child junction outside batch root but batch_progress() direct stat marked same alias ready. Updated batch artifact metadata to reuse _checked_artifact_path; normal files remain ready, escaping/linked child reports size0/not-ready, download remains rejected. Focus76tests11.870s pass, including artifact/physical-boundary,Manager,Http/file-sharing. Lastfull760test gate precedes this targeted batchmetadata fix; full current gate pending. No live link mutation, real job changes, config changes or service restart.


## 批次元数据修复已应用（2026-10-03）

读回 run-20261003-batchmeta-final-01 原始测试日志：765 tests / OK(skipped=2)，报告在21:56完成；之前“full current gate pending”记录被本条取代。确认旧API创建于19:37，早于21:52源码修复，不能据旧进程health宣称已加载。核对本项目Python监听身份/创建时间及idle、queue0、cancelling0、leaseavailable后，只重载该API；现有START退出0并复用模拟器/UI。日志和配置及本记录修改前备份：C:/Users/lenovo/AppData/Local/Temp/facebook-batchmeta-apply-20261003-221628/。

应用后API ready、scheduler alive、idle/queue0/leaseavailable；已有Reaction950、Share16/terminal结果可读，取消结果仍409；UI代理CSV与磁盘哈希一致。配置字节未变。批次链接拒绝/metadata不可用由本版临时夹具及完整门禁验证，未对真实批次插入链接。不重跑已覆盖测试，不重复Facebook采集；本次是已通过补丁的运行应用，不是新增性能或独立审查声明。


## 用户授权的独立复核与测试分支提交（2026-10-03）

用户明确授权此前提出的测试分支提交/推送及只读独立代理；不合并、不强推、不发布。Lovelace(01a10240-a7a6-75a3-9db9-a3ec161b4c32)定向静态审查调度启动/503/health请求局部ADB、artifact/batchmetadata、Reel同对象映射及计时wrapper，未执行代码或读取真实账号/抓包。发现P2：reload发布enabled配置后Thread.start失败，再次同配置reload不补启动。真实Manager临时回归先复现，再只为_worker=None且active enabled/no lease/failed/orphan的分支补启动；已登记句柄不替换。70项聚焦PASS，完整767tests129.752s/OK(skipped2)/exit0，日志 run-20261003-independent-reload-fix-01。审查代理回读新代码和负例，原P2解决、限定增量静态PASS；不声称全仓或实机PASS，T057/原生Mac仍独立未完成。

本机恢复点：C:/Users/lenovo/AppData/Local/Temp/facebook-prepush-restore-20261003-225316/，含原脏源码ZIP/私有配置副本/Git历史bundle/diff/状态/候选范围；不是AVD账号或运行数据全备份。使用临时Git索引构建测试分支，原工作分支及主索引不切换、不重置。只修复ENGINEERING_WORKFLOW.md一条EOF空行。当前运行API未因推送准备重启，源码修复需运行加载另行核验。


## Current branch hosted CI — 2026-10-03

Test branch `codex/facebook-maintenance-validation` commit `eb737356c1406b107af9474a2962146976c9483e` completed GitHub Actions Source and local contracts successfully on both `windows-latest` and `macos-latest`. Run: https://github.com/taoduo0428/facebook-reaction-capture/actions/runs/37139716569. This is current-branch hosted CI evidence, not native user Mac/Android/Facebook acceptance, not release approval, and not main-branch merge. Earlier run failures were fixed incrementally: PowerShell5.1/7 UTF-8 BOM compatibility and Windows CI registry replacement fixture; current run success supersedes those failed run outcomes for this test branch.

No PR was created or merged. Main/current working branch remains separate; test branch was pushed only under explicit user authorization. Local service/config/runtime state was not changed by the push.


## Current test-branch CI and independent review closeout — 2026-10-03

Test branch `codex/facebook-maintenance-validation` commit `eb737356c1406b107af9474a2962146976c9483e` passed hosted GitHub Actions Windows and macOS contracts: run https://github.com/taoduo0428/facebook-reaction-capture/actions/runs/37139716569. This covers current test-branch source, not main merge/release or native Mac/Facebook.

Independent scoped reviewer found one P2: after reload published an enabled worker, native scheduler Thread.start failure could leave `_worker=None` while a same-config reload returned success without retry. Added guarded retry only when active worker is enabled, `_worker is None`, and no uncertain/failed/orphan blocker; retained registered handles and no duplicate scheduler. Focus70 passed, current full local gate before this latest branch CI was 767/2skip, and reviewer reread returned scoped PASS/no new actionable finding. A later Windows CI failure exposed PowerShell UTF-8 BOM and then a concurrent registry replace conflict; both were separately fixed and the final branch CI passed.

T080 is now independently reviewed in scope. Native Mac, hosted CI runtime as a user environment, full source review and whole-system completion remain distinct claims.


## Mac pairing continuation — 2026-10-04

Source review confirms active chain START.command/setup.sh/shared local/API -> facebook_android_capture.py; legacy macos/facebook_macos.py simulation is only a transport plan, executed=false. Native Mac Shares explicitly unsupported at facebook_capture_api.build_capture_command; PCAP rejected by active runner preflight before native tools/ADB. These are intentional capability differences, not native acceptance or freshly discovered production defects. Five additional no-device contracts cover five URL argv/parser subcases, unlimited/explicit cap, serial/ports/output/artifact binding, unsupported Shares/PCAP, and failed owned-child stop with other handles closed. First run failed two fixtures missing required output-dir; fixed fixtures only. Focused run: 65 tests / 2.450s / OK skipped1 / exit0 (fcntl contention needs Mac).

GitHub CLI reread run37139716569: both hosted jobs success, old branch commit eb737356c1406b107af9474a2962146976c9483e; completed Windows2026-10-04 01:16:54+08:00 and Mac01:18:17+08:00. New tests are NOT included in that CI. Runtime precheck no listeners8765/8766/8081; existing5037/7897 left unchanged. No real ADB/capture/startup/network/account probe, service restart, merge/push/release or source algorithm change. External backup C:/Users/lenovo/AppData/Local/Temp/facebook-mac-parity-20261004-090312/. Current full gate in progress at checks/run-20261004-mac-parity-01; not accepted until report/log readback. See completion-audit.md for source-grounded capability matrix. T081/native acceptance, T016 whole-review and broad performance requirements remain separate and unresolved.


## Mac pairing local gate readback — 2026-10-04

Full current local gate passed/ok=true/exit0: 776 tests in 126.188s, 774 passed and 2 skipped. Eight started command results all exit0 with retained original stdout/stderr files verified; release-path gate189 files/noerrors. Report and full streams at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-parity-01/. Summary unittest tail was truncated, so actual original stderr readback confirmed count/OK. This supersedes earlier local test counts for this checkout only; old hostedCI covers eb737356, not these five new tests. T089/T090 closed only for no-device contracts and source-grounded matrix, not T081/nativeMac/T019/fullgoal. No production behavior was changed or live acceptance executed.


## Mac Shares/PCAP implemented — 2026-10-04

User requested integration, not removal of guards. Mac shared API now forwards collect=reactions/shares/both; local doctor and actual capabilities handler expose implementation support for PCAP. Native runner supports safe share-count-only navigation (no Share composer), source-locked fresh reopen after accepted Reaction phase, fresh Share min-line query guard, terminal/query/error/source validation and shared analyzer/API completeness. Shared analyzer adds optional --share-min-line (default1 preserves Windows); earlier same-source Share users/terminal pages excluded from new phase while target/Reaction proof remains. Share total/visible gap retained; empty result needs real terminal query, not zero UI rows. Reaction diagnostics and live progress remain in combined mode.

Android PCAP requires existing su0 and /system/bin/tcpdump, never Mac host sudo/Windows exe, no install/ADBrestart. Register unique path/PID marker before start; verify exact executable/run-specific path before signals, bounded SIGINT/SIGTERM stop, no signals to mismatched PID; preserve uncertain remote evidence and record cleanup failure in runner status for API lease protection. Stream SHA256 after pull, verify PCAP header + remote checksum, atomically publish raw-network.pcap and metadata; only then remove this run's two known remote files. Missing/corrupt requested PCAP cannot be successful capture. Cancellation143 calls cleanup and preserves partial status; signal/ownership/transport behavior in fixtures is not native Android proof.

Focused current122tests passed; existing default/Windows/analyzer tests plus active Mac tests. First syntax/legacy unsupported fixtures repaired before acceptance. Current full gate792tests/132.082s/OK skipped2/exit0, command streams and report read back at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-integration-01/. Same Windows runner byte snapshot unchanged; only narrow Mac argv/capability + opt-in analyzer behavior shared. Generated remote shell syntax passes GitBash -n (not remote execution). No real devices, services or login touched; 5037/7897 PID unchanged and 8765/8766/8081 remain unbound.

Nine public source/test/docs files committed/pushed ONLY existing test branch from isolated temp clone: ea522be0b9efa7fe3e5884f52e709d472d4e361b, CI run37168276769 currently running. Current dirty checkout/branch and main not switched/merged; no forcepush/release. Prior five Mac contract tests from earlier turn also included, old capability exclusions replaced by new contracts. Exact source ZIP C:/Users/lenovo/Desktop/facebook/dist/facebook-source-20261004-093311.zip:190entries/189manifest hashes verified, SHA256=293b5de5a64dc21fa55c9952b3b6a8a96c27610c8ac76e93fc0a3d4c59e44db7. SHA256SUMS is actually facebook/SHA256SUMS.txt; initial verifier's wrong filename fixed, final CRC/entry hash/code readback succeeded. ZIP is local handoff, not published release.

Rollback original source/tests/docs: C:/Users/lenovo/AppData/Local/Temp/facebook-mac-integration-20261004-091112/; turn-scoped implementation.diff/current-sha256.json preserved. Restore only changed files from that snapshot, not git reset/clean; service reload requires rechecking idle ownership. Main executor second-pass inspected source boundaries; NOT independently attributable whole-system review. Native Mac/real Android/Facebook GUI/ABI/CA/Frida/PCAP/Share visibility acceptance unavailable; T081/T019 full goal stay open. Current local implementation supersedes older "Mac Shares/PCAP unsupported" capability statements, not native acceptance gaps.


## Hosted CI closeout — 2026-10-04

Pushed isolated test branch commit `ea522be0b9efa7fe3e5884f52e709d472d4e361b`; GitHub Actions run `37168276769` completed success on both `windows-latest` and `macos-latest` (Windows 2026-10-04 09:35:01 CST, macOS 09:36:01 CST). CI validates source/fixture contracts only; it does not validate a real Mac login, Android root, Frida/CA, Facebook visibility or PCAP transfer. Current worktree remains on `codex/stabilization-20260915`; no merge, release, service restart, or device mutation.


## Review correction — 2026-10-04

The preceding claim that standalone Shares returned shares-terminal was incorrect. Source at ea522be already returns reaction_reason or terminal, so standalone Shares succeeds. Commit57fd2b0 added an unconditional terminal return after Shares; it was unnecessary for standalone and discarded the run_collection bounded Reaction return in combined mode. Existing final status separately retains pagination_stop_reason; no claim that an actual bounded capture was falsely published as full completion is established.

Three-revision isolated method replay confirms: ea522be standalone=terminal/combined_bounded=scroll-budget;57fd2b0 standalone=terminal/combined_bounded=terminal;165e358 restores original outcomes. Added bounded regression failed on57fd2b0, passes after removing its three-line change. Existing standalone regression retained. No other production behavior changed: current runner matches ea522be bytes after line-ending normalization. Evidence/backup C:/Users/lenovo/AppData/Local/Temp/facebook-review-correction-20261004-094504/ (outcome-evidence.json). Focused35 tests passed. Prior gate mac-integration-02 passed793/2skip but did not cover this bounded return, demonstrating why passing CI/local tests were insufficient evidence for that earlier claim.

Corrective test branch commit165e35862d15ffa6914c7c00760072363465ca18, current CI run37169028745. No forcepush/main merge or runtime/device changes. Full local correction gate and current CI awaiting readback; original Mac/Android/Facebook live acceptance and independent whole-system review remain unproven.


Correction gate readback: current full local suite794 tests/130.374s/OK skipped2, reportpassed and all command returncodes0 with original stream files checked. Current local handoff dist/facebook-source-20261004-094855.zip contains190 entries,189 verified SHA256 manifest rows; current runner/tests bytes checked. Archive SHA256962c08cb1cc0326dc7634c746039dba0406c5284563665eb8cfd43a39487c8f6. Older ZIPs remain historical, not removed. Current hosted CI37169028745 is still running at this ledger update; do not infer pass from earlier commits.


Correction hosted CI completed: run37169028745 at commit165e35862d15ffa6914c7c00760072363465ca18 succeeded for both Windows/macOS; gh watch exit0 and final job status readback confirmed. Current local full gate794/2skip and source ZIP094855 are the correction evidence. T096 closed for those checks/handoff only. No independent reviewer/native Mac/real Facebook acceptance is implied; overall maintenance goal remains active.


## PCAP producer lifecycle finding — 2026-10-04

Confirmed in a no-device fixture: stop_pcap initially accepted _pcap_state=stopped before requesting any signal, then published a valid PCAP/hash as finalized. This can conceal premature tcpdump exit; header/checksum do not prove duration. New test expecting rejection failed with CaptureError not raised on the old source. Minimal repair requires owned producer before controlled stop; unexpected exit fails with remote evidence retained. Existing cleanup continues restoring the owned proxy and execute marks exit2/completion_claim_allowed=false/partial_evidence_retained=true via cleanup_errors, retaining existing API cleanup protection. No actual crashed Facebook/device capture claimed.

Focused63 tests passed (Android runner + cancellation/transport); normal SIGINT/hash/publication still covered, mismatched PID/timeouts still fail. Test-branch804be917e4b9b0386e0df9096fd43990ca127f98 contains only runner six-line change plus tests; current CI37169519372 pending. Full local gate run-20261004-pcap-lifecycle-01 pending. Backup C:/Users/lenovo/AppData/Local/Temp/facebook-pcap-lifecycle-20261004-095433/. No new polling, shorter waits, automatic retries, Windows script change, live ADB calls, service restart, main merge or source release. Guard detects pre-stopped producer at finalization, not all packet drops/disk faults; no packet-loss-free guarantee.


## PCAP lifecycle current local verification — 2026-10-04

Current full gate run-20261004-pcap-lifecycle-01 passed796 tests/128.222s/OK skipped2/exit0. report.json and retained original stdout/stderr paths were reread, all command results verified exit0. Current archive C:/Users/lenovo/Desktop/facebook/dist/facebook-source-20261004-095810.zip:190 unique entries/189 SHA256 manifest rows, CRC pass, current runner/tests exact bytes verified; archive SHA256 d360eac587c726a974f4ba5cd7492d954adfbed42166cf87a2f99effc8c5295d. Windows CI37169519372 at804be917e4b9b0386e0df9096fd43990ca127f98 passed; macOS still running at this snapshot. No native acceptance inferred.


## PCAP lifecycle CI closeout — 2026-10-04

Current test-branch804be917e4b9b0386e0df9096fd43990ca127f98 run37169519372 completed success on both Windows and macOS. Final gh job readback confirms Windows completed2026-10-04 10:00:36+08:00/macOS10:01:02+08:00; watch exit0. Current local full gate796/2skip and ZIP095810 verified as above. T098 closed only for these declared code/fixture/CI/source-package scopes. Port checks retain proxy7897 PID11904/ADB5037 PID25244, no8765/8766/8081 listener; isolated test clone clean; no main merge/release/device/service changes. Current runner finalization rejects premature tcpdump exit, but packet loss, permission behavior and actual ARM64 Android/Mac runtime remain unaccepted without real-platform evidence. Overall goal/T019 and independent whole-review not completed.


## Delivery consistency audit — 2026-10-04

Release manifest189 entries compared across current local worktree, source ZIP095810 and isolated remote-test clone804be91. All application/test files match remote branch after line-ending normalization. START.command/CHECK.command ZIP differs from raw local bytes solely because tools/export_source.py intentionally normalizes .sh/.command to LF and sets100755; this is not a code drift defect. Five feature docs (spec/plan/tasks/verification/completion-audit) lagged remote, and latest ledger append naturally differs from older ZIP. Synchronize only these five docs, retain all old records and explicitly reconcile current summary; new public package rebuilt afterward. Source evidence delivery-consistency.json in external PCAP backup; no whole-code review/new runtime test claim.


## Confirmed Mac API/runner output handoff repair — 2026-10-04

Current real run_capture_job opens attempt/capture.log as stdout before Popen. Active Mac CaptureRunner.execute formerly rejected every nonempty output directory, including that legitimate API log. A real child process invoking the actual runner with mocked device/preflight/collection failed exit1 (--output-dir new or empty) on old code instead of the expected cancellation143. This is an API-startup contract defect, not a UI/permissions hypothesis.

Mac build_capture_command now forwards internal --api-log. Runner permits only sole regular non-symlink capture.log whose lstat dev/inode equals inherited stdout fstat dev/inode; other entries, wrong descriptor, empty API directory and standalone existing log remain rejected. Real API/Popen/redirected file/runner/cleanup/status test now exits143 and writes status exit143/completionfalse/cleanuperrors[]; real Facebook/device functions are mocked, Windows-only fcntl shim used in that child where necessary. No actual Android capture, nativeMac or storage durability proof claimed. Added ownership/mismatch/existing-file/symlink guards; focused84 passed with1skip because local Windows cannot create the temporary file symlink (macOS CI runs that case). Windows argv/script unchanged; no clearing API log or allowing arbitrary reused output directories.

Test branch current commit c676363d601da8dc89f4ea1f76f185a0834d438c, run37170610196 pending. Local full gate run-20261004-mac-api-log-01 pending. Backup C:/Users/lenovo/AppData/Local/Temp/facebook-mac-api-log-20261004-101442/; source changes3files reviewed in isolated clone, no reset/merge/runtime/device changes. Prior796tests/CI only establish earlier source; they did not exercise the API-owned stdout directory handoff.


## Windows CI timeout fixture correction — 2026-10-04

Current c676363 Mac API integration passed macOS CI run37170610196; Windows failure was tests.test_maintenance_gate.test_timeout_retains_output_and_is_failed temp-directory cleanup WinError32 on retained stderr log, not a Mac API test failure. Raw failing job111342633951 log retained externally. Live owned4second dummy process observation showed venv Python launcher parent51824 -> actual interpreter child16348; fixture exited naturally, no kill/production service call. Existing gate documents direct-child-only timeout, so original30second sleep via venv redirector incorrectly exercised an extra process and could hold inherited logs beyond parent termination.

Fixture now uses sys._base_executable (fallback sys.executable) only for the stdlib timeout helper, preserving1second deadline/timedout/failure/output assertions. Production gate unchanged; no new descendant kill or weakened cleanup assertion. Three repeat timeout cases plus entire maintenance_gate module29tests passed. Source production c676363 and fixture-only c7ba7d4af1366ae96288fdd4439ac85fff426498 pending currentCI37171037469/full local run-20261004-mac-api-log-02. Prior local800/3skip/125.533s passed and raw streams checked, but current fixture-only change has separate verification below. Third Windows skip is temporary symlink permission check; true native file/GUI/device acceptance not inferred.


## Mac API log handoff final verification — 2026-10-04

Current local gate800 tests/123.472s/OK skipped3/exit0, all original command streams and report read back at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-api-log-02/. Production API/runner fix c676363 plus timeout-fixture correction c7ba7d4af1366ae96288fdd4439ac85fff426498 passed both hosted Windows/macOS CI run37171037469 (Windows completed2026-10-04 10:29:29+08:00; Mac10:30:36+08:00). Test timeout cleanup failure from earlier run retained and no blanket retry/skip/production cleanup expansion used. New file-link guard test unavailable on local Windows but executed on macOS CI; three local skips not native business acceptance.

T100/T101 closed for actual API->Popen->stdout/runner/status fixture and declared local/CI/source handoff scope, not all-platform/capture success. These changes fix the default Mac Reaction entry too, not only new Shares. No real device/account/Frida/CA/bootstrap/version/proxy changes, main merge or release. Existing Windows command path is unchanged; gate production code unchanged. User-facing same API payload remains, internal --api-log is not a JSON API parameter. Whole independent review, broad performance matrix and native Mac business acceptance remain incomplete.
