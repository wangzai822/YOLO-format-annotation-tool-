from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap
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
        self._space_poly_point_pending = False
        self._space_poly_pan_started = False

        self._draw_start: Optional[QPointF] = None
        self._preview_path: Optional[QGraphicsPathItem] = None

        self._poly_points: List[QPointF] = []
        self._poly_preview: Optional[QGraphicsPathItem] = None

        self.set_mode(ToolMode.SELECT)

    def _mode_hint(self, mode: str) -> str:
        if mode == ToolMode.SELECT:
            return (
                "Tool 工具: select  "
                "(double-click annotation to edit 双击标注进入编辑; "
                "Ctrl+drag selected annotation 移动选中标注; "
                "drag edges/handles to resize 拖动边缘/手柄缩放; "
                "right-click to exit 右键退出编辑)"
            )
        if mode in (ToolMode.BBOX, ToolMode.OBB):
            return (
                "Tool 工具: rect  "
                "(draw to create 拖拽绘制; "
                "double-click annotation to edit 双击标注进入编辑; "
                "right-click to exit/select 右键退出到选择模式)"
            )
        if mode == ToolMode.POLY:
            return (
                "Tool 工具: poly  "
                "(left click / Space to add points 左键/空格加点; "
                "Enter/right-click to finish 回车/右键完成; "
                "double-click mask to edit vertices 双击Mask进入顶点编辑)"
            )
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
        self.status.emit(f"Class 类别 ID: {self.current_class_id}")

    def load_image(self, pix: QPixmap) -> None:
        self.scene.clear()
        self.pix_item = QGraphicsPixmapItem(pix)
        self.pix_item.setZValue(-1000)
        self.scene.addItem(self.pix_item)

        self.img_w = pix.width()
        self.img_h = pix.height()
        self.scene.setSceneRect(0, 0, self.img_w, self.img_h)

        self.scene.setProperty("bounds_warning", None)
        self.scene.setProperty("image_name", None)
        self._cancel_in_progress()

    def fit_to_view(self) -> None:
        if self.pix_item is None:
            return
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def _image_scene_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self.img_w), float(self.img_h))

    def _clamp_scene_point(self, p: QPointF) -> QPointF:
        img = self._image_scene_rect()
        return QPointF(
            min(max(p.x(), img.left()), img.right()),
            min(max(p.y(), img.top()), img.bottom()),
        )

    def _set_bounds_warning(self, raw_p: QPointF, clamped_p: QPointF, anno_text: str) -> None:
        img = self._image_scene_rect()
        sides = {
            "left": raw_p.x() < img.left(),
            "top": raw_p.y() < img.top(),
            "right": raw_p.x() > img.right(),
            "bottom": raw_p.y() > img.bottom(),
        }
        if not any(sides.values()):
            self._clear_bounds_warning()
            return

        self.scene.setProperty(
            "bounds_warning",
            {
                "sides": sides,
                "image_text": (
                    f"Image 图像: x[{img.left():.0f}, {img.right():.0f}]  "
                    f"y[{img.top():.0f}, {img.bottom():.0f}]"
                ),
                "cursor_text": (
                    f"Cursor 光标: ({raw_p.x():.1f}, {raw_p.y():.1f}) -> "
                    f"({clamped_p.x():.1f}, {clamped_p.y():.1f})"
                ),
                "anno_text": anno_text,
            },
        )
        self.viewport().update()

    def _set_rect_bounds_warning(self, raw_rect: QRectF, clamped_rect: QRectF) -> None:
        img = self._image_scene_rect()
        sides = {
            "left": raw_rect.left() < img.left(),
            "top": raw_rect.top() < img.top(),
            "right": raw_rect.right() > img.right(),
            "bottom": raw_rect.bottom() > img.bottom(),
        }
        if not any(sides.values()):
            self._clear_bounds_warning()
            return

        self.scene.setProperty(
            "bounds_warning",
            {
                "sides": sides,
                "image_text": (
                    f"Image 图像: x[{img.left():.0f}, {img.right():.0f}]  "
                    f"y[{img.top():.0f}, {img.bottom():.0f}]"
                ),
                "cursor_text": (
                    f"Raw 标注原始: ({raw_rect.left():.1f}, {raw_rect.top():.1f})"
                    f" - ({raw_rect.right():.1f}, {raw_rect.bottom():.1f})"
                ),
                "anno_text": (
                    f"Clamped 标注限制后: ({clamped_rect.left():.1f}, {clamped_rect.top():.1f})"
                    f" - ({clamped_rect.right():.1f}, {clamped_rect.bottom():.1f})"
                ),
            },
        )
        self.viewport().update()

    def _clear_bounds_warning(self) -> None:
        if self.scene.property("bounds_warning") is not None:
            self.scene.setProperty("bounds_warning", None)
            self.viewport().update()

    def _viewport_cursor_scene_pos(self) -> Optional[QPointF]:
        view_pos = self.viewport().mapFromGlobal(QCursor.pos())
        if not self.viewport().rect().contains(view_pos):
            return None
        return self.mapToScene(view_pos)

    def _add_polygon_point(self, raw_sp: QPointF) -> None:
        sp = self._clamp_scene_point(raw_sp)
        self._poly_points.append(sp)
        self._update_poly_preview(sp)
        self._set_bounds_warning(raw_sp, sp, f"Point 点: ({sp.x():.1f}, {sp.y():.1f})")

    def _add_polygon_point_from_cursor(self) -> bool:
        if self.pix_item is None or self.mode != ToolMode.POLY:
            return False

        raw_sp = self._viewport_cursor_scene_pos()
        if raw_sp is None:
            self.status.emit("Move cursor onto canvas first 再将光标移动到画布后按空格加点.")
            return False

        self._add_polygon_point(raw_sp)
        return True

    def drawForeground(self, painter: QPainter, rect) -> None:
        super().drawForeground(painter, rect)
        if self.pix_item is None:
            return

        warn = self.scene.property("bounds_warning")
        if not warn:
            return

        img = self._image_scene_rect()

        painter.save()
        edge_pen = QPen(QColor(255, 72, 72), 4.0)
        edge_pen.setCosmetic(True)
        painter.setPen(edge_pen)
        if warn["sides"].get("left"):
            painter.drawLine(QPointF(img.left(), img.top()), QPointF(img.left(), img.bottom()))
        if warn["sides"].get("top"):
            painter.drawLine(QPointF(img.left(), img.top()), QPointF(img.right(), img.top()))
        if warn["sides"].get("right"):
            painter.drawLine(QPointF(img.right(), img.top()), QPointF(img.right(), img.bottom()))
        if warn["sides"].get("bottom"):
            painter.drawLine(QPointF(img.left(), img.bottom()), QPointF(img.right(), img.bottom()))
        painter.restore()

        painter.save()
        painter.setWorldMatrixEnabled(False)
        box = QRectF(16.0, 16.0, 430.0, 86.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(120, 0, 0, 220))
        painter.drawRoundedRect(box, 8.0, 8.0)
        painter.setPen(QColor(255, 245, 245))
        painter.drawText(
            QRectF(28.0, 28.0, 406.0, 18.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            "Out of bounds 标注越界，已限制在图像范围内",
        )
        painter.drawText(
            QRectF(28.0, 48.0, 406.0, 16.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            warn.get("image_text", ""),
        )
        painter.drawText(
            QRectF(28.0, 64.0, 406.0, 16.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            warn.get("cursor_text", ""),
        )
        painter.drawText(
            QRectF(28.0, 80.0, 406.0, 16.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            warn.get("anno_text", ""),
        )
        painter.restore()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel_in_progress()
            self._set_all_polygon_vertex_edit(False)
            self.status.emit("Canceled 已取消.")
            event.accept()
            return

        if self.mode == ToolMode.POLY and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish_polygon()
            event.accept()
            return

        if event.key() == Qt.Key_Space:
            if self.mode == ToolMode.POLY:
                if not event.isAutoRepeat():
                    self._space_panning = True
                    self._space_poly_point_pending = True
                    self._space_poly_pan_started = False
                event.accept()
                return

            self._space_panning = True
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Space:
            should_add_poly_point = (
                self.mode == ToolMode.POLY
                and self._space_poly_point_pending
                and not self._space_poly_pan_started
            )

            self._space_panning = False
            self._pan_last = None
            self._space_poly_point_pending = False
            self._space_poly_pan_started = False
            self.unsetCursor()

            if should_add_poly_point:
                self._add_polygon_point_from_cursor()

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

        if self._draw_start is not None or self._poly_points:
            super().mouseDoubleClickEvent(event)
            return

        shp = self._shape_item_at(event.position().toPoint())
        if shp is None:
            self._set_all_polygon_vertex_edit(False)
            super().mouseDoubleClickEvent(event)
            return

        if self.mode != ToolMode.SELECT:
            self.set_mode(ToolMode.SELECT)

        try:
            self.scene.clearSelection()
            if getattr(shp, "isVisible", lambda: True)():
                shp.setSelected(True)
        except Exception:
            pass

        if isinstance(shp, PolygonItem):
            for it in self.scene.items():
                if isinstance(it, PolygonItem) and it is not shp:
                    it.set_vertex_edit_mode(False)
            try:
                shp.set_vertex_edit_mode(not shp.vertex_edit_mode())
            except Exception:
                pass

            if shp.vertex_edit_mode():
                self.status.emit(
                    "Mask vertex edit 顶点编辑: drag vertices to adjust 拖动顶点调整 "
                    "(Ctrl+drag shape 移动整体; right-click to exit 右键退出编辑)"
                )
            else:
                self.status.emit(
                    "Mask selected 已选中: Ctrl+drag to move Ctrl+拖动移动; "
                    "double-click for vertex edit 双击进入顶点编辑; right-click to exit 右键退出"
                )
            event.accept()
            return

        self._set_all_polygon_vertex_edit(False)
        self.status.emit(
            "Box edit 框编辑: selected 已选中 "
            "(Ctrl+drag to move Ctrl+拖动移动; "
            "drag corners/handles to resize 拖动角点或手柄缩放; "
            "right-click to exit 右键退出)"
        )
        event.accept()
        return

    def mousePressEvent(self, event) -> None:
        if self.pix_item is None:
            return

        if event.button() == Qt.RightButton:
            if self.mode == ToolMode.POLY and self._poly_points:
                self._finish_polygon()
                event.accept()
                return

            if self._draw_start is not None or self._poly_points or self._preview_path is not None:
                self._cancel_in_progress()
                self.status.emit("Canceled current action 已取消当前状态.")
                event.accept()
                return

            self.scene.clearSelection()
            self._set_all_polygon_vertex_edit(False)
            self._clear_bounds_warning()
            if self.mode != ToolMode.SELECT:
                self.set_mode(ToolMode.SELECT)
            self.status.emit("Exited edit/control mode 已退出编辑/控制模式.")
            event.accept()
            return

        if self._space_panning and event.button() == Qt.LeftButton:
            self._pan_last = event.position()
            if self.mode == ToolMode.POLY and self._space_poly_point_pending:
                self._space_poly_pan_started = True
                self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.LeftButton and self.mode != ToolMode.SELECT:
            shp = self._shape_item_at(event.position().toPoint())
            if shp is not None:
                super().mousePressEvent(event)
                return

        if event.button() == Qt.LeftButton and self.mode == ToolMode.SELECT:
            shp = self._shape_item_at(event.position().toPoint())
            if shp is None:
                self._set_all_polygon_vertex_edit(False)

        raw_sp = self.mapToScene(event.position().toPoint())
        sp = self._clamp_scene_point(raw_sp)

        if self.mode in (ToolMode.BBOX, ToolMode.OBB) and event.button() == Qt.LeftButton:
            self._draw_start = sp
            self._start_rect_preview()
            self._update_rect_preview(sp, sp)
            self._set_bounds_warning(raw_sp, sp, f"Start 起点: ({sp.x():.1f}, {sp.y():.1f})")
            event.accept()
            return

        if self.mode == ToolMode.POLY and event.button() == Qt.LeftButton:
            self._add_polygon_point(raw_sp)
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

        raw_sp = self.mapToScene(event.position().toPoint())
        sp = self._clamp_scene_point(raw_sp)

        if self._draw_start is not None and self._preview_path is not None:
            self._update_rect_preview(self._draw_start, sp)
            self._set_rect_bounds_warning(
                QRectF(self._draw_start, raw_sp).normalized(),
                QRectF(self._draw_start, sp).normalized(),
            )
            event.accept()
            return

        if self.mode == ToolMode.POLY and self._poly_preview is not None and self._poly_points:
            self._update_poly_preview(sp)
            self._set_bounds_warning(raw_sp, sp, f"Point 点: ({sp.x():.1f}, {sp.y():.1f})")
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._space_panning and event.button() == Qt.LeftButton:
            self._pan_last = None
            event.accept()
            return

        if self._draw_start is not None and event.button() == Qt.LeftButton:
            end = self._clamp_scene_point(self.mapToScene(event.position().toPoint()))
            self._commit_rect(self._draw_start, end)
            self._draw_start = None
            self._clear_preview()
            self._clear_bounds_warning()
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
        t, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
        path = QPainterPath()
        path.addRect(l, t, max(1.0, r - l), max(1.0, bottom - t))
        self._preview_path.setPath(path)

    def _commit_rect(self, a: QPointF, b: QPointF) -> None:
        img = self._image_scene_rect()
        a = self._clamp_scene_point(a)
        b = self._clamp_scene_point(b)

        x1, y1 = a.x(), a.y()
        x2, y2 = b.x(), b.y()
        l, r = (x1, x2) if x1 <= x2 else (x2, x1)
        t, bottom = (y1, y2) if y1 <= y2 else (y2, y1)

        w = min(max(2.0, r - l), img.width())
        h = min(max(2.0, bottom - t), img.height())

        l = min(max(l, img.left()), img.right() - w)
        t = min(max(t, img.top()), img.bottom() - h)

        cx = l + w / 2.0
        cy = t + h / 2.0

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
        self._space_poly_point_pending = False
        self._space_poly_pan_started = False

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
        self._clear_bounds_warning()

    def _cancel_in_progress(self) -> None:
        self._draw_start = None
        self._poly_points = []
        self._space_poly_point_pending = False
        self._space_poly_pan_started = False
        if self._poly_preview is not None:
            self.scene.removeItem(self._poly_preview)
            self._poly_preview = None
        self._clear_preview()
        self._clear_bounds_warning()

    def _clear_preview(self) -> None:
        if self._preview_path is not None:
            self.scene.removeItem(self._preview_path)
            self._preview_path = None