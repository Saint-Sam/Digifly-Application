"""Bounded start of the app's manual-review mode; never clicks, downloads or runs.

Uses the existing app CLI rather than a potentially blocking OS app-open call.
All subsequent GUI interaction and actual window observation use computer control.
One output/claim identifies one attempt. A timeout preserves that attempt and
its PID/logs; it does not kill an uncertain window or automatically launch again.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def checked_review_copy(executable: Path, receipt: Path):
    """A staged candidate is not a completed review-copy transaction."""
    executable=executable.resolve();receipt=receipt.resolve()
    if not receipt.is_file():
        raise ValueError('Checked review copy is not complete; do not launch the preceding app')
    record=json.loads(receipt.read_text())
    application=executable.parents[2]
    with executable.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if (record.get('ok') is not True or record.get('mode') not in {'review','install'}
            or Path(record.get('current_app','')).resolve()!=application
            or record.get('actual_executable_sha256')!=digest):
        raise ValueError('Checked review copy does not match this executable')
    manifest=json.loads((application/'Contents/Resources/digifly_build_sources.json').read_text())
    if not record.get('build') or manifest.get('build')!=record['build']:
        raise ValueError('Checked review copy has the wrong embedded build')
    return {'receipt':str(receipt),'executable_sha256':digest,'build':record['build']}


def launch(command, output: Path, *, timeout=30., hold=False, copy_receipt=None):
    if not 0<timeout<=60:
        raise ValueError('Manual app launch readiness deadline must be at most60seconds')
    output=output.resolve()
    checked=checked_review_copy(Path(command[0]),Path(copy_receipt)) if copy_receipt is not None else None
    if output.exists():
        raise FileExistsError('This review attempt already exists; inspect it instead of starting another app')
    claim=output.with_name(output.name+'-launch')
    claim.mkdir(parents=True,exist_ok=False)
    record={'command':list(map(str,command)),'output':str(output),'window_confirmed':False,
            'launcher_pid':os.getpid(),
            'ownership_mode':'foreground-held' if hold else 'startup-only'}
    if checked is not None:record['checked_copy']=checked
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
    if hold:
        # Keep the tool session's parent alive. Some command hosts retire its
        # descendants when an otherwise-successful launcher exits. A ready
        # file alone must never qualify a later, unrelated default-profile app.
        verify_live_review(output)
        print(json.dumps(record,indent=2),flush=True)
        record['exit_code']=child.wait()
        (claim/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def verify_live_review(output: Path):
    """Refuse a dead/wrong owner before computer control can reopen another app."""
    output=output.resolve()
    claim=output.with_name(output.name+'-launch')
    record=json.loads((claim/'launch.json').read_text())
    if (record.get('ownership_mode')!='foreground-held'
            or not record.get('window_confirmed') or 'exit_code' in record):
        raise ValueError('Review does not have a live foreground-held owner; do not bind or relaunch it')
    pid=record['pid']
    parent=record.get('launcher_pid')
    if (not isinstance(pid,int) or pid<=0 or not isinstance(parent,int) or parent<=0):
        raise ValueError('Review owner identities must be positive process IDs')
    ready=json.loads((output/'window-ready.json').read_text())
    if ready.get('pid')!=pid or ready.get('window_visible') is not True:
        raise ValueError('Review readiness belongs to a different process')
    command=record['command']
    if record.get('checked_copy') is not None:
        if checked_review_copy(Path(command[0]),Path(record['checked_copy']['receipt']))!=record['checked_copy']:
            raise ValueError('The checked review executable changed after launch')
    for flag,key in (('--workspace','workspace'),('--existing-profile','profile')):
        if flag in command and ready.get(key)!=command[command.index(flag)+1]:
            raise ValueError('Review readiness has the wrong workspace or profile')
    try:
        os.kill(pid,0)
        os.kill(parent,0)
    except (ProcessLookupError,PermissionError) as exc:
        raise ValueError('Review process is gone or uninspectable; do not bind or relaunch it') from exc
    if os.name=='nt':
        raise ValueError('Live owner command verification is not qualified on Windows')
    actual=subprocess.run(['/bin/ps','-p',str(pid),'-o','ppid=,command='],
                          capture_output=True,text=True,timeout=5)
    fields=actual.stdout.strip().split(None,1)
    if (actual.returncode or len(fields)!=2 or fields[0]!=str(parent)
            or fields[1]!=' '.join(command)):
        raise ValueError('Review PID does not own the exact launched command; do not bind or relaunch it')
    return {'ok':True,'pid':pid,'output':str(output),
            'scope':'Live owner/command/readiness identity only, not UI or feature acceptance'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--executable',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workspace',type=Path)
    p.add_argument('--profile',type=Path)
    p.add_argument('--hold',action='store_true',help='Keep the launcher session until the review app quits')
    p.add_argument('--verify-live',action='store_true',help='Check existing held PID/command before computer control')
    p.add_argument('--copy-receipt',type=Path,help='Require the completed checked-copy receipt and exact build before any launch')
    a=p.parse_args()
    if a.verify_live:
        print(json.dumps(verify_live_review(a.output),indent=2))
    else:
        if not all((a.executable,a.workspace,a.profile)):
            p.error('--executable, --workspace and --profile are required to launch')
        print(json.dumps(launch([str(a.executable.resolve()),'--library-ui-review',str(a.output.resolve()),
            '--workspace',str(a.workspace.resolve()),'--existing-profile',str(a.profile.resolve())],a.output,hold=a.hold,copy_receipt=a.copy_receipt),indent=2))
