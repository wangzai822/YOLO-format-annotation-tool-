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
    r = math.radians(deg); c, s = math.cos(r), math.sin(r)
    return QPointF(v.x() * c - v.y() * s, v.x() * s + v.y() * c)


def _rot_scene_to_local(v: QPointF, deg: float) -> QPointF:
    r = math.radians(deg); c, s = math.cos(r), math.sin(r)
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
    T, B, L, R = "t", "b", "l", "r"  # 顶、底、左、右边


class EditableBase(QGraphicsObject):
    edited = Signal(object, dict, dict)

    def __init__(self, class_id: int = 0):
        super().__init__()
        self.uid = str(uuid.uuid4())
        self.class_id = int(class_id)
        self.setAcceptHoverEvents(True)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self._edit_before: Optional[Dict[str, Any]] = None
        self._cached_scale = 1.0

    def _update_scale_cache(self):
        sc = self.scene()
        if sc and sc.views():
            self._cached_scale = max(1e-6, sc.views()[0].transform().m11())

    def get_anno_id(self) -> str:
        name = self.scene().property("image_name") if self.scene() else None
        return f"{name}_{self.uid[:8]}" if name else f"{getattr(self, 'anno_type', 'anno')}_{self.uid[:8]}"

    def _is_in_draw_mode(self) -> bool:
        sc = self.scene()
        if not (sc and sc.views()): return False
        return sc.views()[0].mode != "select"

    def _alt_highlight(self) -> bool:
        sc = self.scene()
        return bool(sc and sc.property("alt_highlight"))

    def _paint_stats_hud(self, painter: QPainter):
        """HUD 统计面板逻辑"""
        if not self._alt_highlight(): return
        sc = self.scene()
        if not (sc and sc.views()): return
        items = [i for i in sc.items() if hasattr(i, 'uid')]
        if not items or items[0] != self: return
        all_editable = [i for i in sc.items() if isinstance(i, EditableBase)]
        total = len(all_editable)
        counts = {"bbox": 0, "obb": 0, "polygon": 0}
        noises = []
        for i in all_editable:
            t = getattr(i, "anno_type", "unknown")
            counts[t] = counts.get(t, 0) + 1
            br = i.sceneBoundingRect()
            if br.width() < 5 or br.height() < 5:
                noises.append(f"{t.upper()} #{i.uid[:4]}")
        painter.save(); painter.setWorldMatrixEnabled(False)
        hud_rect = QRectF(20, 20, 260, 110 + len(noises) * 20)
        painter.setPen(Qt.NoPen); painter.setBrush(QBrush(QColor(0, 0, 0, 210)))
        painter.drawRoundedRect(hud_rect, 8, 8)
        y = 45; painter.setPen(QColor(0, 255, 255)); painter.drawText(40, y, f"◆ 标注统计 (Total: {total})")
        y += 25; painter.setPen(Qt.white); painter.drawText(50, y, f"BBox: {counts['bbox']} | OBB: {counts['obb']} | Mask: {counts['polygon']}")
        y += 25; painter.setPen(QColor(255, 50, 50) if noises else QColor(50, 255, 50))
        painter.drawText(50, y, f"噪声标注 (Noise): {'无' if not noises else len(noises)}")
        for n in noises: y += 20; painter.drawText(65, y, f"→ {n}")
        painter.restore()

    def _paint_alt_highlight(self, painter: QPainter, path: QPainterPath):
        """高亮轮廓绘制"""
        if not self._alt_highlight(): return
        painter.save()
        p_bold = QPen(QColor(255, 255, 0), 3.0); p_bold.setCosmetic(True); painter.setPen(p_bold); painter.drawPath(path)
        p_contract = QPen(Qt.black, 1.0); p_contract.setCosmetic(True); painter.setPen(p_contract); painter.drawPath(path)
        painter.restore()

    def _paint_alt_label(self, painter: QPainter, anchor: QPointF) -> None:
        if not self._alt_highlight(): return
        text = f"C{self.class_id + 1} #{self.uid[:4]}"
        painter.save(); fm = QFontMetricsF(painter.font()); br = fm.boundingRect(text).adjusted(-4, -2, 4, 2)
        bg = QRectF(anchor.x(), anchor.y() - br.height(), br.width(), br.height())
        painter.setPen(Qt.NoPen); painter.setBrush(QBrush(QColor(0, 0, 0, 220))); painter.drawRoundedRect(bg, 3.0, 3.0)
        painter.setPen(QPen(QColor(255, 255, 0), 1.0)); painter.drawText(anchor, text); painter.restore()

    def pen(self) -> QPen: return QPen(item_color(self.uid), 2.0)
    def brush(self) -> QBrush:
        c = item_color(self.uid); c.setAlpha(45); return QBrush(c)

    def to_state(self) -> Dict[str, Any]:
        return {"uid": self.uid, "class_id": self.class_id, "pos": [self.pos().x(), self.pos().y()], "rotation": float(self.rotation())}

    def apply_state(self, st: Dict[str, Any]) -> None:
        self.class_id = int(st.get("class_id", self.class_id))
        p = st.get("pos", [self.pos().x(), self.pos().y()]); self.setPos(QPointF(float(p[0]), float(p[1])))
        self.setRotation(float(st.get("rotation", self.rotation()))); self.update()

    def begin_edit(self) -> None:
        if self._edit_before is None: self._edit_before = self.to_state()

    def end_edit(self) -> None:
        if self._edit_before is None: return
        b, a = self._edit_before, self.to_state(); self._edit_before = None
        if b != a: self.edited.emit(self, b, a)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.begin_edit(); super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event); self.end_edit()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.ItemSelectedHasChanged: self.setZValue(10000.0 if bool(value) else 0.0)
        return super().itemChange(change, value)


