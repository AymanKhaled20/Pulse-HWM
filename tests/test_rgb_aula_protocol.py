"""AULA protocol codec tests — synthetic vectors validated against the
community-reverse-engineered spec (checksums, fragment shapes, the byte-14
apply-flag quirk, per-key planar layout). No device, no HID."""

from __future__ import annotations

from pulse_hwm.rgb.drivers import aula_protocol as ap
from pulse_hwm.rgb.model import RgbColor


class TestFragmentBasics:
    def test_fragment_shape_and_checksum(self):
        fragment = ap.build_fragment(ap.CMD_WRITE, ap.SUBCMD_CONFIG, 3, [1, 2, 3])
        assert len(fragment) == 20
        assert fragment[0] == 0x13
        assert fragment[1] == ap.CMD_WRITE
        assert fragment[2] == ap.SUBCMD_CONFIG
        assert fragment[3] == 3
        assert fragment[4:7] == [1, 2, 3]
        assert fragment[19] == sum(fragment[:19]) & 0xFF
        assert ap.is_valid_fragment(fragment)

    def test_invalid_checksum_detected(self):
        fragment = ap.build_fragment(ap.CMD_WRITE, ap.SUBCMD_CONFIG, 0, [])
        fragment[19] = (fragment[19] + 1) & 0xFF
        assert ap.is_valid_fragment(fragment) is False

    def test_oversized_payload_rejected(self):
        try:
            ap.build_fragment(ap.CMD_WRITE, ap.SUBCMD_CONFIG, 0, [1] * 16)
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_save_request_shape(self):
        # spec example payload prefix: 04 07 …
        fragment = ap.save_request()
        assert fragment[1] == ap.CMD_SAVE
        assert fragment[2] == ap.SUBCMD_CONFIRM
        assert fragment[4:6] == [0x04, 0x07]
        assert ap.is_valid_fragment(fragment)

    def test_read_request_shape(self):
        fragment = ap.read_request()
        assert fragment[1] == ap.CMD_READ
        assert fragment[2] == ap.SUBCMD_CONFIRM
        assert ap.is_valid_fragment(fragment)


def synthetic_config() -> list[list[int]]:
    """A plausible 10-fragment READ reply (cmd=0x44/sub=0x0A) with a
    non-zero apply flag and effect bytes — exposes the byte-14 quirk."""
    fragments = []
    for seq in range(ap.CONFIG_FRAGMENTS):
        fragment = ap.build_fragment(ap.CMD_READ, ap.SUBCMD_CONFIG, seq, [])
        fragment[ap.CFG_APPLY_FLAG] = 0x01  # as the keyboard leaves it
        fragment[19] = ap.checksum(fragment)
        fragments.append(fragment)
    fragments[0][ap.CFG_EFFECT] = 3
    fragments[0][ap.CFG_COLOR_MODE] = ap.COLOR_MODE_DEFAULT
    return fragments


class TestConfigMutation:
    def test_read_modify_write_flips_to_write(self):
        out = ap.set_effect_on_config(synthetic_config(), effect=2)
        assert out[0][1] == ap.CMD_WRITE
        assert out[0][2] == ap.SUBCMD_CONFIG
        for fragment in out:
            assert ap.is_valid_fragment(fragment)

    def test_apply_flag_cleared_and_confirm_set(self):
        out = ap.set_effect_on_config(synthetic_config(), effect=2)
        assert out[0][ap.CFG_APPLY_FLAG] == 0x00
        assert out[0][ap.CFG_CONFIRM_FLAG] == 0x01

    def test_effect_and_color_mode_written(self):
        out = ap.set_effect_on_config(
            synthetic_config(), effect=1, color_mode=ap.COLOR_MODE_CUSTOM
        )
        assert out[0][ap.CFG_EFFECT] == 1
        assert out[0][ap.CFG_COLOR_MODE] == ap.COLOR_MODE_CUSTOM

    def test_other_fragments_passthrough(self):
        source = synthetic_config()
        source[7][10] = 0xB7
        source[7][19] = ap.checksum(source[7])
        out = ap.set_effect_on_config(source, effect=2)
        assert out[7][10] == 0xB7  # bytes the app doesn't own survive

    def test_wrong_count_rejected(self):
        try:
            ap.set_effect_on_config(synthetic_config()[:5], effect=1)
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestPerEffectSlots:
    def test_effect1_brightness_lands_in_cfg4_offset7(self):
        out = ap.set_effect_on_config(synthetic_config(), effect=1, effect_brightness=3)
        assert out[4][7] == 3  # shared [B][S] slot per the Per-Effect Table

    def test_effect7_slot_cfg5(self):
        out = ap.set_effect_on_config(synthetic_config(), effect=7, effect_brightness=2)
        assert out[5][5] == 2

    def test_effect14_slot_cfg6(self):
        out = ap.set_effect_on_config(
            synthetic_config(), effect=14, effect_brightness=4
        )
        assert out[6][5] == 4

    def test_speed_nibble_packing(self):
        assert ap.to_speed_nibble(4, colorful=True) == 0x47
        assert ap.to_speed_nibble(0, colorful=False) == 0x00
        assert ap.to_speed_nibble(2, colorful=True) == 0x27

    def test_speed_written_preserving_mode_nibble(self):
        out = ap.set_effect_on_config(synthetic_config(), effect=1, effect_speed=3)
        # original speed byte was 0 → mode nibble 0 preserved → 0x30
        assert out[4][8] == 0x30


