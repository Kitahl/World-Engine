# World Engine 1.63 Narrative Traceability — 4.0.2

## Source identity

- Source: `legacy/World_Engine_1.63.txt`
- SHA-256: `0748cf20e6fc870055d1d96ac329b83561c71162922bbb2220278ccb1f2feee5`
- Extraction basis: actual supplied bytes, not a summary.
- Evidence labels describe what is verified by source/code; they do not validate 1.63 marketing claims.

## Matrix

| # | 1.63 rule | Exact location | Intended effect | Still valuable? | 4.0.2 equivalent | Disposition | Status |
|---:|---|---|---|---|---|---|---|
| 1 | Roleplay-forward; hide system tags | L17–L21 | Immersion without losing backend authority | Yes | WETP result + NRP render contract + hard leakage check | KEEP/REWRITE | VERIFIED source |
| 2 | Storyteller should weave plots/characters | L25–L28 | Narrative quality goal | Yes as goal | Director/beat objective; no quality claim | KEEP AS GOAL | SOURCE CLAIM |
| 3 | AAS prioritizes modules by narrative weight/player focus | L51–L57 | Spend attention on relevant material | Yes | Director eligibility + saliency + bounded context | REWRITE | VERIFIED implementation |
| 4 | AUTOMASK scans prose and hides mechanical/audit markers | L92–L100; L128–L151 | Prevent mechanics leakage | Yes | hard mechanics/debug leakage patterns; debug-only packet fields | KEEP/REWRITE | VERIFIED implementation |
| 5 | Convert state shifts into symbolic/metaphoric alerts | L107–L122 | Diegetic feedback | Sometimes | eligible motif only; never mandatory metaphor | REWRITE | INFERRED design |
| 6 | Dream, vision, rumor dialect, emotion, memory hooks | L153–L161 | State-aware narration/dialogue | Yes | typed beat/dialogue/context fields | REWRITE | VERIFIED implementation boundary |
| 7 | NPC dialect/religious/status phrasing | L188–L196 | Distinct social voice | Yes with restraint | NPCVoiceProfile + original anchors | REWRITE | VERIFIED implementation |
| 8 | Dynamic narrative follows player/party actions | L218–L229 | Reactive story | Yes | Director reads completed authoritative turn only | KEEP | VERIFIED implementation |
| 9 | HN-Uplift preserves mechanics while improving prose | L268–L270 | Two-layer goal | Yes as architecture | typed authoritative projection → render packet → prose | KEEP/REWRITE | VERIFIED implementation; quality unverified |
| 10 | Two-layer narrative/mechanical output | L278–L285 | Hide mechanics but retain audit truth | Yes | authoritative result stays internal; NRP is player-render contract | KEEP | VERIFIED implementation |
| 11 | Faction/emotion/rumor/ritual/weather/routine/memory map directly to language | L292–L300 | State-driven speech and description | Partly | state becomes bounded pragmatic/sensory input, not rigid templates | REWRITE | INFERRED design |
| 12 | NPC tone, phrase modifiers, metaphors, hesitation and pacing | L307–L308 | Voice variation | Partly | voice profile + semantic dialogue plan + examples | REWRITE | VERIFIED implementation |
| 13 | Insert 1–2 lines of PC introspection | L311–L312 | Emotional closeness | No | player-locked interiority; sensory/forced/mechanical effects only | DROP | VERIFIED implementation |
| 14 | Fixed symbolic tables: fire=anger, webs=fate, feathers=secrets | L315–L318 | Consistent symbolism | No as mandatory mapping | authored MotifThread with cooldown and event linkage | DROP/REWRITE | VERIFIED implementation |
| 15 | Tone tags Grimdark/Epic/Surreal alter adjective pools | L321–L322 | Style control | Too coarse | multidimensional style profile + positive examples | REWRITE | VERIFIED implementation |
| 16 | Convert mechanical tags/DC/failure into prose | L325–L326 | Narrate outcomes without showing internals | Yes | completed result projection + mechanics visibility policy | KEEP/REWRITE | VERIFIED implementation |
| 17 | Store mechanical truth in compact WST/T tags | L334–L340 | Auditability | Internal only | SQLite events/revisions/context digests; hard leakage gate | REPLACE | VERIFIED implementation |
| 18 | 20–35% token reduction; single-author quality; lossless mechanics | L346–L375 | Performance/quality claims | Not established | no inherited claim; benchmark required | DROP CLAIMS | UNVERIFIED 1.63 claim |
| 19 | Dialogue state = beliefs, emotions, goals, memories | L430–L448 | Contextual NPC speech | Yes | DialoguePlan reads existing cognition/epistemic state | KEEP/REWRITE | VERIFIED implementation |
| 20 | Meaningful state change creates new dialogue | L455–L457 | React to changed state | Yes | semantic cache key and current state projection | KEEP | VERIFIED implementation |
| 21 | Unchanged state repeats cached literal lines | L460–L461 | Token savings | No | cache semantic state/fingerprints, generate fresh wording | DROP | VERIFIED implementation |
| 22 | Unchanged state may receive light paraphrase | L464–L466; L483–L489 | Variation without drift | Partly | fresh realization bounded by unchanged semantic plan and recent fingerprints | REWRITE | VERIFIED implementation |
| 23 | Use Hilbert dialogue only for major/memory-rich/emotional/mystery NPCs | L472–L477 | Allocate cost by importance | Goal useful | dialogue planning activated only when dialogue capability applies; anchors favored for major NPCs | REWRITE | VERIFIED implementation |
| 24 | TMAF/NCSE/TEMS/Hilbert preserve dialogue integrity | L495–L500 | Coherence/audit | No evidence for Hilbert layer | typed tables, facts/beliefs and receipts | DROP/REPLACE | UNVERIFIED 1.63 claim |
| 25 | Fixed 600–750 tokens each scene | L614–L616 | Consistent length | No | dynamic scene-function word bands | DROP | VERIFIED implementation |
| 26 | Hilbert vector emotions/operators/entropy | L765–L833 | Model blended emotional state | No measured value shown | existing mood/needs/goals/beliefs/memory/relationship | DROP | UNVERIFIED 1.63 mechanism |
| 27 | Compressed cutscene commands for camera/music/dialogue/effects/choices/conditions | L895–L921 | Structured authored scene control | Yes conceptually | typed beat/dialogue/render packet; dedicated import schema documented | PORT SEMANTICS | PARTIALLY implemented |
| 28 | Cutscene choices/branches/triggers/world-state flags | L928–L1007 | Interactive authored sequences | Yes with authority guard | choices and conditions remain authoritative inputs; renderer cannot set state | PORT SEMANTICS | PARTIALLY implemented |
| 29 | Automatically suppress cutscene syntax and render prose | L1145–L1164 | Player sees prose, not commands | Yes | NRP is private/debug; accepted prose is visible | KEEP/REWRITE | VERIFIED implementation |
| 30 | Entropy thresholds decide compression/metaphor/reuse | L1224–L1297 | Prevent drift | No evidence | explicit cooldowns, saliency, fingerprints and quality checks | DROP/REPLACE | VERIFIED replacement |
| 31 | Weather phrase bank prepends stock lines | L1328–L1348 | Weather-aware atmosphere | Risk of templates | weather may enter sensory context only when salient; repetition gate | REJECT DIRECT USE | INFERRED design |
| 32 | Voice/TTS assigns platform voices | L1352–L1373 | Audio identity | Separate concern | music/TTS presentation remains outside prose authority | REFERENCE ONLY | OUT OF SCOPE |
| 33 | Combat rolls visible; passive mechanics hidden | L1390–L1426 | Tactical clarity | Yes | existing mechanics visibility policy retained | KEEP | VERIFIED existing policy |
| 34 | LYRICA slowburn, stickiness, motif ancestry, forecasting | L2015–L2107 | Long-form motif continuity | Only core concepts | MotifThread: linkage, eligibility, cooldown, count, transformation, recent realization | REWRITE | VERIFIED implementation |
| 35 | LYRICA trie/vector/entropy/compression architecture and percentage gains | L2118–L2217 | Efficiency and motif recall | No need/evidence at current scale | SQLite indexed records and deterministic selection; no percentage claim | DROP COMPLEXITY/CLAIMS | UNVERIFIED 1.63 claim |
| 36 | Trust/motif placeholder inserts stock breath/glance/silence line | L2268–L2331 | Signal emotional change | No | behavioral detail must be context-specific; repetition checks | REJECT | SOURCE CONTAINS PLACEHOLDER |
| 37 | Image trigger priority uses dialogue climax/memory/motif/dream/combat/location | L2339–L2381 | Scene visualization | Already handled elsewhere | existing visual cue system retained; narrative packet may coordinate but not replace it | KEEP CURRENT WE | VERIFIED existing subsystem |

## Final disposition totals

| Class | Count |
|---|---:|
| Keep or keep/rewrite | 14 |
| Rewrite/replace/port | 17 |
| Drop/reject/reference only | 6 |
| Total matrix entries | 37 |

## Non-negotiable corrections

1. `L311–L312` player introspection is removed.
2. `L460–L466` literal dialogue reuse is replaced by semantic dialogue memory.
3. `L614–L616` fixed scene length is replaced by dynamic bands.
4. `L765–L833` and related Hilbert machinery have no authority in 4.0.2.
5. `L2015–L2217` motifs are retained only as lightweight authored threads with cooldowns and actual-event linkage.
6. 1.63 percentage/fidelity/prose-quality claims remain unverified until an appropriate benchmark is run.
