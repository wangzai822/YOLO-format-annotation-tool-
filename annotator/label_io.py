from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtGui import QImageReader

from .io_utils import clamp01, labels_json_path, load_image_labels, norm_xy, normalize_class_records, safe_relpath

IMPORT_FORMAT_AUTO = "auto"

FORMAT_INTERNAL_JSON = "internal_json"
FORMAT_YOLO_BBOX = "yolo_bbox_txt"
FORMAT_ULTRALYTICS_SEG = "ultralytics_seg_txt"
FORMAT_ULTRALYTICS_OBB = "ultralytics_obb_txt"
FORMAT_COCO = "coco_json"

EXPORT_FORMAT_INTERNAL_JSON = FORMAT_INTERNAL_JSON
EXPORT_FORMAT_YOLO_BBOX = FORMAT_YOLO_BBOX
EXPORT_FORMAT_ULTRALYTICS_SEG = FORMAT_ULTRALYTICS_SEG
EXPORT_FORMAT_ULTRALYTICS_OBB = FORMAT_ULTRALYTICS_OBB
EXPORT_FORMAT_COCO = FORMAT_COCO

IMPORT_FORMAT_OPTIONS = [
    ("Auto 自动识别", IMPORT_FORMAT_AUTO),
    ("Workspace JSON 工作区JSON", FORMAT_INTERNAL_JSON),
    ("YOLO BBox TXT 检测框", FORMAT_YOLO_BBOX),
    ("Ultralytics Seg TXT 多边形", FORMAT_ULTRALYTICS_SEG),
    ("Ultralytics OBB TXT 旋转框", FORMAT_ULTRALYTICS_OBB),
    ("COCO JSON", FORMAT_COCO),
]

EXPORT_FORMAT_OPTIONS = [
    ("YOLO BBox TXT 检测框", EXPORT_FORMAT_YOLO_BBOX),
    ("Ultralytics Seg TXT 多边形", EXPORT_FORMAT_ULTRALYTICS_SEG),
    ("Ultralytics OBB TXT 旋转框", EXPORT_FORMAT_ULTRALYTICS_OBB),
    ("COCO JSON", EXPORT_FORMAT_COCO),
]

FORMAT_DISPLAY_NAMES = {
    IMPORT_FORMAT_AUTO: "Auto 自动识别",
    FORMAT_INTERNAL_JSON: "Workspace JSON 工作区JSON",
    FORMAT_YOLO_BBOX: "YOLO BBox TXT 检测框",
    FORMAT_ULTRALYTICS_SEG: "Ultralytics Seg TXT 多边形",
    FORMAT_ULTRALYTICS_OBB: "Ultralytics OBB TXT 旋转框",
    FORMAT_COCO: "COCO JSON",
}

FORMAT_TOOLTIPS = {
    IMPORT_FORMAT_AUTO: (
        "Auto 自动识别\n"
        "- 优先读取输出目录中的工作区 JSON\n"
        "- 如果没有，再去外部标注目录匹配同名 .json / .txt\n"
        "- 最后尝试 COCO JSON\n"
        "- 适合一个数据集只使用一种标签格式的场景"
    ),
    FORMAT_INTERNAL_JSON: (
        "Workspace JSON 工作区 JSON\n"
        "- 本工具的可编辑工作格式\n"
        "- 单图一个 JSON\n"
        "- 可同时保存 bbox / obb / polygon / classes\n"
        "- 最适合后续继续编辑和增量补标"
    ),
    FORMAT_YOLO_BBOX: (
        "YOLO BBox TXT 检测框\n"
        "- 每行: class x_center y_center width height\n"
        "- 坐标为归一化 0~1\n"
        "- 只表达轴对齐框 bbox\n"
        "- 导出时只会写 bbox 标注"
    ),
    FORMAT_ULTRALYTICS_SEG: (
        "Ultralytics Seg TXT 多边形\n"
        "- 每行: class x1 y1 x2 y2 x3 y3 ...\n"
        "- 坐标为归一化 0~1\n"
        "- 适合实例分割/多边形轮廓\n"
        "- 导出时只会写 polygon 标注"
    ),
    FORMAT_ULTRALYTICS_OBB: (
        "Ultralytics OBB TXT 旋转框\n"
        "- 每行: class x1 y1 x2 y2 x3 y3 x4 y4\n"
        "- 坐标为归一化 0~1\n"
        "- 用 4 个角点表达旋转框\n"
        "- 导出时只会写 obb 标注"
    ),
    FORMAT_COCO: (
        "COCO JSON\n"
        "- 数据集级格式，不是单图单文件\n"
        "- 主要字段: images / annotations / categories\n"
        "- 导入时优先用 segmentation，多边形不存在时回退到 bbox\n"
        "- 导出时会把整个当前数据集写成一个 instances.json"
    ),
}


