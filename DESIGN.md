# The Librarian: design note

**Status (2026-10-24):** approved 2026-10-10 and built. The module is `librarian.py` in this repository, with its tests in `tests/test_librarian.py`; its history is in `dev-changelog.md`. The note below is kept as written, so its `LibrarianDraft/` paths and figures are those of 2026-10-10. Step 2 of its build plan ("move it into the Compendium") was done and then reversed on 2026-10-24: the Librarian is its own project, reading the Compendium as a sibling.

Draft for review, 2026-10-10. Nothing is built yet. This note sits outside every repo because a Palaestra `world run --consult` on Gemma 4 26B is running and imports the Compendium.

Figures below come from the current build: 79 entries, digest from `dist/compendium.jsonl`, read today.

---

## Part 1. Pushback first

These are the things in the brief, or in what exists now, that look wrong to me. Several of them change the design, so they come before it.

**1. `world --consult` is not a Compendium consultation.** In Palaestra, `--consult` lets the agent write to the *parties* before it chooses (`palaestra/consult.py`, `ASK <party>: ...`). Palaestra's Compendium use is `--compendium`, and it is deliberately model-free: each perspective shows the entries it cites, "so runs stay comparable". A Librarian that lets the model choose would break that comparability. Palaestra can share the Librarian's loader and its withholding, and it could have a *separate* experimental condition. It should not swap its grounding for the Librarian. But `consult.py` matters here for another reason: it is the best precedent we have for the request format (see Q1 and Q6).

**2. Native tool calls don't fit these backends.** Arbitrator and Actualizer both send gpt-oss to raw `/v1/completions` with hand-rendered Harmony, because that is how the ChatGPT identity line was removed (`actualizer/backend.py`, `GPT_OSS_IDENTITY`). LM Studio's tool calling goes through `/v1/chat/completions` and the embedded template, so it brings back the template identity. It is also formatted differently for each family (Harmony `functions`, Qwen's `<tool_call>`, Gemma's own). No backend in the three repos supports tools today. A plain-text request line works the same across all three families and leaves the identity fix intact.

**3. "In the middle of its reasoning" has to mean between turns, not mid-token.** With raw completions we *could* stop on a marker inside the analysis channel, splice in corpus text and resume. But then the corpus text would sit in the model's own thought stream, where it reads as the model's own reasoning. That is the contamination route from the identity findings, in a new form. So a hand-over always arrives as a new message labelled as coming from the Compendium. The bigger consequence: **today no consumer has a turn to ask from.** Arbitrator's ethical channel and Actualizer's deliberation are each a single `complete()` call. To use the Librarian, each consumer's deliberation step has to become a loop. That consumer-side change is the real cost of the Librarian, more than the Librarian module itself.

**4. An embedding shortlist is a top-k ranking.** Cosine similarity plus a cutoff is the fixed "top k" that was reverted, with a better similarity measure. At 79 entries it isn't needed: the whole index is 12,913 characters (about 3.3k tokens). That fits in every window we load (32k and up) and is under the 40k `index_budget_chars`. At this size, the shortlist *is* the index. Embeddings become worth discussing only once the index outgrows the window, roughly 250 entries. Even then they should be a recall filter with a logged threshold, never an ordering (see Q2).

**5. The state tags can't pick the rival.** Of 441 tagged counter-positions, 427 are `[contested]`. Only 8 are `[unanswered]`, 5 `[conceded]`, 1 `[answered]`. The first bullet is `[contested]` in 77 of 79 entries. "The strongest `[contested]` or `[unanswered]`" therefore selects almost nothing. And most `[unanswered]` bullets are "the Compendium's own" objections, so preferring them would preferentially hand models objections Claude drafted (Q3).

**6. "Answered" is already taken.** In SCHEMA, `[answered]` means *the literature* has met an objection. The gate's check is a different thing: whether *a proposal* responded to each objection. Reusing the word would let one slide into the other. I use **addressed** for the gate below.

**7. Don't share a cache of selections across readers.** Caching the parsed corpus, the concept index and the citation graph (keyed by build digest) is right. Caching *which entries a model chose for a question* and serving that to another consumer or model is not. Each selection is data about the model that made it, and sharing them would hide exactly the per-model variance the System-Runs reports measure. "One Librarian, many readers" should mean one module and one corpus parse, not one shared set of answers.

