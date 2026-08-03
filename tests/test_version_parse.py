"""
Unit tests for the version parsing/comparison helpers in
agent/services/updater.py. Uses only stdlib unittest — no third-party deps,
consistent with the zero-dependency agent install.

Run with:  python -m unittest discover -s tests
"""

import io
import json
import os
import sys
import unittest
from unittest import mock

_AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from services.updater import (  # noqa: E402
    _parse_version,
    _resolve_latest_version,
    _version_tuple,
    update_status,
)


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


class TestResolveLatestVersion(unittest.TestCase):
    def test_returns_higher_of_release_and_main(self):
        release_body = json.dumps({"tag_name": "v1.4.1"})
        main_body = 'AGENT_VERSION = "1.4.3"\n'

        with mock.patch("services.updater._fetch_with_status", return_value=(200, release_body)), \
             mock.patch("services.updater._fetch", return_value=(True, main_body)):
            self.assertEqual(_resolve_latest_version(), "1.4.3")

    def test_returns_release_when_newer_than_main(self):
        release_body = json.dumps({"tag_name": "v1.5.0"})
        main_body = 'AGENT_VERSION = "1.4.3"\n'

        with mock.patch("services.updater._fetch_with_status", return_value=(200, release_body)), \
             mock.patch("services.updater._fetch", return_value=(True, main_body)):
            self.assertEqual(_resolve_latest_version(), "1.5.0")

    def test_falls_back_to_main_when_releases_fail(self):
        main_body = 'AGENT_VERSION = "1.4.3"\n'

        with mock.patch("services.updater._fetch_with_status", return_value=(404, "")), \
             mock.patch("services.updater._fetch", return_value=(True, main_body)):
            self.assertEqual(_resolve_latest_version(), "1.4.3")

    def test_falls_back_to_release_when_main_fails(self):
        release_body = json.dumps({"tag_name": "v1.4.1"})

        with mock.patch("services.updater._fetch_with_status", return_value=(200, release_body)), \
             mock.patch("services.updater._fetch", return_value=(False, "")):
            self.assertEqual(_resolve_latest_version(), "1.4.1")

    def test_returns_none_when_both_fail(self):
        with mock.patch("services.updater._fetch_with_status", return_value=(None, "")), \
             mock.patch("services.updater._fetch", return_value=(False, "")):
            self.assertIsNone(_resolve_latest_version())


class TestUpdateStatus(unittest.TestCase):
    def test_disabled_does_not_claim_next_metric_report(self):
        with mock.patch("services.updater._read_last_check_ts", return_value=0.0), \
             mock.patch("services.updater._read_last_remote_version", return_value=None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            update_status(auto_updates_enabled=False)
            text = out.getvalue()
        self.assertIn("disabled", text)
        self.assertIn("will not run until auto-updates are enabled", text)
        self.assertNotIn("on next metric report", text)

    def test_enabled_keeps_next_metric_report_when_never_checked(self):
        with mock.patch("services.updater._read_last_check_ts", return_value=0.0), \
             mock.patch("services.updater._read_last_remote_version", return_value=None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            update_status(auto_updates_enabled=True)
            text = out.getvalue()
        self.assertIn("enabled", text)
        self.assertIn("on next metric report", text)


if __name__ == "__main__":
    unittest.main()
