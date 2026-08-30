# World Engine Narrative Benchmark 0.1

## 1. Claim boundary

This benchmark is required before promoting `shadow` to `enforce` or claiming improved story/prose/dialogue quality. Deterministic unit tests are not evidence of literary superiority.

## 2. Corpus

- 30 scenario families in `NARRATIVE_BENCHMARK_SCENARIOS_V402.json`.
- 4 independently authored authoritative state variants per family.
- 120 cases total for the first complete evaluation.
- The 50-turn continuity family must be executed as a linked sequence, not disconnected excerpts.

Each case freezes the same authoritative state, known/unknown facts, player input, style profile, voice profile, world revision and model settings across compared systems.

## 3. Evaluation stages

### Stage A — component ablation

1. current 4.0.1/4.0.2 baseline policy;
2. baseline + voice anchors;
3. baseline + DialoguePlan;
4. Director + DialoguePlan;
5. Director + DialoguePlan + quality gate.

Change one component per arm. Use the same authoritative packet and decoding settings.

### Stage B — final blinded pairwise comparison

Compare the best valid candidate against baseline. Include a third external-inspired configuration only when it is executable under the same authority/cost constraints.

### Stage C — promotion gate

`enforce` is eligible only when:

- hard correctness failures = 0 on the promotion set;
- player-agency violations = 0;
- secret/knowledge leakage = 0;
- candidate pairwise preference exceeds baseline with a reported confidence interval;
- no critical category regresses materially;
- latency/cost remains within the declared operating budget.

No fixed win-rate threshold is asserted as scientifically universal. The release owner must preregister the threshold before scoring.

## 4. Hard correctness

Count separately:

1. authoritative factual errors;
2. secret leakage;
3. player-authorship violations;
4. wrong speaker knowledge;
5. wrong relationship state;
6. wrong world state;
7. mechanics/debug leakage;
8. POV violation;
9. tense violation.

Target: 0 in every class.

## 5. Human narrative dimensions

Pairwise reviewers score which passage is better interactive RPG narration, plus tie:

- prose quality;
- dialogue naturalness;
- voice distinctiveness;
- subtext;
- emotional credibility;
- scene pacing;
- description quality;
- immersion;
- coherence;
- interest;
- character consistency;
- non-repetition;
- player agency and actionability.

“Prettier prose” and “better interactive RPG narration” are separate questions.

## 6. Blinding and randomization

- Strip packet IDs, model/system names and telltale formatting.
- Randomize left/right position independently per case/reviewer.
- Do not show reviewers which arm is new.
- Use at least 3 independent reviewers per case for the first full run.
- Report raw counts, win/tie/loss rates, confidence intervals and agreement statistics.
- Preserve all source packets, outputs, hashes and rating records.

## 7. Automated diagnostics

Use NQR only as diagnostics: word/sentence/paragraph distributions, repeated four-grams/openings, recent-output similarity, cliché/catchphrase/motif counts and dialogue ratio. Do not collapse these into a universal prose score. Generic LLM judgment may be supplemental, never the sole quality definition.

## 8. One-pass versus multi-pass experiment

Measure five policies independently:

1. direct one-pass;
2. one-pass with internal self-edit instruction;
3. generation + deterministic lint;
4. lint-triggered targeted revision;
5. major-scene two-candidate selection + optional revision.

Report model/token cost, wall-clock latency, hard failures and human preference. Ordinary turns remain one-pass unless measured evidence supports otherwise.
