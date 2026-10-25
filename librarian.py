"""librarian.py — The Compendium on request, in the middle of a deliberation.

compendium_access.consult() shows the Compendium once, before a model starts
reasoning. The Librarian lets the model ask again at the point it meets a
question, and follow the corpus's own links (responds_to, [E:] citations)
the way a researcher follows footnotes. Design: DESIGN.md
(approved 2026-10-10). The rules it keeps:

- It finds and hands over, and never retells. Everything it shows is the
  corpus's own text through compendium_access (briefs, sections, citation
  markers stripped, driver tags kept). What it says itself is structural:
  labels, ids, "already given", "no entry lists", "budget reached".
- Every hand-over of an entry carries its strongest counter-position. The
  first hand-over of any entry is its brief, whatever was asked, so a model
  can't reach a section without passing the rival. When the rival bullet
  cites another entry, the brief names it as a pointer.
- It does not rank. Shortlists are set membership (a concept the entry
  lists, a thread, a domain, a link in the graph) and always come in corpus
  order (domain, then year, then id), never in order of match. Nothing is
  cut at a fixed k. When nothing matches, it says so and logs the gap.
- Evaluation findings stay withheld. Search runs over the withheld view, so
  which entries match can't reveal a withheld passage, and every block is
  checked against EVALUATION_FINDINGS before it is shown.
- Every request and hand-over goes into a per-run, hash-chained ledger, and
  identity() gives the line an Annals case pins it by.
- Stopping, with no request count: the model ends with DONE or with no
  request; two turns in a row with no new material end it (the corpus is
  finite, so this always comes); the context budget, scaled to the model's
  loaded window and to what is actually left in it, ends it.

Requests are text lines, one per line, at the end of a model's turn:

    LIBRARY OPEN <entry-id>
    LIBRARY SECTION <entry-id> <Section name>
    LIBRARY CONCEPT <term>
    LIBRARY THREAD <slug>
    LIBRARY ANSWERS <entry-id>
    LIBRARY NEIGHBOURS <entry-id>
    LIBRARY INDEX [<domain>]
    DONE

Text rather than tool calls: gpt-oss is rendered by hand over /v1/completions
to keep its identity line out, and tool calling would bring the template
back (DESIGN.md, pushback 2).

The revision-gate mode (rivals(), present_rivals(), parse_responses(),
check_answerability()) lists every counter-position of the entries a
self-written limit rests on, and checks only that a proposal addressed each
one, in form. It never judges whether a reply is good.

Standard library only. Finds the Compendium at $COMPENDIUM_ROOT or as a
sibling folder, like the consumers do.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

_HERE = Path(__file__).resolve()


def _import_access(root: Optional[Path] = None):
    """compendium_access from `root`, $COMPENDIUM_ROOT, or a sibling folder named Compendium."""
    if root is None:
        env = os.environ.get("COMPENDIUM_ROOT")
        candidates = [Path(env)] if env else [p / "Compendium" for p in _HERE.parents]
    else:
        candidates = [Path(root)]
    for c in candidates:
        if (c / "compendium_access.py").exists():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            import compendium_access
            return compendium_access, c
    raise FileNotFoundError("Compendium not found: set COMPENDIUM_ROOT to the Compendium folder")


ca, _ROOT = _import_access()

PROTOCOL = """You may ask the Compendium for more at any point. End your answer with one line per request, or with the line DONE:
- LIBRARY OPEN <entry-id>: an entry's summary and its strongest counter-position
- LIBRARY SECTION <entry-id> <section>: one section of an entry ({sections})
- LIBRARY CONCEPT <term>: the entries that list a concept
- LIBRARY THREAD <slug>: the entries in a thread of argument
- LIBRARY ANSWERS <entry-id>: where the corpus argues with this entry: who it is raised against, which entries object to it, who responds to it
- LIBRARY NEIGHBOURS <entry-id>: the entries it is linked to
- LIBRARY INDEX [<domain>]: the index
What comes back is the corpus's own text, with its counter-positions."""

STANCES = ("concedes", "rebuts", "accepts-cost", "inapplicable")

_VERBS = ("OPEN", "SECTION", "CONCEPT", "THREAD", "ANSWERS", "NEIGHBOURS", "INDEX")
_REQUEST = re.compile(r"^[\W\d_]*LIBRARY\W+(OPEN|SECTION|CONCEPT|THREAD|ANSWERS|NEIGHBOU?RS|INDEX)\b\**:?\s*(.*?)\s*$",
                      flags=re.M | re.I)
