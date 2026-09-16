"""URLFetcher tests — offline via httpx.MockTransport; gate + caps checked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pulse_hwm.db import Database
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.effects.loader import (
    URLFetcher,
    UserEffectStore,
    validate_definition,
)

GOOD = {
    "id": "remote-effect",
    "name": "REMOTE",
    "layers": [{"type": "solid", "color": "#222222"}],
}


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "url-test.db")


def make_fetcher(db: Database, handler) -> URLFetcher:
    return URLFetcher(db, transport=httpx.MockTransport(handler))


class TestGates:
    def test_disabled_by_default(self, db):
        fetcher = make_fetcher(db, lambda request: httpx.Response(200, json={}))
        result, errors = fetcher.fetch("https://example.com/effect.json")
        assert result is None
        assert "disabled" in errors[0]

    def test_enabled_via_settings(self, db):
        db.set_setting("rgb_allow_effect_urls", "1")
        assert (
            make_fetcher(db, lambda request: httpx.Response(200, json=GOOD)) is not None
        )

    def test_http_rejected(self, db):
        db.set_setting("rgb_allow_effect_urls", "1")
        fetcher = make_fetcher(db, lambda request: httpx.Response(200, json=GOOD))
        result, errors = fetcher.fetch("http://example.com/effect.json")
        assert result is None
        assert "https" in errors[0]


class TestFetchFlow:
    def _fetcher(self, db: Database, body: bytes, status: int = 200) -> URLFetcher:
        db.set_setting("rgb_allow_effect_urls", "1")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, content=body)

        return URLFetcher(db, transport=httpx.MockTransport(handler))

    def test_remote_json_parses(self, db):
        fetcher = self._fetcher(db, json.dumps(GOOD).encode())
        result, errors = fetcher.fetch("https://example.com/effect.json")
        assert result is not None and errors == []
        definition, _ = validate_definition(result)
        assert definition["id"] == "user_remote-effect"
        store = UserEffectStore(db)
        ok, _ = store.add(result)
        assert ok is True
        catalog = EffectCatalog()
        store.register_with_catalog(catalog)
        assert catalog.get("user_remote-effect") is not None

    def test_non_200(self, db):
        fetcher = self._fetcher(db, b"{}", status=404)
        result, errors = fetcher.fetch("https://example.com/effect.json")
        assert result is None
        assert "404" in errors[0]

    def test_oversized_body_rejected(self, db):
        fetcher = self._fetcher(db, b"x" * 21_000)
        result, errors = fetcher.fetch("https://example.com/effect.json")
        assert result is None
        assert "20 KB" in errors[0]

    def test_bad_definition_rejected(self, db):
        fetcher = self._fetcher(db, b'{"id": "x"}')
        result, errors = fetcher.fetch("https://example.com/effect.json")
        assert result is None
        assert any("name" in error for error in errors)
