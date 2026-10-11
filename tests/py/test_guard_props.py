"""Property-based safety tests for the ownership guard (public API: guard.evaluate / guard.Decision).

The guard is a guard rail, so the dangerous failure modes are:

* it raises (Hermes turns a raising ``pre_tool_call`` callback into a block of the tool),
* it blocks something it must never touch (a path outside the team dir, anything under ``.team/``),
* it silently *stops* blocking (an internal error is swallowed as "allow").

The last one is why every property has a positive control: owned files must still be blocked when the
path is written with ``..``, ``.``, doubled slashes, symlinks, unicode and shell quoting.

The oracle is ``os.path.realpath`` on the real file system; it shares no code with the guard.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import pytest
from hermes_crew import guard
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

ME, OTHER = "Mallory", "Owner"

PROFILE = settings(
    max_examples=300,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# ---------------------------------------------------------------- file-system fixture
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """base/
    proj/            team dir      .team/  src/  docs/  alias_src -> src  link_out -> ../outside
                                   link_team -> .team  link_rel -> src/../docs  deep -> src/deeper/inner
    outside/         not part of the team  (back_in -> ../proj/src)
    """
    base = Path(os.path.realpath(tmp_path_factory.mktemp("guardworld")))
    root, out = base / "proj", base / "outside"
    for d in (root / ".team", root / "src" / "deeper" / "inner", root / "docs", out):
        d.mkdir(parents=True)
    (root / "src" / "a.py").write_text("x")
    (out / "o.txt").write_text("x")
    os.symlink(root / "src", root / "alias_src")
    os.symlink("../outside", root / "link_out")
    os.symlink(".team", root / "link_team")
    os.symlink("src/../docs", root / "link_rel")
    os.symlink("../proj/src", out / "back_in")
    os.symlink("src/deeper/inner", root / "deep")
    return {"base": base, "root": root, "out": out}


def classify(world, path: str, cwd: Path) -> tuple[str, str | None]:
    """Independent oracle: ('outside'|'team'|'inside', team-relative path) of the file an OS write to
    ``path`` (relative to the process cwd ``cwd``) would really touch."""
    real = os.path.realpath(os.path.join(cwd, path))
    root = str(world["root"])
    if real != root and not real.startswith(root + os.sep):
        return "outside", None
    rel = os.path.relpath(real, root)
    if rel == ".":
        return "team", None
    if rel == ".team" or rel.startswith(".team" + os.sep):
        return "team", None
    return "inside", rel


# ---------------------------------------------------------------- strategies
_NAME_CHARS = st.sampled_from([*list("abcxyz019_-. "), "é", "ß", "日", "😀", "Ω"])
names = st.text(_NAME_CHARS, min_size=1, max_size=6).filter(
    lambda s: s not in (".", "..") and s.strip() != "" and not s.startswith("-")  # "-x" would be an option, not a path
)
fixed_segments = st.sampled_from(
    [
        "src",
        "docs",
        ".team",
        "alias_src",
        "link_out",
        "link_team",
        "link_rel",
        "deep",
        "deeper",
        "inner",
        "outside",
        "proj",
        "back_in",
        "..",
        ".",
    ]
)
# "<symlink>/.." is where lexical (normpath) and physical (OS) resolution disagree: make it common
symlink_dotdot = st.sampled_from(
    ["deep/..", "link_out/..", "link_rel/..", "alias_src/..", "link_team/..", "back_in/.."]
)
segments = st.one_of(fixed_segments, fixed_segments, symlink_dotdot, symlink_dotdot, names)
# last component: random, or a name that is in all_owned() / an owned file
last_names = st.one_of(names, st.sampled_from(["docs", "src", "a.py", "owned.py", "o.txt", "board.log"]))
paths = st.builds(
    lambda segs, last, sep, lead: lead + sep.join([*segs, last]),
    st.lists(segments, max_size=5),
    last_names,
    st.sampled_from(["/", "//", "/./"]),
    st.sampled_from(["", "./"]),
)
cwd_names = st.sampled_from(["root", "src", "docs", "out", "base"])


def cwd_of(world, key: str) -> Path:
    return {
        "root": world["root"],
        "src": world["root"] / "src",
        "docs": world["root"] / "docs",
        "out": world["out"],
        "base": world["base"],
    }[key]


def shell_forms(p: str):
    q = shlex.quote(p)
    return [
        f"echo hi > {q}",
        f"echo hi >> {q}",
        f"echo hi >{q}",
        f"echo hi | tee {q}",
        f"echo hi | tee -a {q}",
        f"sed -i s/a/b/ {q}",
        f"rm -f {q}",
        f"cp /etc/hostname {q}",
        f"mv /etc/hostname {q}",
        f"sudo -n sh -c true; echo > {q}",
        f'echo > "{p}"' if not any(c in p for c in '"$`\\!') else f"echo > {q}",
    ]


def calls_for(p: str, cwd: Path):
    """Every way of expressing 'write to p' that the guard claims to understand."""
    yield "write_file", {"path": p, "content": "x"}
    yield "patch", {"path": p, "old_string": "a", "new_string": "b"}
    if p == p.strip():  # V4A headers are whitespace-trimmed by Hermes' own patch parser as well
        yield "patch", {"mode": "patch", "patch": f"*** Update File: {p}\n@@\n-a\n+b\n"}
        yield "patch", {"mode": "patch", "patch": f"*** Add File: {p}\n+b\n"}
    for cmd in shell_forms(p):
        yield "terminal", {"command": cmd, "workdir": str(cwd)}


def ev(world, tool, args, cwd, owners, role=ME, auto_own=True):
    return guard.evaluate(
        tool, args, team_dir=str(world["root"]), role=role, owners=owners, cwd=str(cwd), auto_own=auto_own
    )


def all_owned(world) -> dict[str, str]:
    """Owners map that makes *every* plausible inside file owned by someone else, including bogus keys."""
    keys = ["src/a.py", "docs", "src", ".team/board.log", ".team", "../outside/o.txt", "o.txt", "/etc/hostname"]
    return dict.fromkeys(keys, OTHER)


# ---------------------------------------------------------------- 1. never raises
junk = st.recursive(
    st.one_of(
        st.none(), st.booleans(), st.integers(), st.floats(allow_nan=True), st.text(max_size=20), st.binary(max_size=8)
    ),
    lambda c: st.one_of(st.lists(c, max_size=3), st.dictionaries(st.text(max_size=6), c, max_size=3)),
    max_leaves=6,
)
tool_names = st.one_of(
    st.sampled_from(["write_file", "patch", "terminal", "read_file", "execute_code"]), st.text(max_size=8)
)
arg_keys = st.sampled_from(["path", "patch", "command", "workdir", "mode", "content", "old_string"])


@PROFILE
@given(
    tool=tool_names,
    args=st.one_of(junk, st.dictionaries(arg_keys, junk, max_size=5)),
    owners=st.one_of(st.dictionaries(st.text(max_size=12), st.text(max_size=8), max_size=4), junk),
    role=st.one_of(st.text(max_size=8), junk),
    cwd=st.one_of(st.none(), st.text(max_size=20), junk),
    auto_own=st.booleans(),
)
def test_evaluate_never_raises_on_garbage(world, tool, args, owners, role, cwd, auto_own):
    d = guard.evaluate(tool, args, team_dir=str(world["root"]), role=role, owners=owners, cwd=cwd, auto_own=auto_own)
    assert isinstance(d, guard.Decision) and d.action in ("allow", "block")
    assert (d.hook_result() is None) == (d.action == "allow")
    if d.blocked:
        assert d.message.strip()  # Hermes ignores a block without a message


_command_chars = st.sampled_from(
    [
        *list("abc >|&;()<$`'\"\\*?[]{}~#!=-/.\n\t\r"),
        "é",
        "日",
        "\x00",
        "<<EOF",
        "$(",
        ">>",
        "2>&1",
        "tee ",
        "sed -i ",
        "rm -rf ",
        "cd ",
        "mv ",
    ]
)


@PROFILE
@given(cmd=st.lists(_command_chars, max_size=40).map("".join), cwd=cwd_names)
def test_evaluate_never_raises_on_shell_noise(world, cmd, cwd):
    d = ev(
        world, "terminal", {"command": cmd, "workdir": str(cwd_of(world, cwd))}, cwd_of(world, cwd), all_owned(world)
    )
    assert isinstance(d, guard.Decision)


# ---------------------------------------------------------------- 2/3. never blocks outside the team or .team/
@PROFILE
@given(p=paths, cwd=cwd_names)
def test_never_blocks_paths_outside_team_or_under_dot_team(world, p, cwd):
    cwd_p = cwd_of(world, cwd)
    where, _ = classify(world, p, cwd_p)
    if where == "inside":
        return
    owners = all_owned(world)
    for tool, args in calls_for(p, cwd_p):
        d = ev(world, tool, args, cwd_p, owners)
        assert not d.blocked, f"{tool} {args} resolves {where} but was blocked: {d.message}"
        assert d.claim == [] or all(not c.startswith((".team", "..", "/")) for c in d.claim)


# ---------------------------------------------------------------- 4. never claims junk
@PROFILE
@given(p=paths, cwd=cwd_names)
def test_auto_claims_are_always_clean_team_relative_paths(world, p, cwd):
    cwd_p = cwd_of(world, cwd)
    for tool, args in calls_for(p, cwd_p):
        d = ev(world, tool, args, cwd_p, {})
        assert not d.blocked  # no owners at all -> nothing to block
        for c in d.claim:
            assert not os.path.isabs(c) and c != ".." and not c.startswith(("../", ".team"))
            assert os.path.normpath(c) == c


# ---------------------------------------------------------------- 5. positive control: owned files ARE blocked
@PROFILE
@given(p=paths, cwd=cwd_names)
def test_files_owned_by_someone_else_are_blocked_however_they_are_spelled(world, p, cwd):
    cwd_p = cwd_of(world, cwd)
    where, rel = classify(world, p, cwd_p)
    if where != "inside" or rel is None:
        return
    owners = {rel: OTHER}
    is_dir = os.path.isdir(os.path.join(cwd_p, p))
    for tool, args in calls_for(p, cwd_p):
        if is_dir and tool == "terminal" and args["command"].startswith(("cp ", "mv ")):
            continue  # `cp x <dir>` copies INTO the directory: the directory itself is not written
        d = ev(world, tool, args, cwd_p, owners)
        assert d.blocked, f"{tool} {args} writes owned file {rel!r} but was allowed"
        assert OTHER in d.message and d.message.strip()
        # ... and the owner, spelled in another case, may write it
        assert not ev(world, tool, args, cwd_p, owners, role=OTHER.upper()).blocked


@PROFILE
@given(p=paths, cwd=cwd_names)
def test_unowned_inside_files_are_claimed_by_file_tools_only(world, p, cwd):
    cwd_p = cwd_of(world, cwd)
    where, rel = classify(world, p, cwd_p)
    if where != "inside" or rel is None:
        return
    d = ev(world, "write_file", {"path": p, "content": "x"}, cwd_p, {"other.py": OTHER})
    assert d.action == "allow" and d.claim == [rel]
    d = ev(world, "write_file", {"path": p, "content": "x"}, cwd_p, {"other.py": OTHER}, auto_own=False)
    assert d.claim == []
    d = ev(
        world, "terminal", {"command": f"echo > {shlex.quote(p)}", "workdir": str(cwd_p)}, cwd_p, {"other.py": OTHER}
    )
    assert d.claim == []  # shell writes are never auto-claimed


# ---------------------------------------------------------------- 6. decisions do not depend on irrelevant owners
@PROFILE
@given(p=paths, extra=st.dictionaries(st.text(_NAME_CHARS, min_size=1, max_size=6), st.just(OTHER), max_size=4))
def test_unrelated_owners_never_change_a_decision_about_outside_paths(world, p, extra):
    cwd_p = world["root"]
    where, _ = classify(world, p, cwd_p)
    if where == "inside":
        return
    for tool, args in calls_for(p, cwd_p):
        assert ev(world, tool, args, cwd_p, extra).action == "allow"


# ---------------------------------------------------------------- regression: `symlink/..` (fixed by T64)
@pytest.mark.parametrize("tool", ["write_file", "terminal"])
def test_dotdot_after_symlink_is_resolved_like_the_os(world, tool):
    """Was a BYPASS (T64): `deep` -> src/deeper/inner, so `deep/../owned.py` is really src/deeper/owned.py."""
    root = world["root"]
    p = "deep/../owned.py"
    assert classify(world, p, root) == ("inside", "src/deeper/owned.py")
    args = {"path": p, "content": "x"} if tool == "write_file" else {"command": f"echo > {p}"}
    d = ev(world, tool, args, root, {"src/deeper/owned.py": OTHER})
    assert d.blocked


def test_dotdot_after_symlink_to_outside_is_not_blocked(world):
    """Was a false block (T64): `link_out/../docs` is <base>/docs (outside), the guard saw proj/docs."""
    root = world["root"]
    assert classify(world, "link_out/../docs", root)[0] == "outside"
    assert not ev(world, "write_file", {"path": "link_out/../docs", "content": "x"}, root, {"docs": OTHER}).blocked
