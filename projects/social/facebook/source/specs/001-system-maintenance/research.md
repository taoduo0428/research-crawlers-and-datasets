# Research Notes: Current Maintenance Baseline

Date: 2026-10-02

## Current facts

- The worktree contains substantial pre-existing modifications and test relocation; this pass does not reset or normalize them.
- The core flow is local API → JobManager → one active worker → Windows PowerShell or macOS runner → ADB/mitmproxy/Frida → analyzer → JSON/CSV artifacts.
- Existing project rules require strict source/target/completeness/terminal/visibility checks, protected cleanup/lease state, and separate real-platform evidence.
- The recent boundary repair is already present in the worktree: shared local HTTP request checks, strict Facebook hostname validation, bounded request framing, fixed UI proxy authority, and platform-specific remote-shell URL quoting.

## Evidence collected in this pass

- `python -B -m unittest discover -s tests -t .`: 667 tests passed, 2 skipped, exit code 0.
- Focused regression after the Windows quoting correction: 12 tests passed, exit code 0.
- `python tools/maintenance_gate.py --json`: Python compile, Node syntax, PowerShell AST, Git status and unittest checks passed; 667 tests ran, 2 skipped, exit code 0.
- No API, UI, emulator, ADB, mitmproxy, Frida or real Facebook process was started.

## Known limits

Real Windows task execution, real macOS execution, Android/Facebook capture, current login state, current proxy state and device cleanup remain separate acceptance gates. Historical reports do not close those gates. Performance optimization is deferred until a same-condition baseline isolates a bottleneck.


## Runtime snapshot at final local verification

At the current check on 2026-10-02, API 8765 and UI 8766 were not listening. ADB was listening on 127.0.0.1:5037 under the project `platform-tools/adb.exe`; the local proxy listener was owned by `verge-mihomo` on port 7897. No service was restarted or stopped. This snapshot is evidence only for this check and is not a persistent health claim.

## Cross-platform scan

`windows-macos-dev-guard/scripts/audit_cross_platform.py . --format json` scanned 78 files and returned 94 review candidates (235 occurrences): 61 Windows, 4 macOS, 16 pairing, and 13 architecture candidates. The scanner is heuristic; many are intentional platform adapters or CI matrix paths, but T012 remains open until each material candidate is classified against its paired implementation.

Current slice: project-venv full maintenance gate passed 678 tests (2 skipped), 101.289s. See verification.md for exact evidence and remaining gates. Earlier 667/668 results are dated intermediate runs, not current total.

Runtime slice: two pending stopped-job transport restorations incorrectly released global lease after one cleanup. Fixed both release and same-worker restoration predicates; red4 / green8 fixture subcases, focused50 pass, full679 pass (2 skipped). No attached Android device at current readonly check; real restoration remains unverified. See verification.md.

Atomic config slice: four negative fixtures red before persist-before-publish and reload busy-worker guard; 54 focused / 683 full (2 skipped) passed. Operators' registry/config unchanged. reload read-before-lock remains a source-grounded race lead, not closed; T029 and independent/native acceptance remain open.

Reload/setter slice: deterministic old snapshot overwrote live enabled state after newer disk write; moved read+validate+publish inside existing setter lock. Initial red fixture; both-direction green; focus55 / full684 (2 skipped) pass. Earlier stale-read lead is superseded as repaired, independent/HTTP/native/performance review remains open.

Worker HTTP slice: direct config I/O fault dropped response; v2 misreported unreachable backend. Scoped sanitized JSON503 handling added with unchanged200/404/409, direct/proxy negativefixtures and response-write control. Focus68/full687 (2 skipped) pass. Job/manifest I/O and independent/native review remain open. Source-level v2 parallel wording versus serial scheduler inconsistency registered as T034, not fixed by changing concurrency.

Submission/UI slice: single-job queued ghost and partial batch recovery reproduced; persist/commit-before-dispatch now enforced using existing v1 parent metadata. Pending queued-child commit membership guarded while process/lease uncertainty retained; staged failure outputs not deleted. Submission IO503/body408/reply scopes tested through API+proxy, serial wording aligned without concurrency change. Focus87/full695 (2 skipped) passed; lifecycle assignment/finalization IO and independent/native/performance gates remain open.

Dispatch ownership slice: pre-capture manifest failure prematurely published running/attempt/worker state and entered device recovery. Private candidate persistence and assignment-only OSError requeue/event wait now preserve queued ownership; transient storage control proves one capture attempt. Focus63/full698 (2 skipped) pass; finalization IO/observability/independent/native/E2E performance remain open.

Finalization slice: callback storage error escaped before queue/active accounting; scoped OSError boundary now blocks lease/worker takeover and completes local accounting without a second capture. Directcallback/internalpersist fixtures pass; focus64/full699 (2 skipped) passed. Durability recovery/normal completion errors/independent/native/performance gates remain open.

