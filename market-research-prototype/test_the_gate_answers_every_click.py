"""Clicking a price did nothing, as far as anyone could tell.

REPORTED AS "we get stuck here no matter what to push next", and reproduced exactly. The
gate drew three price buttons. Clicking one POSTed /billing/checkout, which refused
because no processor was wired, and buy() put the reason here:

    err.textContent = "Checkout is not available right now: " + e.message;

`err` is the shared slot in the fixed footer bar: 12.5px warn ink, and MEASURED IN THE
BROWSER at y = -180 while the founder was looking at a card in the middle of the page. So
the one piece of feedback for a failed click was small text scrolled off the top of the
screen. The button reset its own label instantly, nothing appeared in the card, and the
screen was indistinguishable from one whose buttons were not wired to anything.

Worse, the one control that DID work sat underneath in faint dashed 12.5px text
("Continue without paying"), so a screen with a working way forward read as a dead end.

Three rules now, and they are about feedback rather than about payments:

  a refusal appears in the card, next to the button that caused it
  test mode says the buttons cannot charge BEFORE they are clicked, not after
  the only working action is styled like an action

The failure text also has to say the money is safe. "We could not open checkout" under a
price reads as a failed payment unless it is told otherwise.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


SRC = Path(__file__).parent / "web" / "survey.js"
CSS = Path(__file__).parent / "web" / "survey.html"


def _fn(body: str, header: str) -> str:
    """One function's source, header to its closing brace at indent 2."""
    start = body.index(header)
    end = body.index("\n  }", start)
    return body[start:end]


