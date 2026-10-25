"""Bounded start of the app's manual-review mode; never clicks, downloads or runs.

Uses the existing app CLI rather than a potentially blocking OS app-open call.
All subsequent GUI interaction and actual window observation use computer control.
One output/claim identifies one attempt. A timeout preserves that attempt and
its PID/logs; it does not kill an uncertain window or automatically launch again.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time


def launch(command, output: Path, *, timeout=30.):
    if not 0<timeout<=60:
        raise ValueError('Manual app launch readiness deadline must be at most60seconds')
    output=output.resolve()
    if output.exists():
        raise FileExistsError('This review attempt already exists; inspect it instead of starting another app')
    claim=output.with_name(output.name+'-launch')
    claim.mkdir(parents=True,exist_ok=False)
    record={'command':list(map(str,command)),'output':str(output),'window_confirmed':False}
    with (claim/'stdout.txt').open('x') as stdout,(claim/'stderr.txt').open('x') as stderr:
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
    record['pid']=child.pid
    (claim/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
    start=time.monotonic();ready=output/'window-ready.json'
    while time.monotonic()-start<timeout:
        if ready.is_file():
            evidence=json.loads(ready.read_text())
            if (evidence.get('pid')!=child.pid or evidence.get('window_visible') is not True
                    or evidence.get('automatic_download_or_simulation') is not False):
                raise ValueError('Manual review readiness does not match the actual app process')
            record['window_confirmed']=True
            break
        if child.poll() is not None:
            record['exit_code']=child.returncode
            break
        time.sleep(.1)
    record['elapsed_seconds']=time.monotonic()-start
    (claim/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
    if not record['window_confirmed']:
        raise RuntimeError('App window not confirmed within the bounded launch. Inspect the saved PID/logs; do not duplicate this attempt')
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--executable',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--profile',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(launch([str(a.executable.resolve()),'--library-ui-review',str(a.output.resolve()),
        '--workspace',str(a.workspace.resolve()),'--existing-profile',str(a.profile.resolve())],a.output),indent=2))
