"""Interactive, bounded-memory raster for saved NetPyNE/PDCM spike events.

The CSV files remain the source of truth. A sparse in-memory byte-offset index
allows time-window reads without loading an entire paper-length run into RAM.
Only display points are sampled; event counts always use every matching row.
"""

from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
from threading import Event

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .snapshot import HIGH_RESOLUTION_WIDTH, save_image_with_dialog
from .style import DARK_THEME, normalize_theme, theme_color


PDCM_POPULATIONS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
POPULATION_COLORS = (
    "#5eafff", "#8bc8ff", "#ed9350", "#f9bf70",
    "#ad83ec", "#d1aeff", "#45c899", "#97dec0",
)
DEFAULT_DISPLAY_LIMIT = 72_000


class RasterCancelled(Exception):
    """A newer view superseded this background CSV scan."""


@dataclass(frozen=True)
class SpikeRasterIndex:
    run_dir: Path
    spike_path: Path
    spike_size: int
    spike_mtime_ns: int
    cell_path: Path
    cell_size: int
    cell_mtime_ns: int
    # (time_ms, byte offset); all offsets point to complete data rows.
    offsets: tuple[tuple[float, int], ...]
    populations: tuple[str, ...]
    cell_counts: dict[str, int]
    gid_rows: dict[int, tuple[str, int]]
    event_count: int
    earliest_ms: float
    duration_ms: float


@dataclass(frozen=True)
class SpikeRasterWindow:
    start_ms: float
    end_ms: float
    populations: tuple[str, ...]
    # Each point is (time_ms, zero-based row within its population).
    points: dict[str, tuple[tuple[float, int], ...]]
    event_counts: dict[str, int]
    total_events: int
    displayed_events: int


def _run_directory(path: str | Path) -> Path:
    selected = Path(path).expanduser().resolve()
    return selected.parent if selected.is_file() and selected.name == "summary.json" else selected


def _parse_spike_row(raw: bytes, line_number: int) -> tuple[float, int, str]:
    fields = raw.rstrip(b"\r\n").split(b",")
    if len(fields) != 3:
        raise ValueError(f"Invalid spike CSV row {line_number}: expected 3 columns")
    try:
        time_ms = float(fields[0])
        gid = int(fields[1])
        population = fields[2].decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid spike CSV row {line_number}") from exc
    if not math.isfinite(time_ms) or time_ms < 0 or gid < 0 or not population:
        raise ValueError(f"Invalid spike CSV row {line_number}")
    return time_ms, gid, population


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RasterCancelled


