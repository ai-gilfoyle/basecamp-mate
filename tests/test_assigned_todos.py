"""Tests for the assigned-todos behavior: to-dos the listened-to people assign to the agent's login, as requests.

/my/assignments.json, the to-dos, their comments and boosts and the notifications are stubbed; nothing touches the network.
"""
import json, os, sys, unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN  # noqa: E402
from test_tools import boost, comment  # noqa: E402
from test_notifications import NotifStub, NotifBase, PROJECT, note  # noqa: E402
import behaviors  # noqa: E402
import sync  # noqa: E402

PARTNER, STRANGER = 55555555, 66666666
OTHER = 44444444  # another project of the account
EYES = "\U0001F440"


def todo(id, by=CAPTAIN, to=(ACTING,), title="Rotate the API keys", description="<div>Both <b>staging</b> and prod.</div>",
         bucket=PROJECT):
    return {"id": id, "content": title, "description": description, "completed": False, "status": "active",
            "creator": {"id": by, "name": f"person-{by}"}, "assignees": [{"id": p, "name": f"person-{p}"} for p in to],
            "updated_at": "2026-10-04T12:00:00Z", "bucket": {"id": bucket, "name": f"Project {bucket}", "type": "Project"}}


class AssignStub(NotifStub):
    """Adds /my/assignments.json, answered from the stubbed to-dos open and assigned to the acting user, and keeps the
    project (-p) each call ran on."""

    def __init__(self):
        super().__init__()
        self.on = []  # (call, its -p project)

    def __call__(self, cmd, **kw):
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            core = args[2:] if args[:1] == ["-P"] else args
            self.on.append((core, cmd[-2]))
            if core[:3] == ["api", "get", "/my/assignments.json"]:
                self.calls.append(core)
                mine = [{"id": t["id"], "type": "todo", "content": t["content"], "completed": False,
                         "bucket": {"id": t["bucket"]["id"], "name": t["bucket"]["name"]}}
                        for t in self.todo_records.values()
                        if not t.get("completed") and self.me in [a["id"] for a in t.get("assignees", [])]]
                data = {"priorities": mine[:1], "non_priorities": mine[1:]}
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def paths(self, verb="get"):
        return [c[2] for c in self.calls if c[:2] == ["api", verb]]

    def fetched(self, tid, bucket=PROJECT):
        return self.paths().count(f"/buckets/{bucket}/todos/{tid}.json")

    def projects(self, verb):
        """The -p project of each `verb` call (e.g. "comments list")."""
        return [p for c, p in self.on if c[:2] == verb.split()]


class AssignBase(NotifBase):
    def setUp(self):
        super().setUp()
        self.stub = AssignStub()
        self.stub.lines = {"77": []}
        self.cfg(assigned_todos={}, people=[PARTNER])

    def add(self, t):
        self.stub.todo_records[str(t["id"])] = t
        return t

    def edit(self, tid, **kw):
        self.stub.todo_records[str(tid)].update(kw)

    def request(self, tid=42):
        return self.todos()[f"request-{tid}"]


