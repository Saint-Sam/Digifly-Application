"""Generate a small native/compiled comparison using the exact renderer setup.

This measures Qt callback dispatch; it does not qualify an application release.
"""
import argparse
import ast
import hashlib
from pathlib import Path
import textwrap


def prepare(root, output):
    output.mkdir(parents=True, exist_ok=False)
    source = root / 'src/digifly_app/ui/circuit_viewport.py'
    tree = ast.parse(source.read_text())
    scene = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == '_CircuitScene')
    method = next(n for n in scene.body if isinstance(n, ast.FunctionDef) and n.name == 'initializeGL')
    method.body.insert(0, ast.parse('self.initializations += 1').body[0])
    setup = textwrap.indent(ast.unparse(ast.fix_missing_locations(method)), '    ')
    header = '''import json,sys,time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import QOpenGLBuffer,QOpenGLShader,QOpenGLShaderProgram,QOpenGLFunctions_2_0
from shiboken6 import delete
GL_DEPTH_TEST=0x0B71
class Messages:
    def __init__(self):self.values=[]
    def emit(self,message):self.values.append(message)
class Scene:
    def __init__(self):
        super().__init__()
        self.initializations=0;self.paints=0;self.resizes=0
        self._gpu_ready=False;self._program=None
        self.status_message=Messages()
    def paintGL(self):self.paints+=1
    def resizeGL(self,w,h):self.resizes+=1
'''
    classes = '''
class Mixed(Scene,QOpenGLWidget):pass
class Forward(Scene,QOpenGLWidget):
    def initializeGL(self):return Scene.initializeGL(self)
    def paintGL(self):return Scene.paintGL(self)
    def resizeGL(self,w,h):return Scene.resizeGL(self,w,h)
class Direct(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.initializations=0;self.paints=0;self.resizes=0
        self._gpu_ready=False;self._program=None;self.status_message=Messages()
    def initializeGL(self):return Scene.initializeGL(self)
    def paintGL(self):self.paints+=1
    def resizeGL(self,w,h):self.resizes+=1
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
record=dict(compiled='__compiled__' in globals(),platform=sys.platform,cases=[])
for cls in (Direct,Mixed,Forward):
    widget=cls();widget.resize(400,400);widget.show()
    loop=__import__('PySide6.QtCore',fromlist=['QEventLoop']).QEventLoop()
    QTimer.singleShot(1200,loop.quit);loop.exec()
    context=widget.context()
    row=dict(name=cls.__name__,valid=widget.isValid(),context=bool(context),
        initialized=widget.initializations,paints=widget.paints,resizes=widget.resizes,
        gpu_ready=widget._gpu_ready,messages=widget.status_message.values)
    if widget.isValid() and not widget._gpu_ready:
        widget.makeCurrent();Scene.initializeGL(widget);widget.doneCurrent()
        row['manual_gpu_ready']=widget._gpu_ready
        row['manual_messages']=widget.status_message.values
    widget.grab().save(str(Path(sys.argv[1]).with_name(cls.__name__+'.png')))
    record['cases'].append(row)
    widget.close();delete(widget);app.processEvents()
Path(sys.argv[1]).write_text(json.dumps(record,indent=2))
print(json.dumps(record))
'''
    target = output / 'main.py'
    target.write_text(header + setup + '\n' + classes)
    print(dict(generated=str(target), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parents[1], args.output)
