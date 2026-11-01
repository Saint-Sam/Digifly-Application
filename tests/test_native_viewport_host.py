"""Presentation boundaries only; source geometry and simulation are untouched."""
import os
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication,QWidget
from digifly_app.ui.circuit_viewport import CircuitViewport


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
def test_native_snapshot_obeys_parent_clipping_and_pixel_ratio(monkeypatch,ratio):
    from PySide6.QtGui import QColor,QImage,QPixmap
    from digifly_app.ui.circuit_viewport import composite_native_viewports
    app=QApplication.instance() or QApplication([])
    root=QWidget();root.resize(200,120)
    clip=QWidget(root);clip.setGeometry(10,10,60,50)
    viewport=CircuitViewport(clip);viewport.setMinimumSize(1,1);viewport.setGeometry(0,30,100,80)
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


def test_native_snapshot_keeps_selection_outline_above_its_scene(monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor,QImage,QPixmap
    from digifly_app.ui.circuit_viewport import composite_native_viewports
    app=QApplication.instance() or QApplication([])
    root=QWidget();root.resize(150,120)
    viewport=CircuitViewport(root);viewport.setMinimumSize(1,1);viewport.setGeometry(10,10,100,80)
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
