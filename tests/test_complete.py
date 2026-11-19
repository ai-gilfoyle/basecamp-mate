"""Tests for writes that cannot fail silently: `sync.py complete` (a decision to-do in a list "decision_lists" names,
completed and read back, retried by the timer until Basecamp shows it completed), the read-back and exit status of
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
        self.fail_get = {}  # `api get` path -> the CLI's failed answer for it (a timeout, not_found)

    def __call__(self, cmd, **kw):
        args = cmd[3:-3] if cmd[0] == "basecamp" else []
        core = args[2:] if args[:1] == ["-P"] else args
        if core[:2] == ["api", "get"] and core[2] in self.fail_get:
            self.calls.append(core)
            return SimpleNamespace(stdout=json.dumps(self.fail_get[core[2]]), stderr="", returncode=1)
        if core[:2] == ["api", "get"] and core[2].startswith("/projects/") and core[2].count("/") == 2:
            self.calls.append(core)  # the project itself, readable
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": {"id": int(core[2][10:-5])}}), stderr="",
                                   returncode=0)
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
        self.cfg(drop=("todos", "inbox"), decision_lists=[LIST])  # inbox delivery off: the failure note still arrives
        self.stub.todo_records[str(TODO)] = {"id": TODO, "content": "Merge?", "completed": False, "status": "active",
                                             "parent": {"id": LIST, "type": "Todolist"}}

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

    def failed(self):
        """The completion-failed notes delivered to the firstmate inbox (fm-inbox.sh): (request id, body)."""
        return [(n[0], n[1]) for n in self.stub.notes if n[0].startswith("basecamp-completion-failed-")]


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
        self.assertEqual(self.state("pending-completions.json"), {})

    def test_a_completion_the_read_back_shows_open_fails_non_zero_and_is_kept_to_retry(self):
        self.stub.ignore_complete = True
        code, out = self.cli("complete", "--todo", REF)
        self.assertEqual(code, 1)
        self.assertIn(f"FAILED complete {REF} RuntimeError: complete {REF}: Basecamp still shows todo {TODO} open after "
                      "the completion call", self.log())
        self.assertIn("FAILED", out)  # the agent that ran it sees the failure, not only sync.log
        p = self.state("pending-completions.json")[REF]
        self.assertEqual(p["title"], "Merge?")
        self.assertIn("still shows todo", p["error"])
        self.assertNotIn("noted", p)

    def test_the_timer_retries_a_failed_completion_until_basecamp_shows_it_completed(self):
        self.stub.ignore_complete = True
        self.assertEqual(self.cli("complete", "--todo", REF)[0], 1)
        self.stub.ignore_complete = False
        self.poll()
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])
        self.assertEqual(self.state("pending-completions.json"), {})
        self.assertEqual(self.failed(), [])
        n = len(self.made("todos complete"))
        self.poll()
        self.assertEqual(len(self.made("todos complete")), n)  # nothing left to retry

    def test_a_retry_that_fails_again_tells_the_agent_once_and_keeps_retrying(self):
        self.stub.ignore_complete = True
        self.cli("complete", "--todo", REF)
        self.poll()
        self.poll()
        [(rid, body)] = self.failed()
        self.assertEqual(rid, f"basecamp-completion-failed-{BUCKET}-{TODO}")
        self.assertIn("'Merge?'", body)
        self.assertIn("is still open", body)
        self.assertTrue(self.state("pending-completions.json")[REF]["noted"])
        self.assertEqual(len(self.made("todos complete")), 3)  # the command and both runs
        self.assertIn(f"Commands: sync.py complete --todo {REF}.", body)
        self.stub.ignore_complete = False
        self.poll()
        self.assertEqual(self.state("pending-completions.json"), {})
        self.assertEqual(len(self.failed()), 1)

    def test_a_retry_of_a_todo_no_longer_in_a_decision_list_is_told_once_and_dropped(self):
        self.stub.ignore_complete = True
        self.cli("complete", "--todo", REF)
        self.cfg(decision_lists=[])
        self.poll()
        [(_, body)] = self.failed()
        self.assertIn("the timer no longer retries it", body)
        self.assertEqual(self.state("pending-completions.json"), {})

    def test_a_failed_first_read_is_kept_and_its_note_says_completion_could_not_be_verified(self):
        get = f"/buckets/{BUCKET}/todos/{TODO}.json"
        self.stub.fail_get[get] = {"ok": False, "error": "timed out", "code": "network"}
        self.assertEqual(self.cli("complete", "--todo", REF)[0], 1)
        self.assertTrue(self.state("pending-completions.json")[REF]["lookup"])
        self.poll()
        [(_, body)] = self.failed()
        self.assertIn("its completion could not be verified", body)
        del self.stub.fail_get[get]
        self.poll()
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])
        self.assertEqual(self.state("pending-completions.json"), {})

    def test_not_found_is_dropped_only_the_second_time_in_a_row_while_its_project_reads(self):
        get = f"/buckets/{BUCKET}/todos/{TODO}.json"
        self.stub.fail_get[get] = {"ok": False, "error": "Resource not found", "code": "not_found"}
        self.stub.fail_get[f"/projects/{BUCKET}.json"] = {"ok": False, "error": "forbidden", "code": "forbidden"}
        self.cli("complete", "--todo", REF)
        self.poll()
        self.assertIn(REF, self.state("pending-completions.json"))  # the project did not read: maybe out of reach
        del self.stub.fail_get[f"/projects/{BUCKET}.json"]
        self.poll()
        self.assertIn(REF, self.state("pending-completions.json"))
        self.poll()
        self.assertEqual(self.state("pending-completions.json"), {})

    def test_a_run_retries_one_pending_todo_the_one_tried_least_recently(self):
        other = TODO + 1
        self.stub.todo_records[str(other)] = dict(self.stub.todo_records[str(TODO)], id=other)
        self.stub.ignore_complete = True
        self.cli("complete", "--todo", REF)
        self.cli("complete", "--todo", f"{BUCKET}/{other}")
        self.poll()
        self.poll()
        self.assertEqual(self.made("todos complete")[2:], [["todos", "complete", str(TODO)], ["todos", "complete", str(other)]])

    def test_a_note_that_cannot_be_delivered_is_logged_failed_once_and_tried_again(self):
        self.stub.ignore_complete = True
        self.cli("complete", "--todo", REF)
        self.stub.inbox_exit = 1
        self.poll()
        self.poll()
        self.assertEqual(self.log().count(f"FAILED complete {REF}: still not completed, and the note"), 1)
        self.stub.inbox_exit = 0
        self.poll()
        self.assertEqual(len(self.failed()), 1)

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
        self.assertEqual(self.state("pending-completions.json"), {})

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
        self.assertIn(REF, self.state("pending-completions.json"))

    def test_dry_run_writes_nothing(self):
        code, _ = self.cli("complete", "--todo", REF, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.made("todos complete"), [])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "pending-completions.json")))


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

    def test_a_reply_that_posted_but_left_the_read_boost_is_finished_by_running_it_again(self):
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
        self.assertEqual(len(self.stub.chat_posts()), 1)
        self.assertFalse(self.sync().reply(1, "Yes."))  # posts nothing
        self.assertEqual(len(self.stub.chat_posts()), 1)
        self.assertEqual(self.stub.boosts["1"], [])

    def test_a_retried_boost_reply_never_removes_its_answer(self):
        # Answered with the read boost itself: kept as the answer, then the reply is run again.
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.assertTrue(self.sync().reply(1, boost="👀"))
        self.assertEqual(self.state("chats.json")["77"]["answers"], {"1": 5})
        self.assertFalse(self.sync().reply(1, boost="👀"))
        self.assertEqual([b["id"] for b in self.stub.boosts["1"]], [5])
        self.assertEqual(self.stub.deletes(), [])

    def test_an_older_reply_without_its_answer_kept_leaves_a_boost_given_as_the_answer(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1]}})
        self.assertFalse(self.sync().reply(1, boost="👀"))
        self.assertEqual(self.stub.deletes(), [])

    def test_a_retried_reply_whose_login_may_not_post_fails_non_zero(self):
        self.side("chats.json", {"77": {"lines": [1], "replied": [1]}})
        self.cfg(drop=("profile",))
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli(["reply", "--recording", "1", "--body-file", __file__, "--home", self.home, "--config",
                             os.path.join(self.cfgdir, "config.json")], runner=self.stub)
        self.assertEqual(code, 1)
        self.assertIn("the login may not post, so the read boost was not checked", self.log())

    def test_a_read_boost_removed_is_read_back_once(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.sync().remove_eyes(1)
        self.assertEqual(self.stub.boosts["1"], [])


if __name__ == "__main__":
    unittest.main()
