"""Age gate for creating a cloud account — pure logic, no Qt.

Why 16: under GDPR (Article 8) EU countries set the age a child can
consent to an online service between 13 and 16, and the US COPPA line is
13. Requiring 16 everywhere covers every one of those without per-country
rules. The birth date is only used for this check on the user's machine;
it is never stored or sent — the server just receives "age confirmed".
"""

from __future__ import annotations

from datetime import date

MIN_ACCOUNT_AGE = 16

# local-only settings key: once someone enters an under-age birth date,
# account creation stays blocked on this device (so the answer can't just
# be changed and retried). Not in the sync allowlist, so it never leaves.
AGE_BLOCK_SETTING = "age_gate_blocked"


def age_on(birth: date, today: date) -> int:
    """Whole years between birth and today."""
    had_birthday = (today.month, today.day) >= (birth.month, birth.day)
    return today.year - birth.year - (0 if had_birthday else 1)


def age_gate_error(birth: date | None, today: date) -> str:
    """ "" when account creation may go ahead, else the message to show."""
    if birth is None:
        return "enter your date of birth to create an account"
    if birth > today:
        return "date of birth can't be in the future"
    if age_on(birth, today) < MIN_ACCOUNT_AGE:
        return f"you must be {MIN_ACCOUNT_AGE} or older to create an account"
    return ""
