"""Tests for the assigned-steps behavior: card steps the listened-to people assign to the agent's login, as requests.

/my/assignments.json, the steps, their cards' comments and the notifications are stubbed; nothing touches the network.
The fixtures keep the shapes Basecamp returned on 2026-11-12 (ids, names and text replaced):

- /my/assignments.json lists a step only nested in its card's entry, as one of its "children" of type "step"; the card
  entry itself is there (type "card") whoever the card is assigned to.
- GET /buckets/<project>/card_tables/steps/<id>.json is the step: "type" "Kanban::Step", its text as "title", no
  description, "parent" the card, "completion" once checked.
- The step's notifications ("Assignment", the "Event" of a step added already assigned, "Completion") subscribe to the
  step itself and link to the card with #__recording_<step id>.
"""
import base64, json, os, sys, unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN  # noqa: E402
from test_tools import boost, comment  # noqa: E402
from test_notifications import APP, PROJECT  # noqa: E402
from test_assigned_todos import AssignBase, AssignStub, OTHER, PARTNER, STRANGER  # noqa: E402
import behaviors  # noqa: E402
import stats  # noqa: E402

EYES = "\U0001F440"
CARD, STEP = 900, 901
SOMEONE = 77777777  # a colleague the card itself is assigned to


def person(pid):
    return {"id": pid, "name": f"person-{pid}"}


def step(id=STEP, card=CARD, by=CAPTAIN, to=(ACTING,), title="Review PR #12", bucket=PROJECT, card_title="Fix the import"):
    """A card step as GET /buckets/<project>/card_tables/steps/<id>.json returns it."""
    return {"id": id, "status": "active", "visible_to_clients": False, "created_at": "2026-11-12T07:09:24Z",
            "updated_at": "2026-11-12T07:09:24Z", "title": title, "inherits_status": True, "type": "Kanban::Step",
            "url": f"https://3.basecampapi.com/1/buckets/{bucket}/subtasks/{id}.json",
            "app_url": f"{APP}/buckets/{bucket}/card_tables/cards/{card}#__recording_{id}",
            "bookmark_url": "https://3.basecampapi.com/1/my/bookmarks/x.json",
            "parent": {"id": card, "title": card_title, "type": "Kanban::Card",
                       "url": f"https://3.basecampapi.com/1/buckets/{bucket}/card_tables/cards/{card}.json",
                       "app_url": f"{APP}/buckets/{bucket}/card_tables/cards/{card}"},
            "bucket": {"id": bucket, "name": f"Project {bucket}", "type": "Project"},
            "creator": person(by), "completed": False, "due_on": None, "assignees": [person(p) for p in to],
            "completion_subscribers": [], "position": 1,
            "completion_url": f"https://3.basecampapi.com/1/subtasks/{id}/completion.json"}


def step_note(id, kind, sid=STEP, card=CARD, who=CAPTAIN, bucket=PROJECT, title="Assigned you: Review PR #12, in Fix"):
    """A step's notification as /my/readings.json lists it: subscribed to the step, linked to its card."""
    gid = base64.b64encode(f"gid://bc3/Recording/{sid}".encode()).decode().rstrip("=")
    return {"id": id, "section": "inbox", "type": kind, "icon_type": {"Event": "subtask"}.get(kind, kind.lower()),
            "title": title, "content_excerpt": "Review PR #12", "bucket_name": f"Project {bucket}",
            "creator": {"id": who, "name": {CAPTAIN: "Cap"}.get(who)}, "created_at": "2099-01-01T00:00:01Z",
            "unread_at": "2099-01-01T00:00:01Z", "updated_at": "2099-01-01T00:00:01Z", "read_at": None,
            "unread_count": 1, "readable_identifier": gid, "readable_sgid": f"sgid-{id}",
            "app_url": f"{APP}/buckets/{bucket}/card_tables/cards/{card}#__recording_{sid}",
            "subscription_url": f"https://3.basecampapi.com/1/buckets/{bucket}/recordings/{sid}/subscription.json"}


