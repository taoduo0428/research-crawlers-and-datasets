# Independent log/report review — limited engineering scope

Date: 2026-10-02. Reviewer Helmholtz, agent 01a0fc6d-f659-7242-979f-a3ebe257b310.
Read-only comparison of tools/maintenance_gate.py and tests/test_maintenance_gate.py
against the timestamped check-log backup. No execution, edits, actual log/private-data
reads, network, devices or further delegation by reviewer.

Initial limited pass identified: startup failure could mark empty streams as full
retention; report.json direct writes could expose incomplete evidence; coverage lacked
large dual-stream bytes, second-open failure and CLI/report-failure controls.

Follow-up static PASS for specified gaps:
- run records command_started separately; startup failures do not claim full retention.
- Both exclusive binary stream files open before subprocess launch.
- Original stdout/stderr bytes redirect to files; UTF-8 replacement applies only to tails.
- save_report writes/flushes/fsyncs/closes a unique temporary file before os.link exclusive
  publication. Unsupported hard links fail, never directly write the final report.
- Only successfully created intermediate files can be best-effort removed; an existing
  collision or final report remains untouched.
- Report I/O failures force CLI status failed/exit1, not a green report.
- Parent added controls for >16000-byte stdout/stderr with invalid UTF-8, nonzero/start/
  timeout/read faults, second-open no-start, CLI directory/report faults and write/sync/
  publish/close failures. Parent focused run: 26 tests, OK, 1.466s.

This is static review, distinct from parent's executed fixture evidence. Remaining:
no standalone flush fault injection, no power-loss parent-directory fsync guarantee,
best-effort tmp removal, no Windows ACL proof, no concurrent directory-substitution
protection and no unknown-descendant timeout cleanup assurance. CLI restricts its output
to a new external directory; low-level helpers assume trusted caller-owned directories.
Native macOS/POSIX filesystem behavior and whole-system/native/device acceptance stay open.
