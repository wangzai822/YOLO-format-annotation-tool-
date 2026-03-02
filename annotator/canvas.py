# from __future__ import annotations

# from typing import List, Optional

# from PySide6.QtCore import QPointF, Qt, Signal
# from PySide6.QtGui import QKeyEvent, QPainter, QPainterPath, QPen, QPixmap
# from PySide6.QtWidgets import (
#     QGraphicsPathItem,
#     QGraphicsPixmapItem,
#     QGraphicsScene,
#     QGraphicsView,
# )

# from .shapes import BBoxItem, OBBItem, PolygonItem


# class ToolMode:
#     SELECT = "select"
#     BBOX = "bbox"
#     OBB = "obb"
#     POLY = "poly"


# class CanvasView(QGraphicsView):
#     status = Signal(str)
#     created_item = Signal(object)

#     def __init__(self, parent=None):
#         super().__init__(parent)
#         self.setRenderHints(
#             QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform
#         )
#         self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
#         self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
#         self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)

#         self.scene = QGraphicsScene(self)
#         self.setScene(self.scene)

#         self.pix_item: Optional[QGraphicsPixmapItem] = None
#         self.img_w = 0
#         self.img_h = 0

#         self.mode = ToolMode.SELECT
#         self.current_class_id = 0

#         self._space_panning = False
#         self._pan_last: Optional[QPointF] = None

#         self._draw_start: Optional[QPointF] = None
#         self._preview_path: Optional[QGraphicsPathItem] = None

#         self._poly_points: List[QPointF] = []
#         self._poly_preview: Optional[QGraphicsPathItem] = None

#         self.set_mode(ToolMode.SELECT)

#     def _mode_hint(self, mode: str) -> str:
#         if mode == ToolMode.SELECT:
#             return "Tool 工具: select  (双击标注进入编辑; 框: Ctrl+拖动缩放; Mask: 顶点编辑模式拖点)"
#         if mode in (ToolMode.BBOX, ToolMode.OBB):
#             return "Tool 工具: rect  (拖拽绘制; 右键取消/退出; 双击标注进入编辑)"
#         if mode == ToolMode.POLY:
#             return "Tool 工具: poly  (左键加点; Enter/右键完成; 双击已有Mask进入顶点编辑)"
#         return f"Tool 工具: {mode}"

#     def set_mode(self, mode: str) -> None:
#         self.mode = mode
#         self._cancel_in_progress()
#         if mode == ToolMode.SELECT:
#             self.setDragMode(QGraphicsView.RubberBandDrag)
#         else:
#             self.setDragMode(QGraphicsView.NoDrag)
#         self.status.emit(self._mode_hint(mode))

#     def set_current_class(self, class_id: int) -> None:
#         self.current_class_id = int(class_id)
#         self.status.emit(f"Class 类别: {self.current_class_id}")

#     def load_image(self, pix: QPixmap) -> None:
#         self.scene.clear()
#         self.pix_item = QGraphicsPixmapItem(pix)
#         self.pix_item.setZValue(-1000)
#         self.scene.addItem(self.pix_item)
#         self.img_w = pix.width()
#         self.img_h = pix.height()
#         self.scene.setSceneRect(0, 0, self.img_w, self.img_h)
#         self._cancel_in_progress()

#     def fit_to_view(self) -> None:
#         if self.pix_item is None:
#             return
#         self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

#     def wheelEvent(self, event) -> None:
#         factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
#         self.scale(factor, factor)

#     def keyPressEvent(self, event: QKeyEvent) -> None:
#         if event.key() == Qt.Key_Escape:
#             self._cancel_in_progress()
#             # Also exit polygon vertex edit mode(s).
#             self._set_all_polygon_vertex_edit(False)
#             self.status.emit("Canceled 取消.")
#             event.accept()
#             return

#         if self.mode == ToolMode.POLY and event.key() in (Qt.Key_Return, Qt.Key_Enter):
#             self._finish_polygon()
#             event.accept()
#             return

#         if event.key() == Qt.Key_Space:
#             self._space_panning = True
#             self.setCursor(Qt.ClosedHandCursor)
#             event.accept()
#             return