def card_note(id, kind, anchor, card=CARD, who=CAPTAIN, bucket=PROJECT):
    """A comment or @mention notification on a card: subscribed to the card, anchored at the comment."""
    gid = base64.b64encode(f"gid://bc3/Recording/{anchor}".encode()).decode().rstrip("=")
    return {"id": id, "section": "inbox", "type": kind, "title": "Re: Fix the import", "content_excerpt": "hi",
            "bucket_name": f"Project {bucket}", "creator": {"id": who, "name": "Cap"},
            "created_at": "2099-01-01T00:00:01Z", "unread_at": "2099-01-01T00:00:0" + str(id % 10) + "Z",
            "updated_at": "2099-01-01T00:00:01Z", "read_at": None, "unread_count": 1, "readable_identifier": gid,
            "readable_sgid": f"sgid-{id}", "app_url": f"{APP}/buckets/{bucket}/card_tables/cards/{card}#__recording_{anchor}",
            "subscription_url": f"https://3.basecampapi.com/1/buckets/{bucket}/recordings/{card}/subscription.json"}


def assigned(at, by=CAPTAIN, to=(ACTING,)):
    """A step's assignment_changed event, as its events.json lists it."""
    return {"id": 1, "recording_id": STEP, "action": "assignment_changed", "created_at": at, "creator": person(by),
            "details": {"added_person_ids": list(to), "removed_person_ids": []}}


def said(id, at, who=CAPTAIN, content="<p>Merge it</p>"):
    """A comment on the card, with its own time."""
    return dict(comment(id, who=who, content=content), created_at=at)


MENTION = f'<bc-attachment sgid="sgid-{ACTING}" content-type="application/vnd.basecamp.mention"></bc-attachment>'


class StepStub(AssignStub):
    """Adds card steps: listed in /my/assignments.json as children of their card's entry, refetched by id, and checked off
    with `basecamp subtasks complete`. A step that is not stubbed is not found."""

    def __init__(self):
        super().__init__()
        self.steps = {}
        self.events = {}  # step id -> its events (GET /buckets/<project>/recordings/<id>/events.json)

    def __call__(self, cmd, **kw):
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            core = args[2:] if args[:1] == ["-P"] else args
            ok = lambda data: SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
            if core[:3] == ["api", "get", "/my/assignments.json"]:
                out = super().__call__(cmd, **kw)
                data = json.loads(out.stdout)["data"]
                cards = {}
                for s in self.steps.values():
                    if s["completed"] or s["status"] != "active" or self.me not in [a["id"] for a in s["assignees"]]:
                        continue
                    p = s["parent"]
                    entry = cards.setdefault(p["id"], {
                        "id": p["id"], "type": "card", "content": p["title"], "completed": False, "app_url": p["app_url"],
                        "assignees": [person(SOMEONE)], "bucket": {"id": s["bucket"]["id"], "name": s["bucket"]["name"]},
                        "comments_count": 0, "has_description": True, "due_on": None, "starts_on": None,
                        "parent": {"id": 1, "title": "Board: In progress"}, "children": []})
                    entry["children"].append({
                        "id": s["id"], "type": "step", "content": s["title"], "completed": False, "app_url": s["app_url"],
                        "assignees": [person(a["id"]) for a in s["assignees"]], "children": [], "comments_count": 0,
                        "bucket": {"id": s["bucket"]["id"], "name": s["bucket"]["name"]}, "due_on": None,
                        "has_description": False, "starts_on": None,
                        "parent": {"id": p["id"], "title": p["title"], "app_url": p["app_url"]}})
                data["non_priorities"] += list(cards.values())
                return ok(data)
            if core[:2] == ["api", "get"] and core[2].endswith("/events.json"):
                self.calls.append(core)
                return ok(self.events.get(core[2].split("/")[4], []))
            if core[:2] == ["api", "get"] and "/card_tables/steps/" in core[2]:
                self.calls.append(core)
                sid = core[2].rsplit("/", 1)[1][:-5]
                if sid not in self.steps:
                    self.errors_seen = getattr(self, "errors_seen", 0) + 1
                    return SimpleNamespace(stdout=json.dumps({"ok": False, "code": "not_found", "error": "Resource not found"}),
                                           stderr="", returncode=1)
                return ok(self.steps[sid])
            if core[:2] == ["subtasks", "complete"]:
                self.calls.append(core)
                if core[2] in self.steps:
                    self.steps[core[2]]["completed"] = True
                return ok(None)
            if core[:2] == ["api", "get"] and "/card_tables/cards/" in core[2]:
                self.calls.append(core)  # a step is never a card: a test asserts this is not called for one
                return SimpleNamespace(stdout=json.dumps({"ok": False, "code": "not_found", "error": "Resource not found"}),
                                       stderr="", returncode=1)
        return super().__call__(cmd, **kw)


