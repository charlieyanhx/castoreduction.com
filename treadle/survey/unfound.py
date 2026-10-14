"""treadle/survey/unfound.py: what a changed value takes with it.

A FINDING IS ONLY AS GOOD AS WHAT IT WAS FOUND FROM. A currency found for Lisbon says
nothing about Tokyo, so when a person changes the city, every unconfirmed finding whose
basis names it is dropped, and the question comes back to be asked or looked up afresh.

THE DROP CARRIES DOWN THE CHAIN. A finding made from a dropped finding has lost its basis
too, so it goes with it, however far down. A person's word is never dropped: a value
someone stated or confirmed stands until that person changes it.

A RULING NEVER OUTLIVES ITS BASIS. A seal names the values a finding was found from
(finding.seal), so a 'not sure', a correction or a ruling on a dispute about the Lisbon
currency matches nothing found for Tokyo: that is a new finding nobody has seen. A held
finding also takes its seal with it when it goes. Kept, the seal would match the same
finding found again if the city changed back, and found() would keep nothing in its
place, the finding silently lost.

A FINDING MAY NOT REST ON WHAT IT REPLACES. A currency found from a season that was found
from the old currency would lose its basis the moment it is written, so it is refused
before it is held (rests_on), never dropped by its own write.
"""
from __future__ import annotations

from typing import Callable, Iterable

from .entry import FINDER, Entry


def unfound(changed: str, entries: tuple[Entry, ...], disputes: tuple[Entry, ...],
            settled: Iterable[str], seal: Callable[[Entry], str],
            ) -> tuple[tuple[Entry, ...], tuple[Entry, ...], frozenset[str]]:
    """`entries`, `disputes` and `settled` without everything found from `changed`, or
    from what was dropped because of it. A dropped dispute does not carry further: the
    statement it contradicted is still held."""
    gone_fields = {changed}
    dropped: list[Entry] = []
    while True:
        gone = [e for e in entries if e.by == FINDER and gone_fields & set(e.basis)]
        if not gone:
            break
        dropped += gone
        gone_fields |= {e.field for e in gone}
        entries = tuple(e for e in entries if e not in gone)
    dropped += [d for d in disputes if gone_fields & set(d.basis)]
    disputes = tuple(d for d in disputes if not gone_fields & set(d.basis))
    return entries, disputes, frozenset(settled) - {seal(e) for e in dropped}


def rests_on(name: str, basis: Iterable[str], entries: tuple[Entry, ...]) -> bool:
    """Whether a value found from `basis` rests on `name`, directly or through the
    findings `entries` hold. A person's word ends the chain: it rests on nothing found."""
    seen: set[str] = set()
    todo = set(basis)
    while todo:
        if name in todo:
            return True
        seen |= todo
        todo = {b for e in entries if e.by == FINDER and e.field in todo
                for b in e.basis} - seen
    return False


__all__ = ["rests_on", "unfound"]
