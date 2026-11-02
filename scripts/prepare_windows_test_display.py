"""Set and verify a temporary display mode on disposable Windows CI only.

Uses EnumDisplaySettingsW and ChangeDisplaySettingsW (CDS_TEST, then flags 0).
No registry update and no change on a user's computer.
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changedisplaysettingsw
"""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time

def require_screen_space(width, height):
    # The existing 1120x760 client also needs room for its native title frame.
    if width < 1120 or height < 800:
        raise ValueError(f'Native UI test desktop is too small: {width}x{height}; need at least 1120x800 available logical pixels')

def require_disposable_windows_ci():
    if (sys.platform!='win32' or os.environ.get('GITHUB_ACTIONS')!='true'
            or os.environ.get('RUNNER_OS')!='Windows'):
        raise ValueError('Display setup is restricted to disposable Windows GitHub CI')

def set_supported_mode():
    from ctypes import wintypes as w
    class Mode(ctypes.Structure):
        _fields_=[('device',w.WCHAR*32),('spec',w.WORD),('driver',w.WORD),
            ('size',w.WORD),('extra',w.WORD),('fields',w.DWORD),
            ('position_x',w.LONG),('position_y',w.LONG),('orientation',w.DWORD),('fixed',w.DWORD),
            ('color',w.SHORT),('duplex',w.SHORT),('yres',w.SHORT),('tt',w.SHORT),('collate',w.SHORT),
            ('form',w.WCHAR*32),('logpixels',w.WORD),('bits',w.DWORD),('width',w.DWORD),('height',w.DWORD),
            ('flags',w.DWORD),('frequency',w.DWORD),('icm_method',w.DWORD),('icm_intent',w.DWORD),
            ('media',w.DWORD),('dither',w.DWORD),('reserved1',w.DWORD),('reserved2',w.DWORD),
            ('pan_width',w.DWORD),('pan_height',w.DWORD)]
    assert ctypes.sizeof(Mode)==220, 'Unexpected Windows DEVMODEW layout'
    api=ctypes.WinDLL('user32',use_last_error=True)
    enum=api.EnumDisplaySettingsW
    enum.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(Mode)];enum.restype=w.BOOL
    change=api.ChangeDisplaySettingsW
    change.argtypes=[ctypes.POINTER(Mode),w.DWORD];change.restype=w.LONG
    modes=[]
    for index in range(512):
        mode=Mode();mode.size=ctypes.sizeof(mode)
        if not enum(None,index,ctypes.byref(mode)):break
        if mode.bits==32 and mode.width>=1920 and mode.height>=1080:
            mode.fields=0x00040000|0x00080000|0x00100000|0x00200000|0x00400000
            modes.append(mode)
    modes.sort(key=lambda m:(m.width*m.height,abs(m.frequency-60)))
    for mode in modes:
        if change(ctypes.byref(mode),2)!=0:continue  # CDS_TEST: no mutation.
        code=change(ctypes.byref(mode),0)  # Temporary mode; no registry write.
        if code==0:return dict(width=mode.width,height=mode.height,bits=mode.bits,frequency=mode.frequency)
    raise RuntimeError('No supported temporary 1920x1080-or-larger CI display mode')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();out=args.output.resolve()
    require_disposable_windows_ci()
    if out.exists():raise ValueError('Display receipt must be new')
    from PySide6.QtGui import QGuiApplication
    app=QGuiApplication([])
    def geometry():
        app.processEvents();r=app.primaryScreen().availableGeometry()
        return dict(x=r.x(),y=r.y(),width=r.width(),height=r.height())
    before=geometry();record=dict(ok=False,before=before,changed=False,registry_changed=False)
    try:
        require_screen_space(before['width'],before['height'])
    except ValueError:
        record['mode']=set_supported_mode();record['changed']=True
    deadline=time.monotonic()+5
    while True:
        after=geometry()
        try:require_screen_space(after['width'],after['height']);break
        except ValueError:
            if time.monotonic()>=deadline:raise
            time.sleep(.02)
    record.update(ok=True,after=after)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as stream:json.dump(record,stream,indent=2)
    print(json.dumps(record))
    return 0

if __name__=='__main__':raise SystemExit(main())
