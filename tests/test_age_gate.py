"""Age gate: 16+ to create an account (GDPR Art. 8 / COPPA)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from pulse_hwm.cloud.age import AGE_BLOCK_SETTING, age_gate_error, age_on
from pulse_hwm.cloud.rest import CloudClient
from pulse_hwm.db import Database

TODAY = date(2026, 10, 2)


# —— pure rule ————————————————————————————————————————————————————————
class TestAgeRule:
    def test_age_counts_whole_years_around_the_birthday(self):
        assert age_on(date(2010, 10, 2), TODAY) == 16  # birthday today
        assert age_on(date(2010, 10, 3), TODAY) == 15  # birthday tomorrow

    def test_leap_day_birthday(self):
        assert age_on(date(2008, 2, 29), date(2024, 2, 28)) == 15
        assert age_on(date(2008, 2, 29), date(2024, 2, 29)) == 16

    def test_sixteen_today_may_create_an_account(self):
        assert age_gate_error(date(2010, 10, 2), TODAY) == ""

    def test_fifteen_is_refused(self):
        assert "16 or older" in age_gate_error(date(2010, 10, 3), TODAY)

    def test_blank_and_future_dates_are_refused(self):
        assert "date of birth" in age_gate_error(None, TODAY)
        assert "future" in age_gate_error(date(2030, 1, 1), TODAY)


# —— client sends only the confirmation, never the date ————————————————
def test_signup_and_oauth_send_the_age_confirmation_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"user": {"id": "u", "email": "e"}})

    client = CloudClient(
        "https://w.example", "key", transport=httpx.MockTransport(handler)
    )
    client.sign_up("e@x.y", "password1", "pulsehwm://auth-callback")
    assert seen["body"]["age_confirmed"] is True
    assert not any("birth" in key for key in seen["body"])

    url = client.oauth_authorize_url("google", "pulsehwm://auth-callback", "chal")
    assert "age_confirmed=1" in url


# —— account tab gate ————————————————————————————————————————————————
@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _SignedOut:
    session_info = None

    def is_signed_in(self) -> bool:
        return False


class _RecordingOauth:
    """Fails the test if any sign-up flow starts past the gate."""

    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, provider: str) -> str:
        self.started.append(provider)
        raise RuntimeError("stop here")  # keeps the test offline

    def start_email_flow(self, kind: str):
        self.started.append(kind)
        raise RuntimeError("stop here")


@pytest.fixture()
def tab(app, tmp_path: Path):
    from pulse_hwm.ui.account_tab import AccountTab

    db = Database(tmp_path / "age.db")
    oauth = _RecordingOauth()
    widget = AccountTab(_SignedOut(), oauth, db=db)
    widget.email.setText("kid@example.com")
    widget.password.setText("password123")
    yield widget, oauth, db
    db.close()


def _set_birth(widget, born: date) -> None:
    from PySide6.QtCore import QDate

    widget.birth_date.setDate(QDate(born.year, born.month, born.day))


def _years_ago(years: int) -> date:
    today = date.today()
    return today.replace(year=today.year - years, day=min(today.day, 28))


class TestAccountTabGate:
    def test_blank_birth_date_blocks_every_way_to_create_an_account(self, tab):
        widget, oauth, _db = tab
        widget._on_sign_up()
        widget._on_provider("google")
        widget._on_provider("github")
        assert oauth.started == []
        assert "date of birth" in widget.feedback.text()

    def test_under_sixteen_is_blocked_and_remembered(self, tab):
        widget, oauth, db = tab
        _set_birth(widget, _years_ago(12))
        widget._on_sign_up()
        assert oauth.started == []
        assert db.get_setting(AGE_BLOCK_SETTING) == "1"

        # changing the answer afterwards doesn't get round it
        _set_birth(widget, _years_ago(30))
        widget._on_provider("google")
        assert oauth.started == []
        assert "16 or older" in widget.feedback.text()

    def test_sixteen_plus_reaches_the_sign_up_flow(self, tab):
        widget, oauth, _db = tab
        _set_birth(widget, _years_ago(30))
        widget._on_sign_up()
        widget._auth_idle()
        widget._on_provider("github")
        assert oauth.started == ["signup", "github"]

    def test_password_sign_in_is_not_gated(self, tab, monkeypatch):
        # existing accounts sign in without a birth date
        widget, _oauth, _db = tab
        scheduled = []

        def gate_must_not_run():
            raise AssertionError("sign-in should not hit the age gate")

        monkeypatch.setattr(widget, "_age_gate_ok", gate_must_not_run)
        monkeypatch.setattr(widget._pool, "start", scheduled.append)
        widget._on_sign_in()
        assert len(scheduled) == 1  # the sign-in request was queued
