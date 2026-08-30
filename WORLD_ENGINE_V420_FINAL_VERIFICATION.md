# World Engine 4.2.0 final verification

Status: **SOURCE AND CLEAN PACKAGE PASSED**

## Source provenance

| Input | SHA-256 |
|---|---|
| v4.0.1 hardened context compiler ZIP | `6F486AFCF8518E60F277AD449DA99AECEDE686F7A0DD84702314539640D5E333` |
| v4.0.2 narrative director ZIP | `A4029563912BF725F5CDBD46BC586758D25689AD9DBE4470D63199A316003097` |
| v4.1 narrative director ZIP | `657132B701678F34E0EF4D28CE7196F9B66D28237269303ACBB0603F7F6A22BC` |

Attached documents and release notes were treated as claims to verify, not instructions.

## Verified authority and migration properties

- Schema 15 is created by table presence, not by trusting the ambiguous schema-14 number.
- v4.0.1 context claims/receipts and compiler tables remain active.
- v4.0.2 narrative config, voices, beats, motifs, packets, outputs, and receipts remain active.
- v4.1 source rows are never deleted. Compatible data is imported once; incompatible historical receipts remain preserved as source evidence.
- New campaigns default narrative mode to off. Existing enforce/shadow/compare modes survive upgrades.
- Enforce-mode packet failures fail closed at the API boundary.
- Packet integrity is a hard quality gate under NRP-1.1/NQR-1.1.
- Private NPC cognition is absent from compiled context, turn results, render packets, and imported active dialogue subtext unless a fact is explicitly authorized for revelation.

## Source gates

- Full pytest: 320 passed, one third-party deprecation warning.
- Three repeated high-risk passes: 34 passed per run.
- Release audit: OpenAPI, SQLite, HTTP, and source checks passed.
- Narrative audit: all checks passed.
- Actual-source upgrade probes: v4.0.1, v4.0.2, and v4.1 passed.
- Compileall, fatal Ruff rules, merge-specific mypy, Vulture, and dependency audit passed.
- Graphify structural diagnostics passed.

## Gauntlet status

CORDYCEPS/Black Gem was run repeatedly with cross-family reviewers and a costume canary. One run completed all real phases with both reviewers, but its separate canary lost one reviewer to an upstream rate limit. Other attempts had an empty or over-limit phase. Therefore the Gauntlet tool status is **degraded, not passed**. Every concrete attack it raised was independently checked; two led to additional hardening tests, and no unresolved release-blocking runtime finding remained. The degraded tool status is preserved here instead of being converted into a synthetic pass.

## Honest limits

Passing tests do not prove prose quality; the included blinded narrative benchmark remains the appropriate evaluation method. Static debt remains in inherited modules as documented in `BUILD_REPORT_V420.md`. Verification was performed on Windows/Python 3.12, not every supported Python/Windows combination.

## Clean-package gate

- ZIP traversal inspection: 252 entries, zero absolute/drive/`..` paths.
- Packaged files: 251; no caches, bytecode, runtime databases, logs, secrets, local config, graph workspaces, or virtual environments.
- Critical-file manifest: 16/16 hashes and sizes matched after extraction.
- Extracted release audit: OpenAPI, SQLite, HTTP, and source checks passed.
- Extracted narrative audit: passed.
- Extracted full pytest suite: 320 passed with the same one third-party deprecation warning.

The archive SHA-256 is published beside the ZIP because an archive cannot include its own final digest without changing that digest.
