# Independent export review — bounded scope

Date: 2026-10-02. Fresh reviewer: Halley, agent 01a0fc5c-6682-7580-9396-485f34113a08.
Scope: _reactor_fieldnames, two write_batch_outputs field-collection loops, and
ReactorFieldnameTests; compared actual current functions with the timestamped
local-perf and batch-perf backups. No source edits, target-code/test execution,
network/device access, private data reads, service startups or further delegation.

## Initial result: limited pass

Under dict[str, ...] and normal normalized key contracts, membership sets do not
produce output headers: original ordered base lists and first-observed append order
remain the output. Batch schema loops use sorted exported rows, as before. Identity,
provenance-value construction and per-post dedupe code are unchanged by these edits.

Independent gaps:
- Legacy list comparison alone did not pin base field constants or full input content.
- Batch tests omitted post_index/post_url/source/reaction provenance-value assertions.
- Two-layer source/reaction vs per-post user identity cases were not covered.
- The checkpoint-named test called only header/writer helpers, not real merging.
- Performance figures were fixture-only and not independent timing, disk or Facebook E2E.

## Reconciliation and follow-up: specified gaps closed, static PASS

Current tests/test_facebook_capture_api.py (reviewed line ranges):
- 4000–4004: deepcopy input-content equality and fixed five base-field empty result.
- 4031–4042: both batch CSVs assert post index, URL, source and reaction, plus job ID.
- 4045–4073: same per-job user with different source/reaction variants; different jobs
  retain different URL/source/reaction/name/provenance and first observed per-job rows.
- 4075–4103: actual _persist_checkpoint_users merges two temporary attempt CSVs;
  validates three identity tuples/header order/extra fields and persisted byte stability.
- 4116–4118: row-count/source/reaction assertions in the single-row writer fixture.

Parent executed expanded schema/layout/frontend/gate focus: 21 tests, OK. Full
project-venv gate after all changes: 706 tests in 108.521s, OK (skipped=2), exit0.
Reviewer did not execute these tests; static opinion and parent runtime evidence are
separate. No whole-project security/runtime/performance acceptance is inferred.

## Remaining limits

Input dict equality checks content, not object identity or key insertion-order identity.
Malformed custom hash/equality key objects and batch non-string keys are not newly
supported by declared string-row contracts. Missing/repeated job IDs, unusual encodings,
CSV special-character/write-crash boundaries require distinct checks. Repeating one
checkpoint fixture proves its persisted bytes remain stable, not crash durability.
Normal field-order local review is closed; native I/O/Facebook/performance and broad
independent Worker/persistence/recovery review remain open.
