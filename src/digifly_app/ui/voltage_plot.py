from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping as MappingABC, Sequence
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
    QLayout,
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
LEGEND_PAGE_SIZE = 60
# Dense regional recordings retain every sample, but draw one browsable page.
# Drawing tens of thousands of antialiased curves blocks the GUI event loop.
SIMULTANEOUS_TRACE_LIMIT = 240
VOLTAGE_AXIS_LABEL = "membrane voltage (mV)"


@dataclass(frozen=True, slots=True)
class VoltageTrace:
    key: str
    label: str
    condition: str
    repetition: int
    neuron_id: str
    times_ms: tuple[float, ...]
    voltages_mV: tuple[float, ...]
    color: str
    compartment_id: str = ""
    value_bounds: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class PreparedTracePlot:
    traces: tuple
    keys: frozenset
    by_key: dict
    sample_count: int
    time_bounds: tuple


class _MappedTraceLookup(MappingABC):
    def __init__(self, traces, lookup):
        self.traces, self.lookup = traces, lookup

    def __len__(self): return len(self.lookup)

    def __iter__(self): return iter(self.lookup)

    def __getitem__(self, key):
        return self.traces.trace_for_row(self.lookup[key])


class MappedVoltageTraces(Sequence):
    """Stable lazy trace/selection view over complete primitive metadata."""
    def __init__(self, store=None, metadata=None, *, root=None, rows=None):
        self._root = root
        if root is None:
            self.store, self.metadata = store, metadata
            self.key_to_row = {key:index for index,key in enumerate(metadata.columns[0])}
        else:
            self.store, self.metadata = root.store, root.metadata
            self.key_to_row = root.key_to_row
        self.rows = range(len(self.store)) if rows is None else rows

    @property
    def root(self): return self if self._root is None else self._root

    def __len__(self): return len(self.rows)

    def __eq__(self, other):
        if not isinstance(other,Sequence):
            return NotImplemented
        if len(self) != len(other):
            return False
        if isinstance(other,MappedVoltageTraces):
            a,b = self.store,other.store
            if a is b and self.rows == other.rows and self.metadata.columns == other.metadata.columns:
                return True
            if (self.rows == other.rows and self.metadata.columns == other.metadata.columns
                    and a.payload_sha256 and a.payload_sha256 == b.payload_sha256
                    and a.cache_reference_unchanged() and b.cache_reference_unchanged()
                    and a.keys_in_order == b.keys_in_order and a.offsets == b.offsets
                    and a.clock_ids == b.clock_ids and a.clock_blocks == b.clock_blocks
                    and a.bounds == b.bounds and a.unique == b.unique):
                return True
        return all(left == right for left,right in zip(self,other))

    def __getitem__(self, index):
        if isinstance(index,slice):
            return MappedVoltageTraces(root=self.root,rows=self.rows[index])
        return self.trace_for_row(self.rows[index])

    def trace_for_row(self, row):
        samples = self.store.row(row)
        if samples.bind_prepared_trace_metadata(self.metadata,row):
            return samples
        fields = tuple(column[row] for column in self.metadata.columns)
        key,label,condition,repetition,neuron_id,color,compartment_id = fields
        return VoltageTrace(key,label,condition,repetition,neuron_id,
                            samples.times,samples.values,color,compartment_id,samples.bounds)

    def filtered(self, queries):
        labels,conditions,neurons,repetitions = (self.metadata.columns[i] for i in (1,2,4,3))
        matches = []
        for row in self.rows:
            haystack = ' '.join((labels[row],conditions[row],neurons[row],str(repetitions[row]))).casefold()
            if any(query in haystack for query in queries):
                matches.append(row)
        return MappedVoltageTraces(root=self.root,rows=tuple(matches))

    def selected_keys(self, keys):
        rows = sorted(self.key_to_row[key] for key in keys
                      if key in self.key_to_row and self.key_to_row[key] in self.rows)
        return MappedVoltageTraces(root=self.root,rows=tuple(rows))

    def prepare(self, check):
        keys = {};count = 0;lower = upper = None
        for index,row in enumerate(self.rows):
            if index % 256 == 0: check()
            key = self.metadata.columns[0][row]
            keys[key] = row
            clock = self.store.clocks[self.store.clock_ids[row]]
            count += len(clock)
            if clock:
                lower = clock[0] if lower is None else min(lower,clock[0])
                upper = clock[-1] if upper is None else max(upper,clock[-1])
        check()
        return PreparedTracePlot(self,frozenset(keys),_MappedTraceLookup(self.root,keys),
                                 count,(lower,upper) if lower is not None else (0.,1.))


