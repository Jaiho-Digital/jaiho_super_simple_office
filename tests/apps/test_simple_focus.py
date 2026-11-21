"""Automated unit and GUI tests for SimpleFocus."""

from __future__ import annotations

import io
import struct
import wave
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_focus.audio import (
    SOUND_BELL,
    SOUND_BOWL,
    SOUND_CHIME,
    SOUND_CUSTOM,
    SOUND_PULSE,
    SOUND_SILENT,
    AlarmAudioPlayer,
    ensure_builtin_sound_wav,
    has_custom_wav,
    import_custom_wav,
)
from apps.simple_focus.main import SimpleFocusWindow, format_mm_ss
from apps.simple_focus.repository import FocusPhase, FocusRepository
from core.database.db_manager import DatabaseManager
from core.file_manager.file_ops import FileOperationError


class _SilentTestAudioPlayer(AlarmAudioPlayer):
    """Test double that tracks ringing/preview state without playing hardware audio."""

    def __init__(self) -> None:
        super().__init__()
        self.preview_history: list[str] = []
        self.alarm_history: list[str] = []

    def play_preview(self, sound_id: str) -> bool:
        self.stop()
        if sound_id == SOUND_SILENT:
            return False
        self.preview_history.append(sound_id)
        return True

    def start_continuous_alarm(self, sound_id: str) -> bool:
        self.stop()
        if sound_id == SOUND_SILENT:
            return False
        self.alarm_history.append(sound_id)
        self._is_ringing = True
        return True

    def stop(self) -> None:
        self._is_ringing = False


def _make_valid_wav_file(target: Path, duration_sec: float = 0.1) -> Path:
    """Write a minimal valid 16-bit mono PCM WAV file for testing custom audio import."""
    sample_rate = 16_000
    num_samples = int(sample_rate * duration_sec)
    frames = struct.pack(f"<{num_samples}h", *([0] * num_samples))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)

    target.write_bytes(buf.getvalue())
    return target


def test_format_mm_ss_formatting() -> None:
    """Verify countdown display rounds fractional seconds upward cleanly."""
    assert format_mm_ss(1500.0) == "25:00"
    assert format_mm_ss(61.1) == "01:02"
    assert format_mm_ss(0.2) == "00:01"
    assert format_mm_ss(0.0) == "00:00"
    assert format_mm_ss(-3.0) == "00:00"


def test_focus_repository_stats_and_streaks(tmp_path: Path) -> None:
    """Verify session persistence, daily focus minute totals, and consecutive day streaks."""
    db = DatabaseManager(app_id="simple_focus", db_path=tmp_path / "focus_test.db")
    repo = FocusRepository(db=db)

    today = date(2026, 11, 21)
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    repo.record_session(
        task_label="Design core schema",
        phase=FocusPhase.FOCUS,
        planned_minutes=25,
        actual_seconds=1500,
        completed=True,
        completed_at=datetime(2026, 11, 19, 10, 0, 0),
    )
    repo.record_session(
        task_label="Build calculator engine",
        phase=FocusPhase.FOCUS,
        planned_minutes=25,
        actual_seconds=1500,
        completed=True,
        completed_at=datetime(2026, 11, 20, 14, 30, 0),
    )
    repo.record_session(
        task_label="Short rest",
        phase=FocusPhase.SHORT_BREAK,
        planned_minutes=5,
        actual_seconds=300,
        completed=True,
        completed_at=datetime(2026, 11, 20, 14, 36, 0),
    )
    repo.record_session(
        task_label="Write SimpleTasks",
        phase=FocusPhase.FOCUS,
        planned_minutes=45,
        actual_seconds=2700,
        completed=True,
        completed_at=datetime(2026, 11, 21, 9, 15, 0),
    )

    today_stats = repo.get_daily_stats(target_date=today)
    assert today_stats.completed_focus_sessions == 1
    assert today_stats.total_focus_minutes == 45
    assert today_stats.completed_break_sessions == 0

    yesterday_stats = repo.get_daily_stats(target_date=yesterday)
    assert yesterday_stats.completed_focus_sessions == 1
    assert yesterday_stats.total_focus_minutes == 25
    assert yesterday_stats.completed_break_sessions == 1

    assert repo.get_current_streak_days(reference_date=today) == 3
    assert repo.get_current_streak_days(reference_date=two_days_ago) == 1
    assert len(repo.list_recent_sessions(limit=10, only_focus=True)) == 3

    repo.clear_all_sessions()
    assert repo.get_daily_stats(target_date=today).completed_focus_sessions == 0


