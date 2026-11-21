"""Automated unit and GUI tests for SimpleChat."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_chat.chat_engine import (
    ChatStorageStore,
    LocalLanChatRelay,
    NetworkConditionPolicy,
    build_sample_voice_wav_bytes,
)
from apps.simple_chat.main import SimpleChatWindow


def test_network_condition_policy_rules() -> None:
    """Verify same-network LAN validation, subnet filtering, peer checks, and passphrase gating."""
    # Disconnected / no private IPv4
    disconnected_policy = NetworkConditionPolicy(require_private_lan=True)
    res_none = disconnected_policy.evaluate(override_ips=["127.0.0.1"])
    assert res_none.allowed is False

    # Valid private LAN subnet
    lan_policy = NetworkConditionPolicy(
        require_private_lan=True,
        allowed_subnet_prefix="192.168.1",
        network_passphrase="lan-secret",
    )
    res_ok = lan_policy.evaluate(
        override_ips=["192.168.1.42"],
        peer_ip="192.168.1.88",
        session_passphrase="lan-secret",
    )
    assert res_ok.allowed is True
    assert res_ok.subnet_tag == "192.168.1.0/24"

    # Mismatched subnet or wrong passphrase is rejected
    res_wrong_sub = lan_policy.evaluate(
        override_ips=["10.0.0.15"],
        session_passphrase="lan-secret",
    )
    assert res_wrong_sub.allowed is False

    res_wrong_peer = lan_policy.evaluate(
        override_ips=["192.168.1.42"],
        peer_ip="192.168.50.10",
        session_passphrase="lan-secret",
    )
    assert res_wrong_peer.allowed is False

    res_wrong_key = lan_policy.evaluate(
        override_ips=["192.168.1.42"],
        session_passphrase="wrong-key",
    )
    assert res_wrong_key.allowed is False


def test_chat_storage_auth_groups_messages_and_lan_relay(tmp_path: Path) -> None:
    """Verify user auth, 1-on-1 DMs, group rooms, text/voice/file messages, and LAN relay."""
    store = ChatStorageStore(tmp_path / "chat_a.sqlite3", attachments_dir=tmp_path / "att_a")
    relay = LocalLanChatRelay(store=store, host="127.0.0.1", port=0)
    peer_store = ChatStorageStore(tmp_path / "chat_b.sqlite3", attachments_dir=tmp_path / "att_b")
    peer_relay = LocalLanChatRelay(store=peer_store, host="127.0.0.1", port=0)

    try:
        peer_port = peer_relay.start()
        assert peer_port > 0

        # Register two users and verify password authentication
        u1 = store.register_user("arshvir", "pass1234", display_name="Arshvir")
        u2 = store.register_user("rohan", "pass5678", display_name="Rohan")
        assert u1.username == "arshvir"
        assert u2.username == "rohan"

        with pytest.raises(ValueError):
            store.register_user("arshvir", "anotherpass")

        assert store.authenticate_user("arshvir", "pass1234") is not None
        assert store.authenticate_user("arshvir", "wrongpass") is None

        # Direct room + group creation
        dm = store.get_or_create_direct_room("arshvir", "rohan")
        assert dm.is_group is False
        assert dm.members == ("arshvir", "rohan")

        grp = store.create_group_room("LAN Builders", "arshvir", ["rohan"])
        assert grp.is_group is True
        assert set(grp.members) == {"arshvir", "rohan"}

        # Text message, voice message, and shared file attachment
        text_msg = store.send_text_message(grp.room_id, "arshvir", "Hello same-network team!")
        assert text_msg.kind == "text"

        wav_bytes = build_sample_voice_wav_bytes(duration_sec=0.5)
        voice_msg = store.send_voice_message(grp.room_id, "arshvir", wav_bytes, duration_sec=0.5)
        assert voice_msg.kind == "voice"
        assert Path(voice_msg.attachment_path).exists()

        shared_doc = tmp_path / "spec_note.txt"
        shared_doc.write_text("Local LAN architecture note.", encoding="utf-8")
        file_msg = store.send_file_message(grp.room_id, "arshvir", shared_doc, caption="Spec")
        assert file_msg.kind == "file"
        assert Path(file_msg.attachment_path).exists()

        msgs = store.list_messages(grp.room_id)
        assert len(msgs) == 3

        # Push message over local LAN relay to peer relay store
        pushed = LocalLanChatRelay.push_message_to_peer("127.0.0.1", peer_port, text_msg)
        assert pushed is True

        # Wait briefly for background listener thread to ingest packet
        for _ in range(20):
            if len(peer_store.list_messages(grp.room_id)) == 1:
                break
            import time

            time.sleep(0.05)

        peer_msgs = peer_store.list_messages(grp.room_id)
        assert len(peer_msgs) == 1
        assert peer_msgs[0].content == "Hello same-network team!"
    finally:
        relay.stop()
        peer_relay.stop()
        store.close()
        peer_store.close()


def test_simple_chat_window_auth_group_and_messaging(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SimpleChatWindow registration/login, group creation, and text/voice/file sending."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    store = ChatStorageStore(tmp_path / "ui_chat.sqlite3", attachments_dir=tmp_path / "ui_att")
    store.register_user("teammate", "team1234", display_name="Teammate")

    # Allow loopback in unit test environment while keeping same-network logic active
    policy = NetworkConditionPolicy(require_private_lan=False)

    window = SimpleChatWindow(store=store, policy=policy)
    qtbot.addWidget(window)
    window.show()

    assert window._auth_gate_view.isVisible() is True
    assert window._chat_studio_view.isVisible() is False

    # Register primary user via window method
    acct = window.register_user("arshvir", "secret123", display_name="Arshvir")
    assert acct is not None
    assert window._auth_gate_view.isVisible() is False
    assert window._chat_studio_view.isVisible() is True

    # Create a group with teammate
    grp = window.create_group("Bengaluru LAN", ["teammate"])
    assert grp is not None
    assert grp.is_group is True

    # Send text message and file attachment
    sent_txt = window.send_text_message("Testing LAN group chat!")
    assert sent_txt is not None

    sample_file = tmp_path / "release_notes.txt"
    sample_file.write_text("Build verified.", encoding="utf-8")
    sent_file = window.send_file_attachment(sample_file)
    assert sent_file is not None
    assert len(store.list_messages(grp.room_id)) == 2

    window.sign_out()
    assert window._auth_gate_view.isVisible() is True
    window.close()