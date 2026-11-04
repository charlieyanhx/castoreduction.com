"""SYN-1: the founder's stated price was never read, because it had no dollar sign.

The first analyst report Opus wrote (docs/plan-2026-09-12.md, SYN-1) said in as many
words that the founder's brief priced a drink "around 5.50" and every financial figure
used the PSM optimum of $5.75 instead, with pricing.price_of_record saying
no_stated_price. Reproduced on diag02: brief.extract_price requires a currency symbol,
and "around 5.50 for a drink" has none. A second miss sat beside it: "$5.50 for a drink"
has the symbol and still read as no price, because "for a" was not among the words the
price-then-unit pattern accepts between the amount and the noun.

THE RULE, kept narrow on purpose: a bare number is a price only when an approximator
("around", "about", "roughly", "approximately", "circa", "~") introduces it AND a
connector ("a", "per", "each", "for a") ties it to a unit noun. "seating for about 14
patrons", "roughly 1,200 square feet" and "about 400 customers a month" have no such
connector to a unit noun and stay what they are. A number with a symbol needs no
approximator, and "for a" now counts as a connector for it too.
"""
from __future__ import annotations

import unittest

from brief import extract_price


class ABareApproximatePriceIsRead(unittest.TestCase):
    def test_the_diag02_brief(self):
        p = extract_price("We would price a drink around 5.50 for a drink, pour-over and espresso.")
        self.assertIsNotNone(p, "the founder's stated price was not read")
        self.assertEqual(p["value"], 5.5)
        self.assertEqual(p["unit"], "drink")
        self.assertEqual(p["currency"], "USD")
        self.assertEqual(p["basis"], "stated price per drink")

    def test_the_approximators(self):
        for word in ("around", "about", "roughly", "approximately", "circa", "~"):
            with self.subTest(word=word):
                p = extract_price(f"we charge {word} 6 a cup")
                self.assertIsNotNone(p, word)
                self.assertEqual(p["value"], 6.0)
                self.assertEqual(p["unit"], "cup")

    def test_the_connectors(self):
        for text in ("about 12 per class", "about 12 a class", "about 12 each class",
                     "about 12 for a class", "about 12 for each class"):
            with self.subTest(text=text):
                p = extract_price(text)
                self.assertIsNotNone(p, text)
                self.assertEqual((p["value"], p["unit"]), (12.0, "class"))

    def test_for_a_counts_with_a_symbol_too(self):
        p = extract_price("$5.50 for a drink")
        self.assertIsNotNone(p)
        self.assertEqual((p["value"], p["unit"]), (5.5, "drink"))

    def test_an_approximator_after_the_noun_with_a_symbol(self):
        """"espresso around $5.50" and "a latte at about $6": the noun comes first and the
        approximator is the lead-in. With the symbol there, no connector is needed."""
        for text, unit, value in (("pour-over and espresso around $5.50", "espresso", 5.5),
                                  ("a latte at about $6", "latte", 6.0),
                                  ("each class costs roughly $12", "class", 12.0)):
            with self.subTest(text=text):
                p = extract_price(text)
                self.assertIsNotNone(p, text)
                self.assertEqual((p["value"], p["unit"]), (value, unit))

    def test_a_bare_number_after_the_noun_is_still_not_a_price(self):
        """"room for about 14" and "seats around 28" are counts with a unit noun in front
        of them; only a symbol makes the noun-first shape a price."""
        for text in ("room for about 14 people", "seats around 28", "a class of about 12"):
            with self.subTest(text=text):
                self.assertIsNone(extract_price(text), text)


class ABareNumberIsNotAPrice(unittest.TestCase):
    """The guard that keeps this narrow: every one of these is a quantity in a real brief,
    and reading it as a price would put a fabricated price of record on page one."""

    def test_counts_and_sizes_stay_counts_and_sizes(self):
        for text in ("seating for about 14 patrons",
                     "roughly 1,200 square feet on a corner site",
                     "about 400 customers a month",
                     "around 28 seats and a small roastery",
                     "we open around 7 each morning",
                     "roughly 3 staff per shift"):
            with self.subTest(text=text):
                self.assertIsNone(extract_price(text), text)

    def test_a_bare_number_without_an_approximator_is_not_a_price(self):
        self.assertIsNone(extract_price("5.50 for a drink"))
        self.assertIsNone(extract_price("we charge 6 a cup"))

    def test_the_symbol_price_still_wins_over_a_bare_one(self):
        p = extract_price("around 6 a cup, or $5.50 per drink for members")
        self.assertEqual(p["value"], 5.5, "a price with its symbol is the surer read")


if __name__ == "__main__":
    unittest.main()