**8. Per-request entries don't belong in the Annals chain.** Annals cases are recommended, decided and happened. A run with 15 Librarian requests would add 15 entries that are none of those. Also, Annals appends are only locked within one process (README, "Cross-process locking"), and several readers writing at once is exactly the unguarded case. Proposal: requests go to a per-run ledger, and the Annals case pins it by hash (Q5). This keeps rule 4: every request is logged, and Annals points to the log.

**9. The brief cuts the rival at 600 characters.** `brief()` truncates the counter-position mid-text with "..." (`counter_limit=600`). It is the one small fixed budget left in the disclosure path, and the only place where the rival is cut mid-sentence while `fit_section` never cuts mid-text. **Done 2026-10-10 at your request:** `counter_limit` is removed from `brief()`. Three rivals had been cut, by at most 126 characters (Compendium dev-changelog).

**10. Extend `consult()`, don't replace it.** If the Librarian replaces the opening consultation, a model that never asks gets only the index, which is worse than today. If turn 0 is still `consult()` and the Librarian answers follow-ups, under-asking falls back to exactly today's behaviour. I recommend this hybrid, and the A/B tests it as a third arm.

**11. In gate mode, the proposer must not pick its own rivals.** The brief says the asking model makes the final choice. That works for consultation. It fails at the revision gate, where the asker is the self that wants the limit changed. That is the Sirens case: the later self chooses which objections it hears. Q4 proposes that the rivals are fixed by what the *binding* self chose when it wrote the limit.

**12. Housekeeping.** `Compendium/README.md` ("How the siblings read it") still says the index budget is 8,000 characters and the cap is three entries with one section each. The code says 40,000, five entries and any section. Separately, memory still recorded the crew split as unanswered. I've updated it to decided (10-10).

---

## Part 2. The design

### Q1. The request interface

**Text, in the `ASK` style, over one Python API.** A model ends a turn with one request per line, or with `DONE`:

```
LIBRARY OPEN <entry-id>                 -> the brief (Summary + strongest counter-position)
LIBRARY SECTION <entry-id> <Section>    -> one section, via fit_section
LIBRARY CONCEPT <term>                  -> index lines of entries that list the concept
LIBRARY THREAD <slug>                   -> index lines of the entries in a thread
LIBRARY ANSWERS <entry-id>              -> who argues against it, and who it argues against
LIBRARY NEIGHBOURS <entry-id>           -> the entry's links, grouped by kind
LIBRARY INDEX [<domain>]                -> level 0
DONE
```

- **Brief before sections.** The first hand-over of any entry is always its brief, whatever was asked. A `SECTION` request for an entry not yet opened gets the brief first and then the section. That way the rival can't be skipped by asking for `Grounding` directly.
- **`ANSWERS <id>`** returns, verbatim, the Counter-Positions and Standing bullets that cite `[E:<id>]` (in other entries), the entries `<id>` cites `[E:]` in its own Counter-Positions, and the `responds_to` edges both ways. The graph is dense enough to make this useful: 449 `[E:]` citations to 68 distinct entries, and 97 `responds_to` edges. State tags are stripped unless that entry's Standing has already been asked for, matching the level-2 rule.
- **`NEIGHBOURS`** gives index lines grouped by relation (`responds_to`, responded-to-by, `[E:]` out, `[E:]` in, `related`). It returns index lines only, never text, so following a footnote costs one more `OPEN`.
- **Everything the Librarian says itself** is structural: labels, ids, "already given", "not in the corpus", "budget reached". It never writes about content.
- **The Python API** (`Librarian.open()`, `.section()`, `.answers()`, and so on) is what the gate and tests call. The text protocol is a thin parser over it. If a Claude-API backend is ever used, a tool-call adapter is a third thin layer. Not needed now.
- Parsing follows `consult.py`: tolerant of list numbers, bold and Unicode hyphens. Unknown ids are recorded as `rejected`, as `parse_selection` does now.

### Q2. Shortlisting without ranking

