from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import dataclass
from math import cos, isfinite, radians, sin
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .snapshot import HIGH_RESOLUTION_WIDTH, render_widget_high_resolution
from .style import DARK_THEME, LIGHT_THEME, normalize_theme, theme_color


MODE_2D = "2d"
MODE_3D_STACK = "3d_stack"
TRACE_COLORS = (
    "#5eafff",
    "#4bd398",
    "#f2a14c",
    "#ef6f91",
    "#bd8cff",
    "#48c9dc",
    "#d0d85b",
    "#a5b8d9",
)
HOVER_TRACE_LIMIT = 12


@dataclass(frozen=True)
class VoltageTrace:
    key: str
    label: str
    condition: str
    repetition: int
    neuron_id: str
    times_ms: tuple[float, ...]
    voltages_mV: tuple[float, ...]
    color: str


def load_voltage_traces(
    path: str | Path,
    *,
    neuron_labels: Mapping[str, str] | None = None,
) -> tuple[VoltageTrace, ...]:
    """Load long-form Digifly voltage CSV data without adding a NumPy dependency."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return ()
    grouped: dict[tuple[str, int, str], list[tuple[float, float]]] = {}
    with source.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_ms = float(row.get("time_ms", ""))
                voltage_mV = float(row.get("voltage_mV", ""))
                repetition = int(float(row.get("repetition") or 0))
            except (TypeError, ValueError):
                continue
            neuron_id = str(row.get("neuron_id") or "").strip()
            condition = str(row.get("condition") or "Recorded run").strip()
            if neuron_id and isfinite(time_ms) and isfinite(voltage_mV):
                grouped.setdefault((condition, repetition, neuron_id), []).append(
                    (time_ms, voltage_mV)
                )

    repetition_counts: dict[str, set[int]] = {}
    for condition, repetition, _neuron_id in grouped:
        repetition_counts.setdefault(condition, set()).add(repetition)
    labels = {str(key): str(value) for key, value in dict(neuron_labels or {}).items()}
    traces: list[VoltageTrace] = []
    for index, ((condition, repetition, neuron_id), samples) in enumerate(grouped.items()):
        ordered = sorted(samples)
        neuron_label = labels.get(neuron_id, f"body {neuron_id}")
        repetition_suffix = (
            f" · rep {repetition}"
            if len(repetition_counts.get(condition, ())) > 1
            else ""
        )
        traces.append(
            VoltageTrace(
                key=f"{condition}\x1f{repetition}\x1f{neuron_id}",
                label=f"{condition} · {neuron_label}{repetition_suffix}",
                condition=condition,
                repetition=repetition,
                neuron_id=neuron_id,
                times_ms=tuple(item[0] for item in ordered),
                voltages_mV=tuple(item[1] for item in ordered),
                color=TRACE_COLORS[index % len(TRACE_COLORS)],
            )
        )
    return tuple(traces)


def voltage_at(trace: VoltageTrace, time_ms: float) -> float | None:
    """Linearly interpolate one trace at a requested simulation time."""

    if not trace.times_ms:
        return None
    index = bisect_left(trace.times_ms, float(time_ms))
    if index <= 0:
        return trace.voltages_mV[0]
    if index >= len(trace.times_ms):
        return trace.voltages_mV[-1]
    t0, t1 = trace.times_ms[index - 1], trace.times_ms[index]
    v0, v1 = trace.voltages_mV[index - 1], trace.voltages_mV[index]
    if abs(t1 - t0) <= 1e-12:
        return v1
    fraction = (float(time_ms) - t0) / (t1 - t0)
    return v0 + fraction * (v1 - v0)


class _VoltageCanvas(QWidget):
    time_selected = Signal(float)
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("VoltagePlotCanvas")
        self.setAccessibleName("Interactive voltage plot")
        self.setMinimumSize(430, 430)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.traces: tuple[VoltageTrace, ...] = ()
        self.active_keys: set[str] = set()
        self.visible_keys: set[str] = set()
        self._visible_trace_cache: tuple[VoltageTrace, ...] = ()
        self._trace_style_revision = 0
        self._static_layer: QImage | None = None
        self._static_layer_key: tuple[object, ...] | None = None
        self.mode = MODE_2D
        self.cursor_time_ms: float | None = None
        self.hover_time_ms: float | None = None
        self._time_bounds = (0.0, 1.0)
        self._time_view = (0.0, 1.0)
        self._voltage_bounds = (-80.0, 40.0)
        self._press_position = QPointF()
        self._last_position = QPointF()
        self._dragged = False
        self.yaw_degrees = -27.0
        self.pitch_degrees = 15.0
        self.zoom = 1.0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual name
        return QSize(780, 540)

    def set_traces(self, traces: tuple[VoltageTrace, ...]) -> None:
        self.traces = tuple(traces)
        self.active_keys = {trace.key for trace in self.traces}
        self.visible_keys = {trace.key for trace in self.traces}
        self._visible_trace_cache = self.traces
        self._trace_style_revision += 1
        times = [value for trace in traces for value in trace.times_ms]
        if times:
            self._time_bounds = (min(times), max(times))
        else:
            self._time_bounds = (0.0, 1.0)
        self._refit_voltage_bounds()
        self.reset_view(emit_status=False)

    def set_mode(self, mode: str) -> None:
        if mode not in {MODE_2D, MODE_3D_STACK}:
            raise ValueError(f"Unknown voltage plot mode: {mode}")
        self.mode = mode
        self.hover_time_ms = None
        self.reset_view(emit_status=False)
        self._refresh_accessibility()
        self.update()

    def set_active_keys(self, keys: set[str]) -> None:
        self.active_keys = set(keys)
        self._trace_style_revision += 1
        self._refresh_accessibility()
        self.update()

    def set_visible_keys(self, keys: set[str]) -> None:
        """Restrict rendering to the traces matched by the legend filter."""

        self.visible_keys = set(keys)
        self._visible_trace_cache = tuple(
            trace for trace in self.traces if trace.key in self.visible_keys
        )
        self._trace_style_revision += 1
        self._refit_voltage_bounds()
        self.hover_time_ms = None
        self._refresh_accessibility()
        self.update()

    def _visible_traces(self) -> tuple[VoltageTrace, ...]:
        return self._visible_trace_cache

    def _refit_voltage_bounds(self) -> None:
        visible = self._visible_traces()
        voltages = [value for trace in visible for value in trace.voltages_mV]
        if not voltages:
            self._voltage_bounds = (-80.0, 40.0)
            return
        low, high = min(voltages), max(voltages)
        pad = max(2.0, (high - low) * 0.08)
        self._voltage_bounds = (low - pad, high + pad)

    def set_time_cursor(self, time_ms: float | None) -> None:
        self.cursor_time_ms = None if time_ms is None else float(time_ms)
        self.update()

    def reset_view(self, *, emit_status: bool = True) -> None:
        self._time_view = self._time_bounds
        self.yaw_degrees = -27.0
        self.pitch_degrees = 15.0
        self.zoom = 1.0
        self.update()
        if emit_status:
            self.status_message.emit("Voltage plot view restored")

    def _refresh_accessibility(self) -> None:
        mode = "2-D traces" if self.mode == MODE_2D else "3-D trace stack"
        visible_count = len(self._visible_traces())
        active_visible_count = sum(
            trace.key in self.active_keys for trace in self._visible_traces()
        )
        self.setAccessibleDescription(
            f"{mode}. {visible_count} of {len(self.traces)} traces plotted; "
            f"{active_visible_count} plotted traces emphasized. "
            "Inactive traces remain visible with reduced opacity."
        )

    def _theme(self) -> str:
        application = QApplication.instance()
        return normalize_theme(
            application.property("digiflyTheme")
            if application is not None
            else DARK_THEME
        )

    def _plot_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(68.0, 28.0, -24.0, -52.0)

    @staticmethod
    def _map(value: float, low: float, high: float, start: float, end: float) -> float:
        if abs(high - low) <= 1e-12:
            return (start + end) / 2.0
        return start + (value - low) / (high - low) * (end - start)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        theme = self._theme()
        background = QColor(theme_color(theme, "stimulus_surface"))
        border = QColor(theme_color(theme, "stimulus_border"))
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0), 9.0, 9.0)
        if not self.traces:
            painter.setPen(QColor(theme_color(theme, "muted")))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No long-form voltage traces are available for this result.",
            )
            painter.end()
            return
        if not self.visible_keys:
            painter.setPen(QColor(theme_color(theme, "muted")))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No voltage traces match the current filter.",
            )
            painter.end()
            return
        layer_key = (
            self.mode,
            theme,
            self.width(),
            self.height(),
            round(self.devicePixelRatioF(), 3),
            self._trace_style_revision,
            self._time_view,
            self._voltage_bounds,
            round(self.yaw_degrees, 3),
            round(self.pitch_degrees, 3),
            round(self.zoom, 4),
        )
        if self._static_layer is None or self._static_layer_key != layer_key:
            self._static_layer = self._render_static_layer(theme)
            self._static_layer_key = layer_key
        painter.drawImage(QPointF(0.0, 0.0), self._static_layer)
        if self.mode == MODE_3D_STACK:
            self._paint_3d_overlay(painter, theme)
        else:
            self._paint_2d_overlay(painter, theme)
        painter.end()

    def _render_static_layer(self, theme: str) -> QImage:
        pixel_ratio = max(1.0, self.devicePixelRatioF())
        image = QImage(
            max(1, round(self.width() * pixel_ratio)),
            max(1, round(self.height() * pixel_ratio)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(pixel_ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self.font())
        if self.mode == MODE_3D_STACK:
            self._paint_3d(painter, theme)
        else:
            self._paint_2d(painter, theme)
        painter.end()
        return image

    def _paint_2d(self, painter: QPainter, theme: str) -> None:
        plot = self._plot_rect()
        grid = QColor(theme_color(theme, "stimulus_grid"))
        axis = QColor(theme_color(theme, "stimulus_axis"))
        label = QColor(theme_color(theme, "stimulus_label"))
        t_min, t_max = self._time_view
        v_min, v_max = self._voltage_bounds

        painter.setFont(self.font())
        for index in range(6):
            fraction = index / 5.0
            x = plot.left() + fraction * plot.width()
            painter.setPen(QPen(grid, 1.0))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(label)
            painter.drawText(
                QRectF(x - 42.0, plot.bottom() + 8.0, 84.0, 20.0),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                f"{t_min + fraction * (t_max - t_min):g}",
            )
        for index in range(5):
            fraction = index / 4.0
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QPen(grid, 1.0))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(label)
            painter.drawText(
                QRectF(5.0, y - 10.0, 54.0, 20.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{v_min + fraction * (v_max - v_min):g}",
            )
        painter.setPen(QPen(axis, 1.2))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.drawLine(plot.topLeft(), plot.bottomLeft())
        painter.setPen(label)
        painter.drawText(
            QRectF(plot.left(), self.height() - 29.0, plot.width(), 20.0),
            Qt.AlignmentFlag.AlignHCenter,
            "simulation time (ms)",
        )
        painter.save()
        painter.translate(18.0, plot.center().y())
        painter.rotate(-90.0)
        painter.drawText(
            QRectF(-plot.height() / 2.0, -10.0, plot.height(), 20.0),
            Qt.AlignmentFlag.AlignHCenter,
            "soma membrane voltage (mV)",
        )
        painter.restore()

        visible = self._visible_traces()
        inactive = [trace for trace in visible if trace.key not in self.active_keys]
        active = [trace for trace in visible if trace.key in self.active_keys]
        dense_alpha = max(0.16, min(1.0, 12.0 / max(1, len(visible))))
        dense_width = max(1.0, min(2.6, 16.0 / max(1, len(visible))))
        painter.save()
        painter.setClipRect(plot.adjusted(-1.0, -1.0, 1.0, 1.0))
        for trace in (*inactive, *active):
            color = QColor(trace.color)
            emphasized = trace.key in self.active_keys
            color.setAlphaF(dense_alpha if emphasized else min(0.12, dense_alpha / 2.0))
            painter.setPen(QPen(color, dense_width if emphasized else 0.9))
            path = QPainterPath()
            started = False
            visible_samples = [
                (time_ms, voltage)
                for time_ms, voltage in zip(trace.times_ms, trace.voltages_mV)
                if t_min <= time_ms <= t_max
            ]
            stride = max(1, len(visible_samples) // max(1, round(plot.width() * 5)))
            for time_ms, voltage in visible_samples[::stride]:
                point = QPointF(
                    self._map(time_ms, t_min, t_max, plot.left(), plot.right()),
                    self._map(voltage, v_min, v_max, plot.bottom(), plot.top()),
                )
                if started:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    started = True
            painter.drawPath(path)

        painter.restore()

    def _paint_2d_overlay(self, painter: QPainter, theme: str) -> None:
        plot = self._plot_rect()
        t_min, t_max = self._time_view
        v_min, v_max = self._voltage_bounds
        active = [
            trace
            for trace in self._visible_traces()
            if trace.key in self.active_keys
        ]
        painter.save()
        painter.setClipRect(plot.adjusted(-1.0, -1.0, 1.0, 1.0))
        hover_time = self.hover_time_ms
        if hover_time is not None and t_min <= hover_time <= t_max:
            hover_color = QColor(theme_color(theme, "muted"))
            painter.setPen(QPen(hover_color, 1.0, Qt.PenStyle.DotLine))
            x = self._map(hover_time, t_min, t_max, plot.left(), plot.right())
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        if self.cursor_time_ms is not None and t_min <= self.cursor_time_ms <= t_max:
            cursor_color = QColor("#fff3a1" if theme == DARK_THEME else "#8a6100")
            painter.setPen(QPen(cursor_color, 1.8, Qt.PenStyle.DashLine))
            x = self._map(self.cursor_time_ms, t_min, t_max, plot.left(), plot.right())
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            marker_traces = active if len(active) <= 64 else ()
            for trace in marker_traces:
                voltage = voltage_at(trace, self.cursor_time_ms)
                if voltage is None:
                    continue
                y = self._map(voltage, v_min, v_max, plot.bottom(), plot.top())
                painter.setPen(QPen(QColor(trace.color), 2.0))
                painter.setBrush(QColor(trace.color))
                painter.drawEllipse(QPointF(x, y), 4.0, 4.0)
        painter.restore()

    def _project_3d(
        self,
        x: float,
        y: float,
        z: float,
        plot: QRectF,
    ) -> QPointF:
        yaw = radians(self.yaw_degrees)
        pitch = radians(self.pitch_degrees)
        x1 = x * cos(yaw) + z * sin(yaw)
        z1 = -x * sin(yaw) + z * cos(yaw)
        y1 = y * cos(pitch) - z1 * sin(pitch)
        z2 = y * sin(pitch) + z1 * cos(pitch)
        perspective = 3.2 / max(1.2, 3.2 - z2 * 0.55)
        scale = min(plot.width(), plot.height()) * 0.39 * self.zoom * perspective
        return QPointF(plot.center().x() + x1 * scale, plot.center().y() - y1 * scale)

    def _paint_3d(self, painter: QPainter, theme: str) -> None:
        plot = QRectF(self.rect()).adjusted(54.0, 45.0, -34.0, -45.0)
        axis = QColor(theme_color(theme, "stimulus_axis"))
        label = QColor(theme_color(theme, "stimulus_label"))
        v_min, v_max = self._voltage_bounds
        t_min, t_max = self._time_bounds
        visible = self._visible_traces()
        lane_count = max(1, len(visible))

        def lane_z(index: int) -> float:
            if lane_count <= 1:
                return 0.0
            return -0.82 + 1.64 * index / (lane_count - 1)

        base_z = lane_z(0)
        axes = (
            ((-1.0, -0.72, base_z), (1.0, -0.72, base_z)),
            ((-1.0, -0.72, base_z), (-1.0, 0.72, base_z)),
            ((-1.0, -0.72, base_z), (-1.0, -0.72, lane_z(lane_count - 1))),
        )
        painter.setPen(QPen(axis, 1.3))
        for start, end in axes:
            painter.drawLine(self._project_3d(*start, plot), self._project_3d(*end, plot))
        painter.setPen(label)
        painter.drawText(
            QRectF(18.0, 13.0, self.width() - 36.0, 20.0),
            Qt.AlignmentFlag.AlignLeft,
            "3D trace stack · drag to rotate · wheel to zoom · right-click to reset",
        )
        painter.drawText(self._project_3d(1.06, -0.72, base_z, plot), "time")
        painter.drawText(self._project_3d(-1.0, 0.84, base_z, plot), "voltage")
        if lane_count > 1:
            painter.drawText(
                self._project_3d(-1.0, -0.72, lane_z(lane_count - 1) + 0.12, plot),
                "trace / condition",
            )

        indexed = list(enumerate(visible))
        inactive = [item for item in indexed if item[1].key not in self.active_keys]
        active = [item for item in indexed if item[1].key in self.active_keys]
        for trace_index, trace in (*inactive, *active):
            emphasized = trace.key in self.active_keys
            color = QColor(trace.color)
            color.setAlphaF(1.0 if emphasized else 0.14)
            painter.setPen(QPen(color, 2.8 if emphasized else 1.0))
            path = QPainterPath()
            started = False
            stride = max(1, len(trace.times_ms) // 5_000)
            for time_ms, voltage in zip(
                trace.times_ms[::stride], trace.voltages_mV[::stride]
            ):
                x = self._map(time_ms, t_min, t_max, -1.0, 1.0)
                y = self._map(voltage, v_min, v_max, -0.68, 0.68)
                point = self._project_3d(x, y, lane_z(trace_index), plot)
                if started:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    started = True
            painter.drawPath(path)
            anchor = self._project_3d(-1.03, -0.67, lane_z(trace_index), plot)
            painter.setPen(color)
            painter.drawText(anchor, str(trace_index + 1))

    def _paint_3d_overlay(self, painter: QPainter, theme: str) -> None:
        if self.cursor_time_ms is None:
            return
        plot = QRectF(self.rect()).adjusted(54.0, 45.0, -34.0, -45.0)
        visible = self._visible_traces()
        lane_count = max(1, len(visible))
        t_min, t_max = self._time_bounds
        v_min, v_max = self._voltage_bounds

        def lane_z(index: int) -> float:
            if lane_count <= 1:
                return 0.0
            return -0.82 + 1.64 * index / (lane_count - 1)

        active = [
            (index, trace)
            for index, trace in enumerate(visible)
            if trace.key in self.active_keys
        ]
        marker_traces = active if len(active) <= 64 else ()
        for trace_index, trace in marker_traces:
            voltage = voltage_at(trace, self.cursor_time_ms)
            if voltage is None:
                continue
            x = self._map(self.cursor_time_ms, t_min, t_max, -1.0, 1.0)
            y = self._map(voltage, v_min, v_max, -0.68, 0.68)
            point = self._project_3d(x, y, lane_z(trace_index), plot)
            painter.setPen(QPen(QColor("#ffffff" if theme == DARK_THEME else "#17283e"), 1.5))
            painter.setBrush(QColor(trace.color))
            painter.drawEllipse(point, 5.0, 5.0)

    def _time_for_x(self, x: float) -> float:
        plot = self._plot_rect()
        t_min, t_max = self._time_view
        fraction = min(1.0, max(0.0, (float(x) - plot.left()) / max(1.0, plot.width())))
        return t_min + fraction * (t_max - t_min)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._press_position = event.position()
        self._last_position = event.position()
        self._dragged = False
        if event.button() == Qt.MouseButton.RightButton:
            self.reset_view()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        delta = event.position() - self._last_position
        if event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position() - self._press_position).manhattanLength() > 3.0:
                self._dragged = True
            if self.mode == MODE_3D_STACK:
                self.yaw_degrees += delta.x() * 0.45
                self.pitch_degrees = max(-80.0, min(80.0, self.pitch_degrees + delta.y() * 0.45))
            else:
                t_min, t_max = self._time_view
                span = t_max - t_min
                shift = -delta.x() / max(1.0, self._plot_rect().width()) * span
                full_min, full_max = self._time_bounds
                new_min = max(full_min, min(full_max - span, t_min + shift))
                self._time_view = (new_min, new_min + span)
            self.update()
        elif self.mode == MODE_2D and self._plot_rect().contains(event.position()):
            self.hover_time_ms = self._time_for_x(event.position().x())
            active_visible = [
                trace
                for trace in self._visible_traces()
                if trace.key in self.active_keys
            ]
            values = []
            for trace in active_visible[:HOVER_TRACE_LIMIT]:
                voltage = voltage_at(trace, self.hover_time_ms)
                if voltage is not None:
                    values.append(f"{trace.label}: {voltage:.2f} mV")
            if len(active_visible) > HOVER_TRACE_LIMIT:
                values.append(
                    f"… {len(active_visible) - HOVER_TRACE_LIMIT} more plotted traces; "
                    "search to isolate them"
                )
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{self.hover_time_ms:.3f} ms\n" + "\n".join(values),
                self,
            )
            self.update()
        self._last_position = event.position()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._dragged
            and self.mode == MODE_2D
            and self._plot_rect().contains(event.position())
        ):
            self.time_selected.emit(self._time_for_x(event.position().x()))
        event.accept()

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.hover_time_ms = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if self.mode == MODE_3D_STACK:
            self.zoom = max(0.45, min(4.0, self.zoom * (1.13 ** steps)))
        else:
            full_min, full_max = self._time_bounds
            current_min, current_max = self._time_view
            full_span = max(1e-12, full_max - full_min)
            old_span = current_max - current_min
            new_span = max(full_span / 100.0, min(full_span, old_span * (0.82 ** steps)))
            anchor = self._time_for_x(event.position().x())
            fraction = (anchor - current_min) / max(old_span, 1e-12)
            new_min = anchor - fraction * new_span
            new_min = max(full_min, min(full_max - new_span, new_min))
            self._time_view = (new_min, new_min + new_span)
        self.update()
        event.accept()


class VoltagePlotWidget(QWidget):
    """Interactive Results plot with clickable legend and 2-D/3-D views."""

    time_selected = Signal(float)
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("VoltagePlotWidget")
        self.traces: tuple[VoltageTrace, ...] = ()
        self.legend_buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        legend_header = QHBoxLayout()
        hint = QLabel(
            "Legend · search isolates plotted traces · click to emphasize or fade · "
            "Shift-click to isolate"
        )
        hint.setObjectName("Muted")
        legend_header.addWidget(hint)
        legend_header.addStretch(1)
        self.trace_count_label = QLabel("0 traces")
        self.trace_count_label.setObjectName("Muted")
        legend_header.addWidget(self.trace_count_label)
        self.show_all_button = QPushButton("Show all")
        self.show_all_button.setObjectName("VoltageLegendShowAll")
        self.show_all_button.clicked.connect(self.show_all)
        legend_header.addWidget(self.show_all_button)
        layout.addLayout(legend_header)

        self.legend_filter = QLineEdit()
        self.legend_filter.setObjectName("VoltageLegendFilter")
        self.legend_filter.setAccessibleName("Filter voltage traces")
        self.legend_filter.setPlaceholderText(
            "Search by neuron ID, type, condition, or repetition to isolate the plot"
        )
        self.legend_filter.setClearButtonEnabled(True)
        self.legend_filter.textChanged.connect(self._filter_legend)
        layout.addWidget(self.legend_filter)

        self.legend_widget = QWidget()
        self.legend_layout = QGridLayout()
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setHorizontalSpacing(7)
        self.legend_layout.setVerticalSpacing(5)
        self.legend_widget.setLayout(self.legend_layout)
        self.legend_scroll = QScrollArea()
        self.legend_scroll.setObjectName("VoltageLegendScroll")
        self.legend_scroll.setWidgetResizable(True)
        self.legend_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.legend_scroll.setWidget(self.legend_widget)
        self.legend_scroll.setMaximumHeight(164)
        layout.addWidget(self.legend_scroll)
        self.canvas = _VoltageCanvas()
        self.canvas.time_selected.connect(self.time_selected)
        self.canvas.status_message.connect(self.status_message)
        layout.addWidget(self.canvas, 1)

    @property
    def trace_count(self) -> int:
        return len(self.traces)

    @property
    def sample_count(self) -> int:
        return sum(len(trace.times_ms) for trace in self.traces)

    @property
    def mode(self) -> str:
        return self.canvas.mode

    def set_traces(self, traces: tuple[VoltageTrace, ...]) -> None:
        self.traces = tuple(traces)
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.legend_buttons.clear()
        for index, trace in enumerate(self.traces):
            button = QPushButton(f"●  {trace.label}")
            button.setObjectName("VoltageLegendButton")
            button.setCheckable(True)
            button.setChecked(True)
            button.setAccessibleName(f"Voltage trace {trace.label}")
            button.setToolTip(
                "Click to keep this trace visible but faded; Shift-click to isolate it"
            )
            button.setStyleSheet(f"QPushButton {{ color: {trace.color}; text-align: left; }}")
            button.clicked.connect(
                lambda checked, key=trace.key: self._legend_clicked(key, checked)
            )
            self.legend_layout.addWidget(button, index // 2, index % 2)
            self.legend_buttons[trace.key] = button
        self.canvas.set_traces(self.traces)
        self._refresh_legend_buttons()
        self.show_all_button.setEnabled(bool(self.traces))
        self.legend_filter.setEnabled(bool(self.traces))
        self.legend_filter.clear()
        self.legend_scroll.setVisible(bool(self.traces))
        self._update_trace_count()

    def set_mode(self, mode: str) -> None:
        self.canvas.set_mode(mode)

    def set_time_cursor(self, time_ms: float | None) -> None:
        self.canvas.set_time_cursor(time_ms)

    def show_all(self) -> None:
        for button in self.legend_buttons.values():
            button.setChecked(True)
        self._apply_legend_state()

    def _legend_clicked(self, key: str, checked: bool) -> None:
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            for trace_key, button in self.legend_buttons.items():
                button.setChecked(trace_key == key)
        else:
            self.legend_buttons[key].setChecked(bool(checked))
        self._apply_legend_state()

    def _apply_legend_state(self) -> None:
        active = {
            key for key, button in self.legend_buttons.items() if button.isChecked()
        }
        self.canvas.set_active_keys(active)
        self._refresh_legend_buttons()
        self.status_message.emit(
            f"{len(active)} of {len(self.traces)} voltage traces emphasized"
        )

    def _filter_legend(self, text: str) -> None:
        query = text.strip().casefold()
        by_key = {trace.key: trace for trace in self.traces}
        visible_keys: set[str] = set()
        for key, button in self.legend_buttons.items():
            trace = by_key[key]
            haystack = " ".join(
                (trace.label, trace.condition, trace.neuron_id, str(trace.repetition))
            ).casefold()
            visible = not query or query in haystack
            button.setVisible(visible)
            if visible:
                visible_keys.add(key)
        self.canvas.set_visible_keys(visible_keys)
        self._update_trace_count()

    def _update_trace_count(self) -> None:
        visible = sum(not button.isHidden() for button in self.legend_buttons.values())
        active = len(self.canvas.active_keys)
        if visible == len(self.traces):
            text = f"{len(self.traces)} plotted · {active} emphasized"
        else:
            active_visible = len(self.canvas.active_keys & self.canvas.visible_keys)
            text = (
                f"{visible} of {len(self.traces)} plotted · "
                f"{active_visible} emphasized"
            )
        self.trace_count_label.setText(text)

    def _refresh_legend_buttons(self) -> None:
        by_key = {trace.key: trace for trace in self.traces}
        for key, button in self.legend_buttons.items():
            active = button.isChecked()
            font = button.font()
            font.setBold(active)
            button.setFont(font)
            trace = by_key[key]
            button.setText(f"{'●' if active else '○'}  {trace.label}")
            button.setAccessibleDescription(
                "Emphasized, fully opaque trace. Activate to fade it."
                if active
                else "Inactive, transparent trace. Activate to emphasize it."
            )
        self._update_trace_count()

    def render_high_resolution(
        self,
        *,
        width: int = HIGH_RESOLUTION_WIDTH,
    ) -> QImage:
        return render_widget_high_resolution(self, width=width)
