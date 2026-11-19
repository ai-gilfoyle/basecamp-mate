"""Tests for writes that cannot fail silently: `sync.py complete` (a decision to-do the agent created in a list
"decision_lists" names, completed and read back), the read-back and exit status of
`todo complete` and `todo subtask`, `reply` and `dismiss`, and the basecamp CLI calls that raise on any answer that is
not ok (an HTTP error, a usage error, empty or unreadable output, a non-zero exit).

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, json, os, sys, unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, line  # noqa: E402
from test_tools import TodoStub, ToolBase  # noqa: E402
import sync  # noqa: E402
from tools import BasecampError  # noqa: E402

BUCKET = 49226851  # another project of the account, as in Gilfoyle's "Decisions for Caleb" list
LIST = 10525772928
TODO = 10525772999
REF = f"{BUCKET}/{TODO}"


class CompleteStub(TodoStub):
    """`todos complete` (`subtasks complete`) that answers ok but changes nothing while `ignore_complete`
    (`ignore_subtask`) is set, as a silent failure would."""

    def __init__(self):
        super().__init__()
        self.ignore_complete = self.ignore_subtask = False

    def __call__(self, cmd, **kw):
        args = cmd[3:-3] if cmd[0] == "basecamp" else []
        core = args[2:] if args[:1] == ["-P"] else args
        if (self.ignore_complete and core[:2] == ["todos", "complete"]
                or self.ignore_subtask and core[:2] == ["subtasks", "complete"]):
            self.calls.append(core)
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)


class Base(ToolBase):
    def setUp(self):
        super().setUp()
        self.stub = CompleteStub()
        # Gilfoyle's home: decision to-dos made outside `todo create` in a list named in "decision_lists", no "todos"
        self.cfg(drop=("todos",), decision_lists=[LIST])
        self.stub.todo_records[str(TODO)] = {"id": TODO, "content": "Merge?", "completed": False, "status": "active",
                                             "parent": {"id": LIST, "type": "Todolist"}, "creator": {"id": ACTING}}

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli([*args, "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")],
                            runner=self.stub)
        return code, out.getvalue()

    def log(self):
        p = os.path.join(self.cfgdir, "sync.log")
        return open(p).read() if os.path.exists(p) else ""

    def state(self, name):
        p = os.path.join(self.cfgdir, name)
        return json.load(open(p)) if os.path.exists(p) else {}

    def made(self, verb):
        return self.stub.made(verb)


class Complete(Base):
    def test_completes_a_todo_in_a_decision_list_of_another_project_and_verifies_it(self):
        code, _ = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 0, self.log())
        self.assertIn(f"complete {REF}: completed and verified", self.log())
        self.assertEqual(self.made("comments create"), [])
        self.assertEqual(self.made("todos complete"), [["todos", "complete", str(TODO)]])
        reads = [i for i, c in enumerate(self.stub.calls) if c == ["api", "get", f"/buckets/{BUCKET}/todos/{TODO}.json"]]
        self.assertGreater(reads[-1], self.stub.calls.index(["todos", "complete", str(TODO)]))  # read back after
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])

    def test_a_completion_the_read_back_shows_open_fails_non_zero_and_running_it_again_completes_it(self):
        self.stub.ignore_complete = True
        code, out = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 1)
        self.assertIn(f"FAILED complete {REF} RuntimeError: complete {REF}: Basecamp still shows todo {TODO} open after "
                      "the completion call", self.log())
        self.assertIn("FAILED", out)  # the agent that ran it sees the failure, not only sync.log
        self.poll()
        self.assertEqual(len(self.made("todos complete")), 1)  # the timer never retries it
        self.stub.ignore_complete = False
        self.assertEqual(self.cli("complete", "--todo", REF)[0], 0)
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])

    def test_a_todo_someone_else_created_in_a_decision_list_is_refused(self):
        self.stub.todo_records[str(TODO)]["creator"] = {"id": CAPTAIN}
        code, _ = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 1)
        self.assertIn(f"it was created by {CAPTAIN}, not the agent's login", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_a_todo_already_completed_gets_no_write(self):
        self.stub.todo_records[str(TODO)]["completed"] = True
        code, _ = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 0)
        self.assertIn("already completed in Basecamp", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_a_todo_outside_the_decision_lists_is_refused(self):
        self.stub.todo_records[str(TODO)]["parent"] = {"id": 999, "type": "Todolist"}  # an ordinary work to-do
        code, _ = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 1)
        self.assertIn('its to-do list 999 is not in the config\'s "decision_lists"', self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_a_tracked_todo_is_refused_for_todo_complete(self):
        self.side("todos.json", {"request-42": {"todo": TODO, "bucket": BUCKET, "request": CAPTAIN,
                                                "closed": "unassigned"}})
        code, _ = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 1)
        self.assertIn("todos.json tracks it as request-42: complete it with `todo complete --todo request-42`", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_only_bucket_slash_id_names_the_todo(self):
        for bad in (str(TODO), f"https://3.basecamp.com/1111111/buckets/{BUCKET}/todos/{TODO}"):
            self.assertEqual(self.cli("complete", "--todo", bad)[0], 1)
            self.assertIn(f"{bad!r} is not a to-do: give <bucket>/<to-do id>", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_decision_lists_must_be_list_ids(self):
        self.cfg(decision_lists="10525772928")
        with self.assertRaises(ValueError):
            self.sync()

    def test_refused_signed_in_as_anyone_but_the_agent(self):
        self.stub.me = CAPTAIN
        self.assertEqual(self.cli("complete", "--todo", REF)[0], 1)
        self.assertEqual(self.made("todos complete"), [])
        self.assertIn("FAILED wrong login", self.log())

    def test_a_login_that_may_not_post_fails_non_zero(self):
        self.cfg(drop=("profile",))  # the default sign-in is the agent, but nothing posts without a profile
        code, _ = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 1)
        self.assertIn("not posting", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_dry_run_writes_nothing(self):
        code, _ = self.cli("complete", "--todo", REF, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.made("todos complete"), [])


class TodoCompleteReadBack(Base):
    def setUp(self):
        super().setUp()
        self.cfg(todos={"todoset": "66"})

    def test_todo_complete_reads_back_and_records_nothing_while_basecamp_shows_it_open(self):
        tid = self.create()
        self.stub.ignore_complete = True
        code, _ = self.cli("todo", "complete", "--todo", "ta-x")
        self.assertEqual(code, 1)
        self.assertIn(f"Basecamp still shows todo {tid} open", self.log())
        self.assertNotIn("completed", self.todos()["ta-x"])
        self.stub.ignore_complete = False
        self.assertEqual(self.cli("todo", "complete", "--todo", "ta-x")[0], 0)  # a retry completes it
        self.assertTrue(self.todos()["ta-x"]["completed"])

    def test_a_todo_recorded_completed_but_open_in_basecamp_is_completed_again(self):
        tid = self.create()
        state = self.todos()
        state["ta-x"]["completed"] = "2026-11-19T10:00:00Z"  # recorded by a completion that silently failed
        self.side("todos.json", state)
        self.assertEqual(self.cli("todo", "complete", "--todo", "ta-x")[0], 0)
        self.assertEqual(self.made("todos complete"), [["todos", "complete", str(tid)]])
        self.assertTrue(self.stub.todo_records[str(tid)]["completed"])

    def test_a_login_that_may_not_post_fails_non_zero(self):
        self.create()
        self.cfg(drop=("profile",))
        self.assertEqual(self.cli("todo", "complete", "--todo", "ta-x")[0], 1)
        self.assertEqual(self.made("todos complete"), [])

    def test_a_request_reopened_and_assigned_to_someone_else_is_not_completed(self):
        self.stub.todo_records["42"] = {"id": 42, "content": "Do it", "completed": False, "status": "active",
                                        "assignees": [{"id": CAPTAIN}]}
        self.side("todos.json", {"request-42": {"todo": 42, "request": CAPTAIN, "completed": "2026-11-18T10:00:00Z"}})
        self.assertEqual(self.cli("todo", "complete", "--todo", "request-42")[0], 1)
        self.assertIn("no longer shows it active and assigned to you", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_a_decision_with_no_step_of_its_own_fails_non_zero(self):
        self.side("todos.json", {"d": {"todo": 77, "on": "a to-do", "subtasks": []}})
        self.assertEqual(self.cli("todo", "complete", "--todo", "d")[0], 1)
        self.assertIn("no decision step of its own to complete", self.log())

    def test_a_closed_request_fails_non_zero(self):
        self.side("todos.json", {"request-42": {"todo": 42, "request": CAPTAIN, "closed": "unassigned"}})
        self.assertEqual(self.cli("todo", "complete", "--todo", "request-42")[0], 1)
        self.assertIn("the request was closed (unassigned)", self.log())
        self.assertEqual(self.made("todos complete"), [])


class SubtaskReadBack(Base):
    def setUp(self):
        super().setUp()
        self.cfg(todos={"todoset": "66"})
        self.tid = self.create(subtasks=["Merge?", "Release?"])

    def test_a_subtask_basecamp_still_shows_open_is_not_recorded_done(self):
        self.stub.ignore_subtask = True
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "1")[0], 1)
        self.assertIn(f"Basecamp still shows subtask {self.tid + 1} open", self.log())
        self.assertIsNone(self.todos()["ta-x"]["subtasks"][0]["done"])

    def test_running_the_last_subtask_again_completes_a_todo_still_open_without_a_second_subtask_write(self):
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "1")[0], 0)
        self.stub.ignore_complete = True
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "2")[0], 1)
        self.assertTrue(self.todos()["ta-x"]["subtasks"][1]["done"])
        self.assertNotIn("completed", self.todos()["ta-x"])
        self.stub.ignore_complete = False
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "2")[0], 0)
        self.assertEqual(len(self.made("subtasks complete")), 2)
        self.assertTrue(self.stub.todo_records[str(self.tid)]["completed"])
        self.assertTrue(self.todos()["ta-x"]["completed"])

    def test_a_sibling_checked_in_basecamp_but_not_yet_read_holds_the_todo_open(self):
        self.stub.subtasks_done.add(str(self.tid + 2))  # checked by the captain; the timer has not read it yet
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "1")[0], 0)
        self.assertEqual(self.made("todos complete"), [])
        self.assertNotIn("completed", self.todos()["ta-x"])

    def test_a_subtask_unchecked_in_basecamp_is_completed_again_and_holds_the_todo_open(self):
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "1")[0], 0)
        self.stub.subtasks_done.discard(str(self.tid + 1))  # unchecked in Basecamp
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "1")[0], 0)
        self.assertEqual(len(self.made("subtasks complete")), 2)
        self.stub.subtasks_done.discard(str(self.tid + 1))
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "2")[0], 0)
        self.assertEqual(self.made("todos complete"), [])  # subtask 1 is open in Basecamp, whatever todos.json says


class Answer:
    """One basecamp CLI answer: stdout, stderr, exit status."""

    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode

    def __call__(self, cmd, **kw):
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)


class LoudCalls(Base):
    def call(self, answer, *args):
        s = self.sync()
        s.run = answer
        return s.bc(*args)

    def test_an_http_error_raises_with_its_endpoint_code_and_exit(self):
        out = json.dumps({"ok": False, "error": "Resource not found", "code": "not_found", "retryable": False})
        with self.assertRaises(BasecampError) as e:
            self.call(Answer(out, returncode=2), "api", "get", f"/buckets/{BUCKET}/todos/1.json")
        self.assertEqual(str(e.exception), f"basecamp api get /buckets/{BUCKET}/todos/1.json: Resource not found "
                                           "(code not_found, exit 2)")
        self.assertEqual(e.exception.code, "not_found")

    def test_a_usage_error_raises(self):
        # The call that left Gilfoyle's decision to-dos open: `api post` without -d never reaches Basecamp.
        out = json.dumps({"ok": False, "error": "--data required", "code": "usage", "retryable": False})
        with self.assertRaises(BasecampError) as e:
            self.call(Answer(out, returncode=1), "api", "post", f"/buckets/{BUCKET}/todos/{TODO}/completion.json")
        self.assertIn(f"api post /buckets/{BUCKET}/todos/{TODO}/completion.json: --data required (code usage, exit 1)",
                      str(e.exception))

    def test_empty_or_unreadable_output_raises(self):
        for answer, said in ((Answer(""), "empty answer from the CLI"),
                             (Answer("not json", "panic", 2), "no JSON answer from the CLI: not jsonpanic"),
                             (Answer("[1, 2]"), "no JSON answer from the CLI: [1, 2]"),
                             (Answer(json.dumps({"data": {}})), "None")):
            with self.assertRaises(BasecampError) as e:
                self.call(answer, "todos", "complete", str(TODO))
            self.assertIn(said, str(e.exception))

    def test_an_ok_that_is_not_exactly_true_raises(self):
        for ok in ("false", "true", 1, None):
            with self.assertRaises(BasecampError):
                self.call(Answer(json.dumps({"ok": ok, "data": None})), "todos", "complete", str(TODO))

    def test_an_ok_answer_with_a_non_zero_exit_raises(self):
        with self.assertRaises(BasecampError) as e:
            self.call(Answer(json.dumps({"ok": True, "data": None}), "boom", 3), "todos", "complete", str(TODO))
        self.assertIn("the CLI answered ok but exited 3: boom", str(e.exception))
        self.assertIn("exit 3", str(e.exception))


class ReplyReadBack(Base):
    def setUp(self):
        super().setUp()
        self.cfg(chats=[77])
        self.stub.lines = {"77": [line(1)]}
        self.side("chats.json", {"77": {"lines": [1]}})

    def test_a_reply_basecamp_answers_without_an_id_fails(self):
        real = self.stub.__call__

        def no_id(cmd, **kw):
            out = real(cmd, **kw)
            if "post" in cmd and any(str(a).endswith("/chats/77/lines.json") for a in cmd):
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
            return out
        s = self.sync()
        s.run = no_id
        with self.assertRaises(RuntimeError) as e:
            s.reply(1, "Yes.")
        self.assertIn("without an id", str(e.exception))
        self.assertNotIn("replied", self.state("chats.json")["77"])

    def test_a_read_boost_basecamp_still_shows_after_removing_it_fails(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        real = self.stub.__call__

        def keep(cmd, **kw):
            if "delete" in cmd:  # answers ok, but the boost stays
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
            return real(cmd, **kw)
        s = self.sync()
        s.run = keep
        with self.assertRaises(RuntimeError) as e:
            s.remove_eyes(1)
        self.assertIn("still shows 👀 boost 5", str(e.exception))

    def test_a_reply_run_again_posts_nothing_removes_nothing_and_reports_a_read_boost_of_unknown_role(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        real = self.stub.__call__

        def keep(cmd, **kw):
            if "delete" in cmd:  # answers ok, but the boost stays
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
            return real(cmd, **kw)
        s = self.sync()
        s.run = keep
        with self.assertRaises(RuntimeError):
            s.reply(1, "Yes.")
        self.assertEqual(self.state("chats.json")["77"]["replied"], [1])
        deletes = len(self.stub.deletes())
        self.assertFalse(self.sync().reply(1, "Yes."))
        self.assertIn("Basecamp shows 👀 boost 5 on it, which may be the answer itself", self.log())
        self.assertEqual(len(self.stub.chat_posts()), 1)  # nothing posted again
        self.assertEqual(len(self.stub.deletes()), deletes)  # nothing removed
        self.stub.boosts["1"] = []
        self.assertFalse(self.sync().reply(1, "Yes."))
        self.assertIn("Basecamp shows no 👀 left on it", self.log())

    def test_an_older_boost_reply_answered_with_the_read_boost_is_reported_not_failed(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1]}})  # replied before "answers"
        self.assertFalse(self.sync().reply(1, boost="👀"))
        self.assertIn("may be the answer itself", self.log())
        self.assertEqual(self.stub.deletes(), [])

    def test_a_read_boost_left_beside_a_kept_answer_fails(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1], "answers": {"1": 9}}})
        with self.assertRaises(RuntimeError) as e:
            self.sync().reply(1, boost="Done")
        self.assertIn("already replied, but Basecamp still shows 👀 boost 5", str(e.exception))
        self.assertEqual(self.stub.deletes(), [])

    def test_a_retried_boost_reply_leaves_its_answer_and_reports_success(self):
        # Answered with the read boost itself: kept as the answer, then the reply is run again.
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.assertTrue(self.sync().reply(1, boost="👀"))
        self.assertEqual(self.state("chats.json")["77"]["answers"], {"1": 5})
        self.assertFalse(self.sync().reply(1, boost="👀"))
        self.assertEqual([b["id"] for b in self.stub.boosts["1"]], [5])
        self.assertEqual(self.stub.deletes(), [])

    def test_a_read_boost_removed_is_read_back_once(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.sync().remove_eyes(1)
        self.assertEqual(self.stub.boosts["1"], [])


if __name__ == "__main__":
    unittest.main()
