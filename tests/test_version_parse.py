"""
Unit tests for the version parsing/comparison helpers in
agent/services/updater.py. Uses only stdlib unittest — no third-party deps,
consistent with the zero-dependency agent install.

Run with:  python -m unittest discover -s tests
"""

import os
import sys
import unittest

_AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from services.updater import _parse_version, _version_tuple  # noqa: E402


class TestParseVersion(unittest.TestCase):
    def test_parses_double_quoted_version(self):
        content = 'AGENT_VERSION = "1.4.1"\nOTHER = 1\n'
        self.assertEqual(_parse_version(content), "1.4.1")

    def test_parses_single_quoted_version(self):
        content = "AGENT_VERSION = '2.0.0'\n"
        self.assertEqual(_parse_version(content), "2.0.0")

    def test_parses_with_extra_whitespace(self):
        content = 'AGENT_VERSION   =   "1.2.3"\n'
        self.assertEqual(_parse_version(content), "1.2.3")

    def test_returns_none_when_missing(self):
        content = "SOME_OTHER_CONST = 42\n"
        self.assertIsNone(_parse_version(content))

    def test_returns_none_for_empty_content(self):
        self.assertIsNone(_parse_version(""))


class TestVersionTuple(unittest.TestCase):
    def test_basic_tuple(self):
        self.assertEqual(_version_tuple("1.4.1"), (1, 4, 1))

    def test_two_part_version(self):
        self.assertEqual(_version_tuple("2.0"), (2, 0))

    def test_invalid_version_falls_back_to_zero(self):
        self.assertEqual(_version_tuple("not-a-version"), (0,))

    def test_comparison_newer_greater_than_older(self):
        self.assertGreater(_version_tuple("1.4.1"), _version_tuple("1.4.0"))
        self.assertGreater(_version_tuple("1.10.0"), _version_tuple("1.9.9"))
        self.assertLess(_version_tuple("1.0.0"), _version_tuple("1.0.1"))

    def test_equal_versions_are_equal(self):
        self.assertEqual(_version_tuple("1.4.1"), _version_tuple("1.4.1"))


if __name__ == "__main__":
    unittest.main()
