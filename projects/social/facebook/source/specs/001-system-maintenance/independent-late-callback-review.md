# Independent late-callback storage reason review

Date: 2026-10-02. Reviewer Lorentz, 01a0fc8e-9a8f-7893-86b2-da76ea06f795.
Bounded read-only comparison against the timestamped late-callback source backup.
No code/test execution, file edit, network/device/private runtime reads or delegation.

Limited-pass conclusion: current Manager's storage-reason registry registration,
release/restore guards and registered-error late-callback coupling are supported
by source. Existing no-storage-membership branches retain their prior behavior.
Main task executed focus69/full727(2skip); reviewer did not execute those checks.

Current reviewed facebook_capture_api.py locations:
- 2797: registry initialization, empty memory-only dict.
- 2819–2827: persistence=True registers a reason; ordinary block does not delete it.
- 3460–3464: same-owner release reasserts registered storage reason rather than clear.
- 3465–3484: other-owner release includes registered blockers and retains their reason.
- 3915–3917: same-job restore cannot clear stored reason/orphan or dispatch.
- 3922–3942: same-Worker sibling storage block prevents clearing that Worker marker.
- 3824–3833, 4104–4116: transport-success/stopped callback flows use those guards.
- 4985–5008: finalization/result/recovery failures register their reason and orphan.

Test design: tests/test_facebook_capture_api.py:1267–1325 covers three fault classes,
two late final callbacks, Worker restore and standby assignment rejection; :1327–1348
covers another clean task's release/restore not erasing reason or same-Worker orphan.
These are local temporary-fixture assertions, not native/real-recovery acceptance.

Open boundary: no registry reconstruction guarantee across restart; currently an
empty new registry is populated only by current-process fault registration, while
loader relies on process/transport evidence. No supported explicit acknowledgment/
reconcile removal protocol is proven. Stop/restore/re-enable are not persistence
confirmation. Native platform/operator/crash durability, all-write-failure restart,
E2E performance and whole-system completion remain unverified.
