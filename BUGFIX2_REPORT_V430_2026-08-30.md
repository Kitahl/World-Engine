# World Engine 4.3.0 BUGFIX2 — Selective Merge and Verification Report

## Verdict

The supplied BUGFIX1 archive contained valuable repairs, but it was based on an older 4.3.0 tree. A whole-file or whole-archive overlay would have removed the newer Microsoft Store ngrok trust boundary, bounded clipboard fallback, launcher callback fixes, and their tests.

BUGFIX2 therefore applies only compatible changes onto the current hardened branch. All current security behavior is retained, the supplied defects are closed, and four additional Windows/security defects found during independent review are also fixed.

## Input identity and report correction

- Supplied archive: `world_engine_v4_3_0_OUTPUT_COMPANION_HARDENED_WINDOWS_FULL_BUGFIX1.zip`
- Independently measured SHA-256: `c3025f879c0a64b599b72b19cd44d049bb06c785a84ad6de0fbdd6a406b40b38`
- Independently measured size: 812,076 bytes
- Archive safety: 268 entries, one root, zero unsafe paths, zero duplicate paths
- Supplied reports present: `BUGFIX_REPORT_V430_2026-08-30.md`, `BUGFIX_AUDIT_V430_2026-08-30.json`
- Claimed handoff file absent: `BUGFIX_HANDOFF_V430_2026-08-30.json` was not contained in the supplied ZIP

The supplied `391/391` result describes the older candidate only. It cannot prove retention of the SAFE Store and clipboard regressions that the candidate did not contain.

## Integrated and retained repairs

| ID | Result |
| --- | --- |
| BF-01 | Cloudflare batch entrypoint now calls the present V399 installer instead of missing V398. |
| BF-02 | Launcher and permanent-endpoint code share one cloudflared version, URL, SHA-256, and versioned cache path. |
| BF-03 | A configured non-ngrok, missing, or unknown provider can never fall through to ngrok hostname repair. |
| BF-04 | Existing Cloudflare Windows services receive bounded automatic start requests without storing or reinstalling the token. |
| BF-05 | Companion worker requires and uses the package-private `.venv` interpreter. |
| BF-06 | `run_windows.bat` anchors to its own directory, creates the private environment safely, and uses its exact Python for pip and the server. |
| BF-07 | Current startup already used version-derived 4.3.0 metadata and failure labels; that newer fix was retained rather than overwritten. |
| BF-08 | Static Action audit imports the same `PUBLIC_ACTION_OPERATION_IDS` policy used by exporter and launcher, then requires exact source and checked-in-schema equality. |
| BF-09 | Instruction regression now checks `CUSTOM_GPT_INSTRUCTIONS_V430.txt`, NRP-1.2, publication, semantic review, and fail-closed connection behavior. |
| BF-10 | Active installer and endpoint runtime labels identify World Engine 4.3.0. |
| BF-11 | Permanent endpoint guide documents Store-only ngrok and precise same-provider, fail-closed recovery. |

## Additional defects found by the BUGFIX2 review

1. **P1 — batch parse-time `%errorlevel%` expansion.** Inside the environment-creation block, the candidate could select `py` even when only `python` existed. The launcher now uses execution-time `if errorlevel` semantics. A real `cmd.exe` regression proves the no-`py` fallback executes.
2. **P2 — missing provider identity failed open.** A saved URL with an empty provider could enter ngrok repair. Any configured URL now requires the exact `ngrok_user` provider before ngrok repair.
3. **P2 — Cloudflare recovery used bare `sc`.** Windows executable search can consider the current directory before System32. Recovery now resolves `sc.exe` through `GetSystemDirectoryW`, validates the file, and launches its absolute path.
4. **P2 — Cloudflare installation had the same bare-`sc` risk.** The installer now reuses the same trusted System32 resolver. Repository-wide review found no remaining bare Python `sc` subprocess call.
5. **P1 — release verifier audited stale V420 instructions.** The legacy-named verifier now reads, hashes, size-checks, and validates required markers in `CUSTOM_GPT_INSTRUCTIONS_V430.txt`.
6. **P2 — package handoff had a source-stability race.** The packager now captures the commit and critical hashes, then rechecks HEAD, worktree cleanliness, and hashes after extracted verification before writing a handoff. Concurrently changed builds are deleted and rejected.

Microsoft documents error 1056 as `ERROR_SERVICE_ALREADY_RUNNING`, so recovery accepts the numeric code without depending only on localized text. Microsoft also documents the executable search order and `GetSystemDirectoryW` as the system-directory resolver.

## Verification

- Full source suite: **430 passed, 8 subtests passed**
- High-risk Windows/provider/SAFE Store suite: **70 passed, 8 subtests passed**, repeated **3 times**
- Python compilation of changed sources and tests: **PASS**
- Static Action audit: **21/21 exact**, 28 source operations, zero missing/extra/duplicate Action IDs
- Runtime release verifier: **PASS**
- Fresh database: **schema 16**, integrity `ok`, zero foreign-key violations, 13/13 narrative tables
- Runtime OpenAPI: **21 unique Actions**, 21 non-consequential flags, zero unresolved references
- Diff hygiene: **PASS**
- Independent Terra review: found and verified the batch P1 fix; final amendment review clear
- Independent Sol security review: found and verified both provider/path P2 fixes; final review clear
- Live read-only Windows probe: trusted `C:\Windows\System32\sc.exe` resolved successfully; SCM reported service-not-installed for `cloudflared`

The final packaged-artifact byte comparison, clean extraction, full extracted test suite, Action audit, and release verifier are recorded in the adjacent external `BUGFIX2_HANDOFF_V430_2026-08-30.json`. Keeping the ZIP hash outside the ZIP avoids a self-referential artifact.

## Preserved security invariants

- No direct `ngrok.exe` or `bin.ngrok.com` runtime download path
- Exact Microsoft Store product and package-family enforcement
- Trusted ngrok listener/process ownership checks
- Reparse-safe legacy portable-cache cleanup
- Bounded STA PowerShell clipboard access with bounded Tk fallback and retry backoff
- Positive 21-operation public Action allowlist
- Provider-preserving permanent endpoint repair
- No stored Cloudflare installation token for recovery

## Unverified operational boundary

The following require an installed/live external environment and are not counted as passed:

- actual `cloudflared` Windows service start/restart and service ACL behavior;
- live ngrok, Cloudflare, and Tailscale connectivity;
- live Foundry relay delivery;
- graphical `pywebview` behavior.