Secondary recovery persistence slice: recovery/checkpoint/failed-manifest OSError escaped failure handling; scoped storage flag now prevents failover requeue and preserves lease block after final callback. Three red/green subcases; focus65/full700(2skip) pass. Operator recovery/restart/native/independent/performance remain open.

Measured local performance slice: _reactor_fieldnames membership set preserves ordered legacy output and CSV provenance; alternating6x 10k dummy fixtures. Postchange medians 26.046->7.704ms (15fields),245.745->28.772ms(60fields),functionpeak +2–3KiB. Focus67/full702(2skip) pass. These metrics do not prove E2E bottleneck or Facebook speed; independent/native/E2E gates remain open.

Batch export schema slice: membership sets preserve ordered CSV lists; paired6x samefixture 5posts x1000rows showed65.407->42.608ms(15fields),335.085->104.031ms(60fields),+2864B tracedpeak. Exact captured exports match pre/posthash; temporary CSV headers/provenance/postscoped identities verified. Focus68/full703(2skip) pass. No disk/FacebookE2E improvement claim; independent/platform/fullaudit remain open.

Independent export review: initial limitedpass exposed provenance/assertion coverage gaps; supplemental actual checkpoint/cross-source+reaction fixtures and assertions were independently statically reviewed as PASS for specified gaps. CI explicit module lists missed maintenance/frontend guards; Windows+macOS wiring and layout guard corrected. Localfocus21/full706(2skip) pass. Requirement-level completion audit reopens T003/T004 raw-output/leakage obligations; no whole-objective/native/hosted/E2E completion inferred.

Full-log slice: optional external new run directory stores exclusive original command byte streams; startup marked separately, timeouts/faults remainfailed; report staged/fsynced/closed then exclusive-link published. Focus26 pass; independent static followupPASS with declaredACL/pathrace/descendant/powerloss/native limits. Full723(2skip)gate passed, original streamfiles/readback+hashverified at external run-20261002-full-log-final-194926. Historical tailrecords not retroactively closed; whole objective still active.

Bounded release/history content audit: 182 whitelisttexts/375reachableobjects/279blobs, no caps/skips/read errors, six synthetic-controlled signatures. 13matches/5unique fingerprints traced by parent to fake flow/privatekey rejection fixtures or doc placeholder; no credential values in retained reports, no live validation/cleanup/history edit. Independent review source not read due output-safety limitation, NOTPASS; T057/broaderT053 open. 6 fixturetests pass; whole goal/platform/recovery/E2E security remain incomplete.

Resultcommit slice: private completedcandidate persisted before livepublication; postcapture-return OSError avoids device recovery/cleanup/requeue and retains globalblock, faileddiagnostic staged likewise. Five negativephases+positivecommit controls; focus67/full725(2skip)pass with fullstreams. Fresh independent review re-read affirms localfixes but restart allwritefailed and duplicatecallback/storage reason-lock gaps remain T059/T060. No native/E2E completion claim.

Latecallback storage slice: separate memoryreason registry guards own/otherowner release and own/sameWorker restore; three faultclass repeatedcallback red/green and anothercleanowner fixture pass. Focus69/full727(2skip), original16streamhashverified. Fresh independent limitedsourcepass affirms coupling, not restart/native/operator acknowledgment. No autoremove/APIendpoint/capturewait/concurrency/userenvironment changes; goal active.

Existing resultstorage marker restart slice: four transportstate redfixtures and unknownprocess diagnostic overwrite redfixture; loader now restores independentmarker before dispatch and preserves it alongside process uncertainty. Ordinary terminal diagnostics still run; actual failurepath+twoManager reconstruction blocked. Focus72/full730(2skip)pass with original16streams/readbackhashed. Noallwritefailedmarkercreation/operatorreconcile/native/E2E guarantee; goal active.

Internal resultstorage reconcile slice: explicit literalTrue, registeredfailedmarker, stoppednopidnohandle/noactive, platformcleanerrorfree, devicefaultresolved guards; candidatepersist before targetreason removal, otherblock retained, failedresults never revived/requeued. Startup doubleblock+queued fixtures confirm firstnostart/lastonestart (mockthread), restartfailed stillfailed. Focus77/full735(2skip)pass original16streamsverified; independentlimitedstaticPASS with operator/native/crash/fullwritesfailed/siblingcoverage limits. NoHTTPCLIentry or actualuserjobreconcile; goal active.

Continuation 2026-10-02: result acknowledgment sibling-isolation matrix first exposed a real omission for registered process handles; `_job_has_uncertain_process_locked` now receives the job id in sibling scans and treats `_running_processes` handles as uncertain. Matrix 12/12, focused 59, and fresh full local gate 743 tests/2 skipped passed. No live service/device/user state changed; native and crash/full-store gates remain open.

Current platform scan refresh: `audit_cross_platform.py . --format json` scanned 78 files with 94 heuristic findings (13 architecture, 4 macOS, 16 pairing, 61 Windows; 235 occurrences), no skipped files. Findings are review leads rather than confirmed defects; native/platform pairing remains open and no broad path rewrite is authorized.
