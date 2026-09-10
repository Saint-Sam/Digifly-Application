from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QPoint, QStandardPaths
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget


HIGH_RESOLUTION_WIDTH = 3840


def safe_png_name(value: str, *, fallback: str = "digifly-visualization") -> str:
    """Return a filesystem-friendly PNG filename for a Save dialog default."""

    stem = Path(str(value).strip()).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return f"{stem or fallback}.png"


def render_widget_high_resolution(
    widget: QWidget,
    *,
    width: int = HIGH_RESOLUTION_WIDTH,
) -> QImage:
    """Render a paint-based Qt widget at a larger, export-oriented resolution."""

    logical_width = max(1, widget.width())
    logical_height = max(1, widget.height())
    target_width = max(logical_width, int(width))
    target_height = max(1, round(target_width * logical_height / logical_width))
    image = QImage(
        target_width,
        target_height,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(target_width / logical_width, target_height / logical_height)
    widget.render(painter, QPoint(0, 0))
    painter.end()
    return image


def save_image_with_dialog(
    parent: QWidget,
    image: QImage | QPixmap,
    *,
    title: str,
    default_name: str,
) -> Path | None:
    """Ask the user for an image name/location and save a PNG there."""

    pictures = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.PicturesLocation
    )
    initial_dir = Path(pictures).expanduser() if pictures else Path.home()
    initial_path = initial_dir / safe_png_name(default_name)
    selected, _ = QFileDialog.getSaveFileName(
        parent,
        title,
        str(initial_path),
        "PNG image (*.png)",
    )
    if not selected:
        return None
    output = Path(selected).expanduser()
    if output.suffix.casefold() != ".png":
        output = output.with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output), "PNG"):
        QMessageBox.critical(
            parent,
            "Could not save image",
            f"Digifly could not write the PNG to:\n{output}",
        )
        return None
    return output.resolve()
