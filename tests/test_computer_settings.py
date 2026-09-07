"""Volume + settings dispatch — uses a fake Windows endpoint, no real speakers."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actions.computer_settings as cs


class FakeEndpoint:
    def __init__(self, scalar=0.4, muted=False):
        self.scalar = scalar
        self.muted = muted

    def get_scalar(self):
        return self.scalar

    def set_scalar(self, value):
        self.scalar = max(0.0, min(1.0, float(value)))

    def get_mute(self):
        return self.muted

    def set_mute(self, muted):
        self.muted = bool(muted)


def setup():
    cs._OS = "Windows"
    cs._volume_backend = FakeEndpoint()


def teardown():
    cs._volume_backend = None


def test_parse_volume_value():
    assert cs._parse_volume_value("50") == 50
    assert cs._parse_volume_value("50%") == 50
    assert cs._parse_volume_value("set to 7") == 7
    assert cs._parse_volume_value(80) == 80
    assert cs._parse_volume_value(None, default=40) == 40
    assert cs._parse_volume_value("loud") is None


def test_volume_set_uses_slider_scalar():
    setup()
    try:
        cs.volume_set("35%")
        assert round(cs._volume_backend.scalar * 100) == 35
        assert cs._volume_backend.muted is False
        assert cs.volume_get() == 35
    finally:
        teardown()


def test_volume_up_down_and_mute():
    setup()
    try:
        cs.volume_set(40)
        cs.volume_up()
        assert cs.volume_get() == 50
        cs.volume_down()
        assert cs.volume_get() == 40
        cs.volume_mute()
        assert cs._volume_backend.muted is True
        cs.volume_unmute()
        assert cs._volume_backend.muted is False
        cs.volume_toggle_mute()
        assert cs._volume_backend.muted is True
    finally:
        teardown()


def test_computer_settings_dispatch():
    setup()
    try:
        msg = cs.computer_settings({"action": "volume_set", "value": "22%"})
        assert "22%" in msg
        msg = cs.computer_settings({"action": "volume_up"})
        assert "32%" in msg or "volume" in msg.lower()
        assert cs.volume_get() == 32
        msg = cs.computer_settings({"description": "turn the volume down"})
        assert "22%" in msg or "volume" in msg.lower()
        assert cs.volume_get() == 22
    finally:
        teardown()


def test_decrease_overrides_volume_up_action():
    """Gemini often sends volume_up even when the user said decrease."""
    setup()
    try:
        cs.volume_set(40)
        msg = cs.computer_settings({
            "action": "volume_up",
            "description": "decrease the volume",
        })
        assert cs.volume_get() == 30
        assert "30%" in msg
    finally:
        teardown()


def test_description_wins_over_wrong_value():
    setup()
    try:
        cs.volume_set(80)
        msg = cs.computer_settings({
            "action": "volume_set",
            "value": "50",
            "description": "set volume to 20",
        })
        assert cs.volume_get() == 20
        assert "20%" in msg
    finally:
        teardown()


def test_volume_set_reads_number_from_description():
    setup()
    try:
        cs.volume_set(80)
        msg = cs.computer_settings({
            "action": "volume_set",
            "description": "set volume to 20",
        })
        assert cs.volume_get() == 20
        assert "20%" in msg
    finally:
        teardown()


def test_volume_set_does_not_default_to_50():
    setup()
    try:
        cs.volume_set(15)
        msg = cs.computer_settings({"action": "volume_set"})
        assert cs.volume_get() == 15
        assert "0 to 100" in msg or "30" in msg
    finally:
        teardown()


def test_decrease_by_amount():
    setup()
    try:
        cs.volume_set(50)
        cs.computer_settings({
            "action": "volume_down",
            "description": "decrease by 20",
        })
        assert cs.volume_get() == 30
    finally:
        teardown()


def test_decrease_to_exact_level():
    setup()
    try:
        cs.volume_set(70)
        cs.computer_settings({
            "action": "volume_up",
            "description": "decrease the volume to 25",
        })
        assert cs.volume_get() == 25
    finally:
        teardown()


def test_resolve_volume_intent():
    assert cs._resolve_volume_intent("volume_up", "decrease the volume", None) == (
        "volume_down", None,
    )
    assert cs._resolve_volume_intent("volume_set", "set volume to 20", None) == (
        "volume_set", 20,
    )
    assert cs._resolve_volume_intent("volume_up", "set volume to 40", None) == (
        "volume_set", 40,
    )
    assert cs._resolve_volume_intent("volume_down", "decrease by 15", None) == (
        "volume_down", 15,
    )
    assert cs._resolve_volume_intent("volume_set", "set volume to 20", "50") == (
        "volume_set", 20,
    )
    assert cs._explicit_volume_target("set the volume limit to 30") == 30
    assert cs._explicit_volume_target("decrease from 80 to 20") == 20


if __name__ == "__main__":
    test_parse_volume_value()
    test_volume_set_uses_slider_scalar()
    test_volume_up_down_and_mute()
    test_computer_settings_dispatch()
    test_decrease_overrides_volume_up_action()
    test_volume_set_reads_number_from_description()
    test_description_wins_over_wrong_value()
    test_volume_set_does_not_default_to_50()
    test_decrease_by_amount()
    test_decrease_to_exact_level()
    test_resolve_volume_intent()
    print("ok")