def format_display_name(fmt: str) -> str:
    return FORMAT_DISPLAY_NAMES.get(str(fmt or ""), str(fmt or ""))


def format_tooltip(fmt: str) -> str:
    return FORMAT_TOOLTIPS.get(str(fmt or ""), str(fmt or ""))


def image_size(path: Path) -> Tuple[int, int]:
    reader = QImageReader(str(path))
    size = reader.size()
    if size.isValid():
        return int(size.width()), int(size.height())
    return 0, 0


@lru_cache(maxsize=16)
def _load_json_cached(path_str: str, mtime_ns: int) -> Any:
    txt = Path(path_str).read_text(encoding="utf-8-sig")
    return json.loads(txt) if txt.strip() else {}


def _load_json_file(path: Path) -> Any:
    try:
        stat = path.stat()
    except Exception:
        return {}
    try:
        return _load_json_cached(str(path), int(stat.st_mtime_ns))
    except Exception:
        return {}


def _read_nonempty_lines(path: Path) -> List[str]:
    try:
        txt = path.read_text(encoding="utf-8-sig")
    except Exception:
        return []
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    out: List[Path] = []
    seen = set()
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _candidate_sidecar_paths(label_dir: Path, input_dir: Path, img_path: Path, suffix: str) -> List[Path]:
    rel = safe_relpath(img_path, input_dir)
    paths = [
        label_dir / rel.parent / (img_path.stem + suffix),
        label_dir / (img_path.stem + suffix),
    ]

    try:
        recursive = sorted(label_dir.rglob(img_path.stem + suffix))
        if len(recursive) == 1:
            paths.append(recursive[0])
    except Exception:
        pass

    return [p for p in _dedupe_paths(paths) if p.exists()]


def _is_internal_json(path: Path) -> bool:
    data = _load_json_file(path)
    return isinstance(data, dict) and data.get("schema") == "ultralytics-json-v1"


def _is_coco_json(path: Path) -> bool:
    data = _load_json_file(path)
    return (
        isinstance(data, dict)
        and isinstance(data.get("images"), list)
        and isinstance(data.get("annotations"), list)
        and isinstance(data.get("categories"), list)
    )


def _infer_txt_format(path: Path) -> Optional[str]:
    lines = _read_nonempty_lines(path)
    if not lines:
        return None

    counts: List[int] = []
    for ln in lines[:20]:
        parts = ln.split()
        if len(parts) < 5:
            return None
        counts.append(len(parts))

    if counts and all(c == 5 for c in counts):
        return FORMAT_YOLO_BBOX
    if counts and all(c == 9 for c in counts):
        return FORMAT_ULTRALYTICS_OBB
    if counts and all(c >= 7 and c % 2 == 1 for c in counts):
        return FORMAT_ULTRALYTICS_SEG
    return None


def _find_coco_json_path(label_dir: Path) -> Optional[Path]:
    try:
        for p in sorted(label_dir.rglob("*.json")):
            if _is_coco_json(p):
                return p
    except Exception:
        pass
    return None


def find_label_source(
    label_dir: Optional[Path],
    input_dir: Optional[Path],
    img_path: Path,
    import_format: str = IMPORT_FORMAT_AUTO,
) -> Optional[Tuple[str, Path]]:
    if label_dir is None or input_dir is None or not label_dir.exists():
        return None

    import_format = str(import_format or IMPORT_FORMAT_AUTO)

    if import_format == FORMAT_INTERNAL_JSON:
        for p in _candidate_sidecar_paths(label_dir, input_dir, img_path, ".json"):
            if _is_internal_json(p):
                return FORMAT_INTERNAL_JSON, p
        return None

    if import_format in (FORMAT_YOLO_BBOX, FORMAT_ULTRALYTICS_SEG, FORMAT_ULTRALYTICS_OBB):
        for p in _candidate_sidecar_paths(label_dir, input_dir, img_path, ".txt"):
            if _infer_txt_format(p) == import_format:
                return import_format, p
        return None

    if import_format == FORMAT_COCO:
        coco_path = _find_coco_json_path(label_dir)
        if coco_path is not None:
            return FORMAT_COCO, coco_path
        return None

    # Auto
    for p in _candidate_sidecar_paths(label_dir, input_dir, img_path, ".json"):
        if _is_internal_json(p):
            return FORMAT_INTERNAL_JSON, p

    for p in _candidate_sidecar_paths(label_dir, input_dir, img_path, ".txt"):
        fmt = _infer_txt_format(p)
        if fmt is not None:
            return fmt, p

    coco_path = _find_coco_json_path(label_dir)
    if coco_path is not None:
        return FORMAT_COCO, coco_path

    return None


