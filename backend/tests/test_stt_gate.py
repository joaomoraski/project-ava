"""Tests for the STT Gate."""
import numpy as np
import pytest

from core.stt.gate import STTGate, GateMode, GateDecision


SAMPLE_RATE = 16000


def make_audio(duration_sec: float, energy: float = 0.1) -> np.ndarray:
    """Generate a mock audio array with given duration and RMS energy."""
    n_samples = int(duration_sec * SAMPLE_RATE)
    # Create signal with approximately the desired RMS energy
    signal = np.random.randn(n_samples).astype(np.float32)
    current_rms = float(np.sqrt(np.mean(signal ** 2)))
    if current_rms > 0:
        signal = signal * (energy / current_rms)
    return signal


class TestSTTGateAlwaysOn:
    def test_always_on_transcribes_everything(self):
        gate = STTGate(mode="always_on")
        audio = make_audio(0.1, energy=0.0001)  # short and quiet
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is True
        assert decision.reason == "always_on"

    def test_always_on_long_audio(self):
        gate = STTGate(mode="always_on")
        audio = make_audio(5.0, energy=0.5)
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is True


class TestSTTGateOnDemand:
    def test_on_demand_not_triggered(self):
        gate = STTGate(mode="on_demand")
        audio = make_audio(2.0, energy=0.5)
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is False
        assert "not triggered" in decision.reason

    def test_on_demand_trigger_fires_once(self):
        gate = STTGate(mode="on_demand")
        gate.trigger()
        audio = make_audio(2.0, energy=0.5)
        # First call: triggered
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is True
        assert "triggered" in decision.reason
        # Second call: not triggered anymore
        decision2 = gate.evaluate(audio)
        assert decision2.should_transcribe is False

    def test_on_demand_multiple_triggers(self):
        gate = STTGate(mode="on_demand")
        gate.trigger()
        gate.trigger()  # calling twice — still fires only once
        audio = make_audio(2.0, energy=0.5)
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is True
        decision2 = gate.evaluate(audio)
        assert decision2.should_transcribe is False


class TestSTTGateSmart:
    def test_smart_short_audio_rejected(self):
        gate = STTGate(mode="smart")
        audio = make_audio(0.1, energy=0.5)  # < 0.5s threshold
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is False
        assert "too short" in decision.reason

    def test_smart_quiet_audio_rejected(self):
        gate = STTGate(mode="smart")
        audio = make_audio(1.0, energy=0.001)  # < 0.005 threshold
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is False
        assert "too quiet" in decision.reason

    def test_smart_good_audio_accepted(self):
        gate = STTGate(mode="smart")
        audio = make_audio(1.0, energy=0.1)
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is True
        assert "duration" in decision.reason
        assert "energy" in decision.reason

    def test_smart_force_trigger_overrides_natural_smart_decision(self):
        # force trigger in smart mode only overrides the final smart decision,
        # after passing min duration and energy checks
        gate = STTGate(mode="smart")
        gate.trigger()
        audio = make_audio(1.0, energy=0.1)  # passes min checks
        decision = gate.evaluate(audio)
        assert decision.should_transcribe is True
        assert "force triggered" in decision.reason

    def test_smart_boundary_duration(self):
        gate = STTGate(mode="smart")
        # Exactly at boundary: 0.5s — sample count = 8000
        audio = make_audio(0.5, energy=0.1)
        decision = gate.evaluate(audio)
        # 0.5s is exactly the minimum, should pass
        assert decision.should_transcribe is True


class TestSTTGateModeSwitch:
    def test_mode_property(self):
        gate = STTGate(mode="always_on")
        assert gate.mode == GateMode.always_on

    def test_mode_setter(self):
        gate = STTGate(mode="always_on")
        gate.mode = "smart"
        assert gate.mode == GateMode.smart

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            STTGate(mode="invalid_mode")

    def test_trigger_state_cleared_on_evaluate(self):
        gate = STTGate(mode="smart")
        gate.trigger()
        audio = make_audio(1.0, energy=0.1)
        gate.evaluate(audio)  # consumes trigger
        assert gate._force_next is False