class StepBase(AssignBase):
    def setUp(self):
        super().setUp()
        self.stub = StepStub()
        self.stub.lines = {"77": []}
        self.cfg(assigned_steps={}, assigned_todos=False)

    def add_step(self, s):
        self.stub.steps[str(s["id"])] = s
        return s

    def on_card(self, *comments, card=CARD):
        self.stub.comments.setdefault(str(card), []).extend(comments)

    def ledger(self):
        p = os.path.join(self.cfgdir, "relay.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def fetched(self, sid=STEP, bucket=PROJECT):
        return self.stub.paths().count(f"/buckets/{bucket}/card_tables/steps/{sid}.json")


class Discovery(StepBase):
    def test_a_step_the_captain_assigns_is_recorded_once_acknowledged_and_tracked_with_its_card(self):
        self.add_step(step())
        self.poll()
        self.poll()
        [rec] = self.pending()
        self.assertEqual({k: rec[k] for k in ("kind", "key", "todo", "n", "title", "text", "captain", "url")},
                         {"kind": "step-request", "key": f"request-{STEP}", "todo": STEP, "n": 1, "title": "Review PR #12",
                          "text": "", "captain": True,
                          "url": f"{APP}/buckets/{PROJECT}/card_tables/cards/{CARD}#__recording_{STEP}"})
        self.assertEqual(rec["card"], {"id": CARD, "title": "Fix the import",
                                       "url": f"{APP}/buckets/{PROJECT}/card_tables/cards/{CARD}"})
        self.assertEqual(rec["author"], {"id": CAPTAIN, "name": f"person-{CAPTAIN}"})
        self.assertEqual(self.stub.posted(), [(str(STEP), EYES)])
        req = self.request(STEP)
        self.assertEqual((req["type"], req["card"], req["todo"], req["request"]["captain"]), ("step", CARD, STEP, True))

    def test_the_card_the_step_is_on_is_not_a_request(self):
        self.cfg(assigned_cards={})
        self.add_step(step())
        self.poll()
        self.assertEqual([r["kind"] for r in self.pending()], ["step-request"])
        self.assertNotIn(f"request-{CARD}", self.todos())

    def test_a_step_assigned_to_someone_else_or_completed_is_not_read(self):
        self.add_step(step(to=(CAPTAIN,)))
        self.add_step(step(id=902))
        self.stub.steps["902"]["completed"] = True
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual((self.fetched(), self.fetched(902)), (0, 0))

    def test_a_step_assigned_by_someone_not_listened_to_is_ignored_and_fetched_once(self):
        self.add_step(step(by=STRANGER))
        self.poll()
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.fetched(), 1)

    def test_another_listened_to_person_is_recorded_as_not_the_captain(self):
        self.add_step(step(by=PARTNER))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["captain"], rec["author"]["id"]), ("step-request", False, PARTNER))

    def test_another_projects_step_only_with_the_account_scope(self):
        self.add_step(step(bucket=OTHER))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.cfg(assigned_steps={"scope": "account"})
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["project"]["id"]), ("step-request", OTHER))
        self.assertGreaterEqual(self.fetched(STEP, OTHER), 1)
        self.assertIn(str(OTHER), self.stub.projects("comments list"))

    def test_off_steps_are_never_read_even_with_cards_and_todos_on(self):
        self.cfg(assigned_steps=False, assigned_cards={}, assigned_todos={})
        self.add_step(step())
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.fetched(), 0)
        self.assertNotIn(f"/buckets/{PROJECT}/card_tables/cards/{STEP}.json", self.stub.paths())

    def test_the_account_scope_is_claimed_one_home_per_login(self):
        import runs
        self.assertIn("step-requests 1", runs.watches({"account": 1, "project": 2, "assigned_steps": {"scope": "account"}}))
        self.assertEqual(runs.watches({"account": 1, "project": 2, "assigned_steps": {}}), ["project 1/2"])

    def test_malformed_config_refused(self):
        for bad in ([], {"limit": 0}, {"scope": "everywhere"}):
            self.cfg(assigned_steps=bad)
            with self.assertRaises(ValueError):
                self.sync()