class Sweep(AssignBase):
    def test_a_todo_the_captain_assigns_is_recorded_once_acknowledged_and_tracked(self):
        self.add(todo(42))
        self.poll()
        self.poll()
        [rec] = self.pending()
        self.assertEqual({k: rec[k] for k in ("kind", "key", "todo", "n", "reopened", "title", "text", "captain")},
                         {"kind": "todo-request", "key": "request-42", "todo": 42, "n": 1, "reopened": False,
                          "title": "Rotate the API keys", "text": "Both staging and prod.", "captain": True})
        self.assertEqual(rec["author"], {"id": CAPTAIN, "name": f"person-{CAPTAIN}"})
        self.assertEqual(rec["url"], "https://x/todos/42")
        self.assertEqual(self.stub.posted(), [("42", EYES)])
        req = self.request()
        self.assertEqual((req["todo"], req["cursor"], req["request"]["captain"]), (42, 0, True))

    def test_another_listened_to_person_is_recorded_as_not_the_captain(self):
        self.add(todo(42, by=PARTNER))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["captain"], rec["author"]["id"]), ("todo-request", False, PARTNER))

    def test_a_todo_assigned_by_someone_not_listened_to_is_ignored_and_fetched_once(self):
        self.add(todo(42, by=STRANGER))
        self.poll()
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.fetched(42), 1)
        self.assertNotIn("request-42", self.todos())

    def test_todos_assigned_to_anyone_else_or_in_other_projects_are_never_read(self):
        self.add(todo(42, to=(CAPTAIN,)))
        self.add(todo(43, bucket=OTHER))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual((self.stub.fetched(42), self.stub.fetched(43)), (0, 0))

    def test_comments_and_boosts_arrive_as_part_of_the_request(self):
        self.add(todo(42))
        self.stub.comments["42"] = [comment(5, content="<p>Prod first, please</p>")]
        self.poll()
        self.stub.comments["42"].append(comment(6, content="<p>Done yet?</p>"))
        self.stub.boosts["42"] = [boost(7)]
        self.poll()
        kinds = [(r["kind"], r.get("comment") or r.get("boost")) for r in self.pending()]
        # The comment already on it when it was assigned is part of the request too. A boost on the owner's own to-do is
        # not aimed at the agent (only a decision to-do's are decision feedback): skipped.
        self.assertEqual(kinds, [("todo-request", None), ("todo-comment", 5), ("todo-comment", 6)])
        self.assertIn("skipped boost 7", open(os.path.join(self.cfgdir, "sync.log")).read())
        self.assertTrue(all(r.get("request") and r["title"] == "Rotate the API keys" for r in self.pending()[1:]))
        self.assertTrue(self.sync().reply(6, "Prod is done."))

    def test_an_edit_is_an_update_recorded_once(self):
        self.add(todo(42))
        self.poll()
        self.edit(42, description="<div>Prod only.</div>")
        self.poll()
        self.poll()
        upd = self.pending()[-1]
        self.assertEqual(len(self.pending()), 2)
        self.assertEqual((upd["kind"], upd["text"], upd["author"]), ("todo-request-update", "Prod only.", None))

    def test_the_owner_completing_it_closes_the_request(self):
        self.add(todo(42))
        self.poll()
        self.edit(42, completed=True, completion={"creator": {"id": CAPTAIN, "name": "Cap"}, "created_at": "tc"})
        self.poll()
        self.stub.comments["42"] = [comment(9)]
        self.poll()
        closed = self.pending()[-1]
        self.assertEqual(len(self.pending()), 2)
        self.assertEqual((closed["kind"], closed["reason"], closed["captain"]), ("todo-request-closed", "completed", True))
        self.assertTrue(self.request()["completed"])
        self.assertFalse(self.sync().todo_complete("request-42"))
        self.assertEqual(self.stub.made("todos complete"), [])

    def test_unassigned_closes_it_and_assigned_again_is_a_new_request(self):
        self.add(todo(42))
        self.poll()
        self.edit(42, assignees=[{"id": CAPTAIN}])
        self.poll()
        self.assertEqual(self.pending()[-1]["reason"], "unassigned")
        self.assertFalse(self.sync().todo_complete(42))
        self.edit(42, assignees=[{"id": ACTING}])
        self.poll()
        again = self.pending()[-1]
        self.assertEqual((again["kind"], again["n"], again["reopened"]), ("todo-request", 2, True))
        self.assertEqual(self.stub.posted(), [("42", EYES)])  # the 👀 is already there

    def test_the_agent_completes_it_when_the_work_is_done_even_without_decision_todos(self):
        self.cfg(drop=("todos",))
        self.add(todo(42))
        self.poll()
        self.assertTrue(self.sync().todo_complete("42"))
        self.assertEqual(self.stub.made("todos complete"), [["todos", "complete", "42"]])
        self.poll()
        self.assertEqual([r["kind"] for r in self.pending()], ["todo-request"])
        with self.assertRaises(RuntimeError):
            self.create()

    def test_reads_at_most_limit_new_todos_a_sweep(self):
        self.cfg(assigned_todos={"limit": 2})
        for i in (41, 42, 43):
            self.add(todo(i))
        self.poll()
        self.assertEqual(len(self.pending()), 2)
        self.poll()
        self.assertEqual(len(self.pending()), 3)

    def test_refused_without_a_profile_or_as_the_owner(self):
        self.add(todo(42))
        self.stub.me = CAPTAIN
        self.poll()
        self.cfg(drop=("profile",))
        self.poll()
        self.assertNotIn("/my/assignments.json", self.stub.paths())
        self.assertEqual(self.pending(), [])

    def test_dry_run_records_and_boosts_nothing(self):
        self.add(todo(42))
        self.poll(dry=True)
        self.assertEqual((self.pending(), self.stub.posted(), self.todos()), ([], [], {}))

    def test_off_makes_no_calls(self):
        self.cfg(assigned_todos=False)
        self.add(todo(42))
        self.poll()
        self.assertNotIn("/my/assignments.json", self.stub.paths())

    def test_malformed_config_refused(self):
        for bad in ([], {"limit": 0}, {"scope": "everywhere"}):
            self.cfg(assigned_todos=bad)
            with self.assertRaises(ValueError):
                self.sync()


