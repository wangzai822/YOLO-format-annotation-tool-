from __future__ import annotations

import math
import uuid
import zlib
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QCursor,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsSceneMouseEvent

from .io_utils import norm_xy


def item_color(uid: str) -> QColor:
    """显著改进颜色随机算法"""
    h_raw = int(zlib.crc32(uid.encode("utf-8"))) & 0xFFFFFFFF
    return QColor.fromHsv(h_raw % 360, 150 + ((h_raw >> 8) % 100), 180 + ((h_raw >> 16) % 70))


def _rot_local_to_scene(v: QPointF, deg: float) -> QPointF:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return QPointF(v.x() * c - v.y() * s, v.x() * s + v.y() * c)


def _rot_scene_to_local(v: QPointF, deg: float) -> QPointF:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return QPointF(v.x() * c + v.y() * s, -v.x() * s + v.y() * c)


ROTATE_HANDLE_OFFSET = 26.0
MIN_SIZE = 2.0
HANDLE_DRAW_PX = 10.0
HANDLE_HIT_PX = 60.0
ROT_HIT_PX = 80.0
VTX_HIT_PX = 60.0
EDGE_HIT_PX = 40.0


class HandleKind:
    TL, TR, BR, BL, ROT = "tl", "tr", "br", "bl", "rot"
    T, B, L, R = "t", "b", "l", "r"


