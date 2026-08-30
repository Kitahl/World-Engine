# World Engine v3.7 — Rules Coverage Matrix

Legend: **FULL** = implemented and tested as a generalized kernel mechanism; **PARTIAL** = useful bounded implementation with documented missing cases; **INFRASTRUCTURE** = version/data architecture exists but bundled content is absent; **NONE** = not implemented.

| Capability | v3.7 status | Evidence/test domain |
|---|---|---|
| Seeded attacks | FULL | modifier composition, natural 1/20, replay |
| Saving throws | FULL | success/failure, proficiency, modifiers |
| Typed damage | FULL | raw/applied/mitigation reporting |
| Resistance/immunity/vulnerability | FULL | combined mitigation regression |
| Temporary HP | FULL | absorption and legacy attack integration |
| Healing | FULL | maximum cap, dead-target rejection |
| Concentration | FULL | start, siblings, replacement, damage DC, expiry |
| Structured effects | FULL | modifiers, conditions, stacking, end reasons |
| Action/bonus/reaction economy | FULL | spend/reset/out-of-turn reaction |
| Grid movement | PARTIAL | explicit path, difficult/block/occupied/cost; no opportunity interrupts yet |
| Attack range/cover/LOS | FULL | normal/long range, half/three-quarter/total cover |
| AoE targeting | PARTIAL | radius/sphere/cube/line/cone/cylinder grid approximations |
| Spell slots | FULL mechanism | consume selected level, rollback, rest recovery |
| Upcasting/level scaling | FULL mechanism | data-driven extra parts/thresholds |
| Short/long rests | FULL kernel | atomic WORLD time + recovery + expiry + reactions |
| Dawn recovery | FULL | multi-day skip and profile-free resource owner |
| Death saves | FULL baseline | natural 1/20, stable/dead persistence |
| Advancement | FULL mechanism | grants objects/resources; content tables absent |
| Summons | PARTIAL | temporary actor/scene/combat/initiative lifecycle |
| Transformations | PARTIAL | HP/maxHP/AC snapshot primitive; special rules not encoded |
| Teleportation | FULL primitive | combat cell or WORLD/SCENE location transfer |
| Automatic reactions | FULL baseline | deterministic first eligible reaction |
| Player-choice reactions | NONE | explicit unsupported continuation error |
| SCENE→WORLD CASCADE | FULL | normalized event + same-transaction cascade |
| Legacy `resolveAttack` compatibility | FULL | shared temp HP/mitigation/effects path |
| 2014/2024 gating | INFRASTRUCTURE | incompatible Activity rejection |
| Official SRD content bundled | NONE | 0 imported rows |
| Complete 5e/5.5e game | NONE | explicitly not claimed |

## Test totals

- Existing v3.6 regressions retained.
- Dedicated v3.7 rules tests: **46**.
- Final full-suite count is recorded in `TEST_OUTPUT_V37_FULL.txt` and `BUILD_REPORT.md`.