class Notifications(AssignBase):
    def setUp(self):
        super().setUp()
        self.cfg(inbox={})
        self.poll()
        self.stub.calls.clear()

    def notify(self, *items):
        self.stub.readings = {"unreads": list(items), "reads": []}
        self.sync().behaviors["notifications"].run()

    def test_the_captain_assigning_a_todo_is_a_request_in_the_same_run(self):
        self.add(todo(42, by=STRANGER))  # created by someone else, assigned by the captain
        self.notify(note(10, "Assignment", thread=42, title="Assigned you: Rotate the API keys"))
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["author"]["id"], rec["captain"]), ("todo-request", CAPTAIN, True))
        self.assertEqual([n[0] for n in self.stub.notes], ["basecamp-todo-request-42-1"])
        self.assertEqual(self.stub.posted(), [("42", EYES)])
        self.assertEqual(self.stub.marked, [["10"]])

    def test_its_comments_and_completion_are_recorded_with_who_did_them(self):
        self.add(todo(42))
        self.notify(note(10, "Assignment", thread=42))
        self.stub.comments["42"] = [comment(5)]
        self.notify(note(10, "Assignment", thread=42), note(11, thread=42, anchor=5))
        self.edit(42, completed=True)
        self.notify(note(10, "Assignment", thread=42), note(11, thread=42, anchor=5), note(12, "Completion", thread=42))
        self.assertEqual([(r["kind"], (r["author"] or {}).get("id")) for r in self.pending()],
                         [("todo-request", CAPTAIN), ("todo-comment", CAPTAIN), ("todo-request-closed", CAPTAIN)])
        self.assertEqual(len(self.stub.notes), 3)

    def test_a_completion_of_a_todo_assigned_to_someone_else_is_ignored_not_unmonitored(self):
        self.add(todo(42, to=(CAPTAIN,)))
        self.notify(note(10, "Completion", thread=42), note(11, "Assignment", thread=43, who=STRANGER))
        self.assertEqual(self.pending(), [])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "unmonitored.json")))
        self.assertEqual(self.stub.marked, [["10", "11"]])

    def test_off_an_assignment_is_unmonitored(self):
        self.cfg(assigned_todos=False)
        self.add(todo(42))
        self.notify(note(10, "Assignment", thread=42, title="Assigned you: Rotate the API keys"))
        self.assertEqual([(r["kind"], r["key"]) for r in self.pending()], [("unmonitored", "todo.assignment_changed/Todo")])

    def test_project_scope_leaves_other_projects_assignments_alone(self):
        self.add(todo(42, bucket=OTHER))
        self.notify(note(10, "Assignment", thread=42, bucket=OTHER))
        self.assertEqual((self.pending(), self.stub.marked, self.stub.fetched(42, OTHER)), ([], [], 0))