#         super().keyPressEvent(event)

#     def keyReleaseEvent(self, event: QKeyEvent) -> None:
#         if event.key() == Qt.Key_Space:
#             self._space_panning = False
#             self._pan_last = None
#             self.unsetCursor()
#             event.accept()
#             return
#         super().keyReleaseEvent(event)

#     def _shape_item_at(self, view_pos) -> Optional[object]:
#         hit = self.itemAt(view_pos)
#         it = hit
#         while it is not None and not isinstance(it, (BBoxItem, OBBItem, PolygonItem)):
#             it = it.parentItem()
#         if isinstance(it, (BBoxItem, OBBItem, PolygonItem)):
#             return it
#         return None

#     def _set_all_polygon_vertex_edit(self, on: bool) -> None:
#         for it in self.scene.items():
#             if isinstance(it, PolygonItem):
#                 try:
#                     it.set_vertex_edit_mode(bool(on))
#                 except Exception:
#                     pass

#     def mouseDoubleClickEvent(self, event) -> None:
#         if self.pix_item is None:
#             return

#         if event.button() != Qt.LeftButton:
#             super().mouseDoubleClickEvent(event)
#             return

#         # Don't disrupt in-progress drawing.
#         if self._draw_start is not None or self._poly_points:
#             super().mouseDoubleClickEvent(event)
#             return

#         shp = self._shape_item_at(event.position().toPoint())
#         if shp is None:
#             # Double click empty area: exit polygon vertex edit mode.
#             self._set_all_polygon_vertex_edit(False)
#             super().mouseDoubleClickEvent(event)
#             return

#         # Double click enters edit: switch to select + select item.
#         if self.mode != ToolMode.SELECT:
#             self.set_mode(ToolMode.SELECT)

#         try:
#             self.scene.clearSelection()
#             if getattr(shp, "isVisible", lambda: True)():
#                 shp.setSelected(True)
#         except Exception:
#             pass

#         # Polygon: toggle vertex edit mode on double click.
#         if isinstance(shp, PolygonItem):
#             # Turn off all other polygons first (keeps UI predictable).
#             for it in self.scene.items():
#                 if isinstance(it, PolygonItem) and it is not shp:
#                     try:
#                         it.set_vertex_edit_mode(False)
#                     except Exception:
#                         pass
#             try:
#                 shp.set_vertex_edit_mode(not shp.vertex_edit_mode())
#             except Exception:
#                 pass

#             if shp.vertex_edit_mode():
#                 self.status.emit("Mask 顶点编辑: 拖动任意顶点调整形状 (双击退出; Esc 退出)")
#             else:
#                 self.status.emit("Mask: 已选中 (可拖动移动整个Mask; 双击进入顶点编辑)")
#             event.accept()
#             return

#         # BBox/OBB: just select (resize behavior is handled by item itself with Ctrl, in SELECT mode).
#         self._set_all_polygon_vertex_edit(False)
#         self.status.emit("框编辑: 拖动移动; 按住Ctrl在框内拖动缩放 (无需点角点)")
#         event.accept()

#     def mousePressEvent(self, event) -> None:
#         if self.pix_item is None:
#             return

#         # Space+Drag for panning
#         if self._space_panning and event.button() == Qt.LeftButton:
#             self._pan_last = event.position()
#             event.accept()
#             return

#         # Right click: finish/cancel/end current annotation action.
#         if event.button() == Qt.RightButton:
#             if self.mode == ToolMode.POLY and self._poly_points:
#                 self._finish_polygon()
#                 event.accept()
#                 return

#             if self._draw_start is not None or self._poly_points or self._preview_path is not None:
#                 self._cancel_in_progress()
#                 self.status.emit("Canceled 取消.")
#                 event.accept()
#                 return

#             if self.mode != ToolMode.SELECT:
#                 self.set_mode(ToolMode.SELECT)
#                 event.accept()
#                 return

#             super().mousePressEvent(event)
#             return

