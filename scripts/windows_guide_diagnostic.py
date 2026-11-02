"""Observe the real wizard's pointer targets; no downloads or simulator run."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import traceback

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV,make_standalone_profile
    profile=out/'workspace/config/resources.json'
    make_standalone_profile(workstation_root=out/'workspace').save(profile)
    os.environ[PROFILE_PATH_ENV]=str(profile);os.environ[SETTINGS_ROOT_ENV]=str(out/'settings')
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from digifly_app.ui.main_window import MainWindow
    from digifly_app.ui.package_first_experiment_smoke import check_first_experiment_guide
    from digifly_app.ui.package_library_smoke import dispose_fixture_window
    app=QApplication.instance() or QApplication([])
    window=MainWindow();window.show()
    original=QApplication.widgetAt;observations=[]
    def rect(value):return [value.x(),value.y(),value.width(),value.height()]
    def observed(*args):
        hit=original(*args);point=args[0] if len(args)==1 else QPoint(*args)
        guide=window.first_experiment_guide;button=guide.next_button
        if button.rect().contains(button.mapFromGlobal(point)):
            screen=app.screenAt(point)
            item=dict(step=guide.index,point=[point.x(),point.y()],
                hit=None if hit is None else dict(name=hit.objectName(),kind=type(hit).__name__),
                visible=button.isVisible(),enabled=button.isEnabled(),
                window=rect(window.geometry()),frame=rect(window.frameGeometry()),
                guide=rect(guide.geometry()),button=rect(button.geometry()),
                available=rect(screen.availableGeometry()) if screen else None,
                screens=[rect(s.availableGeometry()) for s in app.screens()])
            if not observations or observations[-1]!=item:observations.append(item)
        return hit
    QApplication.widgetAt=observed
    record=dict(ok=False,qualification=False,scope='Wizard pointer diagnosis only')
    try:
        record['flags']=check_first_experiment_guide(window,out)
        record['ok']=True
    except Exception:
        record['error']=traceback.format_exc()
        try:window.grab().save(str(out/'failed-window.png'))
        except Exception:record['capture_error']=traceback.format_exc()
    finally:
        QApplication.widgetAt=original
        record['observations']=observations
        (out/'diagnostic.json').write_text(json.dumps(record,indent=2))
        dispose_fixture_window(window,app)
    print(json.dumps(record,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