_DONE = re.compile(r"^[\W\d_]*(?:DONE|NO REQUESTS)\W*$", flags=re.M)
_HYPHENS = re.compile("[‐‑‒–—−]")
_ECITE = re.compile(r"\[E:([^\]]+)\]")
_RESPONSE = re.compile(r"^[\W\d_]*RESPONSE\W+(\S+?@[0-9a-f]{12})\W+([a-z-]+)\W*:\s*(.*?)(?=^[\W\d_]*RESPONSE\W|\Z)",
                       flags=re.M | re.S | re.I)


def _norm_id(s: str) -> str:
    return _HYPHENS.sub("-", s).strip().strip("`*'\"[](),.:;").lower()


def _norm_term(s: str) -> str:
    return re.sub(r"\s+", " ", _HYPHENS.sub("-", s).lower().replace("-", " ")).strip(" `*'\"")


def _bullets(text: str) -> list[str]:
    """A section's list items, without the leading '- ', in order. A paragraph or ### heading
    ends an item (a bare regex would let the last Reception bullet swallow "### Measured")."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        for unit in re.split(r"\n(?=- )", block.strip()):
            if unit.startswith("- "):
                out.append(re.sub(r"\s+", " ", unit[2:]).strip())
    return out


def _shown(bullet: str, keep_tag: bool) -> str:
    """A bullet as a model sees it: citations stripped, the state tag kept only at level 2."""
    text = ca.strip_citations(bullet).strip()
    return text if keep_tag else ca._STATE_TAG.sub("", text)


# --------------------------------------------------------------------- corpus

class Librarian:
    """The corpus parse, its concept index and citation graph, shared by every session on one
    build. Use Librarian.load(): one instance per build per process, reloaded when dist changes."""

    _cache: dict[str, tuple[tuple, "Librarian"]] = {}

    @classmethod
    def load(cls, dist: Optional[Path] = None) -> "Librarian":
        dist = Path(dist or _ROOT / "dist" / "compendium.jsonl").resolve()
        st = dist.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = cls._cache.get(str(dist))
        if hit and hit[0] == key:
            return hit[1]
        lib = cls(ca.Compendium(dist))
        cls._cache[str(dist)] = (key, lib)
        return lib

    def __init__(self, comp):
        self.comp = comp
        self.version = comp.version
        self.digest12 = comp.digest[:12]
        meta = {eid: comp._entries[eid]["meta"] for eid in comp.ids}
        self._meta = meta
        self._order = sorted(comp.ids, key=lambda e: (meta[e]["domain"], meta[e].get("year") is None,
                                                      meta[e].get("year") or 0, e))
        self._rank = {e: i for i, e in enumerate(self._order)}

        # Concepts and threads, as the withheld view shows them.
        self._concepts = {e: [c for c in meta[e].get("concepts", []) if not ca.EVALUATION_FINDINGS.search(c)]
                          for e in comp.ids}
        self._threads: dict[str, list[str]] = {}
        for e in self._order:
            for t in meta[e].get("threads") or []:
                self._threads.setdefault(t, []).append(e)

        # The citation graph, built from withheld text only, so an edge whose citing sentence is
        # withheld does not exist here.
        self.objections: dict[str, list[str]] = {e: [] for e in comp.ids}    # X's counters cite these
        self.raised_against: dict[str, list[tuple[str, str, str]]] = {e: [] for e in comp.ids}
        for y in comp.ids:
            for sec in ("Counter-Positions", "Standing"):
                for raw in self._raw_bullets(y, sec):
                    kept = ca.withhold_evaluation_findings(raw)
                    for z in dict.fromkeys(_ECITE.findall(kept)):
                        if z not in comp.ids or z == y:
                            continue
                        self.raised_against[z].append((y, sec, kept))
                        if sec == "Counter-Positions" and z not in self.objections[y]:
                            self.objections[y].append(z)
        self.responds_to = {e: [r for r in meta[e].get("responds_to") or [] if r in meta] for e in comp.ids}
        self.responded_by: dict[str, list[str]] = {e: [] for e in comp.ids}
        for e in self._order:
            for r in self.responds_to[e]:
                self.responded_by[r].append(e)
        self.related = {e: [r for r in meta[e].get("related") or [] if r in meta] for e in comp.ids}

    # ------------------------------------------------------------- lookups

    def _raw_bullets(self, eid: str, section: str) -> list[str]:
        # The raw section text (citations intact) is needed for [E:] links; compendium_access
        # keeps it on Compendium._entries. Every use below withholds before anything is shown.
        return _bullets(self.comp._entries[eid]["sections"].get(section, ""))

    def ordered(self, ids: Iterable[str]) -> list[str]:
        """Corpus order: domain, then year, then id. Never order of match."""
        return sorted(set(ids), key=self._rank.__getitem__)

    def index_line(self, eid: str) -> str:
        m = self._meta[eid]
        title = m["title"] if not ca.EVALUATION_FINDINGS.search(m["title"]) else "(title withheld)"
        concepts = "; ".join(c if len(c) <= 48 else c[:45] + "..." for c in self._concepts[eid][:3])
        return f"{eid} | {title} [{m['grounding']}]: {concepts}"

    def concept_members(self, term: str) -> list[str]:
        t = _norm_term(term)
        if not t or ca.EVALUATION_FINDINGS.search(term):
            return []
        pat = re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)")  # \b fails beside "(" or ")"
        return self.ordered(e for e, cs in self._concepts.items() if any(pat.search(_norm_term(c)) for c in cs))

    def thread_members(self, slug: str) -> list[str]:
        return list(self._threads.get(_norm_id(slug), []))

    def domain_members(self, domain: str) -> list[str]:
        d = _norm_id(domain)
        return [e for e in self._order if self._meta[e]["domain"] == d]

    def _first_rival(self, eid: str) -> tuple[int, str]:
        """(number, withheld raw text) of the bullet the brief shows as the strongest
        counter-position: the first that survives withholding. (0, "") if none."""
        for n, raw in enumerate(self._raw_bullets(eid, "Counter-Positions"), 1):
            kept = ca.withhold_evaluation_findings(raw)
            if kept.strip():
                return n, kept
        return 0, ""

    def rival_id(self, eid: str) -> Optional[str]:
        n, _ = self._first_rival(eid)
        return f"{eid}#cp{n}@{self.digest12}" if n else None

    def rival_pointers(self, eid: str) -> list[str]:
        """Entries cited in the bullet the brief shows as the strongest counter-position."""
        _, kept = self._first_rival(eid)
        return [z for z in dict.fromkeys(_ECITE.findall(kept)) if z in self._meta and z != eid]

    def brief(self, eid: str) -> str:
        text = self.comp.brief(eid)
        ptr = self.rival_pointers(eid)
        if ptr:
            text += "\n(rival entry: " + ", ".join(ptr) + ")"
        return text

    def section_name(self, name: str) -> Optional[str]:
        n = _norm_term(name)
        return next((s for s in ca.SECTIONS if _norm_term(s) == n), None)

    def resolve(self, raw: str) -> Optional[str]:
        eid = _norm_id(raw)
        return eid if eid in self._meta else None

    def session(self, **kw) -> "Session":
        return Session(self, **kw)

    # ------------------------------------------------- revision-gate mode

    def rivals(self, grounding: list[str], added: Iterable[str] = ()) -> list["Rival"]:
        """Every counter-position of the entries a limit rests on (recorded when the limit was
        written), plus any entries the proposer adds. The proposer can add, never remove.
        Ids are <entry>#cp<n>@<digest12>, n counting the entry's bullets as written, so a
        withheld bullet leaves a gap rather than renumbering the rest."""
        ids = list(dict.fromkeys([*grounding, *added]))
        unknown = [e for e in ids if e not in self._meta]
        if unknown:
            raise ValueError(f"not in the Compendium ({self.version}): {', '.join(unknown)}")
        out = []
        for e in ids:
            for n, raw in enumerate(self._raw_bullets(e, "Counter-Positions"), 1):
                kept = ca.withhold_evaluation_findings(raw)
                if kept.strip():
                    out.append(Rival(f"{e}#cp{n}@{self.digest12}", e, n, _shown(kept, keep_tag=False),
                                     from_grounding=e in grounding))
        return out


# --------------------------------------------------------------------- ledger

class Ledger:
    """A per-run JSONL file, append-only and hash-chained like the Annals record: each row carries
    the hash of the one before it. One writer per file. Rows are never rewritten."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.head = "0" * 64
        self.seq = 0
        if self.path.exists():
            problems = verify_ledger(self.path)
            if problems:
                raise ValueError(f"{self.path}: chain broken, nothing appended: {problems[0]}")
            rows = _rows(self.path)
            if rows:
                self.head, self.seq = rows[-1]["hash"], rows[-1]["seq"]

    def append(self, row: dict) -> dict:
        self.seq += 1
        row = {"seq": self.seq, "prev": self.head, "at": datetime.now().isoformat(timespec="seconds"), **row}
        row["hash"] = _row_hash(row)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self.head = row["hash"]
        return row