#         # In draw modes: prevent existing shapes from being clicked/selected (avoid conflicts).
#         # Resize is only needed in SELECT mode, so we always block hits on existing shapes here.
#         if event.button() == Qt.LeftButton and self.mode != ToolMode.SELECT:
#             shp = self._shape_item_at(event.position().toPoint())
#             if shp is not None:
#                 self.status.emit("提示: 当前为绘制模式。双击标注进入编辑(自动切到select后可Ctrl缩放)。")
#                 event.accept()
#                 return

#         # In select mode: if click empty area, exit polygon vertex edit mode (so move-select feels normal).
#         if event.button() == Qt.LeftButton and self.mode == ToolMode.SELECT:
#             shp = self._shape_item_at(event.position().toPoint())
#             if shp is None:
#                 self._set_all_polygon_vertex_edit(False)

#         sp = self.mapToScene(event.position().toPoint())

#         # Start drawing rect (BBox/OBB)
#         if self.mode in (ToolMode.BBOX, ToolMode.OBB) and event.button() == Qt.LeftButton:
#             self._draw_start = sp
#             self._start_rect_preview()
#             self._update_rect_preview(sp, sp)
#             event.accept()
#             return

#         # Polygon drawing: left adds points
#         if self.mode == ToolMode.POLY and event.button() == Qt.LeftButton:
#             self._poly_points.append(sp)
#             self._update_poly_preview(sp)
#             event.accept()
#             return

#         super().mousePressEvent(event)

#     def mouseMoveEvent(self, event) -> None:
#         if self._space_panning and self._pan_last is not None:
#             delta = event.position() - self._pan_last
#             self._pan_last = event.position()
#             self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
#             self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
#             event.accept()
#             return

#         sp = self.mapToScene(event.position().toPoint())

#         if self._draw_start is not None and self._preview_path is not None:
#             self._update_rect_preview(self._draw_start, sp)
#             event.accept()
#             return

#         if self.mode == ToolMode.POLY and self._poly_preview is not None and self._poly_points:
#             self._update_poly_preview(sp)
#             event.accept()
#             return

#         super().mouseMoveEvent(event)

#     def mouseReleaseEvent(self, event) -> None:
#         if self._space_panning and event.button() == Qt.LeftButton:
#             self._pan_last = None
#             event.accept()
#             return

#         if self._draw_start is not None and event.button() == Qt.LeftButton:
#             end = self.mapToScene(event.position().toPoint())
#             self._commit_rect(self._draw_start, end)
#             self._draw_start = None
#             self._clear_preview()
#             event.accept()
#             return

#         super().mouseReleaseEvent(event)

#     def _start_rect_preview(self) -> None:
#         self._clear_preview()
#         self._preview_path = QGraphicsPathItem()
#         pen = QPen(Qt.yellow, 2.0)
#         pen.setCosmetic(True)
#         self._preview_path.setPen(pen)
#         self.scene.addItem(self._preview_path)

#     def _update_rect_preview(self, a: QPointF, b: QPointF) -> None:
#         if self._preview_path is None:
#             return
#         x1, y1 = a.x(), a.y()
#         x2, y2 = b.x(), b.y()
#         left, right = (x1, x2) if x1 <= x2 else (x2, x1)
#         top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)

#         path = QPainterPath()
#         path.addRect(left, top, max(1.0, right - left), max(1.0, bottom - top))
#         self._preview_path.setPath(path)

#     def _commit_rect(self, a: QPointF, b: QPointF) -> None:
#         x1, y1 = a.x(), a.y()
#         x2, y2 = b.x(), b.y()
#         left, right = (x1, x2) if x1 <= x2 else (x2, x1)
#         top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
#         w = max(2.0, right - left)
#         h = max(2.0, bottom - top)
#         cx = left + w / 2.0
#         cy = top + h / 2.0

#         if self.mode == ToolMode.BBOX:
#             item = BBoxItem(w, h, class_id=self.current_class_id)
#             item.setPos(QPointF(cx, cy))
#             item.setRotation(0.0)
#             self.created_item.emit(item)
#             return

#         if self.mode == ToolMode.OBB:
#             item = OBBItem(w, h, class_id=self.current_class_id)
#             item.setPos(QPointF(cx, cy))
#             item.setRotation(0.0)
#             self.created_item.emit(item)
#             return