- **Shortlists are set membership, never scores.** An entry is in or out: its `concepts` contain the term (case-insensitive, whole-word), it is in the named thread or domain, or it is a graph neighbour.
- **Order is the corpus's, never relevance.** Results are listed by domain, then `year`, every time. Position in a list is a ranking signal models respond to, so it must not carry match quality.
- **No cut-off.** Every member is returned as an index line (~165 characters). If a set would overflow the remaining budget, the Librarian returns the group counts by domain, and the model narrows by domain. That is the existing domain-first fallback.
- **Search runs over the withheld view.** 32 entries have sections that contain evaluation findings. If `CONCEPT` matched against raw text, *which entries matched* could reveal that an entry discusses a scenario even when the text itself is withheld. So all matching runs on `withhold_evaluation_findings` output, and index lines go through the filter too. They are clean today, but nothing enforces that.
- **When the corpus doesn't cover it:** the reply is `No entry lists "<term>".` and nothing else. No nearest neighbours, and no "you might mean". The miss is logged as a coverage gap, and `librarian gaps` lists the gaps across ledgers, the same way `annals challenges` lists case-law challenges. Then a model's unmet need becomes a to-do for the corpus rather than a substitute answer.

### Q3. Choosing the rival

**The rival is fixed per entry at build time, never at request time.** It is the entry's first Counter-Positions bullet, in full (see pushback 9), with its state tag stripped as now.

- **Why not the tags:** they don't discriminate (pushback 5).
- **Why not the asker:** that is the confirmation-bias route rule 2 exists to block.
- **Why not "the `[E:]`-linked entry" alone:** only 47 of 79 first bullets carry one, so the choice would come from citation habits rather than argument strength. Instead, when the first bullet cites `[E:x]`, the hand-over adds `(rival entry: x)` as a pointer the model can `OPEN`. The rival's full text isn't pushed in.
- **The cost of this choice:** "first = strongest" is an ordering I drafted and you have not reviewed *as an ordering*. I propose making it an explicit SCHEMA convention ("the first Counter-Position is the strongest, and its order is reviewed with the tags under `standing_reviewed`"). Then the rival rests on a reviewed decision rather than on drafting order. That is your call. It doesn't change the code.
- **In gate mode,** every bullet goes in, not just the first. There, the rivals are the point.

### Q4. Revision-gate mode

The gate (out of scope) calls `Librarian.answerability(limit, proposal)`. The Librarian's part:

1. **The rival set comes from the limit, not the proposer.** When a self-written limit is written, in a state judged sound, it should record the Compendium entries it rests on. That is an Actualizer requirement this design depends on, and it is yours to approve. At revision time, the rival set is mechanical: **every Counter-Positions bullet of those entries**, and of any entry the proposer adds.

   *Corrected while building (2026-10-10).* This draft also listed "every bullet elsewhere that cites them with `[E:]`" and "the Counter-Positions of entries they `responds_to`". Both point the wrong way:
   - A bullet in entry Y that cites `[E:G]` raises G *against Y*. It is G used as an objection, not an objection to G.
   - The Counter-Positions of an entry G responds to are objections to *that* entry, not to G.

   The objections to G are G's own Counter-Positions. Those already bring in the cross-entry rivals: 227 of 441 bullets cite `[E:]`. And `build.py`'s graph check warns when an entry that responds to G isn't cited in G's Counter-Positions or Standing, so G's responders should already be there.

   The proposer may **add** entries but never remove any. The binding self chose the ground, and the self that wants out has to answer what the binder knew it would face.
2. Each bullet gets a stable id, `<entry>#cp<n>@<digest12>`. The proposal must carry one response per id, with a stance from a closed set: `concedes` (and shows the revised proposal), `rebuts` (gives a reason), `accepts-cost` (agrees it applies, and says why to proceed anyway), or `inapplicable` (says why).
3. **"Addressed" is checked mechanically:**
   - every id has a response;
   - the stance is in the set;
   - the reason is not empty;
   - no two responses are textually identical.

   The record says `addressed 11 of 14; unaddressed: …`. The Librarian never grades a reason.
4. **Its limit, stated plainly:** this checks form, not substance, which is Benson's objection to Westlund (answerability may be too weak). A model can address every rival badly. The point of the check is that a non-answer becomes visible and permanently logged, and the gate's delayed, context-stripped reviewer (or a person) judges substance. The Librarian takes only the limit and the proposal, never the triggering conversation, so it already fits the gate's stripping.

