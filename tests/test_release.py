"""What a release tag needs (release.yml): one version everywhere, and notes
for it in CHANGELOG.md."""

import re
import shutil
import subprocess
from pathlib import Path

from claude_launcher import __version__
from conftest import posix_only

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "scripts" / "release-notes.sh"


def test_pyproject_has_the_same_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "(.*)"$', text, re.MULTILINE).group(1) == __version__


def test_the_changelog_has_this_version_and_its_link():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d\d-\d\d$", text, re.MULTILINE)
    assert f"[{__version__}]: https://github.com/MasonFlint44/claude-launcher/releases/tag/v{__version__}" in text


def notes(version, changelog=None):
    script = NOTES
    if changelog is not None:  # the script reads the CHANGELOG.md beside its own directory
        (changelog.parent / "scripts").mkdir(exist_ok=True)
        script = shutil.copy(NOTES, changelog.parent / "scripts" / NOTES.name)
    return subprocess.run(["bash", str(script), version], capture_output=True, text=True)


@posix_only
def test_this_versions_notes():
    done = notes(__version__)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() and not done.stdout.startswith("## ")  # no version heading


@posix_only
def test_notes_stop_at_the_next_version_and_the_links(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\nIntro.\n\n## [0.2.0] - 2027-01-01\n\nNew things.\n\n"
        "## [0.1.0] - 2026-11-02\n\nThe first.\n\n"
        "[0.2.0]: https://example.com/0.2.0\n[0.1.0]: https://example.com/0.1.0\n",
        encoding="utf-8",
    )
    assert notes("0.2.0", changelog).stdout == "New things.\n"
    assert notes("0.1.0", changelog).stdout == "The first.\n"


@posix_only
def test_no_notes_fails(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [0.1.0] - 2026-11-02\n\n[0.1.0]: x\n", encoding="utf-8")
    done = notes("0.1.0", changelog)
    assert done.returncode == 1 and "no notes for 0.1.0" in done.stderr
    assert notes("0.1", changelog).returncode == 1  # "0.1" isn't "0.1.0"


def test_release_runs_every_test_first():
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    test = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/test.yml" in release and "needs: test" in release
    assert "workflow_call:" in test
    assert 'tags: ["v*"]' not in test  # a tag runs it once, through release.yml
