"""Synthesized acoustic chimes, custom WAV import, and continuous alarm playback for SimpleFocus."""

from __future__ import annotations

import contextlib
import io
import logging
import math
import struct
import sys
import wave
from pathlib import Path

from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    validate_input_file,
)
from core.utilities.paths import get_user_data_root

logger = logging.getLogger(__name__)

SOUND_CHIME = "chime"
SOUND_BOWL = "bowl"
SOUND_PULSE = "pulse"
SOUND_BELL = "bell"
SOUND_CUSTOM = "custom"
SOUND_SILENT = "silent"

BUILTIN_SOUND_OPTIONS: tuple[tuple[str, str], ...] = (
    (SOUND_CHIME, "Harmonic Chime"),
    (SOUND_BOWL, "Zen Singing Bowl"),
    (SOUND_PULSE, "Soft Pulse"),
    (SOUND_BELL, "Classic Bell"),
    (SOUND_SILENT, "Silent (Off)"),
)

_SAMPLE_RATE = 44_100


def get_audio_storage_dir() -> Path:
    """Return and create the persistent local directory for SimpleFocus audio assets."""
    audio_dir = get_user_data_root() / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def get_custom_wav_path() -> Path:
    """Return the canonical local storage path for an imported custom alarm WAV."""
    return get_audio_storage_dir() / "custom_alarm.wav"


def has_custom_wav() -> bool:
    """Return True if the user has previously imported a valid custom alarm WAV."""
    path = get_custom_wav_path()
    return path.exists() and path.is_file() and path.stat().st_size > 44


def import_custom_wav(source_path: str | Path) -> Path:
    """Validate a user-selected .wav file and copy it atomically into local app storage."""
    validated = validate_input_file(
        source_path,
        allowed_extensions={".wav"},
        max_size_mb=15.0,
    )

    try:
        with wave.open(str(validated), "rb") as wf:
            if wf.getnframes() <= 0 or wf.getframerate() <= 0:
                raise FileOperationError("The selected WAV file contains no playable audio frames.")
    except wave.Error as exc:
        raise FileOperationError(
            f"'{validated.name}' is not a valid PCM WAV audio file: {exc}"
        ) from exc

    destination = get_custom_wav_path()
    atomic_write_bytes(destination, validated.read_bytes())
    return destination


