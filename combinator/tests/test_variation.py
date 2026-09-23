from django.test import SimpleTestCase

from combinator import variation

H = ["H1", "H2", "H3"]
B = ["B1", "B2", "B3"]
C = ["C1", "C2", "C3"]


class CombinationTests(SimpleTestCase):
    def test_distinct_uses_each_hook_body_pair_once_and_spreads_closers(self):
        combos = variation.distinct_combinations(H, B, C)
        self.assertEqual(len(combos), 9)
        self.assertEqual(len({(h, b) for h, b, _ in combos}), 9)
        closers = [c for _, _, c in combos]
        self.assertEqual({c: closers.count(c) for c in C}, {"C1": 3, "C2": 3, "C3": 3})

    def test_all_combinations(self):
        self.assertEqual(len(variation.all_combinations(H, B, C)), 27)
        self.assertEqual(variation.all_combinations(["H1"], ["B1"], []), [("H1", "B1", None)])

    def test_counts_match_the_generators(self):
        for closers in ([], ["C1"], C):
            self.assertEqual(
                variation.combination_count("distinct", 3, 3, len(closers)),
                len(variation.distinct_combinations(H, B, closers)),
            )
            self.assertEqual(
                variation.combination_count("all", 3, 3, len(closers)),
                len(variation.all_combinations(H, B, closers)),
            )


class PublicationOrderTests(SimpleTestCase):
    def test_keeps_every_combo(self):
        combos = variation.all_combinations(H, B, C)
        self.assertCountEqual(variation.publication_order(combos), combos)

    def test_distinct_order_is_as_varied_as_possible(self):
        # In 3×3 distinct mode each video has only two "nothing in common" neighbours, forming
        # three separate 3-cycles, so the best possible walk has exactly 2 steps sharing one clip.
        order = variation.publication_order(variation.distinct_combinations(H, B, C))
        shared = [variation._shared_slots(a, b) for a, b in zip(order, order[1:])]
        self.assertEqual(max(shared), 1)
        self.assertEqual(sum(shared), 2)

    def test_all_mode_never_puts_closer_only_changes_back_to_back(self):
        # Every case has ≥3 hook+body pairs; with 2 pairs, videos two apart must repeat a pair.
        for hooks, bodies, closers in ((3, 3, 3), (5, 5, 4), (2, 2, 3), (2, 2, 2), (1, 4, 3), (4, 1, 2), (1, 3, 5)):
            combos = variation.all_combinations(
                [f"H{i}" for i in range(hooks)], [f"B{i}" for i in range(bodies)], [f"C{i}" for i in range(closers)]
            )
            order = variation.publication_order(combos)
            self.assertCountEqual(order, combos)
            # Videos that differ only in the closer are never neighbours nor two apart.
            for gap in (1, 2):
                for a, b in zip(order, order[gap:]):
                    self.assertFalse(a[:2] == b[:2], (hooks, bodies, closers, gap, a, b))


class SimilarityTests(SimpleTestCase):
    durations = {"H1": 3, "B1": 10, "B2": 10, "C1": 2, "C2": 2}

    def test_only_closer_differs_is_high(self):
        combos = [("H1", "B1", "C1"), ("H1", "B1", "C2")]
        result = variation.similarity(combos, self.durations.get)
        self.assertEqual(result[0]["level"], "high")
        self.assertEqual(result[0]["percent"], 87)  # (3 + 10) / 15
        self.assertEqual(result[0]["differs"], ["cierre"])

    def test_different_body_is_low(self):
        combos = [("H1", "B1", "C1"), ("H1", "B2", "C1")]
        result = variation.similarity(combos, self.durations.get)
        self.assertEqual(result[0]["level"], "low")
        self.assertEqual(result[0]["percent"], 33)  # (3 + 2) / 15
        self.assertEqual(result[0]["differs"], ["contenido"])

    def test_unknown_durations_weigh_segments_equally(self):
        combos = [("H1", "B1", None), ("H1", "B2", None)]
        result = variation.similarity(combos, lambda clip: None)
        self.assertEqual(result[0]["percent"], 50)

    def test_closer_only_sibling_wins_over_a_larger_overlap(self):
        # vs #2 only the closer differs (13 of 15 s shared); vs #3 only the hook differs (12 of 15 s).
        durations = {"H1": 3, "H2": 3, "B1": 10, "C1": 2, "C2": 2}
        combos = [("H1", "B1", "C1"), ("H1", "B1", "C2"), ("H2", "B1", "C1")]
        result = variation.similarity(combos, durations.get)
        self.assertEqual(result[2]["level"], "low")  # different hook, same body: fine
        self.assertEqual((result[0]["level"], result[0]["nearest"]), ("high", 1))

        durations["B1"], durations["H1"], durations["H2"] = 4, 1, 1  # now the hook-only sibling overlaps more
        result = variation.similarity(combos, durations.get)
        self.assertEqual((result[0]["level"], result[0]["nearest"]), ("high", 1))

    def test_same_footage_under_two_names_is_identical(self):
        combos = [("H1", "B1", None), ("H1", "B1-copy", None), ("H2", "B2", None)]
        result = variation.similarity(combos, lambda c: None, key=lambda c: c.replace("-copy", ""))
        self.assertEqual((result[0]["level"], result[0]["nearest"]), ("identical", 1))
        self.assertEqual(result[2]["level"], "low")

    def test_order_treats_same_footage_as_same_clip(self):
        combos = variation.all_combinations(["H1", "H2", "H3"], ["B1", "B1-copy", "B2"], [])
        order = variation.publication_order(combos, key=lambda c: c.replace("-copy", ""))
        self.assertCountEqual(order, combos)
        strip = lambda combo: tuple(c.replace("-copy", "") for c in combo[:2])
        for a, b in zip(order, order[1:]):
            self.assertNotEqual(strip(a), strip(b))

    def test_single_video(self):
        self.assertEqual(variation.similarity([("H1", "B1", None)], self.durations.get)[0]["nearest"], None)

    def test_distinct_mode_has_no_near_duplicates(self):
        combos = variation.distinct_combinations(H, B, C)
        self.assertTrue(all(r["level"] == "low" for r in variation.similarity(combos, lambda c: None)))
