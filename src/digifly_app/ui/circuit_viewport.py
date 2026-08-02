from __future__ import annotations

from array import array
from dataclasses import dataclass
from math import ceil, radians, sqrt, tan
from typing import Iterable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMatrix4x4, QVector3D, QVector4D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QRubberBand

from digifly_app.core.morphology import Morphology, SwcSegment


GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71
GL_FLOAT = 0x1406
GL_LINES = 0x0001

SELECTED_NEURON_COLOR = "#f6c65b"
# Reserved exclusively for selected SWC compartments in the morphology view.
SELECTED_COMPARTMENT_COLOR = "#ff00ff"
INTERACTION_PREVIEW_THRESHOLD = 45_000
INTERACTION_PREVIEW_SEGMENT_BUDGET = 30_000
INTERACTION_SETTLE_MS = 140
INTERACTION_PREVIEW_MIN_DISTANCE_RATIO = 0.5
SPATIAL_LEAF_SEGMENTS = 64
EXACT_CULLING_ZOOM_RATIO = 1.0

# Reference view used by "Ablation Baseline and Na Response Match.ipynb". Its
# morphology panel loads this saved VIP GLIA camera and refuses a fallback
# orientation. Only the orientation basis is reused here; framing still adapts
# to every newly loaded morphology cell set.
REFERENCE_CAMERA_POSITION = (-10.127140056996213, 138.41769236803785, 62.41693296997541)
REFERENCE_CAMERA_FOCAL_POINT = (25.071576505154095, 29.67979647636857, 55.241755203047354)
REFERENCE_CAMERA_VIEW_UP = (0.04896257747685693, -0.04997499392139454, 0.9975495807173593)
DEFAULT_YAW_DEGREES = 0.0
DEFAULT_PITCH_DEGREES = 0.0


def _rgba(color: str) -> QVector4D:
    value = QColor(color)
    return QVector4D(value.redF(), value.greenF(), value.blueF(), value.alphaF())


