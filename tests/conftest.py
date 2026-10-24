import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "Downloads"
    root.mkdir()
    return root
