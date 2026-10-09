"""hermes_crew.guard: pure ownership policy for pre_tool_call."""
from __future__ import annotations

import os
import time

import pytest

from hermes_crew import guard


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / ".team").mkdir()
    for f in ("src/app.py", "src/util.py", "README.md", "docs/a.md"):
        (root / f).write_text("x\n")
    return root


OWNERS = {"src/app.py": "Backend", "README.md": "Writer", "docs/a.md": "writer"}


def ev(proj, tool, args, role="Frontend", owners=OWNERS, cwd=None, auto_own=True):
    return guard.evaluate(tool, args, team_dir=str(proj), role=role, owners=owners,
                          cwd=str(cwd or proj), auto_own=auto_own)


# ---------------------------------------------------------------- paths
def test_team_relative(proj, tmp_path):
    assert guard.team_relative(str(proj / "src/app.py"), str(proj)) == "src/app.py"
    assert guard.team_relative(str(proj / "src/../README.md"), str(proj)) == "README.md"
    assert guard.team_relative(str(proj / ".team/board.log"), str(proj)) is None
    assert guard.team_relative(str(proj / ".team"), str(proj)) is None
    assert guard.team_relative(str(proj), str(proj)) is None
    assert guard.team_relative(str(tmp_path / "elsewhere.py"), str(proj)) is None
    assert guard.team_relative(str(tmp_path / "proj2/x.py"), str(proj)) is None   # prefix trap
    assert guard.team_relative(str(proj / "new/dir/file.py"), str(proj)) == "new/dir/file.py"


