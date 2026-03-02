PySide6 GUI Annotator (BBox + OBB + Polygon Mask), JSON-only export (Ultralytics rows in JSON).

Run:
  pip install pyside6
  python main.py

Outputs:
  <output_dir>/labels_json/<relative_path>/<image_stem>.json
  <output_dir>/project_state.json

Keybindings:
  V Select
  B BBox
  O OBB
  M Polygon Mask (Enter/RightClick to finish)
  Ctrl+S Save JSON
  N Next (save)
  PageUp / PageDown Prev/Next (save)
  Ctrl+Z / Ctrl+Y Undo/Redo
  Del Delete selected
  F Fit
  Wheel Zoom
  Space+Drag Pan
  Alt+N Add class
  Alt+R Rename class