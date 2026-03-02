from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene


def _block_item_signals(item: QGraphicsItem, blocked: bool) -> Optional[bool]:
    """
    Best-effort signal blocking. Only works for QGraphicsObject (QObject-based).
    Returns previous blocked state if available.
    """
    fn = getattr(item, "blockSignals", None)
    if not callable(fn):
        return None
    try:
        # Qt's blockSignals returns previous state
        return bool(fn(bool(blocked)))
    except Exception:
        return None


def apply_state(item: QGraphicsItem, st: Dict[str, Any]) -> None:
    """
    Apply serialized state to a graphics item if it exposes apply_state(st).
    We call prepareGeometryChange() beforehand to keep QGraphicsScene internals consistent
    when width/height/shape/boundingRect could change.
    """
    prep = getattr(item, "prepareGeometryChange", None)
    if callable(prep):
        try:
            prep()
        except Exception:
            pass

    prev_blocked = _block_item_signals(item, True)

    try:
        fn = getattr(item, "apply_state", None)
        if callable(fn):
            fn(st)
    finally:
        if prev_blocked is not None:
            # Restore previous state (if we could read it)
            _block_item_signals(item, prev_blocked)
        else:
            _block_item_signals(item, False)


class AddItemCommand(QUndoCommand):
    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        text: str = "Add",
        select_on_redo: bool = True,
    ):
        super().__init__(text)
        self.scene = scene
        self.item = item
        self.select_on_redo = bool(select_on_redo)

    def redo(self) -> None:
        if self.item.scene() is not self.scene:
            self.scene.addItem(self.item)
        if self.select_on_redo:
            try:
                self.scene.clearSelection()
                self.item.setSelected(True)
            except Exception:
                pass

    def undo(self) -> None:
        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)


class DeleteItemCommand(QUndoCommand):
    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        text: str = "Delete",
        select_on_undo: bool = True,
    ):
        super().__init__(text)
        self.scene = scene
        self.item = item
        self.select_on_undo = bool(select_on_undo)

    def redo(self) -> None:
        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)

    def undo(self) -> None:
        if self.item.scene() is not self.scene:
            self.scene.addItem(self.item)
        if self.select_on_undo:
            try:
                self.scene.clearSelection()
                self.item.setSelected(True)
            except Exception:
                pass


class ModifyItemCommand(QUndoCommand):
    def __init__(
        self,
        item: QGraphicsItem,
        before: Dict[str, Any],
        after: Dict[str, Any],
        text: str = "Modify",
    ):
        super().__init__(text)
        self.item = item
        # Deepcopy to avoid accidental external mutation.
        self.before = copy.deepcopy(before)
        self.after = copy.deepcopy(after)

    def redo(self) -> None:
        apply_state(self.item, self.after)

    def undo(self) -> None:
        apply_state(self.item, self.before)