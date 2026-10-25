# Librarian — dev changelog

The Compendium on request, in the middle of a deliberation. Drafted on 2026-10-10 in `LibrarianDraft/`, outside every repo, while a Palaestra world batch was importing the Compendium.

## 2026-10-24

### Its own repository
- **The Librarian is its own project**, a sibling of Arbitrator, Actualizer, Palaestra, Annals and the Compendium, at the user's request. It reads the Compendium the way the consumers do: from `$COMPENDIUM_ROOT`, or a sibling folder named `Compendium`.
- **History.** Earlier the same day it was moved into the Compendium beside `compendium_access.py` (Compendium 50fedcf, step 2 of the design note's build plan) and `LibrarianDraft/` was removed. This repository takes the files from that commit: `librarian.py`, `tests/test_librarian.py`, and `DESIGN.md` (there `LIBRARIAN-DESIGN.md`). The Compendium drops them in its next commit.
- **Code:** only the Compendium lookup goes back to the draft's sibling search; docstrings and the design note's header point at the new home.
- **Checks:** 23/23 tests on Linux, Python 3.14.

## 2026-10-10

### Design
- `DESIGN.md` written and approved, every decision. Thresholds for keeping the one-shot `consult()` (Q7) were set from measured baselines: `consult()` agrees with itself only at Jaccard 0.63 (gpt-oss) and 0.44 (Qwen3-32B), and gpt-oss run time varies by 39%. Qwen3 is out of the A/B until a smaller quant is in hand.
- Corrected while building: the gate's rival set is the Counter-Positions of the entries a limit rests on (plus any the proposer adds). The two further sources in the first draft pointed the wrong way (DESIGN.md, Q4).

### `librarian.py` (stdlib; imports `compendium_access` read-only)
- **Corpus and graph,** built once per build and cached by `Librarian.load()` (reloaded when `dist/compendium.jsonl` changes): corpus order (domain, year, id), concepts and threads, and the citation graph from withheld text only, so an `[E:]` citation in a withheld sentence makes no edge. Loads in 0.2 s at 84 entries.
- **Requests** as text lines (`LIBRARY OPEN / SECTION / CONCEPT / THREAD / ANSWERS / NEIGHBOURS / INDEX`, `DONE`), parsed from the final channel, tolerant of list numbers, bold, colons and Unicode hyphens. The first hand-over of an entry is always its brief, with the whole rival and a pointer to the rival's own entry where it cites one.
- **Shortlists** are set membership in corpus order, never cut at k. When one won't fit, it is replaced by counts by domain. When nothing matches: "No entry lists ...", logged as a gap. A withheld term is not echoed back.
- **Stopping:** DONE or no request; two turns with no new material; or the budget (`budget_for_context`, capped by an optional `window_room` callback). No request count.
- **Ledger:** per run, append-only, hash-chained; `verify`, `gaps`. `identity()` extends the `consult()` identity with what the Librarian handed over and the ledger head.
- **Gate mode:** `rivals()`, `present_rivals()`, `parse_responses()`, `check_answerability()`, which checks form only ("addressed N of M"; missing, stance, empty, duplicate).
- **Tests:** 23 (`python -m unittest discover -s tests`), a synthetic corpus for the edge cases and sweeps of the real build: every view of every entry withholds findings and carries its rival, every listed concept finds its entry, no rival is cut.
- The real-corpus sweep found that `\b` fails beside parentheses ("self-overcoming (Selbstüberwindung)" was unsearchable); fixed with lookarounds.
