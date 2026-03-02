import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _natural_key(s: str) -> List[Any]:
    # Natural sort: "img2" < "img10"
    parts = re.split(r"(\d+)", s)
    out: List[Any] = []
    for p in parts:
        if p.isdigit():
            out.append(int(p))
        else:
            out.append(p.lower())
    return out


def list_images(root: Path) -> List[Path]:
    items: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            items.append(p)

    # Sort by relative path (natural), then full path as tiebreaker.
    def sort_key(p: Path) -> Tuple[List[Any], str]:
        try:
            rel = p.relative_to(root).as_posix()
        except Exception:
            rel = p.as_posix()
        return _natural_key(rel), p.as_posix()

    items.sort(key=sort_key)
    return items


def clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def norm_xy(x: float, y: float, w: int, h: int) -> Tuple[float, float]:
    if w <= 0 or h <= 0:
        return 0.0, 0.0
    return clamp01(x / float(w)), clamp01(y / float(h))


def _safe_relpath(child: Path, parent: Path) -> Path:
    """
    Best-effort relative path. Avoids ValueError on Windows when drives differ,
    and avoids crashing when folder structure changes.
    """
    try:
        return child.relative_to(parent)
    except Exception:
        try:
            rel = os.path.relpath(str(child), str(parent))
            # relpath can return paths like "..\\..\\x"; we don't want that in output tree.
            # If it escapes, fall back to filename only.
            if rel.startswith(".."):
                return Path(child.name)
            return Path(rel)
        except Exception:
            return Path(child.name)


@dataclass
class ProjectState:
    input_dir: str = ""
    output_dir: str = ""
    classes: List[str] = None  # type: ignore
    index: int = 0

    # For future i18n: "en" | "zh" | "bilingual"
    ui_language: str = "bilingual"

    def __post_init__(self) -> None:
        if self.classes is None:
            self.classes = []
        # Keep state resilient if JSON has wrong types.
        if not isinstance(self.classes, list):
            self.classes = []
        self.index = int(self.index or 0)
        if not isinstance(self.ui_language, str) or not self.ui_language:
            self.ui_language = "bilingual"


def load_project_state(path: Path) -> ProjectState:
    try:
        txt = path.read_text(encoding="utf-8-sig")
        data = json.loads(txt) if txt.strip() else {}
        return ProjectState(
            input_dir=str(data.get("input_dir", "")),
            output_dir=str(data.get("output_dir", "")),
            classes=list(data.get("classes", [])),
            index=int(data.get("index", 0)),
            ui_language=str(data.get("ui_language", "bilingual")),
        )
    except Exception:
        return ProjectState()


def save_project_state(path: Path, st: ProjectState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(st)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def labels_json_path(output_dir: Path, input_dir: Path, img_path: Path) -> Path:
    rel = _safe_relpath(img_path, input_dir)
    out = output_dir / "labels_json" / rel.parent / (img_path.stem + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def load_image_labels(json_path: Path) -> Optional[Dict[str, Any]]:
    if not json_path.exists():
        return None
    try:
        txt = json_path.read_text(encoding="utf-8-sig")
        return json.loads(txt) if txt.strip() else None
    except Exception:
        return None


def save_image_labels(json_path: Path, payload: Dict[str, Any]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")