def _class_stub_records(class_ids: List[int]) -> List[Dict[str, Any]]:
    return [{"id": cid, "name": f"class_{cid}"} for cid in sorted(set(class_ids))]


def load_internal_label_doc(path: Path) -> Optional[Dict[str, Any]]:
    data = load_image_labels(path)
    if not data:
        return None
    return {
        "format": FORMAT_INTERNAL_JSON,
        "source_path": path,
        "image": data.get("image", {}),
        "classes": normalize_class_records(data.get("classes", [])),
        "annotations": list(data.get("annotations", [])),
    }


def load_yolo_bbox_txt(path: Path) -> Optional[Dict[str, Any]]:
    annotations: List[Dict[str, Any]] = []
    class_ids: List[int] = []

    for idx, ln in enumerate(_read_nonempty_lines(path), start=1):
        parts = ln.split()
        if len(parts) != 5:
            continue
        try:
            cls = int(float(parts[0]))
            xc = clamp01(float(parts[1]))
            yc = clamp01(float(parts[2]))
            w = clamp01(float(parts[3]))
            h = clamp01(float(parts[4]))
        except Exception:
            continue

        class_ids.append(cls)
        annotations.append(
            {
                "id": f"bbox_{path.stem}_{idx:06d}",
                "type": "bbox",
                "class_id": cls,
                "yolo_bbox": {
                    "x_center": xc,
                    "y_center": yc,
                    "width": w,
                    "height": h,
                },
            }
        )

    return {
        "format": FORMAT_YOLO_BBOX,
        "source_path": path,
        "classes": _class_stub_records(class_ids),
        "annotations": annotations,
    }


def load_ultralytics_seg_txt(path: Path) -> Optional[Dict[str, Any]]:
    annotations: List[Dict[str, Any]] = []
    class_ids: List[int] = []

    for idx, ln in enumerate(_read_nonempty_lines(path), start=1):
        parts = ln.split()
        if len(parts) < 7 or len(parts) % 2 == 0:
            continue
        try:
            cls = int(float(parts[0]))
            nums = [float(x) for x in parts[1:]]
        except Exception:
            continue

        pts = []
        for i in range(0, len(nums), 2):
            pts.append([clamp01(nums[i]), clamp01(nums[i + 1])])

        if len(pts) < 3:
            continue

        class_ids.append(cls)
        annotations.append(
            {
                "id": f"polygon_{path.stem}_{idx:06d}",
                "type": "polygon",
                "class_id": cls,
                "yolo_seg": {"points": pts},
            }
        )

    return {
        "format": FORMAT_ULTRALYTICS_SEG,
        "source_path": path,
        "classes": _class_stub_records(class_ids),
        "annotations": annotations,
    }


def load_ultralytics_obb_txt(path: Path) -> Optional[Dict[str, Any]]:
    annotations: List[Dict[str, Any]] = []
    class_ids: List[int] = []

    for idx, ln in enumerate(_read_nonempty_lines(path), start=1):
        parts = ln.split()
        if len(parts) != 9:
            continue
        try:
            cls = int(float(parts[0]))
            nums = [float(x) for x in parts[1:]]
        except Exception:
            continue

        corners = []
        for i in range(0, len(nums), 2):
            corners.append([clamp01(nums[i]), clamp01(nums[i + 1])])

        if len(corners) != 4:
            continue

        class_ids.append(cls)
        annotations.append(
            {
                "id": f"obb_{path.stem}_{idx:06d}",
                "type": "obb",
                "class_id": cls,
                "yolo_obb": {"corners": corners},
            }
        )

    return {
        "format": FORMAT_ULTRALYTICS_OBB,
        "source_path": path,
        "classes": _class_stub_records(class_ids),
        "annotations": annotations,
    }