#     def _update_poly_preview(self, cursor: QPointF) -> None:
#         if self._poly_preview is None:
#             self._poly_preview = QGraphicsPathItem()
#             pen = QPen(Qt.yellow, 2.0)
#             pen.setCosmetic(True)
#             self._poly_preview.setPen(pen)
#             self.scene.addItem(self._poly_preview)

#         pts = self._poly_points[:]
#         if pts:
#             pts2 = pts + [cursor]
#             path = QPainterPath()
#             path.moveTo(pts2[0])
#             for p in pts2[1:]:
#                 path.lineTo(p)
#             self._poly_preview.setPath(path)

#     def _finish_polygon(self) -> None:
#         if len(self._poly_points) < 3:
#             self._cancel_in_progress()
#             return
#         item = PolygonItem(self._poly_points, class_id=self.current_class_id)
#         item.setPos(QPointF(0.0, 0.0))
#         item.setRotation(0.0)
#         self.created_item.emit(item)
#         self._poly_points = []
#         if self._poly_preview is not None:
#             self.scene.removeItem(self._poly_preview)
#             self._poly_preview = None

#     def _cancel_in_progress(self) -> None:
#         self._draw_start = None
#         self._poly_points = []
#         if self._poly_preview is not None:
#             self.scene.removeItem(self._poly_preview)
#             self._poly_preview = None
#         self._clear_preview()

#     def _clear_preview(self) -> None:
#         if self._preview_path is not None:
#             self.scene.removeItem(self._preview_path)
#             self._preview_path = None


#####################v2

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from .shapes import BBoxItem, OBBItem, PolygonItem


class ToolMode:
    SELECT = "select"
    BBOX = "bbox"
    OBB = "obb"
    POLY = "poly"