def build_spike_index(
    run_dir: str | Path,
    *,
    stride_rows: int = 8192,
    cancel: Event | None = None,
) -> SpikeRasterIndex:
    """Index time-sorted PDCM events using O(cells + spikes/stride) memory."""

    if stride_rows < 1:
        raise ValueError("stride_rows must be positive")
    root = _run_directory(run_dir)
    spike_path = root / "spikes.csv"
    cell_path = root / "cells.csv"
    if not spike_path.is_file() or not cell_path.is_file():
        raise FileNotFoundError("NetPyNE raster needs spikes.csv and cells.csv in the saved run")

    gid_rows: dict[int, tuple[str, int]] = {}
    cell_counts: dict[str, int] = {}
    with cell_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"gid", "population"}.issubset(reader.fieldnames):
            raise ValueError("cells.csv needs gid and population columns")
        for line_number, row in enumerate(reader, start=2):
            _check_cancel(cancel)
            try:
                gid = int(row["gid"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid cell gid at row {line_number}") from exc
            population = str(row["population"] or "").strip()
            if gid < 0 or not population or gid in gid_rows:
                raise ValueError(f"Invalid or duplicate cell at row {line_number}")
            ordinal = cell_counts.get(population, 0)
            gid_rows[gid] = (population, ordinal)
            cell_counts[population] = ordinal + 1
    if not gid_rows:
        raise ValueError("cells.csv contains no cortical cells")

    offsets: list[tuple[float, int]] = []
    count = 0
    earliest = 0.0
    latest = 0.0
    with spike_path.open("rb") as stream:
        if stream.readline().lstrip(b"\xef\xbb\xbf").rstrip(b"\r\n") != b"time_ms,gid,population":
            raise ValueError("spikes.csv needs time_ms,gid,population columns")
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw:
                break
            if count % 8192 == 0:
                _check_cancel(cancel)
            time_ms, gid, population = _parse_spike_row(raw, count + 2)
            mapped = gid_rows.get(gid)
            if mapped is None or mapped[0] != population:
                raise ValueError(f"Spike row {count + 2} does not match cells.csv")
            if count and time_ms < latest:
                raise ValueError("spikes.csv must be sorted by time for interactive browsing")
            if count == 0:
                earliest = time_ms
            if count % stride_rows == 0:
                offsets.append((time_ms, offset))
            latest = time_ms
            count += 1

    duration_ms = latest if count else 0.0
    summary_path = root / "summary.json"
    if summary_path.is_file():
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            stated_duration = float(payload.get("metadata", {}).get("duration_ms", 0))
            if math.isfinite(stated_duration) and stated_duration > duration_ms:
                duration_ms = stated_duration
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    ordered = tuple(pop for pop in PDCM_POPULATIONS if pop in cell_counts)
    ordered += tuple(sorted(set(cell_counts) - set(ordered)))
    spike_stat = spike_path.stat()
    cell_stat = cell_path.stat()
    return SpikeRasterIndex(
        root, spike_path, spike_stat.st_size, spike_stat.st_mtime_ns,
        cell_path, cell_stat.st_size, cell_stat.st_mtime_ns,
        tuple(offsets), ordered, cell_counts, gid_rows,
        count, earliest, duration_ms,
    )


def query_spike_window(
    index: SpikeRasterIndex,
    start_ms: float,
    end_ms: float,
    *,
    populations: tuple[str, ...] | list[str] | set[str] | None = None,
    max_points: int = DEFAULT_DISPLAY_LIMIT,
    cancel: Event | None = None,
) -> SpikeRasterWindow:
    """Count all matching events while retaining a deterministic bounded sample."""

    if not math.isfinite(start_ms) or not math.isfinite(end_ms) or end_ms <= start_ms:
        raise ValueError("Choose a valid time range with End greater than Start")
    chosen = tuple(pop for pop in index.populations if populations is None or pop in populations)
    if max_points < max(1, len(chosen)):
        raise ValueError("max_points must allow at least one point per selected population")
    for path, size, mtime_ns in (
        (index.spike_path, index.spike_size, index.spike_mtime_ns),
        (index.cell_path, index.cell_size, index.cell_mtime_ns),
    ):
        stat = path.stat()
        if stat.st_size != size or stat.st_mtime_ns != mtime_ns:
            raise ValueError(f"Saved run data changed; reload {path.name}")
    if not chosen or not index.offsets:
        return SpikeRasterWindow(start_ms, end_ms, chosen, {p: () for p in chosen}, {p: 0 for p in chosen}, 0, 0)

    chosen_set = set(chosen)
    per_population_limit = max_points // len(chosen)
    counts = {pop: 0 for pop in chosen}
    reservoirs: dict[str, list[tuple[float, int]]] = {pop: [] for pop in chosen}
    randomizers = {
        pop: random.Random(0xD16F1 ^ sum((i + 1) * ord(c) for i, c in enumerate(pop)))
        for pop in chosen
    }
    times = [item[0] for item in index.offsets]
    # Step back one sparse block: equal timestamps can straddle a block edge.
    block = max(0, bisect_left(times, start_ms) - 1)
    with index.spike_path.open("rb") as stream:
        stream.seek(index.offsets[block][1])
        scanned = 0
        while raw := stream.readline():
            if scanned % 8192 == 0:
                _check_cancel(cancel)
            time_ms, gid, population = _parse_spike_row(raw, scanned + 1)
            scanned += 1
            if time_ms < start_ms:
                continue
            if time_ms > end_ms:
                break
            if population not in chosen_set:
                continue
            mapped = index.gid_rows.get(gid)
            if mapped is None or mapped[0] != population:
                raise ValueError("Saved spike data no longer matches its indexed cells")
            counts[population] += 1
            sample = reservoirs[population]
            point = (time_ms, mapped[1])
            if len(sample) < per_population_limit:
                sample.append(point)
            else:
                slot = randomizers[population].randrange(counts[population])
                if slot < per_population_limit:
                    sample[slot] = point
    points = {pop: tuple(sorted(reservoirs[pop])) for pop in chosen}
    return SpikeRasterWindow(
        start_ms, end_ms, chosen, points, counts,
        sum(counts.values()), sum(map(len, points.values())),
    )


class _RasterTask(QThread):
    loaded = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        run_dir: Path,
        index: SpikeRasterIndex | None,
        view: tuple[float, float] | None,
        populations: tuple[str, ...] | None,
        max_points: int,
    ):
        super().__init__()
        self.run_dir = run_dir
        self.index = index
        self.view = view
        self.populations = populations
        self.max_points = max_points
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            index = self.index or build_spike_index(self.run_dir, cancel=self.cancel_event)
            if self.view is None:
                start = 100.0 if index.duration_ms > 100 else 0.0
                end = min(600.0, index.duration_ms)
                if end <= start:
                    end = max(start + 1.0, index.duration_ms)
            else:
                start, end = self.view
            window = query_spike_window(
                index, start, end, populations=self.populations,
                max_points=self.max_points, cancel=self.cancel_event,
            )
            _check_cancel(self.cancel_event)
            self.loaded.emit(index, window)
        except RasterCancelled:
            return
        except Exception as exc:
            self.failed.emit(str(exc))


class _RasterCanvas(QWidget):
    window_requested = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setObjectName("SpikeRasterCanvas")
        self.setMinimumSize(440, 340)
        self.index: SpikeRasterIndex | None = None
        self.window: SpikeRasterWindow | None = None
        self._drag_start: QPointF | None = None
        self.setMouseTracking(True)
        self.setToolTip("Wheel to zoom around the pointer · drag to select a time interval · right-click for full run")

    def set_data(self, index: SpikeRasterIndex | None, window: SpikeRasterWindow | None) -> None:
        self.index = index
        self.window = window
        self.update()

    def _theme(self) -> str:
        app = QApplication.instance()
        return normalize_theme(app.property("digiflyTheme") if app is not None else DARK_THEME)

    @staticmethod
    def _plot_rect(rect: QRectF) -> QRectF:
        return rect.adjusted(79.0, 25.0, -20.0, -49.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint(painter, QRectF(self.rect()))
        painter.end()

    def _paint(self, painter: QPainter, bounds: QRectF) -> None:
        theme = self._theme()
        painter.fillRect(bounds, QColor(theme_color(theme, "stimulus_surface")))
        plot = self._plot_rect(bounds)
        painter.setPen(QPen(QColor(theme_color(theme, "stimulus_border")), 1))
        painter.drawRect(bounds.adjusted(0.5, 0.5, -0.5, -0.5))
        window = self.window
        index = self.index
        if window is None or index is None:
            painter.setPen(QColor(theme_color(theme, "muted")))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "Load a NetPyNE run to browse spikes")
            return
        if not window.populations:
            painter.setPen(QColor(theme_color(theme, "muted")))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "Select a population to display")
            return
        if plot.width() <= 0 or plot.height() <= 0:
            return

        muted = QColor(theme_color(theme, "muted"))
        painter.setPen(muted)
        qualifier = " · display sample" if window.displayed_events < window.total_events else ""
        painter.drawText(
            QRectF(plot.left(), bounds.top() + 3, plot.width(), 18),
            Qt.AlignmentFlag.AlignCenter,
            f"Spike raster · {window.displayed_events:,} of {window.total_events:,} events{qualifier}",
        )
        grid = QColor(theme_color(theme, "stimulus_grid"))
        axis = QColor(theme_color(theme, "stimulus_axis"))
        for tick in range(6):
            x = plot.left() + tick * plot.width() / 5
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            time_ms = window.start_ms + tick * (window.end_ms - window.start_ms) / 5
            painter.setPen(muted)
            label = f"{time_ms / 1000:.2f} s" if window.end_ms - window.start_ms >= 5000 else f"{time_ms:.1f}"
            painter.drawText(QRectF(x - 38, plot.bottom() + 5, 76, 20), Qt.AlignmentFlag.AlignCenter, label)
        painter.setPen(axis)
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.drawText(QRectF(plot.left(), plot.bottom() + 28, plot.width(), 17), Qt.AlignmentFlag.AlignCenter, "Time (ms)" if window.end_ms - window.start_ms < 5000 else "Time")

        total_cells = sum(index.cell_counts[pop] for pop in window.populations)
        floor_height = min(25.0, plot.height() / len(window.populations))
        remaining_height = max(0.0, plot.height() - floor_height * len(window.populations))
        band_top = plot.top()
        painter.save()
        painter.setClipRect(plot)
        for pop_index, pop in enumerate(window.populations):
            cells = index.cell_counts[pop]
            band_height = floor_height + remaining_height * cells / total_cells
            band = QRectF(plot.left(), band_top, plot.width(), band_height)
            if pop_index % 2:
                stripe = QColor(theme_color(theme, "section_header"))
                stripe.setAlpha(65)
                painter.fillRect(band, stripe)
            color = QColor(POPULATION_COLORS[PDCM_POPULATIONS.index(pop) % len(POPULATION_COLORS)] if pop in PDCM_POPULATIONS else POPULATION_COLORS[pop_index % len(POPULATION_COLORS)])
            color.setAlpha(190)
            points = QPolygonF()
            for time_ms, row in window.points[pop]:
                x = plot.left() + (time_ms - window.start_ms) / (window.end_ms - window.start_ms) * plot.width()
                y = band.top() + 2 + (row + 0.5) / cells * max(1.0, band.height() - 4)
                points.append(QPointF(x, y))
            painter.setPen(QPen(color, 1.0))
            if points:
                painter.drawPoints(points)
            band_top += band_height
        painter.restore()

        band_top = plot.top()
        for pop in window.populations:
            cells = index.cell_counts[pop]
            band_height = floor_height + remaining_height * cells / total_cells
            painter.setPen(muted)
            painter.drawText(QRectF(bounds.left() + 7, band_top, 69, min(25.0, band_height)), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{pop}  {cells:,}")
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(plot.left(), band_top), QPointF(plot.right(), band_top))
            band_top += band_height

    def render_high_resolution(self, *, width: int = HIGH_RESOLUTION_WIDTH) -> QImage:
        width = max(int(width), self.width(), 1)
        height = max(1, round(width * max(1, self.height()) / max(1, self.width())))
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        factor = width / max(1, self.width())
        painter.scale(factor, factor)
        self._paint(painter, QRectF(0, 0, self.width(), self.height()))
        painter.end()
        return image

    def _time_for_x(self, x: float) -> float:
        assert self.window is not None
        plot = self._plot_rect(QRectF(self.rect()))
        fraction = min(1.0, max(0.0, (x - plot.left()) / max(1.0, plot.width())))
        return self.window.start_ms + fraction * (self.window.end_ms - self.window.start_ms)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.window is None or self.index is None:
            return
        old_span = self.window.end_ms - self.window.start_ms
        new_span = min(max(self.index.duration_ms, 1.0), max(1.0, old_span * (0.75 if event.angleDelta().y() > 0 else 1.35)))
        anchor = self._time_for_x(event.position().x())
        relative = (anchor - self.window.start_ms) / old_span
        start = max(0.0, min(max(0.0, self.index.duration_ms - new_span), anchor - relative * new_span))
        self.window_requested.emit(start, start + new_span)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.window is not None:
            self._drag_start = event.position()
        elif event.button() == Qt.MouseButton.RightButton and self.index is not None:
            self.window_requested.emit(0.0, max(1.0, self.index.duration_ms))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_start is not None and event.button() == Qt.MouseButton.LeftButton:
            if abs(event.position().x() - self._drag_start.x()) >= 8:
                start = self._time_for_x(min(self._drag_start.x(), event.position().x()))
                end = self._time_for_x(max(self._drag_start.x(), event.position().x()))
                if end > start:
                    self.window_requested.emit(start, end)
            self._drag_start = None
        event.accept()


