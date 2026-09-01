from unittest.mock import MagicMock, patch

import pytest

from tools.sensei_x_trigger import (
    DEFAULT_IAC_PORT,
    MIDI_CHANNEL,
    MIDI_NOTE,
    XKeyGate,
    find_iac_output,
    send_trigger,
)


def test_manual_midi_contract_constants_are_fixed():
    assert MIDI_CHANNEL == 15
    assert MIDI_NOTE == 119
    assert DEFAULT_IAC_PORT == "Sensei X Trigger Bus 1"


def test_x_key_gate_emits_once_until_key_up():
    gate = XKeyGate()
    assert gate.key_down() is True
    assert gate.key_down() is False
    assert gate.key_down(is_repeat=True) is False
    gate.key_up()
    assert gate.key_down() is True


def test_trigger_sends_exactly_one_note_on_and_note_off():
    mido = MagicMock()
    port = MagicMock()
    with patch("tools.sensei_x_trigger.time.sleep"):
        send_trigger(mido, port)
    assert mido.Message.call_count == 2
    assert mido.Message.call_args_list[0].args == ("note_on",)
    assert mido.Message.call_args_list[0].kwargs == {
        "channel": 15, "note": 119, "velocity": 127,
    }
    assert mido.Message.call_args_list[1].args == ("note_off",)
    assert mido.Message.call_args_list[1].kwargs == {
        "channel": 15, "note": 119, "velocity": 0,
    }
    assert port.send.call_count == 2


def test_iac_port_resolution_and_clear_missing_port_error():
    mido = MagicMock()
    mido.get_output_names.return_value = ["Sensei X Trigger Bus 1"]
    assert find_iac_output(mido, DEFAULT_IAC_PORT) == DEFAULT_IAC_PORT
    mido.get_output_names.return_value = ["Other MIDI Port"]
    with pytest.raises(RuntimeError, match="IAC MIDI port.*not found"):
        find_iac_output(mido, DEFAULT_IAC_PORT)
