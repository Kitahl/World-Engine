# World Engine 4.3.0 — Safe ngrok Store Hotfix

Hotfix ID: `V430-SAFE-NGROK-STORE-1.0`

This is a scoped security and release-integration update for World Engine 4.3.0. It preserves the v4.3 context compiler, narrative publication, Companion UI, OpenAPI, and launcher behavior.

## Runtime changes

- World Engine no longer downloads or extracts a standalone `ngrok.exe`.
- Windows installation is pinned to Microsoft Store product `9MVS1J51GMK6` with `--exact --source msstore`.
- The Windows App Execution Alias directory is resolved through the Windows known-folder API rather than environment variables.
- The running WinGet and ngrok processes must report their expected Windows package families through `GetPackageFamilyName`; an existing tunnel is reused only after its loopback listener is bound to an owning PID with the ngrok Store family.
- The configured `msstore` source must report the trusted Microsoft Store identifier and endpoint before installation.
- PATH- or working-directory-planted ngrok and WinGet executables are ignored.
- Installation, source, package-identity, timeout, and alias failures stop without a direct-download fallback.
- The obsolete World Engine portable cache is removed centrally; every existing component of its unresolved lexical path is checked, and locked, unreadable, or reparse-point paths are refused and never executed.
- Imported ngrok configuration is reduced to an allowlisted authtoken in a World Engine-owned minimal config. Tokens are not passed on a child-process command line.

## Release integration correction

The legacy-named `scripts/release_verify_v420.py` remains in place for automation compatibility, but now verifies the current v4.3.0 schema, 21-operation public action surface, NRP-1.2/NQR-1.2 protocols, and the fail-closed boundary for private narrative operations.

## Verification

- Focused startup and safe-ngrok tests: 32 passed, plus 6 parameterized source cases.
- Full repository suite: 409 passed, plus 6 parameterized source cases and 1 dependency deprecation warning.
- Ruff critical-error gate: passed.
- Python compilation gate: passed.
- v4.3 release verifier: passed for OpenAPI, SQLite, HTTP, and source audits.
- Read-only Windows canary: canonical WinGet alias, running package family, trusted Store source, and TCP-listener-to-owner-PID mapping all verified.

An actual ngrok Store installation was intentionally not performed on the operator's machine. The post-install ngrok package-family and version checks are covered with fault-injection tests; a Windows VM installation canary remains the highest-value external release-environment check.

## Threat boundary

Before launch, World Engine requires the canonical WindowsApps path and Microsoft's `AppExecLink` reparse tag. After launch, it checks the retained child-process handle with `GetPackageFamilyName`; the bounded WinGet/ngrok probes and the long-running tunnel all fail closed on an identity mismatch. Before reusing an existing tunnel, it maps `127.0.0.1:4040` to the listener PID and verifies that process family too. This prevents an ordinary executable planted in the working directory, `PATH`, or the expected alias pathname—and a stale standalone loopback listener—from being trusted as ngrok or WinGet.

An attacker already executing as the same Windows user and actively racing replacement of that user's App Execution Alias or configuration while World Engine starts is outside this hotfix's threat model. That account already controls World Engine's per-user configuration and runtime data. A fresh disposable-Windows-VM install remains the appropriate release-environment test for Store publication, alias creation, and the live product-ID-to-package-family mapping.