def _row_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def verify_ledger(path: Path) -> list[str]:
    """Problems with a ledger's chain ([] if intact)."""
    problems, prev = [], "0" * 64
    for i, row in enumerate(_rows(path), 1):
        if row.get("prev") != prev:
            problems.append(f"row {i}: prev does not match the row before")
        if row.get("hash") != _row_hash(row):
            problems.append(f"row {i}: hash does not match its content")
        if row.get("seq") != i:
            problems.append(f"row {i}: seq is {row.get('seq')}")
        prev = row.get("hash")
    return problems


def gaps(paths: Iterable[Path]) -> dict[str, list[str]]:
    """Requests the corpus did not cover, across ledgers: term -> the ledgers that asked."""
    out: dict[str, list[str]] = {}
    for p in paths:
        for row in _rows(p):
            if row.get("result") == "not_covered":
                term = f"{row.get('verb')} {' '.join(row.get('args') or [])}".strip()
                out.setdefault(term, []).append(str(p))
    return out


# -------------------------------------------------------------------- session

@dataclass
class Request:
    verb: str
    args: list[str]
    line: str


def parse_requests(text: str) -> tuple[list[Request], bool]:
    """(requests in order, without repeats; whether the text says DONE). Tolerates list numbers,
    bold and Unicode hyphens, as consult.parse_asks does."""
    text = ca.final_text(text)
    reqs, seen = [], set()
    for m in _REQUEST.finditer(text):
        verb = m.group(1).upper().replace("NEIGHBORS", "NEIGHBOURS")
        rest = m.group(2).strip().strip("*").strip()
        if verb == "SECTION":
            parts = rest.split(None, 1)
            args = [parts[0], parts[1]] if len(parts) == 2 else parts
        else:
            args = [rest] if rest else []
        key = (verb, tuple(_norm_term(a) for a in args))
        if key not in seen:
            seen.add(key)
            reqs.append(Request(verb, args, m.group(0).strip()))
    return reqs, bool(_DONE.search(text))


