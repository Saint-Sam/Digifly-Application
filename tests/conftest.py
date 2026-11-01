"""Make unhandled Python errors in Qt callbacks fail the owning source test."""
import pytest
from digifly_app.ui.student_readiness_check import capture_qt_callback_errors


@pytest.fixture(autouse=True)
def fail_on_unhandled_qt_callback():
    with capture_qt_callback_errors() as errors:
        yield
    if errors:
        pytest.fail('Unhandled Qt callback error(s):\n'+'\n'.join(errors), pytrace=False)
