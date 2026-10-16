"""Replace the explicitly authorized Windows download only after CI gates.

Old ZIP/checksum assets are renamed to rollback names, never deleted. GitHub's
upload digest is checked before touching the old entry. Mac assets stay intact.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import urllib.parse
import zipfile

from digifly_app.packaging.audit import audit_artifact

REPO = "Saint-Sam/Digifly-Application"
TAG = "v0.1.0-alpha.1"
OLD = "Digifly-Workstation-0.1.0-alpha.1-Windows-x86_64.zip"
OLD_SHA = "cad2389f5c104b6e05b2914002a70e1478b8c9ca4552507e2ce455bbf240c851"
BACKUP = "Digifly-Workstation-0.1.0-alpha.1-Windows-x86_64-rollback-20260913.zip"
KNOWN_ROLLBACKS = {
    OLD_SHA: BACKUP,
    "51b05776ce8cebab7db43564a123e334b8532727fd1a196e2a0a3ef4d11e473c":
        "Digifly-Workstation-0.1.0-alpha.1-Windows-x86_64-rollback-20261015-interface-only.zip",
    "f0cc3bcaa9c87d02c7fbb7b92efe6b11742a5cc0a2d4aeeb75a88f54df97a36f":
        "Digifly-Workstation-0.1.0-alpha.1-Windows-x86_64-rollback-20261015-neuron.zip",
}


_TOKEN = None


def _request(endpoint, *, method="GET", payload=None, upload=None):
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = os.environ.get("GH_TOKEN")
        if not _TOKEN:
            auth = subprocess.run(["/Library/Developer/CommandLineTools/usr/bin/git", "credential", "fill"],
                input="protocol=https\nhost=github.com\npath=Saint-Sam/Digifly-Application.git\n\n",
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            fields = dict(line.split("=", 1) for line in auth.stdout.splitlines() if "=" in line)
            _TOKEN = fields.get("password") if auth.returncode == 0 else None
        if not _TOKEN:
            raise ValueError("Existing GitHub authentication is unavailable; no credential displayed")
    if not endpoint.startswith(f"repos/{REPO}/"):
        raise ValueError("Publisher refuses endpoints outside the exact app repository")
    host = "uploads.github.com" if upload else "api.github.com"
    data = upload.read_bytes() if upload else json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": "Bearer " + _TOKEN, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Digifly-reviewed-Windows-release"}
    if data is not None:
        headers["Content-Type"] = "application/octet-stream" if upload else "application/json"
    req = urllib.request.Request("https://" + host + "/" + endpoint, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read())


def gh(*args):
    """Use the standard CLI in CI, or its narrowly scoped REST equivalent locally."""
    if shutil.which("gh"):
        result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=180)
        return result.stdout
    if args[0] == "api":
        method, payload = "GET", {}
        tail = list(args[2:])
        while tail:
            option, value, *tail = tail
            if option == "--method":
                method = value
            elif option == "-f":
                key, value = value.split("=", 1)
                payload[key] = value
            else:
                raise ValueError("Unsupported release API option")
        return json.dumps(_request(args[1], method=method, payload=payload if method != "GET" else None))
    if args[:2] in (("release", "upload"), ("release", "edit")) and args[2] == TAG:
        release = _request(f"repos/{REPO}/releases/tags/{TAG}")
        if args[1] == "upload":
            file = Path(args[3])
            return json.dumps(_request(f"repos/{REPO}/releases/{release['id']}/assets?name=" + urllib.parse.quote(file.name), method="POST", upload=file))
        notes = Path(args[args.index("--notes-file") + 1]).read_text("utf-8")
        return json.dumps(_request(f"repos/{REPO}/releases/{release['id']}", method="PATCH", payload={"body": notes}))
    raise ValueError("Unsupported release operation")


def api(endpoint, *args):
    return json.loads(gh("api", endpoint, *args))


def publish(archive: Path, review=None):
    if os.environ.get("GITHUB_REPOSITORY", REPO) != REPO:
        raise ValueError("Publisher is restricted to the exact app repository")
    review = review or Path(__file__).resolve().parents[1] / "docs/WINDOWS_REDISTRIBUTION_REVIEW.json"
    if not review.is_file():
        raise ValueError("Public replacement held: exact artifact-bound notice and Windows technical review is missing.")
    # This binds the human licensing review to one exact artifact. It cannot
    # establish legal permission by itself, and no review is supplied by default.
    approval = json.loads(review.read_text("utf-8"))
    if approval.get("artifact_sha256") != hashlib.sha256(archive.read_bytes()).hexdigest() or not approval.get("permission_evidence"):
        raise ValueError("Redistribution review is absent, stale or lacks permission evidence")
    if approval.get("technical_windows_package_passed") is not True:
        raise ValueError("Actual Windows package review is missing")
    previous_sha = approval.get("previous_alias_sha256", OLD_SHA)
    if previous_sha not in KNOWN_ROLLBACKS:
        raise ValueError("The previous download is not one of this task's checksum-pinned releases")
    backup_name = KNOWN_ROLLBACKS[previous_sha]
    report = audit_artifact(archive)
    if not report.ok:
        raise ValueError("Actual Windows archive audit failed: " + json.dumps(report.to_dict()))
    with zipfile.ZipFile(archive) as zipped:
        marker = json.loads(zipped.read("digifly_windows_package.json"))
        if set(zipped.namelist()) != {*marker["files"], "digifly_windows_package.json"} or zipped.testzip():
            raise ValueError("Actual Windows ZIP members/CRC differ")
        for name, record in marker["files"].items():
            data = zipped.read(name)
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("Actual Windows ZIP payload changed: " + name)
        build = json.loads(zipped.read("digifly_build.json"))
        notices = json.loads(zipped.read("THIRD_PARTY_COMPONENTS.json"))
        includes_neuron = "installers/neuron/DIGIFLY_NEURON_RUNTIME.json" in marker["files"]
        from digifly_app.core.bundled_pair_graph import PAIR_PATH, PAIR_NOTICE, NOTICE
        includes_pair = PAIR_PATH in marker["files"]
        if includes_pair and (json.loads(zipped.read(PAIR_NOTICE)) != NOTICE
                or approval.get("included_pair_windows_ui_passed") is not True):
            raise ValueError("Included connectivity requires exact data notice and actual Windows app import review")
        if includes_neuron:
            runtime = json.loads(zipped.read("installers/neuron/DIGIFLY_NEURON_RUNTIME.json"))
            from digifly_app.core.windows_neuron_bundle import INSTALLER_SHA256, RUNTIME_ID
            if (runtime.get("runtime_id") != RUNTIME_ID or runtime.get("neuron_installer_sha256") != INSTALLER_SHA256
                    or approval.get("neuron_windows_ui_install_run_passed") is not True):
                raise ValueError("Included NEURON requires actual source-bound packaged install/Run review")
            for name, record in runtime["artifacts"].items():
                if marker["files"].get("installers/neuron/" + name) != record:
                    raise ValueError("Included NEURON artifact differs from checked package")
        if (build.get("source_commit") != approval.get("ci_source_commit")
                or notices.get("schema") != "digifly-windows-notice-inventory-v1"
                or "mechanisms/augustin_2019/NOTICE.md" not in marker["files"]):
            raise ValueError("Compiled source identity or source-specific notices are missing")
    run = api(f"repos/{REPO}/actions/runs/{int(approval['ci_run_id'])}")
    if run.get("conclusion") != "success" or run.get("head_sha") != approval.get("ci_source_commit"):
        raise ValueError("Windows CI run is not successful or differs from reviewed source")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    receipt = {"repository": REPO, "tag": TAG, "sha256": checksum, "versioned_asset": archive.name}
    release = api(f"repos/{REPO}/releases/tags/{TAG}")
    before = {a["name"]: a for a in release["assets"]}
    mac_before = {a["id"]: a.get("digest") for a in release["assets"] if "macOS" in a["name"]}

    def upload(file):
        existing = {a["name"]: a for a in api(f"repos/{REPO}/releases/{release['id']}")["assets"]}.get(file.name)
        wanted = "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()
        if existing:
            if existing.get("digest") != wanted:
                raise ValueError("Release asset collision; refusing to clobber " + file.name)
        else:
            gh("release", "upload", TAG, str(file), "--repo", REPO)
        actual = next(a for a in api(f"repos/{REPO}/releases/{release['id']}")["assets"] if a["name"] == file.name)
        if actual.get("digest") != wanted:
            raise ValueError("GitHub upload checksum mismatch: " + file.name)
        return actual

    current = upload(archive)
    upload(archive.with_suffix(archive.suffix + ".sha256"))
    # Only now, with the new versioned download durable, preserve the old alias.
    if OLD in before and before[OLD].get("digest") == "sha256:" + previous_sha:
        if backup_name in before:
            raise ValueError("Ambiguous old/backup state; refusing destructive replacement")
        api(f"repos/{REPO}/releases/assets/{before[OLD]['id']}", "--method", "PATCH", "-f", "name=" + backup_name)
        if OLD + ".sha256" in before:
            api(f"repos/{REPO}/releases/assets/{before[OLD + '.sha256']['id']}", "--method", "PATCH", "-f", "name=" + backup_name + ".original-sha256.txt")
    elif OLD in before and before[OLD].get("digest") != "sha256:" + checksum:
        raise ValueError("Old Windows alias changed unexpectedly; preserved without overwrite")
    latest = {a["name"]: a for a in api(f"repos/{REPO}/releases/{release['id']}")["assets"]}
    if backup_name not in latest or latest[backup_name].get("digest") != "sha256:" + previous_sha:
        raise ValueError("Checksum-pinned old Windows rollback asset is not preserved")
    alias_dir = archive.parent / "windows-alias"
    alias_dir.mkdir(exist_ok=True)
    alias = alias_dir / OLD
    shutil.copyfile(archive, alias)
    alias.with_suffix(alias.suffix + ".sha256").write_text(f"{checksum}  {OLD}\n", "ascii")
    upload(alias)
    upload(alias.with_suffix(alias.suffix + ".sha256"))
    body = release.get("body") or ""
    if "<!-- current-windows-start -->" in body:
        body = body.split("<!-- current-windows-end -->", 1)[-1].lstrip()
    note = ("<!-- current-windows-start -->\n## Current Windows tester — October 16, 2026\n\n"
            f"Download [{archive.name}]({current['browser_download_url']}). "
            "Extract All and open **Digifly Workstation.exe**. The old Windows link now serves this build; "
            "previous checksum-pinned builds are preserved as rollback. Mac downloads are unchanged.\n\n"
            f"[Exact app source](https://github.com/{REPO}/tree/{approval['ci_source_commit']}) · "
            f"[Windows build and checks](https://github.com/{REPO}/actions/runs/{approval['ci_run_id']})\n\n"
            + ("Includes offline NEURON9.0.2 + private Python3.12.10/NumPy/Matplotlib/DuckDB. "
               "In Workspace runtime setup choose **Install included NEURON**, then **Use selected runtimes**. "
               "Actual extracted app private installation and bounded built-in-HH Run passed on WindowsServer2022 CI; "
               "no registry/globalPATH/other-installation changes. No BioNet/NetPyNE/Arbor runtime included. "
               "Windows11 clean-machine, custom-mechanism compiler, MPI and cross-OS scientific equivalence remain unverified. "
               if includes_neuron else "Native Windows UI/Parquet engineering checks passed on WindowsServer2022 CI. "
               "Windows11 clean-machine/native NEURON/simulator equivalence remain unverified. ")
            + "Unsigned alpha; user-selected experiments remain unverified. Arbor deferred. "
            + ("Includes the user-supplied MANC pair Parquet (exact release/completeness/weight units unverified), not SWCs or located synapses. In Data Library, use the included file with the installed private Python as reader. "
               if includes_pair else "No dataset included; transfer your Parquet separately. ")
            + "Downloads and SWC imports now join one library per exact dataset/server/version, with visible export_swc family/type/body-ID folders and combined-name search. No SWC corpus or token included.\n<!-- current-windows-end -->\n\n")
    notes = archive.parent / "windows-release-notes.md"
    notes.write_text(note + body, "utf-8")
    gh("release", "edit", TAG, "--repo", REPO, "--notes-file", str(notes))
    final = api(f"repos/{REPO}/releases/{release['id']}")
    if {a["id"]: a.get("digest") for a in final["assets"] if "macOS" in a["name"]} != mac_before:
        raise ValueError("Mac asset preservation check failed")
    receipt.update(download_url=current["browser_download_url"], old_backup_sha256=previous_sha,
                   old_backup_asset_id=latest[backup_name]["id"], mac_assets_unchanged=True,
                   included_neuron=includes_neuron)
    (archive.parent / "windows-publication-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", "utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    p.add_argument("--review", type=Path)
    args = p.parse_args()
    publish(args.archive, args.review)