class EditableBase(QGraphicsObject):
    edited = Signal(object, dict, dict)

    def __init__(self, class_id: int = 0):
        super().__init__()
        self.uid = str(uuid.uuid4())
        self.class_id = int(class_id)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self._edit_before: Optional[Dict[str, Any]] = None
        self._cached_scale = 1.0
        self._suspend_bounds_clamp = False

    def _update_scale_cache(self):
        sc = self.scene()
        if sc and sc.views():
            self._cached_scale = max(1e-6, sc.views()[0].transform().m11())

    def get_anno_id(self) -> str:
        name = self.scene().property("image_name") if self.scene() else None
        return f"{name}_{self.uid[:8]}" if name else f"{getattr(self, 'anno_type', 'anno')}_{self.uid[:8]}"

    def _is_in_draw_mode(self) -> bool:
        sc = self.scene()
        if not (sc and sc.views()):
            return False
        return sc.views()[0].mode != "select"

    def _alt_highlight(self) -> bool:
        sc = self.scene()
        return bool(sc and sc.property("alt_highlight"))

    def _image_scene_rect(self) -> QRectF:
        sc = self.scene()
        return sc.sceneRect() if sc is not None else QRectF()

    def _clamp_scene_point(self, p: QPointF) -> QPointF:
        img = self._image_scene_rect()
        return QPointF(
            min(max(p.x(), img.left()), img.right()),
            min(max(p.y(), img.top()), img.bottom()),
        )

    def _content_scene_rect(self) -> QRectF:
        return self.sceneBoundingRect()

    def _set_pos_without_bounds_clamp(self, pos: QPointF) -> None:
        self._suspend_bounds_clamp = True
        try:
            self.setPos(pos)
        finally:
            self._suspend_bounds_clamp = False

    def _set_bounds_warning(self, raw_p: QPointF, clamped_p: QPointF, anno_text: str) -> None:
        sc = self.scene()
        if sc is None:
            return

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

        sc.setProperty(
            "bounds_warning",
            {
                "sides": sides,
                "image_text": f"Image 图像: x[{img.left():.0f}, {img.right():.0f}]  y[{img.top():.0f}, {img.bottom():.0f}]",
                "cursor_text": f"Cursor 光标: ({raw_p.x():.1f}, {raw_p.y():.1f}) -> ({clamped_p.x():.1f}, {clamped_p.y():.1f})",
                "anno_text": anno_text,
            },
        )
        for view in sc.views():
            view.viewport().update()

    def _set_bounds_warning_rect(self, raw_rect: QRectF, clamped_rect: QRectF) -> None:
        sc = self.scene()
        if sc is None:
            return

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

        sc.setProperty(
            "bounds_warning",
            {
                "sides": sides,
                "image_text": f"Image 图像: x[{img.left():.0f}, {img.right():.0f}]  y[{img.top():.0f}, {img.bottom():.0f}]",
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
        for view in sc.views():
            view.viewport().update()

    def _clear_bounds_warning(self) -> None:
        sc = self.scene()
        if sc is None:
            return
        if sc.property("bounds_warning") is not None:
            sc.setProperty("bounds_warning", None)
            for view in sc.views():
                view.viewport().update()

    def _paint_stats_hud(self, painter: QPainter):
        if not self._alt_highlight():
            return
        sc = self.scene()
        if not (sc and sc.views()):
            return
        items = [i for i in sc.items() if isinstance(i, EditableBase)]
        if not items or items[0] != self:
            return

        all_editable = [i for i in sc.items() if isinstance(i, EditableBase)]
        total = len(all_editable)
        counts = {"bbox": 0, "obb": 0, "polygon": 0}
        type_labels = {
            "bbox": "BBox 框",
            "obb": "OBB 旋转框",
            "polygon": "Polygon 多边形",
        }
        noises = []
        for i in all_editable:
            t = getattr(i, "anno_type", "unknown")
            counts[t] = counts.get(t, 0) + 1
            br = i._content_scene_rect()
            if br.width() < 5 or br.height() < 5:
                noises.append(f"{type_labels.get(t, t)} #{i.uid[:4]}")

        painter.save()
        painter.setWorldMatrixEnabled(False)
        hud_rect = QRectF(20, 20, 320, 110 + len(noises) * 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 210)))
        painter.drawRoundedRect(hud_rect, 8, 8)
        y = 45
        painter.setPen(QColor(0, 255, 255))
        painter.drawText(40, y, f"◆ Annotations 标注统计 (Total 总数: {total})")
        y += 25
        painter.setPen(Qt.white)
        painter.drawText(
            50,
            y,
            f"BBox 框: {counts['bbox']} | OBB 旋转框: {counts['obb']} | Polygon 多边形: {counts['polygon']}",
        )
        y += 25
        painter.setPen(QColor(255, 50, 50) if noises else QColor(50, 255, 50))
        painter.drawText(50, y, f"Noise 噪声标注: {'None 无' if not noises else len(noises)}")
        for n in noises:
            y += 20
            painter.drawText(65, y, f"-> {n}")
        painter.restore()

    def _paint_alt_highlight(self, painter: QPainter, path: QPainterPath):
        if not self._alt_highlight():
            return
        painter.save()
        p_bold = QPen(QColor(255, 255, 0), 3.0)
        p_bold.setCosmetic(True)
        painter.setPen(p_bold)
        painter.drawPath(path)
        p_contract = QPen(Qt.black, 1.0)
        p_contract.setCosmetic(True)
        painter.setPen(p_contract)
        painter.drawPath(path)
        painter.restore()

    def _paint_alt_label(self, painter: QPainter, anchor: QPointF) -> None:
        if not self._alt_highlight():
            return
        text = f"Cls/类别 C{self.class_id + 1}  ID/标注 #{self.uid[:4]}"
        painter.save()
        fm = QFontMetricsF(painter.font())
        br = fm.boundingRect(text).adjusted(-4, -2, 4, 2)
        bg = QRectF(anchor.x(), anchor.y() - br.height(), br.width(), br.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 220)))
        painter.drawRoundedRect(bg, 3.0, 3.0)
        painter.setPen(QPen(QColor(255, 255, 0), 1.0))
        painter.drawText(anchor, text)
        painter.restore()

    def pen(self) -> QPen:
        return QPen(item_color(self.uid), 2.0)

    def brush(self) -> QBrush:
        c = item_color(self.uid)
        c.setAlpha(45)
        return QBrush(c)

    def to_state(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "class_id": self.class_id,
            "pos": [self.pos().x(), self.pos().y()],
            "rotation": float(self.rotation()),
        }

    def apply_state(self, st: Dict[str, Any]) -> None:
        self.class_id = int(st.get("class_id", self.class_id))
        p = st.get("pos", [self.pos().x(), self.pos().y()])
        self.setPos(QPointF(float(p[0]), float(p[1])))
        self.setRotation(float(st.get("rotation", self.rotation())))
        self.update()

    def begin_edit(self) -> None:
        if self._edit_before is None:
            self._edit_before = self.to_state()

    def end_edit(self) -> None:
        if self._edit_before is None:
            return
        b, a = self._edit_before, self.to_state()
        self._edit_before = None
        if b != a:
            self.edited.emit(self, b, a)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.begin_edit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        self.end_edit()
        self._clear_bounds_warning()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.setZValue(10000.0 if bool(value) else 0.0)

        elif (
            change == QGraphicsItem.ItemPositionChange
            and not self._suspend_bounds_clamp
            and self.scene() is not None
            and hasattr(value, "x")
            and hasattr(value, "y")
        ):
            img = self._image_scene_rect()
            new_pos = QPointF(float(value.x()), float(value.y()))
            delta = new_pos - self.pos()
            raw_rect = self._content_scene_rect().translated(delta.x(), delta.y())

            clamped_pos = QPointF(new_pos)
            if raw_rect.left() < img.left():
                clamped_pos.setX(clamped_pos.x() + img.left() - raw_rect.left())
            if raw_rect.right() > img.right():
                clamped_pos.setX(clamped_pos.x() - (raw_rect.right() - img.right()))
            if raw_rect.top() < img.top():
                clamped_pos.setY(clamped_pos.y() + img.top() - raw_rect.top())
            if raw_rect.bottom() > img.bottom():
                clamped_pos.setY(clamped_pos.y() - (raw_rect.bottom() - img.bottom()))

            if clamped_pos != new_pos:
                dx = clamped_pos.x() - new_pos.x()
                dy = clamped_pos.y() - new_pos.y()
                self._set_bounds_warning_rect(raw_rect, raw_rect.translated(dx, dy))
                return clamped_pos

            self._clear_bounds_warning()

        return super().itemChange(change, value)


