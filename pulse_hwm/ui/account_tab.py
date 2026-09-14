from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.auth.oauth import OauthCoordinator
from pulse_hwm.auth.rest import AuthResult
from pulse_hwm.auth.session import SessionManager
from pulse_hwm.auth.validators import clean_email, validate_email, validate_password
from pulse_hwm.ui import theme as T

# The ACCOUNT tab: sign-in / sign-up / sign-out + SYNC NOW.
#
# DELEGATION contract:
#   * no QThread parking of its own — network work goes to the global
#     QThreadPool (TerminateTask pattern) so the UI never blocks;
#   * the tab NEVER talks to SessionManager across threads: it schedules
#     tasks, and only the UI thread touches session state afterward;
#   * sync (phase 7) hangs off sync_requested — this tab neither owns nor
#     knows about the sync engine.


class _AuthSignals(QObject):
    """Signal carrier so a QRunnable can talk back to the UI thread."""

    done = Signal(str, object)  # tag, AuthResult (tags: "sign-in", "sign-up", …)
    open_url = Signal(str)  # browser hand-off must happen on the UI thread


class _AuthTask(QRunnable):
    """Runs ONE blocking auth call on the pool, reports via signals."""

    def __init__(self, tag: str, fn, signals: _AuthSignals):
        super().__init__()
        self._tag = tag
        self._fn = fn
        self._signals = signals

    def run(self) -> None:  # worker thread
        try:
            result = self._fn()
        except Exception as exc:  # never let a worker die silently
            result = AuthResult(error=f"unexpected: {type(exc).__name__}")
        self._signals.done.emit(self._tag, result)


def _apply_theme(obj: QWidget) -> None:
    # every widget picks up the global QSS by objectName; no manual colors
    obj.setStyleSheet("")  # explicit no-op: inherit theme styles