class SpikeRasterWidget(QWidget):
    """Results-ready Qt widget for PDCM spikes with asynchronous CSV queries."""

    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, max_display_points: int = DEFAULT_DISPLAY_LIMIT):
        super().__init__(parent)
        self.setObjectName("SpikeRasterWidget")
        self.max_display_points = max_display_points
        self.index: SpikeRasterIndex | None = None
        self.window: SpikeRasterWindow | None = None
        self.population_checks: dict[str, QCheckBox] = {}
        self._run_dir: Path | None = None
        self._task: _RasterTask | None = None
        self._revision = 0
        self._pending = False
        self._desired_view: tuple[float, float] | None = None
        self._desired_populations: tuple[str, ...] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Time (ms)"))
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setObjectName("SpikeRasterStart")
        self.start_spin.setDecimals(1)
        self.start_spin.setSingleStep(100.0)
        self.start_spin.setEnabled(False)
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setObjectName("SpikeRasterEnd")
        self.end_spin.setDecimals(1)
        self.end_spin.setSingleStep(100.0)
        self.end_spin.setEnabled(False)
        controls.addWidget(self.start_spin)
        controls.addWidget(QLabel("to"))
        controls.addWidget(self.end_spin)
        self.whole_run_button = QPushButton("Whole run")
        self.whole_run_button.setEnabled(False)
        self.whole_run_button.clicked.connect(self.show_whole_run)
        controls.addWidget(self.whole_run_button)
        self.reset_button = QPushButton("First 500 ms")
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self.show_first_500_ms)
        controls.addWidget(self.reset_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.population_row = QHBoxLayout()
        self.population_row.setSpacing(7)
        self.population_row.addStretch(1)
        layout.addLayout(self.population_row)
        self.canvas = _RasterCanvas()
        self.canvas.window_requested.connect(self._set_view)
        layout.addWidget(self.canvas, 1)
        footer = QHBoxLayout()
        self.count_label = QLabel("No spikes loaded")
        self.count_label.setObjectName("Muted")
        self.count_label.setWordWrap(True)
        footer.addWidget(self.count_label, 1)
        self.export_button = QPushButton("Save raster…")
        self.export_button.setEnabled(False)
        self.export_button.setToolTip("Save a high-resolution PNG of the current displayed sample and time range")
        self.export_button.clicked.connect(self.save_current_view)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(140)
        self._debounce.timeout.connect(self._request_current_view)
        self.start_spin.valueChanged.connect(lambda _value: self._debounce.start())
        self.end_spin.valueChanged.connect(lambda _value: self._debounce.start())
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self._stop_worker)

    def set_run(self, run_dir_or_summary: str | Path) -> None:
        """Load a saved PDCM run; indexing/querying never blocks the GUI thread."""

        self._debounce.stop()
        self._revision += 1
        self._run_dir = _run_directory(run_dir_or_summary)
        self._desired_view = None
        self._desired_populations = None
        self.index = None
        self.window = None
        self.canvas.set_data(None, None)
        self.count_label.setText("Indexing saved spikes…")
        self.export_button.setEnabled(False)
        self._clear_population_checks()
        if self._task is not None and self._task.isRunning():
            self._task.cancel()
            self._pending = True
        else:
            self._launch()

    def clear_run(self) -> None:
        self._debounce.stop()
        self._revision += 1
        self._run_dir = None
        self.index = None
        self.window = None
        self._desired_view = None
        self._desired_populations = None
        self.canvas.set_data(None, None)
        self.count_label.setText("No spikes loaded")
        self.export_button.setEnabled(False)
        self._clear_population_checks()
        if self._task is not None:
            self._task.cancel()
        self._pending = False

    def _clear_population_checks(self) -> None:
        for checkbox in self.population_checks.values():
            self.population_row.removeWidget(checkbox)
            checkbox.deleteLater()
        self.population_checks.clear()
        for control in (self.start_spin, self.end_spin, self.whole_run_button, self.reset_button):
            control.setEnabled(False)

    def _launch(self) -> None:
        if self._run_dir is None:
            return
        revision = self._revision
        task = _RasterTask(
            self._run_dir, self.index, self._desired_view,
            self._desired_populations, self.max_display_points,
        )
        self._task = task
        self._pending = False
        task.loaded.connect(lambda index, window, rev=revision: self._on_loaded(rev, index, window))
        task.failed.connect(lambda message, rev=revision: self._on_failed(rev, message))
        task.finished.connect(self._on_finished)
        task.start()

    def _on_loaded(self, revision: int, index: SpikeRasterIndex, window: SpikeRasterWindow) -> None:
        if revision != self._revision:
            return
        first_load = self.index is None
        self.index = index
        self.window = window
        if first_load:
            upper = max(1.0, index.duration_ms)
            for spin in (self.start_spin, self.end_spin):
                spin.blockSignals(True)
                spin.setRange(0.0, upper)
                spin.setEnabled(True)
            self.whole_run_button.setEnabled(True)
            self.reset_button.setEnabled(True)
            for pop in index.populations:
                checkbox = QCheckBox(pop)
                checkbox.setObjectName(f"SpikeRasterPopulation{pop}")
                checkbox.setChecked(True)
                checkbox.setAccessibleName(f"Display {pop} spikes")
                checkbox.toggled.connect(lambda _checked: self._debounce.start())
                self.population_row.insertWidget(self.population_row.count() - 1, checkbox)
                self.population_checks[pop] = checkbox
        self.start_spin.blockSignals(True)
        self.end_spin.blockSignals(True)
        self.start_spin.setValue(window.start_ms)
        self.end_spin.setValue(window.end_ms)
        self.start_spin.blockSignals(False)
        self.end_spin.blockSignals(False)
        self.canvas.set_data(index, window)
        suffix = " (display sampled; raw CSV unchanged)" if window.displayed_events < window.total_events else ""
        self.count_label.setText(
            f"{window.displayed_events:,} shown of {window.total_events:,} spikes in view"
            f" · {index.event_count:,} in saved run{suffix}"
        )
        self.export_button.setEnabled(bool(window.populations))
        self.status_message.emit(self.count_label.text())

    def _on_failed(self, revision: int, message: str) -> None:
        if revision != self._revision:
            return
        self.count_label.setText(f"Could not browse spikes: {message}")
        self.status_message.emit(self.count_label.text())

    def _on_finished(self) -> None:
        task = self.sender()
        if not isinstance(task, _RasterTask):
            return
        if self._task is task:
            self._task = None
        task.deleteLater()
        if self._pending and self._run_dir is not None:
            self._launch()

    def _request_current_view(self) -> None:
        if self.index is None:
            return
        view = (self.start_spin.value(), self.end_spin.value())
        if view[1] <= view[0]:
            self.count_label.setText("End time must be greater than start time")
            return
        self._desired_view = view
        self._desired_populations = tuple(pop for pop, box in self.population_checks.items() if box.isChecked())
        self._revision += 1
        self.count_label.setText("Loading selected spikes…")
        if self._task is not None and self._task.isRunning():
            self._task.cancel()
            self._pending = True
        else:
            self._launch()

    def _set_view(self, start_ms: float, end_ms: float) -> None:
        self.start_spin.blockSignals(True)
        self.end_spin.blockSignals(True)
        self.start_spin.setValue(start_ms)
        self.end_spin.setValue(end_ms)
        self.start_spin.blockSignals(False)
        self.end_spin.blockSignals(False)
        self._request_current_view()

    def show_whole_run(self) -> None:
        if self.index is not None:
            self._set_view(0.0, max(1.0, self.index.duration_ms))

    def show_first_500_ms(self) -> None:
        if self.index is not None:
            start = 100.0 if self.index.duration_ms > 100 else 0.0
            self._set_view(start, max(start + 1.0, min(600.0, self.index.duration_ms)))

    def render_high_resolution(self, *, width: int = HIGH_RESOLUTION_WIDTH) -> QImage:
        return self.canvas.render_high_resolution(width=width)

    def save_current_view(self) -> Path | None:
        if self.window is None or self.index is None:
            return None
        image = self.render_high_resolution()
        saved = save_image_with_dialog(
            self, image, title="Save spike raster",
            default_name=f"{self.index.run_dir.name}-raster-{self.window.start_ms:g}-{self.window.end_ms:g}ms",
        )
        if saved is not None:
            self.status_message.emit(f"Saved spike raster to {saved}")
        return saved

    def _stop_worker(self) -> None:
        self._debounce.stop()
        self._run_dir = None
        self._pending = False
        self._revision += 1
        if self._task is not None and self._task.isRunning():
            self._task.cancel()
            self._task.wait()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_worker()
        super().closeEvent(event)