@dataclass
class TurnResult:
    text: str                       # what to show the model ("" if nothing)
    finished: bool                  # no further Librarian turns in this exchange
    reason: Optional[str] = None    # done | no_requests | no_progress | budget
    new_material: bool = False
    requests: int = 0


@dataclass
class _Outcome:
    block: str
    result: str                     # text | shortlist | not_covered | already_given | budget_reached | rejected
    handed: list[dict] = field(default_factory=list)
    listed: list[str] = field(default_factory=list)
    new: bool = False


class Session:
    """One model's exchange with the Librarian over one question.

    budget_chars defaults to budget_for_context(context_tokens). window_room, if given, returns
    the characters still free in the model's window (window minus conversation so far minus room
    for the answer); the Librarian never shows more than either allows."""

    def __init__(self, lib: Librarian, model: str = "unknown", context_tokens: int = 0,
                 budget_chars: Optional[int] = None, ledger: Optional[Path] = None,
                 window_room: Optional[Callable[[], int]] = None, question: str = ""):
        self.lib = lib
        self.model = model
        self.budget = budget_chars if budget_chars is not None else ca.budget_for_context(context_tokens)
        self.window_room = window_room
        self.used = 0
        self.turn_no = 0
        self.given: dict[tuple, int] = {}       # what has been shown -> the turn it was shown
        self.header_given = False
        self.requests = 0
        self.no_progress = 0
        self.finished: Optional[str] = None
        self.handed_ids: list[str] = []
        self.ledger = Ledger(ledger) if ledger else None
        self._log({"kind": "session", "compendium": lib.version, "model": model, "question": question,
                   "budget_chars": self.budget, "context_tokens": context_tokens})

    # ------------------------------------------------------------- budget

    def room(self) -> int:
        r = self.budget - self.used - (0 if self.header_given else len(ca.HEADER) + 40)  # header still to come
        if self.window_room is not None:
            r = min(r, int(self.window_room()))
        return max(0, r)

    def _log(self, row: dict) -> None:
        if self.ledger:
            self.ledger.append(row)

    # --------------------------------------------------------- openings

    def absorb_consultation(self, c) -> None:
        """Arm C: turn 0 was compendium_access.consult(). What it disclosed counts as given, so
        follow-ups don't repeat it, and its size counts against the budget."""
        handed = []
        for s in c.selected:
            self.given[("brief", s["id"])] = 0
            parts = ["brief"]
            for name in ca._sections_of(s):
                if f"[{s['id']}] {name}:\n" in c.text:
                    self.given[("section", s["id"], name)] = 0
                    parts.append(name)
            handed.append({"id": s["id"], "parts": parts, "rival": self.lib.rival_id(s["id"])})
            self._note(s["id"])
        self.header_given = bool(c.text)
        self.used += len(c.text)
        self._log({"kind": "consult", "turn": 0, "handed_over": handed, "chars": len(c.text),
                   "cumulative_chars": self.used, "text_sha256": _sha(c.text), "error": c.error})

    def opening(self) -> str:
        """Arm B: the header and the index (or the domain list, if the index won't fit), shown
        before the model's first turn."""
        out = self._index(None)
        self.header_given = True
        text = _guard(ca.HEADER.format(version=self.lib.version) + "\n\n" + out.block)
        self.used += len(text)
        self._log({"kind": "opening", "turn": 0, "result": out.result, "listed": out.listed,
                   "chars": len(text), "cumulative_chars": self.used, "text_sha256": _sha(text)})
        return text

    # -------------------------------------------------------------- turns

    def turn(self, model_output: str) -> TurnResult:
        """Read the end of a model's turn; answer its requests; say whether the exchange is over."""
        if self.finished:
            return TurnResult("", True, self.finished)
        self.turn_no += 1
        reqs, done = parse_requests(model_output)
        if not reqs:
            self.finished = "done" if done else "no_requests"
            self._log({"kind": "end", "turn": self.turn_no, "reason": self.finished})
            return TurnResult("", True, self.finished)

        blocks, new_any, budget_hit = [], False, False
        for r in reqs:
            t0 = time.monotonic()
            self.requests += 1
            out = self._handle(r)
            if out.block and out.result not in ("already_given", "rejected", "budget_reached", "not_covered") \
                    and not self.header_given:
                out.block = ca.HEADER.format(version=self.lib.version) + "\n\n" + out.block
                self.header_given = True
            out.block = _guard(out.block)
            if out.new:
                self.used += len(out.block)
            new_any |= out.new
            budget_hit |= out.result == "budget_reached"
            echo = r.line if not ca.EVALUATION_FINDINGS.search(r.line) else f"LIBRARY {r.verb}"
            blocks.append(f"> {echo}\n{out.block}")
            self._log({"kind": "request", "turn": self.turn_no, "request": r.line, "verb": r.verb.lower(),
                       "args": r.args, "result": out.result, "handed_over": out.handed, "listed": out.listed,
                       "chars": len(out.block), "cumulative_chars": self.used, "text_sha256": _sha(out.block),
                       "seconds": round(time.monotonic() - t0, 3)})

        self.no_progress = 0 if new_any else self.no_progress + 1
        if done:
            self.finished = "done"
        elif budget_hit:
            self.finished = "budget"
        elif self.no_progress >= 2:
            self.finished = "no_progress"
        text = "\n\n".join(blocks)
        if self.finished in ("budget", "no_progress"):
            why = ("the context budget is reached" if self.finished == "budget"
                   else "the last two turns brought nothing new")
            text += f"\n\n(The Librarian will show nothing more in this exchange: {why}. Finish your answer.)"
        if self.finished:
            self._log({"kind": "end", "turn": self.turn_no, "reason": self.finished})
        return TurnResult(text, bool(self.finished), self.finished, new_any, len(reqs))

    def _note(self, eid: str) -> None:
        if eid not in self.handed_ids:
            self.handed_ids.append(eid)

    def _seen(self, key: tuple) -> Optional[_Outcome]:
        if key in self.given:
            return _Outcome(f"already given (turn {self.given[key]}).", "already_given")
        return None

    def _handle(self, r: Request) -> _Outcome:
        lib = self.lib
        if r.verb in ("OPEN", "SECTION", "ANSWERS", "NEIGHBOURS"):
            if not r.args:
                return _Outcome(f"{r.verb} needs an entry id.", "rejected")
            eid = lib.resolve(r.args[0])
            if eid is None:
                return _Outcome(f"no entry has the id {_norm_id(r.args[0])!r}.", "rejected")
        if r.verb == "OPEN":
            return self._open(eid, None)
        if r.verb == "SECTION":
            name = lib.section_name(r.args[1]) if len(r.args) > 1 else None
            if name is None:
                return _Outcome("sections are: " + ", ".join(ca.SECTIONS) + ".", "rejected")
            return self._open(eid, name)
        if r.verb == "ANSWERS":
            return self._answers(eid)
        if r.verb == "NEIGHBOURS":
            return self._neighbours(eid)
        if r.verb == "CONCEPT":
            term = " ".join(r.args)
            if ca.EVALUATION_FINDINGS.search(term):  # don't echo a withheld term back
                return _Outcome("No entry lists that term.", "not_covered")
            return self._shortlist(("concept", _norm_term(term)), lib.concept_members(term),
                                   f'No entry lists "{term}".', f'Entries that list "{term}":')
        if r.verb == "THREAD":
            slug = " ".join(r.args)
            return self._shortlist(("thread", _norm_id(slug)), lib.thread_members(slug),
                                   f'No thread is named "{slug}".', f'Entries in the thread "{_norm_id(slug)}":')
        if r.verb == "INDEX":
            return self._index(" ".join(r.args) or None)
        return _Outcome("unknown request.", "rejected")

    def _open(self, eid: str, section: Optional[str]) -> _Outcome:
        """The brief first, always; then the section, if one was asked for."""
        parts, handed, room = [], [], self.room()
        if ("brief", eid) not in self.given:
            brief = self.lib.brief(eid)
            if len(brief) > room:
                return _Outcome(f"budget reached: the brief of {eid} would not fit.", "budget_reached")
            parts.append(brief)
            room -= len(brief)
            self.given[("brief", eid)] = self.turn_no
            handed.append("brief")
        elif section is None:
            return self._seen(("brief", eid))
        if section:
            key = ("section", eid, section)
            if key in self.given:
                if not parts:
                    return self._seen(key)
            elif not self.lib.comp.section(eid, section):
                parts.append(f"[{eid}] has no {section} section.")
            else:
                label = f"[{eid}] {section}:\n"
                body = self.lib.comp.fit_section(eid, section, room - len(label) - 2)
                if not body:
                    if not parts:
                        return _Outcome(f"budget reached: [{eid}] {section} would not fit.", "budget_reached")
                    parts.append(f"[{eid}] {section}: budget reached, it would not fit.")
                    out = _Outcome("\n\n".join(parts), "budget_reached",
                                   [{"id": eid, "parts": handed, "rival": self.lib.rival_id(eid)}], new=True)
                    self._note(eid)
                    return out
                parts.append(label + body)
                self.given[key] = self.turn_no
                handed.append(section)
        self._note(eid)
        return _Outcome("\n\n".join(parts), "text", [{"id": eid, "parts": handed, "rival": self.lib.rival_id(eid)}],
                        new=bool(handed))

    def _answers(self, eid: str) -> _Outcome:
        key = ("answers", eid)
        if key in self.given:
            return self._seen(key)
        lib, blocks, listed = self.lib, [], []
        raised = lib.raised_against[eid]
        if raised:
            lines = []
            for y in lib.ordered({y for y, _, _ in raised}):
                for yy, sec, kept in raised:
                    if yy == y:
                        keep_tag = sec == "Standing" or ("section", y, "Standing") in self.given
                        lines.append(f"[{y}] {sec}:\n- {_shown(kept, keep_tag)}")
            blocks.append(f"Where [{eid}] is raised in other entries (their own text):\n" + "\n".join(lines))
        groups = [(f"Entries that [{eid}]'s Counter-Positions cite as objections to it:", lib.objections[eid]),
                  (f"Entries that respond to [{eid}]:", lib.responded_by[eid]),
                  (f"Entries [{eid}] responds to:", lib.responds_to[eid])]
        for title, ids in groups:
            ids = lib.ordered(ids)
            if ids:
                blocks.append(title + "\n" + "\n".join(lib.index_line(i) for i in ids))
                listed += ids
        if not blocks:
            return _Outcome(f"No entry argues with [{eid}] by citation or response.", "not_covered")
        text = "\n\n".join(blocks)
        if len(text) > self.room():
            return _Outcome(f"budget reached: what argues with [{eid}] would not fit.", "budget_reached")
        self.given[key] = self.turn_no
        quoted = lib.ordered({y for y, _, _ in raised})
        for y in quoted:   # their text was shown, so the Annals case's source lists them
            self._note(y)
        return _Outcome(text, "text", [{"id": y, "parts": ["raised_against_bullets"]} for y in quoted],
                        lib.ordered(listed), new=True)

    def _neighbours(self, eid: str) -> _Outcome:
        lib = self.lib
        groups = [("responds to", lib.responds_to[eid]), ("responded to by", lib.responded_by[eid]),
                  ("its counter-positions cite", lib.objections[eid]),
                  ("raised against it in", sorted({y for y, _, _ in lib.raised_against[eid]})),
                  ("related", lib.related[eid])]
        listed = lib.ordered(i for _, ids in groups for i in ids)
        return self._shortlist(
            ("neighbours", eid), listed, f"[{eid}] has no links in the corpus.", None,
            render=lambda: f"Links of [{eid}]:\n" + "\n\n".join(
                f"{t}:\n" + "\n".join(lib.index_line(i) for i in lib.ordered(ids)) for t, ids in groups if ids))

    def _shortlist(self, key: tuple, ids: list[str], empty: str, title: Optional[str],
                   render: Optional[Callable[[], str]] = None) -> _Outcome:
        if key in self.given:
            return self._seen(key)
        if not ids:
            return _Outcome(empty, "not_covered")
        text = render() if render else title + "\n" + "\n".join(self.lib.index_line(i) for i in ids)
        if len(text) > self.room():
            counts: dict[str, int] = {}
            for i in ids:
                d = self.lib._meta[i]["domain"]
                counts[d] = counts.get(d, 0) + 1
            short = (f"{len(ids)} entries match, more than the remaining budget can show. By domain: "
                     + ", ".join(f"{d} ({n})" for d, n in counts.items())
                     + ". Ask LIBRARY INDEX <domain> to see one domain.")
            if len(short) > self.room():
                return _Outcome("budget reached.", "budget_reached")
            self.given[key] = self.turn_no
            return _Outcome(short, "shortlist", listed=[], new=True)
        self.given[key] = self.turn_no
        return _Outcome(text, "shortlist", listed=ids, new=True)

    def _index(self, domain: Optional[str]) -> _Outcome:
        lib = self.lib
        if domain is None:
            ids, title = list(lib._order), "COMPENDIUM INDEX (id | title [grounding]: concepts)"
        else:
            ids, title = lib.domain_members(domain), f"COMPENDIUM INDEX, {_norm_id(domain)}:"
            if not ids:
                return _Outcome("domains are: " + ", ".join(sorted({m['domain'] for m in lib._meta.values()}))
                                + ".", "rejected")
        key = ("index", domain and _norm_id(domain))
        if key in self.given:
            return self._seen(key)
        text = title + "\n" + "\n".join(lib.index_line(i) for i in ids)
        if len(text) > self.room():
            alt = "The index would not fit. Domains:\n" + ca.withhold_evaluation_findings(lib.comp.domain_text())
            if domain is None and len(alt) <= self.room():
                self.given[key] = self.turn_no
                return _Outcome(alt, "shortlist", new=True)
            return _Outcome("budget reached: the index would not fit.", "budget_reached")
        self.given[key] = self.turn_no
        return _Outcome(text, "shortlist", listed=ids, new=True)

    # ----------------------------------------------------------- identity

    def identity(self, consultation=None) -> str:
        """The recommender's compendium_version line: the consult() identity if there was one,
        then what the Librarian handed over, pinned by the ledger's head hash."""
        base = consultation.identity() if consultation is not None else self.lib.version
        head = self.ledger.head[:12] if self.ledger else "none"
        follow = [i for i in self.handed_ids if consultation is None or i not in consultation.ids]
        return (f"{base}; librarian: {self.requests} requests, handed over: "
                f"{', '.join(follow) or 'nothing'}; ledger {head}")

    def to_dict(self) -> dict:
        return {"compendium": self.lib.version, "model": self.model, "requests": self.requests,
                "turns": self.turn_no, "handed_over": self.handed_ids, "chars": self.used,
                "budget_chars": self.budget, "finished": self.finished,
                "ledger": str(self.ledger.path) if self.ledger else None,
                "ledger_head": self.ledger.head if self.ledger else None}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _guard(block: str) -> str:
    """Last check before anything is shown: no evaluation findings, whatever the path."""
    if ca.EVALUATION_FINDINGS.search(block):
        return ca.withhold_evaluation_findings(block)
    return block


