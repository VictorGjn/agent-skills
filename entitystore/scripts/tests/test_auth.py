#!/usr/bin/env python3
"""Bearer-token verification (M12) — cb_auth.verify_token() unit tests.

All tokens/roles here are obviously-fake fixtures (this repo, VictorGjn/
agent-skills, is PUBLIC) — "widget-reader-token-001" etc, never a real
company-brain secret. Hermetic: builds its own token-map JSON files under a
TemporaryDirectory, no network, no real corpus.

Run: python -m pytest entitystore/scripts/tests/test_auth.py -v
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import cb_auth  # noqa: E402

FAKE_READER_TOKEN = "widget-reader-token-001"
FAKE_ADMIN_TOKEN = "widget-admin-token-002"
FAKE_UNKNOWN_ROLE_TOKEN = "widget-mystery-token-003"
FAKE_UNLISTED_TOKEN = "widget-never-issued-token-999"


def _sha256_key(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


class TestAuthFixtureHelpers(unittest.TestCase):
    """No I/O — pure hashing/shape checks."""

    def test_hash_token_matches_hashlib_directly(self):
        self.assertEqual(cb_auth._hash_token(FAKE_READER_TOKEN), _sha256_key(FAKE_READER_TOKEN))

    def test_default_role_caps_cover_all_four_levels(self):
        self.assertEqual(
            set(cb_auth.DEFAULT_ROLE_CAPS.values()),
            {"public", "internal", "confidential", "restricted"},
        )

    def test_load_role_caps_falls_back_to_defaults_when_absent(self):
        self.assertEqual(cb_auth.load_role_caps({}), cb_auth.DEFAULT_ROLE_CAPS)

    def test_load_role_caps_uses_file_roles_section_when_present(self):
        custom = {"roles": {"widget-role": "confidential"}}
        self.assertEqual(cb_auth.load_role_caps(custom), {"widget-role": "confidential"})

    def test_load_role_caps_ignores_empty_roles_section(self):
        self.assertEqual(cb_auth.load_role_caps({"roles": {}}), cb_auth.DEFAULT_ROLE_CAPS)


class TestVerifyTokenWithDefaultRoleCaps(unittest.TestCase):
    """Token map with no "roles" section — DEFAULT_ROLE_CAPS applies."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cb_auth_test_")
        self.tokens_path = pathlib.Path(self._tmp.name) / "tokens.json"
        self.tokens_path.write_text(json.dumps({
            _sha256_key(FAKE_READER_TOKEN): {"role": "reader"},
            _sha256_key(FAKE_ADMIN_TOKEN): {"role": "admin"},
            _sha256_key(FAKE_UNKNOWN_ROLE_TOKEN): {"role": "not-a-real-role"},
        }), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_known_reader_token_maps_to_public_cap(self):
        result = cb_auth.verify_token(FAKE_READER_TOKEN, tokens_path=self.tokens_path)
        self.assertEqual(result, ("reader", "public"))

    def test_known_admin_token_maps_to_restricted_cap(self):
        result = cb_auth.verify_token(FAKE_ADMIN_TOKEN, tokens_path=self.tokens_path)
        self.assertEqual(result, ("admin", "restricted"))

    def test_token_with_role_absent_from_cap_map_fails_closed(self):
        result = cb_auth.verify_token(FAKE_UNKNOWN_ROLE_TOKEN, tokens_path=self.tokens_path)
        self.assertIsNone(result)

    def test_unlisted_token_returns_none(self):
        result = cb_auth.verify_token(FAKE_UNLISTED_TOKEN, tokens_path=self.tokens_path)
        self.assertIsNone(result)

    def test_empty_or_none_presented_returns_none(self):
        self.assertIsNone(cb_auth.verify_token("", tokens_path=self.tokens_path))
        self.assertIsNone(cb_auth.verify_token(None, tokens_path=self.tokens_path))

    def test_near_miss_token_does_not_match(self):
        # Same prefix as a real token, single character off at the end —
        # guards against any accidental substring/prefix comparison.
        near_miss = FAKE_READER_TOKEN[:-1] + "X"
        self.assertIsNone(cb_auth.verify_token(near_miss, tokens_path=self.tokens_path))


class TestVerifyTokenWithCustomRolesSection(unittest.TestCase):
    """Token map WITH its own "roles" section overrides DEFAULT_ROLE_CAPS."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cb_auth_test_")
        self.tokens_path = pathlib.Path(self._tmp.name) / "tokens.json"
        self.tokens_path.write_text(json.dumps({
            _sha256_key(FAKE_READER_TOKEN): {"role": "widget-viewer"},
            "roles": {"widget-viewer": "confidential"},
        }), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_custom_role_cap_mapping_is_honored(self):
        result = cb_auth.verify_token(FAKE_READER_TOKEN, tokens_path=self.tokens_path)
        self.assertEqual(result, ("widget-viewer", "confidential"))

    def test_default_role_names_no_longer_apply(self):
        # "reader" isn't a key in this file's token map at all, so it can't
        # verify regardless of DEFAULT_ROLE_CAPS containing "reader".
        result = cb_auth.verify_token("some-other-token", tokens_path=self.tokens_path)
        self.assertIsNone(result)


class TestVerifyTokenFailsClosedOnBadInput(unittest.TestCase):
    def test_missing_tokens_path_env_and_arg_returns_none(self):
        # No tokens_path arg, and CB_AUTH_TOKENS_PATH is assumed unset in
        # this test process — verify_token must not raise.
        import os
        prev = os.environ.pop("CB_AUTH_TOKENS_PATH", None)
        try:
            self.assertIsNone(cb_auth.verify_token(FAKE_READER_TOKEN))
        finally:
            if prev is not None:
                os.environ["CB_AUTH_TOKENS_PATH"] = prev

    def test_nonexistent_file_returns_none(self):
        result = cb_auth.verify_token(
            FAKE_READER_TOKEN, tokens_path="C:/does/not/exist/tokens.json")
        self.assertIsNone(result)

    def test_malformed_json_returns_none(self):
        with tempfile.TemporaryDirectory(prefix="cb_auth_test_") as td:
            bad_path = pathlib.Path(td) / "tokens.json"
            bad_path.write_text("{not valid json", encoding="utf-8")
            result = cb_auth.verify_token(FAKE_READER_TOKEN, tokens_path=bad_path)
            self.assertIsNone(result)

    def test_json_array_instead_of_object_returns_none(self):
        with tempfile.TemporaryDirectory(prefix="cb_auth_test_") as td:
            arr_path = pathlib.Path(td) / "tokens.json"
            arr_path.write_text("[1, 2, 3]", encoding="utf-8")
            result = cb_auth.verify_token(FAKE_READER_TOKEN, tokens_path=arr_path)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