class Lifecycle(StepBase):
    def test_comments_already_on_the_card_are_the_cards_own(self):
        self.on_card(comment(5, content="<p>Old talk</p>"))
        self.add_step(step())
        self.poll()
        self.poll()
        self.assertEqual([r["kind"] for r in self.pending()], ["step-request"])

    def test_only_comments_that_concern_the_agent_arrive(self):
        self.add_step(step())
        self.poll()
        self.on_card(comment(10, who=SOMEONE, content="<p>Talking to a colleague</p>"),
                     comment(11, content="<p>Team, status?</p>"),
                     comment(12, content=f"<p>{MENTION} please look at the logs too</p>"))
        self.poll()
        recs = self.pending()[1:]
        self.assertEqual([(r["kind"], r["comment"], r["why"], r["type"], r["key"]) for r in recs],
                         [("todo-comment", 12, "addressed", "step", f"request-{STEP}")])
        self.assertEqual(recs[0]["card"]["id"], CARD)
        skipped = [e for e in self.ledger() if e["event"] == "skipped"]
        self.assertEqual([(e["recording"], e["reason"]) for e in skipped], [(11, "not addressed to the agent")])
        self.assertIn("skipped comment 11 by", open(os.path.join(self.cfgdir, "sync.log")).read())

    def test_the_first_comment_after_the_agents_own_answers_it(self):
        self.add_step(step())
        self.poll()
        s = self.sync()
        self.assertTrue(s.todo_comment(f"request-{STEP}", "Reviewed: two issues, see the PR."))
        [made] = self.stub.made("comments create")
        self.assertEqual(made[2], str(CARD))  # on the card, the step has no comments of its own
        self.on_card(comment(1000, content="<p>Fix the first, skip the second</p>"),
                     comment(1001, content="<p>Unrelated note for the team</p>"))
        self.poll()
        self.assertEqual([(r["kind"], r.get("comment"), r.get("why")) for r in self.pending()[1:]],
                         [("todo-comment", 1000, "awaiting")])
        self.assertTrue(self.sync().reply(1000, "Fixed the first."))
        self.assertEqual(self.stub.made("comments create")[-1][2], str(CARD))

    def test_comments_after_the_assignment_count_even_when_made_before_the_sweep_found_it(self):
        self.add_step(dict(step(), created_at="2026-11-12T07:00:00Z"))
        self.stub.events[str(STEP)] = [assigned("2026-11-12T08:00:00Z", by=PARTNER)]
        self.on_card(said(30, "2026-11-12T07:30:00Z", content=f"<p>{MENTION} before the assignment</p>"),
                     said(31, "2026-11-12T08:05:00Z", content=f"<p>{MENTION} after it, before the sweep</p>"))
        self.poll()
        self.assertEqual([(r["kind"], r.get("comment")) for r in self.pending()],
                         [("step-request", None), ("todo-comment", 31)])
        self.assertEqual(self.pending()[0]["author"]["id"], PARTNER)  # its assignment event says who assigned it
        self.assertEqual(self.request(STEP)["start"], 30)

    def test_with_the_reader_of_its_card_off_the_step_reads_the_card(self):
        self.cfg(assigned_cards=False, cards=False)
        self.add_step(step())
        self.poll()
        state = self.todos()
        state[f"request-{CARD}"] = {"todo": CARD, "type": "card", "request": {"captain": True}, "cursor": 0}
        json.dump(state, open(os.path.join(self.cfgdir, "todos.json"), "w"))
        json.dump({"engine/t1": {"card": CARD}}, open(os.path.join(self.cfgdir, "map.json"), "w"))
        self.on_card(comment(1000, content=f"<p>{MENTION} look</p>"))
        self.sync().read_todo_comments(keys=[f"request-{STEP}"])
        self.assertEqual([(r["kind"], r.get("comment")) for r in self.pending()][1:], [("todo-comment", 1000)])

    def test_two_steps_on_one_card_relay_each_comment_once_and_hand_over_without_replay(self):
        self.add_step(step())
        self.add_step(step(id=902, title="Then deploy it"))
        self.poll()
        self.on_card(comment(1000, content=f"<p>{MENTION} look</p>"))
        self.poll()
        self.poll()
        comments = [(r["key"], r["comment"]) for r in self.pending() if r["kind"] == "todo-comment"]
        self.assertEqual(comments, [(f"request-{STEP}", 1000)])
        self.assertTrue(self.sync().todo_complete(f"request-{STEP}"))
        self.on_card(comment(1001, content=f"<p>{MENTION} and the deploy?</p>"))
        self.poll()
        comments = [(r["key"], r["comment"]) for r in self.pending() if r["kind"] == "todo-comment"]
        self.assertEqual(comments, [(f"request-{STEP}", 1000), ("request-902", 1001)])

    def test_a_new_step_on_the_card_does_not_move_an_older_readers_place(self):
        self.add_step(dict(step(), created_at="2026-11-12T07:00:00Z"))
        self.stub.events[str(STEP)] = [assigned("2026-11-12T07:00:00Z")]
        self.poll()
        self.on_card(said(1000, "2026-11-12T08:00:00Z", content=f"<p>{MENTION} look</p>"))
        self.add_step(dict(step(id=902, title="Then deploy it"), created_at="2026-11-12T09:00:00Z"))
        self.stub.events["902"] = [assigned("2026-11-12T09:00:00Z")]
        self.assertTrue(self.sync().read_todo_request(902, what="step"))  # found before the older step read the card
        self.assertEqual(self.request(902)["cursor"], 1000)
        self.sync().read_todo_comments(keys=[f"request-{STEP}"])
        self.assertEqual([(r["key"], r.get("comment")) for r in self.pending() if r["kind"] == "todo-comment"],
                         [(f"request-{STEP}", 1000)])

    def test_the_next_step_takes_over_the_boosts_on_comments_relayed_before(self):
        self.add_step(step())
        self.add_step(step(id=902, title="Then deploy it"))
        self.poll()
        self.on_card(dict(comment(1000, content=f"<p>{MENTION} look</p>"), boosts_count=0))
        self.poll()
        self.assertTrue(self.sync().todo_complete(f"request-{STEP}"))
        self.stub.boosts["1000"] = [boost(7, content="ship it")]
        self.stub.comments[str(CARD)][0]["boosts_count"] = 1
        self.poll()
        self.poll()
        self.assertEqual([(r["kind"], r.get("boost"), r.get("key")) for r in self.pending() if r["kind"] == "boost"],
                         [("boost", 7, "request-902")])

    def test_completion_checks_the_step_off_once_and_is_counted_answered(self):
        self.add_step(step())
        self.poll()
        s = self.sync()
        self.assertTrue(s.todo_complete(f"request-{STEP}"))
        self.assertEqual([c[:3] for c in self.stub.calls if c[:2] == ["subtasks", "complete"]],
                         [["subtasks", "complete", str(STEP)]])
        self.assertNotIn(["cards", "done"], [c[:2] for c in self.stub.calls])
        self.assertTrue(self.request(STEP)["completed"])
        self.assertTrue(self.sync().todo_complete(f"request-{STEP}"))  # Basecamp shows it checked: nothing written
        self.assertEqual(len([c for c in self.stub.calls if c[:2] == ["subtasks", "complete"]]), 1)
        self.poll()
        self.assertEqual([r["kind"] for r in self.pending()], ["step-request"])  # no closing record for its own completion
        r = stats.report(self.sync(), period="all", tokens=False)
        self.assertEqual((r["total"]["relayed"], r["total"]["answered"]), (1, 1))
        self.assertIn(("step-request", "assigned"), [(b["kind"], b["why"]) for b in r["by"]])

    def test_a_step_already_checked_is_recorded_completed(self):
        self.add_step(step())
        self.poll()
        self.stub.steps[str(STEP)]["completed"] = True
        self.assertTrue(self.sync().todo_complete(f"request-{STEP}"))
        self.assertEqual([c for c in self.stub.calls if c[:2] == ["subtasks", "complete"]], [])

    def test_an_edit_is_an_update_recorded_once(self):
        self.add_step(step())
        self.poll()
        self.stub.steps[str(STEP)]["title"] = "Review PR #12 and #13"
        self.poll()
        self.poll()
        self.assertEqual([(r["kind"], r["title"]) for r in self.pending()[1:]],
                         [("step-request-update", "Review PR #12 and #13")])

    def test_unassigning_closes_it_and_a_new_assignment_is_a_new_request(self):
        self.add_step(step())
        self.poll()
        self.stub.steps[str(STEP)]["assignees"] = [person(CAPTAIN)]
        self.poll()
        self.poll()
        self.assertEqual([(r["kind"], r.get("reason")) for r in self.pending()],
                         [("step-request", None), ("step-request-closed", "unassigned")])
        self.stub.steps[str(STEP)]["assignees"] = [person(ACTING)]
        self.poll()
        self.assertEqual([(r["kind"], r.get("n")) for r in self.pending()][-1], ("step-request", 2))

    def test_a_tracked_step_not_found_closes_it_once_and_it_is_not_read_again(self):
        self.add_step(step())
        self.poll()
        del self.stub.steps[str(STEP)]  # deleted, or out of this login's reach
        self.poll()
        fetched = self.fetched()
        self.poll()
        self.assertEqual([(r["kind"], r.get("reason")) for r in self.pending()],
                         [("step-request", None), ("step-request-closed", "not_found")])
        self.assertEqual(self.pending()[-1]["card"]["id"], CARD)
        self.assertEqual(self.fetched(), fetched)

    def test_the_owner_checking_it_off_closes_it(self):
        self.add_step(step())
        self.poll()
        self.stub.steps[str(STEP)].update(completed=True, completion={"created_at": "2026-11-12T08:00:00Z",
                                                                      "creator": person(CAPTAIN)})
        self.poll()
        last = self.pending()[-1]
        self.assertEqual((last["kind"], last["reason"], last["author"]["id"]), ("step-request-closed", "completed", CAPTAIN))
        self.assertTrue(self.request(STEP)["completed"])

    def test_a_step_on_a_card_the_agent_tracks_leaves_its_comments_to_that_reader(self):
        self.cfg(assigned_cards={})
        self.stub.cards = {}
        self.add_step(step())
        self.poll()
        state = self.todos()
        state[f"request-{CARD}"] = {"todo": CARD, "type": "card", "request": {"captain": True}, "cursor": 0}
        json.dump(state, open(os.path.join(self.cfgdir, "todos.json"), "w"))
        self.stub.calls.clear()
        self.sync().read_todo_comments(keys=[f"request-{STEP}"])
        self.assertNotIn(["comments", "list", str(CARD)], [c[:3] for c in self.stub.calls])


