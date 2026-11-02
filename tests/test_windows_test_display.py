import pytest
from scripts.prepare_windows_test_display import require_screen_space,require_disposable_windows_ci

@pytest.mark.parametrize('width,height',[(1120,800),(1280,1024),(1920,1040)])
def test_native_desktop_accepts_room_for_the_real_window(width,height):
    require_screen_space(width,height)

@pytest.mark.parametrize('width,height',[(1024,728),(1119,1040),(1920,799)])
def test_native_desktop_refuses_the_observed_offscreen_control_case(width,height):
    with pytest.raises(ValueError,match='too small'):require_screen_space(width,height)

def test_display_setup_never_changes_a_user_desktop(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    with pytest.raises(ValueError,match='disposable Windows'):require_disposable_windows_ci()
