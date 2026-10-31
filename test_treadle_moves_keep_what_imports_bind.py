"""A move into treadle/ must not change what any import statement binds.

`import url_guard` binds the name url_guard. Rewritten naively to `import treadle.url_guard`
it binds `treadle` instead, and every later `url_guard.check(...)` is a NameError that
surfaces far from the move that caused it. scripts/treadle_move.py rewrites each import
form so the bound name is the one the code already uses.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location(
    "treadle_move", pathlib.Path(__file__).parent / "scripts" / "treadle_move.py")
mover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mover)

MOVED = ["core", "logger", "url_guard"]


class EachImportFormBindsWhatItDidBefore(unittest.TestCase):
    def check(self, before: str, after: str) -> None:
        self.assertEqual(mover.retarget(before, MOVED), after)

    def test_from_a_module(self):
        self.check("from logger import get\n", "from treadle.logger import get\n")

    def test_from_a_submodule(self):
        self.check("from core.section import Section\n",
                   "from treadle.core.section import Section\n")

    def test_plain_import_keeps_its_name(self):
        self.check("import url_guard\n", "from treadle import url_guard\n")

    def test_plain_import_with_an_alias(self):
        self.check("import logger as L\n", "from treadle import logger as L\n")

    def test_dotted_import_with_an_alias(self):
        self.check("import core.section as S\n", "import treadle.core.section as S\n")

    def test_indented_imports_inside_functions(self):
        self.check("def f():\n    from logger import get\n    import url_guard\n",
                   "def f():\n    from treadle.logger import get\n"
                   "    from treadle import url_guard\n")

    def test_a_longer_name_that_starts_the_same_is_left_alone(self):
        for line in ("import loggerx\n", "from core_utils import x\n",
                     "import url_guards\n", "from treadle.core import y\n"):
            self.check(line, line)

    def test_a_dotted_import_without_an_alias_is_refused(self):
        # `import core.section` binds `core`; there is no rewrite that keeps that true.
        with self.assertRaises(SystemExit):
            mover.retarget("import core.section\n", MOVED)


if __name__ == "__main__":
    unittest.main()
