"""No test source in this repository creates a link.

The owner's rule, stated for every repository here: symlinks are prohibited
anywhere, junctions and reparse points included. A case that needs an alias
hands in the answer the decision would have received, so the property is
proved on every host instead of skipped where the privilege is missing.

What counts as a link, and why the scan reads Python as a syntax tree, is
stated once in `tests/link_guard.py`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from link_guard import LinkCreationGuard  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoLinkCreationTests(unittest.TestCase):
    def test_no_test_source_creates_a_link(self) -> None:
        guard = LinkCreationGuard(REPO)
        self.assertEqual(guard.check(), [], "a test source creates a link")

    def test_the_guard_detects_the_shapes_it_claims(self) -> None:
        self.assertEqual(LinkCreationGuard.self_test(), [],
                         "the detector no longer finds what it is for")


if __name__ == "__main__":
    unittest.main()
