# 🎨 Ultralytics GUI 标注器 (JSON) — Ultralytics GUI Annotator (JSON)

<p align="center">
  <img src="/demo/demo.png" width="900" title="Project Demo">
</p>

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)  
[![PySide6](https://img.shields.io/badge/UI-PySide6-green.svg)](https://pypi.org/project/PySide6/)  
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

简短介绍（中文）：
一款轻量、响应迅速的桌面标注工具，专为 YOLO / Ultralytics 训练数据准备设计。支持轴对齐矩形框（BBox）、旋转矩形框（OBB）与多边形掩码（Polygon Mask），并提供快捷键、撤销/重做、自动保存与结构化 JSON 导出，便于训练流水线使用。

Short description (English):
A lightweight, responsive desktop annotation tool tailored for preparing datasets for YOLO / Ultralytics. It supports axis-aligned bounding boxes (BBox), oriented bounding boxes (OBB), and polygon masks, and provides keyboard shortcuts, undo/redo, autosave and structured JSON export suitable for training pipelines.

---

## 目录 / Table of Contents
- 核心特性 / Key Features  
- 安装与运行 / Installation & Run  
- 快速上手 / Quick Start  
- 快捷键 / Keyboard Shortcuts  
- 项目结构 / Project Structure  
- 导出格式 / Export Format  
- 使用建议 / Pro Tips  
- 常见问题 / FAQ  
- 贡献 / Contributing  
- 许可证 / License

---

## ✨ 核心特性 / Key Features

- 多种标注类型：BBox（轴对齐矩形）、OBB（旋转矩形）、Polygon（多边形掩码）。  
  Multi-annotation types: BBox (axis-aligned), OBB (oriented bounding box) and Polygon (mask).

- 高效交互：常用快捷键、鼠标滚轮缩放、空格+拖拽平移、双击进入顶点编辑。  
  Efficient interaction: common keyboard shortcuts, wheel zoom, Space+drag panning and double-click vertex editing.

- 完整的历史操作：支持撤销/重做（Undo / Redo），操作可回溯。  
  Full history support: Undo/Redo available to revert user actions.

- 自动保存与手动导出：编辑过程支持自动保存，标注结果可导出为结构化 JSON。  
  Autosave and manual export: Edits are autosaved, and annotations can be exported as structured JSON.

- 可视化辅助：按住 Alt 可启用全局高亮与 HUD 统计面板，快速定位疑似噪声或面积异常的标注。  
  Visual assistants: Hold Alt to enable global highlight and a HUD with statistics to locate potential noise or abnormally small annotations.

- 图片按自然顺序排列（natural sort），方便顺序化标注流程。  
  Images are presented in natural sort order for convenient sequential annotation.

---

## 🛠️ 安装与运行 / Installation & Run

要求：Python 3.8+（建议使用虚拟环境）。  
Requirement: Python 3.8+ (virtual environment recommended).

1. 克隆仓库 | Clone the repository
```bash
git clone https://github.com/wangzai822/YOLO-format-annotation-tool-.git
cd YOLO-format-annotation-tool-
```

2. 创建并激活虚拟环境（可选） | Create and activate a virtual environment (optional)
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

3. 安装依赖 | Install dependencies
```bash
pip install -r requirements.txt   # 如果仓库包含 requirements.txt
# 或仅安装最小运行依赖
pip install pyside6
```

4. 启动程序 | Run the app
```bash
python main.py
```

---

## 🚀 快速上手 / Quick Start

1. 启动程序后，通过界面选择图片目录或单张图片导入。  
   After launching the app, choose an image folder or import a single image via the UI.

2. 使用 B / O / M / V 等快捷键切换绘制与选择模式，并用数字键（1–9）快速切换类别。  
   Use B/O/M/V keys to switch draw/select modes, and number keys (1–9) to switch classes quickly.

3. 程序会自动保存标注（autosave），并将结果写入输出目录（默认：labels_json）。也可以手动导出。  
   The tool autosaves annotations to the output directory (default: labels_json). Manual export is also available.

4. 使用 Alt 启用全局自检（高亮与 HUD），双击多边形进入顶点编辑，空格+拖拽用于画布平移。  
   Use Alt for a global inspection mode (highlight + HUD), double-click polygons to edit vertices, and Space+drag to pan the canvas.

---

## ⌨️ 快捷键 / Keyboard Shortcuts

| 功能 / Function | 快捷键 / Key | 说明 / Description |
|---|:---:|---|
| 选择/编辑 (Select / Edit) | V | 选中、移动或双击编辑已有标注。Select, move, or double-click to edit existing annotations. |
| 绘制矩形框 (BBox) | B | 绘制轴对齐矩形（Bounding Box）；拖拽确定。Draw an axis-aligned bounding box by dragging. |
| 绘制旋转框 (OBB) | O | 绘制并调整带旋转角度的矩形框。Draw an oriented bounding box with rotation and scaling. |
| 绘制多边形 (Polygon / Mask) | M | 左键逐点添加顶点，Enter 或右键完成。Left-click to add vertices; press Enter or right-click to finish. |
| 类别切换 (Switch class) | 1–9 | 按数字键快速切换当前标注类别。Use number keys to switch the active label class. |
| 下一张 (Save + Next) | N / PageDown | 保存当前并跳转到下一张图片。Save current annotations and go to the next image. |
| 上一张 (Save + Prev) | PageUp | 保存当前并返回上一张图片。Save current annotations and go to the previous image. |
| 全局高亮 (HUD / Inspect) | Hold Alt | 按住 Alt 显示所有标注编号与统计信息，便于检查噪声。Hold Alt to display annotation IDs and statistics for quick inspection. |
| 撤销 / 重做 (Undo / Redo) | Ctrl+Z / Ctrl+Y | 撤销或重做最近的编辑操作。Undo or redo recent edits. |
| 适应窗口 (Fit to View) | F | 将图像缩放以适配窗口大小。Scale the image to fit the window. |
| 画布平移 (Pan) | Space + Drag | 按住空格并拖动以平移画布。Hold Space and drag to pan the canvas. |

---

## 📂 项目结构 / Project Structure

```text
.
├── main.py
├── annotator/
│   ├── canvas.py
│   ├── shapes.py
│   ├── main_window.py
│   ├── undo.py
│   └── io_utils.py
├── assets/
├── requirements.txt
└── README.md
```

说明（中文 / English notes）：
- main.py：程序入口。Main entry point for the application.  
- annotator/：核心模块，包含画布交互、图形对象、UI 主窗口、撤销/重做与 IO 等功能。Core package containing canvas interaction, shape classes, main window, undo/redo and IO utilities.  
- assets/：图标、样式等资源。Icons and style assets.  
- requirements.txt：可选的依赖列表（如果存在）。Optional dependency list (if present).


---

## 📄 导出格式 / Export Format (JSON)

默认导出位置：labels_json/（或通过设置指定的输出目录）  
Default export location: labels_json/ (or a custom output directory configured in settings)

示例 JSON（每张图片的标注可存为单独文件，或将多张图片的标注写入一个汇总文件）：

```json
{
  "image": {
    "file_name": "image.jpg",
    "width": 1920,
    "height": 1080
  },
  "classes": [
    { "id": 0, "name": "damage" },
    { "id": 1, "name": "crack" }
  ],
  "annotations": [
    {
      "id": "bbox_0001",
      "type": "bbox",
      "class_id": 0,
      "yolo_bbox": {
        "x_center": 0.5,
        "y_center": 0.5,
        "width": 0.2,
        "height": 0.1
      },
      "raw_coords": [100, 200, 480, 360]
    },
    {
      "id": "poly_0002",
      "type": "polygon",
      "class_id": 1,
      "yolo_seg": {
        "points": [
          [0.12, 0.34],
          [0.15, 0.36],
          [0.17, 0.33]
        ]
      },
      "raw_points": [
        [230, 410],
        [290, 430],
        [310, 400]
      ]
    }
  ]
}
```

字段说明（中文 / English field descriptions）：
- image：图片元信息（file_name、width、height）。Image metadata (file_name, width, height).  
- classes：类别列表，包含 id 与 name。List of classes with id and name.  
- annotations：标注列表，每条包含 id、type（bbox/polygon/obb）、class_id 及坐标信息。Annotation list; each entry includes id, type (bbox/polygon/obb), class_id, and coordinates.  
- yolo_bbox：以图像宽高归一化的中心 x/y 与宽高（0~1），方便直接用于 Ultralytics/YOLO 格式。Normalized center x/y and width/height (0–1) suitable for Ultralytics/YOLO.  
- raw_coords / raw_points：像素坐标（可用于可视化或后续精校）。Pixel coordinates for visualization or fine tuning.

---

## 🌟 使用建议 / Pro Tips

- Alt 全局自检：长按 Alt 可在 HUD 中查看面积极小的标注（疑似噪声），便于数据清洗。  
  Alt-check: Hold Alt to view HUD warnings for very small areas (possible noise) to help clean data.

- 快速缩放：在选择模式下按住 Ctrl 并在选中框内部拖拽可进行整体缩放，无需精确拖拽角点。  
  Quick scale: In Select mode, hold Ctrl and drag inside a box to scale it without grabbing small corner handles.

- 顶点精调：双击多边形进入顶点编辑模式，可拖拽单个顶点以调整形状。  
  Vertex editing: Double-click a polygon to edit vertices; drag single vertices to refine shape.

- 批量可见性控制：在标注列表中可勾选/反选来控制多条标注的显隐状态，便于查看重叠或遮挡区域。  
  Batch visibility: Use annotation list checkboxes to toggle visibility for multiple annotations to inspect overlaps.

---

## ❓ 常见问题 / FAQ

Q：程序运行报错或窗口无法显示？  
A：请确认 Python 版本 >= 3.8 且已安装 PySide6（或 requirements.txt 中列出的依赖）。建议在虚拟环境中安装并重试。  
Q: App fails to start or window does not appear?  
A: Ensure Python >= 3.8 and PySide6 (or packages from requirements.txt) are installed. Try reinstalling dependencies in a virtual environment.

Q：如何将导出的 JSON 转换为 COCO 或 YOLO txt 格式？  
A：导出的 JSON 已包含归一化的 yolo_bbox 与像素坐标（raw_* 字段），可以基于这些字段编写脚本将数据转换为 COCO 或 YOLO 格式。  
Q: How to convert exported JSON to COCO or YOLO txt?  
A: The JSON includes normalized yolo_bbox and raw pixel coordinates (raw_* fields); you can write a small script to convert them to COCO or YOLO. 

---

## 🤝 贡献 / Contributing

欢迎通过 Issues 报告问题或提出改进建议；也欢迎通过 Fork + Pull Request 的方式提交代码改进。请在 PR 中说明改动内容与动机，并在涉及 UI 变更时提供必要的截图或示例。  
We welcome issues for bug reports or feature requests. Contributions via Fork + Pull Request are appreciated; please describe your changes and rationale in the PR and include screenshots or examples when UI/visual changes are involved.

---

## 📜 许可证 / License

本项目采用 MIT 许可证。详情请参见仓库中的 LICENSE 文件。  
This project is licensed under the MIT License. See the LICENSE file in the repository for details.

---

## ✉️ 联系 / Contact

GitHub: https://github.com/wangzai822/YOLO-format-annotation-tool-  

Made with 😎 by wangzai822 / Created by wangzai822

✉️ wangw00821@gmail.com

---