class ARefusalAppearsWhereTheFounderIsLooking(unittest.TestCase):
    def setUp(self):
        self.src = SRC.read_text(encoding="utf-8")
        self.buy = _fn(self.src, "async function buy(")

    def test_buy_writes_into_the_card_not_only_the_footer(self):
        self.assertIn("msg.hidden = false", self.buy,
                      "the refusal must be revealed inside the gate card")
        self.assertIn("msg.textContent", self.buy)

    def test_the_footer_slot_is_no_longer_the_only_report(self):
        """It may be cleared or kept, but it must not be the sole destination."""
        writes_err = re.search(r'err\.textContent\s*=\s*"[^"]*not available', self.buy)
        self.assertIsNone(
            writes_err,
            "the off-screen footer slot must not be where a failed purchase is reported")

    def test_the_message_says_nothing_was_charged(self):
        """Under a price button, an unqualified error reads as a failed payment."""
        self.assertIn("Nothing was charged", self.buy)

    def test_the_message_is_scrolled_into_view(self):
        self.assertIn("scrollIntoView", self.buy,
                      "a refusal below the fold is the bug this file exists for")

    def test_the_card_owns_a_message_element(self):
        draw = _fn(self.src, "function drawGate(")
        self.assertIn('el("p", "gate-msg")', draw)
        self.assertIn("box.appendChild(msg)", draw)

    def test_the_message_element_is_styled_to_be_seen(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn(".gate-msg", css)
        block = css[css.index(".gate-msg"):css.index(".gate-msg") + 320]
        self.assertIn("--warn", block, "a refusal should not render as body copy")


class TheClickAcknowledgesItself(unittest.TestCase):
    def setUp(self):
        self.buy = _fn(SRC.read_text(encoding="utf-8"), "async function buy(")

    def test_every_price_button_stops_inviting_a_second_click(self):
        self.assertRegex(self.buy, r"all\.forEach\(function \(b\) \{ b\.disabled = true",
                         "one purchase in flight means the others go quiet")

    def test_a_failure_re_enables_them(self):
        """A card left permanently disabled after a recoverable refusal is a worse dead
        end than the one this replaced."""
        self.assertRegex(self.buy, r"all\.forEach\(function \(b\) \{ b\.disabled = false")

    def test_the_label_reports_progress_and_is_restored(self):
        self.assertIn("Opening checkout", self.buy)
        self.assertIn("setLabel(was)", self.buy)


class TestModeSaysSoBeforeTheClick(unittest.TestCase):
    def setUp(self):
        self.src = SRC.read_text(encoding="utf-8")
        self.draw = _fn(self.src, "function drawGate(")

    def test_the_notice_is_drawn_before_the_offers(self):
        notice = self.draw.index("gate-preview")
        offers = self.draw.index("offers.forEach")
        self.assertLess(notice, offers,
                        "telling someone the buttons are dead after they click one wastes "
                        "the only action on screen that looks primary")

    def test_the_notice_says_what_pressing_a_price_will_do(self):
        """Test mode used to say the buttons could not charge a card, which was true and
        useless: it left the founder with no way to see the flow that follows a purchase.
        They now complete a simulated one, and the notice has to say so."""
        block = self.draw[self.draw.index("gate-preview"):]
        self.assertIn("simulated", block[:700])
        self.assertIn("No card is ever charged", block[:700])

    def test_the_escape_hatch_is_a_button_not_a_footnote(self):
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".gate-skip {"):css.index(".gate-skip {") + 420]
        self.assertNotIn("dashed", block,
                         "in test mode this is the only working action on the card")
        self.assertIn("var(--brand-action)", block)

    def test_the_escape_hatch_stays_distinct_from_the_things_being_sold(self):
        """An outline, not a fill. A way past the paywall must never be mistaken for one
        of the prices."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".gate-skip {"):css.index(".gate-skip {") + 420]
        self.assertIn("background: var(--surface)", block)

    def test_it_reports_progress_when_pressed(self):
        block = self.draw[self.draw.index("var skipBtn"):]
        self.assertIn("Starting your report", block[:800])


class TheGateStillOffersOnlyWhatItCanSell(unittest.TestCase):
    """Guard against the fix reintroducing a live-looking button on a real instance: the
    preview notice must be conditional, not unconditional."""

    def test_the_notice_only_appears_in_preview(self):
        draw = _fn(SRC.read_text(encoding="utf-8"), "function drawGate(")
        i = draw.index("gate-preview")
        before = draw[:i]
        self.assertIn("if (st.preview)", before[-400:],
                      "a live instance must never tell buyers its buttons are fake")


if __name__ == "__main__":
    unittest.main()


class ThePriceIsChosenBeforeItIsCharged(unittest.TestCase):
    """Each price used to be its own submit button, so choosing WAS committing.

    No way to compare two options, no way to change your mind, and the most expensive one
    a single misclick away. They are a radio group now with one action underneath, which
    is also what lets the button say what it is about to charge: "Continue to payment —
    $99" rather than a button you press to find out.
    """

    def setUp(self):
        self.src = SRC.read_text(encoding="utf-8")
        self.draw = _fn(self.src, "function drawGate(")

    def test_the_options_are_a_real_radio_group(self):
        """Not divs with click handlers: a native input gives arrow-key navigation and the
        group semantics to a screen reader for free."""
        self.assertIn('radio.type = "radio"', self.draw)
        self.assertIn('group.setAttribute("role", "radiogroup")', self.draw)

    def test_choosing_does_not_charge(self):
        """The radio's own handler must only record the choice."""
        i = self.draw.index("radio.onchange")
        handler = self.draw[i:i + 160]
        self.assertNotIn("buy(", handler,
                         "selecting an option must not open checkout")

    def test_one_action_carries_the_choice(self):
        self.assertIn("buy(chosen,", self.draw)

    def test_the_action_names_the_amount(self):
        self.assertIn("Continue to payment", self.draw)
        self.assertIn("p.toFixed(0)", self.draw)

    def test_something_is_chosen_from_the_start(self):
        """An action button with nothing selected is a dead button."""
        self.assertIn("var chosen = offers.length ? offers[0].kind : null", self.draw)

    def test_the_whole_group_goes_quiet_while_a_purchase_is_in_flight(self):
        buy = _fn(self.src, "async function buy(")
        self.assertIn(".gate-opt-radio", buy)


class TheMomentOfPurchaseIsNotAReceipt(unittest.TestCase):
    def setUp(self):
        self.src = SRC.read_text(encoding="utf-8")
        self.claim = _fn(self.src, "async function claimStep(")

    def test_every_buyer_sees_it_not_only_guests(self):
        """It began as the registration ask, so it ran only for guests and a signed-in
        buyer went from Stripe straight to a progress bar: they paid and the product said
        nothing."""
        resume = _fn(self.src, "async function resumeAfterPurchase(")
        self.assertIn("claimStep(paid, bought, !!whoami.authenticated)", resume)

    def test_it_leads_with_what_they_bought(self):
        self.assertIn("reports are yours", self.claim)

    def test_a_signed_in_buyer_is_asked_for_nothing(self):
        self.assertIn("if (signedIn)", self.claim)

    def test_motion_never_decides_whether_content_is_visible(self):
        """MEASURED: in a renderer that starts animations and never advances them, every
        element freezes on frame one. An animation that fades in from opacity 0, or draws
        a checkmark from an undrawn resting state, renders a blank card there. Only
        `transform` may be animated on this card."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index("@keyframes seal-rise"):css.index("@keyframes seal-pop") + 200]
        self.assertNotIn("opacity", block,
                         "a frozen animation would hide the card it decorates")
        self.assertNotIn("stroke-dashoffset", css[css.index(".gate-seal"):
                                                  css.index("@keyframes seal-pop")],
                         "the mark must be drawn at rest, not by the animation")
