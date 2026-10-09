from __future__ import annotations

import json
import multiprocessing as mp
import os

import pytest

import mp_workers
from hermes_crew import board, events
from hermes_crew.board import BoardError


def test_rel_and_inside(team, monkeypatch):
    monkeypatch.chdir(team.root)
    (team.root / "sub").mkdir()
    assert board.rel(team, "sub/../a.txt") == "a.txt"
    assert board.rel(team, team.root / "sub" / "b.py") == "sub/b.py"
    os.symlink(team.root / "sub", team.root / "link")
    assert board.rel(team, "link/c.py") == "sub/c.py"
    assert board.inside(team, "a.txt") and not board.inside(team, "/etc/passwd")
    assert not board.inside(team, ".team/board.log") and not board.inside(team, ".")


def test_own_transfer_release(team):
    assert board.own(team, "A", "f.txt", "g.txt") == ["f.txt", "g.txt"]
    assert board.owner_of(team, "f.txt") == "A" and board.owner_of(team, "nope") is None
    with pytest.raises(BoardError, match="REFUSED: f.txt is owned by A - ask A \\(or lead\\)"):
        board.own(team, "B", "h.txt", "f.txt")
    assert board.owner_of(team, "h.txt") is None                  # all-or-nothing
    with pytest.raises(BoardError, match="only A or lead can transfer"):
        board.transfer(team, "B", "f.txt", "B")
    assert board.transfer(team, "A", "f.txt", "B") == "A"
    assert board.owner_of(team, "f.txt") == "B"
    with pytest.raises(BoardError, match="only B or lead can release"):
        board.release(team, "A", "f.txt")
    assert board.release(team, "lead", "f.txt") == "B"
    assert board.release(team, "lead", "f.txt") is None
    with pytest.raises(BoardError, match="has no owner - claim it"):
        board.transfer(team, "lead", "f.txt", "C")
    assert board.owners(team) == {"g.txt": "A"}
    assert [e["kind"] for e in events.read(team, kind="board.")][:3] == ["board.own", "board.own", "board.transfer"]


def test_log_format(team):
    board.own(team, "A", "f.txt")
    board.decide(team, "A", "tabs\tand\nnewlines")
    board.approve(team, "B", "f.txt", "abcdef123456", "read it all carefully, twice")
    lines = [ln.split("\t") for ln in board.log_path(team).read_text().splitlines()]
    assert [ln[1:] for ln in lines] == [
        ["A", "OWN", "f.txt"],
        ["A", "DECIDE", "tabs and newlines", ""],
        ["B", "APPROVE", "f.txt", "abcdef123456", "read it all carefully, twice"],
    ]
    assert len(lines[0][0]) == 19 and lines[0][0][10] == "T"


def test_decide_numbering_and_replaces(team):
    assert board.decide(team, "A", "one") == 1
    with pytest.raises(BoardError, match=r"no such decision \(existing: 1..1\)"):
        board.decide(team, "A", "x", replaces=5)
    assert board.decide(team, "A", "two", replaces=1) == 2
    with pytest.raises(BoardError, match="already replaced by #2"):
        board.decide(team, "A", "x", replaces=1)
    assert [d["n"] for d in board.current_decisions(team)] == [2]


def test_files_and_approvals(team):
    (team.root / "f.txt").write_text("v1")
    board.own(team, "A", "f.txt")
    with pytest.raises(BoardError, match="must come from someone else"):
        board.approve(team, "a", "f.txt", board.sha12(team.root / "f.txt"), "x" * 30)
    board.approve(team, "B", "f.txt", board.sha12(team.root / "f.txt"), "checked everything in it")
    f = board.files(team)[0]
    assert f["ok"] and f["approvals"][0]["status"] == "OK"
    (team.root / "f.txt").write_text("v2")
    f = board.files(team)[0]
    assert not f["ok"] and f["approvals"][0]["status"] == "STALE"
    board.transfer(team, "A", "f.txt", "B")
    assert board.files(team)[0]["approvals"][0]["status"] == "IGNORED"


def test_main_texts(team, monkeypatch, capsys):
    monkeypatch.chdir(team.root)

    def run(role, *argv):
        rc = board.main(list(argv), team=team, role=role)
        o = capsys.readouterr()
        return rc, o.out, o.err

    assert run("Eng", "own", "a.txt") == (0, "Eng owns a.txt\n", "")
    assert run("Eng", "decide", "use", "port", "8080") == (0, "decision #1 recorded\n", "")
    assert run("Eng", "decide", "--replaces", "#1", "port 9090") == (0, "decision #2 recorded (replaces #1)\n", "")
    rc, _, err = run("Eng", "decide", "--replaces", "abc", "x")
    assert rc == 1 and "REFUSED: --replaces abc: no such decision" in err
    assert run("Eng", "decide")[0] == 2
    assert run("Eng", "transfer", "a.txt", "Rev") == (0, "a.txt: Eng -> Rev\n", "")
    assert run("Eng", "transfer", "a.txt", "bad role")[0] == 2
    assert run("Rev", "release", "a.txt") == (0, "a.txt released (was Rev)\n", "")
    assert run("Rev", "release", "a.txt") == (0, "a.txt has no owner\n", "")
    (team.root / "b.txt").write_text("b")
    rc, _, err = run("Rev", "approve", "b.txt", "short")
    assert rc == 1 and "describe what you actually checked" in err
    rc, _, err = run("Rev", "approve", "missing.txt", "long enough evidence text here")
    assert rc == 2 and "usage: hermes-team-board approve <existing file>" in err
    rc, out, _ = run("Rev", "approve", "b.txt", "read the whole file and checked the content")
    assert rc == 0 and out == f"approved b.txt @ {board.sha12(team.root / 'b.txt')}\n"
    rc, out, _ = run("lead", "status")
    assert rc == 0
    assert f"== team ({team.root}) ==" in out
    assert "== decisions (current; replaced ones hidden) ==" in out and "#2 [" in out and "#1 [" not in out
    assert "  b.txt                                    owner=(none)" in out
    assert "      approved by Rev            OK - read the whole file" in out
    assert "      !! no owner" in out
    rc, _, err = run("lead", "nonsense")
    assert rc == 2 and err.startswith("usage:")


def test_status_empty_board_does_not_create_team(tmp_path, capsys):
    from hermes_crew.team import Team
    t = Team(tmp_path / "x")
    assert board.main(["status"], team=t, role="lead") == 0
    assert "(board is empty)" in capsys.readouterr().out
    assert not t.dir.exists()


def test_concurrent_own_one_winner(team, tmp_path):
    ctx = mp.get_context("spawn")
    for i in range(4):
        outs = [tmp_path / f"o{i}-{j}.json" for j in range(4)]
        ps = [ctx.Process(target=mp_workers.board_own, args=(str(team.root), f"R{j}", f"f{i}.txt", str(o)))
              for j, o in enumerate(outs)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(60)
        assert sum(json.loads(o.read_text()) for o in outs) == 1
        assert board.log_path(team).read_text().count(f"\tOWN\tf{i}.txt") == 1


def test_concurrent_decide_unique_numbers(team, tmp_path):
    ctx = mp.get_context("spawn")
    outs = [tmp_path / f"d{j}.json" for j in range(8)]
    ps = [ctx.Process(target=mp_workers.board_decide, args=(str(team.root), f"R{j}", f"d{j}", str(o)))
          for j, o in enumerate(outs)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
    assert sorted(json.loads(o.read_text()) for o in outs) == list(range(1, 9))
