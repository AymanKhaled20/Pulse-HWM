from __future__ import annotations

import io
import wave

from pulse_hwm.alerts.notifier import ALERT_TONES, synth_beep


def test_alert_tones_descend_like_an_error():
    """An error cue must fall (first tone well above the last one).

    A rising arc reads as happy/successful- this keeps the descending shape
    somewhere in tests so nobody "fixes" it back into a fanfare by accident.
    """
    assert ALERT_TONES[0][0] > ALERT_TONES[-1][0] * 1.5


def test_synth_beep_is_a_valid_mono_16bit_8khz_wav():
    data = synth_beep()
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 8000
        assert wav.getnframes() > 0


def test_synth_beep_length_matches_tone_script():
    rate = 8000
    expected = sum(int(rate * dur) for _freq, dur in ALERT_TONES)
    with wave.open(io.BytesIO(synth_beep())) as wav:
        assert wav.getnframes() == expected


def test_synth_beep_is_short_and_loud():
    with wave.open(io.BytesIO(synth_beep())) as wav:
        raw = wav.readframes(wav.getnframes())
    assert 0.3 < len(raw) / (8000 * 2) < 0.8  # seconds, 16-bit mono
    peak = max(
        abs(int.from_bytes(raw[i : i + 2], "little")) for i in range(0, len(raw), 2)
    )
    assert peak > 8000  # clearly audible, not a murmur
