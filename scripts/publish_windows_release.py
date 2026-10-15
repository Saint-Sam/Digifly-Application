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

REPO = "Saint-Sam/Digifly-Application"
TAG = "v0.1.0-alpha.1"
OLD = "Digifly-Workstation-0.1.0-alpha.1-Windows-x86_64.zip"
OLD_SHA = "cad2389f5c104b6e05b2914002a70e1478b8c9ca4552507e2ce455bbf240c851"
BACKUP = "Digifly-Workstation-0.1.0-alpha.1-Windows-x86_64-rollback-20260913.zip"


def gh(*args):
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=180)
    return result.stdout


def api(endpoint, *args):
    return json.loads(gh("api", endpoint, *args))


def publish(archive: Path):
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("Publisher is restricted to this repository's authorized CI")
    raise ValueError("Public replacement held: Augustin/ModelDB redistribution permission and complete bundled-dependency notices remain unresolved. Build-only artifacts may be evaluated; do not bypass this release gate.")
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
    if OLD in before and before[OLD].get("digest") == "sha256:" + OLD_SHA:
        if BACKUP in before:
            raise ValueError("Ambiguous old/backup state; refusing destructive replacement")
        api(f"repos/{REPO}/releases/assets/{before[OLD]['id']}", "--method", "PATCH", "-f", "name=" + BACKUP)
        if OLD + ".sha256" in before:
            api(f"repos/{REPO}/releases/assets/{before[OLD + '.sha256']['id']}", "--method", "PATCH", "-f", "name=" + BACKUP + ".original-sha256.txt")
    elif OLD in before and before[OLD].get("digest") != "sha256:" + checksum:
        raise ValueError("Old Windows alias changed unexpectedly; preserved without overwrite")
    latest = {a["name"]: a for a in api(f"repos/{REPO}/releases/{release['id']}")["assets"]}
    if BACKUP not in latest or latest[BACKUP].get("digest") != "sha256:" + OLD_SHA:
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
    note = ("<!-- current-windows-start -->\n## Current Windows tester — October 15, 2026\n\n"
            f"Download [{archive.name}]({current['browser_download_url']}). "
            "Extract All and open **Digifly Workstation.exe**. The old Windows link now serves this build; "
            "the checksum-pinned September ZIP is preserved as rollback. Mac downloads are unchanged.\n\n"
            "Native Windows UI/Parquet engineering checks passed on Windows Server 2022 CI. "
            "Unsigned alpha; Windows 11 clean-machine, native NEURON installer/compiler, simulator "
            "equivalence and user-selected experiment testing remain unverified. Arbor deferred. "
            "No dataset/SWC/token included; transfer your Parquet separately.\n<!-- current-windows-end -->\n\n")
    notes = archive.parent / "windows-release-notes.md"
    notes.write_text(note + body, "utf-8")
    gh("release", "edit", TAG, "--repo", REPO, "--notes-file", str(notes))
    final = api(f"repos/{REPO}/releases/{release['id']}")
    if {a["id"]: a.get("digest") for a in final["assets"] if "macOS" in a["name"]} != mac_before:
        raise ValueError("Mac asset preservation check failed")
    receipt.update(download_url=current["browser_download_url"], old_backup_sha256=OLD_SHA,
                   old_backup_asset_id=latest[BACKUP]["id"], mac_assets_unchanged=True)
    (archive.parent / "windows-publication-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", "utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    publish(p.parse_args().archive)