def load_coco_label_doc(coco_path: Path, input_dir: Path, img_path: Path) -> Optional[Dict[str, Any]]:
    data = _load_json_file(coco_path)
    if not isinstance(data, dict):
        return None

    images = data.get("images", [])
    annotations = data.get("annotations", [])
    categories = normalize_class_records(data.get("categories", []))

    target_rel = safe_relpath(img_path, input_dir).as_posix().replace("\\", "/").lower()
    target_name = img_path.name.lower()

    exact = []
    by_name = []

    for entry in images:
        if not isinstance(entry, dict):
            continue
        file_name = str(entry.get("file_name", "")).replace("\\", "/")
        if file_name.lower() == target_rel:
            exact.append(entry)
        if Path(file_name).name.lower() == target_name:
            by_name.append(entry)

    image_entry: Optional[Dict[str, Any]] = None
    if len(exact) == 1:
        image_entry = exact[0]
    elif not exact and len(by_name) == 1:
        image_entry = by_name[0]

    if image_entry is None:
        return None

    try:
        image_id = image_entry.get("id")
        iw = int(image_entry.get("width", 0))
        ih = int(image_entry.get("height", 0))
    except Exception:
        return None

    anns_out: List[Dict[str, Any]] = []
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        if ann.get("image_id") != image_id:
            continue

        try:
            cls = int(ann.get("category_id", 0))
        except Exception:
            cls = 0

        seg = ann.get("segmentation")
        if isinstance(seg, list):
            polys = [p for p in seg if isinstance(p, list) and len(p) >= 6 and len(p) % 2 == 0]
            if polys and iw > 0 and ih > 0:
                poly = max(polys, key=len)
                pts = []
                for i in range(0, len(poly), 2):
                    xn, yn = norm_xy(float(poly[i]), float(poly[i + 1]), iw, ih)
                    pts.append([xn, yn])

                if len(pts) >= 3:
                    anns_out.append(
                        {
                            "id": f"coco_polygon_{ann.get('id', len(anns_out) + 1)}",
                            "type": "polygon",
                            "class_id": cls,
                            "yolo_seg": {"points": pts},
                        }
                    )
                    continue

        bbox = ann.get("bbox")
        if isinstance(bbox, list) and len(bbox) >= 4 and iw > 0 and ih > 0:
            try:
                x, y, w, h = [float(v) for v in bbox[:4]]
            except Exception:
                continue
            xc = x + w / 2.0
            yc = y + h / 2.0
            xcn, ycn = norm_xy(xc, yc, iw, ih)
            wn = clamp01(w / float(iw))
            hn = clamp01(h / float(ih))
            anns_out.append(
                {
                    "id": f"coco_bbox_{ann.get('id', len(anns_out) + 1)}",
                    "type": "bbox",
                    "class_id": cls,
                    "yolo_bbox": {
                        "x_center": xcn,
                        "y_center": ycn,
                        "width": wn,
                        "height": hn,
                    },
                }
            )

    return {
        "format": FORMAT_COCO,
        "source_path": coco_path,
        "image": {
            "file_name": str(image_entry.get("file_name", img_path.name)),
            "width": iw,
            "height": ih,
        },
        "classes": categories,
        "annotations": anns_out,
    }


def load_external_label_doc(
    label_dir: Optional[Path],
    input_dir: Optional[Path],
    img_path: Path,
    import_format: str = IMPORT_FORMAT_AUTO,
) -> Optional[Dict[str, Any]]:
    src = find_label_source(label_dir, input_dir, img_path, import_format)
    if src is None or input_dir is None:
        return None

    fmt, path = src
    if fmt == FORMAT_INTERNAL_JSON:
        return load_internal_label_doc(path)
    if fmt == FORMAT_YOLO_BBOX:
        return load_yolo_bbox_txt(path)
    if fmt == FORMAT_ULTRALYTICS_SEG:
        return load_ultralytics_seg_txt(path)
    if fmt == FORMAT_ULTRALYTICS_OBB:
        return load_ultralytics_obb_txt(path)
    if fmt == FORMAT_COCO:
        return load_coco_label_doc(path, input_dir, img_path)
    return None


