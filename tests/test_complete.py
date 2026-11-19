"""Tests for writes that cannot fail silently: the read-back and exit status of `todo complete` and `todo subtask`
(a decision to-do made outside `todo create` too, adopted with `todo track`), `reply` and `dismiss`, and the basecamp
CLI calls that raise on any answer that is not ok (an HTTP error, a usage error, empty or unreadable output, a non-zero
exit).

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, json, os, sys, unittest, unittest.mock
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
        # A home whose decision to-dos are made outside `todo create`, in a list of another project: "todos": {} is
        # enough to track and complete them
        self.cfg(todos={})
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


class TrackedElsewhere(Base):
    """A decision to-do made outside `todo create` (by hand, or by another tool) in another project: adopted with
    `todo track --project`, then completed with `todo complete`, which reads it back."""

    def track(self):
        code, _ = self.cli("todo", "track", "--key", "merge", "--todo", str(TODO), "--project", str(BUCKET))
        self.assertEqual(code, 0, self.log())
        self.assertEqual(self.state("todos.json")["merge"]["bucket"], BUCKET)

    def test_tracked_then_completed_in_its_own_project_and_verified(self):
        self.track()
        code, _ = self.cli("todo", "complete", "--todo", "merge")
        self.assertEqual(code, 0, self.log())
        self.assertEqual(self.made("comments create"), [])
        self.assertEqual(self.made("todos complete"), [["todos", "complete", str(TODO)]])
        self.assertEqual(self.stub.profiles[-1], "agent")
        reads = [i for i, c in enumerate(self.stub.calls) if c == ["api", "get", f"/buckets/{BUCKET}/todos/{TODO}.json"]]
        self.assertGreater(reads[-1], self.stub.calls.index(["todos", "complete", str(TODO)]))  # read back after
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])
        self.assertIn("completed", self.state("todos.json")["merge"])

    def test_a_completion_the_read_back_shows_open_fails_non_zero_and_running_it_again_completes_it(self):
        self.track()
        self.stub.ignore_complete = True
        code, out = self.cli("todo", "complete", "--todo", "merge")
        self.assertEqual(code, 1)
        self.assertIn("FAILED todo complete RuntimeError: todo complete merge: Basecamp still shows todo "
                      f"{TODO} open after the completion call", self.log())
        self.assertIn("FAILED", out)  # the agent that ran it sees the failure, not only sync.log
        self.assertNotIn("completed", self.state("todos.json")["merge"])
        self.poll()
        self.assertEqual(len(self.made("todos complete")), 1)  # the timer never retries it
        self.stub.ignore_complete = False
        self.assertEqual(self.cli("todo", "complete", "--todo", "merge")[0], 0)
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])

    def test_a_todo_already_completed_gets_no_write(self):
        self.stub.todo_records[str(TODO)]["completed"] = True
        self.track()
        self.assertEqual(self.cli("todo", "complete", "--todo", "merge")[0], 0, self.log())
        self.assertIn("already completed in Basecamp", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_a_todo_basecamp_lists_an_open_subtask_of_is_refused_naming_it(self):
        self.track()
        self.stub.subtask_lists[str(TODO)] = [{"id": 7, "title": "Merge the fix?"}, {"id": 8, "title": "Ship it today?"}]
        self.stub.subtasks_done.add("8")
        code, _ = self.cli("todo", "complete", "--todo", "merge")
        self.assertEqual(code, 1)
        self.assertIn("Basecamp shows its subtask 7 ('Merge the fix?') open, so the to-do was not completed: finish "
                      "that subtask first", self.log())
        self.assertEqual(self.made("todos complete"), [])
        self.assertNotIn("completed", self.state("todos.json")["merge"])
        self.stub.subtasks_done.add("7")
        self.assertEqual(self.cli("todo", "complete", "--todo", "merge")[0], 0, self.log())
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])

    def test_refused_signed_in_as_anyone_but_the_agent(self):
        self.track()
        self.stub.me = CAPTAIN
        self.assertEqual(self.cli("todo", "complete", "--todo", "merge")[0], 1)
        self.assertEqual(self.made("todos complete"), [])
        self.assertIn("FAILED wrong login", self.log())

    def test_dry_run_writes_nothing(self):
        self.track()
        code, _ = self.cli("todo", "complete", "--todo", "merge", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.made("todos complete"), [])
        self.assertNotIn("completed", self.state("todos.json")["merge"])

    def test_there_is_no_complete_command(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            self.cli("complete", "--todo", REF)
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
        self.assertIn("no longer shows the request active and assigned to you", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_an_id_names_the_open_entry_when_an_older_one_tracks_the_same_todo(self):
        self.side("todos.json", {"a-old": {"todo": 77, "on": True, "completed": "2026-11-18T10:00:00Z",
                                           "subtasks": [{"text": "Old?", "done": "t", "id": 7, "decider": CAPTAIN}]},
                                 "z-current": {"todo": 77, "on": True,
                                               "subtasks": [{"text": "New?", "done": None, "id": 8, "decider": CAPTAIN}]}})
        self.stub.subtasks_done.add("7")
        self.stub.subtask_lists["77"] = [{"id": 7, "title": "Old?"}, {"id": 8, "title": "New?"}]
        self.assertEqual(self.cli("todo", "complete", "--todo", "77")[0], 0, self.log())
        self.assertEqual(self.made("subtasks complete"), [["subtasks", "complete", "8"]])
        self.assertIn("completed", self.state("todos.json")["z-current"])

    def test_a_step_deleted_in_basecamp_is_skipped_and_the_live_one_completed(self):
        self.side("todos.json", {"d": {"todo": 77, "on": True, "subtasks": [
            {"text": "Old?", "done": "t", "id": 7, "decider": CAPTAIN},
            {"text": "New?", "done": None, "id": 8, "decider": CAPTAIN}]}})
        self.stub.subtask_lists["77"] = [{"id": 8, "title": "New?"}]  # step 7 deleted in Basecamp
        self.assertEqual(self.cli("todo", "complete", "--todo", "d")[0], 0, self.log())
        self.assertEqual(self.made("subtasks complete"), [["subtasks", "complete", "8"]])

    def test_a_repeating_request_reopened_as_its_next_occurrence_is_not_completed(self):
        self.stub.todo_records["42"] = {"id": 42, "content": "Do it", "completed": False, "status": "active",
                                        "assignees": [{"id": ACTING}], "due_on": "2026-11-26",
                                        "repetition_schedule": {"frequency": "weekly"}}
        self.side("todos.json", {"request-42": {"todo": 42, "request": CAPTAIN, "completed": "2026-11-19T10:00:00Z",
                                                "due_on": "2026-11-19", "repeats": {"frequency": "weekly"}}})
        self.assertEqual(self.cli("todo", "complete", "--todo", "request-42")[0], 1)
        self.assertIn("a schedule the sync has not read yet", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_an_id_names_the_first_entry_for_todo_show(self):
        self.side("todos.json", {"a-old": {"todo": 77, "completed": "2026-11-18T10:00:00Z"}, "z-current": {"todo": 77}})
        code, out = self.cli("todo", "show", "--todo", "77")
        self.assertEqual(code, 0, self.log())
        self.assertEqual(json.loads(out)["key"], "a-old")

    def test_a_request_is_read_once_before_it_is_completed(self):
        self.stub.todo_records["42"] = {"id": 42, "content": "Do it", "completed": False, "status": "active",
                                        "assignees": [{"id": ACTING}]}
        self.side("todos.json", {"request-42": {"todo": 42, "request": CAPTAIN}})
        self.assertEqual(self.cli("todo", "complete", "--todo", "request-42")[0], 0, self.log())
        write = self.stub.calls.index(["todos", "complete", "42"])
        reads = [c for c in self.stub.calls[:write] if c[:2] == ["api", "get"] and c[2].endswith("/todos/42.json")]
        self.assertEqual(len(reads), 1)

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

    def test_a_subtask_the_owner_added_in_basecamp_holds_the_todo_open(self):
        self.stub.subtask_lists[str(self.tid)].append({"id": 9999, "title": "And the docs?"})
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "1")[0], 0)
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-x", "--number", "2")[0], 0, self.log())
        self.assertIn(f"Basecamp shows subtask 9999 open on todo {self.tid}, so the to-do stays open", self.log())
        self.assertEqual(self.made("todos complete"), [])
        self.assertNotIn("completed", self.todos()["ta-x"])
        self.assertEqual(self.cli("todo", "complete", "--todo", "ta-x")[0], 1)  # refused too, naming it
        self.assertIn("Basecamp shows its subtask 9999 ('And the docs?') open", self.log())
        self.assertEqual(self.made("todos complete"), [])

    def test_a_lone_subtask_with_one_the_owner_added_holds_the_todo_open(self):
        # tracked with one subtask of its own (todo track, then a `decide`-free edit), so it has no saved sibling
        self.stub.todo_records["77"] = {"id": 77, "content": "Merge?", "completed": False, "status": "active"}
        self.side("todos.json", {"ta-y": {"todo": 77, "subtasks": [{"id": 7, "text": "Merge?", "done": None}]}})
        self.stub.subtask_lists["77"] = [{"id": 7, "title": "Merge?"}, {"id": 9999, "title": "And the docs?"}]
        self.assertEqual(self.cli("todo", "subtask", "--todo", "ta-y", "--number", "1")[0], 0, self.log())
        self.assertEqual(self.made("subtasks complete"), [["subtasks", "complete", "7"]])
        self.assertEqual(self.made("todos complete"), [])


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

    def keep_boosts(self):
        """A Tools whose boost deletes answer ok but leave the boost, as a silent failure would."""
        real = self.stub.__call__

        def keep(cmd, **kw):
            if "delete" in cmd:  # answers ok, but the boost stays
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
            return real(cmd, **kw)
        s = self.sync()
        s.run = keep
        return s

    def test_a_text_reply_run_again_posts_nothing_and_takes_the_read_boost_left_off(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        with self.assertRaises(RuntimeError):
            self.keep_boosts().reply(1, "Yes.")
        self.assertEqual(self.state("chats.json")["77"]["replied"], [1])
        self.assertEqual(self.state("chats.json")["77"]["answers"], {"1": None})  # answered with a line
        with self.assertRaises(RuntimeError) as e:  # the removal fails again: still loud
            self.keep_boosts().reply(1, "Yes.")
        self.assertIn("still shows 👀 boost 5 after removing it", str(e.exception))
        self.assertFalse(self.sync().reply(1, "Yes."))
        self.assertIn("taking the 👀 left on it off again", self.log())
        self.assertEqual(self.stub.boosts["1"], [])
        self.assertEqual(len(self.stub.chat_posts()), 1)  # nothing posted again
        self.assertFalse(self.sync().reply(1, "Yes."))
        self.assertIn("Basecamp shows no 👀 left on it", self.log())

    def test_a_text_reply_run_again_through_the_cli_exits_0_once_the_read_boost_is_off(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1], "answers": {"1": None}}})
        body = os.path.join(self.tmp, "body.md")
        with open(body, "w") as f:
            f.write("Yes.")
        self.assertEqual(self.cli("reply", "--recording", "1", "--body-file", body)[0], 0, self.log())
        self.assertEqual(self.stub.boosts["1"], [])
        self.assertEqual(self.stub.chat_posts(), [])

    def test_a_dry_run_again_takes_nothing_off(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1], "answers": {"1": None}}})
        self.assertFalse(self.sync(dry=True).reply(1, "Yes."))
        self.assertIn("dry reply 1: already replied; take 👀 boost 5 off it", self.log())
        self.assertEqual(self.stub.deletes(), [])

    def test_an_older_boost_reply_answered_with_the_read_boost_is_reported_not_failed(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1]}})  # replied before "answers"
        self.assertFalse(self.sync().reply(1, boost="👀"))
        self.assertIn("may be the answer itself", self.log())
        self.assertEqual(self.stub.deletes(), [])

    def test_a_read_boost_left_beside_a_kept_answer_is_taken_off_and_the_answer_kept(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"},
                                 {"id": 9, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1], "answers": {"1": 9}}})
        self.assertFalse(self.sync().reply(1, boost="Done"))
        self.assertEqual(self.stub.deletes(), ["/buckets/22222222/boosts/5.json"])
        self.assertEqual([b["id"] for b in self.stub.boosts["1"]], [9])

    def test_a_text_reply_replaces_the_boost_an_earlier_reply_kept(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.side("chats.json", {"77": {"lines": [1], "replied": [1], "answers": {"1": 5}}})
        with self.assertRaises(RuntimeError):
            self.keep_boosts().reply(1, "Yes.", again=True)
        self.assertEqual(self.state("chats.json")["77"]["answers"], {"1": None})
        self.assertFalse(self.sync().reply(1, "Yes."))
        self.assertEqual(self.stub.boosts["1"], [])  # boost 5 is no longer the answer, so it is taken off

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