def prepare_trace_plot(traces, *, check=lambda: None) -> PreparedTracePlot:
    """Prepare full-record indices without constructing or touching Qt objects."""
    if isinstance(traces,MappedVoltageTraces):
        return traces.prepare(check)
    traces=tuple(traces)
    keys=set();by_key={};count=0;lower=upper=None
    for index,trace in enumerate(traces):
        if index%256==0:check()
        keys.add(trace.key);by_key[trace.key]=trace;count+=len(trace.times_ms)
        if trace.times_ms:
            lower=trace.times_ms[0] if lower is None else min(lower,trace.times_ms[0])
            upper=trace.times_ms[-1] if upper is None else max(upper,trace.times_ms[-1])
    check()
    return PreparedTracePlot(traces,frozenset(keys),by_key,count,
                             (lower,upper) if lower is not None else (0.,1.))


def load_voltage_traces(
    path: str | Path,
    *,
    neuron_labels: Mapping[str, str] | None = None,
    table=None,
    check=lambda: None,
) -> tuple[VoltageTrace, ...]:
    """Load long-form Digifly voltage CSV data without adding a NumPy dependency."""

    from digifly_app.workers.result_storage import result_path, open_result_text, SampleSeries, MAX_PLAYBACK_VOLTAGE_SAMPLES
    from digifly_app.core.result_voltage import MappedSamples, MappedTraceMetadata, MappedSeries
    source = result_path(Path(path).expanduser().resolve())
    if table is None and not source.is_file():
        return ()
    grouped: dict[tuple[str, int, str, str], SampleSeries] = table.series if table is not None else {}
    count = 0
    if table is None:
        with open_result_text(source) as handle:
            for row in csv.DictReader(handle):
                try:
                    time_ms = float(row.get("time_ms", ""))
                    voltage_mV = float(row.get("voltage_mV", ""))
                    repetition = int(float(row.get("repetition") or 0))
                except (TypeError, ValueError):
                    continue
                neuron_id = str(row.get("neuron_id") or "").strip()
                condition = str(row.get("condition") or "Recorded run").strip()
                compartment_id = str(row.get("compartment_id") or "").strip()
                if neuron_id and isfinite(time_ms) and isfinite(voltage_mV):
                    count += 1
                    if count > MAX_PLAYBACK_VOLTAGE_SAMPLES:
                        raise ValueError("Voltage traces exceed the local sample budget")
                    key = (condition, repetition, neuron_id, compartment_id)
                    samples = grouped.get(key)
                    if samples is None:
                        samples = grouped[key] = SampleSeries()
                    samples.append(time_ms, voltage_mV)

    repetition_counts: dict[str, set[int]] = {}
    for index,(condition, repetition, _neuron_id, _compartment_id) in enumerate(grouped):
        if index%256==0:check()
        repetition_counts.setdefault(condition, set()).add(repetition)
    labels = {str(key): str(value) for key, value in dict(neuron_labels or {}).items()}
    if isinstance(grouped,MappedSeries):
        metadata = MappedTraceMetadata()
        for index,(condition,repetition,neuron_id,compartment_id) in enumerate(grouped):
            if index % 256 == 0: check()
            neuron_label = labels.get(neuron_id,f'body {neuron_id}')
            suffix = f' · rep {repetition}' if len(repetition_counts.get(condition,()))>1 else ''
            compartment = f' · SWC {compartment_id}' if compartment_id else ''
            key = f'{condition}\x1f{repetition}\x1f{neuron_id}' + (f'\x1f{compartment_id}' if compartment_id else '')
            metadata.append((key,f'{condition} · {neuron_label}{compartment}{suffix}',
                             condition,repetition,neuron_id,TRACE_COLORS[index%len(TRACE_COLORS)],compartment_id))
        metadata.freeze();check()
        return MappedVoltageTraces(grouped,metadata)
    traces: list[VoltageTrace] = []
    metadata_owner = MappedTraceMetadata()
    for index, ((condition, repetition, neuron_id, compartment_id), samples) in enumerate(grouped.items()):
        if index%256==0:check()
        if isinstance(samples, SampleSeries):
            samples.finish()
        neuron_label = labels.get(neuron_id, f"body {neuron_id}")
        repetition_suffix = (
            f" · rep {repetition}"
            if len(repetition_counts.get(condition, ())) > 1
            else ""
        )
        compartment_suffix = f" · SWC {compartment_id}" if compartment_id else ""
        key = f"{condition}\x1f{repetition}\x1f{neuron_id}" + (f"\x1f{compartment_id}" if compartment_id else "")
        label = f"{condition} · {neuron_label}{compartment_suffix}{repetition_suffix}"
        color = TRACE_COLORS[index % len(TRACE_COLORS)]
        if isinstance(samples, MappedSamples) and samples.bind_trace_metadata(
                metadata_owner, key, label, condition, repetition, neuron_id, color, compartment_id):
            traces.append(samples)
            continue
        traces.append(
            VoltageTrace(
                key=key,
                label=label,
                condition=condition,
                repetition=repetition,
                neuron_id=neuron_id,
                times_ms=samples.times,
                voltages_mV=samples.values,
                color=color,
                compartment_id=compartment_id,
                value_bounds=getattr(samples, 'bounds', None),
            )
        )
    check()
    metadata_owner.freeze()
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
        self.quantity_label = VOLTAGE_AXIS_LABEL
        self.quantity_unit = "mV"
        self._press_position = QPointF()
        self._last_position = QPointF()
        self._dragged = False
        self.yaw_degrees = -27.0
        self.pitch_degrees = 15.0
        self.zoom = 1.0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual name
        return QSize(780, 540)

    def set_traces(self, traces: tuple[VoltageTrace, ...], *, prepared=None) -> None:
        traces=traces if isinstance(traces,MappedVoltageTraces) else tuple(traces)
        prepared=prepared if prepared is not None else prepare_trace_plot(traces)
        if prepared.traces is not traces:
            raise ValueError('Prepared plot belongs to a different recording')
        self.traces = traces
        self.active_keys = set(prepared.keys)
        # The first paint is paged too; never scan/draw the complete recording
        # before the widget installs its first legend page.
        self._visible_trace_cache = (self.traces[:LEGEND_PAGE_SIZE]
                                     if len(self.traces) > SIMULTANEOUS_TRACE_LIMIT else self.traces)
        self.visible_keys = {trace.key for trace in self._visible_trace_cache}
        self._trace_style_revision += 1
        self._time_bounds = prepared.time_bounds
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

    def set_visible_keys(self, keys: set[str], *, traces=None) -> None:
        """Restrict rendering to the traces matched by the legend filter."""

        self.visible_keys = set(keys)
        if traces is not None:
            self._visible_trace_cache = tuple(traces)
        elif isinstance(self.traces,MappedVoltageTraces):
            self._visible_trace_cache = tuple(self.traces.selected_keys(self.visible_keys))
        else:
            self._visible_trace_cache = tuple(trace for trace in self.traces if trace.key in self.visible_keys)
        self._trace_style_revision += 1
        self._refit_voltage_bounds()
        self.hover_time_ms = None
        self._refresh_accessibility()
        self.update()

    def _visible_traces(self) -> tuple[VoltageTrace, ...]:
        return self._visible_trace_cache

    def _refit_voltage_bounds(self) -> None:
        visible = self._visible_traces()
        nonempty = [trace for trace in visible if trace.voltages_mV]
        if not nonempty:
            self._voltage_bounds = (-80.0, 40.0)
            return
        low = min(trace.value_bounds[0] if trace.value_bounds is not None
                  else min(trace.voltages_mV) for trace in nonempty)
        high = max(trace.value_bounds[1] if trace.value_bounds is not None
                   else max(trace.voltages_mV) for trace in nonempty)
        pad = max(2.0 if self.quantity_unit == "mV" else max(abs(low), abs(high), 1e-9)*.02,
                  (high - low) * 0.08)
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
        return QRectF(self.rect()).adjusted(68.0 if self.quantity_unit == "mV" else 112.0, 28.0, -24.0, -52.0)

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
                f"No {'voltage' if self.quantity_unit == 'mV' else 'current'} traces are available for this result.",
            )
            painter.end()
            return
        if not self.visible_keys:
            painter.setPen(QColor(theme_color(theme, "muted")))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"No {'voltage' if self.quantity_unit == 'mV' else 'current'} traces match the current filter.",
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
                QRectF(5.0 if self.quantity_unit == "mV" else 33.0, y - 10.0,
                       54.0 if self.quantity_unit == "mV" else 68.0, 20.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                (f"{v_min + fraction * (v_max - v_min):g}" if self.quantity_unit == "mV"
                 else f"{v_min + fraction * (v_max - v_min):.2g}"),
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
            self.quantity_label,
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
        painter.drawText(self._project_3d(-1.0, 0.84, base_z, plot), self.quantity_label)
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
                    values.append(f"{trace.label}: {voltage:.6g} {self.quantity_unit}")
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
        self._traces_by_key = {}
        self._sample_count = 0
        self.legend_buttons: dict[str, QPushButton] = {}
        self._legend_matches: tuple[VoltageTrace, ...] = ()
        self._legend_page = 0
        self._legend_generation = 0
        self._active_trace_keys: set[str] = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        legend_header = QHBoxLayout()
        hint = QLabel(
            "Legend · search isolates plotted traces · click to emphasize or fade · "
            "Shift-click to isolate · large recordings plot one page at a time"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(hint)
        self.trace_count_label = QLabel("0 traces")
        self.trace_count_label.setObjectName("Muted")
        self.trace_count_label.setWordWrap(True)
        self.trace_count_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        legend_header.addWidget(self.trace_count_label, 1)
        self.show_all_button = QPushButton("Emphasize all")
        self.show_all_button.setObjectName("VoltageLegendShowAll")
        self.show_all_button.clicked.connect(self.show_all)
        legend_header.addWidget(self.show_all_button)
        layout.addLayout(legend_header)

        self.legend_filter = QLineEdit()
        self.legend_filter.setObjectName("VoltageLegendFilter")
        self.legend_filter.setAccessibleName("Filter voltage traces")
        self.legend_filter.setPlaceholderText(
            "Search neuron, SWC edge, or condition; separate alternatives with commas"
        )
        self.legend_filter.setClearButtonEnabled(True)
        self.legend_filter.textChanged.connect(self._filter_legend)
        layout.addWidget(self.legend_filter)

        self.legend_widget = QWidget()
        self.legend_layout = QGridLayout()
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setHorizontalSpacing(7)
        self.legend_layout.setVerticalSpacing(5)
        # Keep page rows at their real button height inside the scroll area.
        # Otherwise a replacement page can collapse every row to one pixel.
        self.legend_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
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
        self.legend_pager = QWidget()
        pager_layout = QHBoxLayout(self.legend_pager)
        pager_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_previous_button = QPushButton("Previous")
        self.legend_previous_button.setObjectName("VoltageLegendPrevious")
        self.legend_previous_button.setAccessibleName("Previous trace legend page")
        self.legend_previous_button.clicked.connect(lambda: self._change_legend_page(-1))
        self.legend_page_label = QLabel()
        self.legend_page_label.setObjectName("VoltageLegendPage")
        self.legend_next_button = QPushButton("Next")
        self.legend_next_button.setObjectName("VoltageLegendNext")
        self.legend_next_button.setAccessibleName("Next trace legend page")
        self.legend_next_button.clicked.connect(lambda: self._change_legend_page(1))
        pager_layout.addWidget(self.legend_previous_button)
        pager_layout.addWidget(self.legend_page_label, 1)
        pager_layout.addWidget(self.legend_next_button)
        layout.addWidget(self.legend_pager)
        self.canvas = _VoltageCanvas()
        self.canvas.time_selected.connect(self.time_selected)
        self.canvas.status_message.connect(self.status_message)
        layout.addWidget(self.canvas, 1)

    @property
    def trace_count(self) -> int:
        return len(self.traces)

    @property
    def sample_count(self) -> int:
        return getattr(self, '_sample_count', 0)

    @property
    def mode(self) -> str:
        return self.canvas.mode

    def set_traces(self, traces: tuple[VoltageTrace, ...], *, prepared=None) -> None:
        traces=traces if isinstance(traces,MappedVoltageTraces) else tuple(traces)
        prepared=prepared if prepared is not None else prepare_trace_plot(traces)
        if prepared.traces is not traces:
            raise ValueError('Prepared plot belongs to a different recording')
        self.traces = traces
        self._prepared = prepared
        self._sample_count = prepared.sample_count
        self._traces_by_key = prepared.by_key
        self._active_trace_keys = set(prepared.keys)
        self.canvas.set_traces(self.traces,prepared=prepared)
        self.show_all_button.setEnabled(bool(self.traces))
        self.legend_filter.setEnabled(bool(self.traces))
        self.legend_filter.blockSignals(True)
        self.legend_filter.clear()
        self.legend_filter.blockSignals(False)
        self.legend_scroll.setVisible(bool(self.traces))
        self._filter_legend("")

    def set_quantity(self, label: str, unit: str) -> None:
        """Reuse the paged renderer without mixing current and voltage axes."""
        self.canvas.quantity_label = label
        self.canvas.quantity_unit = unit
        self.canvas._static_layer_key = None
        self.canvas.setAccessibleName(f"Interactive {label} plot")
        self.legend_filter.setAccessibleName(f"Filter {label} traces")
        self.canvas.update()

    def _render_legend_page(self) -> None:
        self._legend_generation += 1
        generation = self._legend_generation
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.legend_buttons.clear()
        start = self._legend_page * LEGEND_PAGE_SIZE
        page_traces = self._legend_matches[start:start + LEGEND_PAGE_SIZE]
        plotted = (page_traces if len(self._legend_matches) > SIMULTANEOUS_TRACE_LIMIT
                   else self._legend_matches)
        self.canvas.set_visible_keys({trace.key for trace in plotted},traces=plotted)
        for trace in page_traces:
            button = QPushButton(f"●  {trace.label}")
            button.setObjectName("VoltageLegendButton")
            button.setCheckable(True)
            # Full labels remain accessible and in the tooltip; a long body
            # name must not impose a wider-than-window grid column minimum.
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setMinimumWidth(0)
            button.setChecked(trace.key in self._active_trace_keys)
            button.setAccessibleName(f"{'Voltage' if self.canvas.quantity_unit == 'mV' else 'Current'} trace {trace.label}")
            button.setToolTip(
                f"{trace.label}\nClick to keep this trace visible but faded; Shift-click to isolate it"
            )
            button.setStyleSheet(f"QPushButton {{ color: {trace.color}; text-align: left; }}")
            button.toggled.connect(
                lambda checked, key=trace.key, page=generation: self._legend_clicked(key, checked, page)
            )
            self.legend_buttons[trace.key] = button
        self._relayout_legend()
        self._refresh_legend_buttons()
        total = len(self._legend_matches)
        self.legend_pager.setVisible(total > LEGEND_PAGE_SIZE)
        self.legend_previous_button.setEnabled(self._legend_page > 0)
        self.legend_next_button.setEnabled(start + LEGEND_PAGE_SIZE < total)
        scope = "Plotted traces" if total > SIMULTANEOUS_TRACE_LIMIT else "Legend entries"
        self.legend_page_label.setText(f"{scope} {start + 1 if total else 0}–{min(start + LEGEND_PAGE_SIZE, total)} of {total:,}")

    def _change_legend_page(self, delta: int) -> None:
        last = max(0, (len(self._legend_matches) - 1) // LEGEND_PAGE_SIZE)
        self._legend_page = max(0, min(last, self._legend_page + delta))
        self._render_legend_page()

    def _relayout_legend(self) -> None:
        # A narrow split panel needs one readable column; a full-width plot
        # can use two. This changes layout only, never trace membership/state.
        if not hasattr(self, "legend_layout"):
            return
        columns = 1 if self.width() < 680 else 2
        while self.legend_layout.count():
            self.legend_layout.takeAt(0)
        for index, button in enumerate(self.legend_buttons.values()):
            self.legend_layout.addWidget(button, index // columns, index % columns)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual name
        super().resizeEvent(event)
        self._relayout_legend()

    def set_mode(self, mode: str) -> None:
        self.canvas.set_mode(mode)

    def set_time_cursor(self, time_ms: float | None) -> None:
        self.canvas.set_time_cursor(time_ms)

    def show_all(self) -> None:
        prepared = getattr(self,'_prepared',None)
        self._active_trace_keys = set(prepared.keys) if prepared is not None else set()
        self._apply_legend_state()

    def _legend_clicked(self, key: str, checked: bool, generation: int | None = None) -> None:
        if (generation is not None and generation != self._legend_generation) or key not in self.legend_buttons:
            return
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._active_trace_keys = {key}
        elif checked:
            self._active_trace_keys.add(key)
        else:
            self._active_trace_keys.discard(key)
        self._apply_legend_state()

    def _apply_legend_state(self) -> None:
        active = self._active_trace_keys
        self.canvas.set_active_keys(active)
        self._refresh_legend_buttons()
        self.status_message.emit(
            f"{len(active)} of {len(self.traces)} {'voltage' if self.canvas.quantity_unit == 'mV' else 'current'} traces emphasized"
        )

    def _filter_legend(self, text: str) -> None:
        queries = tuple(part.strip().casefold() for part in text.split(",") if part.strip())
        if not queries:
            self._legend_matches = self.traces
            self._legend_page = 0
            self._render_legend_page()
            return
        if isinstance(self.traces,MappedVoltageTraces):
            self._legend_matches = self.traces.filtered(queries)
            self._legend_page = 0
            self._render_legend_page()
            return
        matches = []
        for trace in self.traces:
            haystack = " ".join(
                (trace.label, trace.condition, trace.neuron_id, str(trace.repetition))
            ).casefold()
            visible = not queries or any(query in haystack for query in queries)
            if visible:
                matches.append(trace)
        self._legend_matches = tuple(matches)
        self._legend_page = 0
        self._render_legend_page()

    def _update_trace_count(self) -> None:
        visible = len(self.canvas.visible_keys)
        active = len(self.canvas.active_keys)
        if visible == len(self.traces):
            text = f"{len(self.traces)} plotted · {active} emphasized"
        else:
            active_visible = len(self.canvas.active_keys & self.canvas.visible_keys)
            text = (
                f"{visible} of {len(self.traces)} plotted · "
                f"{active_visible} emphasized · all {len(self.traces):,} saved traces available"
            )
        self.trace_count_label.setText(text)

    def _refresh_legend_buttons(self) -> None:
        by_key = self._traces_by_key
        for key, button in self.legend_buttons.items():
            active = key in self._active_trace_keys
            previous = button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(previous)
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
