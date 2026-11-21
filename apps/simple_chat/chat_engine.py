"""Same-network LAN chat, groups, voice clips & file sharing engine for SimpleChat."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import secrets
import shutil
import socket
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.file_manager.file_ops import (
    atomic_write_bytes,
    sanitize_filename,
    validate_input_file,
)

APP_ID = "simple_chat"
DEFAULT_LAN_PORT = 45829


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_local_ipv4_addresses() -> list[str]:
    """Return active non-loopback local IPv4 addresses on this machine."""
    ips: list[str] = []
    seen: set[str] = set()

    try:
        hostname = socket.gethostname()
        for addr_info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip_str = str(addr_info[4][0])
            if not ip_str.startswith("127.") and ip_str not in seen:
                seen.add(ip_str)
                ips.append(ip_str)
    except OSError:
        pass

    # Probe outbound interface selection without sending packets
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.25)
            sock.connect(("192.0.2.1", 80))
            probed_ip = str(sock.getsockname()[0])
            if not probed_ip.startswith("127.") and probed_ip not in seen:
                seen.add(probed_ip)
                ips.append(probed_ip)
    except OSError:
        pass

    return ips


def _ipv4_to_subnet_prefix(ip_str: str) -> str:
    """Return a /24 subnet prefix (e.g. '192.168.1') for an IPv4 address."""
    try:
        addr = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return ""
    parts = str(addr).split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ""


@dataclass(frozen=True, slots=True)
class NetworkConditionStatus:
    """Result of evaluating whether the device satisfies same-network rules."""

    allowed: bool
    reason: str
    local_ips: tuple[str, ...]
    subnet_tag: str


class NetworkConditionPolicy:
    """Enforces same-network / LAN-only eligibility conditions before chat & sharing."""

    def __init__(
        self,
        require_private_lan: bool = True,
        allowed_subnet_prefix: str = "",
        network_passphrase: str = "",
    ) -> None:
        self.require_private_lan = bool(require_private_lan)
        self.allowed_subnet_prefix = allowed_subnet_prefix.strip()
        self.network_passphrase = network_passphrase.strip()

    def evaluate(
        self,
        override_ips: list[str] | None = None,
        peer_ip: str = "",
        session_passphrase: str = "",
    ) -> NetworkConditionStatus:
        """Check whether local/peer addresses and optional LAN condition pass."""
        ips = override_ips if override_ips is not None else get_local_ipv4_addresses()
        valid_ips: list[str] = []

        for raw_ip in ips:
            try:
                addr = ipaddress.IPv4Address(raw_ip)
            except ValueError:
                continue
            if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
                continue
            if self.require_private_lan and not addr.is_private:
                continue
            valid_ips.append(str(addr))

        if not valid_ips:
            if self.require_private_lan:
                return NetworkConditionStatus(
                    allowed=False,
                    reason=(
                        "No active local/private LAN IPv4 address detected. "
                        "Connect to your local Wi-Fi or Ethernet network first."
                    ),
                    local_ips=tuple(ips),
                    subnet_tag="disconnected",
                )
            return NetworkConditionStatus(
                allowed=False,
                reason="No active non-loopback network interface detected.",
                local_ips=tuple(ips),
                subnet_tag="disconnected",
            )

        primary_ip = valid_ips[0]
        local_subnet = _ipv4_to_subnet_prefix(primary_ip)

        if self.allowed_subnet_prefix and local_subnet != self.allowed_subnet_prefix:
            return NetworkConditionStatus(
                allowed=False,
                reason=(
                    f"Network mismatch: device subnet '{local_subnet}.x' does not match "
                    f"required same-network subnet '{self.allowed_subnet_prefix}.x'."
                ),
                local_ips=tuple(valid_ips),
                subnet_tag=f"{local_subnet}.0/24",
            )

        if peer_ip.strip():
            peer_subnet = _ipv4_to_subnet_prefix(peer_ip.strip())
            if not peer_subnet or peer_subnet != local_subnet:
                return NetworkConditionStatus(
                    allowed=False,
                    reason=(
                        f"Peer IP '{peer_ip}' is outside same local network '{local_subnet}.x'."
                    ),
                    local_ips=tuple(valid_ips),
                    subnet_tag=f"{local_subnet}.0/24",
                )

        if self.network_passphrase and session_passphrase != self.network_passphrase:
            return NetworkConditionStatus(
                allowed=False,
                reason="Same-network access condition failed: network key does not match.",
                local_ips=tuple(valid_ips),
                subnet_tag=f"{local_subnet}.0/24",
            )

        return NetworkConditionStatus(
            allowed=True,
            reason=f"Connected on local network ({local_subnet}.x)",
            local_ips=tuple(valid_ips),
            subnet_tag=f"{local_subnet}.0/24",
        )


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Hash a user password using PBKDF2-HMAC-SHA256 with a per-user random salt."""
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters long.")
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations=120_000,
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    """Constant-time verification of a password against stored salt + PBKDF2 digest."""
    try:
        _, candidate_hex = hash_password(password, salt_hex=salt_hex)
        return hmac.compare_digest(candidate_hex, expected_hash_hex)
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ChatUserAccount:
    """Registered local-network user profile."""

    user_id: str
    username: str
    display_name: str
    subnet_tag: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ChatRoomInfo:
    """1-on-1 direct conversation or multi-user group room."""

    room_id: str
    name: str
    is_group: bool
    members: tuple[str, ...]
    created_by: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    """Message supporting text, voice clips, and file attachments."""

    message_id: str
    room_id: str
    sender_username: str
    kind: str  # 'text' | 'voice' | 'file'
    content: str
    attachment_path: str
    attachment_name: str
    duration_sec: float
    subnet_tag: str
    sent_at: str