class Notifications(StepBase):
    def setUp(self):
        super().setUp()
        self.cfg(inbox={})
        self.poll()
        self.stub.calls.clear()

    def notify(self, *items):
        self.stub.readings = {"unreads": list(items), "reads": []}
        self.sync().behaviors["notifications"].run()

    def test_an_assignment_is_a_request_in_the_same_run_once(self):
        self.add_step(step())
        # Basecamp sends both for a step added already assigned.
        self.notify(step_note(10, "Assignment"), step_note(11, "Event", title="Added: Review PR #12, in Fix"))
        self.assertEqual([r["kind"] for r in self.pending()], ["step-request"])
        self.assertEqual([n[0] for n in self.stub.notes], [f"basecamp-step-request-{STEP}-1"])
        self.assertIn("Basecamp card step assigned to you by the captain, a request: 'Review PR #12'", self.stub.notes[0][1])
        self.assertEqual(sorted(sum(self.stub.marked, [])), ["10", "11"])
        self.assertEqual(self.fetched(), 1)
        self.notify(step_note(10, "Assignment"), step_note(11, "Event"))
        self.assertEqual([r["kind"] for r in self.pending()], ["step-request"])

    def test_its_completion_closes_it(self):
        self.add_step(step())
        self.notify(step_note(10, "Assignment"))
        self.stub.steps[str(STEP)].update(completed=True, completion={"created_at": "2026-11-12T08:00:00Z",
                                                                      "creator": person(CAPTAIN)})
        self.notify(step_note(10, "Assignment"), step_note(12, "Completion", title="Completed: Review PR #12, in Fix"))
        self.assertEqual([(r["kind"], r.get("reason")) for r in self.pending()],
                         [("step-request", None), ("step-request-closed", "completed")])

    def test_a_mention_or_reply_on_the_card_arrives_as_part_of_the_request_not_twice(self):
        self.add_step(step())
        self.notify(step_note(10, "Assignment"))
        self.on_card(comment(20, content=f"<p>{MENTION} see the logs</p>"), comment(21, content="<p>Also the tests</p>"))
        # 21 is a Basecamp Reply to the agent's comment: its text has no @mention, its notification is a Mention.
        self.notify(step_note(10, "Assignment"), card_note(13, "Mention", 20), card_note(14, "Mention", 21))
        self.assertEqual([(r["kind"], r.get("comment"), r.get("why")) for r in self.pending()[1:]],
                         [("todo-comment", 20, "addressed"), ("todo-comment", 21, "addressed")])
        self.assertNotIn("mention", [r["kind"] for r in self.pending()])

    def test_a_reply_named_after_the_sweep_skipped_it_is_still_recorded(self):
        self.add_step(step())
        self.notify(step_note(10, "Assignment"))
        self.on_card(comment(21, content="<p>Also the tests</p>"))
        self.sync().read_todo_comments(keys=[f"request-{STEP}"])  # the sweep, before the notification
        self.assertEqual([r["kind"] for r in self.pending()], ["step-request"])
        self.notify(step_note(10, "Assignment"), card_note(14, "Mention", 21))
        self.notify(step_note(10, "Assignment"), card_note(14, "Mention", 21))
        self.assertEqual([(r["kind"], r.get("comment")) for r in self.pending()], [("step-request", None), ("todo-comment", 21)])

    def test_a_late_reply_does_not_clear_the_wait_on_the_agents_newer_comment(self):
        self.add_step(step())
        self.notify(step_note(10, "Assignment"))
        self.on_card(comment(21, content="<p>Also the tests</p>"), comment(22, who=ACTING, content="<p>Which ones?</p>"))
        self.sync().read_todo_comments(keys=[f"request-{STEP}"])  # the sweep skips 21 and sees the agent's 22
        self.notify(step_note(10, "Assignment"), card_note(14, "Mention", 21))
        self.on_card(comment(23, content="<p>The import ones</p>"))
        self.sync().read_todo_comments(keys=[f"request-{STEP}"])
        self.assertEqual([(r.get("comment"), r.get("why")) for r in self.pending()[1:]],
                         [(21, "addressed"), (23, "awaiting")])

    def test_a_stranger_assigning_a_step_is_answered_on_its_card(self):
        self.cfg(blocked={})
        self.poll()
        self.add_step(dict(step(by=STRANGER), updated_at="2099-01-01T00:00:00Z"))
        self.notify(step_note(10, "Assignment", who=STRANGER))
        self.assertEqual(self.pending(), [])
        self.assertEqual([c[2] for c in self.stub.made("comments create")], [str(CARD)])

    def test_off_an_assignment_is_unmonitored_and_never_read_as_a_todo_or_card(self):
        self.cfg(assigned_steps=False, assigned_todos={}, assigned_cards={})
        self.add_step(step())
        self.notify(step_note(10, "Assignment"), step_note(11, "Event"))
        self.assertEqual([(r["kind"], r["key"]) for r in self.pending()],
                         [("unmonitored", "step.assignment_changed/Kanban::Step")])
        paths = self.stub.paths()
        self.assertNotIn(f"/buckets/{PROJECT}/todos/{STEP}.json", paths)
        self.assertNotIn(f"/buckets/{PROJECT}/card_tables/cards/{STEP}.json", paths)
        self.assertEqual(sorted(sum(self.stub.marked, [])), ["10", "11"])  # handled, not retried every run

    def test_a_step_not_found_is_about_that_step_only_and_not_retried(self):
        self.notify(step_note(10, "Assignment"))  # no such step: deleted, or out of this login's reach
        self.assertEqual(self.pending(), [])
        self.assertEqual(sorted(sum(self.stub.marked, [])), ["10"])
        self.notify(step_note(10, "Assignment"))
        self.assertEqual(self.fetched(), 1)

    def test_a_decision_step_checked_runs_its_records_reader(self):
        self.cfg(assigned_steps=False)
        state = {"ta-x": {"todo": CARD, "type": "card", "on": True, "cursor": 0, "boost_seen": [], "boost_counts": {},
                          "subtasks": [{"id": STEP, "text": "Ship it?", "done": None, "decider": CAPTAIN}]}}
        json.dump(state, open(os.path.join(self.cfgdir, "todos.json"), "w"))
        self.stub.boosts[str(STEP)] = []
        self.notify(step_note(10, "Completion"))
        self.assertIn(["comments", "list", str(CARD)], [c[:3] for c in self.stub.calls])
        self.assertNotIn("unmonitored", [r["kind"] for r in self.pending()])