class AccountWide(AssignBase):
    """"scope": "account": to-dos assigned to the agent in any project of the account are requests, each with its project."""

    def setUp(self):
        super().setUp()
        self.cfg(assigned_todos={"scope": "account"})

    def test_a_todo_in_another_project_is_a_request_carrying_its_project(self):
        self.add(todo(42, bucket=OTHER))
        self.add(todo(43))
        self.poll()
        self.poll()
        recs = {r["todo"]: r for r in self.pending()}
        self.assertEqual(sorted(recs), [42, 43])
        self.assertEqual(recs[42]["project"], {"id": OTHER, "name": f"Project {OTHER}"})
        self.assertEqual(recs[43]["project"], {"id": int(PROJECT), "name": f"Project {PROJECT}"})
        # Read once to record it, then each sweep's edit check: always in its own project.
        self.assertEqual((self.stub.fetched(42, OTHER), self.stub.fetched(42)), (3, 0))
        self.assertIn(f"/buckets/{OTHER}/recordings/42/boosts.json", self.stub.paths("post"))
        self.assertEqual((self.request(42)["bucket"], self.request(42)["project_name"]), (OTHER, f"Project {OTHER}"))

    def test_its_comments_boosts_replies_and_completion_go_to_its_project(self):
        self.add(todo(42, bucket=OTHER))
        self.poll()
        self.stub.comments["42"] = [comment(6, content="<p>Done yet?</p>")]
        self.stub.boosts["42"] = [boost(7)]
        self.poll()
        self.assertEqual([(r["kind"], r["project"]["id"]) for r in self.pending()],
                         [("todo-request", OTHER), ("todo-comment", OTHER)])  # the boost on its to-do is not the agent's
        self.assertEqual(set(self.stub.projects("comments list")), {str(OTHER)})
        self.assertTrue(self.sync().reply(6, "Almost."))
        self.assertTrue(self.sync().todo_comment("request-42", "Started."))
        self.assertTrue(self.sync().todo_complete("request-42"))
        self.assertEqual(self.stub.projects("comments create") + self.stub.projects("todos complete"), [str(OTHER)] * 3)
        self.assertIn(f"/buckets/{OTHER}/recordings/6/boosts.json", self.stub.paths("post"))  # the 👀 on the question
        self.assertTrue(any(p.startswith(f"/buckets/{OTHER}/boosts/") for p in self.stub.paths("delete")))

    def test_its_edit_and_closing_are_read_in_its_project(self):
        self.add(todo(42, bucket=OTHER))
        self.poll()
        self.edit(42, description="<div>Prod only.</div>")
        self.poll()
        self.edit(42, assignees=[{"id": CAPTAIN}])
        self.poll()
        self.assertEqual([(r["kind"], r.get("reason"), r["project"]["id"]) for r in self.pending()],
                         [("todo-request", None, OTHER), ("todo-request-update", None, OTHER),
                          ("todo-request-closed", "unassigned", OTHER)])

    def test_the_project_scope_default_is_unchanged(self):
        self.cfg(assigned_todos={})
        self.add(todo(42, bucket=OTHER))
        self.poll()
        self.assertEqual((self.pending(), self.stub.fetched(42, OTHER)), ([], 0))