class RectLike(EditableBase):
    def __init__(self, w: float, h: float, class_id: int = 0):
        super().__init__(class_id=class_id)
        self.w, self.h = float(max(1.0, w)), float(max(1.0, h))
        self._active_handle, self._resize_anchor_scene = None, None

    def local_rect(self) -> QRectF: return QRectF(-self.w/2, -self.h/2, self.w, self.h)
    def boundingRect(self) -> QRectF: return self.local_rect().adjusted(-5000, -5000, 5000, 5000)

    def _px_to_local(self, px: float) -> float:
        sc = self.scene(); s = max(1e-6, sc.views()[0].transform().m11()) if sc and sc.views() else 1.0
        return px / s

    def _hit_handle(self, p_local: QPointF) -> Optional[str]:
        if not self.isSelected(): return None
        self._update_scale_cache(); lx, ly = p_local.x(), p_local.y(); w2, h2 = self.w/2, self.h/2
        if self.anno_type == "obb":
            if math.hypot(lx, ly + h2 + ROTATE_HANDLE_OFFSET) < (ROT_HIT_PX/self._cached_scale): return HandleKind.ROT
        hit = HANDLE_HIT_PX / self._cached_scale
        if math.hypot(lx + w2, ly + h2) < hit: return HandleKind.TL
        if math.hypot(lx - w2, ly + h2) < hit: return HandleKind.TR
        if math.hypot(lx - w2, ly - h2) < hit: return HandleKind.BR
        if math.hypot(lx + w2, ly - h2) < hit: return HandleKind.BL
        edge = EDGE_HIT_PX / self._cached_scale
        if abs(ly+h2) < edge and abs(lx) < w2: return HandleKind.T
        if abs(ly-h2) < edge and abs(lx) < w2: return HandleKind.B
        if abs(lx+w2) < edge and abs(ly) < h2: return HandleKind.L
        if abs(lx-w2) < edge and abs(ly) < h2: return HandleKind.R
        return None

    def hoverMoveEvent(self, event):
        if self.isSelected():
            h = self._hit_handle(event.pos())
            if h == HandleKind.ROT: self.setCursor(Qt.PointingHandCursor)
            elif h in (HandleKind.TL, HandleKind.BR): self.setCursor(Qt.SizeFDiagCursor)
            elif h in (HandleKind.TR, HandleKind.BL): self.setCursor(Qt.SizeBDiagCursor)
            elif h in (HandleKind.T, HandleKind.B): self.setCursor(Qt.SizeVerCursor)
            elif h in (HandleKind.L, HandleKind.R): self.setCursor(Qt.SizeHorCursor)
            else: self.unsetCursor()
        super().hoverMoveEvent(event)

    def shape(self) -> QPainterPath:
        if self._is_in_draw_mode(): return QPainterPath()
        self._update_scale_cache(); base = QPainterPath(); base.addRect(self.local_rect())
        stroker = QPainterPathStroker(); stroker.setWidth(120.0/self._cached_scale if self.isSelected() else 15.0/self._cached_scale)
        return stroker.createStroke(base).united(base)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pp = QPainterPath(); pp.addRect(self.local_rect()); self._paint_alt_highlight(painter, pp)
        painter.setPen(self.pen()); painter.setBrush(self.brush()); painter.drawRect(self.local_rect())
        self._paint_alt_label(painter, self.local_rect().topLeft() - QPointF(0, 5)); self._paint_stats_hud(painter)
        if self.isSelected():
            self._update_scale_cache(); s = 10.0 / self._cached_scale; r = self.local_rect()
            painter.setPen(QPen(Qt.black, 1.0)); painter.setBrush(Qt.white)
            for pt in [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]: painter.drawRect(QRectF(pt.x()-s/2, pt.y()-s/2, s, s))
            if self.anno_type == "obb": painter.setBrush(QColor(255, 170, 0)); painter.drawEllipse(QPointF(0, -self.h/2 - ROTATE_HANDLE_OFFSET), s/2, s/2)

    def _start_resize(self, h):
        self._active_handle = h; self._resize_rotation_deg = float(self.rotation()); w2, h2 = self.w/2, self.h/2
        m = {HandleKind.TL:(w2,h2), HandleKind.TR:(-w2,h2), HandleKind.BR:(-w2,-h2), HandleKind.BL:(w2,-h2), HandleKind.T:(0,h2), HandleKind.B:(0,-h2), HandleKind.L:(w2,0), HandleKind.R:(-w2,0)}
        self._resize_anchor_scene = self.mapToScene(QPointF(m[h][0], m[h][1])); self._resize_anchor_sxsy = (1 if m[h][0]>0 else -1, 1 if m[h][1]>0 else -1)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.begin_edit(); ctrl = bool(event.modifiers() & Qt.ControlModifier); self.setFlag(QGraphicsItem.ItemIsMovable, ctrl)
        if self.isSelected() and event.button() == Qt.LeftButton:
            h = self._hit_handle(event.pos())
            if h:
                if h == HandleKind.ROT: self._active_handle = HandleKind.ROT
                else: self._start_resize(h)
                event.accept(); return
            if not ctrl:
                corners = [HandleKind.TL, HandleKind.TR, HandleKind.BR, HandleKind.BL]
                r = self.local_rect(); cs = {HandleKind.TL: r.topLeft(), HandleKind.TR: r.topRight(), HandleKind.BR: r.bottomRight(), HandleKind.BL: r.bottomLeft()}
                c = min(corners, key=lambda k: math.hypot(event.pos().x()-cs[k].x(), event.pos().y()-cs[k].y())); self._start_resize(c); event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self._active_handle: super().mouseMoveEvent(event); return
        if self._active_handle == HandleKind.ROT:
            v = event.scenePos() - self.mapToScene(QPointF(0,0)); self.setRotation(math.degrees(math.atan2(v.y(), v.x())) + 90.0)
        elif self._resize_anchor_scene:
            vl = _rot_scene_to_local(event.scenePos() - self._resize_anchor_scene, self._resize_rotation_deg); sx, sy = self._resize_anchor_sxsy; hk = self._active_handle
            self.w = max(MIN_SIZE, vl.x() * -sx) if hk not in (HandleKind.T, HandleKind.B) else self.w
            self.h = max(MIN_SIZE, vl.y() * -sy) if hk not in (HandleKind.L, HandleKind.R) else self.h
            self.prepareGeometryChange(); tx = sx * self.w/2 if hk not in (HandleKind.T, HandleKind.B) else 0; ty = sy * self.h/2 if hk not in (HandleKind.L, HandleKind.R) else 0
            self.setPos(self._resize_anchor_scene - _rot_local_to_scene(QPointF(tx, ty), self._resize_rotation_deg))
        self.update(); event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None: super().mouseReleaseEvent(event); self._active_handle = None; self.end_edit()