def load_workspace_label_doc(output_dir: Optional[Path], input_dir: Optional[Path], img_path: Path) -> Optional[Dict[str, Any]]:
    if output_dir is None or input_dir is None:
        return None
    jp = labels_json_path(output_dir, input_dir, img_path, create_parent=False)
    if not jp.exists():
        return None
    return load_internal_label_doc(jp)


def load_best_label_doc(
    output_dir: Optional[Path],
    label_dir: Optional[Path],
    input_dir: Optional[Path],
    img_path: Path,
    import_format: str = IMPORT_FORMAT_AUTO,
) -> Optional[Dict[str, Any]]:
    doc = load_workspace_label_doc(output_dir, input_dir, img_path)
    if doc is not None:
        return doc
    return load_external_label_doc(label_dir, input_dir, img_path, import_format)


def _text_output_path(
    output_dir: Path,
    subdir: str,
    input_dir: Path,
    img_path: Path,
    create_parent: bool = False,
) -> Path:
    rel = safe_relpath(img_path, input_dir)
    out = output_dir / subdir / rel.parent / (img_path.stem + ".txt")
    if create_parent:
        out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _write_rows_or_remove(path: Path, rows: List[str]) -> Path:
    rows = [str(r).strip() for r in rows if str(r).strip()]
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    elif path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    return path


def _bbox_row_from_ann(ann: Dict[str, Any]) -> str:
    bb = ann.get("yolo_bbox", {})
    cls = int(ann.get("class_id", 0))
    return (
        f"{cls} "
        f"{float(bb.get('x_center', 0.0)):.6f} "
        f"{float(bb.get('y_center', 0.0)):.6f} "
        f"{float(bb.get('width', 0.0)):.6f} "
        f"{float(bb.get('height', 0.0)):.6f}"
    )


def _seg_row_from_ann(ann: Dict[str, Any]) -> str:
    pts = ann.get("yolo_seg", {}).get("points", [])
    cls = int(ann.get("class_id", 0))
    coords = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        coords.append(f"{float(p[0]):.6f}")
        coords.append(f"{float(p[1]):.6f}")
    return f"{cls} " + " ".join(coords)


def _obb_row_from_ann(ann: Dict[str, Any]) -> str:
    pts = ann.get("yolo_obb", {}).get("corners", [])
    cls = int(ann.get("class_id", 0))
    coords = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        coords.append(f"{float(p[0]):.6f}")
        coords.append(f"{float(p[1]):.6f}")
    return f"{cls} " + " ".join(coords)


def export_yolo_bbox_txt(output_dir: Path, input_dir: Path, img_path: Path, annotations: List[Dict[str, Any]]) -> Path:
    path = _text_output_path(output_dir, "labels_yolo_bbox", input_dir, img_path)
    rows = []
    for ann in annotations:
        if ann.get("type") != "bbox":
            continue
        rows.append(str(ann.get("ultralytics_row") or _bbox_row_from_ann(ann)))
    return _write_rows_or_remove(path, rows)


def export_ultralytics_seg_txt(output_dir: Path, input_dir: Path, img_path: Path, annotations: List[Dict[str, Any]]) -> Path:
    path = _text_output_path(output_dir, "labels_ultralytics_seg", input_dir, img_path)
    rows = []
    for ann in annotations:
        if ann.get("type") != "polygon":
            continue
        rows.append(str(ann.get("ultralytics_row") or _seg_row_from_ann(ann)))
    return _write_rows_or_remove(path, rows)


def export_ultralytics_obb_txt(output_dir: Path, input_dir: Path, img_path: Path, annotations: List[Dict[str, Any]]) -> Path:
    path = _text_output_path(output_dir, "labels_ultralytics_obb", input_dir, img_path)
    rows = []
    for ann in annotations:
        if ann.get("type") != "obb":
            continue
        rows.append(str(ann.get("ultralytics_row") or _obb_row_from_ann(ann)))
    return _write_rows_or_remove(path, rows)


def coco_json_path(output_dir: Path, create_parent: bool = True) -> Path:
    path = output_dir / "coco" / "instances.json"
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _polygon_points_px_from_ann(ann: Dict[str, Any], iw: int, ih: int) -> List[Tuple[float, float]]:
    typ = str(ann.get("type", ""))

    if typ == "polygon":
        pts = ann.get("yolo_seg", {}).get("points", [])
        return [(float(p[0]) * iw, float(p[1]) * ih) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]

    if typ == "obb":
        pts = ann.get("yolo_obb", {}).get("corners", [])
        return [(float(p[0]) * iw, float(p[1]) * ih) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]

    return []