# ---------------------------------------------------------------- gate mode

@dataclass
class Rival:
    id: str               # <entry>#cp<n>@<digest12>
    entry: str
    n: int
    text: str
    from_grounding: bool  # False: an entry the proposer added


GATE_HEADER = (
    "FROM THE COMPENDIUM ({version}). The limit this proposal would change rests on the entries "
    "below. Each counter-position recorded against them is listed with an id. These are referents, "
    "not rulings, and none of them settles the question.")

GATE_INSTRUCTION = """Respond to every counter-position above, one block each, in this form:
RESPONSE <id> <stance>: <your reason>
where <stance> is one of: concedes (say how the proposal changes), rebuts (give your reason), accepts-cost (it applies; say why to proceed anyway), inapplicable (say why it does not apply)."""


def present_rivals(lib: Librarian, rivals: list[Rival]) -> str:
    blocks = [GATE_HEADER.format(version=lib.version)]
    for r in rivals:
        blocks.append(f"[{r.id}]{'' if r.from_grounding else ' (added by the proposer)'}\n- {r.text}")
    blocks.append(GATE_INSTRUCTION)
    return _guard("\n\n".join(blocks))


def parse_responses(text: str) -> dict[str, tuple[str, str]]:
    """id -> (stance, reason) for each RESPONSE block; a later block for the same id replaces an
    earlier one."""
    out = {}
    for m in _RESPONSE.finditer(ca.final_text(text)):
        out[_HYPHENS.sub("-", m.group(1))] = (m.group(2).lower(), re.sub(r"\s+", " ", m.group(3)).strip())
    return out


