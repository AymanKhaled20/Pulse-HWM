from __future__ import annotations

from pulse_hwm.single_instance import auth_urls_from_args


def test_extracts_pulsehwm_urls_from_argv():
    args = [
        "C:\\Program Files\\PulseHWM\\PulseHWM.exe",
        "pulsehwm://auth-callback?code=abc123&state=x",
        "--some-flag",
    ]
    urls = auth_urls_from_args(args)
    assert urls == ["pulsehwm://auth-callback?code=abc123&state=x"]


def test_multiple_urls_all_kept_in_order():
    args = ["pulsehwm://auth-callback?code=1", "x", "pulsehwm://auth-callback?code=2"]
    assert auth_urls_from_args(args) == [
        "pulsehwm://auth-callback?code=1",
        "pulsehwm://auth-callback?code=2",
    ]


def test_no_urls_means_empty():
    assert auth_urls_from_args(["PulseHWM.exe", "--selftest"]) == []
    assert auth_urls_from_args([]) == []


def test_surrounding_whitespace_is_trimmed():
    # Windows sometimes pads %1 shell arguments
    assert auth_urls_from_args(["  pulsehwm://auth-callback?code=z "]) == [
        "pulsehwm://auth-callback?code=z"
    ]


def test_case_insensitive_scheme_match():
    assert auth_urls_from_args(["PULSEHWM://auth-callback?code=q"]) == [
        "PULSEHWM://auth-callback?code=q"
    ]