def _bbox_from_points(points: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    left = min(xs)
    top = min(ys)
    width = max(xs) - left
    height = max(ys) - top
    return left, top, width, height


def _polygon_area(points: List[Tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def _annotation_to_coco(
    ann: Dict[str, Any],
    image_id: int,
    annotation_id: int,
    iw: int,
    ih: int,
) -> Optional[Dict[str, Any]]:
    try:
        cls = int(ann.get("class_id", 0))
    except Exception:
        cls = 0

    typ = str(ann.get("type", ""))

    if typ == "bbox":
        bb = ann.get("yolo_bbox", {})
        xc = float(bb.get("x_center", 0.0)) * iw
        yc = float(bb.get("y_center", 0.0)) * ih
        w = float(bb.get("width", 0.0)) * iw
        h = float(bb.get("height", 0.0)) * ih
        x = xc - w / 2.0
        y = yc - h / 2.0
        return {
            "id": annotation_id,
            "image_id": image_id,
            "category_id": cls,
            "bbox": [x, y, w, h],
            "area": max(0.0, w * h),
            "iscrowd": 0,
            "segmentation": [],
        }

    if typ in ("polygon", "obb"):
        pts = _polygon_points_px_from_ann(ann, iw, ih)
        if len(pts) < 3:
            return None
        bbox = _bbox_from_points(pts)
        seg = [coord for p in pts for coord in p]
        return {
            "id": annotation_id,
            "image_id": image_id,
            "category_id": cls,
            "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
            "area": _polygon_area(pts),
            "iscrowd": 0,
            "segmentation": [seg],
        }

    return None


def build_coco_payload(
    output_dir: Path,
    input_dir: Path,
    image_paths: List[Path],
    class_lookup: Dict[int, str],
    label_dir: Optional[Path] = None,
    import_format: str = IMPORT_FORMAT_AUTO,
) -> Dict[str, Any]:
    images_payload: List[Dict[str, Any]] = []
    annotations_payload: List[Dict[str, Any]] = []
    used_class_ids = set()
    ann_id = 1

    for image_id, img_path in enumerate(image_paths, start=1):
        doc = load_workspace_label_doc(output_dir, input_dir, img_path)
        if doc is None:
            doc = load_external_label_doc(label_dir, input_dir, img_path, import_format)

        iw = 0
        ih = 0
        if doc is not None:
            img_meta = doc.get("image", {})
            try:
                iw = int(img_meta.get("width", 0))
                ih = int(img_meta.get("height", 0))
            except Exception:
                iw, ih = 0, 0

        if iw <= 0 or ih <= 0:
            iw, ih = image_size(img_path)

        rel_name = safe_relpath(img_path, input_dir).as_posix().replace("\\", "/")
        images_payload.append(
            {
                "id": image_id,
                "file_name": rel_name,
                "width": iw,
                "height": ih,
            }
        )

        if doc is None or iw <= 0 or ih <= 0:
            continue

        for ann in doc.get("annotations", []):
            coco_ann = _annotation_to_coco(ann, image_id, ann_id, iw, ih)
            if coco_ann is None:
                continue
            annotations_payload.append(coco_ann)
            used_class_ids.add(int(ann.get("class_id", 0)))
            ann_id += 1

    category_ids = set(used_class_ids)
    for cid, name in class_lookup.items():
        if str(name or "").strip():
            category_ids.add(int(cid))

    categories_payload = [
        {"id": cid, "name": str(class_lookup.get(cid) or f"class_{cid}")}
        for cid in sorted(category_ids)
    ]

    return {
        "info": {"description": "Exported by Ultralytics GUI Annotator"},
        "images": images_payload,
        "annotations": annotations_payload,
        "categories": categories_payload,
    }


def export_coco_dataset(
    output_dir: Path,
    input_dir: Path,
    image_paths: List[Path],
    class_lookup: Dict[int, str],
    label_dir: Optional[Path] = None,
    import_format: str = IMPORT_FORMAT_AUTO,
) -> Path:
    payload = build_coco_payload(
        output_dir=output_dir,
        input_dir=input_dir,
        image_paths=image_paths,
        class_lookup=class_lookup,
        label_dir=label_dir,
        import_format=import_format,
    )
    path = coco_json_path(output_dir, create_parent=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path