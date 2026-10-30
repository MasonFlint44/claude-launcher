"""platforms/linux/setup.py's links in ~/.local/bin, and taking them out."""

import os
import subprocess

from claude_launcher.paths import CORE, REPO_DIR
from claude_launcher.platforms.linux.setup import COMMANDS, link_commands, unlink_commands, unlink_ours
from conftest import desktop_only


def env_for(home):
    return {"HOME": str(home), "PATH": "/usr/bin:/bin"}


def test_links_and_unlinks_the_commands(tmp_path):
    env = env_for(tmp_path)
    lines = list(link_commands(env))
    for name in COMMANDS:
        assert os.readlink(tmp_path / ".local" / "bin" / name) == str(REPO_DIR / "bin" / name)
    assert lines[-1] == f"warning: {tmp_path / '.local' / 'bin'} is not on your PATH"
    assert list(unlink_commands(env)) == [f"Removed {tmp_path / '.local' / 'bin' / name}" for name in COMMANDS]
    assert not list((tmp_path / ".local" / "bin").iterdir())


def test_on_path_no_warning(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / ".local" / "bin")}
    assert not [line for line in link_commands(env) if line.startswith("warning: ")]


def test_unlinks_links_made_through_a_symlink_to_the_repo(tmp_path):
    # Older installs linked to the repo by the path install.sh was run from,
    # which can go through a symlink.
    through = tmp_path / "code"
    through.symlink_to(REPO_DIR)
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name in COMMANDS:
        (bin_dir / name).symlink_to(through / "bin" / name)
    assert len(list(unlink_commands(env_for(tmp_path)))) == len(COMMANDS)
    assert not list(bin_dir.iterdir())


def test_leaves_other_links_and_files_alone(tmp_path):
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "claude-launcher").symlink_to("/usr/bin/env")
    (bin_dir / "claude-session-hook").write_text("mine")
    assert list(unlink_commands(env_for(tmp_path))) == []
    assert (bin_dir / "claude-launcher").is_symlink() and (bin_dir / "claude-session-hook").is_file()


def test_a_dangling_link_into_the_repo_still_goes(tmp_path):
    link = tmp_path / "gone"
    link.symlink_to(REPO_DIR / "bin" / "no-longer-here")
    assert list(unlink_ours(link)) == [f"Removed {link}"]


@desktop_only  # WSL's platform has no terminals to list
def test_terminal_list():
    done = subprocess.run([str(CORE), "terminal", "list"], capture_output=True, text=True, check=True)
    assert done.stdout.split()[:2] == ["ghostty", "kitty"] and "default" in done.stdout.split()
