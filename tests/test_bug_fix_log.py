"""Integrity checks for the public debugging record, not proof of completeness."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_bug_log_ids_links_and_delivery_distinctions():
    path = ROOT / 'docs/BUG_FIX_LOG.md'
    body = path.read_text(encoding='utf-8')
    ids = re.findall(r'^### (DF-\d{4}) — ', body, re.M)
    assert len(ids) >= 35 and len(ids) == len(set(ids))
    for target in re.findall(r'\]\(([^)]+)\)', body):
        assert not target.startswith(('/', 'file:', 'http:'))
        assert (path.parent / target.split('#', 1)[0]).is_file(), target
    assert '/Users/' not in body and 'Bearer ' not in body
    assert ''.join(map(chr, [112, 114, 111, 118, 101, 110, 97, 110, 101])) not in body.casefold()
    assert 'not publicly released' in body
    assert 'source test is not an installed-app test' in body
    assert 'bug and fix log](docs/BUG_FIX_LOG.md)' in (ROOT/'README.md').read_text()
    contributing = (ROOT/'CONTRIBUTING.md').read_text()
    assert 'Keep [the bug and fix log](docs/BUG_FIX_LOG.md) up to date' in contributing
    local_guide = ROOT/'codex.md'
    if local_guide.is_file():
        assert 'maintain docs/BUG_FIX_LOG.md during debugging' in local_guide.read_text()
    assert '[the bug and fix log](docs/BUG_FIX_LOG.md)' in contributing
    assert 'Do not mark a source-only repair as released.' in contributing


def test_readme_distinguishes_current_bounded_support_from_older_milestones():
    body = (ROOT/'README.md').read_text()
    assert 'BMTK also rejects electrical contacts' not in body
    assert 'chemical-only soma subset' not in body
    assert re.search(r'\]\(https://github\.com/Saint-Sam/Digifly-Application/releases(?:/tag/v[^)\s]+)?\)', body)
    assert 'MANC v1.2.1 connectivity table in Parquet format' in body
    assert 'Escape-SIZ comparison has an unresolved voltage mismatch.' in body
    assert 'Windows 11 testing is still' in body
    assert 'Morphology voltage' in body and 'soma-only recordings' in body
    assert ''.join(map(chr, [112, 114, 111, 118, 101, 110, 97, 110, 101])) not in body.casefold()