class TestBrightnessLevel:
    def test_percent_to_levels(self):
        assert ap.brightness_level(0) == 0
        assert ap.brightness_level(12) == 0
        assert ap.brightness_level(50) == 2
        assert ap.brightness_level(88) == 4
        assert ap.brightness_level(150) == 4


class TestPalette:
    def test_fragment_count_and_command(self):
        fragments = ap.palette_fragments(RgbColor(255, 0, 0))
        assert len(fragments) == ap.PALETTE_FRAGMENTS
        for seq, fragment in enumerate(fragments):
            assert fragment[1] == ap.CMD_COLOR
            assert fragment[2] == ap.SUBCMD_PALETTE
            assert fragment[3] == seq
            assert ap.is_valid_fragment(fragment)

    def test_custom_color_slot(self):
        fragments = ap.palette_fragments(RgbColor(0x11, 0x22, 0x33))
        fragment = fragments[ap.PALETTE_SEQ_CUSTOM]
        assert fragment[8] == 0x11
        assert fragment[9] == 0x22
        assert fragment[10] == 0x33
        assert fragment[12] == 0xFF  # custom-active flag

    def test_end_marker_on_last_fragment(self):
        # marker written at payload pos 0 → fragment bytes 4/5
        fragments = ap.palette_fragments(RgbColor())
        last = fragments[ap.PALETTE_LAST_SEQ]
        assert (last[4], last[5]) == (0x5A, 0xA5)


class TestPerKey:
    def solid(self, color: RgbColor):
        return ap.perkey_fragments([color] * ap.PERKEY_LED_COUNT)

    def test_fragment_count_and_shape(self):
        fragments = self.solid(RgbColor(10, 20, 30))
        assert len(fragments) == ap.PERKEY_FRAGMENTS
        for seq, fragment in enumerate(fragments):
            assert fragment[1] == ap.CMD_PERKEY
            assert fragment[2] == ap.SUBCMD_PERKEY
            assert fragment[3] == seq
            assert ap.is_valid_fragment(fragment)

    def test_red_plane_values(self):
        fragments = self.solid(RgbColor(0x11, 0x22, 0x33))
        # plane R = seqs 0-8; first fragment data marker + 14 values
        fragment = fragments[0]
        assert fragment[4] == ap.DATA_MARKER
        assert fragment[5:19] == [0x11] * 14

    def test_plane_assignment(self):
        fragments = self.solid(RgbColor(0x11, 0x22, 0x33))
        assert fragments[9][5] == 0x22  # plane G starts at seq 9
        assert fragments[18][5] == 0x33  # plane B starts at seq 18

    def test_led_index_mapping(self):
        # LED 20 = plane R fragment 1 (seq 1), byte position 6 → fragment[4+1+6]
        colors = [RgbColor(0, 0, 0)] * ap.PERKEY_LED_COUNT
        colors[20] = RgbColor(200, 0, 0)
        fragments = ap.perkey_fragments(colors)
        assert fragments[1][11] == 200  # payload pos 7 = LED 20 within R

    def test_trailer(self):
        fragments = self.solid(RgbColor())
        trailer = fragments[ap.PERKEY_TRAILER_SEQ]
        # spec payload "06 00 00 5A A5 …" lands at fragment bytes 4,7,8
        assert trailer[4] == 0x06
        assert (trailer[7], trailer[8]) == (0x5A, 0xA5)