class RectLike(EditableBase):
    def __init__(self, w: float, h: float, class_id: int = 0):
        super().__init__(class_id=class_id)
        self.w, self.h = float(max(1.0, w)), float(max(1.0, h))
        self._active_handle: Optional[str] = None
        self._resize_anchor_scene: Optional[QPointF] = None
        self._resize_anchor_sxsy: Optional[Tuple[float, float]] = None
        self._resize_rotation_deg = 0.0
        self._rotate_last_valid = 0.0

    def local_rect(self) -> QRectF:
        return QRectF(-self.w / 2.0, -self.h / 2.0, self.w, self.h)

    def boundingRect(self) -> QRectF:
        return self.local_rect().adjusted(-5000, -5000, 5000, 5000)

    def _content_scene_rect(self) -> QRectF:
        r = self.local_rect()
        pts = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        poly = QPolygonF([self.mapToScene(p) for p in pts])
        return poly.boundingRect()

    def _px_to_local(self, px: float) -> float:
        sc = self.scene()
        s = max(1e-6, sc.views()[0].transform().m11()) if sc and sc.views() else 1.0
        return px / s

    def _hit_handle(self, p_local: QPointF) -> Optional[str]:
        if not self.isSelected():
            return None

        self._update_scale_cache()
        lx, ly = p_local.x(), p_local.y()
        w2, h2 = self.w / 2.0, self.h / 2.0

        if self.anno_type == "obb":
            if math.hypot(lx, ly + h2 + ROTATE_HANDLE_OFFSET) < (ROT_HIT_PX / self._cached_scale):
                return HandleKind.ROT

        hit = HANDLE_HIT_PX / self._cached_scale
        if math.hypot(lx + w2, ly + h2) < hit:
            return HandleKind.TL
        if math.hypot(lx - w2, ly + h2) < hit:
            return HandleKind.TR
        if math.hypot(lx - w2, ly - h2) < hit:
            return HandleKind.BR
        if math.hypot(lx + w2, ly - h2) < hit:
            return HandleKind.BL

        edge = EDGE_HIT_PX / self._cached_scale
        if abs(ly + h2) < edge and abs(lx) < w2:
            return HandleKind.T
        if abs(ly - h2) < edge and abs(lx) < w2:
            return HandleKind.B
        if abs(lx + w2) < edge and abs(ly) < h2:
            return HandleKind.L
        if abs(lx - w2) < edge and abs(ly) < h2:
            return HandleKind.R

        return None

    def hoverMoveEvent(self, event):
        if self.isSelected():
            h = self._hit_handle(event.pos())
            if h == HandleKind.ROT:
                self.setCursor(Qt.PointingHandCursor)
            elif h in (HandleKind.TL, HandleKind.BR):
                self.setCursor(Qt.SizeFDiagCursor)
            elif h in (HandleKind.TR, HandleKind.BL):
                self.setCursor(Qt.SizeBDiagCursor)
            elif h in (HandleKind.T, HandleKind.B):
                self.setCursor(Qt.SizeVerCursor)
            elif h in (HandleKind.L, HandleKind.R):
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.unsetCursor()
        super().hoverMoveEvent(event)

    def shape(self) -> QPainterPath:
        if self._is_in_draw_mode():
            return QPainterPath()
        self._update_scale_cache()
        base = QPainterPath()
        base.addRect(self.local_rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(120.0 / self._cached_scale if self.isSelected() else 15.0 / self._cached_scale)
        return stroker.createStroke(base).united(base)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pp = QPainterPath()
        pp.addRect(self.local_rect())
        self._paint_alt_highlight(painter, pp)

        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(self.local_rect())

        self._paint_alt_label(painter, self.local_rect().topLeft() - QPointF(0, 5))
        self._paint_stats_hud(painter)

        if self.isSelected():
            self._update_scale_cache()
            s = 10.0 / self._cached_scale
            r = self.local_rect()
            painter.setPen(QPen(Qt.black, 1.0))
            painter.setBrush(Qt.white)
            for pt in [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]:
                painter.drawRect(QRectF(pt.x() - s / 2.0, pt.y() - s / 2.0, s, s))
            if self.anno_type == "obb":
                painter.setBrush(QColor(255, 170, 0))
                painter.drawEllipse(QPointF(0, -self.h / 2.0 - ROTATE_HANDLE_OFFSET), s / 2.0, s / 2.0)

    def _start_resize(self, h):
        self._active_handle = h
        self._resize_rotation_deg = float(self.rotation())
        w2, h2 = self.w / 2.0, self.h / 2.0
        m = {
            HandleKind.TL: (w2, h2),
            HandleKind.TR: (-w2, h2),
            HandleKind.BR: (-w2, -h2),
            HandleKind.BL: (w2, -h2),
            HandleKind.T: (0.0, h2),
            HandleKind.B: (0.0, -h2),
            HandleKind.L: (w2, 0.0),
            HandleKind.R: (-w2, 0.0),
        }
        self._resize_anchor_scene = self.mapToScene(QPointF(m[h][0], m[h][1]))
        self._resize_anchor_sxsy = (1 if m[h][0] > 0 else -1, 1 if m[h][1] > 0 else -1)

    def _apply_resize_pos(self, handle: str) -> None:
        if self._resize_anchor_scene is None or self._resize_anchor_sxsy is None:
            return
        sx, sy = self._resize_anchor_sxsy
        tx = sx * self.w / 2.0 if handle not in (HandleKind.T, HandleKind.B) else 0.0
        ty = sy * self.h / 2.0 if handle not in (HandleKind.L, HandleKind.R) else 0.0
        self._set_pos_without_bounds_clamp(
            self._resize_anchor_scene - _rot_local_to_scene(QPointF(tx, ty), self._resize_rotation_deg)
        )

    def _set_resized_geometry(self, w: float, h: float, handle: str) -> None:
        self.prepareGeometryChange()
        self.w = max(MIN_SIZE, float(w))
        self.h = max(MIN_SIZE, float(h))
        self._apply_resize_pos(handle)

    def _inside_image_rect(self, rect: QRectF) -> bool:
        img = self._image_scene_rect()
        return (
            rect.left() >= img.left()
            and rect.top() >= img.top()
            and rect.right() <= img.right()
            and rect.bottom() <= img.bottom()
        )

    def _shift_inside_scene(self, raw_rect: QRectF) -> QRectF:
        img = self._image_scene_rect()
        corrected = QRectF(raw_rect)
        dx = 0.0
        dy = 0.0

        if corrected.left() < img.left():
            dx += img.left() - corrected.left()
        if corrected.right() > img.right():
            dx -= corrected.right() - img.right()
        if corrected.top() < img.top():
            dy += img.top() - corrected.top()
        if corrected.bottom() > img.bottom():
            dy -= corrected.bottom() - img.bottom()

        if dx or dy:
            self._set_pos_without_bounds_clamp(self.pos() + QPointF(dx, dy))
            corrected = raw_rect.translated(dx, dy)

        return corrected

    def _constrain_inside_after_resize(self, handle: str) -> None:
        raw_rect = self._content_scene_rect()
        if self._inside_image_rect(raw_rect):
            self._clear_bounds_warning()
            return

        raw_w, raw_h = self.w, self.h

        if handle in (HandleKind.L, HandleKind.R):
            lo, hi = MIN_SIZE, raw_w
            best = MIN_SIZE
            for _ in range(18):
                mid = (lo + hi) / 2.0
                self._set_resized_geometry(mid, raw_h, handle)
                if self._inside_image_rect(self._content_scene_rect()):
                    best = mid
                    lo = mid
                else:
                    hi = mid
            self._set_resized_geometry(best, raw_h, handle)

        elif handle in (HandleKind.T, HandleKind.B):
            lo, hi = MIN_SIZE, raw_h
            best = MIN_SIZE
            for _ in range(18):
                mid = (lo + hi) / 2.0
                self._set_resized_geometry(raw_w, mid, handle)
                if self._inside_image_rect(self._content_scene_rect()):
                    best = mid
                    lo = mid
                else:
                    hi = mid
            self._set_resized_geometry(raw_w, best, handle)

        else:
            lo, hi = 0.0, 1.0
            best = 0.0
            for _ in range(18):
                mid = (lo + hi) / 2.0
                cand_w = max(MIN_SIZE, raw_w * mid)
                cand_h = max(MIN_SIZE, raw_h * mid)
                self._set_resized_geometry(cand_w, cand_h, handle)
                if self._inside_image_rect(self._content_scene_rect()):
                    best = mid
                    lo = mid
                else:
                    hi = mid
            self._set_resized_geometry(max(MIN_SIZE, raw_w * best), max(MIN_SIZE, raw_h * best), handle)

        corrected = self._content_scene_rect()
        if not self._inside_image_rect(corrected):
            corrected = self._shift_inside_scene(corrected)

        self._set_bounds_warning_rect(raw_rect, corrected)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.begin_edit()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        self.setFlag(QGraphicsItem.ItemIsMovable, ctrl)

        if self.isSelected() and event.button() == Qt.LeftButton:
            h = self._hit_handle(event.pos())
            if h:
                if h == HandleKind.ROT:
                    self._active_handle = HandleKind.ROT
                    self._rotate_last_valid = float(self.rotation())
                else:
                    self._start_resize(h)
                event.accept()
                return

            if not ctrl:
                corners = [HandleKind.TL, HandleKind.TR, HandleKind.BR, HandleKind.BL]
                r = self.local_rect()
                cs = {
                    HandleKind.TL: r.topLeft(),
                    HandleKind.TR: r.topRight(),
                    HandleKind.BR: r.bottomRight(),
                    HandleKind.BL: r.bottomLeft(),
                }
                c = min(
                    corners,
                    key=lambda k: math.hypot(event.pos().x() - cs[k].x(), event.pos().y() - cs[k].y()),
                )
                self._start_resize(c)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self._active_handle:
            super().mouseMoveEvent(event)
            return

        if self._active_handle == HandleKind.ROT:
            v = event.scenePos() - self.mapToScene(QPointF(0, 0))
            new_rot = math.degrees(math.atan2(v.y(), v.x())) + 90.0
            self.setRotation(new_rot)

            raw_rect = self._content_scene_rect()
            if self._inside_image_rect(raw_rect):
                self._rotate_last_valid = float(self.rotation())
                self._clear_bounds_warning()
            else:
                corrected = self._shift_inside_scene(raw_rect)
                if not self._inside_image_rect(corrected):
                    self.setRotation(self._rotate_last_valid)
                    corrected = self._content_scene_rect()
                self._set_bounds_warning_rect(raw_rect, corrected)

        elif self._resize_anchor_scene and self._resize_anchor_sxsy is not None:
            vl = _rot_scene_to_local(
                event.scenePos() - self._resize_anchor_scene,
                self._resize_rotation_deg,
            )
            sx, sy = self._resize_anchor_sxsy
            hk = self._active_handle

            new_w = self.w
            new_h = self.h
            if hk not in (HandleKind.T, HandleKind.B):
                new_w = max(MIN_SIZE, vl.x() * -sx)
            if hk not in (HandleKind.L, HandleKind.R):
                new_h = max(MIN_SIZE, vl.y() * -sy)

            self._set_resized_geometry(new_w, new_h, hk)
            self._constrain_inside_after_resize(hk)

        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        self._active_handle = None
        self.end_edit()


class BBoxItem(RectLike):
    anno_type = "bbox"

    def pen(self):
        p = super().pen()
        p.setStyle(Qt.DashLine)
        return p

    def ultralytics_row(self, iw, ih) -> str:
        r = self.mapToScene(self.local_rect()).boundingRect()
        xc, yc, w, h = r.center().x() / iw, r.center().y() / ih, r.width() / iw, r.height() / ih
        return f"{self.class_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}"

    def to_label_dict(self, iw, ih):
        r = self.mapToScene(self.local_rect()).boundingRect()
        return {
            "id": self.get_anno_id(),
            "type": "bbox",
            "class_id": self.class_id,
            "yolo_bbox": {
                "x_center": r.center().x() / iw,
                "y_center": r.center().y() / ih,
                "width": r.width() / iw,
                "height": r.height() / ih,
            },
            "ultralytics_row": self.ultralytics_row(iw, ih),
        }


class OBBItem(RectLike):
    anno_type = "obb"

    def corners_scene(self) -> List[QPointF]:
        r = self.local_rect()
        return [self.mapToScene(p) for p in [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]]

    def ultralytics_row(self, iw, ih) -> str:
        cs = self.corners_scene()
        coords = []
        for p in cs:
            xn, yn = norm_xy(p.x(), p.y(), iw, ih)
            coords.extend([f"{xn:.6f}", f"{yn:.6f}"])
        return f"{self.class_id} " + " ".join(coords)

    def to_label_dict(self, iw, ih):
        cs = [[norm_xy(p.x(), p.y(), iw, ih)[0], norm_xy(p.x(), p.y(), iw, ih)[1]] for p in self.corners_scene()]
        return {
            "id": self.get_anno_id(),
            "type": "obb",
            "class_id": self.class_id,
            "yolo_obb": {"corners": cs},
            "ultralytics_row": self.ultralytics_row(iw, ih),
        }


class PolygonItem(EditableBase):
    anno_type = "polygon"

    def __init__(self, points: List[QPointF], class_id: int = 0):
        super().__init__(class_id=class_id)
        self.points = [QPointF(p) for p in points]
        self._active_vtx = None
        self._vertex_edit_mode = False

    def set_vertex_edit_mode(self, on: bool):
        self._vertex_edit_mode = bool(on)
        self.update()

    def vertex_edit_mode(self):
        return self._vertex_edit_mode

    def boundingRect(self):
        xs, ys = [p.x() for p in self.points], [p.y() for p in self.points]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-5000, -5000, 5000, 5000)

    def _content_scene_rect(self) -> QRectF:
        poly = QPolygonF([self.mapToScene(pt) for pt in self.points])
        return poly.boundingRect()

    def hoverMoveEvent(self, event):
        if self._vertex_edit_mode:
            self._update_scale_cache()
            idx = self._nearest_vertex(event.pos())
            if idx is not None and math.hypot(
                event.pos().x() - self.points[idx].x(),
                event.pos().y() - self.points[idx].y(),
            ) < (VTX_HIT_PX / self._cached_scale):
                self.setCursor(Qt.PointingHandCursor)
                return
        self.unsetCursor()
        super().hoverMoveEvent(event)

    def shape(self):
        if self._is_in_draw_mode():
            return QPainterPath()
        self._update_scale_cache()
        b = QPainterPath()
        b.addPolygon(QPolygonF(self.points))
        s = QPainterPathStroker()
        s.setWidth(120.0 / self._cached_scale if self._vertex_edit_mode else 15.0 / self._cached_scale)
        return s.createStroke(b).united(b)

    def paint(self, painter: QPainter, option, widget=None):
        pp = QPainterPath()
        pp.addPolygon(QPolygonF(self.points))
        if self._alt_highlight():
            self._paint_alt_highlight(painter, pp)

        p = QPen(
            Qt.red if self._vertex_edit_mode else self.pen().color(),
            2.0,
            Qt.DashLine if self._vertex_edit_mode else Qt.SolidLine,
        )
        painter.setPen(p)
        painter.setBrush(self.brush())
        if len(self.points) >= 3:
            painter.drawPolygon(QPolygonF(self.points))

        if self.points:
            self._paint_alt_label(painter, self.points[0] - QPointF(0, 5))
        self._paint_stats_hud(painter)

        if self.isSelected() or self._vertex_edit_mode:
            self._update_scale_cache()
            d = 11.0 / self._cached_scale
            painter.setBrush(Qt.white)
            painter.setPen(QPen(Qt.red if self._vertex_edit_mode else Qt.black, 1.0))
            for pt in self.points:
                painter.drawRect(QRectF(pt.x() - d / 2.0, pt.y() - d / 2.0, d, d))
            if self._vertex_edit_mode:
                label = " [ EDITING VERTEX 顶点编辑中 ] "
                fm = QFontMetricsF(painter.font())
                br = fm.boundingRect(label).adjusted(-10, -5, 10, 5)
                bb = QPolygonF(self.points).boundingRect()
                anchor = QPointF(bb.center().x() - br.width() / 2.0, bb.top() - 30.0)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(220, 0, 0, 230)))
                painter.drawRoundedRect(QRectF(anchor.x(), anchor.y() - br.height(), br.width(), br.height()), 5, 5)
                painter.setPen(Qt.white)
                painter.drawText(anchor, label)

    def _nearest_vertex(self, p_local: QPointF):
        if not self.points:
            return None
        return min(
            range(len(self.points)),
            key=lambda i: math.hypot(p_local.x() - self.points[i].x(), p_local.y() - self.points[i].y()),
        )

    def mousePressEvent(self, event):
        if self._is_in_draw_mode():
            event.ignore()
            return
        self.begin_edit()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        self.setFlag(QGraphicsItem.ItemIsMovable, ctrl)
        if self._vertex_edit_mode and event.button() == Qt.LeftButton and not ctrl:
            idx = self._nearest_vertex(event.pos())
            self._update_scale_cache()
            if idx is not None and math.hypot(
                event.pos().x() - self.points[idx].x(),
                event.pos().y() - self.points[idx].y(),
            ) < (150.0 / self._cached_scale):
                self._active_vtx = idx
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._active_vtx is not None:
            raw_scene = self.mapToScene(event.pos())
            clamped_scene = self._clamp_scene_point(raw_scene)

            if raw_scene != clamped_scene:
                self._set_bounds_warning(
                    raw_scene,
                    clamped_scene,
                    f"Point 点: ({clamped_scene.x():.1f}, {clamped_scene.y():.1f})",
                )
            else:
                self._clear_bounds_warning()

            self.prepareGeometryChange()
            self.points[self._active_vtx] = self.mapFromScene(clamped_scene)
            self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._active_vtx = None
        self.end_edit()

    def ultralytics_row(self, iw, ih) -> str:
        pts = self.points
        coords = []
        for p in pts:
            sp = self.mapToScene(p)
            xn, yn = norm_xy(sp.x(), sp.y(), iw, ih)
            coords.extend([f"{xn:.6f}", f"{yn:.6f}"])
        return f"{self.class_id} " + " ".join(coords)

    def to_label_dict(self, iw, ih):
        pts = [
            [
                norm_xy(self.mapToScene(pt).x(), self.mapToScene(pt).y(), iw, ih)[0],
                norm_xy(self.mapToScene(pt).x(), self.mapToScene(pt).y(), iw, ih)[1],
            ]
            for pt in self.points
        ]
        return {
            "id": self.get_anno_id(),
            "type": "polygon",
            "class_id": self.class_id,
            "yolo_seg": {"points": pts},
            "ultralytics_row": self.ultralytics_row(iw, ih),
        }