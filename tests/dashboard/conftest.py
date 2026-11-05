"""Dashboard test fixtures."""

import pytest

from tests.dashboard.session_builder import make_full_session as _make_full_session


@pytest.fixture
def make_full_session():
    """The builder function itself; call it with keyword options."""
    return _make_full_session
