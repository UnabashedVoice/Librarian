# Librarian

The Compendium on request, in the middle of a deliberation.

The [**Compendium**](https://github.com/UnabashedVoice/Compendium) is the philosophy corpus that [**Arbitrator**](https://github.com/UnabashedVoice/Arbitrator-AI), [**Actualizer**](https://github.com/UnabashedVoice/Actualizer) and [**Palaestra**](https://github.com/UnabashedVoice/Palaestra) read from. Its `compendium_access.consult()` shows it to a model once, before the model starts reasoning. The Librarian lets the model ask again at the point it actually meets the question, and follow the corpus's own links (`responds_to`, `[E:]` citations) the way a researcher follows footnotes.

It has a second job. Actualizer's self-binding needs something a proposed change to the agent's self-written limits must answer to. The Compendium, served through the Librarian, is that critical perspective; [**Annals**](https://github.com/UnabashedVoice/Annals) is the log, and a separate revision gate (not built yet) supplies the delay. The Librarian's gate mode is the piece that gate uses.

**Requirements.** Python 3.10+, standard library only, and a Compendium checkout: a sibling folder named `Compendium`, or the folder `$COMPENDIUM_ROOT` names. It reads the Compendium's build (`dist/compendium.jsonl`) and never writes to it.

**Status.** Built and tested (2026-10-10). No sibling uses it yet: today each consumer's deliberation is a single model call, so each has to become a loop before a model has a turn to ask from. Actualizer comes first, since the gate lives there.

## Design commitments

- **It finds and hands over, and never retells.** Everything it shows is the corpus's own text through `compendium_access` (briefs, sections, citation markers stripped, driver tags kept). What it says itself is structural: labels, ids, "already given", "no entry lists", "budget reached".
- **The rival comes first.** The first hand-over of any entry is its brief, which carries its strongest counter-position, whatever was asked. When the rival cites another entry, the brief names it.
- **It does not rank.** Shortlists are set membership (a concept the entry lists, a thread, a domain, a link in the graph), always in corpus order, never cut at a fixed number. When nothing matches it says so and logs the gap.
- **Evaluation findings stay withheld.** Search runs over the withheld view, and every block is checked against the Compendium's `EVALUATION_FINDINGS` before it is shown.
- **Every request is logged** in a per-run, append-only, hash-chained ledger. `identity()` gives the line an Annals case pins it by.
- **It stops without a request count:** on `DONE` or no request, after two turns with nothing new, or when the context budget (scaled to the model's loaded window and what is left in it) runs out.

## The protocol

A model ends its turn with request lines, or with `DONE`:

```
LIBRARY OPEN <entry-id>
LIBRARY SECTION <entry-id> <Section name>
LIBRARY CONCEPT <term>
LIBRARY THREAD <slug>
LIBRARY ANSWERS <entry-id>
LIBRARY NEIGHBOURS <entry-id>
LIBRARY INDEX [<domain>]
DONE
```

The answer arrives as a new message headed as the Compendium's, never inside the model's own reasoning. Requests are text rather than tool calls because gpt-oss is rendered by hand over `/v1/completions` to keep its template identity line out (DESIGN.md, pushback 2).

```python
from pathlib import Path
import librarian as L

lib = L.Librarian.load()
s = lib.session(model="gpt-oss-20b", context_tokens=131072, ledger=Path("run/librarian.jsonl"))
r = s.turn(model_output)   # r.text goes to the model; stop when r.finished
```

## Gate mode

`lib.rivals(grounding)` lists every counter-position of the entries a self-written limit rests on. `present_rivals()` shows them to the proposer, `parse_responses()` reads the replies, and `check_answerability()` reports whether each one was addressed ("addressed N of M"). It checks form only, never whether a reply is good.

## Command line

```bash
python librarian.py ask "LIBRARY OPEN locke-person-forensic"   # what a model would be shown
python librarian.py rivals ulysses-contracts                   # the gate's rival set
python librarian.py verify run/librarian.jsonl                 # check a ledger's chain
python librarian.py gaps run/*.jsonl                           # requests the corpus did not cover
```

## Tests

```bash
python -m unittest discover -s tests
```

A synthetic corpus covers the edge cases; sweeps of the real Compendium build check that every view of every entry withholds findings and carries its rival, and that every listed concept finds its entry.

## Layout

```
librarian.py               the module: corpus graph, protocol, sessions, ledger, gate mode, CLI
tests/test_librarian.py    tests
DESIGN.md                  the design note, approved 2026-10-10
dev-changelog.md           history
```
