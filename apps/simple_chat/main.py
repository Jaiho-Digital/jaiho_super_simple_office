"""SimpleChat — Soft Modern Pastel local-network chat, groups, voice & file sharing."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.simple_chat.chat_engine import (
    APP_ID,
    ChatMessageRecord,
    ChatRoomInfo,
    ChatStorageStore,
    ChatUserAccount,
    LocalLanChatRelay,
    NetworkConditionPolicy,
    build_sample_voice_wav_bytes,
    get_local_ipv4_addresses,
)
from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import show_error_dialog
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    SectionHeader,
    StatusBadge,
    VerticalDivider,
    make_button,
)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # pragma: no cover
    QAudioOutput = None  # type: ignore[assignment,misc]
    QMediaPlayer = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


def _default_chat_db_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "JaihoSuperSimpleOffice" / APP_ID / "simple_chat.sqlite3"


class CreateGroupDialog(QDialog):
    """Dialog for creating a new same-network group room with selected members."""

    def __init__(
        self,
        available_usernames: list[str],
        current_username: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Network Group")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        layout.addWidget(SectionHeader("New Group Chat", parent=self))

        name_lbl = QLabel("Group Name", self)
        name_lbl.setProperty("role", "caption")
        layout.addWidget(name_lbl)

        self._name_edit = QLineEdit(self)
        self._name_edit.setPlaceholderText("e.g. Engineering LAN, Project Team...")
        layout.addWidget(self._name_edit)

        mem_lbl = QLabel("Select Members (in addition to you)", self)
        mem_lbl.setProperty("role", "caption")
        layout.addWidget(mem_lbl)

        self._checks: list[tuple[str, QCheckBox]] = []
        for uname in available_usernames:
            if uname == current_username:
                continue
            chk = QCheckBox(uname, self)
            chk.setChecked(True)
            layout.addWidget(chk)
            self._checks.append((uname, chk))

        if not self._checks:
            none_lbl = QLabel("No other users registered yet — you can still create the group.", self)
            none_lbl.setProperty("role", "muted")
            none_lbl.setWordWrap(True)
            layout.addWidget(none_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def group_name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def selected_members(self) -> list[str]:
        return [uname for uname, chk in self._checks if chk.isChecked()]


class SimpleChatWindow(BaseWindow):
    """Main window for SimpleChat — same-network login gate, 1-on-1 & group chat, voice & files."""

    def __init__(
        self,
        store: ChatStorageStore | None = None,
        policy: NetworkConditionPolicy | None = None,
    ) -> None:
        super().__init__(app_id=APP_ID, min_width=980, min_height=650)
        if self.settings.get("window_width") is None:
            self.resize(1240, 790)

        self.store: ChatStorageStore = store or ChatStorageStore(_default_chat_db_path())
        self.policy: NetworkConditionPolicy = policy or NetworkConditionPolicy(
            require_private_lan=True,
            allowed_subnet_prefix=str(self.settings.get("allowed_subnet_prefix", "") or ""),
            network_passphrase=str(self.settings.get("network_passphrase", "") or ""),
        )
        self._relay = LocalLanChatRelay(store=self.store, host="127.0.0.1", port=0)
        try:
            self._relay.start()
        except Exception:
            pass

        self._current_user: ChatUserAccount | None = None
        self._current_room: ChatRoomInfo | None = None
        self._session_passphrase: str = ""
        self._peer_ip_filter: str = ""

        # Optional audio playback for voice messages
        self._player = QMediaPlayer(self) if QMediaPlayer is not None else None
        self._audio_out = QAudioOutput(self) if QAudioOutput is not None else None
        if self._player is not None and self._audio_out is not None:
            self._player.setAudioOutput(self._audio_out)

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._refresh_network_condition_ui()
        self._sync_auth_state()

    def _build_header_actions(self) -> None:
        """Mount Network Check, New Group, and Sign Out actions in the header."""
        net_btn = make_button(
            text="Check Network",
            variant="ghost",
            tooltip="Re-verify same-network connection conditions",
            on_click=self._refresh_network_condition_ui,
            parent=self,
        )
        net_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(net_btn)

        self._new_group_btn = make_button(
            text="+ New Group",
            variant="pastel",
            tooltip="Create a group chat for same-network members",
            on_click=self.create_group_prompt,
            icon_name="plus",
            icon_size=14,
            parent=self,
        )
        self._new_group_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._new_group_btn)

        self._logout_btn = make_button(
            text="Sign Out",
            variant="ghost",
            tooltip="Sign out of current account",
            on_click=self.sign_out,
            parent=self,
        )
        self._logout_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._logout_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # View A: Username + Password Login / Register & Network Condition Gate
        # ==============================================================
        self._auth_gate_view = QWidget(central)
        gate_layout = QVBoxLayout(self._auth_gate_view)
        gate_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gate_layout.setContentsMargins(24, 24, 24, 24)

        auth_card = CardFrame(padding=28, spacing=14, parent=self._auth_gate_view)
        auth_card.setMaximumWidth(480)

        gate_title = QLabel("SimpleChat — Local Network Sign In", auth_card)
        gate_title.setProperty("role", "title")
        gate_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        auth_card.body_layout.addWidget(gate_title)

        gate_sub = QLabel(
            "Sign in or register with a username and password to message members, "
            "share voice clips, and send files over your local network.",
            auth_card,
        )
        gate_sub.setProperty("role", "muted")
        gate_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gate_sub.setWordWrap(True)
        auth_card.body_layout.addWidget(gate_sub)

        self._gate_net_badge = StatusBadge("Checking LAN...", variant="butter", parent=auth_card)
        auth_card.body_layout.addWidget(self._gate_net_badge)

        self._gate_net_reason = QLabel("", auth_card)
        self._gate_net_reason.setProperty("role", "caption")
        self._gate_net_reason.setWordWrap(True)
        auth_card.body_layout.addWidget(self._gate_net_reason)

        auth_card.body_layout.addWidget(HorizontalDivider(auth_card))

        self._auth_tabs = QTabWidget(auth_card)

        # Login Tab
        login_tab = QWidget(self._auth_tabs)
        l_form = QFormLayout(login_tab)
        l_form.setContentsMargins(8, 12, 8, 8)
        l_form.setSpacing(10)

        self._login_user_edit = QLineEdit(login_tab)
        self._login_user_edit.setPlaceholderText("Enter username")
        l_form.addRow("Username", self._login_user_edit)

        self._login_pass_edit = QLineEdit(login_tab)
        self._login_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._login_pass_edit.setPlaceholderText("Enter password")
        self._login_pass_edit.returnPressed.connect(self.login_from_form)
        l_form.addRow("Password", self._login_pass_edit)

        self._login_btn = make_button(
            text="Sign In to Network Chat",
            variant="primary",
            on_click=self.login_from_form,
            parent=login_tab,
        )
        l_form.addRow("", self._login_btn)
        self._auth_tabs.addTab(login_tab, "Sign In")

        # Register Tab
        reg_tab = QWidget(self._auth_tabs)
        r_form = QFormLayout(reg_tab)
        r_form.setContentsMargins(8, 12, 8, 8)
        r_form.setSpacing(10)

        self._reg_user_edit = QLineEdit(reg_tab)
        self._reg_user_edit.setPlaceholderText("Choose a unique username (e.g. arshvir)")
        r_form.addRow("Username", self._reg_user_edit)

        self._reg_name_edit = QLineEdit(reg_tab)
        self._reg_name_edit.setPlaceholderText("Optional display name")
        r_form.addRow("Display Name", self._reg_name_edit)

        self._reg_pass_edit = QLineEdit(reg_tab)
        self._reg_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._reg_pass_edit.setPlaceholderText("At least 4 characters")
        r_form.addRow("Password", self._reg_pass_edit)

        self._reg_btn = make_button(
            text="Create Account & Join",
            variant="pastel",
            on_click=self.register_from_form,
            parent=reg_tab,
        )
        r_form.addRow("", self._reg_btn)
        self._auth_tabs.addTab(reg_tab, "Register")

        auth_card.body_layout.addWidget(self._auth_tabs)

        # Same-Network Condition Settings
        cond_box = QFrame(auth_card)
        cond_lay = QVBoxLayout(cond_box)
        cond_lay.setContentsMargins(0, 4, 0, 0)
        cond_lay.setSpacing(8)

        self._require_lan_chk = QCheckBox("Require same-network private LAN IP", cond_box)
        self._require_lan_chk.setChecked(self.policy.require_private_lan)
        self._require_lan_chk.stateChanged.connect(self._on_condition_controls_changed)
        cond_lay.addWidget(self._require_lan_chk)

        c_row = QHBoxLayout()
        c_row.setSpacing(8)
        self._subnet_edit = QLineEdit(cond_box)
        self._subnet_edit.setPlaceholderText("Subnet prefix (e.g. 192.168.1)")
        self._subnet_edit.setText(self.policy.allowed_subnet_prefix)
        self._subnet_edit.textChanged.connect(self._on_condition_controls_changed)
        c_row.addWidget(self._subnet_edit, 1)

        self._passphrase_edit = QLineEdit(cond_box)
        self._passphrase_edit.setPlaceholderText("Network key (optional)")
        self._passphrase_edit.setText(self.policy.network_passphrase)
        self._passphrase_edit.textChanged.connect(self._on_condition_controls_changed)
        c_row.addWidget(self._passphrase_edit, 1)
        cond_lay.addLayout(c_row)

        auth_card.body_layout.addWidget(cond_box)
        gate_layout.addWidget(auth_card)
        outer.addWidget(self._auth_gate_view, 1)

        # ==============================================================
        # View B: 3-Pane LAN Chat Studio (Rooms & Members | Thread | Info)
        # ==============================================================
        self._chat_studio_view = QWidget(central)
        studio_row = QHBoxLayout(self._chat_studio_view)
        studio_row.setContentsMargins(0, 0, 0, 0)
        studio_row.setSpacing(0)

        # Left Pane: Groups & Direct Members
        left_pane = QFrame(self._chat_studio_view)
        left_pane.setProperty("surface", "sidebar")
        left_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_pane.setFixedWidth(250)

        l_layout = QVBoxLayout(left_pane)
        l_layout.setContentsMargins(14, 14, 14, 14)
        l_layout.setSpacing(10)

        l_layout.addWidget(SectionHeader("Groups & Chats", parent=left_pane))
        self._rooms_list = QListWidget(left_pane)
        self._rooms_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._rooms_list.currentRowChanged.connect(self._on_room_selected)
        l_layout.addWidget(self._rooms_list, 1)

        l_layout.addWidget(HorizontalDivider(left_pane))
        l_layout.addWidget(SectionHeader("Network Members", parent=left_pane))

        self._members_list = QListWidget(left_pane)
        self._members_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._members_list.itemClicked.connect(self._on_member_clicked_for_dm)
        l_layout.addWidget(self._members_list, 1)

        studio_row.addWidget(left_pane)

        # Center Pane: Active Conversation Stream + Text / Voice / File Composer
        center_pane = QWidget(self._chat_studio_view)
        c_layout = QVBoxLayout(center_pane)
        c_layout.setContentsMargins(18, 14, 18, 14)
        c_layout.setSpacing(10)

        # Room Header Strip
        room_hdr_card = CardFrame(padding=12, spacing=4, parent=center_pane)
        self._room_title_lbl = QLabel("Select a chat or group", room_hdr_card)
        self._room_title_lbl.setProperty("role", "heading")
        room_hdr_card.body_layout.addWidget(self._room_title_lbl)

        self._room_members_lbl = QLabel("Same-network messages, voice notes & files", room_hdr_card)
        self._room_members_lbl.setProperty("role", "caption")
        room_hdr_card.body_layout.addWidget(self._room_members_lbl)
        c_layout.addWidget(room_hdr_card)

        # Scrollable Message Feed
        self._feed_scroll = QScrollArea(center_pane)
        self._feed_scroll.setWidgetResizable(True)
        self._feed_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._feed_container = QWidget(self._feed_scroll)
        self._feed_layout = QVBoxLayout(self._feed_container)
        self._feed_layout.setContentsMargins(4, 6, 4, 8)
        self._feed_layout.setSpacing(8)
        self._feed_layout.addStretch(1)
        self._feed_scroll.setWidget(self._feed_container)
        c_layout.addWidget(self._feed_scroll, 1)

        # Composer Bar: File Attach + Voice Clip + Message Input + Send
        composer_card = CardFrame(padding=10, spacing=8, parent=center_pane)
        comp_row = QHBoxLayout()
        comp_row.setSpacing(6)

        self._attach_file_btn = make_button(
            text="File",
            variant="ghost",
            tooltip="Share a file with this chat or group",
            on_click=self.attach_file_prompt,
            parent=composer_card,
        )
        self._attach_file_btn.setStyleSheet("padding: 5px 9px; font-size: 12px;")
        comp_row.addWidget(self._attach_file_btn)

        self._voice_btn = make_button(
            text="Voice Clip",
            variant="pastel",
            tooltip="Record / send a local voice message clip",
            on_click=self.send_voice_clip_action,
            parent=composer_card,
        )
        self._voice_btn.setStyleSheet("padding: 5px 10px; font-size: 12px;")
        comp_row.addWidget(self._voice_btn)

        self._msg_input = QLineEdit(composer_card)
        self._msg_input.setPlaceholderText("Write a message to your network...")
        self._msg_input.returnPressed.connect(self.send_text_from_composer)
        comp_row.addWidget(self._msg_input, 1)

        self._send_btn = make_button(
            text="Send",
            variant="primary",
            on_click=self.send_text_from_composer,
            parent=composer_card,
        )
        self._send_btn.setStyleSheet("padding: 5px 14px; font-size: 12px;")
        comp_row.addWidget(self._send_btn)

        composer_card.body_layout.addLayout(comp_row)
        c_layout.addWidget(composer_card)

        studio_row.addWidget(center_pane, 1)

        # Right Pane: Network Condition & Peer Relay Status
        right_pane = QFrame(self._chat_studio_view)
        right_pane.setProperty("surface", "inspector")
        right_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_pane.setFixedWidth(265)

        r_layout = QVBoxLayout(right_pane)
        r_layout.setContentsMargins(14, 14, 14, 14)
        r_layout.setSpacing(10)

        r_layout.addWidget(SectionHeader("Network Condition", parent=right_pane))
        r_layout.addWidget(HorizontalDivider(right_pane))

        self._side_net_badge = StatusBadge("LAN Active", variant="sage", parent=right_pane)
        r_layout.addWidget(self._side_net_badge)

        self._side_net_detail = QLabel("", right_pane)
        self._side_net_detail.setProperty("role", "caption")
        self._side_net_detail.setWordWrap(True)
        r_layout.addWidget(self._side_net_detail)

        r_layout.addWidget(HorizontalDivider(right_pane))
        r_layout.addWidget(SectionHeader("LAN Peer Push", parent=right_pane))

        self._peer_ip_edit = QLineEdit(right_pane)
        self._peer_ip_edit.setPlaceholderText("Peer LAN IP (optional)")
        r_layout.addWidget(self._peer_ip_edit)

        self._peer_port_edit = QLineEdit(right_pane)
        self._peer_port_edit.setPlaceholderText("Peer Relay Port (e.g. 45829)")
        r_layout.addWidget(self._peer_port_edit)

        self._relay_info_lbl = QLabel("", right_pane)
        self._relay_info_lbl.setProperty("role", "muted")
        self._relay_info_lbl.setWordWrap(True)
        r_layout.addWidget(self._relay_info_lbl)

        r_layout.addStretch(1)
        studio_row.addWidget(right_pane)

        self._chat_studio_view.setVisible(False)
        outer.addWidget(self._chat_studio_view, 1)

        # ==============================================================
        # Bottom Status Bar
        # ==============================================================
        footer_bar = QFrame(central)
        footer_bar.setProperty("surface", "footer-bar")
        footer_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_bar.setFixedHeight(34)

        f_row = QHBoxLayout(footer_bar)
        f_row.setContentsMargins(16, 4, 16, 4)
        f_row.setSpacing(10)

        self._footer_badge = StatusBadge("Signed Out", variant="lavender", parent=footer_bar)
        f_row.addWidget(self._footer_badge)

        self._footer_lbl = QLabel("Local-only same-network chat & sharing", footer_bar)
        self._footer_lbl.setProperty("role", "muted")
        f_row.addWidget(self._footer_lbl)

        f_row.addStretch(1)
        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(self.create_group_prompt)

    # ------------------------------------------------------------------
    # Network Condition Evaluation & Enforcement
    # ------------------------------------------------------------------

    def _on_condition_controls_changed(self) -> None:
        self.policy.require_private_lan = self._require_lan_chk.isChecked()
        self.policy.allowed_subnet_prefix = self._subnet_edit.text().strip()
        self.policy.network_passphrase = self._passphrase_edit.text().strip()
        self.settings.set("allowed_subnet_prefix", self.policy.allowed_subnet_prefix)
        self.settings.set("network_passphrase", self.policy.network_passphrase)
        self._refresh_network_condition_ui()

    def _refresh_network_condition_ui(self) -> None:
        status = self.policy.evaluate(
            peer_ip=self._peer_ip_filter,
            session_passphrase=self._session_passphrase,
        )
        ips_str = ", ".join(status.local_ips) if status.local_ips else "None"
        relay_port = self._relay.bound_port

        if status.allowed:
            self._gate_net_badge.set_status(f"Same Network OK ({status.subnet_tag})", variant="sage")
            self._side_net_badge.set_status(f"Same Network ({status.subnet_tag})", variant="sage")
        else:
            self._gate_net_badge.set_status("Network Condition Blocked", variant="blush")
            self._side_net_badge.set_status("Network Condition Blocked", variant="blush")

        detail = f"{status.reason}\nLocal IPs: {ips_str}"
        self._gate_net_reason.setText(detail)
        self._side_net_detail.setText(detail)
        self._relay_info_lbl.setText(
            f"Local relay port: {relay_port if relay_port else 'Standby'}  •  Zero cloud servers"
        )

        # Enable or gate sending controls when signed in
        can_send = (self._current_user is not None) and status.allowed
        for w in (self._msg_input, self._send_btn, self._attach_file_btn, self._voice_btn):
            w.setEnabled(can_send)

    # ------------------------------------------------------------------
    # Authentication (Username + Password)
    # ------------------------------------------------------------------

    def register_user(self, username: str, password: str, display_name: str = "") -> ChatUserAccount | None:
        net_status = self.policy.evaluate(session_passphrase=self._session_passphrase)
        try:
            acct = self.store.register_user(
                username=username,
                password=password,
                display_name=display_name,
                subnet_tag=net_status.subnet_tag,
            )
        except ValueError as exc:
            show_error_dialog(self, "Registration Error", str(exc))
            return None

        self._activate_user_session(acct)
        self.notify(
            "Account Created",
            f"Registered '{acct.username}' on {net_status.subnet_tag}.",
            level=NotificationLevel.SUCCESS,
        )
        return acct

    def login_user(self, username: str, password: str) -> ChatUserAccount | None:
        acct = self.store.authenticate_user(username, password)
        if acct is None:
            show_error_dialog(
                self,
                "Sign In Failed",
                "Invalid username or password for this local network.",
            )
            return None

        self._activate_user_session(acct)
        self.set_status_message(f"Signed in as {acct.username}", timeout_ms=2000)
        return acct

    def login_from_form(self) -> None:
        self.login_user(self._login_user_edit.text(), self._login_pass_edit.text())
        self._login_pass_edit.clear()

    def register_from_form(self) -> None:
        self.register_user(
            self._reg_user_edit.text(),
            self._reg_pass_edit.text(),
            self._reg_name_edit.text(),
        )
        self._reg_pass_edit.clear()

    def _activate_user_session(self, acct: ChatUserAccount) -> None:
        self._current_user = acct
        self._sync_auth_state()
        self._rebuild_members_and_rooms()
        self._refresh_network_condition_ui()

    def sign_out(self) -> None:
        self._current_user = None
        self._current_room = None
        self._sync_auth_state()
        self._refresh_network_condition_ui()

    def _sync_auth_state(self) -> None:
        signed_in = self._current_user is not None
        self._auth_gate_view.setVisible(not signed_in)
        self._chat_studio_view.setVisible(signed_in)
        self._new_group_btn.setEnabled(signed_in)
        self._logout_btn.setEnabled(signed_in)

        if not signed_in:
            self.setWindowTitle(self.meta.display_name)
            self._footer_badge.set_status("Signed Out", variant="lavender")
            self._footer_lbl.setText("Username + password required for same-network chat")
        else:
            assert self._current_user is not None
            self.setWindowTitle(f"{self._current_user.username} — {self.meta.display_name}")
            self._footer_badge.set_status(f"@{self._current_user.username}", variant="sage")
            self._footer_lbl.setText("Connected to local network chat & file sharing")

    # ------------------------------------------------------------------
    # Direct Chats, Groups & Messages
    # ------------------------------------------------------------------

    def _rebuild_members_and_rooms(self) -> None:
        if self._current_user is None:
            return
        me = self._current_user.username

        self._members_list.blockSignals(True)
        try:
            self._members_list.clear()
            for usr in self.store.list_users():
                if usr.username == me:
                    continue
                item = QListWidgetItem(f"● {usr.display_name} (@{usr.username})")
                item.setData(Qt.ItemDataRole.UserRole, usr.username)
                self._members_list.addItem(item)
        finally:
            self._members_list.blockSignals(False)

        rooms = self.store.list_rooms_for_user(me)
        # Ensure at least a default "LAN General" group exists for easy same-network group chat
        if not any(r.is_group for r in rooms):
            all_names = [u.username for u in self.store.list_users()]
            self.store.create_group_room("LAN General", me, all_names)
            rooms = self.store.list_rooms_for_user(me)

        self._rooms_list.blockSignals(True)
        try:
            self._rooms_list.clear()
            selected_row = 0
            for idx, room in enumerate(rooms):
                prefix = "👥 Group: " if room.is_group else "💬 DM: "
                item = QListWidgetItem(f"{prefix}{room.name}")
                item.setData(Qt.ItemDataRole.UserRole, room.room_id)
                self._rooms_list.addItem(item)
                if self._current_room is not None and room.room_id == self._current_room.room_id:
                    selected_row = idx

            if rooms:
                self._rooms_list.setCurrentRow(selected_row)
                self._select_room(rooms[selected_row])
        finally:
            self._rooms_list.blockSignals(False)

    def _on_room_selected(self, row: int) -> None:
        if self._current_user is None or row < 0:
            return
        rooms = self.store.list_rooms_for_user(self._current_user.username)
        if 0 <= row < len(rooms):
            self._select_room(rooms[row])

    def _on_member_clicked_for_dm(self, item: QListWidgetItem) -> None:
        if self._current_user is None:
            return
        peer_user = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(peer_user, str) or not peer_user:
            return
        dm_room = self.store.get_or_create_direct_room(self._current_user.username, peer_user)
        self._current_room = dm_room
        self._rebuild_members_and_rooms()

    def _select_room(self, room: ChatRoomInfo) -> None:
        self._current_room = room
        kind_tag = "Group Chat" if room.is_group else "Direct Chat"
        self._room_title_lbl.setText(f"{room.name}  ({kind_tag})")
        self._room_members_lbl.setText(f"Members: {', '.join(room.members)}")
        self._render_room_messages()

    def create_group_prompt(self) -> None:
        if self._current_user is None:
            return
        all_users = [u.username for u in self.store.list_users()]
        dlg = CreateGroupDialog(
            available_usernames=all_users,
            current_username=self._current_user.username,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.group_name:
            self.create_group(dlg.group_name, dlg.selected_members)

    def create_group(self, group_name: str, members: list[str]) -> ChatRoomInfo | None:
        if self._current_user is None:
            return None
        try:
            room = self.store.create_group_room(
                group_name=group_name,
                creator_username=self._current_user.username,
                member_usernames=members,
            )
        except ValueError as exc:
            show_error_dialog(self, "Could Not Create Group", str(exc))
            return None

        self._current_room = room
        self._rebuild_members_and_rooms()
        return room

    def _ensure_network_allowed(self) -> bool:
        status = self.policy.evaluate(
            peer_ip=self._peer_ip_filter,
            session_passphrase=self._session_passphrase,
        )
        if not status.allowed:
            show_error_dialog(self, "Same-Network Condition Not Met", status.reason)
            return False
        return True

    def send_text_message(self, text: str) -> ChatMessageRecord | None:
        if self._current_user is None or self._current_room is None:
            return None
        if not self._ensure_network_allowed():
            return None
        net_status = self.policy.evaluate(session_passphrase=self._session_passphrase)
        try:
            rec = self.store.send_text_message(
                room_id=self._current_room.room_id,
                sender_username=self._current_user.username,
                text=text,
                subnet_tag=net_status.subnet_tag,
            )
        except ValueError as exc:
            show_error_dialog(self, "Empty Message", str(exc))
            return None

        self._push_to_peer_if_configured(rec)
        self._render_room_messages()
        return rec

    def send_text_from_composer(self) -> None:
        txt = self._msg_input.text().strip()
        if not txt:
            return
        if self.send_text_message(txt) is not None:
            self._msg_input.clear()

    def attach_file_prompt(self) -> None:
        if self._current_user is None or self._current_room is None:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select File to Share on Network",
            str(Path.home() / "Documents"),
            "All Files (*.*)",
        )
        if selected:
            self.send_file_attachment(selected, caption=self._msg_input.text().strip())
            self._msg_input.clear()

    def send_file_attachment(self, file_path: str | Path, caption: str = "") -> ChatMessageRecord | None:
        if self._current_user is None or self._current_room is None:
            return None
        if not self._ensure_network_allowed():
            return None
        net_status = self.policy.evaluate(session_passphrase=self._session_passphrase)
        try:
            rec = self.store.send_file_message(
                room_id=self._current_room.room_id,
                sender_username=self._current_user.username,
                source_file=file_path,
                caption=caption,
                subnet_tag=net_status.subnet_tag,
            )
        except FileOperationError as exc:
            show_error_dialog(self, "File Share Failed", str(exc))
            return None

        self._push_to_peer_if_configured(rec)
        self._render_room_messages()
        return rec

    def send_voice_clip_action(self) -> ChatMessageRecord | None:
        """Capture/send a voice message clip (or select an audio file) to the active room."""
        if self._current_user is None or self._current_room is None:
            return None
        if not self._ensure_network_allowed():
            return None

        choice, ok = QInputDialog.getItem(
            self,
            "Send Voice Clip",
            "Select voice input mode:",
            ["Record Quick Voice Note (2.0s)", "Choose Audio File (.wav / .mp3 / .m4a)..."],
            0,
            False,
        )
        if not ok:
            return None

        net_status = self.policy.evaluate(session_passphrase=self._session_passphrase)

        if choice.startswith("Choose Audio"):
            audio_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Voice Audio File",
                str(Path.home() / "Music"),
                "Audio Files (*.wav *.mp3 *.m4a *.ogg)",
            )
            if not audio_path:
                return None
            p = Path(audio_path)
            rec = self.store.send_voice_message(
                room_id=self._current_room.room_id,
                sender_username=self._current_user.username,
                audio_bytes=p.read_bytes(),
                file_ext=p.suffix or ".wav",
                duration_sec=2.0,
                subnet_tag=net_status.subnet_tag,
            )
        else:
            wav_bytes = build_sample_voice_wav_bytes(duration_sec=2.0)
            rec = self.store.send_voice_message(
                room_id=self._current_room.room_id,
                sender_username=self._current_user.username,
                audio_bytes=wav_bytes,
                file_ext=".wav",
                duration_sec=2.0,
                subnet_tag=net_status.subnet_tag,
            )

        self._push_to_peer_if_configured(rec)
        self._render_room_messages()
        return rec

    def _push_to_peer_if_configured(self, rec: ChatMessageRecord) -> None:
        peer_ip = self._peer_ip_edit.text().strip()
        peer_port_txt = self._peer_port_edit.text().strip()
        if peer_ip and peer_port_txt.isdigit():
            LocalLanChatRelay.push_message_to_peer(peer_ip, int(peer_port_txt), rec)

    def _play_voice_attachment(self, file_path_str: str) -> None:
        p = Path(file_path_str)
        if not p.exists():
            return
        if self._player is not None:
            self._player.setSource(QUrl.fromLocalFile(str(p)))
            self._player.play()
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _open_file_attachment(self, file_path_str: str) -> None:
        p = Path(file_path_str)
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _render_room_messages(self) -> None:
        # Clear previous message widgets while keeping bottom stretch
        while self._feed_layout.count() > 1:
            item = self._feed_layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()

        if self._current_room is None:
            return

        me = self._current_user.username if self._current_user else ""
        messages = self.store.list_messages(self._current_room.room_id)

        for msg in messages:
            is_mine = msg.sender_username == me
            row_wrap = QWidget(self._feed_container)
            row_lay = QHBoxLayout(row_wrap)
            row_lay.setContentsMargins(4, 2, 4, 2)

            bubble = CardFrame(padding=10, spacing=5, parent=row_wrap)
            bubble.setMaximumWidth(520)

            meta_lbl = QLabel(
                f"@{msg.sender_username}  •  {msg.sent_at}",
                bubble,
            )
            meta_lbl.setProperty("role", "caption")
            bubble.body_layout.addWidget(meta_lbl)

            if msg.kind == "voice":
                v_row = QHBoxLayout()
                v_row.setSpacing(8)
                v_lbl = QLabel(f"🎤 {msg.content}", bubble)
                v_lbl.setWordWrap(True)
                v_row.addWidget(v_lbl, 1)

                play_btn = make_button(
                    text="Play",
                    variant="pastel",
                    on_click=lambda _=False, ap=msg.attachment_path: self._play_voice_attachment(ap),
                    parent=bubble,
                )
                play_btn.setStyleSheet("padding: 3px 9px; font-size: 11px;")
                v_row.addWidget(play_btn)
                bubble.body_layout.addLayout(v_row)

            elif msg.kind == "file":
                f_row = QHBoxLayout()
                f_row.setSpacing(8)
                f_lbl = QLabel(f"📎 {msg.attachment_name or msg.content}", bubble)
                f_lbl.setWordWrap(True)
                f_row.addWidget(f_lbl, 1)

                open_f_btn = make_button(
                    text="Open File",
                    variant="pastel",
                    on_click=lambda _=False, ap=msg.attachment_path: self._open_file_attachment(ap),
                    parent=bubble,
                )
                open_f_btn.setStyleSheet("padding: 3px 9px; font-size: 11px;")
                f_row.addWidget(open_f_btn)
                bubble.body_layout.addLayout(f_row)

            else:
                body_lbl = QLabel(msg.content, bubble)
                body_lbl.setWordWrap(True)
                bubble.body_layout.addWidget(body_lbl)

            if is_mine:
                row_lay.addStretch(1)
                row_lay.addWidget(bubble)
            else:
                row_lay.addWidget(bubble)
                row_lay.addStretch(1)

            self._feed_layout.insertWidget(self._feed_layout.count() - 1, row_wrap)

        self._feed_scroll.verticalScrollBar().setValue(
            self._feed_scroll.verticalScrollBar().maximum()
        )

    def can_close(self) -> bool:
        self._relay.stop()
        self.store.close()
        return True


def main() -> int:
    """Application entry point for SimpleChat.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleChatWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())