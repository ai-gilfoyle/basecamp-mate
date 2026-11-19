"""Tests for the decision-autocomplete behavior: a decision to-do whose body links to its decision in mate is commented
on and completed once mate no longer lists that decision as open, and left alone otherwise.

The basecamp CLI and curl are stubbed; nothing touches the network.
"""
import json, os, sys, unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import Base, Stub  # noqa: E402
import sync  # noqa: E402

SOURCE = "http://mate.test:3300"
LINK = "https://mate.example.ts.net:8443/#/decision/{}/{}"


def body(home="main", task="pick-db"):
    return f'<div>Which database?<br><a href="{LINK.format(home, task)}">{LINK.format(home, task)}</a></div>'


class MateStub(Stub):
    """Adds curl answering `GET <SOURCE>/api/decisions` from `decisions` (None: curl fails; `raw`, when set, is its
    output as is), and the to-dos and cards (`todos`: id -> record) their refetch reads, with `todos complete` /
    `cards done` marking them completed (unless `fail_complete`)."""

    def __init__(self):
        super().__init__()
        self.decisions = {"homes": [{"id": "main", "ok": True}], "cards": [], "history": []}
        self.todos, self.curls, self.missing, self.raw, self.fail_complete = {}, [], set(), None, False

    def __call__(self, cmd, **kw):
        if cmd[0] == "curl":
            self.curls.append(cmd)
            if self.raw is not None:
                return SimpleNamespace(stdout=self.raw, stderr="", returncode=0)
            if self.decisions is None:
                return SimpleNamespace(stdout="", stderr="curl: (7) Failed to connect", returncode=7)
            return SimpleNamespace(stdout=json.dumps(self.decisions), stderr="", returncode=0)
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            args = args[2:] if args[:1] == ["-P"] else args
            if self.fail_complete and args[:2] in (["todos", "complete"], ["cards", "done"]):
                self.calls.append(args)
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            if args[:2] == ["api", "get"] and ("/todos/" in args[2] or "/card_tables/cards/" in args[2]):
                self.calls.append(args)
                tid = int(args[2].rsplit("/", 1)[1][:-5])
                if tid in self.missing:
                    return SimpleNamespace(stdout=json.dumps({"ok": False, "code": "not_found", "error": "Resource not found"}),
                                           stderr="", returncode=1)
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": self.todos.get(tid, {})}), stderr="", returncode=0)
            if args[:2] in (["todos", "complete"], ["cards", "done"]):
                self.todos.setdefault(int(args[2]), {})["completed"] = True
            if args[:2] == ["comments", "create"]:
                self.calls.append(args)
                self.comments.setdefault(args[2], []).append({"id": 4000 + len(self.calls), "input": kw.get("input")})
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": {"id": 4000 + len(self.calls)}}),
                                       stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def did(self, verb):
        return [a for a in self.calls if a[:2] == verb.split()]


