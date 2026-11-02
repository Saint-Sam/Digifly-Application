"""Presentation boundaries only; source geometry and simulation are untouched."""
import os
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QWidget
from digifly_app.ui.circuit_viewport import _NativeCircuitViewport as CircuitViewport


@pytest.fixture(params=('native', 'windows'))
def composed_viewport(monkeypatch, request):
    from digifly_app.ui import circuit_viewport as scene
    selected = scene._NativeCircuitViewport if request.param == 'native' else scene._WindowsCircuitViewport
    monkeypatch.setattr(scene, 'CircuitViewport', selected)
    return selected


def test_windows_uses_widget_context_and_mac_retains_native_surface():
    import sys
    from digifly_app.ui import circuit_viewport as scene
    assert scene.CircuitViewport is scene._viewport_class(sys.platform)
    assert scene._viewport_class('win32') is scene._WindowsCircuitViewport
    assert issubclass(scene._viewport_class('win32'), QOpenGLWidget)
    assert not issubclass(scene._viewport_class('darwin'), QOpenGLWidget)
    assert scene._viewport_class('darwin') is scene._NativeCircuitViewport
    # All camera, picking, geometry, voltage and render calculations stay shared.
    for name in ('initializeGL', 'paintGL', 'resizeGL', 'mousePressEvent',
                 'mouseMoveEvent', 'mouseReleaseEvent', 'wheelEvent'):
        assert getattr(scene._WindowsCircuitViewport, name) is getattr(scene._NativeCircuitViewport, name)


def test_windows_widget_keeps_state_and_has_no_embedded_native_window():
    from digifly_app.ui.circuit_viewport import _WindowsCircuitViewport
    app = QApplication.instance() or QApplication([])
    viewport = _WindowsCircuitViewport(camera_only=True)
    try:
        assert viewport.camera_only and not viewport.isValid()
        assert viewport._surface is None and viewport._surface_container is None
        assert not viewport._rubber_band.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    finally:
        viewport.deleteLater(); app.processEvents()


def test_viewport_does_not_force_render_to_texture_on_its_owner_window():
    assert issubclass(CircuitViewport,QWidget)
    assert not issubclass(CircuitViewport,QOpenGLWidget)


def test_headless_viewport_keeps_state_without_claiming_a_graphics_context():
    app=QApplication.instance() or QApplication([])
    viewport=CircuitViewport(camera_only=True)
    try:
        assert viewport.camera_only
        assert viewport.context() is None
        assert not viewport.isValid()
    finally:
        viewport.close();viewport.deleteLater();app.processEvents()


def test_native_surface_routes_same_local_camera_events_and_respects_disabled_host():
    from PySide6.QtCore import QEvent,QPointF,Qt
    from PySide6.QtGui import QMouseEvent
    from digifly_app.ui.circuit_viewport import _CircuitSurface
    app=QApplication.instance() or QApplication([]);viewport=CircuitViewport(camera_only=True)
    surface=_CircuitSurface(viewport)
    def event(kind,x,y,button,buttons):
        return QMouseEvent(kind,QPointF(x,y),QPointF(x,y),button,buttons,
                           Qt.KeyboardModifier.NoModifier)
    try:
        original=viewport.yaw_degrees
        surface.mousePressEvent(event(QEvent.Type.MouseButtonPress,10,20,
            Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton))
        surface.mouseMoveEvent(event(QEvent.Type.MouseMove,30,20,
            Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton))
        assert viewport.yaw_degrees==original+9.
        viewport.setEnabled(False)
        surface.mouseMoveEvent(event(QEvent.Type.MouseMove,50,20,
            Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton))
        assert viewport.yaw_degrees==original+9.
    finally:
        surface.close();surface.deleteLater();viewport.close();viewport.deleteLater();app.processEvents()


@pytest.mark.parametrize('ratio',[1,2])
def test_native_snapshot_obeys_parent_clipping_and_pixel_ratio(monkeypatch,ratio,composed_viewport):
    from PySide6.QtGui import QColor,QImage,QPixmap
    from digifly_app.ui.circuit_viewport import composite_native_viewports
    app=QApplication.instance() or QApplication([])
    root=QWidget();root.resize(200,120)
    clip=QWidget(root);clip.setGeometry(10,10,60,50)
    viewport=composed_viewport(clip);viewport.setMinimumSize(1,1);viewport.setGeometry(0,30,100,80)
    native=QImage(100*ratio,80*ratio,QImage.Format.Format_ARGB32);native.fill(QColor('red'))
    monkeypatch.setattr(viewport,'isValid',lambda:True)
    monkeypatch.setattr(viewport,'grabFramebuffer',lambda:native)
    root.show();app.processEvents()
    try:
        pixmap=QPixmap(200*ratio,120*ratio);pixmap.setDevicePixelRatio(ratio);pixmap.fill(QColor('green'))
        image=composite_native_viewports(root,pixmap).toImage()
        assert image.pixelColor(20*ratio,50*ratio)==QColor('red')
        assert image.pixelColor(20*ratio,65*ratio)==QColor('green')
        assert image.pixelColor(80*ratio,50*ratio)==QColor('green')
    finally:
        root.close();root.deleteLater();app.processEvents()


