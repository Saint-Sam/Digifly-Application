"""Bounded isolated-process execution of independent, pre-materialized trials.

No simulator is imported here. Each command starts a fresh interpreter and its
own process group; no live hoc state is forked or shared between trials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence


class TrialCancelled(RuntimeError):
    pass


def _available_memory() -> tuple[int | None,str]:
    try:
        import psutil
        return int(psutil.virtual_memory().available),"psutil.virtual_memory.available"
    except (ImportError,OSError,AttributeError):
        pass
    # Scientific runtimes need not install the app's psutil dependency.
    # Read the same reclaimable free+inactive pages used by macOS psutil,
    # and Linux's kernel MemAvailable, without launching a scientific process.
    try:
        if sys.platform=="darwin":
            output=subprocess.run(["/usr/bin/vm_stat"],capture_output=True,text=True,timeout=5,check=True).stdout
            page_size=int(re.search(r"page size of (\d+) bytes",output).group(1))
            free=int(re.search(r"Pages free:\s+(\d+)",output).group(1))
            inactive=int(re.search(r"Pages inactive:\s+(\d+)",output).group(1))
            return (free+inactive)*page_size,"Darwin vm_stat free+inactive pages"
        if sys.platform.startswith("linux"):
            memory=Path("/proc/meminfo").read_text()
            return int(re.search(r"MemAvailable:\s+(\d+) kB",memory).group(1))*1024,"Linux /proc/meminfo MemAvailable"
    except (OSError,AttributeError,ValueError,subprocess.SubprocessError):
        pass
    return None,"unavailable"


def resource_forecast(requested: int, trials: int, *, engine: str,
                      segments: int = 0, report_values: int = 0,
                      cpu_count: int | None = None, available_memory: int | None = None) -> dict[str, Any]:
    """An explicit conservative engineering reserve, not a measured RSS claim."""
    cores = max(1, int(cpu_count if cpu_count is not None else os.cpu_count() or 1))
    memory_source="caller supplied"
    if available_memory is None:
        available_memory,memory_source=_available_memory()
    reserve = (1024 if engine == "bmtk" else 512) * 1024**2 + max(0,segments)*4096 + max(0,report_values)*64
    budget = int(available_memory*.6) if available_memory is not None else None
    memory_cap = max(0, budget//reserve) if budget is not None else 1
    effective = min(max(1,int(requested)),max(1,int(trials)),cores,memory_cap)
    return {"mode":"isolated_condition_repetition_processes", "requested_workers":int(requested),
            "effective_workers":effective,"execution_admitted":effective>0,"runnable_trials":int(trials),"logical_cpus":cores,
            "available_memory_bytes":available_memory,"available_memory_source":memory_source,"memory_budget_bytes":budget,
            "estimated_reserve_per_trial_bytes":reserve,"memory_worker_cap":memory_cap,
            "estimate_policy":"60% available-memory budget; fixed runtime reserve plus 4096 bytes/segment and 64 bytes/report value; engineering forecast, not measured RSS or guaranteed peak",
            "unavailable_memory_policy":"Use one process when available memory cannot be measured",
            "compiler_policy":"Compile once serially before dispatch; children load hash-verified libraries without compiling",
            "seed_policy":"Original random_seed + repetition - 1; trial completion order never changes input seeds"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signal_group(child: subprocess.Popen[Any], *, force: bool = False) -> None:
    try:
        if os.name == "posix":
            os.killpg(child.pid,signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            child.kill()
        else:
            child.terminate()
    except ProcessLookupError:
        pass


def execute(tasks: Sequence[Mapping[str, Any]], workers: int, *,
            environment: Mapping[str,str], receipt_path: Path,
            cancellation: Callable[[],None] | None = None,
            cancel_marker: Path | None = None, timeout_seconds: float | None = None) -> dict[str, Any]:
    """Run up to workers trials; on first failure/cancel, stop and reap all groups."""
    if workers < 1:
        raise ValueError("Independent trial resource forecast cannot fit even one trial; no child was dispatched")
    if os.name != "posix" and tasks:
        raise ValueError("Isolated trial concurrency is enabled only on POSIX hosts; Windows descendant cancellation is not verified. Use Workers 1.")
    records: list[dict[str,Any]] = [{"index":i,"label":str(t.get("label",i)),"seed":t.get("seed"),"state":"pending"} for i,t in enumerate(tasks)]
    active: dict[int,tuple[subprocess.Popen[Any],Any,float]] = {}
    next_index = 0
    requested_signal: list[int] = []
    previous_handlers = {}
    if cancellation is None:
        for signum in (signal.SIGTERM,signal.SIGINT):
            try:
                previous_handlers[signum] = signal.signal(signum,lambda number,_frame:requested_signal.append(number))
            except ValueError:
                pass
    result: dict[str,Any] = {"schema_version":1,"mode":"isolated_condition_repetition_processes",
                           "effective_workers":workers,"tasks":records,"max_active_children":0,"state":"running",
                           "cleanup_verified":True,"cleanup_errors":[]}
    def stop_group(child: subprocess.Popen[Any], *, force: bool = False) -> None:
        try:
            _signal_group(child,force=force)
        except OSError as exc:
            # Restricted diagnostic sandboxes can deny group signals. Continue
            # stopping every owned leader; never claim descendant cleanup then.
            result["cleanup_verified"]=False
            result["cleanup_errors"].append({"pid":child.pid,"group_signal_error":str(exc)})
            if child.poll() is None:
                try:
                    child.kill() if force else child.terminate()
                except OSError as leader_error:
                    result["cleanup_errors"].append({"pid":child.pid,"leader_signal_error":str(leader_error)})
    def check_cancel() -> None:
        if cancellation is not None:
            cancellation()
        if requested_signal or (cancel_marker is not None and cancel_marker.is_file()):
            raise TrialCancelled("Independent trials cancelled by app request")
    try:
        while next_index < len(tasks) or active:
            check_cancel()
            # Reap/check before refilling slots, so an already-failed peer
            # cannot cause a pending trial to be dispatched first.
            for index,(child,log,started) in list(active.items()):
                code = child.poll()
                if code is None:
                    if timeout_seconds is not None and time.monotonic()-started > timeout_seconds:
                        raise TimeoutError(f"Independent trial exceeded {timeout_seconds:g} seconds: {records[index]['label']}")
                    continue
                child.wait()
                stop_group(child,force=True)
                log.close()
                del active[index]
                records[index].update(returncode=code,finished_unix=time.time(),state="complete" if code==0 else "failed")
                if code:
                    raise RuntimeError(f"Independent trial failed ({code}): {records[index]['label']}; see {records[index]['log_path']}")
            while next_index < len(tasks) and len(active) < workers:
                check_cancel()
                index = next_index
                task = tasks[index]
                for pin in task.get("pins") or ():
                    if _sha256(Path(pin["path"])) != pin["sha256"]:
                        raise ValueError(f"Independent trial source/input changed: {pin['path']}")
                cwd = Path(task["cwd"]).resolve()
                log_path = Path(task["log_path"]).resolve()
                log = log_path.open("w",encoding="utf-8")
                try:
                    child = subprocess.Popen(list(task["command"]),cwd=cwd,env=dict(environment),
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=os.name=="posix",
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0)
                except BaseException:
                    log.close()
                    raise
                active[index]=(child,log,time.monotonic())
                records[index].update(state="running",pid=child.pid,started_unix=time.time(),log_path=str(log_path))
                next_index += 1
                result["max_active_children"]=max(result["max_active_children"],len(active))
            if active:
                time.sleep(.05)
        result["state"]="complete"
    except BaseException as exc:
        result.update(state="cancelled" if isinstance(exc,TrialCancelled) or "cancel" in type(exc).__name__.lower() else "failed",error=str(exc))
        for child,_log,_started in active.values():
            stop_group(child)
        deadline = time.monotonic()+2
        while any(child.poll() is None for child,_log,_started in active.values()) and time.monotonic()<deadline:
            time.sleep(.05)
        for index,(child,log,_started) in active.items():
            # Signal the group even when its leader exited: a descendant may remain.
            stop_group(child,force=True)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                result["cleanup_verified"]=False
                result["cleanup_errors"].append({"pid":child.pid,"reap_error":"Owned child did not exit after cancellation"})
            log.close()
            records[index].update(state="cancelled_after_failure",returncode=child.returncode,finished_unix=time.time())
        raise
    finally:
        for signum,handler in previous_handlers.items():
            signal.signal(signum,handler)
        temporary = receipt_path.with_suffix(receipt_path.suffix+".tmp")
        temporary.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
        temporary.replace(receipt_path)
    return result
