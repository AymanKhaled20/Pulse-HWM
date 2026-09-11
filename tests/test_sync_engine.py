from __future__ import annotations

from pulse_hwm.auth.rest import AuthResult, Tokens
from pulse_hwm.auth.session import SessionManager
from pulse_hwm.auth.sync_engine import SyncEngine
from pulse_hwm.db import Database

# One real cycle against a FAKE cloud client + a REAL (tmp) sqlite db:
# local sites push; remote settings/site pulls adopt; tombstones land.


class FakeStore:
    def __init__(self):
        self.refresh = ""

    def save_refresh_token(self, token: str) -> bool:
        self.refresh = token
        return True

    def load_refresh_token(self) -> str:
        return self.refresh

    def clear_refresh_token(self) -> None:
        self.refresh = ""


class FakeClient:
    """Mimics enough of SupabaseClient for one SyncWorker cycle."""

    def __init__(self, settings_rows, site_rows):
        self.settings_rows = settings_rows
        self.site_rows = site_rows
        self.upserts: dict[str, list[dict]] = {}

    def rest_select(self, table, query, token):
        rows = self.settings_rows if table == "user_settings" else self.site_rows
        return 200, rows

    def rest_upsert(self, table, rows, token):
        self.upserts.setdefault(table, []).extend(rows)
        return 200, rows


def _session():
    client = type("C", (), {})()  # SessionManager only needs `.refresh`
    client.refresh = lambda token: AuthResult()  # never used in-cycle
    store = FakeStore()
    session = SessionManager(client, store)
    # plant a signed-in state by hand (session internals, like the app does)
    session.tokens = Tokens(access_token="t", refresh_token="r")
    session._adopt(
        AuthResult(
            ok=True, tokens=session.tokens, user={"id": "u-1", "email": "e@x.y"}
        ),
        provider="password",
    )
    return session, store


def test_full_cycle_push_pull(tmp_path):
    import time as _time

    now_iso = (
        _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(_time.time() + 5)) + "+00:00"
    )

    db = Database(tmp_path / "pulse.db")
    db.add_site(name="Pulse", url="https://pulse.dev")
    cloud_only = [
        {
            "user_id": "u-1",
            "site_uuid": "b" * 32,
            "name": "Cloud site",
            "url": "https://cloud.source",
            "method": "GET",
            "timeout_s": 10.0,
            "expected_status": 200,
            "keyword": "up",
            "enabled": True,
            "deleted": False,
            "updated_at": now_iso,
        }
    ]
    client = FakeClient(
        settings_rows=[
            {
                "user_id": "u-1",
                "key": "theme_color",
                "value": "paper",
                "updated_at": now_iso,
            },
        ],
        site_rows=cloud_only,
    )
    session, _ = _session()
    engine = SyncEngine(session, client, db)
    engine._inflight = False
    worker = _sync_worker(session, client, db)
    summary, ok, error = worker._cycle()
    assert ok is True, error

    # remote site adopted locally (by uuid), local site pushed
    adopted = [r for r in db.get_sites() if r["url"] == "https://cloud.source"]
    assert len(adopted) == 1 and adopted[0]["uuid"] == "b" * 32
    pushed = client.upserts["user_sites"]
    assert any(row["site_uuid"] for row in pushed if row["url"] == "https://pulse.dev")
    # remote setting pulled (cloud newer)
    assert db.get_setting("theme_color") == "paper"
    assert "pulled 1 sites/1 settings" in summary


def _sync_worker(session, client, db):
    from pulse_hwm.auth.sync_engine import SyncWorker

    return SyncWorker(session, client, db, SyncWorker._Signals())


def test_device_keys_not_pushed(tmp_path):
    db = Database(tmp_path / "x.db")
    db.set_setting("hardware_interval_ms", "987")
    client = FakeClient([], [])
    session, _ = _session()
    worker = _sync_worker(session, client, db)
    summary, ok, error = worker._cycle()
    assert ok is True
    pushed_keys = {row["key"] for row in client.upserts.get("user_settings", [])}
    assert "hardware_interval_ms" not in pushed_keys
    assert "limit_resources" not in pushed_keys
    assert db.get_setting("hardware_interval_ms") == "987"


def test_not_signed_in_cycle_skipped(tmp_path):
    db = Database(tmp_path / "x.db")
    client = FakeClient([], [])
    session, _ = _session()
    session.tokens = None
    worker = _sync_worker(session, client, db)
    summary, ok, error = worker._cycle()
    assert ok is False and error == ""  # silent skip, not an error
    assert client.upserts == {}
