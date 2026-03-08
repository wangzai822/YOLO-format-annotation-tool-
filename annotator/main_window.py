from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
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
    DatasetState,
    ProjectState,
    dataset_state_path,
    labels_json_path,
    list_images,
    load_dataset_state,
    load_project_state,
    normalize_class_records,
    save_dataset_state,
    save_image_labels,
    save_project_state,
)
from .label_io import (
    EXPORT_FORMAT_COCO,
    EXPORT_FORMAT_INTERNAL_JSON,
    EXPORT_FORMAT_ULTRALYTICS_OBB,
    EXPORT_FORMAT_ULTRALYTICS_SEG,
    EXPORT_FORMAT_YOLO_BBOX,
    IMPORT_FORMAT_AUTO,
    IMPORT_FORMAT_OPTIONS,
    export_coco_dataset,
    export_ultralytics_obb_txt,
    export_ultralytics_seg_txt,
    export_yolo_bbox_txt,
    format_display_name,
    format_tooltip,
    load_best_label_doc,
)
from .shapes import BBoxItem, OBBItem, PolygonItem
from .undo import AddItemCommand, DeleteItemCommand, ModifyItemCommand

ANNOTATION_TYPES = (BBoxItem, OBBItem, PolygonItem)

# Must match shapes.py (used for Alt highlight overlay numbering).
DATA_SEQ = 9001
DATA_TYPE_SEQ = 9002