class BBoxItem(RectLike):
    anno_type = "bbox"
    def pen(self): p = super().pen(); p.setStyle(Qt.DashLine); return p
    def ultralytics_row(self, iw, ih) -> str:
        r = self.mapToScene(self.local_rect()).boundingRect()
        xc, yc, w, h = r.center().x()/iw, r.center().y()/ih, r.width()/iw, r.height()/ih
        return f"{self.class_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}"
    def to_label_dict(self, iw, ih):
        r = self.mapToScene(self.local_rect()).boundingRect()
        return {"id": self.get_anno_id(), "type": "bbox", "class_id": self.class_id, "yolo_bbox": {"x_center": r.center().x()/iw, "y_center": r.center().y()/ih, "width": r.width()/iw, "height": r.height()/ih}, "ultralytics_row": self.ultralytics_row(iw, ih)}


class OBBItem(RectLike):
    anno_type = "obb"
    def corners_scene(self) -> List[QPointF]: 
        r = self.local_rect(); return [self.mapToScene(p) for p in [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]]
    def ultralytics_row(self, iw, ih) -> str:
        cs = self.corners_scene(); coords = []
        for p in cs: xn, yn = norm_xy(p.x(), p.y(), iw, ih); coords.extend([f"{xn:.6f}", f"{yn:.6f}"])
        return f"{self.class_id} " + " ".join(coords)
    def to_label_dict(self, iw, ih):
        cs = [[norm_xy(p.x(), p.y(), iw, ih)[0], norm_xy(p.x(), p.y(), iw, ih)[1]] for p in self.corners_scene()]
        return {"id": self.get_anno_id(), "type": "obb", "class_id": self.class_id, "yolo_obb": {"corners": cs}, "ultralytics_row": self.ultralytics_row(iw, ih)}


