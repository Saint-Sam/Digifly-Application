import os
import sys
from pathlib import Path
import pytest
from digifly_app import app


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_startup_cache_guard_precedes_update_routes_without_changing_windows(monkeypatch, platform):
    from digifly_app.core import update_installer
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(sys, 'dont_write_bytecode', False)
    monkeypatch.delenv('PYTHONDONTWRITEBYTECODE', raising=False)
    monkeypatch.setattr(sys, 'argv', ['app', '--finish-update', str(Path('/fixture/request.json'))])
    def inspect(path):
        assert sys.dont_write_bytecode is (platform == 'darwin')
        assert os.environ.get('PYTHONDONTWRITEBYTECODE') == ('1' if platform == 'darwin' else None)
        return {'engineering_test': 'exit-before-startup'}
    monkeypatch.setattr(update_installer, 'read_request', inspect)
    assert app.main() == 73