class AccountWideNotifications(AssignBase):
    def setUp(self):
        super().setUp()
        self.cfg(assigned_todos={"scope": "account"}, inbox={})
        self.poll()
        self.stub.calls.clear()

    def notify(self, *items):
        self.stub.readings = {"unreads": list(items), "reads": []}
        self.sync().behaviors["notifications"].run()

    def test_a_todo_assigned_in_another_project_is_a_request_in_the_same_run(self):
        self.add(todo(42, by=STRANGER, bucket=OTHER))
        self.notify(note(10, "Assignment", thread=42, bucket=OTHER))
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["author"]["id"], rec["project"]["id"]), ("todo-request", CAPTAIN, OTHER))
        [(rid, body, _, _)] = self.stub.notes
        self.assertEqual(rid, "basecamp-todo-request-42-1")
        self.assertIn(f"in project 'Project {OTHER}' ({OTHER})", body)
        self.assertIn("it is in another project; comments and completion go there", body)
        self.assertEqual(self.stub.marked, [["10"]])

    def test_comments_and_closing_on_it_follow_it(self):
        self.add(todo(42, bucket=OTHER))
        self.notify(note(10, "Assignment", thread=42, bucket=OTHER))
        self.stub.comments["42"] = [comment(5)]
        self.notify(note(11, thread=42, anchor=5, bucket=OTHER))
        self.edit(42, completed=True)
        self.notify(note(12, "Completion", thread=42, bucket=OTHER))
        self.assertEqual([r["kind"] for r in self.pending()], ["todo-request", "todo-comment", "todo-request-closed"])
        self.assertEqual(set(self.stub.projects("comments list")), {str(OTHER)})
        self.assertEqual(len(self.stub.notes), 3)

    def test_other_projects_items_on_untracked_todos_read_nothing(self):
        self.add(todo(42, to=(CAPTAIN,), bucket=OTHER))
        self.notify(note(11, thread=42, anchor=5, bucket=OTHER), note(12, "Mention", thread=42, anchor=6, bucket=OTHER))
        self.assertEqual((self.pending(), self.stub.marked), ([], []))
        self.assertEqual(self.stub.projects("comments list"), [])


class Note(unittest.TestCase):
    def test_request_notes(self):
        rid, body = behaviors.inbox_note({"kind": "todo-request", "key": "request-42", "todo": 42, "n": 1,
                                          "title": "Rotate keys", "text": "Prod too.", "url": "https://x/todos/42",
                                          "captain": True}, "1", "2")
        self.assertEqual(rid, "basecamp-todo-request-42-1")
        self.assertIn("assigned to you by the captain, a request: 'Rotate keys'", body)
        self.assertIn("captain work", body)
        self.assertIn("sync.py todo complete --todo request-42", body)
        _, body = behaviors.inbox_note({"kind": "todo-request", "key": "request-42", "todo": 42, "captain": False,
                                        "author": {"id": PARTNER, "name": "Pat"}}, "1", "2")
        self.assertIn("by Pat (not the captain)", body)
        self.assertIn("never authorizes a decision", body)
        _, body = behaviors.inbox_note({"kind": "todo-comment", "request": True, "key": "request-42", "title": "Rotate keys",
                                        "comment": 5, "captain": True}, "1", "2")
        self.assertIn("on to-do request 'Rotate keys' (request-42)", body)
        self.assertIn("part of the request", body)
        _, body = behaviors.inbox_note({"kind": "boost", "surface": "todo", "request": True, "key": "request-42",
                                        "title": "Rotate keys", "boost": 8}, "1", "2")
        self.assertIn("on to-do request 'Rotate keys'", body)
        self.assertNotIn("settles the decision", body)

    def test_a_request_in_the_configured_project_does_not_name_it(self):
        _, body = behaviors.inbox_note({"kind": "todo-request", "key": "request-42", "todo": 42, "captain": True,
                                        "project": {"id": 2, "name": "Home"}}, "1", "2")
        self.assertNotIn("in project", body)
        _, body = behaviors.inbox_note({"kind": "todo-request-closed", "key": "request-42", "todo": 42, "reason": "trashed",
                                        "captain": None, "author": None, "project": {"id": 3, "name": "Ops"}}, "1", "2")
        self.assertIn("(request-42) in project 'Ops' (3) trashed", body)

    def test_update_and_closed_notes(self):
        rid, body = behaviors.inbox_note({"kind": "todo-request-update", "key": "request-42", "todo": 42, "title": "Rotate",
                                          "text": "Prod only.", "author": None, "captain": None}, "1", "2")
        self.assertTrue(rid.startswith("basecamp-todo-request-update-42-1-"))
        self.assertIn("edited; it now reads", body)
        rid, body = behaviors.inbox_note({"kind": "todo-request-closed", "key": "request-42", "todo": 42, "title": "Rotate",
                                          "reason": "completed", "author": {"id": CAPTAIN, "name": "Cap"},
                                          "captain": True}, "1", "2")
        self.assertEqual(rid, "basecamp-todo-request-closed-42-1-completed")
        self.assertIn("completed by the captain", body)
        self.assertIn("the request is closed", body)