class Autocomplete(Base):
    def setUp(self):
        super().setUp()
        self.stub = MateStub()
        self.config(todos={}, decision_autocomplete={"source": SOURCE}, profile="firstmate")

    def config(self, **kw):
        path = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(path))
        cfg.update(kw)
        json.dump(cfg, open(path, "w"))

    def track(self, key="pick-db", tid=101, description=None, **rec):
        state = self.load()
        state[key] = {"todo": tid, "title": "Which database?", "cursor": 0, "created": "2026-11-19T00:00:00Z", **rec}
        self.side("todos.json", state)
        self.stub.todos[tid] = {"id": tid, "content": "Which database?", "status": "active", "completed": False,
                                "description": body(task=key) if description is None else description}

    def load(self):
        p = os.path.join(self.cfgdir, "todos.json")
        return json.load(open(p)) if os.path.exists(p) else {}

    def open_card(self, task="pick-db", home="main"):
        self.stub.decisions["cards"].append({"home": home, "task": task, "state": "open"})

    def run_step(self, dry=False):
        s = self.sync(dry=dry)
        s.behaviors["decision-autocomplete"].run()
        return s

    def log(self):
        p = os.path.join(self.cfgdir, "sync.log")
        return open(p).read() if os.path.exists(p) else ""

    def test_a_closed_decision_completes_its_todo_with_the_answer_as_a_comment(self):
        self.track()
        self.stub.decisions["history"] = [
            {"home": "main", "task": "pick-db", "state": "stale", "answer": None, "at": "2026-11-19T03:00:00Z"},
            {"home": "main", "task": "pick-db", "state": "answered", "answer": "Postgres", "at": "2026-11-19T02:00:00Z"},
            {"home": "main", "task": "other", "state": "answered", "answer": "No", "at": "2026-11-19T04:00:00Z"}]
        self.run_step()
        [c] = self.stub.did("comments create")
        self.assertEqual(c[2], "101")
        self.assertEqual(self.stub.comments["101"][0]["input"], "Answered in mate: Postgres")
        self.assertEqual([a[2] for a in self.stub.did("todos complete")], ["101"])
        rec = self.load()["pick-db"]
        self.assertTrue(rec["completed"])
        self.assertEqual(rec["decision"], ["main", "pick-db"])
        self.run_step()  # once completed, never again
        self.assertEqual((len(self.stub.did("comments create")), len(self.stub.did("todos complete"))), (1, 1))

    def test_with_no_recorded_answer_the_comment_says_only_answered_in_mate(self):
        self.track()
        self.run_step()
        self.assertEqual(self.stub.comments["101"][0]["input"], "Answered in mate.")
        self.assertTrue(self.load()["pick-db"]["completed"])

    def test_an_open_decision_is_untouched(self):
        self.track()
        self.open_card()
        self.run_step()
        self.run_step()
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.assertNotIn("completed", self.load()["pick-db"])
        self.assertEqual(sum(a[2].endswith("/todos/101.json") for a in self.stub.did("api get")), 1)  # body read once

    def test_an_unreadable_source_touches_nothing_and_is_logged_once(self):
        self.track()
        self.stub.decisions = None
        self.run_step()
        self.run_step()
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.assertNotIn("completed", self.load()["pick-db"])
        self.assertEqual(self.log().count("cannot read the decisions source, nothing completed"), 1)
        for bad in ("not json", json.dumps({"cards": []}), json.dumps(["x"])):
            self.stub.raw = bad
            self.run_step()
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.stub.raw = None
        self.stub.decisions = {"homes": [{"id": "main", "ok": True}], "cards": [], "history": []}
        self.run_step()
        self.assertIn("the decisions source reads again", self.log())
        self.assertTrue(self.load()["pick-db"]["completed"])

    def test_a_home_mate_could_not_read_or_does_not_list_is_untouched(self):
        self.track()
        self.track("ynab-thing", 102, description=body("ynab", "ynab-thing"))
        self.stub.decisions["homes"] = [{"id": "main", "ok": False, "error": "boom"}]
        self.run_step()
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.assertIn("mate did not read home main, ynab", self.log())

    def test_a_todo_without_a_link_is_untouched_and_its_body_read_once(self):
        self.track(description="<div>Which database? No link here.</div>")
        self.run_step()
        self.run_step()
        self.assertEqual(self.stub.curls, [])  # nothing linked: the source is not even read
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.assertIsNone(self.load()["pick-db"]["decision"])
        self.assertEqual(len(self.stub.did("api get")), 1)

    def test_a_body_linking_two_decisions_is_untouched(self):
        self.track(description=body("main", "a") + body("main", "b"))
        self.run_step()
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.assertIsNone(self.load()["pick-db"]["decision"])

    def test_an_already_completed_todo_is_untouched(self):
        self.track(completed="2026-11-19T01:00:00Z")
        self.run_step()
        self.assertEqual(self.stub.calls, [])
        self.assertEqual(self.stub.curls, [])

    def test_a_todo_completed_in_basecamp_meanwhile_is_left_as_it_is(self):
        self.track()
        self.stub.todos[101]["completed"] = True
        self.run_step()
        self.run_step()
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        rec = self.load()["pick-db"]
        self.assertNotIn("completed", rec)
        self.assertIn("decision_settled", rec)

    def test_a_not_found_todo_is_about_that_record_only(self):
        self.track()
        self.track("pick-cache", 102, description=body(task="pick-cache"))
        self.stub.missing.add(101)
        self.run_step()
        self.assertIsNone(self.load()["pick-db"]["decision"])
        self.assertEqual([a[2] for a in self.stub.did("todos complete")], ["102"])

    def test_requests_and_todos_a_decision_was_only_put_on_are_never_touched(self):
        self.track("request-7", 107, request={"captain": True})
        self.track("on-8", 108, on=True)
        self.track("step-9", 109, type="step", card=5)
        self.run_step()
        self.assertEqual((self.stub.calls, self.stub.curls), ([], []))

    def test_an_adopted_todo_is_never_touched(self):
        self.track("marked", 103, description=body(task="marked"), adopted=True)
        self.track("legacy", 104, description=body(task="legacy"), created="2026-11-01T10:00:00.123Z")  # before the marker
        self.run_step()
        self.assertEqual((self.stub.calls, self.stub.curls), ([], []))

    def test_todo_track_marks_the_record_adopted(self):
        self.stub.todos[105] = {"id": 105, "content": "Made by hand", "created_at": "2026-11-01T10:00:00.123Z"}
        self.sync().todo_track("by-hand", 105)
        self.assertIs(self.load()["by-hand"]["adopted"], True)

    def test_a_todo_with_an_unanswered_subtask_is_untouched_until_all_are_answered(self):
        self.track(subtasks=[{"text": "A?", "done": "2026-11-19T01:00:00Z", "id": 1}, {"text": "B?", "done": None, "id": 2}])
        self.run_step()
        self.assertEqual((self.stub.calls, self.stub.curls), ([], []))
        state = self.load()
        state["pick-db"]["subtasks"][1]["done"] = "2026-11-19T02:00:00Z"
        self.side("todos.json", state)
        self.run_step()
        self.assertTrue(self.load()["pick-db"]["completed"])

    def test_a_failed_completion_is_retried_without_a_second_comment(self):
        self.track()
        self.stub.fail_complete = True
        self.run_step()
        self.assertNotIn("completed", self.load()["pick-db"])
        self.assertIn("decision close pick-db: basecamp todos complete", self.log())
        self.stub.fail_complete = False
        self.run_step()
        self.assertEqual(len(self.stub.did("comments create")), 1)
        self.assertTrue(self.load()["pick-db"]["completed"])

    def test_a_dry_run_reads_but_posts_nothing(self):
        self.track()
        self.run_step(dry=True)
        self.assertEqual(self.stub.did("comments create") + self.stub.did("todos complete"), [])
        self.assertNotIn("decision", self.load()["pick-db"])

    def test_a_card_decision_is_moved_to_done(self):
        self.track(type="card")
        self.stub.todos[101] = {"id": 101, "title": "Which database?", "completed": False, "content": body()}
        self.run_step()
        self.assertEqual([a[2] for a in self.stub.did("cards done")], ["101"])

    def test_the_timer_runs_it_when_on_and_never_when_off(self):
        self.config(cards=False)
        self.track()
        self.sync().main()
        self.assertTrue(self.load()["pick-db"]["completed"])
        self.config(decision_autocomplete=None)
        self.track("pick-cache", 102, description=body(task="pick-cache"))
        self.sync().main()
        self.assertNotIn("completed", self.load()["pick-cache"])

    def test_config_is_checked(self):
        for bad in ({"source": "mate"}, {"source": SOURCE, "x": 1}, "yes", {}):
            self.config(decision_autocomplete=bad)
            with self.assertRaises(ValueError):
                self.sync()
        self.config(decision_autocomplete={"source": SOURCE}, todos=None)
        with self.assertRaises(ValueError):
            self.sync()

    def test_behaviors_lists_it(self):
        out = []
        sys.stdout, real = SimpleNamespace(write=out.append, flush=lambda: None), sys.stdout
        try:
            sync.cli(["behaviors", "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")], runner=self.stub)
        finally:
            sys.stdout = real
        line = next(ln for ln in "".join(out).splitlines() if ln.startswith("decision-autocomplete"))
        self.assertIn("on", line.split()[1])
        self.assertIn("5 min", line)


if __name__ == "__main__":
    unittest.main()
