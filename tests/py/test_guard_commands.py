"""hermes_crew.guard.commands: the EXTRACTORS table (rsync and cmake --install prove it extends)."""

from __future__ import annotations

import os

import pytest
from hermes_crew import guard


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    for d in ("src", "dist/lib", "docs"):
        (root / d).mkdir(parents=True)
    for f in ("src/app.py", "dist/lib/core.so", "docs/a.md"):
        (root / f).write_text("x\n")
    return root


def targets(proj, cmd):
    return [(os.path.relpath(t.path, proj), t.op, t.recursive) for t in guard.command_targets(cmd, str(proj))]


def test_table_has_one_entry_per_command():
    assert {"tee", "sed", "perl", "rm", "mv", "cp", "git", "rsync", "cmake"} <= set(guard.EXTRACTORS)
    assert all(callable(f) for f in guard.EXTRACTORS.values())


def test_rsync(proj):
    assert targets(proj, "rsync -av /tmp/app.py src/app.py") == [("src/app.py", "write", False)]
    assert targets(proj, "rsync -a /tmp/app.py src") == [("src/app.py", "write", False)]  # into a directory
    assert ("docs", "delete", True) in targets(proj, "rsync -a --delete /tmp/site/ docs/")
    assert ("src/app.py", "move", False) in targets(proj, "rsync --remove-source-files src/app.py /tmp/x")
    assert targets(proj, "rsync -e ssh src/app.py host:/srv/app.py") == []  # remote destination
    assert targets(proj, "rsync src/") == []  # listing only
    # a directory source: everything below where it lands (trailing slash = its contents)
    assert targets(proj, "rsync -a src/ dist/") == [("dist", "write", True)]
    assert targets(proj, "rsync -a src dist/") == [("dist/src", "write", True)]
    assert targets(proj, "rsync -a src dist") == [("dist/src", "write", True)]
    # options with a separate argument are not operands
    assert targets(proj, "rsync -a --exclude '*.o' --link-dest /old src/ dist/") == [("dist", "write", True)]
    assert targets(proj, "rsync -a --files-from list.txt --chmod u+w /tmp/x.py src/") == [("src/x.py", "write", False)]


def test_cmake_install(proj):
    assert targets(proj, "cmake --install build --prefix dist") == [("dist", "write", True)]
    assert targets(proj, "cmake --install build --prefix=dist") == [("dist", "write", True)]
    assert targets(proj, "cmake --install build") == []  # cached prefix: unknown
    assert targets(proj, "cmake --build build --prefix dist") == []  # not an install


def test_new_extractors_block_through_evaluate(proj, tmp_path):
    (tmp_path / "site").mkdir()
    (tmp_path / "lib").mkdir()
    owners = {"src/app.py": "Backend", "dist/lib/core.so": "Build", "docs/a.md": "Writer"}

    def ev(cmd):
        return guard.evaluate("terminal", {"command": cmd}, team_dir=str(proj), role="Frontend", owners=owners)

    assert ev("rsync -a /tmp/app.py src/").blocked
    assert ev("rsync -a --delete /tmp/site/ docs/").blocked
    assert ev("cmake --install build --prefix dist").blocked  # writes below dist/: Build's file
    assert not ev("cmake --install build --prefix /opt/x").blocked
    assert not ev("rsync -a src/app.py /tmp/backup/").blocked
    assert ev(f"rsync -a {tmp_path}/site/ dist/").blocked  # contents land on dist/lib/core.so (Build's)
    assert ev(f"rsync -a {tmp_path}/lib dist/").blocked  # dist/lib/... below
    assert not ev(f"rsync -a --exclude dist {tmp_path}/site/ docs/new/").blocked  # "dist" is the exclude pattern


def test_dotdot_after_symlink_resolves_physically(proj, tmp_path):
    """T64: `<symlink>/..` is the symlink target's parent (as the OS resolves it), not lexically collapsed."""
    (proj / "src/deeper/inner").mkdir(parents=True)
    (proj / "src/deeper/owned.py").write_text("x\n")
    (proj / "deep").symlink_to(proj / "src/deeper/inner")  # deep/../owned.py == src/deeper/owned.py
    (tmp_path / "outside/sub").mkdir(parents=True)
    (tmp_path / "outside/docs").mkdir()
    (proj / "link_out").symlink_to(tmp_path / "outside/sub")  # link_out/.. == <tmp>/outside
    (proj / "docs/a.md").write_text("x\n")
    owners = {"src/deeper/owned.py": "Backend", "docs/a.md": "Writer"}

    def ev(tool, **args):
        return guard.evaluate(tool, args, team_dir=str(proj), role="Frontend", owners=owners, cwd=str(proj))

    assert ev("write_file", path="deep/../owned.py").blocked  # was a bypass
    assert ev("terminal", command="echo x > deep/../owned.py").blocked
    assert not ev("write_file", path="link_out/../docs/a.md").blocked  # really outside the team dir
    assert not ev("terminal", command="rm -r link_out/../docs").blocked
    assert ev("write_file", path="src/./x/../../docs/a.md").blocked  # plain `..` still works
