# Maintenance Quickstart

From the project root:

```powershell
python tools/maintenance_gate.py --json
```

This is a non-destructive gate. It checks current Git state, Python compile, Node syntax, PowerShell AST and the deterministic unittest suite. It does not start services, emulator, ADB, mitmproxy, Frida, browser or Facebook.

For a new change:

1. Read `AGENTS.md`, `STABLE_BASELINE.md` and the relevant platform/API/test docs.
2. Run the maintenance gate and save its result outside runtime data if an audit record is needed.
3. Use `$speckit-specify` for the new or changed behavior only.
4. Continue with `$speckit-plan`, `$speckit-tasks`, implement, verify, independent review and `$speckit-converge`.
5. Add real-platform acceptance only when the configured environment is available and the task explicitly authorizes it.
