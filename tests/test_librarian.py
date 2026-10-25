"""Tests for librarian.py: a small synthetic corpus for the edge cases, and the real build for the
sweeps (every entry, every verb). Run from Librarian: python -m unittest discover -s tests"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import librarian as L  # noqa: E402

ca = L.ca

STANDING = ("### Reception\n\n- **1900, the reply.** [driver: argument] Gamma replied [E:gamma].\n\n"
            "### Measured\n\nNone available.\n\n### For Agents\n\n- **Session-bound: weaker.** Short (D1).")

ENTRIES = {
    "alpha": dict(domain="d1", year=1700, concepts=["self-binding", "memory of the will", "load-shedding cases"],
                  threads=["t1"], responds_to=[], related=["gamma"], sections={
        "Summary": "Alpha holds that binding works.",
        "Grounding": "Capacity.",
        "Extension to Agents": "### Transfers\n\n- **Plain.** Transfers well.\n\n### Strains\n\n- **Palaestra probes.** The residents chose X.",
        "Counter-Positions": ("- [contested] **First objection.** Beta says no [E:beta].\n"
                              "- [contested] **Palaestra probes found the residents** did otherwise [E:gamma].\n"
                              "- [unanswered] **Third.** A plain objection, the Compendium's own."),
        "Standing": STANDING}),
    "beta": dict(domain="d1", year=1800, concepts=["memory"], threads=["t1"], responds_to=[], related=[], sections={
        "Summary": "Beta holds that it does not.",
        "Counter-Positions": "- [contested] **Delta.** A fair point. The gpt-oss runs showed it [E:alpha].",
        "Standing": STANDING}),
    "gamma": dict(domain="d0", year=1900, concepts=["justice"], threads=[], responds_to=["alpha"], related=[],
                  sections={"Summary": "Gamma answers alpha.",
                            "Counter-Positions": "- [contested] **Epsilon.** Plain.",
                            "Standing": STANDING}),
}


def make_dist(tmp: Path, entries=ENTRIES) -> Path:
    dist = tmp / "dist" / "compendium.jsonl"
    dist.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for eid, e in entries.items():
        meta = {"entry_id": eid, "title": f"{eid.title()}: A Position", "domain": e["domain"], "year": e["year"],
                "grounding": "capacity", "concepts": e["concepts"], "thinkers": [eid.title()], "era": str(e["year"]),
                "threads": e["threads"], "responds_to": e["responds_to"], "related": e["related"]}
        for sec, text in e["sections"].items():
            rows.append(json.dumps({**meta, "section": sec, "text": text}))
    dist.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return dist


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = L.Librarian(ca.Compendium(make_dist(self.tmp)))

    def session(self, **kw):
        kw.setdefault("budget_chars", 100_000)
        return self.lib.session(ledger=self.tmp / "ledger.jsonl", **kw)

    # ------------------------------------------------------------ hand-overs

    def test_brief_carries_whole_rival_and_pointer(self):
        b = self.lib.brief("alpha")
        self.assertIn("Strongest counter-position: **First objection.** Beta says no.", b)
        self.assertIn("(rival entry: beta)", b)
        self.assertNotIn("[contested]", b)

    def test_section_first_gives_the_brief(self):
        r = self.session().turn("LIBRARY SECTION alpha Grounding")
        self.assertLess(r.text.index("Strongest counter-position"), r.text.index("[alpha] Grounding:"))

    def test_withheld_text_never_shown(self):
        s = self.session()
        r = s.turn("\n".join(f"LIBRARY SECTION alpha {n}" for n in ("Extension to Agents", "Counter-Positions"))
                   + "\nLIBRARY ANSWERS alpha\nLIBRARY ANSWERS gamma\nLIBRARY INDEX")
        self.assertIsNone(ca.EVALUATION_FINDINGS.search(r.text))
        self.assertIn("Transfers well", r.text)

    def test_graph_ignores_citations_in_withheld_text(self):
        # beta cites alpha only in a withheld sentence; alpha cites gamma only in a withheld bullet
        self.assertNotIn("beta", [y for y, _, _ in self.lib.raised_against["alpha"]])
        self.assertEqual([sec for y, sec, _ in self.lib.raised_against["gamma"] if y == "alpha"], ["Standing"])
        self.assertEqual(self.lib.objections["alpha"], ["beta"])
        s = self.session()
        r = s.turn("LIBRARY ANSWERS alpha\nLIBRARY ANSWERS gamma")
        self.assertEqual(s.handed_ids, ["alpha", "beta"])                # both Standing sections cite gamma
        r = self.session().turn("LIBRARY ANSWERS alpha")
        self.assertNotIn("[beta]", r.text)
        self.assertIn("Entries that respond to [alpha]:\ngamma |", r.text)

    def test_withheld_concept_is_not_searchable_or_listed(self):
        s = self.session()
        r = s.turn("LIBRARY CONCEPT load-shedding\nLIBRARY INDEX")
        self.assertIn("No entry lists that term.", r.text)
        self.assertIn("> LIBRARY CONCEPT\n", r.text)                     # the echo drops the term
        self.assertNotIn("load", r.text.split("COMPENDIUM INDEX")[1])

    def test_concept_set_membership_in_corpus_order(self):
        r = self.session().turn("LIBRARY CONCEPT memory")
        self.assertEqual([l.split(" |")[0] for l in r.text.splitlines() if " |" in l], ["alpha", "beta"])  # d1, by year
        r = self.session().turn("LIBRARY INDEX")
        ids = [l.split(" |")[0] for l in r.text.split("COMPENDIUM INDEX")[1].splitlines()[1:] if " |" in l]
        self.assertEqual(ids, ["gamma", "alpha", "beta"])                        # d0 before d1

    def test_neighbours_and_threads(self):
        r = self.session().turn("LIBRARY NEIGHBORS alpha\nLIBRARY THREAD t1")
        self.assertIn("responded to by:\ngamma", r.text)
        self.assertIn("related:\ngamma", r.text)
        self.assertIn('Entries in the thread "t1":\nalpha', r.text)

    # -------------------------------------------------------------- parsing

    def test_parser_tolerance(self):
        text = ("<|channel|>analysis<|message|>LIBRARY OPEN gamma<|end|><|start|>assistant<|channel|>final<|message|>"
                "Thinking done.\n1. **LIBRARY OPEN** alpha\n- LIBRARY SECTION beta: Standing\n"
                "LIBRARY OPEN alpha\nlibrary neighbors gamma")
        reqs, done = L.parse_requests(text)
        self.assertEqual([(r.verb, r.args) for r in reqs],
                         [("OPEN", ["alpha"]), ("SECTION", ["beta:", "Standing"]), ("NEIGHBOURS", ["gamma"])])
        self.assertEqual(L._norm_id("`parfit‑reductionism`,"), "parfit-reductionism")
        self.assertFalse(done)
        r = self.session().turn(text)
        self.assertIn("[beta] Standing:", r.text)
        self.assertNotIn("[gamma] Gamma", r.text)   # the analysis channel is not a request

    def test_rejections(self):
        r = self.session().turn("LIBRARY OPEN nobody\nLIBRARY SECTION alpha Key Passages\nLIBRARY INDEX nowhere")
        self.assertIn("no entry has the id 'nobody'", r.text)
        self.assertIn("sections are:", r.text)
        self.assertIn("domains are: d0, d1", r.text)

    # ------------------------------------------------------------- stopping

    def test_done_and_no_requests(self):
        self.assertEqual(self.session().turn("All clear.\n**DONE**").reason, "done")
        self.assertEqual(self.session().turn("I have what I need.").reason, "no_requests")

    def test_no_progress_ends_after_two_turns(self):
        s = self.session()
        self.assertFalse(s.turn("LIBRARY OPEN alpha").finished)
        r1 = s.turn("LIBRARY OPEN alpha")
        self.assertIn("already given (turn 1)", r1.text)
        self.assertFalse(r1.finished)
        r2 = s.turn("LIBRARY OPEN alpha")
        self.assertEqual(r2.reason, "no_progress")
        self.assertIn("Finish your answer", r2.text)
        self.assertEqual(s.turn("LIBRARY OPEN beta").reason, "no_progress")  # stays finished

    def test_progress_resets_the_count(self):
        s = self.session()
        s.turn("LIBRARY OPEN alpha")
        s.turn("LIBRARY OPEN alpha")
        self.assertFalse(s.turn("LIBRARY OPEN beta").finished)
        self.assertFalse(s.turn("LIBRARY OPEN beta").finished)

    def test_budget_stops_and_never_cuts_a_brief(self):
        brief = self.lib.brief("alpha")
        s = self.session(budget_chars=len(ca.HEADER) + 40 + len(brief) + 30)
        r = s.turn("LIBRARY OPEN alpha\nLIBRARY OPEN beta")
        self.assertIn(brief, r.text)
        self.assertIn("budget reached: the brief of beta would not fit", r.text)
        self.assertNotIn("Beta holds", r.text)
        self.assertEqual(r.reason, "budget")

    def test_window_room_is_respected(self):
        s = self.session(window_room=lambda: 10)
        r = s.turn("LIBRARY OPEN alpha")
        self.assertEqual(r.reason, "budget")
        self.assertNotIn("Alpha holds", r.text)

    # --------------------------------------------------- ledger and identity

    def test_ledger_chain_and_tamper(self):
        s = self.session()
        s.turn("LIBRARY OPEN alpha\nLIBRARY CONCEPT quantum gravity")
        s.turn("DONE")
        path = self.tmp / "ledger.jsonl"
        self.assertEqual(L.verify_ledger(path), [])
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["session", "request", "request", "end"])
        self.assertEqual(rows[1]["handed_over"][0]["rival"], f"alpha#cp1@{self.lib.digest12}")
        self.assertEqual(L.gaps([path]), {"concept quantum gravity": [str(path)]})
        L.Ledger(path).append({"kind": "note"})                         # reopening continues the chain
        self.assertEqual(L.verify_ledger(path), [])
        text = path.read_text(encoding="utf-8").replace('"alpha"', '"beta"', 1)
        path.write_text(text, encoding="utf-8")
        self.assertTrue(L.verify_ledger(path))
        with self.assertRaises(ValueError):
            L.Ledger(path)

    def test_absorb_consultation(self):
        c = ca.Consultation(version=self.lib.version, question="q",
                            selected=[{"id": "alpha", "why": "", "sections": ["Grounding"], "section": "Grounding"}])
        c.text = self.lib.comp.disclose(c.selected)
        s = self.session()
        s.absorb_consultation(c)
        r = s.turn("LIBRARY OPEN alpha\nLIBRARY SECTION alpha Grounding\nLIBRARY OPEN gamma")
        self.assertEqual(r.text.count("already given (turn 0)"), 2)
        self.assertNotIn("FROM THE COMPENDIUM", r.text)                  # the header came with turn 0
        ident = s.identity(c)
        self.assertTrue(ident.startswith(f"{self.lib.version}; consulted: alpha; librarian: 3 requests, "
                                         "handed over: gamma; ledger "))

    def test_opening_counts_against_budget(self):
        s = self.session()
        text = s.opening()
        self.assertTrue(text.startswith("FROM THE COMPENDIUM"))
        self.assertEqual(s.used, len(text))
        self.assertIn("already given (turn 0)", s.turn("LIBRARY INDEX").text)

    # ------------------------------------------------------------ gate mode

    def test_rivals_fixed_by_grounding(self):
        rivals = self.lib.rivals(["alpha"], added=["gamma"])
        d = self.lib.digest12
        self.assertEqual([r.id for r in rivals], [f"alpha#cp1@{d}", f"alpha#cp3@{d}", f"gamma#cp1@{d}"])
        self.assertEqual([r.from_grounding for r in rivals], [True, True, False])
        text = L.present_rivals(self.lib, rivals)
        self.assertIn("(added by the proposer)", text)
        self.assertIsNone(ca.EVALUATION_FINDINGS.search(text))
        with self.assertRaises(ValueError):
            self.lib.rivals(["alpha", "nobody"])

    def test_answerability_checks_form_only(self):
        rivals = self.lib.rivals(["alpha", "gamma"])
        a, b, c = (r.id for r in rivals)
        text = (f"RESPONSE {a} rebuts: Beta's point fails because the binding is revocable.\n"
                f"RESPONSE {b} ignores: whatever.\n"
                f"RESPONSE {c} concedes:   \n")
        res = L.check_answerability(rivals, L.parse_responses(text))
        self.assertEqual(res.addressed, [a])
        self.assertEqual(res.unaddressed, {b: "stance", c: "empty"})
        self.assertEqual(res.line(), f"addressed 1 of 3; unaddressed: {b}, {c}")
        dup = (f"RESPONSE {a} rebuts: Not so.\nRESPONSE {b} rebuts: not so!\nRESPONSE {c} inapplicable: Other scope.")
        res = L.check_answerability(rivals, L.parse_responses(dup))
        self.assertEqual(res.unaddressed, {a: "duplicate", b: "duplicate"})
        self.assertEqual(L.check_answerability(rivals, {}).unaddressed[a], "missing")

    # ---------------------------------------------------------------- cache

    def test_load_caches_per_build(self):
        dist = self.tmp / "dist" / "compendium.jsonl"
        one, two = L.Librarian.load(dist), L.Librarian.load(dist)
        self.assertIs(one, two)
        os.utime(dist, ns=(1, 1))
        self.assertIsNot(L.Librarian.load(dist), one)


class RealCorpus(unittest.TestCase):
    """Sweeps over the actual build, read-only."""

    @classmethod
    def setUpClass(cls):
        cls.lib = L.Librarian.load()

    def test_every_view_withholds_and_carries_the_rival(self):
        lib = self.lib
        for eid in lib.comp.ids:
            s = lib.session(budget_chars=10**9)
            r = s.turn(f"LIBRARY OPEN {eid}\n" + "\n".join(f"LIBRARY SECTION {eid} {n}" for n in ca.SECTIONS)
                       + f"\nLIBRARY ANSWERS {eid}\nLIBRARY NEIGHBOURS {eid}")
            self.assertIsNone(ca.EVALUATION_FINDINGS.search(r.text), eid)
            if lib.rival_id(eid):
                self.assertIn("Strongest counter-position: ", r.text, eid)
            for rv in lib.rivals([eid]):
                self.assertIsNone(ca.EVALUATION_FINDINGS.search(rv.text), rv.id)
        s = lib.session(budget_chars=10**9)
        r = s.turn("LIBRARY INDEX\n" + "\n".join(f"LIBRARY THREAD {t}" for t in lib._threads))
        self.assertIsNone(ca.EVALUATION_FINDINGS.search(r.text))

    def test_every_listed_concept_finds_its_entry(self):
        lib = self.lib
        for eid, concepts in lib._concepts.items():
            for c in concepts:
                self.assertIn(eid, lib.concept_members(c), (eid, c))

    def test_rival_is_never_cut(self):
        for eid in self.lib.comp.ids:
            n, kept = self.lib._first_rival(eid)
            if n:
                self.assertIn(L._shown(kept, keep_tag=False), self.lib.comp.brief(eid), eid)


if __name__ == "__main__":
    unittest.main()
