"""
W5-6: context/compaction.py — fold the observation log to POINTERS, not to nothing.

W1/H13 already folds an over-long agent observation log into one summary line. But
that fold is lossy and terminal: the payload is gone, so nothing downstream can ever
answer "what did step 3 actually return?". The agent's own anti-thrash cap exists
because repeated folding digests its own summaries.

Microcompaction keeps the store. Each folded observation becomes a pointer with a
FIXED schema — {ref, headline, chars} — and the full text stays retrievable by ref.
The prompt shrinks; the evidence doesn't disappear.

The invariants under test:
  * refs are stable and unique, and resolve back to the exact original text;
  * folding twice is idempotent on already-folded entries (no summary-of-summaries);
  * the newest N observations are never folded — that is where detail matters;
  * a store round-trips through plain JSON, so it can live in the run ledger.
"""
from __future__ import annotations

import json
import unittest

from context.compaction import CompactionStore, compact

OBS = [f"step {i}: tool_{i}(arg) → count=3 payload={{\"k\": \"v{i}\"}}" for i in range(1, 11)]


class TestPointers(unittest.TestCase):
    def setUp(self):
        self.store = CompactionStore()
        self.folded = compact(OBS, keep_recent=4, store=self.store)

    def test_recent_observations_are_kept_verbatim(self):
        self.assertEqual(self.folded[-4:], OBS[-4:])

    def test_folded_entries_become_one_pointer_line(self):
        self.assertEqual(len(self.folded), 5)
        self.assertIn("compacted 6", self.folded[0])

    def test_every_folded_observation_is_retrievable_by_ref(self):
        self.assertEqual(len(self.store.refs()), 6)
        for i, ref in enumerate(self.store.refs()):
            self.assertEqual(self.store.get(ref), OBS[i])

    def test_the_pointer_line_names_the_refs_it_stands_for(self):
        for ref in self.store.refs():
            self.assertIn(ref, self.folded[0])

    def test_records_have_the_fixed_schema(self):
        rec = self.store.record(self.store.refs()[0])
        self.assertEqual(set(rec), {"ref", "headline", "chars"})
        self.assertIsInstance(rec["chars"], int)

    def test_unknown_ref_is_none_not_a_crash(self):
        self.assertIsNone(self.store.get("obs-999"))


class TestNoSummaryOfSummaries(unittest.TestCase):
    def test_folding_an_already_folded_log_does_not_refold_the_pointer(self):
        store = CompactionStore()
        once = compact(OBS, keep_recent=4, store=store)
        twice = compact(once + ["step 11: t(x) → y"], keep_recent=4, store=store)
        pointers = [line for line in twice if line.startswith("[compacted")]
        self.assertEqual(len(pointers), 1, "a pointer line got folded into another pointer")

    def test_originals_survive_a_second_fold(self):
        store = CompactionStore()
        compact(OBS, keep_recent=4, store=store)
        compact(OBS + ["step 11: t(x) → y"], keep_recent=2, store=store)
        self.assertEqual(store.get("obs-1"), OBS[0])

    def test_the_same_observation_folded_twice_keeps_one_ref(self):
        store = CompactionStore()
        compact(OBS, keep_recent=4, store=store)
        n = len(store.refs())
        compact(OBS, keep_recent=4, store=store)
        self.assertEqual(len(store.refs()), n)


class TestShortLogs(unittest.TestCase):
    def test_a_log_shorter_than_keep_recent_is_untouched(self):
        store = CompactionStore()
        self.assertEqual(compact(OBS[:3], keep_recent=4, store=store), OBS[:3])
        self.assertEqual(store.refs(), [])

    def test_empty_log(self):
        self.assertEqual(compact([], keep_recent=4, store=CompactionStore()), [])


class TestPersistence(unittest.TestCase):
    def test_round_trips_through_json(self):
        store = CompactionStore()
        compact(OBS, keep_recent=4, store=store)
        back = CompactionStore.from_dict(json.loads(json.dumps(store.to_dict())))
        self.assertEqual(back.get("obs-1"), OBS[0])
        self.assertEqual(back.refs(), store.refs())

    def test_tolerates_a_malformed_payload(self):
        store = CompactionStore.from_dict({"entries": "not-a-list"})
        self.assertEqual(store.refs(), [])


class TestAgentUsesIt(unittest.TestCase):
    def test_agent_compaction_preserves_the_originals(self):
        """The agent used to fold to a lossy line; the payloads are now recoverable."""
        import harness.agent as agent
        store = CompactionStore()
        folded = agent._compact_observations(OBS, keep_recent=4, store=store)
        self.assertEqual(folded[-4:], OBS[-4:])
        self.assertEqual(store.get("obs-1"), OBS[0])

    def test_agent_compaction_without_a_store_still_works(self):
        import harness.agent as agent
        folded = agent._compact_observations(OBS, keep_recent=4)
        self.assertEqual(len(folded), 5)


if __name__ == "__main__":
    unittest.main()