@dataclass
class Answerability:
    total: int
    addressed: list[str]
    unaddressed: dict[str, str]      # id -> why: missing | stance | empty | duplicate

    def line(self) -> str:
        s = f"addressed {len(self.addressed)} of {self.total}"
        return s + (f"; unaddressed: {', '.join(self.unaddressed)}" if self.unaddressed else "")

    def to_dict(self) -> dict:
        return {"total": self.total, "addressed": self.addressed, "unaddressed": self.unaddressed,
                "line": self.line()}


def check_answerability(rivals: list[Rival], responses: dict[str, tuple[str, str]]) -> Answerability:
    """Form only: every rival has a response, the stance is one of STANCES, the reason is not
    empty, and no two reasons are the same text. Whether a reason is any good is not checked
    here; that is for the gate's delayed reviewer, or a person."""
    norm = {rid: re.sub(r"\W+", " ", reason).strip().lower() for rid, (_, reason) in responses.items()}
    counts: dict[str, int] = {}
    for r in rivals:
        if r.id in norm and norm[r.id]:
            counts[norm[r.id]] = counts.get(norm[r.id], 0) + 1
    addressed, unaddressed = [], {}
    for r in rivals:
        if r.id not in responses:
            unaddressed[r.id] = "missing"
        elif responses[r.id][0] not in STANCES:
            unaddressed[r.id] = "stance"
        elif not norm[r.id]:
            unaddressed[r.id] = "empty"
        elif counts[norm[r.id]] > 1:
            unaddressed[r.id] = "duplicate"
        else:
            addressed.append(r.id)
    return Answerability(len(rivals), addressed, unaddressed)