class PolygonItem(EditableBase):
    anno_type = "polygon"
    def __init__(self, points: List[QPointF], class_id: int = 0):
        super().__init__(class_id=class_id); self.points, self._active_vtx, self._vertex_edit_mode = [QPointF(p) for p in points], None, False
    def set_vertex_edit_mode(self, on: bool): self._vertex_edit_mode = bool(on); self.update()
    def vertex_edit_mode(self): return self._vertex_edit_mode
    def boundingRect(self): xs, ys = [p.x() for p in self.points], [p.y() for p in self.points]; return QRectF(min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)).adjusted(-5000, -5000, 5000, 5000)
    def hoverMoveEvent(self, event):
        if self._vertex_edit_mode:
            self._update_scale_cache(); idx = self._nearest_vertex(event.pos())
            if idx is not None and math.hypot(event.pos().x()-self.points[idx].x(), event.pos().y()-self.points[idx].y()) < (VTX_HIT_PX/self._cached_scale):
                self.setCursor(Qt.PointingHandCursor); return
        self.unsetCursor(); super().hoverMoveEvent(event)
    def shape(self):
        if self._is_in_draw_mode(): return QPainterPath()
        self._update_scale_cache(); b = QPainterPath(); b.addPolygon(QPolygonF(self.points))
        s = QPainterPathStroker(); s.setWidth(120.0/self._cached_scale if self._vertex_edit_mode else 15.0/self._cached_scale); return s.createStroke(b).united(b)
    def paint(self, painter: QPainter, option, widget=None):
        pp = QPainterPath(); pp.addPolygon(QPolygonF(self.points)); self._alt_highlight() and self._paint_alt_highlight(painter, pp)
        p = QPen(Qt.red if self._vertex_edit_mode else self.pen().color(), 2.0, Qt.DashLine if self._vertex_edit_mode else Qt.SolidLine)
        painter.setPen(p); painter.setBrush(self.brush()); len(self.points) >= 3 and painter.drawPolygon(QPolygonF(self.points))
        self.points and self._paint_alt_label(painter, self.points[0] - QPointF(0, 5)); self._paint_stats_hud(painter)
        if self.isSelected() or self._vertex_edit_mode:
            self._update_scale_cache(); d = 11.0 / self._cached_scale; painter.setBrush(Qt.white); painter.setPen(QPen(Qt.red if self._vertex_edit_mode else Qt.black, 1.0))
            for pt in self.points: painter.drawRect(QRectF(pt.x()-d/2, pt.y()-d/2, d, d))
            if self._vertex_edit_mode:
                label = " [ EDITING VERTEX ] "; fm = QFontMetricsF(painter.font()); br = fm.boundingRect(label).adjusted(-10, -5, 10, 5); bb = QPolygonF(self.points).boundingRect()
                anchor = QPointF(bb.center().x() - br.width()/2, bb.top() - 30); painter.setPen(Qt.NoPen); painter.setBrush(QBrush(QColor(220, 0, 0, 230))); painter.drawRoundedRect(QRectF(anchor.x(), anchor.y()-br.height(), br.width(), br.height()), 5, 5); painter.setPen(Qt.white); painter.drawText(anchor, label)
    def _nearest_vertex(self, p_local: QPointF):
        if not self.points: return None
        return min(range(len(self.points)), key=lambda i: math.hypot(p_local.x()-self.points[i].x(), p_local.y()-self.points[i].y()))
    def mousePressEvent(self, event):
        if self._is_in_draw_mode(): event.ignore(); return
        self.begin_edit(); ctrl = bool(event.modifiers() & Qt.ControlModifier); self.setFlag(QGraphicsItem.ItemIsMovable, ctrl)
        if self._vertex_edit_mode and event.button() == Qt.LeftButton and not ctrl:
            idx = self._nearest_vertex(event.pos()); self._update_scale_cache()
            if idx is not None and math.hypot(event.pos().x()-self.points[idx].x(), event.pos().y()-self.points[idx].y()) < (150.0/self._cached_scale):
                self._active_vtx = idx; event.accept(); return
        super().mousePressEvent(event)
    def mouseMoveEvent(self, event):
        if self._active_vtx is not None: self.points[self._active_vtx] = event.pos(); self.prepareGeometryChange(); self.update(); event.accept(); return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self, event): super().mouseReleaseEvent(event); self._active_vtx = None; self.end_edit()
    def ultralytics_row(self, iw, ih) -> str:
        pts = self.points; coords = []
        for p in pts: sp = self.mapToScene(p); xn, yn = norm_xy(sp.x(), sp.y(), iw, ih); coords.extend([f"{xn:.6f}", f"{yn:.6f}"])
        return f"{self.class_id} " + " ".join(coords)
    def to_label_dict(self, iw, ih):
        pts = [[norm_xy(self.mapToScene(pt).x(), self.mapToScene(pt).y(), iw, ih)[0], norm_xy(self.mapToScene(pt).x(), self.mapToScene(pt).y(), iw, ih)[1]] for pt in self.points]
        return {"id": self.get_anno_id(), "type": "polygon", "class_id": self.class_id, "yolo_seg": {"points": pts}, "ultralytics_row": self.ultralytics_row(iw, ih)}