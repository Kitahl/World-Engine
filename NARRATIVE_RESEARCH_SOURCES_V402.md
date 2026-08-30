# Narrative Research Source and Adoption Ledger — 4.0.2

## Evidence policy

Priority: source code → official technical documentation → project tests/benchmarks → peer-reviewed research → documented user/professional evaluation → issue/community evidence → marketing. No external prose samples or named-author imitation text are bundled.

| Candidate | Primary source | Extracted method | Disposition | Evidence label |
|---|---|---|---|---|
| World Engine 1.63 | `legacy/World_Engine_1.63.txt` | hidden mechanics, state-driven dialogue, motifs, cutscene semantics | rewrite selectively | verified source bytes; claims unverified |
| Current World Engine 4.0.1 | local base source | dynamic length, concrete prose, agency guard, mechanics visibility | retain baseline | verified code/tests |
| Yarn Spinner 3 | https://docs.yarnspinner.dev/3.1/write-yarn-scripts/advanced-scripting/saliency | storylet eligibility and saliency | copy algorithmic pattern | official docs |
| ink | https://github.com/inkle/ink | authored knots/stitches/variables/conditions | optional authoring/import reference | source repository |
| SillyTavern | https://docs.sillytavern.app/usage/core-concepts/characterdesign/ | first/example messages as voice demonstrations | copy prompt pattern | official docs; WE gain unverified |
| Convai | https://docs.convai.com/api-docs/convai-playground/character-customization/character-description | speaking style and sample dialogue | schema reference only | official docs |
| Façade | https://www.interactivestory.net/ | global dramatic beats plus reactive local behavior | reference only | published project |
| Comme il Faut / Prom Week | https://users.soe.ucsc.edu/~michaelm/publications/mcCoyPromWeekFDG2013.pdf | social state and reusable interaction realization | copy semantic-selection concept | research paper |
| Versu | https://emshort.blog/2013/02/26/versu-conversation-implementation/ | speaker/topic/speech-act planning and interruptions | copy concept | author technical account |
| Re³ | https://aclanthology.org/2022.emnlp-main.296/ | plan, candidate continuation, rerank, consistency edit | selective major-scene reference | peer-reviewed paper |
| Dramatron | https://arxiv.org/abs/2209.14958 | hierarchical logline/characters/beats/locations/dialogue | reference only | paper + professional study |
| DOME | https://aclanthology.org/2025.naacl-long.63/ | dynamic outline and temporal conflict checking | selective consistency reference | peer-reviewed paper |
| Vale | https://github.com/errata-ai/vale | configurable prose rules | optional direct external CLI | source repository/license |
| RapidFuzz | https://github.com/rapidfuzz/RapidFuzz | near-duplicate similarity | optional direct dependency | source repository/license |
| textstat | https://github.com/textstat/textstat | descriptive readability signals | optional diagnostic only | source repository/license |
| LanguageTool | https://github.com/languagetool-org/languagetool | multilingual grammar/style checks | reference/optional separate service | source repository/license |

## Code provenance

No external narrative engine code was copied into the runtime. The new core is World Engine-native Python. Optional `RapidFuzz` and `textstat` are not required for startup; the standard-library fallback remains functional. Vale configuration is bundled as an optional lint profile. LanguageTool is not bundled.

## Unverified until benchmarked

1. Voice examples outperform fields alone for World Engine.
2. Director + DialoguePlan improves human preference.
3. Two-candidate revision improves major scenes enough to justify cost.
4. Any configuration provides “single-author quality.”
5. Numeric style dimensions are more controllable than compact natural-language profiles.
