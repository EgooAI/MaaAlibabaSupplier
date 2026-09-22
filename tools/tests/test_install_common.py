"""Release selection for install scripts must honor pinned version patterns."""

import unittest
from unittest.mock import patch

from tools import _common


class ReleasePatternTests(unittest.TestCase):
    def test_pin_keeps_minor_and_allows_prereleases(self):
        tags = ["1.5.0", "1.40.0", "1.4.8-beta19", "1.4.7"]
        with patch.object(_common, "fetch_json", return_value=[{"tag_name": tag} for tag in tags]):
            release = _common.github_release_by_tag_pattern("owner/repo", "1.4.*")
        self.assertEqual(release["tag_name"], "1.4.8-beta19")

    def test_unmatched_pattern_fails(self):
        with patch.object(_common, "fetch_json", return_value=[{"tag_name": "1.5.0"}]):
            with self.assertRaises(SystemExit) as error:
                _common.github_release_by_tag_pattern("owner/repo", "1.4.*")
        self.assertIn("1.4.*", str(error.exception))


if __name__ == "__main__":
    unittest.main()