class Note(unittest.TestCase):
    def test_step_request_notes(self):
        card = {"id": CARD, "title": "Fix the import", "url": "https://x/cards/900"}
        rec = {"kind": "step-request", "key": f"request-{STEP}", "todo": STEP, "n": 1, "title": "Review PR #12",
               "captain": True, "role": "captain", "author": {"id": CAPTAIN}, "card": card}
        rid, body = behaviors.inbox_note(rec, 1, PROJECT)
        self.assertEqual(rid, f"basecamp-step-request-{STEP}-1")
        self.assertIn("Basecamp card step assigned to you by the captain, a request: 'Review PR #12'", body)
        self.assertIn("a step on card 'Fix the import' (https://x/cards/900), which is not yours", body)
        self.assertIn(f"sync.py todo complete --todo request-{STEP}", body)
        rid, body = behaviors.inbox_note(dict(rec, kind="step-request-closed", reason="unassigned"), 1, PROJECT)
        self.assertIn(f"Basecamp card step request 'Review PR #12' (request-{STEP}) no longer assigned to you", body)
        rid, body = behaviors.inbox_note({"kind": "todo-comment", "key": f"request-{STEP}", "comment": 5, "request": True,
                                          "type": "step", "title": "Review PR #12", "card": card, "why": "awaiting",
                                          "role": "captain", "author": {"id": CAPTAIN}}, 1, PROJECT)
        self.assertIn(f"on card step request 'Review PR #12' (request-{STEP})", body)
        self.assertIn("on the card 'Fix the import'", body)
        self.assertIn("the first comment there after your own", body)


if __name__ == "__main__":
    unittest.main()