def dist(a, b) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1320, 860)

        self.undo_stack = QUndoStack(self)
        self.state = ProjectState()

        self.images: List[Path] = []
        self.input_dir: Optional[Path] = None
        self.label_dir: Optional[Path] = None
        self.output_dir: Optional[Path] = None

        self._uid_to_item: Dict[str, object] = {}
        self._uid_to_treeitem: Dict[str, QTreeWidgetItem] = {}
        self._classid_to_treeitem: Dict[int, QTreeWidgetItem] = {}

        self._create_counter = 0
        self._suspend_autosave = False
        self._alt_down = False

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(250)
        self._autosave_timer.timeout.connect(lambda: self.save_current_labels(silent=True))
        self.undo_stack.indexChanged.connect(self._queue_autosave)
        self.undo_stack.indexChanged.connect(self._on_undo_stack_index_changed)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.canvas = CanvasView(self)
        self.setCentralWidget(self.canvas)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.canvas.status.connect(self.status_bar.showMessage)

        self._build_actions()
        self._build_toolbar()
        self._build_docks()

        self.canvas.created_item.connect(self._on_created_item)
        self.canvas.scene.selectionChanged.connect(self._sync_ann_tree_from_scene)

        self._sync_path_edits()
        self._sync_format_widgets()
        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._update_window_title()

    # ---------- Global events ----------
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

    # ---------- Autosave / undo refresh ----------
    def _queue_autosave(self) -> None:
        if self._suspend_autosave:
            return
        if self.input_dir is None or self.output_dir is None or not self.images:
            return
        self._autosave_timer.start()

    def _on_undo_stack_index_changed(self, _index: int) -> None:
        if self._suspend_autosave:
            return
        self._refresh_ann_tree()

    # ---------- UI ----------
    def _build_actions(self) -> None:
        self.act_open_input = QAction("Open Image Folder... 打开图片文件夹...", self)
        self.act_open_input.triggered.connect(self.choose_input_dir)

        self.act_open_labels = QAction("Open Label Folder... 打开标注文件夹...", self)
        self.act_open_labels.triggered.connect(self.choose_label_dir)

        self.act_open_output = QAction("Set Output Folder... 设置输出目录...", self)
        self.act_open_output.triggered.connect(self.choose_output_dir)

        self.act_save = QAction("Save 保存", self)
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
        m_file.addAction(self.act_open_labels)
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
        tb.addAction(self.act_open_labels)
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
        self.setDockNestingEnabled(True)

        left_panel = QWidget(self)
        v = QVBoxLayout(left_panel)

        g_paths = QGroupBox("Paths 路径", left_panel)
        vp = QVBoxLayout(g_paths)

        self.in_edit = QLineEdit()
        self.in_edit.setReadOnly(True)
        btn_in = QPushButton("Browse Images... 选择图片目录")
        btn_in.clicked.connect(self.choose_input_dir)

        self.label_edit = QLineEdit()
        self.label_edit.setReadOnly(True)
        btn_label = QPushButton("Browse Labels... 选择标注目录")
        btn_label.clicked.connect(self.choose_label_dir)

        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        btn_out = QPushButton("Browse Output... 选择输出目录")
        btn_out.clicked.connect(self.choose_output_dir)

        vp.addWidget(QLabel("Images 输入图片:"))
        vp.addWidget(self.in_edit)
        vp.addWidget(btn_in)
        vp.addWidget(QLabel("Labels 输入标注(可选):"))
        vp.addWidget(self.label_edit)
        vp.addWidget(btn_label)
        vp.addWidget(QLabel("Output 输出目录:"))
        vp.addWidget(self.out_edit)
        vp.addWidget(btn_out)

        g_formats = QGroupBox("Formats 格式", left_panel)
        vf = QVBoxLayout(g_formats)

        import_row = QWidget(g_formats)
        hi = QHBoxLayout(import_row)
        hi.setContentsMargins(0, 0, 0, 0)
        hi.addWidget(QLabel("Import 导入格式:"))
        self.import_format_combo = QComboBox()
        for text, value in IMPORT_FORMAT_OPTIONS:
            self.import_format_combo.addItem(text, value)
            idx = self.import_format_combo.count() - 1
            self.import_format_combo.setItemData(idx, format_tooltip(value), Qt.ToolTipRole)
        self.import_format_combo.currentIndexChanged.connect(self._on_import_format_changed)
        hi.addWidget(self.import_format_combo, 1)
        vf.addWidget(import_row)

        fmt_tip = QLabel("Hover a format for details  悬停对应格式查看详细说明")
        fmt_tip.setWordWrap(True)
        vf.addWidget(fmt_tip)

        vf.addWidget(QLabel("Export 导出格式:"))

        self.chk_export_internal = QCheckBox("Workspace JSON 工作区JSON (always 总是保存)")
        self.chk_export_internal.setChecked(True)
        self.chk_export_internal.setEnabled(False)
        self.chk_export_internal.setToolTip(format_tooltip(EXPORT_FORMAT_INTERNAL_JSON))

        self.chk_export_yolo_bbox = QCheckBox("YOLO BBox TXT 检测框")
        self.chk_export_seg = QCheckBox("Ultralytics Seg TXT 多边形")
        self.chk_export_obb = QCheckBox("Ultralytics OBB TXT 旋转框")
        self.chk_export_coco = QCheckBox("COCO JSON (manual save / page change 手动保存/翻页时更新)")

        self.chk_export_yolo_bbox.setToolTip(format_tooltip(EXPORT_FORMAT_YOLO_BBOX))
        self.chk_export_seg.setToolTip(format_tooltip(EXPORT_FORMAT_ULTRALYTICS_SEG))
        self.chk_export_obb.setToolTip(format_tooltip(EXPORT_FORMAT_ULTRALYTICS_OBB))
        self.chk_export_coco.setToolTip(format_tooltip(EXPORT_FORMAT_COCO))

        self.chk_export_yolo_bbox.stateChanged.connect(self._on_export_formats_changed)
        self.chk_export_seg.stateChanged.connect(self._on_export_formats_changed)
        self.chk_export_obb.stateChanged.connect(self._on_export_formats_changed)
        self.chk_export_coco.stateChanged.connect(self._on_export_formats_changed)

        vf.addWidget(self.chk_export_internal)
        vf.addWidget(self.chk_export_yolo_bbox)
        vf.addWidget(self.chk_export_seg)
        vf.addWidget(self.chk_export_obb)
        vf.addWidget(self.chk_export_coco)

        g_classes = QGroupBox("Classes 类别", left_panel)
        vc = QVBoxLayout(g_classes)

        self.class_tree = QTreeWidget(self)
        self.class_tree.setHeaderHidden(True)
        self.class_tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.class_tree.itemSelectionChanged.connect(self._on_class_tree_selection_changed)
        vc.addWidget(self.class_tree)

        name_row = QWidget(g_classes)
        hn = QHBoxLayout(name_row)
        hn.setContentsMargins(0, 0, 0, 0)

        self.class_name_edit = QLineEdit()
        self.class_name_edit.setPlaceholderText("Class name 类别名称 (Enter / Apply 应用)")
        self.class_name_edit.returnPressed.connect(self._apply_class_name_from_edit)

        btn_apply_name = QPushButton("Apply 应用")
        btn_apply_name.clicked.connect(self._apply_class_name_from_edit)

        hn.addWidget(self.class_name_edit, 1)
        hn.addWidget(btn_apply_name)
        vc.addWidget(name_row)

        action_row = QWidget(g_classes)
        ha = QHBoxLayout(action_row)
        ha.setContentsMargins(0, 0, 0, 0)

        btn_add_class = QPushButton("Add 新增")
        btn_add_class.clicked.connect(self.add_class)

        btn_delete_class = QPushButton("Delete 删除")
        btn_delete_class.clicked.connect(self._delete_current_class)

        ha.addWidget(btn_add_class)
        ha.addWidget(btn_delete_class)
        vc.addWidget(action_row)

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
        v.addWidget(g_formats)
        v.addWidget(g_classes)
        v.addWidget(g_nav)
        v.addWidget(g_tools)
        v.addStretch(1)

        dock_left = QDockWidget("Project 项目", self)
        dock_left.setWidget(left_panel)
        dock_left.setMinimumWidth(340)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock_left)

        self.ann_tree = QTreeWidget(self)
        self.ann_tree.setHeaderHidden(True)
        self.ann_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.ann_tree.itemSelectionChanged.connect(self._on_ann_tree_selection)
        self.ann_tree.itemChanged.connect(self._on_ann_tree_item_changed)

        dock_ann = QDockWidget("Annotations 标注", self)
        dock_ann.setWidget(self.ann_tree)
        dock_ann.setMinimumWidth(320)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_ann)

        help_text = QLabel(
            "Shortcuts 快捷键:\n"
            "  1..9 选择前9个已添加类别\n"
            "  V Select 选择\n"
            "  B BBox 轴对齐框\n"
            "  O OBB 旋转框\n"
            "  M Mask 多边形 (Enter/RightClick 右键结束)\n"
            "  Ctrl+S Save 保存\n"
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
            "  图片目录 / 标注目录 / 输出目录可分别设置\n"
            "  类别按数据集分别保存，切换数据集时不会残留上一个数据集的类别\n"
            "  外部标注目录支持相对路径同名匹配，也支持扁平同名匹配\n"
            "  读取顺序: 优先读取输出目录中的工作区JSON, 若不存在再读取外部标注目录\n"
            "  导出支持: 工作区JSON + 可选 YOLO / Seg / OBB / COCO\n"
            "  COCO 为整数据集导出, 在手动保存或翻页时整体更新\n"
            "  Formats 区域支持鼠标悬停查看详细格式说明\n"
            "  右侧树的勾选框: 控制该标注/该类型在画布上显示或隐藏\n"
            "  已保存标注: Add/Modify/Delete/Undo/Redo 自动更新工作区JSON\n"
        )
        help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_text.setWordWrap(True)
        help_text.setContentsMargins(10, 10, 10, 10)

        dock_help = QDockWidget("Help 帮助", self)
        dock_help.setWidget(help_text)
        dock_help.setMinimumHeight(220)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_help)

        # Put help under annotation tree so it no longer squeezes/overlaps the left bottom area.
        self.splitDockWidget(dock_ann, dock_help, Qt.Vertical)
        self.resizeDocks([dock_left, dock_ann], [360, 340], Qt.Horizontal)
        self.resizeDocks([dock_ann, dock_help], [430, 240], Qt.Vertical)

    # ---------- Paths / dataset state ----------
    def _sync_path_edits(self) -> None:
        if hasattr(self, "in_edit"):
            self.in_edit.setText(str(self.input_dir or ""))
        if hasattr(self, "label_edit"):
            self.label_edit.setText(str(self.label_dir or ""))
        if hasattr(self, "out_edit"):
            self.out_edit.setText(str(self.output_dir or ""))

    def _update_import_format_tooltip(self) -> None:
        if not hasattr(self, "import_format_combo"):
            return
        fmt = str(self.import_format_combo.currentData() or IMPORT_FORMAT_AUTO)
        self.import_format_combo.setToolTip(format_tooltip(fmt))

    def _sync_format_widgets(self) -> None:
        if hasattr(self, "import_format_combo"):
            idx = self.import_format_combo.findData(self.state.import_label_format)
            if idx < 0:
                idx = self.import_format_combo.findData(IMPORT_FORMAT_AUTO)
            self.import_format_combo.blockSignals(True)
            self.import_format_combo.setCurrentIndex(max(0, idx))
            self.import_format_combo.blockSignals(False)
            self._update_import_format_tooltip()

        selected = set(self.state.export_formats or [EXPORT_FORMAT_INTERNAL_JSON])

        if hasattr(self, "chk_export_yolo_bbox"):
            self.chk_export_yolo_bbox.blockSignals(True)
            self.chk_export_yolo_bbox.setChecked(EXPORT_FORMAT_YOLO_BBOX in selected)
            self.chk_export_yolo_bbox.blockSignals(False)

        if hasattr(self, "chk_export_seg"):
            self.chk_export_seg.blockSignals(True)
            self.chk_export_seg.setChecked(EXPORT_FORMAT_ULTRALYTICS_SEG in selected)
            self.chk_export_seg.blockSignals(False)

        if hasattr(self, "chk_export_obb"):
            self.chk_export_obb.blockSignals(True)
            self.chk_export_obb.setChecked(EXPORT_FORMAT_ULTRALYTICS_OBB in selected)
            self.chk_export_obb.blockSignals(False)

        if hasattr(self, "chk_export_coco"):
            self.chk_export_coco.blockSignals(True)
            self.chk_export_coco.setChecked(EXPORT_FORMAT_COCO in selected)
            self.chk_export_coco.blockSignals(False)

    def _selected_export_formats(self) -> List[str]:
        out = [EXPORT_FORMAT_INTERNAL_JSON]
        if hasattr(self, "chk_export_yolo_bbox") and self.chk_export_yolo_bbox.isChecked():
            out.append(EXPORT_FORMAT_YOLO_BBOX)
        if hasattr(self, "chk_export_seg") and self.chk_export_seg.isChecked():
            out.append(EXPORT_FORMAT_ULTRALYTICS_SEG)
        if hasattr(self, "chk_export_obb") and self.chk_export_obb.isChecked():
            out.append(EXPORT_FORMAT_ULTRALYTICS_OBB)
        if hasattr(self, "chk_export_coco") and self.chk_export_coco.isChecked():
            out.append(EXPORT_FORMAT_COCO)
        return out

    def _clear_active_dataset_state(self, clear_label_dir: bool = True) -> None:
        self.state.classes = []
        self.state.index = 0
        if clear_label_dir:
            self.label_dir = None
        self.canvas.set_current_class(0)

    def _load_dataset_state_for_current_input(self, reset_if_missing: bool = True) -> None:
        if self.input_dir is None or self.output_dir is None:
            if reset_if_missing:
                self._clear_active_dataset_state(clear_label_dir=True)
            return

        ds_path = dataset_state_path(self.output_dir, self.input_dir, create_parent=False)
        ds = load_dataset_state(ds_path)

        if ds is None:
            if reset_if_missing:
                self._clear_active_dataset_state(clear_label_dir=True)
            return

        self.state.classes = normalize_class_records(ds.classes)
        self.state.index = int(ds.index or 0)

        if ds.label_dir:
            p = Path(ds.label_dir)
            self.label_dir = p if p.exists() else None
        elif reset_if_missing:
            self.label_dir = None

        if ds.import_label_format:
            self.state.import_label_format = ds.import_label_format

        classes = self._sorted_class_records()
        if classes:
            valid_ids = [int(rec.get("id", 0)) for rec in classes]
            if int(self.canvas.current_class_id or 0) not in valid_ids:
                self.canvas.set_current_class(valid_ids[0])
        else:
            self.canvas.set_current_class(0)

    def _save_active_dataset_state(self) -> None:
        if self.output_dir is None or self.input_dir is None:
            return

        ds = DatasetState(
            input_dir=str(self.input_dir),
            label_dir=str(self.label_dir or ""),
            classes=normalize_class_records(self.state.classes),
            index=int(self.state.index or 0),
            import_label_format=str(self.state.import_label_format or IMPORT_FORMAT_AUTO),
        )
        save_dataset_state(dataset_state_path(self.output_dir, self.input_dir), ds)

    def _on_import_format_changed(self) -> None:
        self.state.import_label_format = str(self.import_format_combo.currentData() or IMPORT_FORMAT_AUTO)
        self._update_import_format_tooltip()
        self._save_project_state()
        self._reload_current_image()

    def _on_export_formats_changed(self) -> None:
        self.state.export_formats = self._selected_export_formats()
        self._save_project_state()

    def _update_window_title(self) -> None:
        idx = self.state.index + 1 if self.images else 0
        total = len(self.images)
        in_s = str(self.input_dir) if self.input_dir else "(no images 无图片)"
        label_s = str(self.label_dir) if self.label_dir else "(no labels 无外部标注)"
        out_s = str(self.output_dir) if self.output_dir else "(no output 无输出)"
        self.setWindowTitle(
            f"Annotator 标注工具 | {idx}/{total} | images={in_s} | labels={label_s} | out={out_s}"
        )

    def choose_input_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select image folder 选择图片目录")
        if not d:
            return

        new_input = Path(d)
        keep_index = self.input_dir is not None and new_input == self.input_dir

        # Save old dataset state before switching to another dataset.
        if self.output_dir is not None and self.input_dir is not None and self.input_dir != new_input:
            self._save_project_state()

        self.input_dir = new_input
        self.images = list_images(self.input_dir)

        if not self.images:
            QMessageBox.warning(self, "No images 没有图片", "No supported images found. 未找到支持的图片格式。")
            self._sync_path_edits()
            self._update_window_title()
            return

        if keep_index:
            self.state.index = max(0, min(self.state.index, len(self.images) - 1))
        else:
            self._load_dataset_state_for_current_input(reset_if_missing=True)
            self.state.index = max(0, min(self.state.index, len(self.images) - 1))

        self._sync_path_edits()
        self._sync_format_widgets()
        self._save_project_state()
        self._load_current_image()

    def choose_label_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select label folder 选择标注目录")
        if not d:
            return
        self.label_dir = Path(d)
        self._sync_path_edits()
        self._save_project_state()
        self._reload_current_image()

    def choose_output_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder 选择输出目录")
        if not d:
            return

        self.output_dir = Path(d)
        st_path = self.output_dir / "project_state.json"
        st = load_project_state(st_path)

        # Keep current in-memory classes if user already picked an input folder in this session.
        if self.input_dir is None:
            self.state.classes = normalize_class_records(st.classes)

        self.state.import_label_format = st.import_label_format
        self.state.export_formats = st.export_formats

        if self.input_dir is None and st.input_dir:
            p = Path(st.input_dir)
            if p.exists():
                self.input_dir = p

        if self.label_dir is None and st.label_dir:
            p = Path(st.label_dir)
            if p.exists():
                self.label_dir = p

        if self.input_dir is not None and self.input_dir.exists():
            self.images = list_images(self.input_dir)
            if self.images:
                self.state.index = max(0, min(st.index, len(self.images) - 1))

        # If a dataset-specific state exists, use it to replace global classes.
        if self.input_dir is not None:
            self._load_dataset_state_for_current_input(reset_if_missing=False)
            if self.images:
                self.state.index = max(0, min(self.state.index, len(self.images) - 1))

        self._sync_path_edits()
        self._sync_format_widgets()
        self._refresh_class_tree()
        self._save_project_state()

        if self.images:
            self._load_current_image()
        else:
            self._refresh_ann_tree()
            self._update_window_title()

    def _save_project_state(self) -> None:
        if self.output_dir is None:
            return

        self.state.input_dir = str(self.input_dir or "")
        self.state.label_dir = str(self.label_dir or "")
        self.state.output_dir = str(self.output_dir or "")
        self.state.export_formats = self._selected_export_formats()

        st_path = self.output_dir / "project_state.json"
        save_project_state(st_path, self.state)
        self._save_active_dataset_state()

    def _reload_current_image(self) -> None:
        if self.images:
            self._load_current_image()
        else:
            self._refresh_ann_tree()
            self._update_window_title()

    # ---------- Classes ----------
    def _sorted_class_records(self) -> List[Dict[str, object]]:
        recs = normalize_class_records(self.state.classes)
        recs.sort(key=lambda x: int(x.get("id", 0)))
        return recs

    def _class_lookup(self) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for rec in self._sorted_class_records():
            try:
                cid = int(rec.get("id", 0))
            except Exception:
                continue
            out[cid] = str(rec.get("name", "") or "")
        return out

    def _find_class_record(self, class_id: int) -> Optional[Dict[str, object]]:
        for rec in self.state.classes:
            try:
                if int(rec.get("id", -1)) == int(class_id):
                    return rec
            except Exception:
                continue
        return None

    def _ensure_class_record(self, class_id: int, name: str = "") -> None:
        class_id = int(class_id)
        rec = self._find_class_record(class_id)
        if rec is None:
            self.state.classes.append({"id": class_id, "name": str(name or "")})
            return
        if name and not str(rec.get("name", "") or "").strip():
            rec["name"] = str(name)

    def _merge_class_records(self, records: List[Dict[str, object]]) -> None:
        for rec in normalize_class_records(records):
            cid = int(rec.get("id", 0))
            name = str(rec.get("name", "") or "")
            self._ensure_class_record(cid, name)

    def _next_available_class_id(self) -> int:
        used = {int(rec.get("id", 0)) for rec in self._sorted_class_records()}
        cid = 0
        while cid in used:
            cid += 1
        return cid

    def _class_label(self, class_id: int) -> str:
        name = self._class_lookup().get(int(class_id), "")
        if str(name or "").strip():
            return f"ID {class_id}: {name}"
        return f"ID {class_id}: (unnamed 未命名)"

    def _refresh_class_tree(self) -> None:
        self._classid_to_treeitem.clear()
        self.class_tree.blockSignals(True)
        self.class_tree.clear()

        classes = self._sorted_class_records()
        current_id = int(self.canvas.current_class_id or 0)

        if classes:
            ids = [int(rec.get("id", 0)) for rec in classes]
            if current_id not in ids:
                current_id = ids[0]
                self.canvas.set_current_class(current_id)

            target_item: Optional[QTreeWidgetItem] = None
            for idx, rec in enumerate(classes, start=1):
                cid = int(rec.get("id", 0))
                name = str(rec.get("name", "") or "")
                text = f"{idx}. ID {cid}: {name}" if name else f"{idx}. ID {cid}: (unnamed 未命名)"
                item = QTreeWidgetItem([text])
                item.setData(0, Qt.UserRole, cid)
                self.class_tree.addTopLevelItem(item)
                self._classid_to_treeitem[cid] = item
                if cid == current_id:
                    target_item = item

            if target_item is not None:
                self.class_tree.setCurrentItem(target_item)
                target_item.setSelected(True)

            cur_rec = self._find_class_record(current_id)
            self.class_name_edit.setText(str(cur_rec.get("name", "") if cur_rec else ""))
        else:
            tip = QTreeWidgetItem(["(No classes yet)  还没有类别"])
            tip.setFlags(Qt.ItemIsEnabled)
            self.class_tree.addTopLevelItem(tip)
            self.class_name_edit.clear()

        self.class_tree.blockSignals(False)

    def _on_class_tree_selection_changed(self) -> None:
        items = self.class_tree.selectedItems()
        if not items:
            return
        item = items[0]
        cid = item.data(0, Qt.UserRole)
        if not isinstance(cid, int):
            return
        self.canvas.set_current_class(cid)
        rec = self._find_class_record(cid)
        self.class_name_edit.setText(str(rec.get("name", "") if rec else ""))
        self.status_bar.showMessage(f"Class 类别: {self._class_label(cid)}", 2500)

    def _select_class_by_id(self, class_id: int) -> None:
        self._refresh_class_tree()
        item = self._classid_to_treeitem.get(int(class_id))
        if item is not None:
            self.class_tree.setCurrentItem(item)
            item.setSelected(True)

    def add_class(self) -> None:
        next_id = self._next_available_class_id()
        name, ok = QInputDialog.getText(
            self,
            "Add class 新增类别",
            f"Class name 类别名称 (ID {next_id}):",
            text="",
        )
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return

        self._ensure_class_record(next_id, name)
        self.canvas.set_current_class(next_id)
        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._save_project_state()
        self.status_bar.showMessage(f"Added class 已新增类别: {self._class_label(next_id)}", 3000)

    def rename_class(self) -> None:
        cid = int(self.canvas.current_class_id or 0)
        rec = self._find_class_record(cid)
        if rec is None:
            QMessageBox.information(self, "No class 无类别", "Please select a class first.\n请先选择一个类别。")
            return

        cur = str(rec.get("name", "") or "")
        name, ok = QInputDialog.getText(
            self,
            "Rename class 重命名类别",
            f"Class name 类别名称 (ID {cid}):",
            text=cur,
        )
        if not ok:
            return

        rec["name"] = (name or "").strip()
        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._save_project_state()
        self._queue_autosave()

    def _apply_class_name_from_edit(self) -> None:
        cid = int(self.canvas.current_class_id or 0)
        rec = self._find_class_record(cid)
        if rec is None:
            QMessageBox.information(self, "No class 无类别", "Please add/select a class first.\n请先新增或选择一个类别。")
            return

        rec["name"] = (self.class_name_edit.text() or "").strip()
        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._save_project_state()
        self._queue_autosave()

    def _delete_current_class(self) -> None:
        cid = int(self.canvas.current_class_id or 0)
        rec = self._find_class_record(cid)
        if rec is None:
            return

        in_use = 0
        for it in self.canvas.scene.items():
            if isinstance(it, ANNOTATION_TYPES) and int(getattr(it, "class_id", -1)) == cid:
                in_use += 1

        if in_use > 0:
            QMessageBox.warning(
                self,
                "Class in use 类别仍在使用",
                f"Class ID {cid} is used by {in_use} annotation(s).\n"
                f"当前类别 ID {cid} 仍被 {in_use} 个标注使用，不能删除。",
            )
            return

        self.state.classes = [r for r in self.state.classes if int(r.get("id", -1)) != cid]
        classes = self._sorted_class_records()
        if classes:
            self.canvas.set_current_class(int(classes[0].get("id", 0)))
        else:
            self.canvas.set_current_class(0)
        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._save_project_state()

    def keyPressEvent(self, event) -> None:
        if hasattr(self, "class_name_edit") and self.class_name_edit.hasFocus():
            super().keyPressEvent(event)
            return

        k = event.key()
        if Qt.Key_1 <= k <= Qt.Key_9:
            idx = int(k - Qt.Key_1)
            classes = self._sorted_class_records()
            if 0 <= idx < len(classes):
                cid = int(classes[idx].get("id", 0))
                self.canvas.set_current_class(cid)
                self._select_class_by_id(cid)
                event.accept()
                return

        super().keyPressEvent(event)

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

    def _iter_ann_items(self):
        items = [it for it in self.canvas.scene.items() if isinstance(it, ANNOTATION_TYPES)]
        items.sort(key=lambda x: int(getattr(x, "created_index", 0) or 0))
        return items

    def _update_item_display_numbers(self, items) -> None:
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

    def _merge_classes_from_doc(self, doc: Optional[dict]) -> None:
        if not doc:
            return

        self._merge_class_records(doc.get("classes", []))

        for ann in doc.get("annotations", []):
            try:
                cid = int(ann.get("class_id", 0))
            except Exception:
                cid = 0
            self._ensure_class_record(cid, "")

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

        self.undo_stack.clear()
        self.canvas.load_image(pix)
        self.canvas.scene.setProperty("image_name", img_path.stem)
        self.canvas.fit_to_view()

        self._create_counter = 0
        self._load_existing_labels(img_path)

        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._save_project_state()
        self._sync_path_edits()
        self._sync_format_widgets()
        self._update_window_title()

        self._suspend_autosave = False

    def _load_existing_labels(self, img_path: Path) -> None:
        doc = load_best_label_doc(
            output_dir=self.output_dir,
            label_dir=self.label_dir,
            input_dir=self.input_dir,
            img_path=img_path,
            import_format=self.state.import_label_format,
        )

        if not doc:
            return

        self._merge_classes_from_doc(doc)

        anns = doc.get("annotations", [])
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

        fmt = str(doc.get("format", ""))
        src = doc.get("source_path")
        src_text = str(src) if src else ""
        self.status_bar.showMessage(f"Loaded labels 已加载标注: {format_display_name(fmt)} | {src_text}", 5000)

    # ---------- Annotation tree ----------
    def _refresh_ann_tree(self) -> None:
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

                leaf = QTreeWidgetItem([f"{idx}. {self._class_label(cls)}"])
                leaf.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                leaf.setData(0, Qt.UserRole, uid)
                leaf.setCheckState(0, Qt.Checked if it.isVisible() else Qt.Unchecked)

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
            if isinstance(it, ANNOTATION_TYPES):
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

    # ---------- Create / delete / save ----------
    def _on_created_item(self, item) -> None:
        self._ensure_class_record(int(getattr(item, "class_id", 0)), "")
        self._assign_created_index(item, None)
        self._connect_item_signals(item)
        self.undo_stack.push(AddItemCommand(self.canvas.scene, item, "Add annotation 新增标注"))
        item.setSelected(True)
        self._refresh_class_tree()
        self._refresh_ann_tree()
        self._save_project_state()

    def delete_selected_items(self) -> None:
        items = [it for it in self.canvas.scene.selectedItems() if isinstance(it, ANNOTATION_TYPES)]
        if not items:
            return
        for it in items:
            self.undo_stack.push(DeleteItemCommand(self.canvas.scene, it, "Delete annotation 删除标注"))
        self._refresh_ann_tree()

    def _build_annotation_payload(self) -> Tuple[List[Dict[str, object]], Dict[str, List[str]]]:
        annotations: List[Dict[str, object]] = []
        rows = {"bbox": [], "obb": [], "polygon": []}
        type_counters = {"bbox": 0, "obb": 0, "polygon": 0}

        items = list(self._iter_ann_items())
        self._update_item_display_numbers(items)

        for it in items:
            self._ensure_class_record(int(getattr(it, "class_id", 0)), "")

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

        return annotations, rows

    def _classes_payload_for_save(self, annotations: List[Dict[str, object]]) -> List[Dict[str, object]]:
        used_ids = {int(a.get("class_id", 0)) for a in annotations}
        lookup = self._class_lookup()

        payload: List[Dict[str, object]] = []
        existing = set()

        for rec in self._sorted_class_records():
            cid = int(rec.get("id", 0))
            name = str(rec.get("name", "") or "")
            if name.strip() or cid in used_ids:
                payload.append({"id": cid, "name": name if name.strip() else f"class_{cid}"})
                existing.add(cid)

        for cid in sorted(used_ids):
            if cid not in existing:
                payload.append({"id": cid, "name": lookup.get(cid, "") or f"class_{cid}"})

        payload.sort(key=lambda x: int(x.get("id", 0)))
        return payload

    def _export_selected_sidecar_formats(
        self,
        img_path: Path,
        annotations: List[Dict[str, object]],
        silent: bool,
        update_dataset_exports: bool,
    ) -> None:
        if self.output_dir is None or self.input_dir is None:
            return

        selected = set(self._selected_export_formats())

        if EXPORT_FORMAT_YOLO_BBOX in selected:
            export_yolo_bbox_txt(self.output_dir, self.input_dir, img_path, annotations)

        if EXPORT_FORMAT_ULTRALYTICS_SEG in selected:
            export_ultralytics_seg_txt(self.output_dir, self.input_dir, img_path, annotations)

        if EXPORT_FORMAT_ULTRALYTICS_OBB in selected:
            export_ultralytics_obb_txt(self.output_dir, self.input_dir, img_path, annotations)

        if EXPORT_FORMAT_COCO in selected and update_dataset_exports:
            export_coco_dataset(
                output_dir=self.output_dir,
                input_dir=self.input_dir,
                image_paths=self.images,
                class_lookup=self._class_lookup(),
                label_dir=self.label_dir,
                import_format=self.state.import_label_format,
            )
            if not silent:
                self.status_bar.showMessage("COCO JSON 已更新.", 3000)

    def save_current_labels(self, silent: bool = False, update_dataset_exports: Optional[bool] = None) -> None:
        if update_dataset_exports is None:
            update_dataset_exports = not silent

        if self.input_dir is None or self.output_dir is None or not self.images:
            if not silent:
                QMessageBox.information(
                    self,
                    "Missing paths 缺少路径",
                    "Please set image folder and output folder first.\n请先设置图片目录和输出目录。",
                )
            return

        img_path = self.images[self.state.index]
        jp = labels_json_path(self.output_dir, self.input_dir, img_path)

        annotations, rows = self._build_annotation_payload()
        classes_payload = self._classes_payload_for_save(annotations)

        payload = {
            "schema": "ultralytics-json-v1",
            "image": {"file_name": img_path.name, "width": self.canvas.img_w, "height": self.canvas.img_h},
            "classes": classes_payload,
            "annotations": annotations,
            "ultralytics_rows": rows,
            "note": "JSON-only workspace export. Additional sidecar formats can be written if enabled.",
        }

        save_image_labels(jp, payload)
        self._export_selected_sidecar_formats(img_path, annotations, silent, bool(update_dataset_exports))
        self._save_project_state()

        if not silent:
            self.status_bar.showMessage(f"Saved 已保存: {jp}", 5000)

    def next_image(self) -> None:
        if not self.images:
            return
        self.save_current_labels(silent=False, update_dataset_exports=True)
        self.state.index = min(self.state.index + 1, len(self.images) - 1)
        self._load_current_image()

    def prev_image(self) -> None:
        if not self.images:
            return
        self.save_current_labels(silent=False, update_dataset_exports=True)
        self.state.index = max(self.state.index - 1, 0)
        self._load_current_image()