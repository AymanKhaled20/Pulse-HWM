from __future__ import annotations

from pulse_hwm.auth.validators import clean_email, validate_email, validate_password


class TestEmail:
    def test_accepts_normal_addresses(self):
        assert validate_email("a@b.co") == ""
        assert validate_email("first.last@sub.example.com") == ""

    def test_rejects_garbage(self):
        assert validate_email("") != ""
        assert validate_email("nope") == "that does not look like a valid email"
        assert validate_email("a@b") == "that does not look like a valid email"
        assert validate_email("a b@c.d") == "that does not look like a valid email"


class TestCleanEmail:
    def test_trims_and_lowers(self):
        assert clean_email("  FIRST.LAST@EXAMPLE.COM  ") == "first.last@example.com"

    def test_nfkc_normalizes_lookalike_characters(self):
        # zero-width joiner/carrier chars that phishers use get removed
        assert "\u200c" not in clean_email("sneaky\u200cb@x.y")


class TestPassword:
    def test_minimum_length(self):
        assert validate_password("Sh0rt!") != ""
        assert len(validate_password("Sh0rt!")) > 0

    def test_accepts_reasonable(self):
        assert validate_password("Correct-Horse-42") == ""

    def test_rejects_whitespace_only(self):
        assert validate_password("        ") != ""

    def test_hint_mentions_minimum(self):
        assert "8" in validate_password("a")
