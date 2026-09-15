from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm.cloud.oauth import OauthCoordinator
from pulse_hwm.cloud.rest import AuthResult
from pulse_hwm.cloud.session import SessionManager
from pulse_hwm.cloud.validators import clean_email, validate_email, validate_password
from pulse_hwm.ui.widgets.pixel_panel import PixelPanel

# The ACCOUNT tab: sign-in / sign-up / sign-out + SYNC NOW.
#
# DELEGATION contract:
#   * no QThread parking of its own Ã¢â‚¬â€— network work goes to the global
#     QThreadPool (TerminateTask pattern) so the UI never blocks;
#   * the tab NEVER talks to SessionManager across threads: it schedules
#     tasks, and only the UI thread touches session state afterward;
#   * sync (phase 7) hangs off sync_requested Ã¢â‚¬â€— this tab neither owns nor
#     knows about the sync engine.


class _AuthSignals(QObject):
    """Signal carrier so a QRunnable can talk back to the UI thread."""

    done = Signal(str, object)  # tag, AuthResult (tags: "sign-in", "sign-up", Ã¢â‚¬¦)
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
    # updates (v1.2.0): the tab stays engine-free — it only ASKS; app.py
    # wires install/dismiss to the real installer + persistence
    install_update_requested = Signal()
    update_dismissed = Signal(str)  # version the user clicked LATER on

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
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        hero = QHBoxLayout()
        self.title = QLabel("ACCOUNT")
        self.title.setObjectName("title")
        hero.addWidget(self.title)
        hero.addStretch(1)
        self.status = QLabel("SIGNED OUT")
        self.status.setObjectName("muted")
        hero.addWidget(self.status)
        outer.addLayout(hero)

        # Ã¢â€—â‚¬Ã¢â€—â‚¬ forms stack: two QWidgets, visibility swaps Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬
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

        divider = QLabel(
            "Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬  OR CONTINUE WITH  Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬"
        )
        divider.setObjectName("muted")
        divider.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addWidget(divider, 3, 0, 1, 4)

        self.btn_google = QPushButton("GOOGLE")
        self.btn_github = QPushButton("GITHUB")
        form.addWidget(self.btn_google, 4, 1)
        form.addWidget(self.btn_github, 4, 2)
        outer.addWidget(self._signed_out_form)

        # Ã¢â€—â‚¬Ã¢â€—â‚¬ signed-in panel Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬
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

        # —— updates: nudge panel (signed-out) + banner (signed-in) ——————
        # One panel ships both faces — refresh_from_session() swaps what's
        # visible; app.py connects the button signals to the real installer
        self._updates_panel = PixelPanel("UPDATES")
        updates_body = self._updates_panel.body()

        self.cta_updates = QLabel(
            "LOG IN TO GET THE LATEST OF PULSE — INCLUDING SECURITY FIXES.\n"
            "Updates, settings sync and site bookmarks are member benefits."
        )
        self.cta_updates.setObjectName("muted")
        self.cta_updates.setWordWrap(True)
        updates_body.addWidget(self.cta_updates)
        self.btn_cta_log_in = QPushButton("LOG IN TO GET THE LATEST")
        updates_body.addWidget(self.btn_cta_log_in)

        self.lbl_update = QLabel("")
        self.lbl_update.setObjectName("title")
        self.lbl_update.setWordWrap(True)
        self.lbl_update.setVisible(False)
        updates_body.addWidget(self.lbl_update)

        self.lbl_update_notes = QLabel("")
        self.lbl_update_notes.setObjectName("muted")
        self.lbl_update_notes.setWordWrap(True)
        self.lbl_update_notes.setVisible(False)
        updates_body.addWidget(self.lbl_update_notes)

        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setVisible(False)
        updates_body.addWidget(self.update_progress)

        update_row = QHBoxLayout()
        self.btn_install_now = QPushButton("INSTALL NOW")
        self.btn_install_now.setObjectName("success")
        self.btn_install_now.setVisible(False)
        self.btn_update_later = QPushButton("LATER")
        self.btn_update_later.setObjectName("muted")
        self.btn_update_later.setVisible(False)
        update_row.addWidget(self.btn_install_now)
        update_row.addWidget(self.btn_update_later)
        update_row.addStretch(1)
        updates_body.addLayout(update_row)

        self.lbl_update_status = QLabel("")
        self.lbl_update_status.setObjectName("muted")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setVisible(False)
        updates_body.addWidget(self.lbl_update_status)

        self._updates_panel.setVisible(False)
        outer.addWidget(self._updates_panel)

        # Ã¢â€—â‚¬Ã¢â€—â‚¬ wire buttons (fire Ã¢â€ â€™ schedule Ã¢â€ â€™ feedback) Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬
        self.btn_sign_in.clicked.connect(self._on_sign_in)
        self.btn_sign_up.clicked.connect(self._on_sign_up)
        self.btn_forgot.clicked.connect(self._on_forgot)
        self.btn_google.clicked.connect(lambda: self._on_provider("google"))
        self.btn_github.clicked.connect(lambda: self._on_provider("github"))
        self.btn_sign_out.clicked.connect(self._on_sign_out)
        self.btn_sync_now.clicked.connect(self.sync_requested)
        self.btn_cta_log_in.clicked.connect(self._cta_login)
        self.btn_install_now.clicked.connect(self.install_update_requested)
        self.btn_update_later.clicked.connect(self._on_update_later)

        self._auth_in_flight = False  # re-entry guard, see _auth_busy()
        self.refresh_from_session()

    # Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬ helpers Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬
    def _mk_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("muted")
        return lbl

    def _set_feedback(self, text: str) -> None:
        self.feedback.setText(text)

    def _open_browser(self, url: str) -> None:
        QDesktopServices.openUrl(url)  # UI thread only Ã¢â‚¬â€— emitted via signal

    # Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬ action slots Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬
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
        self._set_feedback("signing in Ã¢â‚¬¦")
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
        self._set_feedback("creating account Ã¢â‚¬¦")
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
        self._set_feedback("sending reset link Ã¢â‚¬¦")
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

    # Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬ network results (UI thread, via signals) Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬Ã¢â€—â‚¬
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
                    "account created Ã¢â‚¬â€— CHECK YOUR INBOX to confirm, then sign in"
                )
            elif result.ok:
                self.refresh_from_session()
                self.signed_in_changed.emit(True)
            else:
                self._set_feedback(result.error)
        elif tag == "recover":
            if result.ok:
                self._set_feedback(
                    "reset link sent Ã¢â‚¬â€— CHECK YOUR INBOX (valid a short while)"
                )
            else:
                self._set_feedback(result.error)

    def refresh_from_session(self) -> None:
        """Repaint to match SessionManager state (call after any external
        adoption, e.g. an OAuth callback lands via the running instance)."""
        info = self._session.session_info
        active = self._session.is_signed_in()
        # the OAuth callback path repaints through here too Ã¢â‚¬â€— release the
        # in-flight lock AND re-enable the buttons (the OAuth flow never
        # schedules a task, so _on_auth_done/_auth_idle never fire for it;
        # without this the provider buttons stayed greyed out forever).
        # persist_enabled() keeps the unconfigured-cloud state respected.
        self._auth_in_flight = False
        self.set_configured(self.persist_enabled())
        self._signed_out_form.setVisible(not active)
        self._signed_in_panel.setVisible(active)
        # updates CTA shows ONLY while signed out; the signed-in banner
        # takes over the panel when an offer arrives (see show_update_*)
        self.cta_updates.setVisible(not active)
        self.btn_cta_log_in.setVisible(not active)
        if not active:
            self._updates_panel.setVisible(True)
            self._reset_update_banner()
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
        """No Supabase keys configured Ã¢â€ â€™ auth is inert, clearly."""
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
        from pulse_hwm.cloud.config import auth_config

        return auth_config().is_configured()

    # —— updates (v1.2.0) ————————————————————————————————————————————————
    def _cta_login(self) -> None:
        """The nudge button: no downloads, no URLs — just land the user in
        the sign-in form with a one-line explanation."""
        self.email.setFocus()
        self._set_feedback(
            "Sign in to receive update notifications — including security "
            "fixes — before they reach the release page."
        )

    def _on_update_later(self) -> None:
        self._reset_update_banner()
        self.update_dismissed.emit(getattr(self, "current_update_version", ""))

    def _reset_update_banner(self) -> None:
        """Hide the signed-in update rows, leave the CTA decision to
        refresh_from_session()'s next call."""
        for w in (
            self.lbl_update,
            self.lbl_update_notes,
            self.update_progress,
            self.btn_install_now,
            self.btn_update_later,
            self.lbl_update_status,
        ):
            w.setVisible(False)
        self._updates_panel.setVisible(not self._session.is_signed_in())

    def show_update_available(
        self, release: dict, current_version: str, forced: bool = False
    ) -> None:
        """Signed-in face: one offer, [INSTALL NOW] [LATER].
        `forced` (security floor / mandatory) removes the LATER escape."""
        self.current_update_release = dict(release)
        self.current_update_version = str(release.get("version", ""))
        self.lbl_update.setText(
            f"PULSE v{self.current_update_version} IS AVAILABLE — YOU ARE ON v{current_version}"
        )
        notes = (str(release.get("notes", "")) or "").strip()
        self.lbl_update_notes.setText(notes[:1500])
        self.lbl_update.setVisible(True)
        self.lbl_update_notes.setVisible(bool(notes))
        self.btn_install_now.setVisible(True)
        self.btn_install_now.setEnabled(True)
        self.btn_update_later.setVisible(not forced)
        self.lbl_update_status.setText(
            "SECURITY UPDATE — RECOMMENDED" if forced else ""
        )
        self.lbl_update_status.setVisible(forced)
        self.update_progress.setVisible(False)
        self._updates_panel.setVisible(True)

    def show_update_progress(self, done: int, total: int) -> None:
        """Chunked download feedback; indeterminate when total is -1."""
        self.btn_install_now.setEnabled(False)
        self.update_progress.setVisible(True)
        if total <= 0:
            self.update_progress.setRange(0, 0)
        else:
            self.update_progress.setRange(0, 100)
            self.update_progress.setValue(int(done * 100 / total))
        self.lbl_update_status.setText(
            f"DOWNLOADING {done / 1_000_000:.1f} MB"
            if total <= 0
            else f"DOWNLOADING {done / 1_000_000:.1f} / {total / 1_000_000:.1f} MB"
        )
        self.lbl_update_status.setVisible(True)

    def show_update_error(self, message: str) -> None:
        self.lbl_update_status.setText(f"UPDATE FAILED — {message}")
        self.lbl_update_status.setVisible(True)
        self.update_progress.setVisible(False)
        self.btn_install_now.setEnabled(True)
        self._updates_panel.setVisible(True)

    def show_update_done(self, version: str) -> None:
        self._reset_update_banner()
        self._set_feedback(f"Update v{version} installing — Pulse will restart.")

    def clear_update_banner(self) -> None:
        self._reset_update_banner()
