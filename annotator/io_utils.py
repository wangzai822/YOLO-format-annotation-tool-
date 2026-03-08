import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

DEFAULT_IMPORT_LABEL_FORMAT = "auto"
DEFAULT_EXPORT_FORMATS = ["internal_json"]
DATASET_STATE_DIRNAME = "dataset_states"


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


def safe_relpath(child: Path, parent: Path) -> Path:
    """
    Best-effort relative path. Avoids ValueError on Windows when drives differ,
    and avoids crashing when folder structure changes.
    """
    try:
        return child.relative_to(parent)
    except Exception:
        try:
            rel = os.path.relpath(str(child), str(parent))
            if rel.startswith(".."):
                return Path(child.name)
            return Path(rel)
        except Exception:
            return Path(child.name)


def normalize_class_records(raw: Any) -> List[Dict[str, Any]]:
    """
    Supports both:
      - old format: ["class_a", "class_b", ...]
      - new format: [{"id": 0, "name": "class_a"}, ...]
    """
    out: List[Dict[str, Any]] = []
    used_ids = set()

    if not isinstance(raw, list):
        return out

    # Backward compatibility with old project_state.json format.
    if raw and all(isinstance(x, (str, type(None))) for x in raw):
        for idx, name in enumerate(raw):
            out.append({"id": idx, "name": str(name or "")})
        return out

    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("id"))
        except Exception:
            continue
        if cid in used_ids:
            continue
        used_ids.add(cid)
        out.append({"id": cid, "name": str(item.get("name", "") or "")})

    out.sort(key=lambda x: int(x.get("id", 0)))
    return out


def normalize_export_formats(raw: Any) -> List[str]:
    out: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item and item not in out:
                out.append(item)

    if "internal_json" not in out:
        out.insert(0, "internal_json")

    return out


@dataclass
class ProjectState:
    input_dir: str = ""
    label_dir: str = ""
    output_dir: str = ""
    classes: List[Dict[str, Any]] = None  # type: ignore[assignment]
    index: int = 0

    # For future i18n: "en" | "zh" | "bilingual"
    ui_language: str = "bilingual"

    # Import / export settings
    import_label_format: str = DEFAULT_IMPORT_LABEL_FORMAT
    export_formats: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.classes = normalize_class_records(self.classes)
        self.index = int(self.index or 0)

        if not isinstance(self.ui_language, str) or not self.ui_language:
            self.ui_language = "bilingual"

        if not isinstance(self.import_label_format, str) or not self.import_label_format:
            self.import_label_format = DEFAULT_IMPORT_LABEL_FORMAT

        self.export_formats = normalize_export_formats(self.export_formats)


@dataclass
class DatasetState:
    input_dir: str = ""
    label_dir: str = ""
    classes: List[Dict[str, Any]] = None  # type: ignore[assignment]
    index: int = 0
    import_label_format: str = DEFAULT_IMPORT_LABEL_FORMAT

    def __post_init__(self) -> None:
        self.classes = normalize_class_records(self.classes)
        self.index = int(self.index or 0)

        if not isinstance(self.import_label_format, str) or not self.import_label_format:
            self.import_label_format = DEFAULT_IMPORT_LABEL_FORMAT


def load_project_state(path: Path) -> ProjectState:
    try:
        txt = path.read_text(encoding="utf-8-sig")
        data = json.loads(txt) if txt.strip() else {}
        return ProjectState(
            input_dir=str(data.get("input_dir", "")),
            label_dir=str(data.get("label_dir", "")),
            output_dir=str(data.get("output_dir", "")),
            classes=data.get("classes", []),
            index=int(data.get("index", 0)),
            ui_language=str(data.get("ui_language", "bilingual")),
            import_label_format=str(data.get("import_label_format", DEFAULT_IMPORT_LABEL_FORMAT)),
            export_formats=data.get("export_formats", DEFAULT_EXPORT_FORMATS),
        )
    except Exception:
        return ProjectState()


def save_project_state(path: Path, st: ProjectState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(st)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _dataset_state_key(input_dir: Path) -> str:
    try:
        src = str(input_dir.resolve())
    except Exception:
        src = str(input_dir)

    digest = hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", input_dir.name or "dataset").strip("_")
    if not base:
        base = "dataset"
    return f"{base}_{digest}"


def dataset_state_path(output_dir: Path, input_dir: Path, create_parent: bool = True) -> Path:
    out = output_dir / DATASET_STATE_DIRNAME / f"{_dataset_state_key(input_dir)}.json"
    if create_parent:
        out.parent.mkdir(parents=True, exist_ok=True)
    return out


def load_dataset_state(path: Path) -> Optional[DatasetState]:
    if not path.exists():
        return None
    try:
        txt = path.read_text(encoding="utf-8-sig")
        data = json.loads(txt) if txt.strip() else {}
        return DatasetState(
            input_dir=str(data.get("input_dir", "")),
            label_dir=str(data.get("label_dir", "")),
            classes=data.get("classes", []),
            index=int(data.get("index", 0)),
            import_label_format=str(data.get("import_label_format", DEFAULT_IMPORT_LABEL_FORMAT)),
        )
    except Exception:
        return None


def save_dataset_state(path: Path, st: DatasetState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(st)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def labels_json_path(
    output_dir: Path,
    input_dir: Path,
    img_path: Path,
    create_parent: bool = True,
) -> Path:
    rel = safe_relpath(img_path, input_dir)
    out = output_dir / "labels_json" / rel.parent / (img_path.stem + ".json")
    if create_parent:
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