def test_team_relative_resolves_symlinks(proj, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(proj)
    assert guard.team_relative(str(link / "src/app.py"), str(proj)) == "src/app.py"


# ---------------------------------------------------------------- write_file / patch
def test_write_file_owned_by_other_is_blocked(proj):
    d = ev(proj, "write_file", {"path": "src/app.py", "content": "y"})
    assert d.blocked
    assert d.message == ('hermes-crew: src/app.py is owned by Backend. Send the owner the exact change: '
                         'hermes-crew send Backend "..." (or ask lead to transfer it).')
    assert d.hook_result() == {"action": "block", "message": d.message}


def test_write_file_absolute_and_dotted_paths(proj):
    assert ev(proj, "write_file", {"path": str(proj / "src/app.py")}).blocked
    assert ev(proj, "write_file", {"path": "./src/../src/app.py"}).blocked
    assert ev(proj, "write_file", {"path": "app.py"}, cwd=proj / "src").blocked


def test_owner_compared_case_insensitively(proj):
    d = ev(proj, "write_file", {"path": "src/app.py"}, role="backend")
    assert not d.blocked and d.claim == []
    assert not ev(proj, "write_file", {"path": "docs/a.md"}, role="Writer").blocked


def test_unowned_file_is_auto_owned(proj):
    d = ev(proj, "write_file", {"path": "src/util.py"})
    assert not d.blocked and d.claim == ["src/util.py"] and d.hook_result() is None
    d = ev(proj, "write_file", {"path": "new/thing.py"})
    assert d.claim == ["new/thing.py"]


def test_auto_own_disabled(proj):
    d = ev(proj, "write_file", {"path": "src/util.py"}, auto_own=False)
    assert not d.blocked and d.claim == []


def test_paths_outside_team_and_team_dir_are_ignored(proj, tmp_path):
    for p in (str(tmp_path / "other.py"), "/etc/hosts", ".team/briefs/Frontend.md", "../outside.txt"):
        d = ev(proj, "write_file", {"path": p})
        assert not d.blocked and d.claim == [], p


def test_patch_replace_mode(proj):
    assert ev(proj, "patch", {"path": "README.md", "old_string": "a", "new_string": "b"}).blocked
    d = ev(proj, "patch", {"path": "src/util.py", "old_string": "a", "new_string": "b"})
    assert d.claim == ["src/util.py"]


def test_patch_v4a_mode(proj):
    text = ("*** Begin Patch\n*** Update File: src/util.py\n@@\n-x\n+y\n"
            "*** Add File: src/new.py\n+hello\n*** End Patch")
    d = ev(proj, "patch", {"mode": "patch", "patch": text})
    assert not d.blocked and d.claim == ["src/util.py", "src/new.py"]
    text2 = "*** Begin Patch\n*** Delete File: src/app.py\n*** End Patch"
    assert ev(proj, "patch", {"mode": "patch", "patch": text2}).blocked
    text3 = "*** Begin Patch\n*** Update File: src/util.py\n*** Move to: README.md\n*** End Patch"
    d = ev(proj, "patch", {"patch": text3})
    assert d.blocked and "README.md is owned by Writer" in d.message


def test_delete_never_auto_owns(proj):
    text = "*** Begin Patch\n*** Delete File: src/util.py\n*** End Patch"
    assert ev(proj, "patch", {"mode": "patch", "patch": text}).claim == []


def test_other_tools_and_bad_args_are_allowed(proj):
    for tool, args in (("read_file", {"path": "src/app.py"}), ("web_search", {"query": "x"}),
                       ("write_file", {}), ("write_file", {"path": 7}), ("write_file", None),
                       ("patch", {"mode": "patch", "patch": None}), ("terminal", {"command": None}),
                       ("terminal", "not a dict"), ("write_file", ["list"])):
        d = ev(proj, tool, args)
        assert not d.blocked and d.claim == [], (tool, args)


def test_evaluate_never_raises(proj, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(guard, "file_tool_targets", boom)
    d = ev(proj, "write_file", {"path": "src/app.py"})
    assert not d.blocked


def test_multiple_conflicts_listed(proj):
    d = ev(proj, "terminal", {"command": "rm src/app.py README.md"})
    assert d.blocked and "src/app.py is owned by Backend" in d.message
    assert "Also owned by others: README.md (Writer)" in d.message


# ---------------------------------------------------------------- terminal
BLOCKED_COMMANDS = [
    "echo hi > src/app.py",
    "echo hi >> src/app.py",
    "echo hi >| src/app.py",
    "make 2> src/app.py",
    "make &> src/app.py",
    "make &>> src/app.py",
    "echo hi>src/app.py",
    "cat x | tee src/app.py",
    "cat x | tee -a src/app.py >/dev/null",
    "sed -i 's/a/b/' src/app.py",
    "sed -i.bak -e 's/a/b/' src/app.py",
    "sed -E -i 's/a/b/' src/app.py",
    "sed -ni 's/a/b/p' src/app.py",
    "sed --in-place=.orig 's/a/b/' src/app.py",
    "sed -i -e s/a/b/ -e s/c/d/ src/util.py src/app.py",
    "perl -pi -e 's/a/b/' src/app.py",
    "perl -i.bak -pe 's/a/b/' src/app.py",
    "mv src/app.py src/old.py",
    "mv src/util.py src/app.py",
    "mv src/util.py README.md",
    "cp src/util.py src/app.py",
    "cp -f /tmp/x.py src/app.py",
    "cp README.md.new README.md",
    "mv -t src/ /tmp/app.py",
    "cp /tmp/app.py src",
    "cp /tmp/app.py src/",
    "rm src/app.py",
    "rm -f -- src/app.py",
    "rm -rf src",
    "rm -r ./docs/",
    "unlink README.md",
    "truncate -s 0 src/app.py",
    "dd if=/dev/zero of=src/app.py bs=1 count=0",
    "git mv src/app.py src/x.py",
    "git rm src/app.py",
    "sudo tee src/app.py < /dev/null",
    "env FOO=1 sed -i s/a/b/ src/app.py",
    "FOO=1 BAR=2 rm src/app.py",
    "cd src && echo x > app.py",
    "cd src; sed -i s/a/b/ app.py",
    "true && echo x > README.md || echo fail",
    "echo 'quoted ; stuff' > 'src/app.py'",
    'echo "a > b" > "README.md"',
    "rm src/*.py",
    "rm src/app.p?",
    "cat > src/app.py <<'EOF'\nprint('hi')\nEOF",
    "echo start\necho x > README.md",
    "nohup time rm README.md",
]

ALLOWED_COMMANDS = [
    "ls -la src",
    "cat src/app.py",
    "grep '>' src/app.py",
    'grep ">" src/app.py',
    "grep -n foo src/app.py | head",
    "echo hi > src/util.py",             # unowned (terminal never auto-owns)
    "echo hi > /tmp/out.txt",
    "make 2>&1 | tee build.log",
    "cmd >&2",
    "cmd 2>&1 >/dev/null",
    "sed 's/a/b/' src/app.py > /tmp/new",
    "sed -n 1,5p src/app.py",
    "perl -pe 's/a/b/' src/app.py",
    "cp src/app.py /tmp/backup.py",
    "cp src/app.py src/copy.py",
    "rm -rf build node_modules",
    "rm -r src/sub",                     # dir without owned files
    "echo $X > $TARGET",
    "x=$(rm src/app.py)",                # sub-command: documented limitation
    "python -c \"open('src/app.py','w')\"",
    "cat <<EOF > notes.txt\nrm src/app.py\necho > README.md\nEOF",
    "echo hi > .team/scratch",
    "rm 'src/*.py'",                     # quoted glob: literal name, not owned
    "# rm src/app.py",
    "echo '",                            # unbalanced quote
    "",
    "cd /tmp && rm app.py",
    "cd $SOMEWHERE && rm app.py",        # unknown cwd → relative paths unresolved
    "truncate --size 10 src/util.py",
]


@pytest.mark.parametrize("cmd", BLOCKED_COMMANDS)
def test_terminal_blocked(proj, cmd):
    d = ev(proj, "terminal", {"command": cmd, "timeout": 60})
    assert d.blocked, cmd
    assert d.message.startswith("hermes-crew: ") and "is owned by" in d.message
    assert d.claim == []


@pytest.mark.parametrize("cmd", ALLOWED_COMMANDS)
def test_terminal_allowed(proj, cmd):
    d = ev(proj, "terminal", {"command": cmd})
    assert not d.blocked, (cmd, d.message)
    assert d.claim == []


def test_terminal_owner_may_edit_own_file(proj):
    assert not ev(proj, "terminal", {"command": "sed -i s/a/b/ src/app.py"}, role="Backend").blocked
    assert not ev(proj, "terminal", {"command": "rm -rf docs"}, role="Writer").blocked


def test_terminal_workdir_arg(proj):
    assert ev(proj, "terminal", {"command": "rm app.py", "workdir": str(proj / "src")}).blocked
    assert not ev(proj, "terminal", {"command": "rm app.py", "workdir": "/tmp"}).blocked


def test_terminal_without_owners_is_fast_path(proj):
    assert not ev(proj, "terminal", {"command": "rm -rf src"}, owners={}).blocked


def test_lead_is_blocked_too(proj):
    d = ev(proj, "write_file", {"path": "src/app.py"}, role="lead")
    assert d.blocked and "owned by Backend" in d.message


# ---------------------------------------------------------------- tokenizer details
def test_tokenizer_quotes_and_ops():
    words = guard.tokenize("echo \"a > b\" 'c;d' e\\ f 2>err >&2 && x")
    texts = [(w.text, w.op) for w in words]
    assert texts == [("echo", False), ("a > b", False), ("c;d", False), ("e f", False),
                     (">", True), ("err", False), (">&", True), ("2", False), ("&&", True), ("x", False)]


def test_tokenizer_heredoc_body_skipped():
    words = guard.tokenize("cat <<-EOF > f\n\tline > g\n\tEOF\necho done")
    assert [w.text for w in words] == ["cat", "<<-", "EOF", ">", "f", "\n", "echo", "done"]


def test_command_targets_ops(proj):
    t = guard.command_targets("mv a b; rm -r c; tee d", str(proj))
    got = [(os.path.relpath(x.path, proj), x.op, x.recursive) for x in t]
    assert ("b", "write", False) in got and ("a", "move", False) in got
    assert ("c", "delete", True) in got and ("d", "write", False) in got


# ---------------------------------------------------------------- performance
def test_guard_is_fast(proj):
    owners = {f"pkg/mod{i}/file{j}.py": "Backend" for i in range(50) for j in range(40)}
    owners.update(OWNERS)
    cmd = "cd src && sed -i 's/a/b/' util.py && cat x | tee -a ../docs/b.md > /dev/null; rm -rf ../pkg/mod3"
    t0 = time.perf_counter()
    for _ in range(200):
        d = ev(proj, "terminal", {"command": cmd}, owners=owners)
        ev(proj, "write_file", {"path": "src/util.py"}, owners=owners)
    per_call = (time.perf_counter() - t0) / 400
    assert d.blocked  # rm -rf of a dir containing Backend's files
    assert per_call < 0.05, per_call
