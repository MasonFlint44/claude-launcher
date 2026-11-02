"""Replacing a file that another process holds for a moment (Windows)."""

import os

import pytest

from claude_launcher import files


@pytest.fixture
def busy(monkeypatch):
    """os.replace refused `times` times, as Windows refuses a file in use, then done."""
    monkeypatch.setattr(files, "_PAUSE", 0)
    real = os.replace
    state = {"times": 0, "calls": 0}

    def replace(src, dst):
        state["calls"] += 1
        if state["calls"] <= state["times"]:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    return state


@pytest.fixture
def pair(tmp_path):
    (tmp_path / "new").write_text("new")
    (tmp_path / "old").write_text("old")
    return tmp_path / "new", tmp_path / "old"


def test_tried_again_while_the_file_is_in_use(busy, pair):
    busy["times"] = files._TRIES - 1
    files.replace(*pair)
    assert pair[1].read_text() == "new" and not pair[0].exists()
    assert busy["calls"] == files._TRIES


def test_gives_up_if_it_stays_in_use(busy, pair):
    busy["times"] = 1000
    with pytest.raises(PermissionError):
        files.replace(*pair)
    assert busy["calls"] == files._TRIES
    assert pair[1].read_text() == "old" and pair[0].exists()


def test_other_errors_arent_tried_again(busy, pair, tmp_path):
    with pytest.raises(FileNotFoundError):
        files.replace(tmp_path / "missing", pair[1])
    assert busy["calls"] == 1