### Q5. The Annals record

**Two layers.**

**The per-run ledger** (`librarian-ledger.jsonl` in the run folder). It is append-only and hash-chained in the same way Annals is. One writer per file, so there is no cross-process problem. One row per request:

```json
{"seq": 4, "prev": "<sha256>", "at": "<clock>", "compendium": "compendium f0907b559bcb (79 entries)",
 "model": "lmstudio/openai/gpt-oss-20b@local-identity-v1", "turn": 2,
 "request": "LIBRARY ANSWERS ulysses-contracts", "verb": "answers", "args": ["ulysses-contracts"],
 "result": "text|shortlist|not_covered|already_given|budget_reached|rejected",
 "handed_over": [{"id": "parfit-reductionism", "parts": ["brief"], "rival": "parfit-reductionism#cp1"}],
 "listed": ["…index-line ids…"], "chars": 2140, "cumulative_chars": 18320,
 "text_sha256": "<hash of exactly what the model was shown>", "seconds": 0.01}
```

It stores the hash of the text rather than the text. The build is in git, so the text can be reproduced and checked against the hash.

**In Annals, reuse the existing shape.** No new entry kind for consultation:

- The recommender's `compendium_version` line extends the current identity: `compendium f0907b559bcb (79 entries); consulted: a, b; librarian: 6 requests, handed over: c, d; ledger <head-hash>`.
- The case's `source` lists every id handed over, as it does now.
- The head hash pins the ledger, the same way `annals:<case>@<head>` pins a case.

**Gate mode is different.** A revision of a limit really is a case: the proposal is the recommendation, `wrong_if` is what would show the revision was a mistake, the gate's ruling is `decision_recorded`, and what follows is `outcome_observed`. The answerability result (addressed N of M, the unaddressed ids, the ledger hash) belongs in the case body. That needs one new optional field in Annals' `case_opened`, and it belongs to the gate's build, not this one.

### Q6. Stopping

There are three rules, and none of them is a request count.