def test_builtin_sound_synthesis_and_custom_wav_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify synthesized built-in WAV tones and custom WAV validation/import."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    for sound_id in (SOUND_CHIME, SOUND_BOWL, SOUND_PULSE, SOUND_BELL):
        wav_path = ensure_builtin_sound_wav(sound_id)
        assert wav_path is not None
        assert wav_path.exists()
        with wave.open(str(wav_path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 44_100
            assert wf.getnframes() > 10_000

    assert ensure_builtin_sound_wav(SOUND_SILENT) is None
    assert has_custom_wav() is False

    # Reject non-WAV extension
    bad_ext = tmp_path / "ringtone.mp3"
    bad_ext.write_bytes(b"not-a-wav")
    with pytest.raises(FileOperationError):
        import_custom_wav(bad_ext)

    # Reject corrupt WAV header
    corrupt_wav = tmp_path / "corrupt.wav"
    corrupt_wav.write_bytes(b"RIFF0000WAVEcorrupt")
    with pytest.raises(FileOperationError):
        import_custom_wav(corrupt_wav)

    # Import valid custom WAV successfully
    valid_wav = _make_valid_wav_file(tmp_path / "my_bell.wav")
    stored_path = import_custom_wav(valid_wav)
    assert stored_path.exists()
    assert has_custom_wav() is True
    assert ensure_builtin_sound_wav(SOUND_CUSTOM) == stored_path


def test_simple_focus_window_timer_alarm_and_cycle_progression(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify start/pause/reset, continuous alarm stop, 4-cycle long break, and close behavior."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    silent_audio = _SilentTestAudioPlayer()
    window = SimpleFocusWindow(audio_player=silent_audio)
    qtbot.addWidget(window)
    window.show()

    # Default state: Focus 25m
    assert window._current_phase == FocusPhase.FOCUS
    assert window._dial._time_text == "25:00"
    assert window._primary_btn.text() == "Start"

    # Switch preset to 15m
    window._preset_buttons[0].click()
    assert window._durations[FocusPhase.FOCUS] == 15
    assert window._dial._time_text == "15:00"

    # Start and pause timer
    window._task_input.setText("Draft SimpleTasks architecture")
    window.toggle_start_pause()
    assert window._timer_state == "running"
    assert window._primary_btn.text() == "Pause"

    # Closing while running minimizes to tray instead of exiting when tray is supported
    if window.notifier.tray_supported:
        assert window.can_close() is False
        assert not window.isVisible()
        window.showNormal()

    window.toggle_start_pause()
    assert window._timer_state == "paused"
    assert window._primary_btn.text() == "Resume"

    # Complete current focus phase -> triggers continuous alarm and logs session
    window._complete_current_phase()
    assert silent_audio.is_ringing is True
    assert silent_audio.alarm_history == [SOUND_CHIME]
    assert window._stop_alarm_btn.isVisible()
    assert not window._primary_btn.isVisible()
    assert window._current_phase == FocusPhase.SHORT_BREAK
    assert "Today: 1 session" in window._summary_lbl.text()

    # Clicking Stop Alarm silences audio and restores transport buttons
    window.stop_alarm()
    assert silent_audio.is_ringing is False
    assert not window._stop_alarm_btn.isVisible()
    assert window._primary_btn.isVisible()

    # Verify 4th completed focus block transitions into Long Break
    window.select_phase(FocusPhase.FOCUS)
    window._completed_focus_in_cycle = 3
    window._complete_current_phase()
    assert window._current_phase == FocusPhase.LONG_BREAK
    window.stop_alarm()

    window.quit_application()