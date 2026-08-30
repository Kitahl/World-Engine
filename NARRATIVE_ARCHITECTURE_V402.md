# World Engine 4.0.2 Narrative Architecture

## 1. Decision

**C — HYBRID METHODS, NATIVE WORLD ENGINE IMPLEMENTATION.**

World Engine borrows tested patterns—storylet eligibility/saliency, semantic dialogue acts, hierarchical render planning, examples as voice anchors and deterministic linting—but does not embed a cloud character platform or make a second narrative engine authoritative.

## 2. Runtime pipeline

```text
AUTHORITATIVE WORLD ENGINE RESULT
              ↓
BOUNDED CONTEXT COMPILER
              ↓
NARRATIVE DIRECTOR + STORYLET SELECTOR
              ↓
DIALOGUE PLANNER [only when applicable]
              ↓
STYLE + VOICE + MOTIF PACKET COMPILER
              ↓
NRP-1.0 NARRATIVE RENDER PACKET
              ↓
CHATGPT PROSE RENDERER
              ↓
NQR-1.0 HARD/SOFT QUALITY GATE
              ↓
OPTIONAL TARGETED REVISION
              ↓
ACCEPTED PLAYER-FACING PROSE
```

The Director and storylet selector are one backend stage because both answer the same bounded question: **which already-authoritative material deserves foregrounding?** Style, voice and motif compilation are also one stage because they are presentation constraints, not separate authorities.

## 3. Authority boundary

| Component | May decide | Must not decide |
|---|---|---|
| World Engine kernels | mechanics, state, facts, knowledge, actions, consequences | prose wording |
| Context compiler | bounded relevant projection | new facts or outcomes |
| Narrative Director | saliency, foregrounding, pacing target | what mechanically happened |
| Dialogue planner | speech act, objective, authorized reveal/conceal plan | unacquired knowledge or exact line |
| Packet compiler | style/voice/motif/length contract | world truth |
| Model renderer | wording, paragraphing, surface dialogue, sensory realization | authority, secrets, player authorship |
| Quality gate | local detectable violations and diagnostics | complete literary judgment or full semantic entailment |
| Accepted-output recorder | consume selected persistent beat/motif and update semantic dialogue history | accept hard-failing output |

No model-authored passage becomes canonical world state merely because it was displayed.

## 4. Migration modes

| Mode | Player-facing output | Candidate packet | Persistent beat/motif consumption |
|---|---|---|---|
| `off` | current baseline | none | none |
| `shadow` | current baseline | compiled and stored privately | none until explicit accepted recording |
| `compare` | current baseline | independently rendered for blind comparison | none until explicit accepted recording |
| `enforce` | candidate from NRP-1.0 | required | only after NQR hard pass and `record_output` |

Default is `shadow`. A packet is a proposal, not an accepted narrative event.

## 5. NarrativeBeat selection

Persistent storylets are filtered by deterministic preconditions, once/cooldown state, involved entities, scene function, quest/capability requirements and resolution state. Eligible beats receive a utility/saliency score with stable digest-derived jitter for deterministic tie-breaking. An ephemeral fallback beat is generated when no persistent storylet is eligible.

The selector never mutates beat state during compilation. `use_count` and `last_selected_turn` update only when an accepted output is recorded.

## 6. DialoguePlan

Dialogue is planned semantically before surface realization. The plan includes speaker/listener, speech act, objective, topic, relevant beliefs, facts to reveal/conceal, relationship, goal, motive, emotion, subtext, desired effect, interruptibility, recent speech acts and voice anchors.

World truth and speaker belief remain distinct. An NPC can sincerely state a false belief stored for that NPC, but cannot gain an unacquired canonical fact from the renderer.

Literal generated lines are not the cache unit. The cache stores:

1. facts communicated;
2. facts concealed;
3. recent speech acts;
4. recent realization fingerprints;
5. voice state;
6. subtext state;
7. topic/speaker/listener key.

## 7. Voice model

A stored `NPCVoiceProfile` requires 2–5 original example utterances and combines them with compact fields: formality, register, vocabulary, length, verbosity, directness, humor, sarcasm, metaphor preference, dialect strength, idiom, professional vocabulary, hesitation, contractions, swearing, taboos, address forms, catchphrases/cooldown and quirks.

At most three context-relevant anchors are inserted into a plan. Named-author imitation fields and imitation instructions are rejected.

## 8. Style model

`NarrativeStyleProfile` uses enums for POV/tense/interiority and 0–4 scales for lexical complexity, sentence variance, paragraph length, sensory/dialogue/description/metaphor density, figurative language, humor, horror, romanticism, action speed, exposition tolerance, explicitness and environment emphasis. It also permits up to five original positive examples and bounded custom guidance.

This is a renderer-control representation, not a claim that numeric scales measure literary quality.

## 9. MotifThread

The backend selects motif eligibility from linked entities, scene type, activation conditions, cooldown, use limit and status. The model may realize an eligible motif subtly or omit it when unnatural. The motif is consumed only when the accepted-output call explicitly reports it as used.

Motif realization cannot forecast or canonize a future event. Recurrence metadata tracks use count, last turn, transformation stage and recent surface realizations.

## 10. Quality gate

### Hard, locally detectable

- internal/audit/context/revision/capability leakage;
- explicit player speech/decision/private-thought/emotional-conclusion invention;
- exact withheld-string leakage;
- gross word-budget violation;
- gross second-person POV drift.

### Soft diagnostics

- near-duplicate recent output;
- repeated four-grams;
- repeated sentence openings;
- cliché density;
- repeated “you see/notice/observe” constructions;
- catchphrase and motif overuse;
- descriptive sentence/paragraph/readability statistics.

### Not claimed deterministic

- full factual entailment;
- subtle speaker-knowledge correctness;
- emotional credibility;
- prose excellence;
- human preference.

Those remain semantic/human evaluation tasks.

## 11. Cutscenes

The old `::cs`, `::cam`, `::d`, `::fx`, `::msc`, `::choice` and `::trg` concepts map into a validated hidden `CUT-1.0` packet containing scene goal, participants, beats, dialogue intents, authorized actions/reveals, sensory cues, choices, conditions and ending state. `NarrativeKernel.validate_cutscene_packet` normalizes authored imports, rejects voluntary player-character actions unless their authority is `player_supplied`, `authoritative_result`, or `mechanically_forced`, and embeds the validated structure in the `NarrativeRenderPacket` without exposing command syntax to players.

## 12. Implementation map

| File | Purpose |
|---|---|
| `world_engine/narrative.py` | schema + narrative kernel |
| `world_engine/engine.py` | schema 14 migration and public wrappers |
| `world_engine/turn_router.py` | `narrative.manage`, dialogue plan, context activation |
| `world_engine/turn_policy.py` | baseline-preserving migration directives and agency rule |
| `app.py` | automatic packet compilation and mode-specific response fields |
| `tests/test_v402_narrative.py` | narrative authority/migration/quality tests |
| `scripts/narrative_release_audit.py` | executable release audit |
