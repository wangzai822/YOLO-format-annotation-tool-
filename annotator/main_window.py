from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .canvas import CanvasView, ToolMode
from .io_utils import (
    ProjectState,
    labels_json_path,
    list_images,
    load_image_labels,
    load_project_state,
    save_image_labels,
    save_project_state,
)
from .shapes import BBoxItem, OBBItem, PolygonItem
from .undo import AddItemCommand, DeleteItemCommand, ModifyItemCommand

MAX_CLASS_SLOTS = 9  # fixed 1..9

# Must match shapes.py (used for Alt highlight overlay numbering).
DATA_SEQ = 9001
DATA_TYPE_SEQ = 9002


def dist(a, b) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 820)

        self.undo_stack = QUndoStack(self)
        self.state = ProjectState()

        self.images: List[Path] = []
        self.input_dir: Optional[Path] = None
        self.output_dir: Optional[Path] = None

        self._uid_to_item: Dict[str, object] = {}
        self._uid_to_treeitem: Dict[str, QTreeWidgetItem] = {}

        # Stable ordering across refreshes; stored in JSON as created_index.
        self._create_counter = 0

        # Autosave + Alt highlight state.
        self._suspend_autosave = False
        self._alt_down = False

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(250)
        self._autosave_timer.timeout.connect(lambda: self.save_current_labels(silent=True))
        self.undo_stack.indexChanged.connect(self._queue_autosave)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.canvas = CanvasView(self)
        self.setCentralWidget(self.canvas)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.canvas.status.connect(self.status_bar.showMessage)

        self._ensure_class_slots()

        self._build_actions()
        self._build_toolbar()
        self._build_docks()

        self.canvas.created_item.connect(self._on_created_item)
        self.canvas.scene.selectionChanged.connect(self._sync_ann_tree_from_scene)

        self._update_window_title()
        self._sync_path_edits()
        self._refresh_class_buttons()
        self._refresh_ann_tree()

    # ---------- Global events (Alt highlight) ----------
    def eventFilter(self, obj, event) -> bool:
        t = event.type()
        if t == QEvent.KeyPress and event.key() == Qt.Key_Alt:
            if not self._alt_down:
                self._alt_down = True
                self._set_alt_highlight(True)
        elif t == QEvent.KeyRelease and event.key() == Qt.Key_Alt:
            if self._alt_down:
                self._alt_down = False
                self._set_alt_highlight(False)
        elif t in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
            if self._alt_down:
                self._alt_down = False
                self._set_alt_highlight(False)
        return False

    def _set_alt_highlight(self, on: bool) -> None:
        try:
            self.canvas.scene.setProperty("alt_highlight", bool(on))
            self.canvas.scene.update()
        except Exception:
            pass

    # ---------- Autosave ----------
    def _queue_autosave(self) -> None:
        # Auto-save after every undo-stack step (add/modify/delete/undo/redo).
        if self._suspend_autosave:
            return
        if self.input_dir is None or self.output_dir is None or not self.images:
            return
        self._autosave_timer.start()

    # ---------- UI ----------
    def _build_actions(self) -> None:
        self.act_open_input = QAction("Open Image Folder... 打开图片文件夹...", self)
        self.act_open_input.triggered.connect(self.choose_input_dir)

        self.act_open_output = QAction("Set Output Folder... 设置输出目录...", self)
        self.act_open_output.triggered.connect(self.choose_output_dir)

        self.act_save = QAction("Save JSON 保存JSON", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.triggered.connect(self.save_current_labels)

        self.act_next = QAction("Next (Save) 下一张(保存)", self)
        self.act_next.setShortcuts([QKeySequence("N"), QKeySequence(Qt.Key_PageDown)])
        self.act_next.triggered.connect(self.next_image)

        self.act_prev = QAction("Prev (Save) 上一张(保存)", self)
        self.act_prev.setShortcuts([QKeySequence(Qt.Key_PageUp)])
        self.act_prev.triggered.connect(self.prev_image)

        self.act_fit = QAction("Fit 适配窗口", self)
        self.act_fit.setShortcut(QKeySequence("F"))
        self.act_fit.triggered.connect(self.canvas.fit_to_view)

        self.act_undo = self.undo_stack.createUndoAction(self, "Undo 撤销")
        self.act_undo.setShortcut(QKeySequence.Undo)

        self.act_redo = self.undo_stack.createRedoAction(self, "Redo 重做")
        self.act_redo.setShortcut(QKeySequence.Redo)

        self.act_select = QAction("Select 选择 (V)", self)
        self.act_select.setShortcut(QKeySequence("V"))
        self.act_select.triggered.connect(lambda: self.canvas.set_mode(ToolMode.SELECT))

        self.act_bbox = QAction("BBox 轴对齐框 (B)", self)
        self.act_bbox.setShortcut(QKeySequence("B"))
        self.act_bbox.triggered.connect(lambda: self.canvas.set_mode(ToolMode.BBOX))

        self.act_obb = QAction("OBB 旋转框 (O)", self)
        self.act_obb.setShortcut(QKeySequence("O"))
        self.act_obb.triggered.connect(lambda: self.canvas.set_mode(ToolMode.OBB))

        self.act_poly = QAction("Mask 多边形 (M)", self)
        self.act_poly.setShortcut(QKeySequence("M"))
        self.act_poly.triggered.connect(lambda: self.canvas.set_mode(ToolMode.POLY))

        # Keep menu items for compatibility; map to "set name" behavior.
        self.act_add_class = QAction("Add Class 新增类别...", self)
        self.act_add_class.setShortcut(QKeySequence("Alt+N"))
        self.act_add_class.triggered.connect(self.add_class)

        self.act_rename_class = QAction("Rename Class 重命名类别...", self)
        self.act_rename_class.setShortcut(QKeySequence("Alt+R"))
        self.act_rename_class.triggered.connect(self.rename_class)

        self.act_delete = QAction("Delete Selected 删除选中", self)
        self.act_delete.setShortcut(QKeySequence.Delete)
        self.act_delete.triggered.connect(self.delete_selected_items)

        m_file = self.menuBar().addMenu("File 文件")
        m_file.addAction(self.act_open_input)
        m_file.addAction(self.act_open_output)
        m_file.addSeparator()
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_prev)
        m_file.addAction(self.act_next)

        m_edit = self.menuBar().addMenu("Edit 编辑")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_delete)

        m_tools = self.menuBar().addMenu("Tools 工具")
        m_tools.addAction(self.act_select)
        m_tools.addAction(self.act_bbox)
        m_tools.addAction(self.act_obb)
        m_tools.addAction(self.act_poly)

        m_classes = self.menuBar().addMenu("Classes 类别")
        m_classes.addAction(self.act_add_class)
        m_classes.addAction(self.act_rename_class)

        m_view = self.menuBar().addMenu("View 视图")
        m_view.addAction(self.act_fit)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Tools 工具", self)
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        tb.addAction(self.act_open_input)
        tb.addAction(self.act_open_output)
        tb.addSeparator()
        tb.addAction(self.act_save)
        tb.addAction(self.act_prev)
        tb.addAction(self.act_next)
        tb.addSeparator()
        tb.addAction(self.act_select)
        tb.addAction(self.act_bbox)
        tb.addAction(self.act_obb)
        tb.addAction(self.act_poly)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        tb.addAction(self.act_fit)

    def _build_docks(self) -> None:
        # Left panel: Paths + Classes(1..9 buttons) + Save/Next + Tools(V/B/O/M)
        left_panel = QWidget(self)
        v = QVBoxLayout(left_panel)

        g_paths = QGroupBox("Paths 路径", left_panel)
        vp = QVBoxLayout(g_paths)

        self.in_edit = QLineEdit()
        self.in_edit.setReadOnly(True)
        btn_in = QPushButton("Browse Input... 选择输入图片目录")
        btn_in.clicked.connect(self.choose_input_dir)

        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        btn_out = QPushButton("Browse Output... 选择输出目录")
        btn_out.clicked.connect(self.choose_output_dir)

        vp.addWidget(QLabel("Input 输入:"))
        vp.addWidget(self.in_edit)
        vp.addWidget(btn_in)
        vp.addWidget(QLabel("Output 输出:"))
        vp.addWidget(self.out_edit)
        vp.addWidget(btn_out)

        g_classes = QGroupBox("Classes 类别 (1..9 数字快捷键)", left_panel)
        vc = QVBoxLayout(g_classes)

        self.class_btn_group = QButtonGroup(self)
        self.class_btn_group.setExclusive(True)
        self.class_buttons: List[QPushButton] = []

        grid = QGridLayout()
        for i in range(MAX_CLASS_SLOTS):
            btn = QPushButton(str(i + 1))
            btn.setCheckable(True)
            btn.setMinimumHeight(34)
            self.class_btn_group.addButton(btn, i)
            self.class_buttons.append(btn)
            r = i // 3
            c = i % 3
            grid.addWidget(btn, r, c)
        self.class_btn_group.idClicked.connect(self._on_class_button_clicked)

        name_row = QWidget(g_classes)
        hn = QHBoxLayout(name_row)
        hn.setContentsMargins(0, 0, 0, 0)
        self.class_name_edit = QLineEdit()
        self.class_name_edit.setPlaceholderText("给当前类别命名，例如: spalling  (Enter 保存)")
        self.class_name_edit.returnPressed.connect(self._apply_class_name_from_edit)
        btn_set_name = QPushButton("Set 设置")
        btn_set_name.clicked.connect(self._apply_class_name_from_edit)
        btn_clear_name = QPushButton("Clear 清空")
        btn_clear_name.clicked.connect(self._clear_current_class_name)
        hn.addWidget(self.class_name_edit, 1)
        hn.addWidget(btn_set_name)
        hn.addWidget(btn_clear_name)

        vc.addLayout(grid)
        vc.addWidget(name_row)

        g_nav = QGroupBox("Save/Next 保存/翻页", left_panel)
        vn = QVBoxLayout(g_nav)
        btn_save = QPushButton("Save 保存 (Ctrl+S)")
        btn_prev = QPushButton("Prev 上一张 (PageUp)")
        btn_next = QPushButton("Next 下一张 (N / PageDown)")
        btn_del = QPushButton("Delete 删除选中 (Del)")
        btn_save.clicked.connect(self.act_save.trigger)
        btn_prev.clicked.connect(self.act_prev.trigger)
        btn_next.clicked.connect(self.act_next.trigger)
        btn_del.clicked.connect(self.act_delete.trigger)
        vn.addWidget(btn_save)
        vn.addWidget(btn_prev)
        vn.addWidget(btn_next)
        vn.addWidget(btn_del)

        g_tools = QGroupBox("Tools 工具 (V/B/O/M)", left_panel)
        vt = QVBoxLayout(g_tools)
        btn_v = QPushButton("Select 选择 (V)")
        btn_b = QPushButton("BBox 轴对齐框 (B)")
        btn_o = QPushButton("OBB 旋转框 (O)")
        btn_m = QPushButton("Mask 多边形 (M)")
        btn_v.clicked.connect(self.act_select.trigger)
        btn_b.clicked.connect(self.act_bbox.trigger)
        btn_o.clicked.connect(self.act_obb.trigger)
        btn_m.clicked.connect(self.act_poly.trigger)
        vt.addWidget(btn_v)
        vt.addWidget(btn_b)
        vt.addWidget(btn_o)
        vt.addWidget(btn_m)

        v.addWidget(g_paths)
        v.addWidget(g_classes)
        v.addWidget(g_nav)
        v.addWidget(g_tools)
        v.addStretch(1)

        dock_left = QDockWidget("Project 项目", self)
        dock_left.setWidget(left_panel)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock_left)

        # Right: Annotations tree (checkbox per item + checkbox per type group)
        self.ann_tree = QTreeWidget(self)
        self.ann_tree.setHeaderHidden(True)
        self.ann_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.ann_tree.itemSelectionChanged.connect(self._on_ann_tree_selection)
        self.ann_tree.itemChanged.connect(self._on_ann_tree_item_changed)

        dock_ann = QDockWidget("Annotations 标注", self)
        dock_ann.setWidget(self.ann_tree)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_ann)

        # Help dock
        help_text = QLabel(
            "Shortcuts 快捷键:\n"
            "  1..9 选择类别\n"
            "  V Select 选择\n"
            "  B BBox 轴对齐框\n"
            "  O OBB 旋转框\n"
            "  M Mask 多边形 (Enter/RightClick 右键结束)\n"
            "  Ctrl+S Save 保存JSON\n"
            "  N / PageDown Next 下一张(保存)\n"
            "  PageUp Prev 上一张(保存)\n"
            "  Ctrl+Z / Ctrl+Y Undo/Redo 撤销/重做\n"
            "  Del Delete 删除\n"
            "  F Fit 适配窗口\n"
            "  Wheel Zoom 滚轮缩放\n"
            "  Space+Drag Pan 空格+拖拽平移\n"
            "  Alt Hold 高亮显示所有标注+编号(检查噪声)\n"
            "\n"
            "Tips 提示:\n"
            "  画框/画Mask模式下：按住 Ctrl 再点击已有标注可选择/编辑\n"
            "  BBox/OBB 缩放：按住 Ctrl，在框附近一定范围内拖拽即可缩放(不必精确点角点)\n"
            "  右侧树的勾选框：控制该标注/该类型在画布上显示/隐藏\n"
            "  已保存标注：每步操作(Add/Modify/Delete/Undo/Redo)自动更新保存JSON\n"
        )
        help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)

        dock_help = QDockWidget("Help 帮助", self)
        dock_help.setWidget(help_text)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock_help)

    # ---------- Window ----------
    def _sync_path_edits(self) -> None:
        if hasattr(self, "in_edit"):
            self.in_edit.setText(str(self.input_dir or ""))
        if hasattr(self, "out_edit"):
            self.out_edit.setText(str(self.output_dir or ""))

    def _update_window_title(self) -> None:
        in_s = str(self.input_dir) if self.input_dir else "(no input 无输入)"
        out_s = str(self.output_dir) if self.output_dir else "(no output 无输出)"
        idx = self.state.index + 1 if self.images else 0
        total = len(self.images)
        self.setWindowTitle(f"Annotator 标注工具 | {idx}/{total} | in={in_s} | out={out_s}")

    # ---------- Classes (1..9) ----------
    def _ensure_class_slots(self) -> None:
        if self.state.classes is None:
            self.state.classes = []
        if len(self.state.classes) < MAX_CLASS_SLOTS:
            self.state.classes.extend([""] * (MAX_CLASS_SLOTS - len(self.state.classes)))
        if len(self.state.classes) > MAX_CLASS_SLOTS:
            self.state.classes = self.state.classes[:MAX_CLASS_SLOTS]

    def _class_label(self, class_id: int) -> str:
        idx1 = class_id + 1
        name = ""
        if 0 <= class_id < len(self.state.classes):
            name = (self.state.classes[class_id] or "").strip()
        if name:
            return f"{idx1}: {name}"
        return f"{idx1}: (unnamed 未命名)"

    def _refresh_class_buttons(self) -> None:
        self._ensure_class_slots()
        cur = int(self.canvas.current_class_id or 0)
        cur = max(0, min(cur, MAX_CLASS_SLOTS - 1))
        self.canvas.set_current_class(cur)

        for i, btn in enumerate(self.class_buttons):
            name = (self.state.classes[i] or "").strip()
            btn.setText(f"{i + 1}: {name}" if name else f"{i + 1}")
            btn.setToolTip(self._class_label(i))

        self.class_btn_group.blockSignals(True)
        self.class_buttons[cur].setChecked(True)
        self.class_btn_group.blockSignals(False)

        self.class_name_edit.blockSignals(True)
        self.class_name_edit.setText((self.state.classes[cur] or "").strip())
        self.class_name_edit.blockSignals(False)

    def _on_class_button_clicked(self, class_id: int) -> None:
        class_id = int(class_id)
        if class_id < 0 or class_id >= MAX_CLASS_SLOTS:
            return
        self.canvas.set_current_class(class_id)
        self.class_name_edit.setText((self.state.classes[class_id] or "").strip())
        self.status_bar.showMessage(f"Class 类别: {self._class_label(class_id)}", 2500)

    def _apply_class_name_from_edit(self) -> None:
        cid = int(self.canvas.current_class_id or 0)
        cid = max(0, min(cid, MAX_CLASS_SLOTS - 1))
        name = (self.class_name_edit.text() or "").strip()
        self._ensure_class_slots()
        self.state.classes[cid] = name
        self._refresh_class_buttons()
        self._save_project_state()
        self._refresh_ann_tree()
        self._queue_autosave()  # update current image JSON classes list silently

    def _clear_current_class_name(self) -> None:
        self.class_name_edit.setText("")
        self._apply_class_name_from_edit()

    def add_class(self) -> None:
        # Put name into first empty slot.
        self._ensure_class_slots()
        name, ok = QInputDialog.getText(self, "Add class 新增类别", "Class name 类别名称:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        for i in range(MAX_CLASS_SLOTS):
            if not (self.state.classes[i] or "").strip():
                self.state.classes[i] = name
                self.canvas.set_current_class(i)
                self._refresh_class_buttons()
                self._save_project_state()
                self._refresh_ann_tree()
                self._queue_autosave()
                return
        QMessageBox.information(self, "Full 已满", "Classes 1..9 are already named.\n类别 1..9 都已命名。")

    def rename_class(self) -> None:
        self._ensure_class_slots()
        cid = int(self.canvas.current_class_id or 0)
        cid = max(0, min(cid, MAX_CLASS_SLOTS - 1))
        cur = (self.state.classes[cid] or "").strip()
        name, ok = QInputDialog.getText(
            self,
            "Rename class 重命名类别",
            f"Class {cid + 1} name 类别名称:",
            text=cur,
        )
        if not ok:
            return
        name = (name or "").strip()
        self.state.classes[cid] = name
        self._refresh_class_buttons()
        self._save_project_state()
        self._refresh_ann_tree()
        self._queue_autosave()

    def keyPressEvent(self, event) -> None:
        # 1..9 quick switch class (unless typing in the name edit).
        if hasattr(self, "class_name_edit") and self.class_name_edit.hasFocus():
            super().keyPressEvent(event)
            return

        k = event.key()
        if Qt.Key_1 <= k <= Qt.Key_9:
            cid = int(k - Qt.Key_1)  # 0..8
            if 0 <= cid < MAX_CLASS_SLOTS:
                self.class_buttons[cid].click()
                event.accept()
                return

        super().keyPressEvent(event)

    # ---------- Paths ----------
    def choose_input_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select image folder 选择图片目录")
        if not d:
            return
        self.input_dir = Path(d)
        self.images = list_images(self.input_dir)
        self.state.index = 0
        if not self.images:
            QMessageBox.warning(self, "No images 没有图片", "No supported images found. 未找到支持的图片格式。")
            return
        self._load_current_image()
        self._update_window_title()
        self._sync_path_edits()

    def choose_output_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder 选择输出目录")
        if not d:
            return
        self.output_dir = Path(d)

        st_path = self.output_dir / "project_state.json"
        st = load_project_state(st_path)
        if st.classes is not None:
            self.state.classes = list(st.classes)
            self._ensure_class_slots()
            self._refresh_class_buttons()

        self._save_project_state()
        self._update_window_title()
        self._sync_path_edits()
        self._refresh_ann_tree()
        self._queue_autosave()

    def _save_project_state(self) -> None:
        if self.output_dir is None:
            return
        self._ensure_class_slots()
        self.state.input_dir = str(self.input_dir or "")
        self.state.output_dir = str(self.output_dir or "")
        st_path = self.output_dir / "project_state.json"
        save_project_state(st_path, self.state)

    # ---------- Annotation items ----------
    def _connect_item_signals(self, item) -> None:
        if hasattr(item, "edited"):
            item.edited.connect(self._on_item_edited)

    def _on_item_edited(self, item, before: dict, after: dict) -> None:
        self.undo_stack.push(ModifyItemCommand(item, before, after, "Modify annotation 修改标注"))
        self._refresh_ann_tree()

    def _assign_created_index(self, item, from_json: Optional[dict] = None) -> None:
        ci = 0
        if from_json:
            try:
                ci = int(from_json.get("created_index", 0) or 0)
            except Exception:
                ci = 0

        if ci > 0:
            setattr(item, "created_index", ci)
            self._create_counter = max(self._create_counter, ci)
            return

        self._create_counter += 1
        setattr(item, "created_index", self._create_counter)

    def _load_current_image(self) -> None:
        if not self.images:
            return
        self.state.index = max(0, min(self.state.index, len(self.images) - 1))

        img_path = self.images[self.state.index]
        pix = QPixmap(str(img_path))
        if pix.isNull():
            QMessageBox.warning(self, "Load failed 加载失败", f"Failed to load image 无法加载图片:\n{img_path}")
            return

        self._suspend_autosave = True
        self._autosave_timer.stop()

        self.undo_stack.clear()  # per-image history
        self.canvas.load_image(pix)
        self.canvas.fit_to_view()

        self._create_counter = 0
        self._load_existing_labels(img_path)

        self._update_window_title()
        self._save_project_state()
        self._sync_path_edits()
        self._refresh_ann_tree()

        self._suspend_autosave = False

    def _load_existing_labels(self, img_path: Path) -> None:
        if self.output_dir is None or self.input_dir is None:
            return

        jp = labels_json_path(self.output_dir, self.input_dir, img_path)
        data = load_image_labels(jp)
        if not data:
            return

        anns = data.get("annotations", [])
        for a in anns:
            t = a.get("type")
            cls = int(a.get("class_id", 0))

            if t == "bbox":
                bb = a.get("yolo_bbox", {})
                xc = float(bb.get("x_center", 0.5)) * self.canvas.img_w
                yc = float(bb.get("y_center", 0.5)) * self.canvas.img_h
                w = float(bb.get("width", 0.1)) * self.canvas.img_w
                h = float(bb.get("height", 0.1)) * self.canvas.img_h

                item = BBoxItem(w, h, class_id=cls)
                item.uid = str(a.get("id", item.uid))
                item.setPos(xc, yc)
                item.setRotation(0.0)

                self._assign_created_index(item, a)
                self.canvas.scene.addItem(item)
                self._connect_item_signals(item)

            elif t == "obb":
                corners = a.get("yolo_obb", {}).get("corners", [])
                if len(corners) == 4:
                    pts = [
                        QPointF(float(p[0]) * self.canvas.img_w, float(p[1]) * self.canvas.img_h)
                        for p in corners
                    ]
                    c = QPointF(sum(p.x() for p in pts) / 4.0, sum(p.y() for p in pts) / 4.0)
                    w = max(2.0, dist(pts[0], pts[1]))
                    h = max(2.0, dist(pts[1], pts[2]))
                    angle = math.degrees(math.atan2(pts[1].y() - pts[0].y(), pts[1].x() - pts[0].x()))

                    item = OBBItem(w, h, class_id=cls)
                    item.uid = str(a.get("id", item.uid))
                    item.setPos(c)
                    item.setRotation(angle)

                    self._assign_created_index(item, a)
                    self.canvas.scene.addItem(item)
                    self._connect_item_signals(item)

            elif t == "polygon":
                pts = a.get("yolo_seg", {}).get("points", [])
                if len(pts) >= 3:
                    ps = [QPointF(float(x) * self.canvas.img_w, float(y) * self.canvas.img_h) for x, y in pts]
                    item = PolygonItem(ps, class_id=cls)
                    item.uid = str(a.get("id", item.uid))
                    item.setPos(0.0, 0.0)
                    item.setRotation(0.0)

                    self._assign_created_index(item, a)
                    self.canvas.scene.addItem(item)
                    self._connect_item_signals(item)

    def _iter_ann_items(self):
        items = [it for it in self.canvas.scene.items() if isinstance(it, (BBoxItem, OBBItem, PolygonItem))]
        items.sort(key=lambda x: int(getattr(x, "created_index", 0) or 0))
        return items

    def _update_item_display_numbers(self, items) -> None:
        # For Alt highlight overlay (shapes.py reads these via item.data()).
        type_counts = {"bbox": 0, "obb": 0, "polygon": 0}
        for seq, it in enumerate(items, start=1):
            t = str(getattr(it, "anno_type", "") or "")
            if t in type_counts:
                type_counts[t] += 1
            try:
                it.setData(DATA_SEQ, seq)
                it.setData(DATA_TYPE_SEQ, type_counts.get(t, 0))
            except Exception:
                pass

    # ---------- Annotation tree (type -> items, with checkboxes) ----------
    def _refresh_ann_tree(self) -> None:
        self._ensure_class_slots()

        self._uid_to_item.clear()
        self._uid_to_treeitem.clear()

        self.ann_tree.blockSignals(True)
        self.ann_tree.clear()

        items = list(self._iter_ann_items())
        self._update_item_display_numbers(items)

        if not items:
            tip = QTreeWidgetItem(["(No annotations yet)  还没有标注"])
            tip.setFlags(Qt.ItemIsEnabled)
            self.ann_tree.addTopLevelItem(tip)
            self.ann_tree.blockSignals(False)
            return

        groups: List[Tuple[str, str]] = [
            ("bbox", "BBox 轴对齐框"),
            ("obb", "OBB 旋转框"),
            ("polygon", "Mask 多边形"),
        ]
        by_type: Dict[str, List[object]] = {k: [] for k, _ in groups}

        for it in items:
            uid = getattr(it, "uid", "")
            if uid:
                self._uid_to_item[uid] = it
            t = getattr(it, "anno_type", "")
            if t in by_type:
                by_type[t].append(it)

        for t, title in groups:
            bucket = by_type.get(t, [])
            if not bucket:
                continue

            parent = QTreeWidgetItem([f"{title} ({len(bucket)})"])
            parent.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)

            # Parent checkbox reflects children visibility (checked/unchecked/partial).
            vis = [bool(getattr(it, "isVisible", lambda: True)()) for it in bucket]
            if all(vis):
                parent.setCheckState(0, Qt.Checked)
            elif not any(vis):
                parent.setCheckState(0, Qt.Unchecked)
            else:
                parent.setCheckState(0, Qt.PartiallyChecked)

            self.ann_tree.addTopLevelItem(parent)

            for idx, it in enumerate(bucket, start=1):
                uid = getattr(it, "uid", "")
                cls = int(getattr(it, "class_id", 0))
                cls_name = self.state.classes[cls].strip() if 0 <= cls < len(self.state.classes) else ""
                cls_label = f"{cls + 1}: {cls_name}" if cls_name else f"{cls + 1}: (unnamed 未命名)"

                leaf = QTreeWidgetItem([f"{idx}. {cls_label}"])
                leaf.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                leaf.setData(0, Qt.UserRole, uid)
                leaf.setCheckState(0, Qt.Checked if it.isVisible() else Qt.Unchecked)

                # Show item color in the list (each annotation now has its own color).
                try:
                    leaf.setForeground(0, it.pen().color())
                except Exception:
                    pass

                parent.addChild(leaf)

                if uid:
                    self._uid_to_treeitem[uid] = leaf

            parent.setExpanded(True)

        self.ann_tree.blockSignals(False)
        self._sync_ann_tree_from_scene()

    def _on_ann_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        uid = item.data(0, Qt.UserRole)
        if not isinstance(uid, str) or not uid:
            # Parent nodes will propagate checkbox state to children; children changes are handled below.
            return

        shp = self._uid_to_item.get(uid)
        if shp is None:
            return

        checked = item.checkState(0) == Qt.Checked
        try:
            shp.setVisible(bool(checked))
            if not checked:
                shp.setSelected(False)
        except Exception:
            pass

        self._sync_ann_tree_from_scene()

    def _sync_ann_tree_from_scene(self) -> None:
        selected_uids = set()
        for it in self.canvas.scene.selectedItems():
            if isinstance(it, (BBoxItem, OBBItem, PolygonItem)):
                uid = getattr(it, "uid", "")
                if uid:
                    selected_uids.add(uid)

        self.ann_tree.blockSignals(True)
        self.ann_tree.clearSelection()
        for uid in selected_uids:
            ti = self._uid_to_treeitem.get(uid)
            if ti is not None:
                ti.setSelected(True)
        self.ann_tree.blockSignals(False)

    def _on_ann_tree_selection(self) -> None:
        selected_uids = set()
        for it in self.ann_tree.selectedItems():
            uid = it.data(0, Qt.UserRole)
            if isinstance(uid, str) and uid:
                selected_uids.add(uid)

        self.canvas.scene.blockSignals(True)
        self.canvas.scene.clearSelection()
        for uid in selected_uids:
            shp = self._uid_to_item.get(uid)
            if shp is not None and getattr(shp, "isVisible", lambda: True)():
                shp.setSelected(True)
        self.canvas.scene.blockSignals(False)

    # ---------- Create/Delete/Save ----------
    def _on_created_item(self, item) -> None:
        self._assign_created_index(item, None)
        self._connect_item_signals(item)
        self.undo_stack.push(AddItemCommand(self.canvas.scene, item, "Add annotation 新增标注"))
        item.setSelected(True)
        self._refresh_ann_tree()

    def delete_selected_items(self) -> None:
        items = [it for it in self.canvas.scene.selectedItems() if isinstance(it, (BBoxItem, OBBItem, PolygonItem))]
        if not items:
            return
        for it in items:
            self.undo_stack.push(DeleteItemCommand(self.canvas.scene, it, "Delete annotation 删除标注"))
        self._refresh_ann_tree()

    def save_current_labels(self, silent: bool = False) -> None:
        if self.input_dir is None or self.output_dir is None or not self.images:
            if not silent:
                QMessageBox.information(
                    self,
                    "Missing paths 缺少路径",
                    "Please set input folder and output folder first.\n请先设置输入图片目录和输出目录。",
                )
            return

        self._ensure_class_slots()

        img_path = self.images[self.state.index]
        jp = labels_json_path(self.output_dir, self.input_dir, img_path)

        annotations = []
        rows = {"bbox": [], "obb": [], "polygon": []}

        type_counters = {"bbox": 0, "obb": 0, "polygon": 0}

        items = list(self._iter_ann_items())
        self._update_item_display_numbers(items)

        for seq, it in enumerate(items, start=1):
            d = it.to_label_dict(self.canvas.img_w, self.canvas.img_h)

            t = d.get("type")
            if t in type_counters:
                type_counters[t] += 1
                d["type_seq"] = type_counters[t]
            else:
                d["type_seq"] = 0

            d["seq"] = seq
            d["created_index"] = int(getattr(it, "created_index", 0) or 0)

            annotations.append(d)

            if t in rows:
                rows[t].append(d.get("ultralytics_row", ""))

        payload = {
            "schema": "ultralytics-json-v1",
            "image": {"file_name": img_path.name, "width": self.canvas.img_w, "height": self.canvas.img_h},
            # Only save classes that actually exist (non-empty names). Keep original id.
            "classes": [
                {"id": i, "name": (n or "").strip()}
                for i, n in enumerate(self.state.classes[:MAX_CLASS_SLOTS])
                if (n or "").strip()
            ],
            "annotations": annotations,
            "ultralytics_rows": rows,
            "note": "JSON-only export. ultralytics_rows can be written to *.txt if needed.",
        }

        save_image_labels(jp, payload)
        if not silent:
            self.status_bar.showMessage(f"Saved 已保存: {jp}", 5000)
        self._save_project_state()

    def next_image(self) -> None:
        if not self.images:
            return
        self.save_current_labels(silent=False)
        self.state.index = min(self.state.index + 1, len(self.images) - 1)
        self._load_current_image()

    def prev_image(self) -> None:
        if not self.images:
            return
        self.save_current_labels(silent=False)
        self.state.index = max(self.state.index - 1, 0)
        self._load_current_image()