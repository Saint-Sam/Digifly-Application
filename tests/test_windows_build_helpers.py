import re
from pathlib import Path
import pytest
from scripts.stage_windows_source import inputs, verify_build_helpers


def test_every_actual_build_python_helper_is_in_normal_inputs():
    root=Path(__file__).resolve().parents[1]
    script=(root/'scripts/build_windows.ps1').read_text()
    required={p.replace('\\','/') for p in re.findall(r'& \$python "(scripts\\[^"\s]+\.py)"',script)}
    declared={p.relative_to(root).as_posix() for p in inputs(root)}
    assert 'scripts/stage_windows_graphics.py' in required
    assert required <= declared, sorted(required-declared)


def test_valid_helper_is_checked(tmp_path):
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/build_windows.ps1').write_text('& $python "scripts\\example.py"')
    (tmp_path/'scripts/example.py').write_text('pass\n')
    assert verify_build_helpers(tmp_path)==['scripts/example.py']


def test_missing_helper_refuses_before_compiler(tmp_path):
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/build_windows.ps1').write_text('& $python "scripts\\missing.py"')
    with pytest.raises(ValueError,match='missing.py'):verify_build_helpers(tmp_path)


def test_unsafe_helper_refuses(tmp_path):
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/build_windows.ps1').write_text('& $python "scripts\\..\\outside.py"')
    with pytest.raises(ValueError,match='unsafe'):verify_build_helpers(tmp_path)