def test_native_snapshot_keeps_selection_outline_above_its_scene(monkeypatch,composed_viewport):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor,QImage,QPixmap
    from digifly_app.ui.circuit_viewport import composite_native_viewports
    app=QApplication.instance() or QApplication([])
    root=QWidget();root.resize(150,120)
    viewport=composed_viewport(root);viewport.setMinimumSize(1,1);viewport.setGeometry(10,10,100,80)
    native=QImage(100,80,QImage.Format.Format_ARGB32);native.fill(QColor('red'))
    monkeypatch.setattr(viewport,'isValid',lambda:True)
    monkeypatch.setattr(viewport,'grabFramebuffer',lambda:native)
    outline=viewport._rubber_band;outline.setGeometry(20,20,25,25)
    def paint_outline(painter,offset):
        painter.fillRect(QRect(offset,outline.size()),QColor('yellow'))
    monkeypatch.setattr(outline,'render',paint_outline)
    root.show();outline.show();app.processEvents()
    try:
        pixmap=QPixmap(150,120);pixmap.fill(QColor('green'))
        image=composite_native_viewports(root,pixmap).toImage()
        assert image.pixelColor(35,35)==QColor('yellow')
        assert image.pixelColor(20,20)==QColor('red')
    finally:
        root.close();root.deleteLater();app.processEvents()


@pytest.mark.parametrize('exposed,framebuffer', [(False, 1), (True, 0)])
def test_snapshot_refuses_native_frame_without_exposure_and_allocated_buffer(monkeypatch, exposed, framebuffer):
    app=QApplication.instance() or QApplication([])
    viewport=CircuitViewport(camera_only=True)
    class Surface:
        def isExposed(self):return exposed
        def defaultFramebufferObject(self):return framebuffer
        def width(self):return 200
        def height(self):return 100
        def grabFramebuffer(self):raise AssertionError('Unsafe native framebuffer read')
    monkeypatch.setattr(viewport,'isValid',lambda:True)
    viewport._surface=Surface()
    try:
        with pytest.raises(RuntimeError,match='first visible frame'):
            viewport.grabFramebuffer()
    finally:
        viewport._surface=None
        viewport.close();viewport.deleteLater();app.processEvents()


def test_snapshot_reads_a_presented_native_frame(monkeypatch):
    from PySide6.QtGui import QImage
    app=QApplication.instance() or QApplication([])
    viewport=CircuitViewport(camera_only=True)
    image=QImage(20,10,QImage.Format.Format_ARGB32)
    class Surface:
        def isExposed(self):return True
        def defaultFramebufferObject(self):return 1
        def width(self):return 20
        def height(self):return 10
        def grabFramebuffer(self):return image
    monkeypatch.setattr(viewport,'isValid',lambda:True)
    viewport._surface=Surface()
    try:
        assert viewport.grabFramebuffer() is image
    finally:
        viewport._surface=None
        viewport.close();viewport.deleteLater();app.processEvents()


def test_composed_capture_does_not_silently_omit_a_visible_unready_native_scene(monkeypatch,composed_viewport):
    from PySide6.QtGui import QPixmap
    from digifly_app.ui.circuit_viewport import composite_native_viewports,NativeSceneNotReady
    app=QApplication.instance() or QApplication([])
    root=QWidget();root.resize(200,120)
    viewport=composed_viewport(root);viewport.setMinimumSize(1,1);viewport.setGeometry(10,10,100,80)
    root.show();app.processEvents()
    viewport._surface=object()
    monkeypatch.setattr(viewport,'isValid',lambda:False)
    try:
        with pytest.raises(NativeSceneNotReady,match='first visible frame'):
            composite_native_viewports(root,QPixmap(200,120))
    finally:
        viewport._surface=None
        root.close();root.deleteLater();app.processEvents()
