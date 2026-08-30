# World Engine v3.9.4 — Final Verification

| Gate | Result |
|---|---:|
| Parent release | v3.9.3 |
| Schema | 12 |
| Source tests | 224/224 PASS |
| Gauntlet-merge focused tests | 6/6 PASS |
| GPT-visible Actions | 30 |
| Unique operation IDs | 30 |
| Always-Allow eligible (`x-openai-isConsequential:false`) | 30/30 |
| Missing object `properties` | 0 |
| Unresolved `$ref` | 0 |
| Raw OpenAPI compatibility errors | 0 |
| Launcher live-schema operations | 30 |
| SQLite integrity | ok |
| Foreign-key violations | 0 |
| Compact GPT instructions | 8000 bytes |

A clean-extraction test result is appended after the final release ZIP is created.

## Patch provenance

The supplied gauntlet manifest was not PASS evidence. v3.9.4 treats its runner as a patch specification and independently verifies the merged behavior.

## External limitations

Real Windows WebView2 audio playback and real YouTube availability/embeddability require network/platform acceptance testing. A syntactically valid 11-character YouTube ID is not proof that the video exists or permits embedding.
