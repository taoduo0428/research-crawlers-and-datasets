# Bounded release/local-history content audit

Date: 2026-10-02. Read-only scope; no credential validity/network checks, history
rewrite, file deletion, commit/push/publication or new scanner/skill installation.
Parent-owned temporary scripts and private redacted evidence are outside the project.

## Snapshot and coverage

HEAD: 2f6036d6213f9c6cd1b5efe9381255c245ebf1ff; local reachable commit count: 26.
Initial bounded run examined 182 release-whitelisted UTF-8 files (2,832,293 bytes),
375 reachable objects and 279 blobs (6,768,810 bytes). Maximum per-file size 5MiB,
object cap 2000. No size/count skips, decode skips or read errors occurred.
The Git object inventory uses local rev-list --objects --all, without remote fetch.
Current dirty worktree release content is checked separately from committed history.

Private evidence at
C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261002-release-history-195620/:
- release-inputs.json: exact whitelist source file sizes/content SHA256.
- history-inputs.json: exact object IDs/blob sizes/content SHA256.
- redacted-report.json: bounded-scan counts, rules, local ref snapshot and input hashes.
- redacted-triage.json: parent context classifications, no credential values.

## Detection and triage

Six signature classes: private-key BEGIN marker, AWS access ID shape, GitHub/Slack/
OpenAI token shape, and long quoted credential-like assignment. Each signature had a
synthetic positive/negative control before scanning. These are not an exhaustive secret
scanner or proof that arbitrary secrets cannot occur in code, data or Git history.

13 signature matches, five distinct value fingerprints:
- tests/test_facebook_mac_device.py:35 and three historical versions of the test:
  private-key-header-only fixture whose test requires rejecting it before commands.
- tests/test_facebook_raw_capture.py:23/24/30 and matching historical test blob:
  explicitly constructed fake HTTPFlow header values with roundtrip/redaction assertions.
- API_USAGE.md:136 and two historical documentation blobs:
  short-lived authorization placeholder in a private-forwarder example.

Parent checked exact candidate values against synthetic/example expectations and reviewed
limited source context. Historical candidate fingerprints match current fixture/example
values. All 13 are classified by parent as non-secret fixture/placeholder in this scope;
no usability/authentication test was performed. The candidates were not removed, nor
were false-positive paths blindly ignored. They stay in redacted reports for traceability.

Independent reviewer could not safely read the requested source with its available
read-only output tools while honoring the no-match-print restriction, and returned
needs-verification without reading. Therefore independent classification is NOT PASS.
T057 remains open; this document does not claim independently confirmed absence.

## Verification and boundaries

Parent ran tests.test_facebook_raw_capture and tests.test_facebook_mac_device:
6 tests, OK, exit0. These verify the local fake-flow/certificate fixture behavior,
not absence or validity of arbitrary credentials. Signature scanner runs are read-only;
no fresh whole-suite run is required for audit-document-only changes. Most recent code
full gate remains the separately recorded 723-test run, not a new content audit outcome.

Excluded: unreachable/reflog/detached/index-only/unfetched remote objects, ignored runtime
or account/local config files outside whitelist, binary/non-UTF8 files (none encountered
within selected inputs), arbitrary credential formats and secret validity. Inventory
path per Git object is a hint, not every commit/path occurrence. The output is private
local evidence, not publishable raw logs or a GitHub security approval.

Further audits must re-check exact current hashes. Documentation/task updates following
a scan do not retroactively belong to its snapshot. Broad security/platform/real-device/
operator recovery/E2E performance acceptance and whole objective remain incomplete.