def _encode_pcm16_wav(samples: list[float], sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Encode normalized float samples [-1.0, 1.0] into a 16-bit mono WAV byte buffer."""
    pcm = bytearray(len(samples) * 2)
    for idx, sample in enumerate(samples):
        clamped = max(-0.95, min(0.95, sample))
        struct.pack_into("<h", pcm, idx * 2, int(clamped * 32767))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))
    return buf.getvalue()


def _synthesize_harmonic_chime() -> bytes:
    """Warm ascending fifth (C5 -> G5) with soft octave shimmer and calm loop pause."""
    total_samples = int(_SAMPLE_RATE * 2.2)
    partials: tuple[tuple[float, float, float, float], ...] = (
        (0.00, 523.25, 0.34, 2.5),
        (0.00, 1046.50, 0.08, 3.8),
        (0.20, 783.99, 0.30, 2.1),
        (0.20, 1567.98, 0.06, 3.4),
    )
    samples: list[float] = []
    for i in range(total_samples):
        t = i / _SAMPLE_RATE
        val = 0.0
        for onset, freq, amp, decay in partials:
            dt = t - onset
            if dt >= 0.0:
                attack = min(1.0, dt / 0.015)
                val += amp * attack * math.exp(-decay * dt) * math.sin(2.0 * math.pi * freq * dt)
        samples.append(val)
    return _encode_pcm16_wav(samples)


def _synthesize_zen_bowl() -> bytes:
    """Deep resonant singing bowl (220 Hz fundamental + gentle harmonic beating)."""
    total_samples = int(_SAMPLE_RATE * 2.8)
    samples: list[float] = []
    for i in range(total_samples):
        t = i / _SAMPLE_RATE
        attack = min(1.0, t / 0.04)
        env_main = attack * math.exp(-1.15 * t)
        env_overtone = attack * math.exp(-1.75 * t)
        fundamental = 0.36 * env_main * math.sin(2.0 * math.pi * 220.0 * t)
        beat_pair = 0.14 * env_overtone * (
            math.sin(2.0 * math.pi * 440.0 * t) + math.sin(2.0 * math.pi * 442.2 * t)
        )
        high_shimmer = 0.07 * env_overtone * math.sin(2.0 * math.pi * 660.0 * t)
        samples.append(fundamental + beat_pair + high_shimmer)
    return _encode_pcm16_wav(samples)


def _synthesize_soft_pulse() -> bytes:
    """Gentle three-note wooden marimba pulse (E5) with calm spacing."""
    total_samples = int(_SAMPLE_RATE * 2.0)
    onsets = (0.00, 0.24, 0.48)
    samples: list[float] = []
    for i in range(total_samples):
        t = i / _SAMPLE_RATE
        val = 0.0
        for onset in onsets:
            dt = t - onset
            if dt >= 0.0:
                attack = min(1.0, dt / 0.008)
                env = attack * math.exp(-9.5 * dt)
                val += 0.34 * env * math.sin(2.0 * math.pi * 659.25 * dt)
                val += 0.10 * env * math.sin(2.0 * math.pi * 1318.5 * dt)
        samples.append(val)
    return _encode_pcm16_wav(samples)


def _synthesize_classic_bell() -> bytes:
    """Crisp mechanical desk timer bell (A5) with clean metallic overtones."""
    total_samples = int(_SAMPLE_RATE * 2.1)
    partials: tuple[tuple[float, float, float], ...] = (
        (880.0, 0.32, 2.3),
        (1760.0, 0.14, 3.6),
        (2637.0, 0.08, 5.2),
        (3520.0, 0.05, 6.8),
    )
    samples: list[float] = []
    for i in range(total_samples):
        t = i / _SAMPLE_RATE
        attack = min(1.0, t / 0.004)
        val = sum(
            amp * attack * math.exp(-decay * t) * math.sin(2.0 * math.pi * freq * t)
            for freq, amp, decay in partials
        )
        samples.append(val)
    return _encode_pcm16_wav(samples)


def ensure_builtin_sound_wav(sound_id: str) -> Path | None:
    """Ensure the requested built-in sound WAV exists on disk and return its Path."""
    if sound_id == SOUND_SILENT:
        return None
    if sound_id == SOUND_CUSTOM:
        custom = get_custom_wav_path()
        return custom if has_custom_wav() else ensure_builtin_sound_wav(SOUND_CHIME)

    generators = {
        SOUND_CHIME: _synthesize_harmonic_chime,
        SOUND_BOWL: _synthesize_zen_bowl,
        SOUND_PULSE: _synthesize_soft_pulse,
        SOUND_BELL: _synthesize_classic_bell,
    }
    resolved_id = sound_id if sound_id in generators else SOUND_CHIME
    wav_path = get_audio_storage_dir() / f"builtin_{resolved_id}.wav"

    if not wav_path.exists() or wav_path.stat().st_size <= 44:
        wav_bytes = generators[resolved_id]()
        atomic_write_bytes(wav_path, wav_bytes)

    return wav_path


class AlarmAudioPlayer:
    """Non-blocking Windows WAV player supporting single previews and continuous alarm loops."""

    def __init__(self) -> None:
        self._is_ringing: bool = False

    @property
    def is_ringing(self) -> bool:
        """Return True while a continuous completion alarm is actively looping."""
        return self._is_ringing

    def play_preview(self, sound_id: str) -> bool:
        """Play a single non-looping preview of `sound_id`. Returns True if audio started."""
        self.stop()
        if sound_id == SOUND_SILENT:
            return False

        wav_path = ensure_builtin_sound_wav(sound_id)
        if wav_path is None or not wav_path.exists():
            return False

        return self._play_windows_wav(wav_path, loop=False)

    def start_continuous_alarm(self, sound_id: str) -> bool:
        """Start looping `sound_id` continuously until `stop()` is called."""
        self.stop()
        if sound_id == SOUND_SILENT:
            return False

        wav_path = ensure_builtin_sound_wav(sound_id)
        if wav_path is None or not wav_path.exists():
            return False

        started = self._play_windows_wav(wav_path, loop=True)
        self._is_ringing = started
        return started

    def stop(self) -> None:
        """Immediately halt any active preview or looping alarm audio."""
        self._is_ringing = False
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                import winsound

                winsound.PlaySound(None, 0)

    @staticmethod
    def _play_windows_wav(wav_path: Path, loop: bool) -> bool:
        if sys.platform == "win32":
            try:
                import winsound

                flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
                if loop:
                    flags |= winsound.SND_LOOP
                winsound.PlaySound(str(wav_path), flags)
                return True
            except Exception as exc:
                logger.warning("Failed to play WAV '%s': %s", wav_path, exc)
                return False
        return False