def _point_segment_distance_and_t(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> tuple[float, float]:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    qx, qy = ax + t * dx, ay + t * dy
    return sqrt((px - qx) ** 2 + (py - qy) ** 2), t


def _line_intersects_rect(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    rect: QRectF,
) -> bool:
    """Return whether a 2-D line segment intersects a normalized rectangle."""

    rect = rect.normalized()
    dx, dy = bx - ax, by - ay
    lower, upper = 0.0, 1.0
    for p, q in (
        (-dx, ax - rect.left()),
        (dx, rect.right() - ax),
        (-dy, ay - rect.top()),
        (dy, rect.bottom() - ay),
    ):
        if abs(p) <= 1e-12:
            if q < 0.0:
                return False
            continue
        ratio = q / p
        if p < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _preview_lines(morphology: Morphology, target_segments: int) -> tuple[tuple[float, ...], ...]:
    """Create a connected topology-preserving interaction preview.

    Long degree-two SWC chains are collapsed into longer lines. Branch points
    and terminals remain present, while the original morphology is untouched
    for settled rendering, picking, editing, and export.
    """

    segments = morphology.segments
    if len(segments) <= max(1, target_segments):
        return tuple((*segment.parent, *segment.child) for segment in segments)

    step = max(2, ceil(len(segments) / max(1, target_segments)))
    children: dict[int, list[SwcSegment]] = {}
    child_ids: set[int] = set()
    for segment in segments:
        children.setdefault(segment.parent_id, []).append(segment)
        child_ids.add(segment.child_id)
    roots = {segment.parent_id for segment in segments if segment.parent_id not in child_ids}
    anchors = roots | {node_id for node_id, outgoing in children.items() if len(outgoing) != 1}
    visited: set[int] = set()
    preview: list[tuple[float, ...]] = []

    def add_chain(first: SwcSegment) -> None:
        chain = [first]
        visited.add(first.child_id)
        cursor = first
        while cursor.child_id not in anchors:
            outgoing = children.get(cursor.child_id, ())
            if len(outgoing) != 1 or outgoing[0].child_id in visited:
                break
            cursor = outgoing[0]
            chain.append(cursor)
            visited.add(cursor.child_id)
        for offset in range(0, len(chain), step):
            chunk = chain[offset : offset + step]
            preview.append((*chunk[0].parent, *chunk[-1].child))

    for anchor in anchors:
        for segment in children.get(anchor, ()):
            if segment.child_id not in visited:
                add_chain(segment)
    for segment in segments:
        if segment.child_id not in visited:
            add_chain(segment)
    return tuple(preview)


@dataclass(frozen=True)
class _SpatialNode:
    bounds: tuple[float, float, float, float, float, float]
    start: int
    count: int
    segments: tuple[SwcSegment, ...] = ()
    children: tuple["_SpatialNode", ...] = ()


def _segment_bounds(
    segments: tuple[SwcSegment, ...],
) -> tuple[float, float, float, float, float, float]:
    first = segments[0]
    xmin, xmax = sorted((first.parent[0], first.child[0]))
    ymin, ymax = sorted((first.parent[1], first.child[1]))
    zmin, zmax = sorted((first.parent[2], first.child[2]))
    for segment in segments[1:]:
        xmin = min(xmin, segment.parent[0], segment.child[0])
        xmax = max(xmax, segment.parent[0], segment.child[0])
        ymin = min(ymin, segment.parent[1], segment.child[1])
        ymax = max(ymax, segment.parent[1], segment.child[1])
        zmin = min(zmin, segment.parent[2], segment.child[2])
        zmax = max(zmax, segment.parent[2], segment.child[2])
    return xmin, xmax, ymin, ymax, zmin, zmax


def _build_spatial_tree(
    segments: tuple[SwcSegment, ...],
    vertices: array,
    vertex_index: int,
) -> tuple[_SpatialNode, int]:
    """Append spatially grouped exact lines and return their range hierarchy."""

    bounds = _segment_bounds(segments)
    if len(segments) <= SPATIAL_LEAF_SEGMENTS:
        start = vertex_index
        for segment in segments:
            vertices.extend(segment.parent)
            vertices.extend(segment.child)
            vertex_index += 2
        return _SpatialNode(bounds, start, vertex_index - start, segments), vertex_index

    center_spans = (
        bounds[1] - bounds[0],
        bounds[3] - bounds[2],
        bounds[5] - bounds[4],
    )
    axis = max(range(3), key=center_spans.__getitem__)
    ordered = tuple(
        sorted(
            segments,
            key=lambda segment: segment.parent[axis] + segment.child[axis],
        )
    )
    middle = len(ordered) // 2
    left, vertex_index = _build_spatial_tree(ordered[:middle], vertices, vertex_index)
    right, vertex_index = _build_spatial_tree(ordered[middle:], vertices, vertex_index)
    return (
        _SpatialNode(
            bounds,
            left.start,
            left.count + right.count,
            children=(left, right),
        ),
        vertex_index,
    )


class CircuitViewport(QOpenGLWidget):
    """Batched OpenGL SWC renderer with CPU-assisted neuron and segment picking."""

    neuron_selected = Signal(str)
    compartments_changed = Signal(str, object)
    isolation_changed = Signal(bool)
    status_message = Signal(str)

    _palette = (
        "#6aa8ff",
        "#63d6a5",
        "#f4aa62",
        "#c58cff",
        "#ff7295",
        "#57c7dd",
        "#d4d96b",
        "#a3b8d8",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CircuitViewport")
        self.setAccessibleName("Circuit morphology viewport")
        self.setAccessibleDescription(
            "Interactive SWC morphology view. Click a neuron to isolate it, then click "
            "segments to select SWC child-node edges. Command-or-Control-Shift-drag a "
            "box to add multiple compartments; drag to rotate and use the mouse wheel to zoom."
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)

        self.morphologies: dict[str, Morphology] = {}
        self._ordered_ids: list[str] = []
        self._preview_ranges: dict[str, tuple[int, int]] = {}
        self._spatial_roots: dict[str, _SpatialNode] = {}
        self._vertex_data = b""
        self._preview_data = b""
        self._selection_data = b""
        self._geometry_dirty = True
        self._preview_dirty = True
        self._selection_dirty = True

        self.selected_neuron_id: str | None = None
        self.selected_compartments: set[int] = set()
        self.isolated = False

        self.yaw_degrees = DEFAULT_YAW_DEGREES
        self.pitch_degrees = DEFAULT_PITCH_DEGREES
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.distance = 10.0
        self.scene_center = QVector3D(0.0, 0.0, 0.0)
        self.scene_radius = 1.0

        self._press_position = QPointF()
        self._last_position = QPointF()
        self._dragged = False
        self._box_origin: QPoint | None = None
        self._box_selecting = False
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._rubber_band.setStyleSheet(
            "QRubberBand { border: 1px solid #ffffff; background-color: rgba(255,255,255,35); }"
        )
        self._interaction_preview = False
        self._interaction_timer = QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.setInterval(INTERACTION_SETTLE_MS)
        self._interaction_timer.timeout.connect(self._end_interaction_preview)
        self._last_submitted_segment_count = 0
        self._palette_rgba = tuple(_rgba(color) for color in self._palette)
        self._selected_neuron_rgba = _rgba(SELECTED_NEURON_COLOR)
        self._selected_compartment_rgba = _rgba(SELECTED_COMPARTMENT_COLOR)
        self._gpu_ready = False
        self._program: QOpenGLShaderProgram | None = None
        self._vertex_buffer: QOpenGLBuffer | None = None
        self._preview_buffer: QOpenGLBuffer | None = None
        self._selection_buffer: QOpenGLBuffer | None = None

    @property
    def neuron_count(self) -> int:
        return len(self.morphologies)

    @property
    def segment_count(self) -> int:
        return sum(len(morphology.segments) for morphology in self.morphologies.values())

    @property
    def interaction_preview_segment_count(self) -> int:
        return len(self._preview_data) // 24

    def set_morphologies(self, morphologies: Iterable[Morphology]) -> None:
        items = list(morphologies)
        self.morphologies = {item.record.neuron_id: item for item in items}
        self._ordered_ids = [item.record.neuron_id for item in items]
        vertices = array("f")
        preview_vertices = array("f")
        self._preview_ranges.clear()
        self._spatial_roots.clear()
        vertex_index = 0
        preview_vertex_index = 0
        total_segments = sum(len(item.segments) for item in items)
        build_preview = total_segments > INTERACTION_PREVIEW_THRESHOLD
        for morphology in items:
            neuron_id = morphology.record.neuron_id
            spatial_root, vertex_index = _build_spatial_tree(
                morphology.segments, vertices, vertex_index
            )
            self._spatial_roots[neuron_id] = spatial_root
            if build_preview:
                preview_start = preview_vertex_index
                budget = max(
                    256,
                    round(
                        INTERACTION_PREVIEW_SEGMENT_BUDGET
                        * len(morphology.segments)
                        / max(1, total_segments)
                    ),
                )
                for line in _preview_lines(morphology, budget):
                    preview_vertices.extend(line)
                    preview_vertex_index += 2
                self._preview_ranges[neuron_id] = (
                    preview_start,
                    preview_vertex_index - preview_start,
                )
        self._vertex_data = vertices.tobytes()
        self._preview_data = preview_vertices.tobytes()
        self._geometry_dirty = True
        self._preview_dirty = True
        self._interaction_timer.stop()
        self._interaction_preview = False
        self._box_selecting = False
        self._box_origin = None
        self._rubber_band.hide()
        self.unsetCursor()
        self.selected_neuron_id = None
        self.selected_compartments.clear()
        self.isolated = False
        self.yaw_degrees = DEFAULT_YAW_DEGREES
        self.pitch_degrees = DEFAULT_PITCH_DEGREES
        self._rebuild_selection_data()
        self.fit_all()
        self.update()

    def clear(self) -> None:
        self.set_morphologies(())

    def fit_all(self) -> None:
        if not self.morphologies:
            self.scene_center = QVector3D(0.0, 0.0, 0.0)
            self.scene_radius = 1.0
            self.distance = 10.0
        else:
            bounds = [morphology.bounds for morphology in self.morphologies.values()]
            xmin = min(value[0] for value in bounds)
            xmax = max(value[1] for value in bounds)
            ymin = min(value[2] for value in bounds)
            ymax = max(value[3] for value in bounds)
            zmin = min(value[4] for value in bounds)
            zmax = max(value[5] for value in bounds)
            self.scene_center = QVector3D(
                (xmin + xmax) / 2.0,
                (ymin + ymax) / 2.0,
                (zmin + zmax) / 2.0,
            )
            self.scene_radius = max(
                sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) / 2.0,
                0.01,
            )
            self.distance = max(self.scene_radius * 2.8, 0.1)
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()

    def focus_neuron(self, neuron_id: str, *, isolate: bool = True) -> None:
        morphology = self.morphologies.get(str(neuron_id))
        if morphology is None:
            return
        changed = self.selected_neuron_id != str(neuron_id)
        self.selected_neuron_id = str(neuron_id)
        self.isolated = bool(isolate)
        self._interaction_timer.stop()
        self._interaction_preview = False
        if changed:
            self.selected_compartments.clear()
            self._rebuild_selection_data()
        center = morphology.center
        self.scene_center = QVector3D(*center)
        self.scene_radius = morphology.radius
        self.distance = max(morphology.radius * 2.8, 0.1)
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.neuron_selected.emit(str(neuron_id))
        self.isolation_changed.emit(self.isolated)
        self.compartments_changed.emit(str(neuron_id), tuple(sorted(self.selected_compartments)))
        self.update()

    def restore_all(self) -> None:
        self.isolated = False
        self._interaction_timer.stop()
        self._interaction_preview = False
        self.fit_all()
        self.isolation_changed.emit(False)
        self.status_message.emit("Showing all loaded neurons")

    def clear_compartments(self) -> None:
        self.selected_compartments.clear()
        self._rebuild_selection_data()
        if self.selected_neuron_id is not None:
            self.compartments_changed.emit(self.selected_neuron_id, ())
        self.update()

    def _rebuild_selection_data(self) -> None:
        vertices = array("f")
        if self.selected_neuron_id is not None:
            morphology = self.morphologies.get(self.selected_neuron_id)
            if morphology is not None:
                wanted = self.selected_compartments
                for segment in morphology.segments:
                    if segment.child_id in wanted:
                        vertices.extend(segment.parent)
                        vertices.extend(segment.child)
        self._selection_data = vertices.tobytes()
        self._selection_dirty = True

    def initializeGL(self) -> None:
        try:
            functions = self.context().functions()
            functions.initializeOpenGLFunctions()
            functions.glEnable(GL_DEPTH_TEST)

            program = QOpenGLShaderProgram(self)
            vertex_shader = """
                attribute highp vec3 position;
                uniform highp mat4 mvp;
                void main() { gl_Position = mvp * vec4(position, 1.0); }
            """
            fragment_shader = """
                uniform lowp vec4 tint;
                void main() { gl_FragColor = tint; }
            """
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex_shader):
                raise RuntimeError(program.log())
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment_shader):
                raise RuntimeError(program.log())
            program.bindAttributeLocation("position", 0)
            if not program.link():
                raise RuntimeError(program.log())

            vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            preview_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            selection_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            if (
                not vertex_buffer.create()
                or not preview_buffer.create()
                or not selection_buffer.create()
            ):
                raise RuntimeError("OpenGL vertex buffers could not be created")
            vertex_buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            preview_buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            selection_buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
            self._program = program
            self._vertex_buffer = vertex_buffer
            self._preview_buffer = preview_buffer
            self._selection_buffer = selection_buffer
            self._gpu_ready = True
            self._geometry_dirty = True
            self._preview_dirty = True
            self._selection_dirty = True
        except Exception as exc:
            self._gpu_ready = False
            self.status_message.emit(f"Morphology renderer unavailable: {exc}")

    def _upload_if_needed(self) -> None:
        if not self._gpu_ready:
            return
        if self._geometry_dirty and self._vertex_buffer is not None:
            self._vertex_buffer.bind()
            self._vertex_buffer.allocate(self._vertex_data, len(self._vertex_data))
            self._vertex_buffer.release()
            self._geometry_dirty = False
        if self._preview_dirty and self._preview_buffer is not None:
            self._preview_buffer.bind()
            self._preview_buffer.allocate(self._preview_data, len(self._preview_data))
            self._preview_buffer.release()
            self._preview_dirty = False
        if self._selection_dirty and self._selection_buffer is not None:
            self._selection_buffer.bind()
            self._selection_buffer.allocate(self._selection_data, len(self._selection_data))
            self._selection_buffer.release()
            self._selection_dirty = False

    def paintGL(self) -> None:
        functions = self.context().functions()
        functions.glEnable(GL_DEPTH_TEST)
        functions.glClearColor(0.025, 0.043, 0.082, 1.0)
        functions.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if not self._gpu_ready or self._program is None or self._vertex_buffer is None:
            return
        self._upload_if_needed()
        if not self._vertex_data:
            return

        program = self._program
        program.bind()
        mvp = self._mvp()
        matrix = tuple(mvp.data())
        program.setUniformValue("mvp", mvp)
        use_preview = bool(
            self._interaction_preview
            and not self.isolated
            and self._preview_data
            and self._preview_buffer is not None
        )
        render_buffer = self._preview_buffer if use_preview else self._vertex_buffer
        render_buffer.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 0)

        visible_ids = (
            [self.selected_neuron_id]
            if self.isolated and self.selected_neuron_id in self.morphologies
            else self._ordered_ids
        )
        submitted_segments = 0
        for index, neuron_id in enumerate(visible_ids):
            if neuron_id is None:
                continue
            color = (
                self._selected_neuron_rgba
                if neuron_id == self.selected_neuron_id
                else self._palette_rgba[index % len(self._palette_rgba)]
            )
            program.setUniformValue("tint", color)
            functions.glLineWidth(
                2.5 if neuron_id == self.selected_neuron_id else (1.0 if use_preview else 1.35)
            )
            for start, count in self._render_ranges_for(
                neuron_id, matrix, use_preview=use_preview
            ):
                submitted_segments += count // 2
                functions.glDrawArrays(GL_LINES, start, count)
        self._last_submitted_segment_count = submitted_segments

        program.disableAttributeArray(0)
        render_buffer.release()

        if self._selection_data and self._selection_buffer is not None:
            # Keep selected compartments unmistakable even where their source
            # skeleton line has the same depth.
            functions.glDisable(GL_DEPTH_TEST)
            self._selection_buffer.bind()
            program.enableAttributeArray(0)
            program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 0)
            program.setUniformValue("tint", self._selected_compartment_rgba)
            functions.glLineWidth(5.0)
            functions.glDrawArrays(GL_LINES, 0, len(self._selection_data) // 12)
            program.disableAttributeArray(0)
            self._selection_buffer.release()
            functions.glEnable(GL_DEPTH_TEST)
        program.release()

    def resizeGL(self, width: int, height: int) -> None:
        if self.context() is not None:
            self.context().functions().glViewport(0, 0, max(1, width), max(1, height))

    def _mvp(self) -> QMatrix4x4:
        projection = QMatrix4x4()
        aspect = max(1.0, float(self.width())) / max(1.0, float(self.height()))
        half_height = max(0.001, self.distance * tan(radians(42.0 / 2.0)))
        half_width = half_height * aspect
        camera_depth = max(1.0, self.scene_radius * 4.0)
        projection.ortho(
            -half_width,
            half_width,
            -half_height,
            half_height,
            0.001,
            max(1000.0, camera_depth + self.scene_radius * 10.0),
        )
        view = QMatrix4x4()
        view.translate(self.pan_x, self.pan_y, -camera_depth)
        view.rotate(self.pitch_degrees, 1.0, 0.0, 0.0)
        view.rotate(self.yaw_degrees, 0.0, 1.0, 0.0)
        camera_position = QVector3D(*REFERENCE_CAMERA_POSITION)
        camera_focal = QVector3D(*REFERENCE_CAMERA_FOCAL_POINT)
        reference_forward = (camera_focal - camera_position).normalized()
        view.lookAt(QVector3D(0.0, 0.0, 0.0), reference_forward, QVector3D(*REFERENCE_CAMERA_VIEW_UP))
        view.translate(-self.scene_center)
        return projection * view

    def _project_with_depth(
        self, point: tuple[float, float, float], mvp: QMatrix4x4
    ) -> tuple[float, float, float] | None:
        return self._project_with_matrix(point, tuple(mvp.data()))

    def _project_with_matrix(
        self, point: tuple[float, float, float], matrix: tuple[float, ...]
    ) -> tuple[float, float, float] | None:
        # QMatrix4x4.data() is column-major. Manual projection avoids hundreds
        # of thousands of temporary QVector allocations during a large pick.
        x, y, z = point
        clip_x = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]
        clip_y = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]
        clip_z = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
        clip_w = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15]
        if clip_w <= 1e-8:
            return None
        nx, ny, nz = clip_x / clip_w, clip_y / clip_w, clip_z / clip_w
        return (
            (nx + 1.0) * 0.5 * self.width(),
            (1.0 - ny) * 0.5 * self.height(),
            nz,
        )

    def _project(self, point: tuple[float, float, float], mvp: QMatrix4x4) -> tuple[float, float] | None:
        projected = self._project_with_depth(point, mvp)
        return None if projected is None else projected[:2]

    def _bounds_contain_screen_point(
        self,
        bounds: tuple[float, float, float, float, float, float],
        x: float,
        y: float,
        matrix: tuple[float, ...],
        *,
        padding: float = 14.0,
    ) -> bool:
        corners = (
            (bounds[ix], bounds[iy], bounds[iz])
            for ix in (0, 1)
            for iy in (2, 3)
            for iz in (4, 5)
        )
        screen = [
            value
            for corner in corners
            if (value := self._project_with_matrix(corner, matrix)) is not None
        ]
        if not screen:
            return False
        xs, ys = [point[0] for point in screen], [point[1] for point in screen]
        return min(xs) - padding <= x <= max(xs) + padding and min(ys) - padding <= y <= max(ys) + padding

    def _bounds_intersect_screen_rect(
        self,
        bounds: tuple[float, float, float, float, float, float],
        rect: QRectF,
        matrix: tuple[float, ...],
        *,
        padding: float = 2.0,
    ) -> bool:
        corners = (
            (bounds[ix], bounds[iy], bounds[iz])
            for ix in (0, 1)
            for iy in (2, 3)
            for iz in (4, 5)
        )
        screen = [
            value
            for corner in corners
            if (value := self._project_with_matrix(corner, matrix)) is not None
        ]
        if not screen:
            return False
        xs, ys = [point[0] for point in screen], [point[1] for point in screen]
        projected_bounds = QRectF(
            min(xs) - padding,
            min(ys) - padding,
            max(xs) - min(xs) + padding * 2.0,
            max(ys) - min(ys) + padding * 2.0,
        )
        return projected_bounds.intersects(rect.normalized())

    @staticmethod
    def _clip_bounds_state(
        bounds: tuple[float, float, float, float, float, float],
        matrix: tuple[float, ...],
    ) -> int:
        """Return -1 outside, 0 intersecting, or 1 inside the x/y clip volume."""

        center = (
            (bounds[0] + bounds[1]) * 0.5,
            (bounds[2] + bounds[3]) * 0.5,
            (bounds[4] + bounds[5]) * 0.5,
        )
        extent = (
            (bounds[1] - bounds[0]) * 0.5,
            (bounds[3] - bounds[2]) * 0.5,
            (bounds[5] - bounds[4]) * 0.5,
        )
        # Homogeneous clip inequalities: x+w, -x+w, y+w, -y+w >= 0.
        planes = (
            (
                matrix[0] + matrix[3],
                matrix[4] + matrix[7],
                matrix[8] + matrix[11],
                matrix[12] + matrix[15],
            ),
            (
                -matrix[0] + matrix[3],
                -matrix[4] + matrix[7],
                -matrix[8] + matrix[11],
                -matrix[12] + matrix[15],
            ),
            (
                matrix[1] + matrix[3],
                matrix[5] + matrix[7],
                matrix[9] + matrix[11],
                matrix[13] + matrix[15],
            ),
            (
                -matrix[1] + matrix[3],
                -matrix[5] + matrix[7],
                -matrix[9] + matrix[11],
                -matrix[13] + matrix[15],
            ),
        )
        fully_inside = True
        for nx, ny, nz, offset in planes:
            distance = nx * center[0] + ny * center[1] + nz * center[2] + offset
            radius = abs(nx) * extent[0] + abs(ny) * extent[1] + abs(nz) * extent[2]
            if distance + radius < 0.0:
                return -1
            if distance - radius < 0.0:
                fully_inside = False
        return 1 if fully_inside else 0

    def _visible_spatial_ranges(
        self,
        node: _SpatialNode,
        matrix: tuple[float, ...],
    ) -> list[tuple[int, int]]:
        state = self._clip_bounds_state(node.bounds, matrix)
        if state < 0:
            return []
        if state > 0 or not node.children:
            return [(node.start, node.count)]
        ranges: list[tuple[int, int]] = []
        for child in node.children:
            for start, count in self._visible_spatial_ranges(child, matrix):
                if ranges and ranges[-1][0] + ranges[-1][1] == start:
                    prior_start, prior_count = ranges[-1]
                    ranges[-1] = (prior_start, prior_count + count)
                else:
                    ranges.append((start, count))
        return ranges

    def _render_ranges_for(
        self,
        neuron_id: str,
        matrix: tuple[float, ...],
        *,
        use_preview: bool,
    ) -> list[tuple[int, int]]:
        if use_preview:
            return [self._preview_ranges[neuron_id]]
        root = self._spatial_roots[neuron_id]
        deep_zoom = self.distance < self.scene_radius * EXACT_CULLING_ZOOM_RATIO
        if not deep_zoom:
            return [(root.start, root.count)]
        return self._visible_spatial_ranges(root, matrix)

    def exact_visible_segment_count(self) -> int:
        """Expose exact geometry submission size for diagnostics and regression tests."""

        matrix = tuple(self._mvp().data())
        visible = (
            [self.selected_neuron_id]
            if self.isolated and self.selected_neuron_id in self.morphologies
            else self._ordered_ids
        )
        return sum(
            count // 2
            for neuron_id in visible
            if neuron_id is not None
            for _start, count in self._render_ranges_for(
                neuron_id, matrix, use_preview=False
            )
        )

    def _segments_at_screen_point(
        self,
        node: _SpatialNode,
        x: float,
        y: float,
        matrix: tuple[float, ...],
    ) -> Iterable[SwcSegment]:
        if not self._bounds_contain_screen_point(node.bounds, x, y, matrix):
            return
        if node.children:
            for child in node.children:
                yield from self._segments_at_screen_point(child, x, y, matrix)
        else:
            yield from node.segments

    def _segments_in_screen_rect(
        self,
        node: _SpatialNode,
        rect: QRectF,
        matrix: tuple[float, ...],
    ) -> Iterable[SwcSegment]:
        if not self._bounds_intersect_screen_rect(node.bounds, rect, matrix):
            return
        if node.children:
            for child in node.children:
                yield from self._segments_in_screen_rect(child, rect, matrix)
        else:
            yield from node.segments

    def _select_compartments_in_rect(self, rect: QRectF) -> int:
        """Add isolated-neuron compartments whose projected lines cross *rect*."""

        neuron_id = self.selected_neuron_id
        if not self.isolated or neuron_id is None or neuron_id not in self.morphologies:
            return 0
        rect = rect.normalized()
        if rect.width() < 1.0 or rect.height() < 1.0:
            return 0
        matrix = tuple(self._mvp().data())
        matched: set[int] = set()
        root = self._spatial_roots[neuron_id]
        for segment in self._segments_in_screen_rect(root, rect, matrix):
            a = self._project_with_matrix(segment.parent, matrix)
            b = self._project_with_matrix(segment.child, matrix)
            if a is None or b is None:
                continue
            if _line_intersects_rect(a[0], a[1], b[0], b[1], rect):
                matched.add(segment.child_id)
        added = len(matched - self.selected_compartments)
        if matched:
            self.selected_compartments.update(matched)
            self._rebuild_selection_data()
            self.compartments_changed.emit(
                neuron_id, tuple(sorted(self.selected_compartments))
            )
            self.update()
        return added

    @staticmethod
    def _box_selection_requested(modifiers: Qt.KeyboardModifier) -> bool:
        command_or_control = (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        )
        return bool(modifiers & Qt.KeyboardModifier.ShiftModifier) and bool(
            modifiers & command_or_control
        )

    def _begin_interaction_preview(self) -> None:
        overview_scale = (
            self.distance
            >= self.scene_radius * INTERACTION_PREVIEW_MIN_DISTANCE_RATIO
        )
        if self._preview_data and not self.isolated and overview_scale:
            self._interaction_preview = True
            self._interaction_timer.start()
        elif self._interaction_preview:
            self._interaction_preview = False
            self._interaction_timer.stop()

    def _end_interaction_preview(self) -> None:
        if self._interaction_preview:
            self._interaction_preview = False
            self.update()

    def _candidate_ids(self, x: float, y: float, matrix: tuple[float, ...]) -> list[str]:
        visible = (
            [self.selected_neuron_id]
            if self.isolated and self.selected_neuron_id in self.morphologies
            else self._ordered_ids
        )
        candidates: list[str] = []
        for neuron_id in visible:
            if neuron_id is None:
                continue
            if self._bounds_contain_screen_point(
                self.morphologies[neuron_id].bounds, x, y, matrix
            ):
                candidates.append(neuron_id)
        return candidates

    def _pick(self, position: QPointF) -> tuple[str, SwcSegment] | None:
        mvp = self._mvp()
        matrix = tuple(mvp.data())
        best: tuple[tuple[float, float], str, SwcSegment] | None = None
        for neuron_id in self._candidate_ids(position.x(), position.y(), matrix):
            root = self._spatial_roots[neuron_id]
            for segment in self._segments_at_screen_point(
                root, position.x(), position.y(), matrix
            ):
                a = self._project_with_matrix(segment.parent, matrix)
                b = self._project_with_matrix(segment.child, matrix)
                if a is None or b is None:
                    continue
                distance, t = _point_segment_distance_and_t(
                    position.x(), position.y(), a[0], a[1], b[0], b[1]
                )
                if distance > 12.0:
                    continue
                depth = a[2] + t * (b[2] - a[2])
                # Cursor distance is primary; projected depth breaks true
                # screen-space overlaps without letting a nearby front
                # branch steal a click from the line under the pointer.
                score = (distance, depth)
                if best is None or score < best[0]:
                    best = (score, neuron_id, segment)
        if best is None:
            return None
        return best[1], best[2]

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._press_position = event.position()
        self._last_position = event.position()
        self._dragged = False
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.isolated
            and self.selected_neuron_id is not None
            and self._box_selection_requested(event.modifiers())
        ):
            self._box_origin = event.position().toPoint()
            self._box_selecting = True
            self._rubber_band.setGeometry(QRect(self._box_origin, self._box_origin))
            self._rubber_band.show()
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.status_message.emit("Box-selecting compartments on the isolated neuron")
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        delta = event.position() - self._last_position
        total = event.position() - self._press_position
        if abs(total.x()) + abs(total.y()) > 4.0:
            self._dragged = True
        if self._box_selecting and self._box_origin is not None:
            geometry = QRect(self._box_origin, event.position().toPoint()).normalized()
            self._rubber_band.setGeometry(geometry.intersected(self.rect()))
            self._last_position = event.position()
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._pan(delta.x(), delta.y())
            else:
                self._begin_interaction_preview()
                self.yaw_degrees += float(delta.x()) * 0.45
                self.pitch_degrees = max(-89.0, min(89.0, self.pitch_degrees + float(delta.y()) * 0.45))
                self.update()
        elif event.buttons() & Qt.MouseButton.MiddleButton:
            self._pan(delta.x(), delta.y())
        self._last_position = event.position()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._box_selecting and event.button() == Qt.MouseButton.LeftButton:
            selection_rect = QRectF(self._rubber_band.geometry())
            self._rubber_band.hide()
            self._box_selecting = False
            self._box_origin = None
            self.unsetCursor()
            added = self._select_compartments_in_rect(selection_rect)
            neuron_id = self.selected_neuron_id or "Neuron"
            self.status_message.emit(
                f"{neuron_id}: box added {added} SWC segment(s); "
                f"{len(self.selected_compartments)} selected"
            )
            event.accept()
            return
        if not self._dragged and event.button() == Qt.MouseButton.LeftButton:
            picked = self._pick(event.position())
            if picked is not None:
                neuron_id, segment = picked
                if self.isolated and self.selected_neuron_id == neuron_id:
                    if segment.child_id in self.selected_compartments:
                        self.selected_compartments.remove(segment.child_id)
                    else:
                        self.selected_compartments.add(segment.child_id)
                    self._rebuild_selection_data()
                    self.compartments_changed.emit(neuron_id, tuple(sorted(self.selected_compartments)))
                    self.status_message.emit(
                        f"{neuron_id}: {len(self.selected_compartments)} SWC segment(s) selected"
                    )
                    self.update()
                else:
                    self.focus_neuron(neuron_id, isolate=True)
                    self.status_message.emit(f"Selected and isolated neuron {neuron_id}")
        elif not self._dragged and event.button() == Qt.MouseButton.RightButton:
            picked = self._pick(event.position())
            if picked is not None:
                self.focus_neuron(picked[0], isolate=self.isolated)
                self.status_message.emit(f"Centered camera on neuron {picked[0]}")
            else:
                self.restore_all()
                self.status_message.emit("Camera framed all loaded neurons")
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.restore_all()
        event.accept()

    def wheelEvent(self, event) -> None:
        steps = float(event.angleDelta().y()) / 120.0
        self.distance = max(0.01, self.distance * (0.86 ** steps))
        self._begin_interaction_preview()
        self.update()
        event.accept()

    def _pan(self, dx_pixels: float, dy_pixels: float) -> None:
        self._begin_interaction_preview()
        world_per_pixel = (
            2.0 * max(self.distance, 0.01) * tan(radians(42.0 / 2.0)) / max(1.0, float(self.height()))
        )
        self.pan_x += float(dx_pixels) * world_per_pixel
        self.pan_y -= float(dy_pixels) * world_per_pixel
        self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_I):
            if self.isolated:
                self.restore_all()
            elif self.selected_neuron_id is not None:
                self.focus_neuron(self.selected_neuron_id, isolate=True)
        elif key in (Qt.Key.Key_F, Qt.Key.Key_R):
            if key == Qt.Key.Key_R:
                self.yaw_degrees = DEFAULT_YAW_DEGREES
                self.pitch_degrees = DEFAULT_PITCH_DEGREES
            if self.isolated and self.selected_neuron_id is not None:
                self.focus_neuron(self.selected_neuron_id, isolate=True)
            else:
                self.fit_all()
        elif key == Qt.Key.Key_C:
            self.clear_compartments()
        elif key == Qt.Key.Key_A:
            self._begin_interaction_preview()
            self.yaw_degrees -= 6.0
            self.update()
        elif key == Qt.Key.Key_D:
            self._begin_interaction_preview()
            self.yaw_degrees += 6.0
            self.update()
        elif key == Qt.Key.Key_W:
            self._begin_interaction_preview()
            self.pitch_degrees = max(-89.0, self.pitch_degrees - 6.0)
            self.update()
        elif key == Qt.Key.Key_S:
            self._begin_interaction_preview()
            self.pitch_degrees = min(89.0, self.pitch_degrees + 6.0)
            self.update()
        elif key == Qt.Key.Key_Left:
            self._pan(-18.0, 0.0)
        elif key == Qt.Key.Key_Right:
            self._pan(18.0, 0.0)
        elif key == Qt.Key.Key_Up:
            self._pan(0.0, -18.0)
        elif key == Qt.Key.Key_Down:
            self._pan(0.0, 18.0)
        else:
            super().keyPressEvent(event)