WEEKLY = {"frequency": "every_week", "days": [0], "hour": 0, "minute": 0, "week_instance": None, "week_interval": None,
          "month_interval": None, "start_date": "2026-10-18", "duration": None, "end_date": None}
RULE = {"frequency": "every_week", "days": [0], "hour": 0, "minute": 0, "start_date": "2026-10-18"}


def scheduled(id, due="2026-10-18", rule=WEEKLY, by=CAPTAIN, title="Update firstmate", parent=9, **kw):
    """A to-do due on `due` that repeats by `rule` (None: does not repeat), in to-do list or set `parent`."""
    t = todo(id, by=by, title=title, description="<p>Run it on the due date and not before.</p>", **kw)
    t.update(due_on=due, parent={"id": parent, "title": "Maintenance", "type": "Todoset"})
    if rule is not None:
        t["repetition_schedule"] = dict(rule)
    return t


class Scheduled(AssignBase):
    """Scheduled to-dos: a due date on any request, and Basecamp's repeating to-dos, whose next occurrence Basecamp makes."""

    def next_occurrence(self, id, due="2026-10-25", by=ACTING, **kw):
        """What Basecamp does on completing a repeating to-do: a new to-do with the next due date. Its creator is not
        the person who asked (here the agent that completed it), so only the series makes it a request."""
        return self.add(scheduled(id, due=due, by=by, **kw))

    def test_a_request_carries_its_due_date_and_repetition_rule(self):
        self.add(scheduled(42))
        self.add(scheduled(43, rule=None, title="Renew the domain", parent=8))
        self.add(todo(44, title="Undated"))
        self.poll()
        got = {r["todo"]: (r["due_on"], r["repeats"], r["follows"]) for r in self.pending()}
        self.assertEqual(got, {42: ("2026-10-18", RULE, None), 43: ("2026-10-18", None, None), 44: (None, None, None)})
        self.assertEqual((self.request(42)["due_on"], self.request(42)["repeats"]), ("2026-10-18", RULE))

    def test_the_next_occurrence_after_the_agent_completes_one_is_a_new_request_once(self):
        self.add(scheduled(42))
        self.poll()
        self.assertTrue(self.sync().todo_complete("request-42"))
        self.next_occurrence(43)
        self.poll()
        self.poll()
        first, nxt = self.pending()
        self.assertEqual(len(self.pending()), 2)
        self.assertEqual({k: nxt[k] for k in ("kind", "key", "todo", "n", "reopened", "follows", "due_on", "repeats")},
                         {"kind": "todo-request", "key": "request-43", "todo": 43, "n": 1, "reopened": False,
                          "follows": 42, "due_on": "2026-10-25", "repeats": RULE})
        # Asked for by whoever asked for the series, not the to-do's creator (Basecamp's machinery, or the agent).
        self.assertEqual((nxt["author"]["id"], nxt["captain"]), (CAPTAIN, True))
        self.assertEqual((self.request(42)["next"], self.request(43).get("completed")), (43, None))
        self.assertEqual(self.stub.made("todos create"), [])  # the agent never makes the next one

    def test_the_next_occurrence_after_the_owner_completes_one_follows_the_closed_request(self):
        self.add(scheduled(42))
        self.poll()
        self.edit(42, completed=True, completion={"creator": {"id": CAPTAIN, "name": "Cap"}, "created_at": "tc"})
        self.next_occurrence(43, by=CAPTAIN)
        self.poll()
        self.poll()
        self.assertEqual([(r["kind"], r["todo"], r.get("reason"), r.get("follows")) for r in self.pending()],
                         [("todo-request", 42, None, None), ("todo-request-closed", 42, "completed", None),
                          ("todo-request", 43, None, 42)])
        self.assertEqual(self.pending()[1]["repeats"], RULE)

    def test_a_series_request_completed_since_the_last_sweep_closes_first_with_its_last_comments(self):
        self.add(scheduled(42))
        self.poll()
        self.stub.comments["42"] = [comment(5, content="<p>Thanks!</p>")]
        self.edit(42, completed=True, completion={"creator": {"id": CAPTAIN, "name": "Cap"}, "created_at": "tc"})
        self.next_occurrence(43)  # made by Basecamp, its creator not listened to
        self.poll()
        self.assertEqual([(r["kind"], r["todo"], r.get("follows")) for r in self.pending()],
                         [("todo-request", 42, None), ("todo-comment", 42, None), ("todo-request-closed", 42, None),
                          ("todo-request", 43, 42)])
        self.assertEqual(self.pending()[-1]["author"]["id"], CAPTAIN)

    def test_a_reused_todo_open_again_with_a_new_due_date_is_the_next_occurrence(self):
        self.add(scheduled(42))
        self.poll()
        self.assertTrue(self.sync().todo_complete("request-42"))
        self.poll()
        self.edit(42, completed=False, due_on="2026-10-25")
        self.poll()
        self.poll()
        nxt = self.pending()[-1]
        self.assertEqual(len(self.pending()), 2)
        self.assertEqual((nxt["kind"], nxt["n"], nxt["reopened"], nxt["follows"], nxt["due_on"]),
                         ("todo-request", 2, False, 42, "2026-10-25"))
        self.assertFalse(self.request(42).get("completed"))

    def test_only_one_to_do_follows_a_completed_occurrence(self):
        self.add(scheduled(42))
        self.poll()
        self.assertTrue(self.sync().todo_complete("request-42"))
        self.next_occurrence(43)
        self.poll()
        self.next_occurrence(44, by=STRANGER)  # a copy someone else made is not the series
        self.poll()
        self.assertEqual([r["todo"] for r in self.pending()], [42, 43])

    def test_a_like_named_todo_does_not_follow_a_request_that_did_not_repeat_or_was_not_completed(self):
        self.add(scheduled(42, rule=None))
        self.add(scheduled(50, title="Rotate", parent=7))
        self.poll()
        self.assertTrue(self.sync().todo_complete("request-42"))
        self.edit(50, assignees=[{"id": CAPTAIN}])  # unassigned: closed, but not completed
        self.next_occurrence(43)
        self.next_occurrence(51, title="Rotate", parent=7)
        self.poll()
        self.assertEqual([(r["kind"], r["todo"]) for r in self.pending()],
                         [("todo-request", 42), ("todo-request", 50), ("todo-request-closed", 50)])

    def test_a_moved_due_date_or_changed_rule_is_an_update_carrying_the_schedule(self):
        self.add(scheduled(42))
        self.poll()
        self.edit(42, due_on="2026-10-19")
        self.poll()
        self.poll()
        rule = dict(WEEKLY, days=[1])
        self.edit(42, repetition_schedule=rule)
        self.poll()
        self.edit(42, repetition_schedule=None, due_on=None)
        self.poll()
        self.assertEqual([(r["kind"], r["due_on"], (r["repeats"] or {}).get("days")) for r in self.pending()],
                         [("todo-request", "2026-10-18", [0]), ("todo-request-update", "2026-10-19", [0]),
                          ("todo-request-update", "2026-10-19", [1]), ("todo-request-update", None, None)])
        ids = [behaviors.inbox_note(r, "1", "2")[0] for r in self.pending()[1:]]
        self.assertEqual(len(set(ids)), 3)  # each move is its own note, though the text did not change

    def test_a_request_tracked_before_schedules_were_kept_picks_its_schedule_up_quietly(self):
        self.add(scheduled(42))
        self.poll()
        state = self.todos()
        for k in ("due_on", "repeats", "parent"):
            del state["request-42"][k]
        self.sync().save_json("todos.json", state)
        self.poll()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual((self.request(42)["due_on"], self.request(42)["parent"]), ("2026-10-18", 9))
        self.edit(42, due_on="2026-10-20")
        self.poll()
        self.assertEqual((self.pending()[-1]["kind"], self.pending()[-1]["due_on"]), ("todo-request-update", "2026-10-20"))
        self.assertTrue(self.sync().todo_complete("request-42"))
        self.next_occurrence(43)
        self.poll()
        self.assertEqual((self.pending()[-1]["todo"], self.pending()[-1]["follows"]), (43, 42))

    def test_todo_show_reads_the_schedule_from_basecamp_now(self):
        self.add(scheduled(42))
        self.poll()
        self.edit(42, due_on="2026-10-21")
        out = []
        self.assertTrue(self.sync().todo_show("request-42", out=out.append))
        shown = json.loads(out[0])
        self.assertEqual({k: shown[k] for k in ("key", "todo", "open", "assigned_to_you", "due_on", "repeats")},
                         {"key": "request-42", "todo": 42, "open": True, "assigned_to_you": True, "due_on": "2026-10-21",
                          "repeats": RULE})
        self.assertEqual(self.pending()[-1]["kind"], "todo-request")  # a read records nothing

    def test_scheduled_and_repeating_notes(self):
        rec = {"kind": "todo-request", "key": "request-43", "todo": 43, "n": 1, "title": "Update firstmate",
               "captain": True, "due_on": "2026-10-25", "repeats": RULE, "follows": 42}
        _, body = behaviors.inbox_note(rec, "1", "2")
        self.assertIn("next occurrence of a repeating to-do, asked for by the captain", body)
        self.assertIn(", due 2026-10-25, repeating every week on Sunday:", body)
        self.assertIn("Basecamp owns the date and may move it (sync.py todo show --todo request-43", body)
        self.assertIn("Basecamp makes the next occurrence, which arrives as a new request", body)
        self.assertNotIn("not before", body)  # when to do it is the home's defaults, not the note's
        _, body = behaviors.inbox_note(dict(rec, repeats=None, follows=None), "1", "2")
        self.assertIn("assigned to you by the captain", body)
        self.assertNotIn("repeat", body)
        _, body = behaviors.inbox_note({"kind": "todo-request", "key": "request-42", "todo": 42, "captain": True,
                                        "due_on": None, "repeats": None}, "1", "2")
        self.assertNotIn("scheduled", body)
        _, body = behaviors.inbox_note({"kind": "todo-request-closed", "key": "request-42", "todo": 42, "reason": "completed",
                                        "author": {"id": CAPTAIN}, "captain": True, "repeats": RULE}, "1", "2")
        self.assertIn("Basecamp makes the next occurrence", body)
        _, body = behaviors.inbox_note({"kind": "todo-request-update", "key": "request-42", "todo": 42, "text": "x",
                                        "author": None, "due_on": None, "repeats": None}, "1", "2")
        self.assertIn("edited, now undated; it now reads", body)


if __name__ == "__main__":
    unittest.main()
