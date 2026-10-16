# Independent result-commit review — bounded state-machine scope

Date: 2026-10-02. Reviewer Anscombe, 01a0fc80-5d4f-7e33-8f5b-89d35922f711.
Read-only source/test examination; no project execution, file edits, network/device/
private-data reads or delegation. Initial review identified shared completed state
before checkpoint/manifest commit and returned-output I/O entering device recovery.

After independently rereading the edited _worker_loop, review confirms this slice:
- completed candidate is checkpointed/persisted before publishing into shared job;
- returned-capture OSError has a separate local persistence branch, not device recovery/
  cleanup/failover requeue;
- failed diagnostic itself commits before publication or stays unconfirmed;
- final callback runs before the result/recovery persistence block is reasserted.

Static findings support the local repair but not durability of the whole output
directory. Main task subsequently expanded runtime fixtures to include result build
and a normal successful completed-candidate publication control. Parent executed
67 relevant Worker/cancel tests, OK; independent reviewer did not run those tests.

Open boundaries explicitly identified by reviewer:
1. run_capture_job-internal OSError still belongs to pre-return recovery; this edit
   does not classify every local I/O site or all returned non-OSError defects.
2. failed output write may leave staged files; no directory-wide rollback claim.
3. If all postcapture writes fail, old running metadata may remain and restart can
   still requeue once process/transport evidence appears clear. In-memory blocking
   alone is not durable across process crash/restart.
4. A later duplicate callback may release the lease reason, which is not a separate
   persistent storage-failure lock; actual dispatch effects also depend on orphan
   markers and must be independently tested, not assumed from lease text alone.
5. Operator unblocking rules, native device/proxy recovery and all crash points remain
   unverified. No whole-system, security, exactly-once or E2E performance acceptance.

See verification.md and open T059/T060 for follow-up; no issue is hidden by local PASS.