class ChatStorageStore:
    """SQLite WAL persistence for local users, groups, messages, voice clips, and files."""

    def __init__(self, db_path: str | Path, attachments_dir: str | Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachments_dir = (
            Path(attachments_dir)
            if attachments_dir is not None
            else (self.db_path.parent / "attachments")
        )
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    salt_hex TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    subnet_tag TEXT NOT NULL DEFAULT '192.168.1.0/24',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    room_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_group INTEGER NOT NULL DEFAULT 0,
                    members_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    sender_username TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'text',
                    content TEXT NOT NULL DEFAULT '',
                    attachment_path TEXT NOT NULL DEFAULT '',
                    attachment_name TEXT NOT NULL DEFAULT '',
                    duration_sec REAL NOT NULL DEFAULT 0.0,
                    subnet_tag TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_room_sent ON messages(room_id, sent_at);"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Authentication: Register & Login with Username + Password
    # ------------------------------------------------------------------

    def register_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        subnet_tag: str = "local-lan",
    ) -> ChatUserAccount:
        clean_user = username.strip().lower()
        if len(clean_user) < 2 or not all(ch.isalnum() or ch in {"_", "."} for ch in clean_user):
            raise ValueError(
                "Username must be at least 2 characters (letters, numbers, '_', or '.')."
            )
        salt_hex, pw_hash = hash_password(password)
        user_id = f"usr_{uuid.uuid4().hex[:10]}"
        disp = display_name.strip() or clean_user
        now_iso = _utc_now_iso()

        with self._lock:
            existing = self._conn.execute(
                "SELECT user_id FROM users WHERE lower(username) = ?",
                (clean_user,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Username '{clean_user}' is already registered on this network.")

            self._conn.execute(
                """
                INSERT INTO users (user_id, username, display_name, salt_hex, password_hash, subnet_tag, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (user_id, clean_user, disp, salt_hex, pw_hash, subnet_tag, now_iso),
            )
            self._conn.commit()

        return ChatUserAccount(
            user_id=user_id,
            username=clean_user,
            display_name=disp,
            subnet_tag=subnet_tag,
            created_at=now_iso,
        )

    def authenticate_user(self, username: str, password: str) -> ChatUserAccount | None:
        clean_user = username.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE lower(username) = ?;",
                (clean_user,),
            ).fetchone()
        if row is None:
            return None
        if not verify_password(password, str(row["salt_hex"]), str(row["password_hash"])):
            return None
        return ChatUserAccount(
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            subnet_tag=str(row["subnet_tag"]),
            created_at=str(row["created_at"]),
        )

    def list_users(self) -> list[ChatUserAccount]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY username ASC;").fetchall()
        return [
            ChatUserAccount(
                user_id=str(r["user_id"]),
                username=str(r["username"]),
                display_name=str(r["display_name"]),
                subnet_tag=str(r["subnet_tag"]),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Direct Chats & Group Creation
    # ------------------------------------------------------------------

    @staticmethod
    def direct_room_id(user_a: str, user_b: str) -> str:
        pair = sorted((user_a.strip().lower(), user_b.strip().lower()))
        return f"dm_{pair[0]}_{pair[1]}"

    def get_or_create_direct_room(self, user_a: str, user_b: str) -> ChatRoomInfo:
        a = user_a.strip().lower()
        b = user_b.strip().lower()
        rid = self.direct_room_id(a, b)
        members_sorted = tuple(sorted({a, b}))
        label = b if a != b else a
        now_iso = _utc_now_iso()

        with self._lock:
            existing = self._conn.execute("SELECT * FROM rooms WHERE room_id = ?;", (rid,)).fetchone()
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO rooms (room_id, name, is_group, members_json, created_by, created_at)
                    VALUES (?, ?, 0, ?, ?, ?);
                    """,
                    (rid, label, json.dumps(list(members_sorted)), a, now_iso),
                )
                self._conn.commit()
                created_at = now_iso
            else:
                created_at = str(existing["created_at"])

        return ChatRoomInfo(
            room_id=rid,
            name=label,
            is_group=False,
            members=members_sorted,
            created_by=a,
            created_at=created_at,
        )

    def create_group_room(
        self,
        group_name: str,
        creator_username: str,
        member_usernames: list[str],
    ) -> ChatRoomInfo:
        clean_name = group_name.strip()
        if not clean_name:
            raise ValueError("Group name cannot be empty.")
        creator = creator_username.strip().lower()
        all_members = sorted(
            {creator} | {m.strip().lower() for m in member_usernames if m.strip()}
        )
        room_id = f"grp_{uuid.uuid4().hex[:10]}"
        now_iso = _utc_now_iso()

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO rooms (room_id, name, is_group, members_json, created_by, created_at)
                VALUES (?, ?, 1, ?, ?, ?);
                """,
                (room_id, clean_name, json.dumps(all_members), creator, now_iso),
            )
            self._conn.commit()

        return ChatRoomInfo(
            room_id=room_id,
            name=clean_name,
            is_group=True,
            members=tuple(all_members),
            created_by=creator,
            created_at=now_iso,
        )

    def list_rooms_for_user(self, username: str) -> list[ChatRoomInfo]:
        clean_user = username.strip().lower()
        with self._lock:
            rows = self._conn.execute("SELECT * FROM rooms ORDER BY created_at ASC;").fetchall()

        results: list[ChatRoomInfo] = []
        for r in rows:
            try:
                members = tuple(json.loads(str(r["members_json"])))
            except Exception:
                members = ()
            is_grp = bool(int(r["is_group"]))
            if not is_grp and clean_user not in members:
                continue
            results.append(
                ChatRoomInfo(
                    room_id=str(r["room_id"]),
                    name=str(r["name"]),
                    is_group=is_grp,
                    members=members,
                    created_by=str(r["created_by"]),
                    created_at=str(r["created_at"]),
                )
            )
        return results

    # ------------------------------------------------------------------
    # Messages, Voice Notes & Shared Files
    # ------------------------------------------------------------------

    def send_text_message(
        self,
        room_id: str,
        sender_username: str,
        text: str,
        subnet_tag: str = "local-lan",
    ) -> ChatMessageRecord:
        clean_txt = text.strip()
        if not clean_txt:
            raise ValueError("Cannot send an empty message.")
        rec = ChatMessageRecord(
            message_id=f"msg_{uuid.uuid4().hex[:10]}",
            room_id=room_id,
            sender_username=sender_username.strip().lower(),
            kind="text",
            content=clean_txt,
            attachment_path="",
            attachment_name="",
            duration_sec=0.0,
            subnet_tag=subnet_tag,
            sent_at=_utc_now_iso(),
        )
        self.insert_message(rec)
        return rec

    def send_file_message(
        self,
        room_id: str,
        sender_username: str,
        source_file: str | Path,
        caption: str = "",
        subnet_tag: str = "local-lan",
    ) -> ChatMessageRecord:
        validated = validate_input_file(source_file, max_size_mb=200.0)
        safe_name = sanitize_filename(validated.name)
        stored_path = self.attachments_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        shutil.copy2(validated, stored_path)

        rec = ChatMessageRecord(
            message_id=f"msg_{uuid.uuid4().hex[:10]}",
            room_id=room_id,
            sender_username=sender_username.strip().lower(),
            kind="file",
            content=caption.strip() or f"Shared file: {validated.name}",
            attachment_path=str(stored_path.resolve()),
            attachment_name=validated.name,
            duration_sec=0.0,
            subnet_tag=subnet_tag,
            sent_at=_utc_now_iso(),
        )
        self.insert_message(rec)
        return rec

    def send_voice_message(
        self,
        room_id: str,
        sender_username: str,
        audio_bytes: bytes,
        file_ext: str = ".wav",
        duration_sec: float = 1.0,
        subnet_tag: str = "local-lan",
    ) -> ChatMessageRecord:
        if not audio_bytes:
            raise ValueError("Voice recording payload is empty.")
        clean_ext = file_ext if file_ext.startswith(".") else f".{file_ext}"
        fname = f"voice_{uuid.uuid4().hex[:8]}{clean_ext}"
        stored_path = atomic_write_bytes(self.attachments_dir / fname, audio_bytes, backup_app_id=APP_ID)

        rec = ChatMessageRecord(
            message_id=f"msg_{uuid.uuid4().hex[:10]}",
            room_id=room_id,
            sender_username=sender_username.strip().lower(),
            kind="voice",
            content=f"Voice message ({duration_sec:.1f}s)",
            attachment_path=str(stored_path.resolve()),
            attachment_name=fname,
            duration_sec=max(0.1, float(duration_sec)),
            subnet_tag=subnet_tag,
            sent_at=_utc_now_iso(),
        )
        self.insert_message(rec)
        return rec

    def insert_message(self, rec: ChatMessageRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id, room_id, sender_username, kind, content,
                    attachment_path, attachment_name, duration_sec, subnet_tag, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    rec.message_id,
                    rec.room_id,
                    rec.sender_username,
                    rec.kind,
                    rec.content,
                    rec.attachment_path,
                    rec.attachment_name,
                    rec.duration_sec,
                    rec.subnet_tag,
                    rec.sent_at,
                ),
            )
            self._conn.commit()

    def list_messages(self, room_id: str) -> list[ChatMessageRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE room_id = ? ORDER BY rowid ASC;",
                (room_id,),
            ).fetchall()
        return [
            ChatMessageRecord(
                message_id=str(r["message_id"]),
                room_id=str(r["room_id"]),
                sender_username=str(r["sender_username"]),
                kind=str(r["kind"]),
                content=str(r["content"]),
                attachment_path=str(r["attachment_path"]),
                attachment_name=str(r["attachment_name"]),
                duration_sec=float(r["duration_sec"]),
                subnet_tag=str(r["subnet_tag"]),
                sent_at=str(r["sent_at"]),
            )
            for r in rows
        ]


def build_sample_voice_wav_bytes(duration_sec: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a valid PCM 16-bit mono WAV audio byte sequence for voice clip sharing."""
    import math
    import struct

    num_samples = max(1600, int(sample_rate * max(0.2, min(10.0, duration_sec))))
    pcm_frames = bytearray()
    for i in range(num_samples):
        # Gentle soft chime envelope so any local audio player plays cleanly
        t = i / float(sample_rate)
        env = math.sin(math.pi * min(1.0, i / float(num_samples)))
        sample = int(3200.0 * env * math.sin(2.0 * math.pi * 440.0 * t))
        pcm_frames.extend(struct.pack("<h", max(-32767, min(32767, sample))))

    data_size = len(pcm_frames)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # Mono
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + bytes(pcm_frames)


class LocalLanChatRelay:
    """Lightweight LAN TCP relay so same-network peers can exchange messages & files."""

    def __init__(self, store: ChatStorageStore, host: str = "0.0.0.0", port: int = 0) -> None:
        self.store = store
        self.host = host
        self.requested_port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.bound_port: int = 0

    def start(self) -> int:
        if self._sock is not None:
            return self.bound_port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.requested_port))
        srv.listen(8)
        srv.settimeout(0.3)
        self.bound_port = int(srv.getsockname()[1])
        self._sock = srv
        self._stop_event.clear()

        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        return self.bound_port

    def stop(self) -> None:
        self._stop_event.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _serve_loop(self) -> None:
        assert self._sock is not None
        while not self._stop_event.is_set():
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break

            with conn:
                conn.settimeout(2.0)
                buf = b""
                while not self._stop_event.is_set():
                    try:
                        chunk = conn.recv(65536)
                    except TimeoutError:
                        break
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._handle_packet(line, addr[0])

    def _handle_packet(self, raw_line: bytes, sender_ip: str) -> None:
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict) or payload.get("type") != "chat_message":
            return

        msg_id = str(payload.get("message_id") or f"msg_{uuid.uuid4().hex[:10]}")
        room_id = str(payload.get("room_id") or "")
        sender = str(payload.get("sender_username") or "peer")
        kind = str(payload.get("kind") or "text")
        content = str(payload.get("content") or "")
        att_name = sanitize_filename(str(payload.get("attachment_name") or ""))
        duration = float(payload.get("duration_sec") or 0.0)
        subnet_tag = str(payload.get("subnet_tag") or sender_ip)
        sent_at = str(payload.get("sent_at") or _utc_now_iso())

        stored_path = ""
        b64_data = payload.get("attachment_b64")
        if isinstance(b64_data, str) and b64_data:
            try:
                raw_bytes = base64.b64decode(b64_data)
                out_file = self.store.attachments_dir / f"lan_{uuid.uuid4().hex[:8]}_{att_name or 'file.bin'}"
                atomic_write_bytes(out_file, raw_bytes, backup_app_id=APP_ID)
                stored_path = str(out_file.resolve())
            except Exception:
                pass

        if room_id:
            self.store.insert_message(
                ChatMessageRecord(
                    message_id=msg_id,
                    room_id=room_id,
                    sender_username=sender,
                    kind=kind,
                    content=content,
                    attachment_path=stored_path,
                    attachment_name=att_name,
                    duration_sec=duration,
                    subnet_tag=subnet_tag,
                    sent_at=sent_at,
                )
            )

    @staticmethod
    def push_message_to_peer(
        target_ip: str,
        target_port: int,
        record: ChatMessageRecord,
        timeout_sec: float = 2.0,
    ) -> bool:
        """Send a serialized ChatMessageRecord (including file/voice payload) to a LAN peer."""
        payload: dict[str, object] = {
            "type": "chat_message",
            "message_id": record.message_id,
            "room_id": record.room_id,
            "sender_username": record.sender_username,
            "kind": record.kind,
            "content": record.content,
            "attachment_name": record.attachment_name,
            "duration_sec": record.duration_sec,
            "subnet_tag": record.subnet_tag,
            "sent_at": record.sent_at,
        }
        if record.attachment_path and Path(record.attachment_path).exists():
            raw_bytes = Path(record.attachment_path).read_bytes()
            if len(raw_bytes) <= 15 * 1024 * 1024:
                payload["attachment_b64"] = base64.b64encode(raw_bytes).decode("ascii")

        encoded = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with socket.create_connection((target_ip, int(target_port)), timeout=timeout_sec) as sock:
                sock.sendall(encoded)
            return True
        except OSError:
            return False