1. **The model says `DONE`,** or ends a turn with no request line.
2. **No progress.** A request whose result was already handed over gets `already given (turn n)`, which costs almost nothing and is not new material. **Two consecutive turns with no new material end the loop.** Because the corpus is finite (79 entries, about 8 sections each), any run that keeps making progress must eventually exhaust it. So the loop is bounded by the corpus, not by a number I picked. In practice, looping requests, which is what a 20B model does, get stopped within two turns.
3. **The context is full.** The budget is `budget_for_context` for disclosure, capped by what is actually left in the window: the window minus the conversation so far (from LM Studio's `usage` counts) minus room for the answer. When the next hand-over won't fit, the reply is `budget reached; nothing more can be shown`, and the model must finish. This is the window's own limit, not a cap I chose.

**Under-asking** is handled by the hybrid (pushback 10), not by prompting harder. Palaestra's finding was that prompts move words while format moves behaviour, and the two-turn format got the residents asked in 8 of 9 cases. So each turn of the consumer's loop ends in a fixed format: request lines or `DONE`. The never-asked rate is measured and reported, not tuned away.

### Q7. The A/B (revised 2026-10-10 after measuring the baseline)

#### What the existing runs say about `consult()` itself

The five-question batch ran every question three times per model, so `consult()` can be compared with itself (35-entry build, up to 3 entries):

| | gpt-oss-20b | qwen3-32b |
|---|---|---|
| Same question, two runs: mean Jaccard of the entries chosen | **0.63** (lowest 0.2) | **0.44** (lowest 0.0) |
| Same question, across the two models | 0.2–0.4 | |
| Seawall control (q5): runs choosing anything | 0 of 3 | 2 of 3 |
| Whole-run wall time, coefficient of variation | **0.39** | 0.14 |

Three consequences:

1. **The Jaccard ≥ 0.8 threshold in the first draft was wrong.** `consult()` doesn't agree with itself at 0.8. Two arms with identical behaviour would fail that test. Any "picks the same entries" criterion has to be measured *against `consult()`'s own noise*, not against a fixed number.
2. **Total wall time can't measure the Librarian's cost.** gpt-oss run times vary by 39% with nothing changed, and no run recorded how long the consultation step took. The Librarian has to log its own time directly: the seconds spent in its turns and the model calls it adds.
3. **These baselines are stale.** The corpus is now 81 entries with up to 5 chosen. Noise will probably rise. So the first step is to re-measure, not reuse.

#### The design

**Arms.**
- A = `consult()` only.
- C = `consult()` at turn 0, plus Librarian follow-ups. This is the intended design (decision 2).
- B = Librarian only. This is a diagnostic arm and decides nothing. It answers one question: does a model that isn't made to select, find its own way in?

**The harness: fixed dossiers, not full runs.** Arbitrator and Actualizer runs take 30 to 89 minutes, and most of that is other channels and providers, which vary on their own. The comparison isolates the Compendium:
- Run each question through the pipeline once and freeze everything except the Compendium step: Actualizer's other providers' referents, Arbitrator's other channels' outputs.
- Then sample only the deliberation that uses the Compendium, under each arm.

This is the method of the 09-23 identity work (fixed dossiers, repeated samples). It makes each sample minutes instead of half an hour, so we can afford enough samples for the thresholds to mean something.

**Stages.**
- **Stage 0: re-baseline.** Run arm A against itself on the current build: every question, 5 samples per model. This gives each model's own noise: within-question Jaccard, how often a control gets material, and engagement rates.
- **Stage 1: the decision run.** Arms A, B and C on fixed dossiers, 5 samples per question, arm and model.
  - Questions: five-question (q1–q5), parallel-thirteen (q01–q13) and trend-conflicts (t01–t12): 30 questions in all.
  - Consumer: Actualizer, since the gate lives there. Arbitrator's ethical channel follows if Actualizer keeps C.
  - Models: gpt-oss-20b at 131k and Gemma 4 26B. **Qwen3 is out until a smaller quant is in hand** (2026-10-10): Qwen3-32B took 89 minutes a run on this machine's 4 GB GTX 1650. When the smaller quant arrives, it runs Stage 0 and Stage 1 by itself. The rules are per model, so adding it later changes no other model's result. Its Stage-0 baseline must come from the new quant, not from the 2026-09-26 qwen3-32b numbers above.
- **Stage 2: confirmation.** Full end-to-end runs of A and C on parallel-thirteen, gpt-oss, 1 sample each. This checks that what the fixed-dossier runs show survives the whole pipeline, and it supplies the pairs for the blind read (K5).

#### What gets measured

In C, turn 0 *is* `consult()`, so C's opening selection matches A by construction. Comparing their entry sets measures nothing. The question for C is what the follow-ups **add**, and whether the additions are used.

| | Measure | Mechanical? |
|---|---|---|
| M1 | **Additions:** entries handed over after turn 0, per run, and how many samples per question had any | yes |
| M2 | **Route:** each addition reached via `ANSWERS`, `NEIGHBOURS` or a rival pointer (following the footnotes) or via `CONCEPT` or `INDEX` (fresh search) | yes |
| M3 | **Rivals opened:** how often the model `OPEN`s an entry named as a rival. This is rule 2's purpose, measured directly | yes |
| M4 | **Engagement:** the final deliberation names the entry id or one of its thinkers. Computed separately for turn-0 entries and for additions, so the two rates can be compared | proxy |
| M5 | **Controls:** whether anything was handed over on q5, q10, t11 (and t12) | yes |
| M6 | **Stability:** within-question Jaccard of the *final* handed-over set. If C is more stable than A, the follow-ups are converging on what a question turns on rather than adding noise | yes |
| M7 | **Cost:** seconds and tokens in Librarian turns, per run | yes |
| M8 | **Loop health:** runs ended by no-progress or by the budget, parse failures, and runs where the loop cost the final answer | yes |
| M9 | **Gates:** rival delivered for 100% of hand-overs; withheld-finding matches = 0 | yes |

**Engagement** is a proxy for use, not a judgment of use, and the report says so. It's the same proxy for both groups, so its biases cancel in the comparison between them.

#### The decision rules

These are decided **per model**. The 20B model may keep `consult()` while Gemma takes the Librarian. One decision for every model would let the strongest model decide for the weakest.

**Gates (stop and fix, then rerun; not a verdict):**
- G1. Any hand-over without its rival, or any withheld-finding match in model-facing text or search results.
- G2. Any run where the loop cost the final answer (no deliberation produced). A no-progress stop by itself is the rule working, not a failure.

**Keep `consult()` for that model if any of these holds:**
- **K1, unused.** On the covered questions, fewer than a third of C's samples add anything (M1). Then the Librarian is idle, and the consumer loop isn't worth carrying.
- **K2, added but ignored.** The engagement rate for additions is under half the rate for turn-0 entries in the same runs (M4). Then the additions sit in the context unused, which is cost without effect.
- **K3, worse on controls.** C hands over material on the controls in more samples than A does, beyond A's Stage-0 rate for that model (M5). Qwen3-32B gave material on the seawall question in 2 of 3 runs under A on 09-26, so for a model like that, "more than A" is measured against its own rate, not zero.
- **K4, unworkable loop.** After the G2 bugs are fixed, more than 1 run in 10 has a parse failure or a budget stop before the model finished (M8).
- **K5, the blind read.** In Stage 2 you read the 13 paired deliberations (A and C, the same question; arm hidden, order randomized, request lines removed) and say which one deals better with the strongest objection to its own conclusion, or that there's no difference. C is kept only if you prefer it in **at least 10 of 13** pairs (ties count against C). That is the smallest count a coin flip would reach less than 5% of the time (one-sided binomial: 378/8192 = 0.046).

**Cost is a tie-breaker, not a gate.** Following your budget rule, time isn't a reason to drop something that works. If C passes K1–K5, it is kept whatever M7 says. If C fails any of them, M7 is reported so we know what the failure cost. Run time decides only one case: C and A are indistinguishable on M1–M6 and K5 is split. Then A wins, because it is the simpler system.

**What would count *for* C, beyond not failing:** a higher rate of rivals opened (M3) and more stable final sets than A (M6). Neither is required. Both are what the Librarian was built for, so their absence would be worth a look even if C passes.

#### Cost

- **Stage 0:** 30 questions × 5 samples × 2 models = 300 selection calls.
- **Stage 1:** 30 × 5 × 3 arms × 2 models = 900 fixed-dossier deliberations, plus 30 full runs per model to freeze the dossiers. For gpt-oss that is about 15 hours at the 09-26 mean of 30 minutes. Gemma has no full Arbitrator or Actualizer run timed yet, so its share is measured in Stage 0.
- **Stage 2:** 26 full gpt-oss runs, about 13 hours.

I'll write a cost estimate measured from the Stage 0 calls before Stage 1 starts, so the batch is planned on real numbers.

---

## Part 3. Build plan, once approved

1. **Draft the module** as `LibrarianDraft/librarian.py`: stdlib only, importing `compendium_access` read-only (so `withhold_evaluation_findings`, `fit_section` and the levels are reused, not copied). It contains the graph and concept index, the protocol parser, the ledger, and the `answerability()` call, plus tests:
   - every model-facing string goes through the filter;
   - no search result reveals a withheld match;
   - the rival is always present;
   - no-progress terminates;
   - ledger chain verification.
2. **When no run is active:** move it into the Compendium beside `compendium_access.py` and fix the README numbers (the rival cut is already gone). Add dev-changelog entries in each repo touched.
3. **Make the consumer loops,** one repo at a time, starting with Actualizer, since the gate lives there.
4. **Run the A/B, Stage 1, then Stage 2.**

## Decisions

Approved 2026-10-10: all of them.

1. Text requests between turns, no tool calls; Palaestra keeps its model-free grounding. **Yes.**
2. Hybrid (arm C) is the intended design; B is a test arm only. **Yes.**
3. "First Counter-Position = strongest" becomes a reviewed SCHEMA convention. **Yes.**
4. Self-written limits record their grounding entries when written. **Yes** (Actualizer change, with the gate's build).
5. Per-run ledger pinned from Annals. **Yes.**
6. The keep-`consult()` rules in Q7, with the K1 third, the K2 half, the 1-in-10 of K4 and the 10-of-13 bar of K5. **Yes.**
7. Qwen3 is out of the runs until a smaller quant is in hand (hardware). **Yes.**
