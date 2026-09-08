"""core/registry.py — one Registry, three users.

tools/registry.py, skills/registry.py and agents/registry.py each carried their own copy
of the same shape: a module-level dict, a `list_*`, a `get_*`, a `describe_*` and a
`describe_all_*`. Three copies of one idea, drifting independently -- get_tool raised on a
miss while get_skill returned None, and describe_tool reported a field the other two never
had. This is that idea, once.

A MutableMapping, not a bag with methods. Every call site already treats these as dicts
(`TOOL_REGISTRY[name]`, `.values()`, `.get()`, `.items()`), and tests insert fixtures
directly (`TOOL_REGISTRY["my_test_tool"] = meta`) and delete them afterwards. Subclassing
MutableMapping keeps all of that working unchanged while giving the frame one place to put
behaviour that used to be copied.

AN INSTANCE, NOT A MODULE GLOBAL. The old registries were module-level dicts, so there
could only ever be one set of tools in a process. A registry you can construct is what
lets a second report type carry its own -- which is the difference between a frame and a
program that happens to be well factored.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Callable, Generic, Iterator, Optional, TypeVar

E = TypeVar("E")


class DuplicateRegistration(ValueError):
    """A name was registered twice.

    Raised rather than overwritten: a silent replacement means the entry a caller
    resolves depends on import order, and the loser disappears with no signal. The one
    legitimate re-registration -- a test installing a fixture -- goes through __setitem__,
    which is deliberately permissive.
    """


class Registry(MutableMapping, Generic[E]):
    """Named entries of one kind (tools, skills, agents), addressable like a dict.

    `kind` names what is being registered and appears in error messages, so a miss says
    "no tool named 'x'" rather than "KeyError: 'x'".
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._entries: dict[str, E] = {}

    # ---------------------------------------------------------------- mapping protocol
    def __getitem__(self, name: str) -> E:
        return self._entries[name]

    def __setitem__(self, name: str, entry: E) -> None:
        """Permissive by design: this is the seam tests use to install a fixture and
        remove it again. `register` is the strict door that production code goes through."""
        self._entries[name] = entry

    def __delitem__(self, name: str) -> None:
        del self._entries[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"<Registry {self.kind}: {len(self._entries)} entries>"

    # ------------------------------------------------------------------------- writing
    def register(self, name: str, entry: E) -> E:
        """Add an entry, refusing a name that is already taken. Returns the entry."""
        if name in self._entries:
            raise DuplicateRegistration(
                f"{self.kind} {name!r} is already registered — two definitions of one "
                f"name means the winner depends on import order")
        self._entries[name] = entry
        return entry


    def entries(self, sort_key: Optional[Callable[[E], Any]] = None, **match: Any) -> list[E]:
        """Entries whose attributes equal every keyword given, in a stable order.

        `entries()` is everything; `entries(produces="market_sizing")` is the filtered
        `list_skills(produces=...)` this replaces. Sorted so callers and generated docs get
        the same order every run rather than insertion order -- by name unless the caller
        wants a grouping (tools read better by category, skills by what they produce).
        """
        out = [e for e in self._entries.values()
               if all(getattr(e, k, None) == v for k, v in match.items())]
        return sorted(out, key=sort_key or (lambda e: getattr(e, "name", "")))

    def describe(self, name: str, fields: tuple[str, ...]) -> dict:
        """One entry as a JSON-able dict of `fields`.

        An unknown name returns {"error": ...} rather than raising, because every caller
        of this is a description surface (an API route, a docs page) where a miss is data
        to render, not an exception to handle.
        """
        entry = self._entries.get(name)
        if entry is None:
            return {"error": f"{self.kind} {name!r} not registered"}
        return {f: getattr(entry, f, None) for f in fields}

