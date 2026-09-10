from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPaintEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .style import DARK_THEME, normalize_theme, theme_color
from .snapshot import HIGH_RESOLUTION_WIDTH, render_widget_high_resolution


def pulse_intervals(
    *,
    duration_ms: float,
    delay_ms: float,
    pulse_width_ms: float,
    frequency_hz: float,
    pulse_count: int,
    waveform: str,
    limit: int = 2_000,
) -> tuple[tuple[float, float], ...]:
    """Return the visible stimulus intervals inside the simulation time window."""

    duration = max(0.0, float(duration_ms))
    delay = max(0.0, float(delay_ms))
    width = max(0.0, float(pulse_width_ms))
    count = max(0, int(pulse_count))
    if duration <= 0.0 or delay >= duration or width <= 0.0 or count == 0:
        return ()

    if waveform != "pulse_train":
        count = 1
    period_ms = 1000.0 / max(float(frequency_hz), 1e-12)
    intervals: list[tuple[float, float]] = []
    for index in range(min(count, max(1, int(limit)))):
        start = delay + index * period_ms
        if start >= duration:
            break
        intervals.append((start, min(duration, start + width)))
    return tuple(intervals)


class StimulusPreview(QWidget):
    """Live, simulator-independent rendering of the configured injected current."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StimulusPreview")
        self.setAccessibleName("Injected current stimulus preview")
        self.setMinimumHeight(300)
        self._protocol = {
            "duration_ms": 110.0,
            "random_seed": 1,
            "amplitude_nA": 0.9,
            "delay_ms": 5.0,
            "pulse_width_ms": 0.4,
            "frequency_hz": 100.0,
            "pulse_count": 10,
            "waveform": "pulse_train",
        }
        self._refresh_accessibility()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual name
        return QSize(620, 330)

    def set_protocol(
        self,
        *,
        duration_ms: float,
        random_seed: int,
        amplitude_nA: float,
        delay_ms: float,
        pulse_width_ms: float,
        frequency_hz: float,
        pulse_count: int,
        waveform: str,
    ) -> None:
        self._protocol = {
            "duration_ms": max(0.0, float(duration_ms)),
            "random_seed": max(0, int(random_seed)),
            "amplitude_nA": float(amplitude_nA),
            "delay_ms": max(0.0, float(delay_ms)),
            "pulse_width_ms": max(0.0, float(pulse_width_ms)),
            "frequency_hz": max(0.0, float(frequency_hz)),
            "pulse_count": max(0, int(pulse_count)),
            "waveform": str(waveform),
        }
        self._refresh_accessibility()
        self.update()

    def protocol(self) -> dict[str, float | int | str]:
        return dict(self._protocol)

    def intervals(self) -> tuple[tuple[float, float], ...]:
        return pulse_intervals(
            duration_ms=float(self._protocol["duration_ms"]),
            delay_ms=float(self._protocol["delay_ms"]),
            pulse_width_ms=float(self._protocol["pulse_width_ms"]),
            frequency_hz=float(self._protocol["frequency_hz"]),
            pulse_count=int(self._protocol["pulse_count"]),
            waveform=str(self._protocol["waveform"]),
        )

    def render_high_resolution(
        self,
        *,
        width: int = HIGH_RESOLUTION_WIDTH,
    ) -> QImage:
        """Render the live stimulus diagram as a 4K-width PNG-ready image."""

        return render_widget_high_resolution(self, width=width)

    def summary_text(self) -> str:
        protocol = self._protocol
        waveform = str(protocol["waveform"])
        amplitude = float(protocol["amplitude_nA"])
        delay = float(protocol["delay_ms"])
        width = float(protocol["pulse_width_ms"])
        duration = float(protocol["duration_ms"])
        if waveform == "pulse_train":
            stimulus = (
                f"{int(protocol['pulse_count'])} pulses @ "
                f"{float(protocol['frequency_hz']):g} Hz"
            )
        elif waveform == "ramp":
            stimulus = "single ramp"
        else:
            stimulus = "single square step"
        return (
            f"{amplitude:g} nA · {stimulus} · {width:g} ms wide · "
            f"starts at {delay:g} ms · simulation ends at {duration:g} ms"
        )

    def _refresh_accessibility(self) -> None:
        self.setAccessibleDescription(
            f"Seed: {int(self._protocol['random_seed'])}. {self.summary_text()}"
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt virtual name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        application = QApplication.instance()
        theme = normalize_theme(
            application.property("digiflyTheme") if application is not None else DARK_THEME
        )

        def color(key: str) -> QColor:
            return QColor(theme_color(theme, key))

        bounds = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        painter.setPen(QPen(color("stimulus_border"), 1.0))
        painter.setBrush(color("stimulus_surface"))
        painter.drawRoundedRect(bounds, 10.0, 10.0)

        painter.setPen(color("stimulus_title"))
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPointSizeF(max(11.0, title_font.pointSizeF()))
        painter.setFont(title_font)
        painter.drawText(
            QRectF(18.0, 13.0, max(0.0, bounds.width() - 36.0), 24.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Injected current over simulation time",
        )

        plot = QRectF(
            62.0,
            54.0,
            max(40.0, bounds.width() - 88.0),
            max(70.0, bounds.height() - 112.0),
        )
        painter.setFont(self.font())

        grid_pen = QPen(color("stimulus_grid"), 1.0)
        axis_pen = QPen(color("stimulus_axis"), 1.0)
        for division in range(5):
            fraction = division / 4.0
            x = plot.left() + plot.width() * fraction
            painter.setPen(grid_pen)
            painter.drawLine(x, plot.top(), x, plot.bottom())
            painter.setPen(color("stimulus_label"))
            time_ms = float(self._protocol["duration_ms"]) * fraction
            painter.drawText(
                QRectF(x - 38.0, plot.bottom() + 8.0, 76.0, 18.0),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                f"{time_ms:g}",
            )
        for division in range(3):
            fraction = division / 2.0
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(grid_pen)
            painter.drawLine(plot.left(), y, plot.right(), y)

        painter.setPen(axis_pen)
        painter.drawLine(plot.left(), plot.top(), plot.left(), plot.bottom())
        painter.drawLine(plot.left(), plot.bottom(), plot.right(), plot.bottom())

        amplitude = abs(float(self._protocol["amplitude_nA"]))
        scale_max = max(1.0, amplitude * 1.2)
        signal_y = plot.bottom() - (amplitude / scale_max) * plot.height()
        painter.setPen(color("stimulus_label"))
        painter.drawText(
            QRectF(5.0, plot.top() - 8.0, 50.0, 18.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{scale_max:g}",
        )
        painter.drawText(
            QRectF(5.0, plot.bottom() - 9.0, 50.0, 18.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "0",
        )
        painter.drawText(
            QRectF(plot.left(), bounds.bottom() - 25.0, plot.width(), 18.0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "simulation time (ms)",
        )
        painter.save()
        painter.translate(16.0, plot.center().y())
        painter.rotate(-90.0)
        painter.drawText(
            QRectF(-plot.height() / 2.0, -9.0, plot.height(), 18.0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "current (nA)",
        )
        painter.restore()

        duration = max(float(self._protocol["duration_ms"]), 1e-12)

        def x_for(time_ms: float) -> float:
            return plot.left() + plot.width() * min(1.0, max(0.0, time_ms / duration))

        intervals = self.intervals()
        if amplitude > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            signal_fill = color("stimulus_signal_fill")
            signal_fill.setAlpha(45)
            painter.setBrush(signal_fill)
            for start, end in intervals:
                left = x_for(start)
                width = max(2.0, x_for(end) - left)
                if str(self._protocol["waveform"]) == "ramp":
                    ramp = QPainterPath()
                    ramp.moveTo(left, plot.bottom())
                    ramp.lineTo(left + width, signal_y)
                    ramp.lineTo(left + width, plot.bottom())
                    ramp.closeSubpath()
                    painter.drawPath(ramp)
                else:
                    painter.drawRect(QRectF(left, signal_y, width, plot.bottom() - signal_y))

        signal = QPainterPath()
        signal.moveTo(plot.left(), plot.bottom())
        for start, end in intervals:
            start_x = x_for(start)
            end_x = x_for(end)
            signal.lineTo(start_x, plot.bottom())
            if str(self._protocol["waveform"]) == "ramp":
                signal.lineTo(end_x, signal_y)
                signal.lineTo(end_x, plot.bottom())
            else:
                signal.lineTo(start_x, signal_y)
                signal.lineTo(end_x, signal_y)
                signal.lineTo(end_x, plot.bottom())
        signal.lineTo(plot.right(), plot.bottom())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color("stimulus_signal"), 2.2))
        painter.drawPath(signal)

        visible_count = len(intervals)
        if visible_count == 0:
            status = "Stimulus begins outside this simulation window"
        else:
            status = f"{visible_count} visible pulse{'s' if visible_count != 1 else ''}"
        painter.setPen(color("stimulus_status"))
        painter.drawText(
            QRectF(plot.left(), 31.0, plot.width(), 18.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"Seed: {int(self._protocol['random_seed'])}",
        )
        painter.drawText(
            QRectF(plot.left(), 31.0, plot.width(), 18.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            status,
        )
        painter.end()