class CanvasView(QGraphicsView):
    status = Signal(str)
    created_item = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.pix_item: Optional[QGraphicsPixmapItem] = None
        self.img_w = 0
        self.img_h = 0

        self.mode = ToolMode.SELECT
        self.current_class_id = 0

        self._space_panning = False
        self._pan_last: Optional[QPointF] = None

        self._draw_start: Optional[QPointF] = None
        self._preview_path: Optional[QGraphicsPathItem] = None

        self._poly_points: List[QPointF] = []
        self._poly_preview: Optional[QGraphicsPathItem] = None

        self.set_mode(ToolMode.SELECT)

    def _mode_hint(self, mode: str) -> str:
        if mode == ToolMode.SELECT:
            return "Tool 工具: select  (双击标注进入编辑; 拖动边缘缩放; 右键退出编辑)"
        if mode in (ToolMode.BBOX, ToolMode.OBB):
            return "Tool 工具: rect  (双击标注进入编辑模式后可拉动手柄; 右键退出编辑)"
        if mode == ToolMode.POLY:
            return "Tool 工具: poly  (左键加点; 双击Mask进入顶点编辑; 右键退出)"
        return f"Tool 工具: {mode}"

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._cancel_in_progress()
        if mode == ToolMode.SELECT:
            self.setDragMode(QGraphicsView.RubberBandDrag)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
        self.status.emit(self._mode_hint(mode))

    def set_current_class(self, class_id: int) -> None:
        self.current_class_id = int(class_id)
        self.status.emit(f"Class 类别: {self.current_class_id}")

    def load_image(self, pix: QPixmap) -> None:
        self.scene.clear()
        self.pix_item = QGraphicsPixmapItem(pix)
        self.pix_item.setZValue(-1000)
        self.scene.addItem(self.pix_item)
        self.img_w = pix.width()
        self.img_h = pix.height()
        self.scene.setSceneRect(0, 0, self.img_w, self.img_h)
        self._cancel_in_progress()

    def fit_to_view(self) -> None:
        if self.pix_item is None:
            return
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel_in_progress()
            self._set_all_polygon_vertex_edit(False)
            self.status.emit("Canceled 取消.")
            event.accept()
            return

        if self.mode == ToolMode.POLY and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish_polygon()
            event.accept()
            return

        if event.key() == Qt.Key_Space:
            self._space_panning = True
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Space:
            self._space_panning = False
            self._pan_last = None
            self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _shape_item_at(self, view_pos) -> Optional[object]:
        hit = self.itemAt(view_pos)
        it = hit
        while it is not None and not isinstance(it, (BBoxItem, OBBItem, PolygonItem)):
            it = it.parentItem()
        if isinstance(it, (BBoxItem, OBBItem, PolygonItem)):
            return it
        return None

    def _set_all_polygon_vertex_edit(self, on: bool) -> None:
        for it in self.scene.items():
            if isinstance(it, PolygonItem):
                try:
                    it.set_vertex_edit_mode(bool(on))
                except Exception:
                    pass

    def mouseDoubleClickEvent(self, event) -> None:
        if self.pix_item is None:
            return

        if event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        # Don't disrupt in-progress drawing.
        if self._draw_start is not None or self._poly_points:
            super().mouseDoubleClickEvent(event)
            return

        shp = self._shape_item_at(event.position().toPoint())
        if shp is None:
            self._set_all_polygon_vertex_edit(False)
            super().mouseDoubleClickEvent(event)
            return

        # Double click enters edit: switch to select + select item.
        if self.mode != ToolMode.SELECT:
            self.set_mode(ToolMode.SELECT)

        try:
            self.scene.clearSelection()
            if getattr(shp, "isVisible", lambda: True)():
                shp.setSelected(True)
        except Exception:
            pass

        # Polygon: toggle vertex edit mode on double click.
        if isinstance(shp, PolygonItem):
            for it in self.scene.items():
                if isinstance(it, PolygonItem) and it is not shp:
                    it.set_vertex_edit_mode(False)
            try:
                shp.set_vertex_edit_mode(not shp.vertex_edit_mode())
            except Exception:
                pass

            if shp.vertex_edit_mode():
                self.status.emit("Mask 顶点编辑: 拖动顶点调整 (右键退出编辑)")
            else:
                self.status.emit("Mask: 已选中 (拖动移动; 双击进入顶点编辑; 右键退出)")
            event.accept()
            return

        self._set_all_polygon_vertex_edit(False)
        self.status.emit("框编辑: 已选中 (拖动中心移动; 拖动角点/手柄缩放; 右键退出)")
        event.accept()

    def mousePressEvent(self, event) -> None:
        if self.pix_item is None:
            return

        # 右键逻辑核心：清空选择，退出所有编辑模式
        if event.button() == Qt.RightButton:
            if self.mode == ToolMode.POLY and self._poly_points:
                self._finish_polygon()
                event.accept()
                return

            # 如果正在绘制预览框，则取消绘制
            if self._draw_start is not None or self._poly_points or self._preview_path is not None:
                self._cancel_in_progress()
                self.status.emit("Canceled 取消状态.")
                event.accept()
                return

            # 关键：退出选择、清除顶点编辑、回到 SELECT 模式
            self.scene.clearSelection()
            self._set_all_polygon_vertex_edit(False)
            if self.mode != ToolMode.SELECT:
                self.set_mode(ToolMode.SELECT)
            self.status.emit("已退出编辑/控制模式.")
            event.accept()
            return

        if self._space_panning and event.button() == Qt.LeftButton:
            self._pan_last = event.position()
            event.accept()
            return

        # In draw modes: DoubleClick edits. Single click draw.
        if event.button() == Qt.LeftButton and self.mode != ToolMode.SELECT:
            shp = self._shape_item_at(event.position().toPoint())
            if shp is not None:
                super().mousePressEvent(event)
                return

        # select mode click empty: exit polygon edit
        if event.button() == Qt.LeftButton and self.mode == ToolMode.SELECT:
             shp = self._shape_item_at(event.position().toPoint())
             if shp is None:
                 self._set_all_polygon_vertex_edit(False)

        sp = self.mapToScene(event.position().toPoint())

        # Start drawing rect
        if self.mode in (ToolMode.BBOX, ToolMode.OBB) and event.button() == Qt.LeftButton:
            self._draw_start = sp
            self._start_rect_preview()
            self._update_rect_preview(sp, sp)
            event.accept()
            return

        # Polygon drawing
        if self.mode == ToolMode.POLY and event.button() == Qt.LeftButton:
            self._poly_points.append(sp)
            self._update_poly_preview(sp)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._space_panning and self._pan_last is not None:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return

        sp = self.mapToScene(event.position().toPoint())

        if self._draw_start is not None and self._preview_path is not None:
            self._update_rect_preview(self._draw_start, sp)
            event.accept()
            return

        if self.mode == ToolMode.POLY and self._poly_preview is not None and self._poly_points:
            self._update_poly_preview(sp)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._space_panning and event.button() == Qt.LeftButton:
            self._pan_last = None
            event.accept()
            return

        if self._draw_start is not None and event.button() == Qt.LeftButton:
            end = self.mapToScene(event.position().toPoint())
            self._commit_rect(self._draw_start, end)
            self._draw_start = None
            self._clear_preview()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def _start_rect_preview(self) -> None:
        self._clear_preview()
        self._preview_path = QGraphicsPathItem()
        pen = QPen(Qt.yellow, 2.0)
        pen.setCosmetic(True)
        self._preview_path.setPen(pen)
        self.scene.addItem(self._preview_path)

    def _update_rect_preview(self, a: QPointF, b: QPointF) -> None:
        if self._preview_path is None:
            return
        x1, y1 = a.x(), a.y()
        x2, y2 = b.x(), b.y()
        l, r = (x1, x2) if x1 <= x2 else (x2, x1)
        t, b = (y1, y2) if y1 <= y2 else (y2, y1)
        path = QPainterPath()
        path.addRect(l, t, max(1.0, r - l), max(1.0, b - t))
        self._preview_path.setPath(path)

    def _commit_rect(self, a: QPointF, b: QPointF) -> None:
        x1, y1 = a.x(), a.y()
        x2, y2 = b.x(), b.y()
        l, r = (x1, x2) if x1 <= x2 else (x2, x1)
        t, b = (y1, y2) if y1 <= y2 else (y2, y1)
        w, h = max(2.0, r - l), max(2.0, b - t)
        cx, cy = l + w / 2.0, t + h / 2.0

        if self.mode == ToolMode.BBOX:
            item = BBoxItem(w, h, class_id=self.current_class_id)
            item.setPos(QPointF(cx, cy))
            self.created_item.emit(item)
            return

        if self.mode == ToolMode.OBB:
            item = OBBItem(w, h, class_id=self.current_class_id)
            item.setPos(QPointF(cx, cy))
            self.created_item.emit(item)
            return

    def _update_poly_preview(self, cursor: QPointF) -> None:
        if self._poly_preview is None:
            self._poly_preview = QGraphicsPathItem()
            pen = QPen(Qt.yellow, 2.0, Qt.DotLine)
            pen.setCosmetic(True)
            self._poly_preview.setPen(pen)
            self.scene.addItem(self._poly_preview)

        pts = self._poly_points[:]
        if pts:
            pts2 = pts + [cursor]
            path = QPainterPath()
            path.moveTo(pts2[0])
            for p in pts2[1:]:
                path.lineTo(p)
            self._poly_preview.setPath(path)

    def _finish_polygon(self) -> None:
        if len(self._poly_points) < 3:
            self._cancel_in_progress()
            return
        item = PolygonItem(self._poly_points, class_id=self.current_class_id)
        item.setPos(QPointF(0.0, 0.0))
        self.created_item.emit(item)
        self._poly_points = []
        if self._poly_preview is not None:
            self.scene.removeItem(self._poly_preview)
            self._poly_preview = None

    def _cancel_in_progress(self) -> None:
        self._draw_start = None
        self._poly_points = []
        if self._poly_preview is not None:
             self.scene.removeItem(self._poly_preview)
             self._poly_preview = None
        self._clear_preview()

    def _clear_preview(self) -> None:
        if self._preview_path is not None:
            self.scene.removeItem(self._preview_path)
            self._preview_path = None