class AccountTab(QWidget):
    """Sign in / create account / provider buttons / SYNC NOW / status."""

    signed_in_changed = Signal(bool)
    signed_out = Signal()
    sync_requested = Signal()

    def __init__(self, session: SessionManager, oauth: OauthCoordinator, db=None):
        super().__init__()
        self.setObjectName("root")
        self._session = session
        self._oauth = oauth
        self._db = db  # unused now; kept so callers can pass it harmlessly
        self._pool = QThreadPool.globalInstance()
        self._signals = _AuthSignals()
        self._signals.done.connect(self._on_auth_done)
        self._signals.open_url.connect(self._open_browser)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.s(10), T.s(10), T.s(10), T.s(10))
        outer.setSpacing(T.s(8))

        hero = QHBoxLayout()
        self.title = QLabel("ACCOUNT")
        self.title.setObjectName("title")
        hero.addWidget(self.title)
        hero.addStretch(1)
        self.status = QLabel("SIGNED OUT")
        self.status.setObjectName("muted")
        hero.addWidget(self.status)
        outer.addLayout(hero)

        # ── forms stack: two QWidgets, visibility swaps ────────────────
        self._signed_out_form = QWidget()
        form = QGridLayout(self._signed_out_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        form.addWidget(self._mk_label("EMAIL"), 0, 0)
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        form.addWidget(self.email, 0, 1, 1, 3)

        form.addWidget(self._mk_label("PASSWORD"), 1, 0)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("8+ characters")
        form.addWidget(self.password, 1, 1, 1, 3)

        self.btn_sign_in = QPushButton("SIGN IN")
        self.btn_sign_up = QPushButton("CREATE ACCOUNT")
        form.addWidget(self.btn_sign_in, 2, 1)
        form.addWidget(self.btn_sign_up, 2, 2)
        self.btn_forgot = QPushButton("FORGOT PASSWORD?")
        self.btn_forgot.setObjectName("muted")
        form.addWidget(self.btn_forgot, 2, 3)

        divider = QLabel("───  OR CONTINUE WITH  ───")
        divider.setObjectName("muted")
        divider.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addWidget(divider, 3, 0, 1, 4)

        self.btn_google = QPushButton("GOOGLE")
        self.btn_github = QPushButton("GITHUB")
        form.addWidget(self.btn_google, 4, 1)
        form.addWidget(self.btn_github, 4, 2)
        outer.addWidget(self._signed_out_form)

        # ── signed-in panel ───────────────────────────────────────────
        self._signed_in_panel = QWidget()
        grid = QGridLayout(self._signed_in_panel)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.addWidget(self._mk_label("SIGNED IN AS"), 0, 0)
        self.lbl_email = QLabel("")
        self.lbl_email.setObjectName("title")
        grid.addWidget(self.lbl_email, 0, 1)
        grid.addWidget(self._mk_label("VIA"), 1, 0)
        self.lbl_provider = QLabel("")
        self.lbl_provider.setObjectName("muted")
        grid.addWidget(self.lbl_provider, 1, 1)
        self.btn_sync_now = QPushButton("SYNC NOW")
        self.btn_sign_out = QPushButton("SIGN OUT")
        grid.addWidget(self.btn_sync_now, 2, 1)
        grid.addWidget(self.btn_sign_out, 2, 2)
        outer.addWidget(self._signed_in_panel)

        self.feedback = QLabel("")
        self.feedback.setObjectName("muted")
        self.feedback.setWordWrap(True)
        outer.addWidget(self.feedback)
        outer.addStretch(1)

        # ── wire buttons (fire → schedule → feedback) ─────────────────
        self.btn_sign_in.clicked.connect(self._on_sign_in)
        self.btn_sign_up.clicked.connect(self._on_sign_up)
        self.btn_forgot.clicked.connect(self._on_forgot)
        self.btn_google.clicked.connect(lambda: self._on_provider("google"))
        self.btn_github.clicked.connect(lambda: self._on_provider("github"))
        self.btn_sign_out.clicked.connect(self._on_sign_out)
        self.btn_sync_now.clicked.connect(self.sync_requested)

        self._auth_in_flight = False  # re-entry guard, see _auth_busy()
        self.refresh_from_session()

    # ─── helpers ───────────────────────────────────────────────────────
    def _mk_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("muted")
        return lbl

    def _set_feedback(self, text: str) -> None:
        self.feedback.setText(text)

    def _open_browser(self, url: str) -> None:
        QDesktopServices.openUrl(url)  # UI thread only — emitted via signal

    # ─── action slots ─────────────────────────────────────────────────
    def _auth_busy(self) -> bool:
        """One auth exchange at a time: two concurrent ones would race
        the rotating refresh token (the second response parks a token
        the server has already superseded). Also disables re-entry."""
        if self._auth_in_flight:
            return True
        self._auth_in_flight = True
        for w in (
            self.btn_sign_in,
            self.btn_sign_up,
            self.btn_forgot,
            self.btn_google,
            self.btn_github,
        ):
            w.setEnabled(False)
        return False

    def _auth_idle(self) -> None:
        self._auth_in_flight = False
        self.set_configured(True)

    def _on_sign_in(self) -> None:
        if self._auth_busy():
            return
        email = clean_email(self.email.text())
        err = validate_email(email) or validate_password(self.password.text())
        if err:
            self._auth_idle()
            self._set_feedback(err)
            return
        self._set_feedback("signing in …")
        password = self.password.text()
        self._pool.start(
            _AuthTask(
                "sign-in", lambda: self._session.sign_in(email, password), self._signals
            )
        )

    def _on_sign_up(self) -> None:
        if self._auth_busy():
            return
        email = clean_email(self.email.text())
        err = validate_email(email) or validate_password(self.password.text())
        if err:
            self._auth_idle()
            self._set_feedback(err)
            return
        try:
            flow = self._oauth.start_email_flow("signup")
        except RuntimeError as exc:
            self._auth_idle()
            self._set_feedback(str(exc))
            return
        self._set_feedback("creating account …")
        password = self.password.text()
        # PKCE signup: the challenge rides the POST body; the verification
        # email's code later matches our parked verifier
        self._pool.start(
            _AuthTask(
                "sign-up",
                lambda: self._session.sign_up(
                    email,
                    password,
                    redirect_to=flow.redirect_uri,
                    challenge=flow.challenge,
                ),
                self._signals,
            )
        )

    def _on_forgot(self) -> None:
        if self._auth_busy():
            return
        email = clean_email(self.email.text())
        err = validate_email(email)
        if err:
            self._auth_idle()
            self._set_feedback(err)
            return
        try:
            flow = self._oauth.start_email_flow("recover")
        except RuntimeError as exc:
            self._auth_idle()
            self._set_feedback(str(exc))
            return
        self._set_feedback("sending reset link …")
        self._pool.start(
            _AuthTask(
                "recover",
                lambda: self._session.recover(email, challenge=flow.challenge),
                self._signals,
            )
        )

    def _on_provider(self, provider: str) -> None:
        if self._auth_busy():
            return
        try:
            url = self._oauth.start(provider)
        except RuntimeError as exc:
            self._auth_idle()
            self._set_feedback(str(exc))
            return
        self._set_feedback(f"finish signing in with {provider.upper()} in your browser")
        self._signals.open_url.emit(url)

    def _on_sign_out(self) -> None:
        self._session.sign_out()
        self._set_feedback("signed out")
        self.refresh_from_session()
        self.signed_out.emit()

    # ─── network results (UI thread, via signals) ─────────────────────
    def _on_auth_done(self, tag: str, result: object) -> None:
        assert isinstance(result, AuthResult)
        self._auth_idle()  # re-enable buttons whoever it was
        if tag == "sign-in":
            if result.ok:
                self._set_feedback("")
                self.refresh_from_session()
                self.signed_in_changed.emit(True)
            else:
                self._set_feedback(result.error)
        elif tag == "sign-up":
            if result.needs_email_confirmation:
                self._set_feedback(
                    "account created — CHECK YOUR INBOX to confirm, then sign in"
                )
            elif result.ok:
                self.refresh_from_session()
                self.signed_in_changed.emit(True)
            else:
                self._set_feedback(result.error)
        elif tag == "recover":
            if result.ok:
                self._set_feedback(
                    "reset link sent — CHECK YOUR INBOX (valid a short while)"
                )
            else:
                self._set_feedback(result.error)

    def refresh_from_session(self) -> None:
        """Repaint to match SessionManager state (call after any external
        adoption, e.g. an OAuth callback lands via the running instance)."""
        info = self._session.session_info
        active = self._session.is_signed_in()
        # the OAuth callback path repaints through here too — release the
        # in-flight lock AND re-enable the buttons (the OAuth flow never
        # schedules a task, so _on_auth_done/_auth_idle never fire for it;
        # without this the provider buttons stayed greyed out forever).
        # persist_enabled() keeps the unconfigured-cloud state respected.
        self._auth_in_flight = False
        self.set_configured(self.persist_enabled())
        self._signed_out_form.setVisible(not active)
        self._signed_in_panel.setVisible(active)
        if active:
            self.lbl_email.setText(info.email)
            self.lbl_provider.setText((info.provider or "password").upper())
            self.status.setText("SIGNED IN")
            self.btn_sync_now.setEnabled(True)
        else:
            self.status.setText("SIGNED OUT")
            self.btn_sync_now.setEnabled(False)
            self.password.clear()

    def release_auth_lock(self) -> None:
        """Auth flow ended without a network result (bad/expired callback
        link, provider error): clear the in-flight latch and re-enable
        the buttons so the user can simply try again."""
        self._auth_in_flight = False
        self.set_configured(self.persist_enabled())

    def apply_cloud_offline(self, message: str) -> None:
        """Sync engine / config reports problems here."""
        self._set_feedback(message)

    def set_configured(self, on: bool) -> None:
        """No Supabase keys configured → auth is inert, clearly."""
        for w in (
            self.email,
            self.password,
            self.btn_sign_in,
            self.btn_sign_up,
            self.btn_forgot,
            self.btn_google,
            self.btn_github,
        ):
            w.setEnabled(on)

    def persist_enabled(self) -> bool:
        """Whether accounts are usable at all (public keys configured)."""
        from pulse_hwm.auth.config import auth_config

        return auth_config().is_configured()
