#!/usr/bin/env python3
"""
Video Player  ·  PyQt6 + Qt Multimedia
========================================
功能：
  · 打开单个/多个视频文件，或整个文件夹
  · 目录树逐层展开/折叠，双击播放
  · 播放/暂停/上一个/下一个
  · 进度条点击/拖拽定位
  · 播放速度选择 (0.25× – 3.0×)
  · 音量调节
  · 快捷键 P 显示/隐藏播放列表侧边栏
  · 全屏切换（F键 或 双击视频画面）
  · 书签：M键 添加，单击跳转，右键删除
  · 自动记忆并恢复每个视频的最后播放位置
  · 历史记录：所有加载过的文件/文件夹，双击恢复
  · 视频区域右键菜单：速度/循环/全屏/书签等设置

快捷键
  Space       播放 / 暂停
  M           添加书签
  P           显示 / 隐藏 侧边栏
  F           全屏切换
  Escape      退出全屏
  ← / →       快退/快进 5 秒
  Shift+← / → 快退/快进 30 秒
  ↑ / ↓       音量 +5 / -5
  N           下一个
  B           上一个
  [ / ]       上一个/下一个书签
  Alt+1..9    跳转到第 1–9 个书签
  Ctrl+F / /  搜索播放列表
  Ctrl+S      截图
  Ctrl+O      打开文件
  Ctrl+Shift+O 打开文件夹

全局快捷键（需安装 keyboard 包）
  Ctrl+Alt+Space  播放 / 暂停
  Ctrl+Alt+→      下一个
  Ctrl+Alt+←      上一个
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel,
    QFileDialog, QTreeWidget, QTreeWidgetItem,
    QSplitter, QComboBox, QStatusBar, QStyle,
    QTabWidget, QListWidget, QListWidgetItem,
    QInputDialog, QMenu, QAbstractItemView,
    QLineEdit, QSystemTrayIcon,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import Qt, QTimer, QUrl, QPoint, QPointF, QObject, pyqtSignal
from PyQt6.QtGui import (
    QKeySequence, QShortcut, QPalette, QColor, QPainter, QPolygonF,
    QPixmap, QScreen, QDragEnterEvent, QDropEvent, QIcon,
)

# 可选依赖：keyboard（全局快捷键）
try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False


VIDEO_EXT = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".vob",
    ".rmvb", ".rm", ".3gp", ".ogv", ".f4v", ".divx",
}

LOOP_LABELS = ["不循环", "单曲循环", "列表循环"]   # loop mode 0/1/2
_PINNED_ROLE = Qt.ItemDataRole.UserRole + 1        # 自定义角色：节点是否钉住


def _normpath(p: str) -> str:
    """规范化路径：统一使用反斜杠，确保路径匹配一致"""
    return os.path.normpath(p) if p else p


# ── 数据持久化 ─────────────────────────────────────────────────────────────────
class DataManager:
    """书签、播放位置、会话、历史记录 → ~/.videoplayer_data.json"""

    _HISTORY_MAX = 50

    def __init__(self):
        self._path = Path.home() / ".videoplayer_data.json"
        self._data = self._load()
        self._prune_missing()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("data file is not a JSON object")
        except Exception:
            data = {}
        # 确保所有键存在（兼容旧版本/损坏的数据文件）
        data.setdefault("last_session", None)
        data.setdefault("last_dir", "")
        data.setdefault("prefs", {"volume": 80, "speed": "1.0x"})
        for section in ("last_positions", "bookmarks", "video_settings",
                        "watched", "folder_states"):
            if not isinstance(data.get(section), dict):
                data[section] = {}
        if not isinstance(data.get("history"), list):
            data["history"] = []
        # 旧版本数据路径未做归一化，读取时统一迁移
        for section in ("last_positions", "bookmarks", "video_settings", "watched"):
            data[section] = {_normpath(k): v for k, v in data[section].items()}
        return data

    def _prune_missing(self):
        """启动时清理已不存在（被移动/删除）的视频记录，防止数据文件无限膨胀"""
        removed = False
        for section in ("last_positions", "bookmarks", "video_settings", "watched"):
            kept = {k: v for k, v in self._data[section].items() if os.path.isfile(k)}
            if len(kept) != len(self._data[section]):
                self._data[section] = kept
                removed = True
        if removed:
            self._save()

    # ── 上次打开目录 ──────────────────────────────────────────────────────────
    def get_last_dir(self) -> str:
        return self._data.get("last_dir", "")

    def set_last_dir(self, path: str):
        self._data["last_dir"] = path
        self._save()

    # ── 音量 / 倍速偏好 ───────────────────────────────────────────────────────
    def get_prefs(self) -> dict:
        return self._data.get("prefs", {"volume": 80, "speed": "1.0x"})

    def set_prefs(self, volume: int, speed: str):
        self._data["prefs"] = {"volume": volume, "speed": speed}
        self._save()

    def _save(self):
        try:
            tmp_path = self._path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._path)
        except Exception as e:
            # 静默吞掉会让用户在磁盘满/文件被占用时无声丢数据
            print(f"[VideoPlayer] 数据保存失败: {e}", file=sys.stderr)

    # ── 最后会话 ──────────────────────────────────────────────────────────────
    def get_last_session(self) -> dict | None:
        return self._data.get("last_session")

    def set_last_session(self, session_type: str, path_or_paths, index: int = 0):
        self._data["last_session"] = {
            "type":  session_type,
            "path":  path_or_paths if session_type == "folder" else None,
            "paths": path_or_paths if session_type == "files"  else None,
            "index": index,
        }
        self._save()

    def set_session_sources(self, sources: list[dict], index: int = 0):
        """新格式：以来源列表保存会话，支持多文件夹/文件混合"""
        self._data["last_session"] = {"sources": sources, "index": index}
        self._save()

    def append_session_source(self, source: dict):
        """向当前会话追加一个来源；若会话为旧格式则先转换"""
        s = self._data.get("last_session") or {}
        if "sources" not in s:
            # 将旧格式转换为新格式
            old_sources: list[dict] = []
            if s.get("type") == "folder" and s.get("path"):
                old_sources = [{"type": "folder", "path": s["path"]}]
            elif s.get("type") == "files" and s.get("paths"):
                old_sources = [{"type": "files", "paths": s["paths"]}]
            s = {"sources": old_sources, "index": s.get("index", 0)}
        s["sources"].append(source)
        self._data["last_session"] = s
        self._save()

    def update_session_index(self, index: int):
        s = self._data.get("last_session")
        if s:
            s["index"] = index
            self._save()

    # ── 历史记录 ──────────────────────────────────────────────────────────────
    def get_history(self) -> list[dict]:
        return list(self._data.get("history", []))

    def add_to_history(self, entry: dict):
        """
        entry 格式:
          {"type":"folder","path":..., "label":..., "count":..., "time":...}
          {"type":"files", "paths":[...], "label":..., "count":..., "time":...}
        相同路径/文件列表时移到最前（更新时间）。
        """
        history: list = self._data.setdefault("history", [])

        # 去重：同 type+path/paths 的项先移除
        def key(e):
            if e["type"] == "folder":
                return ("folder", e.get("path", ""))
            return ("files", tuple(sorted(e.get("paths", []))))

        new_key = key(entry)
        # 保留被覆盖条目的钉住状态
        old_pinned = next((e.get("pinned", False) for e in history if key(e) == new_key), False)
        history[:] = [e for e in history if key(e) != new_key]
        if old_pinned:
            entry["pinned"] = True
        if entry.get("pinned", False):
            history.insert(0, entry)           # 钉住 → 放钉住区最前
        else:
            insert_pos = sum(1 for e in history if e.get("pinned", False))
            history.insert(insert_pos, entry)  # 普通 → 接在钉住区之后

        if len(history) > self._HISTORY_MAX:
            history[:] = history[:self._HISTORY_MAX]
        self._save()

    def remove_from_history(self, idx: int):
        history = self._data.get("history", [])
        if 0 <= idx < len(history):
            history.pop(idx)
            self._save()

    def clear_history(self):
        self._data["history"] = []
        self._save()

    def move_history_to_top(self, idx: int):
        history = self._data.get("history", [])
        if 0 <= idx < len(history):
            history.insert(0, history.pop(idx))
            self._save()

    def toggle_history_pin(self, idx: int):
        """切换钉住状态；钉住条目始终排在未钉住条目之前"""
        history = self._data.get("history", [])
        if not (0 <= idx < len(history)):
            return
        entry = history.pop(idx)
        entry["pinned"] = not entry.get("pinned", False)
        if entry["pinned"]:
            history.insert(0, entry)
        else:
            insert_pos = sum(1 for e in history if e.get("pinned", False))
            history.insert(insert_pos, entry)
        self._save()

    def replace_history(self, entries: list[dict]):
        """整体替换历史列表（用于拖拽排序后同步）"""
        self._data["history"] = entries
        self._save()

    # ── 播放位置 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _split_pos(value) -> tuple[int, int]:
        """进度记录兼容两种格式：旧版 int（只有位置）、新版 dict（位置+时长）"""
        if isinstance(value, dict):
            return value.get("pos", 0), value.get("dur", 0)
        return value, 0

    def get_position(self, path: str) -> int:
        return self._split_pos(self._data["last_positions"].get(_normpath(path), 0))[0]

    def get_duration(self, path: str) -> int:
        return self._split_pos(self._data["last_positions"].get(_normpath(path), 0))[1]

    def set_position(self, path: str, pos: int, duration: int):
        path = _normpath(path)
        if pos < 5_000:
            self._data["last_positions"].pop(path, None)
        elif duration > 0 and pos > duration - 60_000:
            self._data["last_positions"].pop(path, None)
        else:
            self._data["last_positions"][path] = {"pos": pos, "dur": duration}
        # 播放超过 90% 自动标记为已看
        if duration > 0 and pos > duration * 0.9:
            self.set_watched(path, True)
        self._save()

    # ── 已看/未看 ─────────────────────────────────────────────────────────────
    def is_watched(self, path: str) -> bool:
        return self._data.get("watched", {}).get(_normpath(path), False)

    def set_watched(self, path: str, watched: bool):
        path = _normpath(path)
        w = self._data.setdefault("watched", {})
        if watched:
            w[path] = True
        else:
            w.pop(path, None)
        self._save()

    def get_continue_watching(self) -> list[dict]:
        """返回有播放进度但未标记为已看的视频列表"""
        positions = self._data.get("last_positions", {})
        watched = self._data.get("watched", {})
        result = []
        for path, value in positions.items():
            if watched.get(path, False):
                continue
            pos, dur = self._split_pos(value)
            if pos > 0:
                result.append({"path": path, "position": pos, "duration": dur})
        return result

    # ── 书签 ──────────────────────────────────────────────────────────────────
    def get_bookmarks(self, path: str) -> list[dict]:
        return list(self._data["bookmarks"].get(_normpath(path), []))

    def add_bookmark(self, path: str, time_ms: int, label: str) -> bool:
        bms = self._data["bookmarks"].setdefault(_normpath(path), [])
        if any(abs(b["time"] - time_ms) < 1_000 for b in bms):
            return False
        bms.append({"time": time_ms, "label": label})
        bms.sort(key=lambda x: x["time"])
        self._save()
        return True

    def remove_bookmark(self, path: str, time_ms: int):
        key = _normpath(path)
        bms = self._data["bookmarks"].get(key, [])
        self._data["bookmarks"][key] = [b for b in bms if b["time"] != time_ms]
        self._save()

    def clear_bookmarks(self, path: str):
        self._data["bookmarks"].pop(_normpath(path), None)
        self._save()

    # ── 每个视频独立设置 ─────────────────────────────────────────────────────────
    def get_video_settings(self, path: str) -> dict:
        return self._data.get("video_settings", {}).get(path, {})

    def set_video_settings(self, path: str, speed: str = None, volume: int = None):
        vs = self._data.setdefault("video_settings", {})
        settings = vs.get(path, {})
        if speed is not None:
            settings["speed"] = speed
        if volume is not None:
            settings["volume"] = volume
        if settings:
            vs[path] = settings
        else:
            vs.pop(path, None)
        self._save()

    # ── 文件夹展开状态 ─────────────────────────────────────────────────────────
    def get_folder_state(self, folder_path: str) -> dict:
        return self._data.get("folder_states", {}).get(folder_path, {})

    def set_folder_state(self, folder_path: str, subfolder: str, expanded: bool):
        fs = self._data.setdefault("folder_states", {})
        state = fs.get(folder_path, {})
        state[subfolder] = expanded
        fs[folder_path] = state
        self._save()


# ── 支持拖拽排序的播放列表树 ──────────────────────────────────────────────────
class PlaylistTree(QTreeWidget):
    """仅允许顶层节点（文件夹/独立文件）相互拖拽排序"""

    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 与历史列表相同：移动完成后 rowsMoved 触发 order_changed
        self.model().rowsMoved.connect(self.order_changed)

    def startDrag(self, supported_actions):
        """只允许顶层节点发起拖拽，子节点不可拖"""
        if self.currentItem() and self.currentItem().parent() is None:
            super().startDrag(supported_actions)

    def dragMoveEvent(self, event):
        target = self.itemAt(event.position().toPoint())
        # 子节点旁边不允许拖入
        if target is not None and target.parent() is not None:
            event.ignore()
            return
        # 先让 Qt 计算 drop indicator 位置，再检查是否为 OnItem
        super().dragMoveEvent(event)
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.OnItem:
            event.ignore()

    def dropEvent(self, event):
        """兜底：确保 OnItem 拖入（嵌套）绝对不会发生"""
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.OnItem:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        if target is not None and target.parent() is not None:
            event.ignore()
            return
        super().dropEvent(event)


# ── 可点击进度条 ──────────────────────────────────────────────────────────────
class ClickSlider(QSlider):
    """点击/拖拽均可直接定位；在 groove 上方绘制书签标记"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._markers: list[float] = []   # 0.0–1.0 相对位置

    def set_markers(self, ratios: list[float]):
        self._markers = ratios
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._markers:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        handle_half = 6
        usable_w = max(1, self.width() - 2 * handle_half)
        cy = self.height() // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5a623"))
        for ratio in self._markers:
            x = handle_half + ratio * usable_w
            tri = QPolygonF([
                QPointF(x - 4, cy - 9),
                QPointF(x + 4, cy - 9),
                QPointF(x,     cy - 3),
            ])
            painter.drawPolygon(tri)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._seek_to(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def _seek_to(self, x: float):
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        value = int(self.minimum() + ratio * (self.maximum() - self.minimum()))
        self.setValue(value)
        self.sliderMoved.emit(value)


# ── OSD 叠层提示 ─────────────────────────────────────────────────────────────
class OSDLabel(QLabel):
    """半透明黑底白字叠层标签，用于全屏时显示操作反馈"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "background: rgba(0,0,0,180); color: #ffffff;"
            " padding: 8px 18px; border-radius: 6px; font-size: 15px;"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()


# ── 自定义视频控件 ────────────────────────────────────────────────────────────
class VideoWidget(QVideoWidget):
    """双击切换全屏；转发鼠标/拖放事件给主窗口"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            w = self.window()
            w.showNormal() if w.isFullScreen() else w.showFullScreen()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        w = self.window()
        if hasattr(w, '_on_video_mouse_move'):
            w._on_video_mouse_move(event.position().toPoint())
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        self.window().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.window().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.window().dropEvent(event)


# ── 主窗口 ───────────────────────────────────────────────────────────────────
class PlayerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Player")
        self.resize(1280, 720)
        self.setMinimumSize(800, 500)
        self.setAcceptDrops(True)

        self.playlist: list[str] = []
        self.current_index = -1
        self._stored_sidebar_w = 280
        self._pending_resume = 0
        self._autosave_counter = 0
        self._loop_mode = 0          # 0=不循环 1=单曲 2=列表
        self._controls_visible = True
        self._was_sidebar_visible = True

        self._dm = DataManager()

        # 每视频设置延迟合并写入，避免拖音量条时高频写盘
        self._pending_vs: dict[str, dict] = {}
        self._vs_timer = QTimer(singleShot=True, interval=800)
        self._vs_timer.timeout.connect(self._flush_video_settings)

        # 控制栏自动隐藏倒计时（仅全屏播放时启用）
        self._controls_timer = QTimer(singleShot=True, interval=3000)
        self._controls_timer.timeout.connect(self._hide_controls)

        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.audio_out.setVolume(0.8)

        self._build_ui()
        self._build_shortcuts()
        self._wire_signals()

        # 恢复上次的音量和倍速
        prefs = self._dm.get_prefs()
        self.vol_slider.setValue(prefs.get("volume", 80))
        self.speed_box.setCurrentText(prefs.get("speed", "1.0x"))

        self._clock = QTimer(interval=400)
        self._clock.timeout.connect(self._tick)
        self._clock.start()

        QTimer.singleShot(0, self._restore_session)

        # 系统托盘
        self._tray_mgr = TrayManager(self)

    # ── 构建界面 ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_menubar()
        root = QWidget()
        self.setCentralWidget(root)
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        rl.addWidget(self.splitter)

        # ── 侧边栏（三个 Tab）───────────────────────────────────────────────
        self._sidebar = QWidget()
        self._sidebar.setMinimumWidth(0)
        sl = QVBoxLayout(self._sidebar)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane  { border: none; background: #1c1c1c; }
            QTabBar::tab {
                background: #252525; color: #888;
                padding: 6px 14px; border: none; font-size: 12px;
            }
            QTabBar::tab:selected      { background: #1c1c1c; color: #ccc;
                                         border-bottom: 2px solid #0d6efd; }
            QTabBar::tab:hover:!selected { background: #2e2e2e; color: #aaa; }
        """)

        # ── Tab 0: 播放列表 ──────────────────────────────────────────────────
        pl_root = QWidget()
        pl_vl = QVBoxLayout(pl_root)
        pl_vl.setContentsMargins(0, 0, 0, 0)
        pl_vl.setSpacing(0)

        # 搜索框（默认隐藏）
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("搜索播放列表...")
        self._search_box.setFixedHeight(28)
        self._search_box.setStyleSheet(
            "QLineEdit { background:#2a2a2a; color:#ccc; border:1px solid #444;"
            " border-radius:4px; padding:2px 8px; font-size:12px; }"
        )
        self._search_box.textChanged.connect(self._filter_tree)
        self._search_box.hide()
        pl_vl.addWidget(self._search_box)

        self.tree = PlaylistTree()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(18)
        self.tree.setStyleSheet(self._tree_css())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pl_vl.addWidget(self.tree)

        # 播放列表底部工具栏
        pl_bar = self._make_toolbar()
        btn_add_files  = self._tbtn("+ 文件")
        btn_add_folder = self._tbtn("+ 文件夹")
        btn_clear_pl   = self._tbtn("清空")
        btn_add_files.clicked.connect(self.add_files_to_playlist)
        btn_add_folder.clicked.connect(self.add_folder_to_playlist)
        btn_clear_pl.clicked.connect(self.clear_playlist)
        pl_bar.layout().addWidget(btn_add_files)
        pl_bar.layout().addWidget(btn_add_folder)
        pl_bar.layout().addStretch()
        pl_bar.layout().addWidget(btn_clear_pl)
        pl_vl.addWidget(pl_bar)

        self.tab_widget.addTab(pl_root, "播放列表")

        # ── Tab 1: 继续观看 ────────────────────────────────────────────────────
        cw_root = QWidget()
        cw_vl = QVBoxLayout(cw_root)
        cw_vl.setContentsMargins(0, 0, 0, 0)
        cw_vl.setSpacing(0)

        self.cw_list = QListWidget()
        self.cw_list.setStyleSheet(self._list_css())
        self.cw_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cw_list.customContextMenuRequested.connect(self._cw_context_menu)
        self.cw_list.itemDoubleClicked.connect(self._on_cw_dbl_click)
        cw_vl.addWidget(self.cw_list)

        cw_bar = self._make_toolbar()
        btn_clear_cw = self._tbtn("清空全部")
        btn_clear_cw.clicked.connect(self._clear_continue_watching)
        cw_bar.layout().addStretch()
        cw_bar.layout().addWidget(btn_clear_cw)
        cw_vl.addWidget(cw_bar)
        self.tab_widget.addTab(cw_root, "继续观看")

        # ── Tab 2: 书签 ──────────────────────────────────────────────────────
        bm_root = QWidget()
        bm_vl = QVBoxLayout(bm_root)
        bm_vl.setContentsMargins(0, 0, 0, 0)
        bm_vl.setSpacing(0)

        self.bm_list = QListWidget()
        self.bm_list.setStyleSheet(self._list_css())
        self.bm_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bm_list.customContextMenuRequested.connect(self._bm_context_menu)
        bm_vl.addWidget(self.bm_list)

        bm_bar = self._make_toolbar()
        btn_add_bm   = self._tbtn("+ 添加  [M]")
        btn_clear_bm = self._tbtn("清空全部")
        btn_export_bm = self._tbtn("导出")
        btn_add_bm.clicked.connect(self.add_bookmark)
        btn_clear_bm.clicked.connect(self.clear_bookmarks)
        btn_export_bm.clicked.connect(lambda: self._export_bookmarks())
        bm_bar.layout().addWidget(btn_add_bm)
        bm_bar.layout().addWidget(btn_clear_bm)
        bm_bar.layout().addWidget(btn_export_bm)
        bm_bar.layout().addStretch()
        bm_vl.addWidget(bm_bar)
        self.tab_widget.addTab(bm_root, "书签")

        # ── Tab 3: 历史记录 ──────────────────────────────────────────────────
        hist_root = QWidget()
        hist_vl = QVBoxLayout(hist_root)
        hist_vl.setContentsMargins(0, 0, 0, 0)
        hist_vl.setSpacing(0)

        self.hist_list = QListWidget()
        self.hist_list.setStyleSheet(self._list_css())
        self.hist_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.hist_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.hist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.hist_list.customContextMenuRequested.connect(self._hist_context_menu)
        hist_vl.addWidget(self.hist_list)

        hist_bar = self._make_toolbar()
        btn_clear_hist = self._tbtn("清空历史")
        btn_clear_hist.clicked.connect(self._clear_history)
        hist_bar.layout().addStretch()
        hist_bar.layout().addWidget(btn_clear_hist)
        hist_vl.addWidget(hist_bar)
        self.tab_widget.addTab(hist_root, "历史")

        sl.addWidget(self.tab_widget)

        # ── 内容区 ──────────────────────────────────────────────────────────
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self.video = VideoWidget()
        self.video.setStyleSheet("background:#000000;")
        self.video.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.player.setVideoOutput(self.video)
        cl.addWidget(self.video, stretch=1)
        cl.addWidget(self._build_controls())

        # OSD 叠层提示
        self._osd = OSDLabel(content)
        self._osd.raise_()
        self._osd_timer = QTimer(singleShot=True)
        self._osd_timer.timeout.connect(self._osd.hide)

        self.splitter.addWidget(self._sidebar)
        self.splitter.addWidget(content)
        self.splitter.setSizes([280, 1000])

        # ── 状态栏 ──────────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet("background:#0f0f0f;color:#555;font-size:11px;")
        self.setStatusBar(self._status_bar)
        self._update_status_hint()

    # ── 菜单栏 ────────────────────────────────────────────────────────────────
    def _build_menubar(self):
        """主菜单：让新用户能发现“打开文件/文件夹”等入口（不与已有 QShortcut 重复绑键）"""
        mb = self.menuBar()

        m_file = mb.addMenu("文件(&F)")
        act_open = m_file.addAction("打开文件…")
        act_open.triggered.connect(self.open_file)
        act_open_dir = m_file.addAction("打开文件夹…")
        act_open_dir.triggered.connect(self.open_folder)
        m_file.addSeparator()
        act_clear_pl = m_file.addAction("清空播放列表")
        act_clear_pl.triggered.connect(self.clear_playlist)
        m_file.addSeparator()
        act_quit = m_file.addAction("退出")
        act_quit.triggered.connect(self.close)

        m_play = mb.addMenu("播放(&P)")
        act_toggle = m_play.addAction("播放 / 暂停")
        act_toggle.triggered.connect(self.toggle_play)
        act_prev = m_play.addAction("上一个")
        act_prev.triggered.connect(self.play_prev)
        act_next = m_play.addAction("下一个")
        act_next.triggered.connect(self.play_next)
        m_play.addSeparator()
        act_bm = m_play.addAction("添加书签")
        act_bm.triggered.connect(self.add_bookmark)

        m_view = mb.addMenu("视图(&V)")
        act_side = m_view.addAction("显示 / 隐藏侧边栏")
        act_side.triggered.connect(self.toggle_sidebar)
        act_fs = m_view.addAction("全屏切换")
        act_fs.triggered.connect(self.toggle_fullscreen)
        m_view.addSeparator()
        act_shot = m_view.addAction("截图")
        act_shot.triggered.connect(self._take_screenshot)

    def _build_controls(self):
        w = QWidget()
        w.setFixedHeight(70)
        w.setStyleSheet("background:#181818;")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(14, 6, 14, 8)
        vl.setSpacing(6)

        # 进度条行
        pr = QHBoxLayout()
        pr.setSpacing(8)
        self.lbl_pos = QLabel("0:00:00")
        self.lbl_pos.setStyleSheet("color:#888;font-size:12px;min-width:55px;")
        pr.addWidget(self.lbl_pos)
        self.seek_slider = ClickSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 10_000)
        self.seek_slider.setStyleSheet(self._slider_css("#0d6efd"))
        pr.addWidget(self.seek_slider, stretch=1)
        self.lbl_dur = QLabel("0:00:00")
        self.lbl_dur.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_dur.setStyleSheet("color:#888;font-size:12px;min-width:55px;")
        pr.addWidget(self.lbl_dur)
        vl.addLayout(pr)

        # 按钮行
        br = QHBoxLayout()
        br.setSpacing(6)

        self.btn_prev = self._ibtn("⏮", 34)
        self.btn_play = self._ibtn("▶", 44, primary=True)
        self.btn_next = self._ibtn("⏭", 34)
        br.addWidget(self.btn_prev)
        br.addWidget(self.btn_play)
        br.addWidget(self.btn_next)

        # 循环模式按钮
        self.btn_loop = self._tbtn("循环:关", checkable=False)
        self.btn_loop.setFixedWidth(62)
        self.btn_loop.clicked.connect(self._cycle_loop)
        br.addSpacing(6)
        br.addWidget(self.btn_loop)
        br.addSpacing(10)

        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("color:#888;font-size:13px;")
        br.addWidget(vol_icon)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setFixedWidth(82)
        self.vol_slider.setStyleSheet(self._slider_css("#888888"))
        br.addWidget(self.vol_slider)

        br.addStretch()

        spd_lbl = QLabel("速度:")
        spd_lbl.setStyleSheet("color:#888;font-size:12px;")
        br.addWidget(spd_lbl)
        self.speed_box = QComboBox()
        self.speed_box.addItems(
            ["0.25x", "0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x", "3.0x"]
        )
        self.speed_box.setCurrentText("1.0x")
        self.speed_box.setFixedWidth(78)
        self.speed_box.setStyleSheet(self._combo_css())
        br.addWidget(self.speed_box)

        vl.addLayout(br)
        return w

    # ── 快捷键 ────────────────────────────────────────────────────────────────
    def _build_shortcuts(self):
        def sc(key, fn):
            QShortcut(QKeySequence(key), self, activated=fn)

        sc("Space",       self.toggle_play)
        sc("M",           self.add_bookmark)
        sc("P",           self.toggle_sidebar)
        sc("F",           self.toggle_fullscreen)
        sc("Escape",      self._exit_fullscreen)
        sc("N",           self.play_next)
        sc("B",           self.play_prev)
        sc("Right",       lambda: self._seek_rel(5_000))
        sc("Left",        lambda: self._seek_rel(-5_000))
        sc("Shift+Right", lambda: self._seek_rel(30_000))
        sc("Shift+Left",  lambda: self._seek_rel(-30_000))
        sc("Up",          lambda: self._vol_delta(5))
        sc("Down",        lambda: self._vol_delta(-5))
        # 书签跳转
        sc("[",           lambda: self._jump_adjacent_bookmark(-1))
        sc("]",           lambda: self._jump_adjacent_bookmark(1))
        for i in range(1, 10):
            sc(f"Alt+{i}", lambda n=i: self._jump_to_bookmark(n - 1))
        # 搜索 / 截图 / 打开
        sc("Ctrl+F",      self._toggle_search)
        sc("/",           self._toggle_search)
        sc("Ctrl+S",      self._take_screenshot)
        sc("Ctrl+O",      self.open_file)
        sc("Ctrl+Shift+O", self.open_folder)

    # ── 信号连接 ──────────────────────────────────────────────────────────────
    def _wire_signals(self):
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_next.clicked.connect(self.play_next)
        self.seek_slider.sliderMoved.connect(self._seek_abs)
        # 音量变化统一由 _on_vol_changed 处理（设音量 + 保存），不再重复连接
        self.speed_box.currentTextChanged.connect(self._on_speed_changed)
        self.vol_slider.valueChanged.connect(self._on_vol_changed)
        self.tree.itemExpanded.connect(self._on_tree_expand)
        self.tree.itemCollapsed.connect(self._on_tree_collapse)
        self.tree.itemDoubleClicked.connect(self._on_tree_dbl_click)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.tree.order_changed.connect(self._rebuild_after_reorder)
        self.bm_list.itemClicked.connect(self._on_bm_click)
        self.hist_list.itemDoubleClicked.connect(self._on_hist_dbl_click)
        self.hist_list.model().rowsMoved.connect(self._on_hist_reordered)
        self.video.customContextMenuRequested.connect(self._show_video_menu)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)

    # ── 打开文件 / 文件夹 ──────────────────────────────────────────────────────
    def open_file(self):
        last_dir = self._dm.get_last_dir()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "打开视频文件", last_dir,
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm "
            "*.m4v *.mpg *.mpeg *.ts *.mts *.m2ts *.vob *.3gp *.ogv)",
        )
        if paths:
            self._dm.set_last_dir(os.path.dirname(paths[0]))
            self._load_file_paths(paths)

    def open_folder(self):
        last_dir = self._dm.get_last_dir()
        folder = QFileDialog.getExistingDirectory(self, "打开文件夹", last_dir)
        if folder:
            self._dm.set_last_dir(folder)
            self._load_folder_path(folder)

    def _load_file_paths(self, paths: list[str], restore_index: int = 0):
        self._save_current_position()
        self.playlist.clear()
        self.tree.clear()
        file_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        for p in paths:
            p = _normpath(p)
            if p in self.playlist:
                continue
            self.playlist.append(p)
            it = QTreeWidgetItem([os.path.basename(p)])
            it.setIcon(0, file_ico)
            it.setData(0, Qt.ItemDataRole.UserRole, p)
            self.tree.addTopLevelItem(it)
        self._dm.set_session_sources([{"type": "files", "paths": [_normpath(p) for p in paths]}], restore_index)
        # 历史记录
        n = len(paths)
        label = os.path.basename(paths[0]) if n == 1 else f"{os.path.basename(paths[0])} 等{n}个文件"
        self._dm.add_to_history({
            "type": "files", "paths": [_normpath(p) for p in paths], "label": label,
            "count": n, "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        self._refresh_hist_list()
        self._refresh_tree_watched_state()
        self._play(restore_index)

    def _load_folder_path(self, folder: str, restore_index: int = 0, auto_play: bool = False):
        folder = _normpath(folder)
        self._save_current_position()
        self.playlist.clear()
        self.tree.clear()
        dir_ico  = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        root_it = QTreeWidgetItem([os.path.basename(folder)])
        root_it.setIcon(0, dir_ico)
        root_it.setData(0, Qt.ItemDataRole.UserRole, folder)   # 存文件夹路径
        self.tree.addTopLevelItem(root_it)
        self._scan_dir(folder, root_it, dir_ico, file_ico)
        root_it.setExpanded(True)
        self._dm.set_session_sources([{"type": "folder", "path": folder}], restore_index)
        # 历史记录
        n = len(self.playlist)
        self._dm.add_to_history({
            "type": "folder", "path": folder,
            "label": os.path.basename(folder),
            "count": n, "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        self._refresh_hist_list()
        self._refresh_tree_watched_state()
        if self.playlist:
            if auto_play:
                self._play(restore_index)
            else:
                self._prepare(restore_index)   # 建树但不自动播放，等待用户点击

    # ── 追加到播放列表 ────────────────────────────────────────────────────────
    def add_files_to_playlist(self):
        """向当前播放列表追加文件（不清空）"""
        last_dir = self._dm.get_last_dir()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加视频文件", last_dir,
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm "
            "*.m4v *.mpg *.mpeg *.ts *.mts *.m2ts *.vob *.3gp *.ogv)",
        )
        if not paths:
            return
        self._dm.set_last_dir(os.path.dirname(paths[0]))
        self._append_file_paths(paths)

    def add_folder_to_playlist(self):
        """向当前播放列表追加文件夹（不清空）"""
        last_dir = self._dm.get_last_dir()
        folder = QFileDialog.getExistingDirectory(self, "添加文件夹", last_dir)
        if not folder:
            return
        self._dm.set_last_dir(folder)
        self._append_folder_path(folder)

    def _append_file_paths(self, paths: list[str]):
        file_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        added_paths: list[str] = []
        for p in paths:
            p = _normpath(p)
            if p in self.playlist:
                continue
            self.playlist.append(p)
            it = QTreeWidgetItem([os.path.basename(p)])
            it.setIcon(0, file_ico)
            it.setData(0, Qt.ItemDataRole.UserRole, p)
            self.tree.addTopLevelItem(it)
            added_paths.append(p)
        if added_paths:
            self._dm.append_session_source({"type": "files", "paths": added_paths})

    def _append_folder_path(self, folder: str):
        dir_ico  = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        folder_it = QTreeWidgetItem([os.path.basename(folder)])
        folder_it.setIcon(0, dir_ico)
        folder_it.setData(0, Qt.ItemDataRole.UserRole, folder)  # 存文件夹路径
        before = len(self.playlist)
        self._scan_dir_append(folder, folder_it, dir_ico, file_ico, _depth=0)
        # 只要文件夹包含视频文件就显示（即使全部已在 playlist 中）
        if folder_it.childCount() > 0:
            self.tree.addTopLevelItem(folder_it)
            folder_it.setExpanded(True)
            if len(self.playlist) > before:
                self._dm.append_session_source({"type": "folder", "path": folder})

    def _scan_dir_append(self, path, parent_item, dir_ico, file_ico, _depth=0):
        """递归扫描并追加；已在 playlist 的文件只建树节点、不重复加入列表"""
        if _depth > 64:
            return
        try:
            entries = sorted(
                os.scandir(path),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except OSError:
            return
        for e in entries:
            if e.is_dir() and not e.is_symlink():
                it = QTreeWidgetItem([e.name])
                it.setIcon(0, dir_ico)
                it.setData(0, Qt.ItemDataRole.UserRole, None)
                parent_item.addChild(it)
                self._scan_dir_append(e.path, it, dir_ico, file_ico, _depth + 1)
                if it.childCount() == 0:
                    parent_item.removeChild(it)
            elif e.is_file():
                if os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                    p = _normpath(e.path)   # 与 _scan_dir 保持一致的路径格式
                    it = QTreeWidgetItem([e.name])
                    it.setIcon(0, file_ico)
                    it.setData(0, Qt.ItemDataRole.UserRole, p)
                    parent_item.addChild(it)
                    if p not in self.playlist:
                        self.playlist.append(p)

    def clear_playlist(self):
        """清空播放列表和树"""
        self._save_current_position()
        self.player.stop()
        self.player.setSource(QUrl())
        self.playlist.clear()
        self.current_index = -1
        self.tree.clear()
        self.seek_slider.setValue(0)
        self.lbl_pos.setText("0:00:00")
        self.lbl_dur.setText("0:00:00")
        self.setWindowTitle("Video Player")
        self._dm.set_session_sources([], 0)
        self._refresh_bm_list()

    def _refresh_seek_markers(self):
        if self.current_index < 0:
            self.seek_slider.set_markers([])
            return
        dur = self.player.duration()
        if dur <= 0:
            self.seek_slider.set_markers([])
            return
        path = self.playlist[self.current_index]
        ratios = [
            bm["time"] / dur
            for bm in self._dm.get_bookmarks(path)
            if 0 < bm["time"] < dur
        ]
        self.seek_slider.set_markers(ratios)

    def _restore_session(self):
        s = self._dm.get_last_session()
        if not s:
            self._refresh_hist_list()
            return
        idx = s.get("index", 0)
        # 新格式：来源列表
        if "sources" in s:
            sources = s["sources"]
            if sources:
                self._restore_from_sources(sources, idx)
                return
            self._refresh_hist_list()
            return
        # 旧格式（向后兼容）
        if s.get("type") == "folder":
            folder = s.get("path") or ""
            if os.path.isdir(folder):
                self._load_folder_path(folder, restore_index=idx, auto_play=True)
                return
        elif s.get("type") == "files":
            paths = [p for p in (s.get("paths") or []) if os.path.isfile(p)]
            if paths:
                self._load_file_paths(paths, restore_index=min(idx, len(paths) - 1))
                return
        self._refresh_hist_list()

    def _restore_from_sources(self, sources: list[dict], index: int):
        """按来源列表重建播放列表树，不写入会话、不自动播放"""
        self.playlist.clear()
        self.tree.clear()
        dir_ico  = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        for src in sources:
            is_pinned = src.get("pinned", False)
            pin_prefix = "📌 " if is_pinned else ""
            if src.get("type") == "folder":
                folder = _normpath(src.get("path") or "")
                if not os.path.isdir(folder):
                    continue
                display = src.get("name") or os.path.basename(folder)
                root_it = QTreeWidgetItem([pin_prefix + display])
                root_it.setIcon(0, dir_ico)
                root_it.setData(0, Qt.ItemDataRole.UserRole, folder)  # 存文件夹路径
                root_it.setData(0, _PINNED_ROLE, is_pinned)
                self.tree.addTopLevelItem(root_it)
                self._scan_dir(folder, root_it, dir_ico, file_ico)
                root_it.setExpanded(src.get("expanded", False))
            elif src.get("type") == "files":
                for p in (src.get("paths") or []):
                    p = _normpath(p)
                    if os.path.isfile(p) and p not in self.playlist:
                        self.playlist.append(p)
                        it = QTreeWidgetItem([pin_prefix + os.path.basename(p)])
                        it.setIcon(0, file_ico)
                        it.setData(0, Qt.ItemDataRole.UserRole, p)
                        it.setData(0, _PINNED_ROLE, is_pinned)
                        self.tree.addTopLevelItem(it)
        self._refresh_hist_list()
        self._refresh_tree_watched_state()
        if self.playlist:
            self._prepare(min(index, len(self.playlist) - 1))

    def _scan_dir(self, path, parent_item, dir_ico, file_ico, _depth=0):
        if _depth > 64:
            return
        try:
            entries = sorted(
                os.scandir(path),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except OSError:
            return
        for e in entries:
            if e.is_dir() and not e.is_symlink():
                it = QTreeWidgetItem([e.name])
                it.setIcon(0, dir_ico)
                it.setData(0, Qt.ItemDataRole.UserRole, None)
                parent_item.addChild(it)
                self._scan_dir(e.path, it, dir_ico, file_ico, _depth + 1)
                if it.childCount() == 0:
                    parent_item.removeChild(it)
            elif e.is_file():
                if os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                    it = QTreeWidgetItem([e.name])
                    it.setIcon(0, file_ico)
                    it.setData(0, Qt.ItemDataRole.UserRole, _normpath(e.path))
                    parent_item.addChild(it)
                    self.playlist.append(_normpath(e.path))

    # ── 播放控制 ──────────────────────────────────────────────────────────────
    def _play(self, idx: int):
        if not (0 <= idx < len(self.playlist)):
            return
        self._save_current_position()
        self.current_index = idx
        self._dm.update_session_index(idx)
        path = self.playlist[idx]
        self._pending_resume = self._dm.get_position(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.setWindowTitle(f"Video Player  —  {os.path.basename(path)}")
        self._highlight_current()
        self._refresh_bm_list()
        self._refresh_continue_watching()
        # 恢复该视频的独立设置
        vs = self._dm.get_video_settings(path)
        if vs.get("speed"):
            self.speed_box.setCurrentText(vs["speed"])
        if vs.get("volume") is not None:
            self.vol_slider.setValue(vs["volume"])

    def _prepare(self, idx: int):
        """仅设置当前项并高亮，不加载媒体、不播放（用于文件夹加载）"""
        if not (0 <= idx < len(self.playlist)):
            return
        self.current_index = idx
        self._dm.update_session_index(idx)
        path = self.playlist[idx]
        self.setWindowTitle(f"Video Player  —  {os.path.basename(path)}  [已就绪]")
        self._highlight_current()
        self._refresh_bm_list()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.current_index < 0 and self.playlist:
            self._play(0)
        elif self.current_index >= 0 and self.player.source().isEmpty():
            # 文件夹加载后未自动播放，首次点击播放键时加载并播放
            self._play(self.current_index)
        else:
            self.player.play()

    def play_next(self):
        if self._loop_mode == 2 and self.current_index >= len(self.playlist) - 1:
            self._play(0)
        else:
            self._play(self.current_index + 1)

    def play_prev(self):
        if self._loop_mode == 2 and self.current_index <= 0 and self.playlist:
            self._play(len(self.playlist) - 1)
        else:
            self._play(self.current_index - 1)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if self._was_sidebar_visible:
                self._restore_sidebar()
        else:
            self._was_sidebar_visible = self._is_sidebar_visible()
            if self._was_sidebar_visible:
                self._hide_sidebar()
            self.showFullScreen()

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _is_sidebar_visible(self) -> bool:
        return self.splitter.sizes()[0] > 0

    def _hide_sidebar(self):
        sizes = self.splitter.sizes()
        self._stored_sidebar_w = sizes[0]
        self.splitter.setSizes([0, sizes[0] + sizes[1]])

    def _restore_sidebar(self):
        sizes = self.splitter.sizes()
        total = sizes[0] + sizes[1]
        self.splitter.setSizes([self._stored_sidebar_w, total - self._stored_sidebar_w])

    # ── 循环模式 ──────────────────────────────────────────────────────────────
    def _cycle_loop(self):
        self._loop_mode = (self._loop_mode + 1) % 3
        self._update_loop_btn()
        self._update_status_hint()
        self._show_osd(f"🔁 {LOOP_LABELS[self._loop_mode]}")

    def set_loop_mode(self, mode: int):
        self._loop_mode = mode
        self._update_loop_btn()
        self._update_status_hint()

    def _update_loop_btn(self):
        labels = ["循环:关", "单曲循环", "列表循环"]
        self.btn_loop.setText(labels[self._loop_mode])
        active = self._loop_mode > 0
        self.btn_loop.setStyleSheet(
            self._tbtn_css(highlight=active)
        )

    # ── 书签 ──────────────────────────────────────────────────────────────────
    def add_bookmark(self):
        if self.current_index < 0:
            return
        path = self.playlist[self.current_index]
        pos  = self.player.position()
        default_label = f"书签 {self._fmt_time(pos)}"
        label, ok = QInputDialog.getText(
            self, "添加书签", "书签名称（可留空）:", text=default_label
        )
        if not ok:
            return
        label = label.strip() or default_label
        if self._dm.add_bookmark(path, pos, label):
            self._refresh_bm_list()
            self.tab_widget.setCurrentIndex(1)

    def clear_bookmarks(self):
        if self.current_index < 0:
            return
        self._dm.clear_bookmarks(self.playlist[self.current_index])
        self._refresh_bm_list()

    def _refresh_bm_list(self):
        self.bm_list.clear()
        if self.current_index < 0:
            return
        for bm in self._dm.get_bookmarks(self.playlist[self.current_index]):
            item = QListWidgetItem(f"  {self._fmt_time(bm['time'])}   {bm['label']}")
            item.setData(Qt.ItemDataRole.UserRole, bm["time"])
            self.bm_list.addItem(item)
        self._refresh_seek_markers()

    def _on_bm_click(self, item: QListWidgetItem):
        time_ms = item.data(Qt.ItemDataRole.UserRole)
        if time_ms is not None:
            self.player.setPosition(time_ms)
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.player.play()

    def _bm_context_menu(self, pos: QPoint):
        item = self.bm_list.itemAt(pos)
        if not item:
            return
        menu = self._make_menu()
        act_jump   = menu.addAction("▶  跳转到此处")
        act_rename = menu.addAction("✎  重命名")
        menu.addSeparator()
        act_delete = menu.addAction("✕  删除书签")
        action = menu.exec(self.bm_list.mapToGlobal(pos))
        if action == act_jump:
            self._on_bm_click(item)
        elif action == act_rename:
            self._bm_rename(item)
        elif action == act_delete:
            self._bm_delete(item)

    def _bm_rename(self, item: QListWidgetItem):
        if self.current_index < 0:
            return
        time_ms   = item.data(Qt.ItemDataRole.UserRole)
        path      = self.playlist[self.current_index]
        old_label = next(
            (b["label"] for b in self._dm.get_bookmarks(path) if b["time"] == time_ms), ""
        )
        new_label, ok = QInputDialog.getText(self, "重命名书签", "新名称:", text=old_label)
        if ok and new_label.strip():
            self._dm.remove_bookmark(path, time_ms)
            self._dm.add_bookmark(path, time_ms, new_label.strip())
            self._refresh_bm_list()

    def _bm_delete(self, item: QListWidgetItem):
        if self.current_index < 0:
            return
        self._dm.remove_bookmark(
            self.playlist[self.current_index],
            item.data(Qt.ItemDataRole.UserRole),
        )
        self._refresh_bm_list()

    # ── 继续观看 ──────────────────────────────────────────────────────────────
    def _refresh_continue_watching(self):
        """刷新继续观看列表"""
        from PyQt6.QtGui import QFont
        self.cw_list.clear()
        items = self._dm.get_continue_watching()
        for entry in items:
            path = entry["path"]
            pos = entry["position"]
            name = os.path.basename(path)
            widget = QWidget()
            hl = QHBoxLayout(widget)
            hl.setContentsMargins(8, 4, 8, 4)
            hl.setSpacing(8)
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet("color:#ccc;font-size:13px;font-family:'Microsoft YaHei','SimSun',sans-serif;")
            lbl_name.setFont(QFont("Microsoft YaHei", 13))
            lbl_name.setMinimumWidth(0)
            hl.addWidget(lbl_name, stretch=1)
            bar = QSlider(Qt.Orientation.Horizontal)
            bar.setRange(0, 100)
            bar.setFixedWidth(80)
            bar.setEnabled(False)
            pct = self._estimate_progress(pos, entry.get("duration", 0))
            bar.setValue(pct)
            bar.setStyleSheet(self._slider_css("#0d6efd"))
            hl.addWidget(bar)
            lbl_pct = QLabel(f"{pct}%")
            lbl_pct.setStyleSheet("color:#888;font-size:12px;min-width:35px;font-family:'Microsoft YaHei','SimSun',sans-serif;")
            lbl_pct.setFont(QFont("Microsoft YaHei", 12))
            lbl_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hl.addWidget(lbl_pct)
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, path)
            list_item.setSizeHint(widget.sizeHint())
            self.cw_list.addItem(list_item)
            self.cw_list.setItemWidget(list_item, widget)

    @staticmethod
    def _estimate_progress(pos: int, duration: int) -> int:
        """播放进度百分比：优先用真实时长；旧数据缺时长时按位置粗略估算"""
        if duration > 0:
            return min(99, max(1, int(pos / duration * 100)))
        minutes = pos / 60_000
        return min(95, max(1, int(minutes / 2 * 100)))

    def _on_cw_dbl_click(self, item: QListWidgetItem):
        """双击继续观看列表项 → 找到对应视频并续播"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        idx = self._find_playlist_index(path)
        if idx >= 0:
            self._play(idx)
        else:
            self._load_file_paths([path])

    def _cw_context_menu(self, pos: QPoint):
        item = self.cw_list.itemAt(pos)
        menu = self._make_menu()
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            act_play = menu.addAction("▶  继续播放")
            act_play.triggered.connect(lambda: self._on_cw_dbl_click(item))
            act_mark = menu.addAction("✓  标记为已看")
            act_mark.triggered.connect(lambda: self._cw_mark_watched(path))
            menu.addSeparator()
            act_remove = menu.addAction("✕  从此列表移除")
            act_remove.triggered.connect(lambda: self._cw_remove(path))
        else:
            menu.addAction("（暂无继续观看的视频）").setEnabled(False)
        menu.exec(self.cw_list.mapToGlobal(pos))

    def _cw_mark_watched(self, path: str):
        self._dm.set_watched(path, True)
        self._dm.set_position(path, 0, 0)
        self._refresh_continue_watching()
        self._refresh_tree_watched_state()

    def _cw_remove(self, path: str):
        self._dm.set_position(path, 0, 0)
        self._refresh_continue_watching()

    def _clear_continue_watching(self):
        items = self._dm.get_continue_watching()
        for entry in items:
            self._dm.set_position(entry["path"], 0, 0)
        self._refresh_continue_watching()

    # ── 播放列表树中标记已看状态 ────────────────────────────────────────────────
    def _refresh_tree_watched_state(self):
        """刷新播放列表树中所有文件的已看/未看样式"""
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._update_item_watched_style(root.child(i))

    def _update_item_watched_style(self, item: QTreeWidgetItem):
        """递归更新单个项及其子项的已看样式"""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            is_watched = self._dm.is_watched(path)
            name = os.path.basename(path)
            if is_watched:
                display = f"✓ {name}"
                item.setForeground(0, QColor("#666666"))
            else:
                display = name
                item.setForeground(0, QColor("#cccccc"))
            is_pinned = bool(item.data(0, _PINNED_ROLE))
            pin_prefix = "📌 " if is_pinned else ""
            item.setText(0, pin_prefix + display)
        for i in range(item.childCount()):
            self._update_item_watched_style(item.child(i))

    # ── OSD 叠层提示 ──────────────────────────────────────────────────────────
    def _show_osd(self, text: str):
        self._osd.setText(text)
        self._osd.adjustSize()
        content = self._osd.parent()
        x = content.width() - self._osd.width() - 20
        y = content.height() - self._osd.height() - 80
        self._osd.move(max(10, x), max(10, y))
        self._osd.show()
        self._osd.raise_()
        self._osd_timer.start(1500)

    # ── 书签跳转 ──────────────────────────────────────────────────────────────
    def _jump_adjacent_bookmark(self, direction: int):
        """跳到上一个/下一个书签（direction: -1 或 +1）"""
        if self.current_index < 0:
            return
        path = self.playlist[self.current_index]
        bms = self._dm.get_bookmarks(path)
        if not bms:
            return
        pos = self.player.position()
        if direction > 0:
            target = next((b for b in bms if b["time"] > pos + 500), bms[-1])
        else:
            target = next(
                (b for b in reversed(bms) if b["time"] < pos - 500), bms[0]
            )
        self.player.setPosition(target["time"])
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()
        self._show_osd(f"🔖 {target['label']}")

    def _jump_to_bookmark(self, index: int):
        """跳到第 index 个书签（0-based）"""
        if self.current_index < 0:
            return
        path = self.playlist[self.current_index]
        bms = self._dm.get_bookmarks(path)
        if 0 <= index < len(bms):
            bm = bms[index]
            self.player.setPosition(bm["time"])
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.player.play()
            self._show_osd(f"🔖 {bm['label']}")

    # ── 书签导出 ──────────────────────────────────────────────────────────────
    def _export_bookmarks(self):
        """导出当前视频书签为 JSON 或 SRT"""
        if self.current_index < 0:
            return
        path = self.playlist[self.current_index]
        bms = self._dm.get_bookmarks(path)
        if not bms:
            self._show_osd("没有可导出的书签")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, "导出书签", name + "_bookmarks",
            "JSON 文件 (*.json);;SRT 字幕 (*.srt)",
        )
        if not file_path:
            return
        try:
            if selected_filter.startswith("JSON"):
                data = [{"time": b["time"], "label": b["label"],
                         "time_str": self._fmt_time(b["time"])} for b in bms]
                Path(file_path).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                lines = []
                for i, bm in enumerate(bms, 1):
                    start = self._ms_to_srt_time(bm["time"])
                    end = self._ms_to_srt_time(bm["time"] + 5000)
                    lines.append(f"{i}\n{start} --> {end}\n{bm['label']}\n")
                Path(file_path).write_text("\n".join(lines), encoding="utf-8")
            self._show_osd(f"书签已导出: {os.path.basename(file_path)}")
        except Exception as e:
            self._show_osd(f"导出失败: {e}")

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        h = ms // 3_600_000
        m = (ms % 3_600_000) // 60_000
        s = (ms % 60_000) // 1000
        millis = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"

    # ── 拖放打开文件 ──────────────────────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = [u.toLocalFile() for u in event.mimeData().urls()]
        if not urls:
            return
        folders = [u for u in urls if os.path.isdir(u)]
        files = [u for u in urls if os.path.isfile(u) and
                 os.path.splitext(u)[1].lower() in VIDEO_EXT]
        if folders:
            self._dm.set_last_dir(folders[0])
            self._load_folder_path(folders[0])
        elif files:
            self._dm.set_last_dir(os.path.dirname(files[0]))
            self._load_file_paths(files)

    # ── 历史记录 ──────────────────────────────────────────────────────────────
    def _refresh_hist_list(self):
        self.hist_list.clear()
        for entry in self._dm.get_history():
            icon = "📁" if entry["type"] == "folder" else "📄"
            pin_pfx = "📌 " if entry.get("pinned", False) else ""
            cnt  = entry.get("count", 0)
            cnt_str = f"  ({cnt}个)" if cnt else ""
            time_str = entry.get("time", "")
            text = f"  {pin_pfx}{icon} {entry['label']}{cnt_str}    {time_str}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.hist_list.addItem(item)

    def _on_hist_dbl_click(self, item: QListWidgetItem):
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return
        if entry["type"] == "folder":
            folder = entry.get("path") or ""
            if os.path.isdir(folder):
                self._load_folder_path(folder)
        elif entry["type"] == "files":
            paths = [p for p in (entry.get("paths") or []) if os.path.isfile(p)]
            if paths:
                self._load_file_paths(paths)

    def _hist_context_menu(self, pos: QPoint):
        item = self.hist_list.itemAt(pos)
        menu = self._make_menu()
        act_open  = menu.addAction("▶  加载此记录") if item else None
        act_top   = menu.addAction("⬆  置顶") if item else None
        menu.addSeparator()
        if item:
            entry = item.data(Qt.ItemDataRole.UserRole) or {}
            is_pinned = entry.get("pinned", False)
            act_pin = menu.addAction("📌  取消钉住" if is_pinned else "📌  钉住")
        else:
            act_pin = None
        menu.addSeparator()
        act_del   = menu.addAction("✕  删除此条记录") if item else None
        menu.addSeparator()
        act_clear = menu.addAction("清空全部历史")
        action = menu.exec(self.hist_list.mapToGlobal(pos))
        if action == act_open and item:
            self._on_hist_dbl_click(item)
        elif action == act_top and item:
            self._hist_move_to_top(item)
        elif action == act_pin and item:
            self._hist_toggle_pin(item)
        elif action == act_del and item:
            row = self.hist_list.row(item)
            self._dm.remove_from_history(row)
            self._refresh_hist_list()
        elif action == act_clear:
            self._clear_history()

    def _clear_history(self):
        self._dm.clear_history()
        self._refresh_hist_list()

    def _hist_move_to_top(self, item: QListWidgetItem):
        """将历史条目移到最顶部（置顶）"""
        row = self.hist_list.row(item)
        if row > 0:
            self._dm.move_history_to_top(row)
            self._refresh_hist_list()

    def _hist_toggle_pin(self, item: QListWidgetItem):
        """切换历史条目的钉住状态"""
        self._dm.toggle_history_pin(self.hist_list.row(item))
        self._refresh_hist_list()

    # ── 播放列表拖拽排序 / 文件夹重命名 ──────────────────────────────────────
    def _rebuild_after_reorder(self):
        """拖拽完成后重建 playlist 顺序并保存会话"""
        current_path = (self.playlist[self.current_index]
                        if 0 <= self.current_index < len(self.playlist) else None)
        new_playlist: list[str] = []

        def collect(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path and os.path.isfile(path):
                new_playlist.append(path)
            for i in range(item.childCount()):
                collect(item.child(i))

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            collect(root.child(i))

        self.playlist = new_playlist
        if current_path and current_path in new_playlist:
            self.current_index = new_playlist.index(current_path)
        self._save_session_from_tree()

    def _save_session_from_tree(self):
        """从树结构重建来源列表并写入会话"""
        sources: list[dict] = []
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            is_pinned = bool(item.data(0, _PINNED_ROLE))
            if item.childCount() > 0:
                # 文件夹节点
                src: dict = {"type": "folder", "path": path,
                             "expanded": item.isExpanded()}
                if is_pinned:
                    src["pinned"] = True
                name = item.text(0).removeprefix("📌 ")
                if path and name != os.path.basename(path):
                    src["name"] = name   # 保存自定义名称
                sources.append(src)
            elif path and os.path.isfile(path):
                # 独立文件
                src = {"type": "files", "paths": [path]}
                if is_pinned:
                    src["pinned"] = True
                sources.append(src)
        self._dm.set_session_sources(sources, max(self.current_index, 0))

    def _tree_context_menu(self, pos: QPoint):
        item = self.tree.itemAt(pos)
        if not item:
            return
        menu = self._make_menu()
        path = item.data(0, Qt.ItemDataRole.UserRole)
        # 文件节点：显示已看/未看切换
        if path and os.path.isfile(path) and item.parent() is not None:
            is_watched = self._dm.is_watched(path)
            if is_watched:
                act_unwatch = menu.addAction("○  标记为未看")
                act_unwatch.triggered.connect(lambda: self._toggle_watched(path, False))
            else:
                act_watch = menu.addAction("✓  标记为已看")
                act_watch.triggered.connect(lambda: self._toggle_watched(path, True))
            menu.addSeparator()
            act_top  = menu.addAction("⬆  置顶")
            act_pin  = menu.addAction("📌  取消钉住" if bool(item.data(0, _PINNED_ROLE)) else "📌  钉住")
            action = menu.exec(self.tree.mapToGlobal(pos))
            if action == act_top:
                self._playlist_move_to_top(item)
            elif action == act_pin:
                self._playlist_toggle_pin(item)
            return
        # 文件夹节点：原有逻辑
        if item.parent() is not None:
            return
        is_pinned = bool(item.data(0, _PINNED_ROLE))
        act_top  = menu.addAction("⬆  置顶")
        act_pin  = menu.addAction("📌  取消钉住" if is_pinned else "📌  钉住")
        if item.childCount() > 0:
            menu.addSeparator()
            act_rename = menu.addAction("✎  重命名")
        else:
            act_rename = None
        action = menu.exec(self.tree.mapToGlobal(pos))
        if action == act_top:
            self._playlist_move_to_top(item)
        elif action == act_pin:
            self._playlist_toggle_pin(item)
        elif action == act_rename:
            self._rename_folder_item(item)

    def _toggle_watched(self, path: str, watched: bool):
        """切换视频的已看/未看状态"""
        self._dm.set_watched(path, watched)
        if watched:
            self._dm.set_position(path, 0, 0)
        self._refresh_tree_watched_state()
        self._refresh_continue_watching()

    def _rename_folder_item(self, item: QTreeWidgetItem):
        new_name, ok = QInputDialog.getText(
            self, "重命名文件夹", "显示名称:", text=item.text(0).removeprefix("📌 ")
        )
        if ok and new_name.strip():
            prefix = "📌 " if bool(item.data(0, _PINNED_ROLE)) else ""
            item.setText(0, prefix + new_name.strip())
            self._save_session_from_tree()

    def _playlist_move_to_top(self, item: QTreeWidgetItem):
        """将播放列表顶层节点移到最前"""
        root = self.tree.invisibleRootItem()
        idx = root.indexOfChild(item)
        if idx <= 0:
            return
        taken = root.takeChild(idx)
        root.insertChild(0, taken)
        self._rebuild_after_reorder()

    def _playlist_toggle_pin(self, item: QTreeWidgetItem):
        """切换播放列表顶层节点的钉住状态"""
        is_pinned = bool(item.data(0, _PINNED_ROLE))
        new_pinned = not is_pinned
        item.setData(0, _PINNED_ROLE, new_pinned)
        name = item.text(0)
        root = self.tree.invisibleRootItem()
        if new_pinned:
            if not name.startswith("📌 "):
                item.setText(0, "📌 " + name)
            idx = root.indexOfChild(item)
            taken = root.takeChild(idx)
            # 插入到已钉住节点末尾
            insert_at = 0
            for i in range(root.childCount()):
                if bool(root.child(i).data(0, _PINNED_ROLE)):
                    insert_at = i + 1
                else:
                    break
            root.insertChild(insert_at, taken)
        else:
            if name.startswith("📌 "):
                item.setText(0, name.removeprefix("📌 "))
        self._rebuild_after_reorder()

    def _on_hist_reordered(self):
        """历史列表拖拽排序后同步到 DataManager"""
        entries = [self.hist_list.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.hist_list.count())]
        self._dm.replace_history([e for e in entries if e])

    # ── 视频区域右键菜单（设置）──────────────────────────────────────────────
    def _show_video_menu(self, pos: QPoint):
        menu = self._make_menu()

        # 播放/暂停
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        menu.addAction("⏸  暂停" if playing else "▶  播放").triggered.connect(self.toggle_play)
        menu.addAction("⏮  上一个").triggered.connect(self.play_prev)
        menu.addAction("⏭  下一个").triggered.connect(self.play_next)
        menu.addSeparator()

        # 播放速度子菜单
        spd_menu = menu.addMenu("⏩  播放速度")
        spd_menu.setStyleSheet(menu.styleSheet())
        cur_speed = self.speed_box.currentText()
        for spd in ["0.25x", "0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x", "3.0x"]:
            a = spd_menu.addAction(("✓ " if spd == cur_speed else "   ") + spd)
            a.triggered.connect(lambda _, s=spd: self.speed_box.setCurrentText(s))

        # 循环模式子菜单
        loop_menu = menu.addMenu("🔁  循环模式")
        loop_menu.setStyleSheet(menu.styleSheet())
        for i, lbl in enumerate(LOOP_LABELS):
            a = loop_menu.addAction(("✓ " if i == self._loop_mode else "   ") + lbl)
            a.triggered.connect(lambda _, m=i: self.set_loop_mode(m))

        menu.addSeparator()

        # 全屏
        fs_txt = "⛶  退出全屏" if self.isFullScreen() else "⛶  全屏"
        menu.addAction(fs_txt).triggered.connect(self.toggle_fullscreen)

        menu.addSeparator()

        # 书签
        menu.addAction("🔖  添加书签  [M]").triggered.connect(self.add_bookmark)

        menu.addSeparator()

        # 历史
        menu.addAction("🗑  清空历史记录").triggered.connect(self._clear_history)

        # 截图
        menu.addSeparator()
        menu.addAction("📷  截图  [Ctrl+S]").triggered.connect(self._take_screenshot)

        # 打开文件/文件夹
        menu.addSeparator()
        menu.addAction("📂  打开文件").triggered.connect(self.open_file)
        menu.addAction("📁  打开文件夹").triggered.connect(self.open_folder)

        menu.exec(self.video.mapToGlobal(pos))

    # ── 位置记忆 ──────────────────────────────────────────────────────────────
    def _save_current_position(self):
        if self.current_index < 0 or not self.playlist:
            return
        pos = self.player.position()
        dur = self.player.duration()
        if pos > 0:
            self._dm.set_position(self.playlist[self.current_index], pos, dur)

    # ── 侧边栏显隐 ────────────────────────────────────────────────────────────
    def toggle_sidebar(self):
        sizes = self.splitter.sizes()
        if sizes[0] > 0:
            self._stored_sidebar_w = sizes[0]
            self.splitter.setSizes([0, sizes[0] + sizes[1]])
        else:
            total = sizes[0] + sizes[1]
            self.splitter.setSizes(
                [self._stored_sidebar_w, total - self._stored_sidebar_w]
            )

    # ── 定位 / 音量 ───────────────────────────────────────────────────────────
    def _seek_abs(self, val: int):
        dur = self.player.duration()
        if dur:
            self.player.setPosition(int(val * dur / 10_000))

    def _seek_rel(self, ms: int):
        pos = max(0, min(self.player.position() + ms, self.player.duration()))
        self.player.setPosition(pos)
        self._show_osd(f"{'⏩' if ms > 0 else '⏪'} {self._fmt_time(pos)}")

    def _vol_delta(self, delta: int):
        self.vol_slider.setValue(max(0, min(100, self.vol_slider.value() + delta)))
        self._show_osd(f"🔊 音量 {self.vol_slider.value()}%")

    # ── 定时刷新 + 自动保存 ───────────────────────────────────────────────────
    def _tick(self):
        pos = self.player.position()
        dur = self.player.duration()
        self.lbl_pos.setText(self._fmt_time(pos))
        if dur > 0 and not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(int(pos * 10_000 / dur))
        self._autosave_counter += 1
        if self._autosave_counter >= 75:
            self._autosave_counter = 0
            self._save_current_position()
            # 同步保存音量和倍速偏好
            self._dm.set_prefs(self.vol_slider.value(), self.speed_box.currentText())

    # ── 播放器回调 ────────────────────────────────────────────────────────────
    def _on_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("⏸" if playing else "▶")
        if playing:
            self._arm_controls_timer()
        else:
            self._controls_timer.stop()
            if not self._controls_visible:
                self._show_controls()

    def _on_duration_changed(self, dur: int):
        self.lbl_dur.setText(self._fmt_time(dur))
        if self._pending_resume > 5_000 and dur > 0:
            resume = self._pending_resume
            self._pending_resume = 0
            source = self.player.source()
            if resume < dur - 10_000:
                QTimer.singleShot(150, lambda: self._do_resume(resume, source))
        self._refresh_seek_markers()

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._save_current_position()
            if self._loop_mode == 1:                # 单曲循环
                self.player.setPosition(0)
                self.player.play()
            elif self._loop_mode == 2:              # 列表循环
                next_idx = self.current_index + 1
                self._play(0 if next_idx >= len(self.playlist) else next_idx)
            else:                                   # 不循环
                self.play_next()

    def _do_resume(self, pos: int, expected_source):
        """恢复播放位置，校验 source 未变（防止快速切歌后误操作）"""
        if self.player.source() == expected_source:
            self.player.setPosition(pos)

    def _on_tree_dbl_click(self, item: QTreeWidgetItem, _col):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            idx = self._find_playlist_index(path)
            if idx >= 0:
                self._play(idx)

    # ── 高亮当前播放项 ────────────────────────────────────────────────────────
    def _highlight_current(self):
        if not (0 <= self.current_index < len(self.playlist)):
            return
        target = self.playlist[self.current_index]

        def search(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) == target:
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    return True
                if search(child):
                    return True
            return False

        search(self.tree.invisibleRootItem())

    def closeEvent(self, event):
        self._save_current_position()
        self._flush_video_settings()
        self._dm.set_prefs(self.vol_slider.value(), self.speed_box.currentText())
        self._save_session_from_tree()   # 保存文件夹展开状态
        self._tray_mgr.cleanup()
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self._tray_mgr.on_minimize()

    # ── 状态栏提示 ────────────────────────────────────────────────────────────
    def _update_status_hint(self):
        loop_hint = ["不循环", "单曲循环", "列表循环"][self._loop_mode]
        self._status_bar.showMessage(
            f"Space=播放/暂停   M=书签   P=侧边栏   F/双击=全屏   "
            f"←→=±5s   Shift←→=±30s   ↑↓=音量   N=下一个   B=上一个   "
            f"右键=设置   循环:{loop_hint}"
        )

    # ── 工具方法 ──────────────────────────────────────────────────────────────
    def _on_speed_changed(self, text: str):
        try:
            self.player.setPlaybackRate(float(text.rstrip("x")))
            self._show_osd(f"⏩ 速度 {text}")
            # 保存独立设置
            if self.current_index >= 0:
                self._dm.set_video_settings(
                    self.playlist[self.current_index], speed=text)
        except ValueError:
            pass

    def _on_vol_changed(self, value: int):
        self.audio_out.setVolume(value / 100.0)
        # 保存独立设置（延迟合并写入，避免拖动时高频写盘）
        if self.current_index >= 0:
            self._defer_video_settings(volume=value)

    def _defer_video_settings(self, **kwargs):
        path = self.playlist[self.current_index]
        self._pending_vs.setdefault(path, {}).update(kwargs)
        self._vs_timer.start()

    def _flush_video_settings(self):
        for path, kw in self._pending_vs.items():
            self._dm.set_video_settings(path, **kw)
        self._pending_vs = {}

    # ── 文件夹展开状态持久化 ────────────────────────────────────────────────────
    def _on_tree_expand(self, item: QTreeWidgetItem):
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder and os.path.isdir(folder):
            # 找到顶层父节点（根文件夹）
            root_folder = self._get_root_folder(item)
            if root_folder:
                self._dm.set_folder_state(root_folder, folder, True)

    def _on_tree_collapse(self, item: QTreeWidgetItem):
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder and os.path.isdir(folder):
            root_folder = self._get_root_folder(item)
            if root_folder:
                self._dm.set_folder_state(root_folder, folder, False)

    def _get_root_folder(self, item: QTreeWidgetItem) -> str | None:
        """向上查找顶层文件夹路径"""
        while item.parent() is not None:
            item = item.parent()
        path = item.data(0, Qt.ItemDataRole.UserRole)
        return path if path and os.path.isdir(path) else None

    def _find_playlist_index(self, path: str) -> int:
        """从树结构中查找 path 对应的 playlist 索引（处理重复路径）"""
        idx = 0
        def search(parent):
            nonlocal idx
            for i in range(parent.childCount()):
                child = parent.child(i)
                p = child.data(0, Qt.ItemDataRole.UserRole)
                if p and os.path.isfile(p):
                    if p == path:
                        return idx
                    idx += 1
                result = search(child)
                if result is not None:
                    return result
            return None
        result = search(self.tree.invisibleRootItem())
        # 不能写 `result or -1`：第一项匹配时返回 0，会被误判成 -1
        return result if result is not None else -1

    # ── 控制栏自动隐藏 ──────────────────────────────────────────────────────────
    def _on_video_mouse_move(self, local_pos: QPoint):
        """VideoWidget 转发鼠标移动事件，用于控制栏自动隐藏"""
        if not self._controls_visible:
            self._show_controls()
        else:
            self._arm_controls_timer()

    def _arm_controls_timer(self):
        """仅全屏播放时才启动自动隐藏倒计时"""
        if (self.isFullScreen()
                and self.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState):
            self._controls_timer.start()

    def _show_controls(self):
        self._controls_visible = True
        controls_bar = self._get_controls_bar()
        if controls_bar:
            controls_bar.show()
        self._arm_controls_timer()

    def _hide_controls(self):
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        controls_bar = self._get_controls_bar()
        if controls_bar is None:
            return
        if controls_bar.underMouse():
            self._controls_timer.start()   # 鼠标还停在控制栏上，稍后再隐藏
            return
        self._controls_visible = False
        controls_bar.hide()

    def _get_controls_bar(self) -> QWidget | None:
        """获取控制栏 widget（content layout 的第二个 widget）"""
        content = self.video.parent()
        if content and content.layout() and content.layout().count() >= 2:
            return content.layout().itemAt(1).widget()
        return None

    # ── 播放列表搜索 ──────────────────────────────────────────────────────────
    def _toggle_search(self):
        if self._search_box.isVisible():
            self._search_box.clear()
            self._search_box.hide()
        else:
            self._search_box.show()
            self._search_box.setFocus()

    def _filter_tree(self, query: str):
        query_lower = query.lower().strip()
        root = self.tree.invisibleRootItem()

        def filter_item(item):
            has_visible_child = False
            for i in range(item.childCount()):
                if filter_item(item.child(i)):
                    has_visible_child = True
            if item.childCount() == 0:
                text = item.text(0).lower()
                match = not query_lower or query_lower in text
                item.setHidden(not match)
                return match
            else:
                item.setHidden(not has_visible_child)
                return has_visible_child

        for i in range(root.childCount()):
            filter_item(root.child(i))

    # ── 截图（占位，Phase 4 完善）────────────────────────────────────────────
    def _take_screenshot(self):
        if self.current_index < 0:
            return
        screenshots_dir = str(Path.home() / "Pictures")
        os.makedirs(screenshots_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = os.path.join(screenshots_dir, f"screenshot_{ts}.png")
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", default_name, "PNG 图片 (*.png)")
        if not file_path:
            return
        screen = self.video.screen()
        if screen:
            pixmap = screen.grabWindow(self.video.winId())
            if not pixmap.isNull():
                pixmap.save(file_path)
                self._show_osd(f"📷 截图已保存")
                return
        self._show_osd("截图失败")

    # ── OSD 位置更新 ─────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_osd_position()

    def _update_osd_position(self):
        content = self._osd.parent()
        if content:
            x = content.width() - self._osd.width() - 20
            y = content.height() - self._osd.height() - 80
            self._osd.move(max(10, x), max(10, y))

    @staticmethod
    def _fmt_time(ms: int) -> str:
        s = max(0, ms) // 1000
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"

    def _make_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(34)
        bar.setStyleSheet("background:#222222;border-top:1px solid #333333;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        return bar

    def _make_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #242424; color: #cccccc;
                border: 1px solid #444444; padding: 3px;
                font-size: 13px;
            }
            QMenu::item           { padding: 6px 22px 6px 10px; border-radius: 3px; }
            QMenu::item:selected  { background: #0d6efd; color: #ffffff; }
            QMenu::separator      { height: 1px; background: #3a3a3a; margin: 3px 0; }
            QMenu::right-arrow    { width: 8px; }
        """)
        return menu

    def _ibtn(self, text: str, size: int, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(size, size)
        r = size // 2
        if primary:
            b.setStyleSheet(f"""
                QPushButton {{
                    background: #0d6efd; color: #ffffff;
                    border: none; border-radius: {r}px; font-size: 16px;
                }}
                QPushButton:hover   {{ background: #0b5ed7; }}
                QPushButton:pressed {{ background: #0a58ca; }}
            """)
        else:
            b.setStyleSheet(f"""
                QPushButton {{
                    background: #2a2a2a; color: #cccccc;
                    border: none; border-radius: {r}px; font-size: 13px;
                }}
                QPushButton:hover   {{ background: #3a3a3a; }}
                QPushButton:pressed {{ background: #4a4a4a; }}
            """)
        return b

    def _tbtn(self, text: str, checkable: bool = False,
              checked: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCheckable(checkable)
        b.setChecked(checked)
        b.setStyleSheet(self._tbtn_css(highlight=checked))
        return b

    @staticmethod
    def _tbtn_css(highlight: bool = False) -> str:
        if highlight:
            return """
                QPushButton {
                    background: #0d6efd; color: #ffffff;
                    border: 1px solid #0d6efd; border-radius: 4px;
                    padding: 5px 10px; font-size: 12px;
                }
                QPushButton:hover { background: #0b5ed7; }
            """
        return """
            QPushButton {
                background: #2a2a2a; color: #cccccc;
                border: 1px solid #444444; border-radius: 4px;
                padding: 5px 10px; font-size: 12px;
            }
            QPushButton:hover   { background: #3a3a3a; }
            QPushButton:checked {
                background: #0d6efd; color: #ffffff; border-color: #0d6efd;
            }
        """

    @staticmethod
    def _slider_css(color: str) -> str:
        return f"""
            QSlider::groove:horizontal {{
                height: 4px; background: #333333; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 12px; height: 12px; background: {color};
                border-radius: 6px; margin: -4px 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {color}; border-radius: 2px;
            }}
        """

    @staticmethod
    def _combo_css() -> str:
        return """
            QComboBox {
                background: #2a2a2a; color: #cccccc;
                border: 1px solid #444444; border-radius: 4px;
                padding: 4px 6px; font-size: 12px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #2a2a2a; color: #cccccc;
                selection-background-color: #0d6efd;
                border: 1px solid #555555;
            }
        """

    @staticmethod
    def _list_css() -> str:
        return """
            QListWidget {
                background: #1c1c1c; color: #cccccc;
                border: none; font-size: 13px;
            }
            QListWidget::item {
                padding: 7px 10px;
                border-bottom: 1px solid #252525;
            }
            QListWidget::item:hover    { background: #2a2a2a; }
            QListWidget::item:selected { background: #0d6efd; color: #ffffff; }
        """

    @staticmethod
    def _tree_css() -> str:
        return """
            QTreeWidget {
                background: #1c1c1c; color: #cccccc;
                border: none; font-size: 13px;
            }
            QTreeWidget::item { padding: 5px 2px; }
            QTreeWidget::item:hover    { background: #2a2a2a; border-radius: 3px; }
            QTreeWidget::item:selected { background: #0d6efd; color: #fff; border-radius: 3px; }
            QTreeWidget::branch        { background: #1c1c1c; }
        """


# ── 系统托盘 + 全局快捷键 ─────────────────────────────────────────────────────
class TrayManager(QObject):
    """系统托盘图标 + 全局快捷键管理"""

    # keyboard 库在其自己的后台线程里回调，Qt 禁止跨线程操作控件，
    # 因此回调只 emit 信号，由队列连接把实际 GUI 操作转回主线程执行
    hotkey_cmd = pyqtSignal(str)

    _COMMANDS = {
        "toggle": "toggle_play",
        "next":   "play_next",
        "prev":   "play_prev",
    }

    def __init__(self, window: PlayerWindow):
        super().__init__()
        self._win = window
        self._tray: QSystemTrayIcon | None = None
        self._hotkeys: list[str] = []
        self.hotkey_cmd.connect(self._dispatch_command)
        self._build_tray()
        self._register_hotkeys()

    def _dispatch_command(self, cmd: str):
        fn = getattr(self._win, self._COMMANDS.get(cmd, ""), None)
        if fn:
            fn()

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(Path(__file__).with_name("icon.ico")))
        if icon.isNull():
            icon = self._win.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._tray = QSystemTrayIcon(icon, self._win)
        self._tray.setToolTip("Video Player")
        # 右键菜单
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background:#242424; color:#ccc; border:1px solid #444;
                    padding:3px; font-size:13px; }
            QMenu::item { padding:6px 22px 6px 10px; border-radius:3px; }
            QMenu::item:selected { background:#0d6efd; color:#fff; }
        """)
        act_play = menu.addAction("▶  播放 / 暂停")
        act_play.triggered.connect(self._win.toggle_play)
        act_prev = menu.addAction("⏮  上一个")
        act_prev.triggered.connect(self._win.play_prev)
        act_next = menu.addAction("⏭  下一个")
        act_next.triggered.connect(self._win.play_next)
        menu.addSeparator()
        act_show = menu.addAction("🗗  显示主窗口")
        act_show.triggered.connect(self._restore_window)
        menu.addSeparator()
        act_quit = menu.addAction("✕  退出")
        act_quit.triggered.connect(self._quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_window()

    def _restore_window(self):
        self._win.showNormal()
        self._win.activateWindow()

    def _quit(self):
        self.cleanup()
        QApplication.quit()

    def _register_hotkeys(self):
        if not HAS_KEYBOARD:
            return
        try:
            self._hotkeys.append(
                keyboard.add_hotkey("ctrl+alt+space",
                                    lambda: self.hotkey_cmd.emit("toggle")))
            self._hotkeys.append(
                keyboard.add_hotkey("ctrl+alt+right",
                                    lambda: self.hotkey_cmd.emit("next")))
            self._hotkeys.append(
                keyboard.add_hotkey("ctrl+alt+left",
                                    lambda: self.hotkey_cmd.emit("prev")))
        except Exception as e:
            print(f"[VideoPlayer] 全局快捷键注册失败: {e}", file=sys.stderr)

    def on_minimize(self):
        """最小化时隐藏到托盘"""
        if self._tray and self._win.isMinimized():
            self._win.hide()

    def cleanup(self):
        if HAS_KEYBOARD:
            for hk in self._hotkeys:
                try:
                    keyboard.remove_hotkey(hk)
                except Exception:
                    pass
        if self._tray:
            self._tray.hide()


# ── 入口 ──────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    pal = QPalette()
    colors = {
        QPalette.ColorRole.Window:          "#1a1a1a",
        QPalette.ColorRole.WindowText:      "#cccccc",
        QPalette.ColorRole.Base:            "#1c1c1c",
        QPalette.ColorRole.AlternateBase:   "#222222",
        QPalette.ColorRole.Text:            "#cccccc",
        QPalette.ColorRole.Button:          "#2a2a2a",
        QPalette.ColorRole.ButtonText:      "#cccccc",
        QPalette.ColorRole.Highlight:       "#0d6efd",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase:     "#2a2a2a",
        QPalette.ColorRole.ToolTipText:     "#cccccc",
        QPalette.ColorRole.BrightText:      "#ffffff",
        QPalette.ColorRole.Link:            "#0d6efd",
    }
    for role, hex_c in colors.items():
        pal.setColor(role, QColor(hex_c))
    app.setPalette(pal)

    win = PlayerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