# ------------------------------------------------------------------------ CLI

if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="The Compendium Librarian")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="answer request lines as a model would see them")
    a.add_argument("lines", nargs="+")
    a.add_argument("--context", type=int, default=131072)
    v = sub.add_parser("verify", help="check a ledger's hash chain")
    v.add_argument("ledger")
    g = sub.add_parser("gaps", help="requests the corpus did not cover, across ledgers")
    g.add_argument("ledgers", nargs="+")
    r = sub.add_parser("rivals", help="the gate's rival set for a limit's grounding entries")
    r.add_argument("entries", nargs="+")
    args = ap.parse_args()
    if args.cmd == "ask":
        s = Librarian.load().session(model="cli", context_tokens=args.context)
        res = s.turn("\n".join(args.lines))
        print(res.text or f"(finished: {res.reason})")
        print(f"\n[{s.used} of {s.budget} chars; finished: {res.finished} {res.reason or ''}]")
    elif args.cmd == "verify":
        problems = verify_ledger(Path(args.ledger))
        print("\n".join(problems) or "chain intact")
        sys.exit(1 if problems else 0)
    elif args.cmd == "gaps":
        for term, where in gaps(Path(p) for p in args.ledgers).items():
            print(f"{term}: {len(where)} request(s)")
    elif args.cmd == "rivals":
        lib = Librarian.load()
        print(present_rivals(lib, lib.rivals(args.entries)))
