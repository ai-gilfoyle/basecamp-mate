"""Unit tests for sync.py. The basecamp and tasks-axi CLIs are stubbed; nothing touches the network."""
import json, os, re, shutil, sys, tempfile, unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Timer runs claim their watches in the run registry (runs.py); never the real one.
os.environ["BASECAMP_MATE_RUNS"] = tempfile.mkdtemp(prefix="basecamp-mate-runs-")
import sync  # noqa: E402

CAPTAIN = 33333333
ACTING = 44444444  # the dedicated firstmate user the CLI profile signs in as


class Stub:
    """Records every CLI call and answers like the basecamp CLI would."""

    def __init__(self, comments=None, boosts=None):
        self.profiles = []
        self.calls, self.next_id, self.comments, self.boosts = [], 500, comments or {}, boosts or {}
        self.lines = {}  # chat id -> lines
        self.questions, self.answers = [], {}  # check-in questions; question id -> answers
        self.me, self.fail_post = ACTING, False  # /my/profile.json id; make boost posts fail
        self.lavish, self.lavish_calls = "", 0  # stdout of plain `lavish-axi`, or an exception to raise
        self.readings, self.my_boosts = {"unreads": [], "reads": []}, []  # /my/readings.json, /my/boosts.json
        self.marked = []  # the ids of each `notifications read` call
        self.pingable = []  # `people pingable`: the people the acting login can @mention; None fails it, an exception is raised
        self.project_people = {}  # bucket id -> its /projects/<id>/people.json; a bucket not listed has everyone pingable

    def __call__(self, cmd, **kw):
        if cmd[0] == "lavish-axi":
            self.lavish_calls += 1
            if isinstance(self.lavish, Exception):
                raise self.lavish
            return SimpleNamespace(stdout=self.lavish, stderr="", returncode=0)
        if cmd[0] != "basecamp":
            raise AssertionError(f"unexpected command {cmd}")
        args = cmd[3:-3]
        if args[:1] == ["-P"]:
            self.profiles.append(args[1])
            args = args[2:]
        else:
            self.profiles.append(None)
        self.calls.append(args)
        data = None
        if args[:2] == ["cards", "create"]:
            self.next_id += 1
            data = {"id": self.next_id}
        elif args[:2] == ["comments", "list"]:
            data = self.comments.get(args[2], [])
        elif args[:3] == ["api", "get", "/my/readings.json"]:
            data = self.readings
        elif args[:3] == ["api", "get", "/my/boosts.json"]:
            data = self.my_boosts
        elif args[:2] == ["people", "pingable"]:
            if isinstance(self.pingable, Exception):
                raise self.pingable
            if self.pingable is None:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            data = self.pingable
        elif args[:2] == ["api", "get"] and re.fullmatch(r"/projects/\d+/people\.json", args[2]):
            data = self.project_people.get(args[2].split("/")[2], self.pingable if isinstance(self.pingable, list) else [])
            if data is None:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
        elif args[:2] == ["notifications", "read"]:
            self.marked.append(args[2:])
        elif args[:3] == ["api", "get", "/my/profile.json"]:
            data = {"id": self.me, "attachable_sgid": f"sgid-{self.me}"}
        elif args[:2] == ["api", "get"] and args[2].startswith("/people/"):
            data = {"id": int(args[2].split("/")[2].split(".")[0]), "attachable_sgid": "sgid-owner"}
        elif args[:2] == ["api", "get"] and "/questionnaires/" in args[2]:
            data = self.questions
        elif args[:2] == ["api", "get"] and "/questions/" in args[2]:
            data = self.answers.get(args[2].split("/")[4], [])
        elif args[:3] == ["checkins", "answer", "create"]:
            data = {"id": 600 + len(self.calls)}
            self.answers.setdefault(args[3], []).append({"creator": {"id": self.me}, "group_on": args[6]})
        elif args[:2] == ["api", "get"] and "/chats/" in args[2]:
            data = list(reversed(self.lines.get(args[2].split("/")[4], [])))
        elif args[:2] == ["api", "post"] and "/chats/" in args[2]:
            if self.fail_post:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            data = {"id": 700 + len(self.calls), "creator": {"id": self.me}, "content": json.loads(args[4])["content"]}
            self.lines.setdefault(args[2].split("/")[4], []).append(data)
        elif args[:2] == ["api", "get"]:
            data = self.boosts.get(args[2].split("/")[4], [])
        elif args[:2] == ["api", "post"] and args[2].endswith("/comments.json"):
            if self.fail_post:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            data = {"id": 800 + len(self.calls), "creator": {"id": self.me}, "content": json.loads(args[4])["content"]}
            self.comments.setdefault(args[2].split("/")[4], []).append(data)
        elif args[:2] == ["api", "post"]:
            if self.fail_post:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            rid = args[2].split("/")[4]
            data = {"id": 900 + len(self.calls), "booster": {"id": self.me}, "content": json.loads(args[4])["content"]}
            self.boosts.setdefault(rid, []).append(data)
        elif args[:2] == ["api", "delete"]:
            bid = int(args[2].split("/")[4].split(".")[0])
            for bs in self.boosts.values():
                bs[:] = [b for b in bs if b["id"] != bid]
        return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)

    def verbs(self):
        return [a[1] if a[0] == "cards" else a[0] for a in self.calls if a[:2] not in (["comments", "list"], ["api", "get"], ["api", "post"])]

    def posts(self):
        return [a[2] for a in self.calls if a[:2] == ["api", "post"]]

    def posted(self):
        return [(a[2].split("/")[4], json.loads(a[4])["content"]) for a in self.calls
                if a[:2] == ["api", "post"] and a[2].endswith("/boosts.json")]

    def replies(self):
        return [(a[2].split("/")[4], json.loads(a[4])["content"]) for a in self.calls
                if a[:2] == ["api", "post"] and a[2].endswith("/comments.json")]

    def chat_posts(self):
        return [(a[2].split("/")[4], json.loads(a[4])["content"]) for a in self.calls
                if a[:2] == ["api", "post"] and "/chats/" in a[2]]

    def deletes(self):
        return [a[2] for a in self.calls if a[:2] == ["api", "delete"]]


def item(id, section="Queued", repo="srv", hold=None, hold_kind=None, until=None, title="A task"):
    return {"id": id, "section": section, "title": title, "links": [], "repo": repo, "kind": "ship",
            "hold": hold, "hold_kind": hold_kind, "until": until, "blocked_by": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["BASECAMP_MATE_RUNS"] = os.path.join(self.tmp, "runs")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, "state"))
        self.cfgdir = os.path.join(self.tmp, "cfg")
        os.makedirs(self.cfgdir)
        shutil.copy(os.path.join(ROOT, "examples", "config.example.json"), os.path.join(self.cfgdir, "config.json"))
        cfg = json.load(open(os.path.join(self.cfgdir, "config.json")))
        cfg["repos"] = {"srv": "server", "eng": "engine"}
        for opt in ("chats", "ask_chat", "checkins", "people"):  # opt-in features the example shows; off by default here
            cfg.pop(opt)
        json.dump(cfg, open(os.path.join(self.cfgdir, "config.json"), "w"))
        self.stub = Stub()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def sync(self, dry=False):
        return sync.Sync(self.home, os.path.join(self.cfgdir, "config.json"), dry=dry, runner=self.stub)

    def side(self, name, value):
        json.dump(value, open(os.path.join(self.cfgdir, name), "w"))

    def cards(self):
        return json.load(open(os.path.join(self.cfgdir, "map.json")))

    def pr(self, task):
        open(os.path.join(self.home, "state", f"{task}.meta"), "w").write("pr=https://github.com/o/r/pull/1\n")


class ColumnRules(Base):
    def col(self, it, notnow=None):
        return self.sync().column_for(it, notnow or {})

    def test_queued_is_triage(self):
        self.assertEqual(self.col(item("a")), ("Triage", False))

    def test_in_flight_without_pr_is_in_progress(self):
        self.assertEqual(self.col(item("a", "In flight")), ("In progress", False))

    def test_in_flight_with_pr_is_ready_for_qa(self):
        self.pr("a")
        self.assertEqual(self.col(item("a", "In flight")), ("Ready for QA", False))

    def test_done(self):
        self.assertEqual(self.col(item("a", "Done")), ("Done", False))
        self.assertEqual(self.col(item("a", "Done"), {"a": "x"}), ("Done", False))

    def test_captain_hold_is_figuring_and_assigned(self):
        self.assertEqual(self.col(item("a", hold="pick", hold_kind="captain")), ("Figuring it out", True))

    def test_captain_hold_with_until_is_not_now(self):
        self.assertEqual(self.col(item("a", hold="pick", hold_kind="captain", until="2026-10-01")), ("Not now", False))

    def test_parked_is_not_now(self):
        self.assertEqual(self.col(item("a", hold="x", hold_kind="parked")), ("Not now", False))
        self.assertEqual(self.col(item("a", hold="parked by ruling")), ("Not now", False))

    def test_not_now_file_unless_captain_hold(self):
        self.assertEqual(self.col(item("a"), {"a": "later"}), ("Not now", False))
        self.assertEqual(self.col(item("a", hold="q", hold_kind="captain"), {"a": "later"}), ("Figuring it out", True))

    def test_figuring_file_moves_triage(self):
        self.side("figuring.json", {"a": "needs a plan"})
        self.sync().main([item("a")])
        self.assertEqual(self.cards()["a|server"]["column"], "Figuring it out")
        self.assertIn("needs a plan", self.stub.calls[0][3])


class Assignment(Base):
    def test_create_assigns_captain_when_waiting(self):
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        create = self.stub.calls[0]
        self.assertEqual(create[:2], ["cards", "create"])
        self.assertEqual(create[create.index("--assignee") + 1], str(CAPTAIN))
        self.assertTrue(self.cards()["a|server"]["assigned"])

    def test_unassign_after_decision(self):
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        self.stub.calls.clear()
        self.sync().main([item("a")])
        self.assertIn(["unassign", "501", "--card", "--from", str(CAPTAIN)], self.stub.calls)
        rec = self.cards()["a|server"]
        self.assertFalse(rec["assigned"])
        self.assertEqual(rec["column"], "Triage")

    def test_assign_existing_card(self):
        self.sync().main([item("a")])
        self.stub.calls.clear()
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        assign = [c for c in self.stub.calls if "--assignee" in c]
        self.assertEqual(len(assign), 1)
        self.assertEqual(assign[0][:3], ["cards", "update", "501"])


class NoCaptainAssignment(Base):
    def config(self, **opts):
        path = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(path))
        cfg.update(opts)
        json.dump(cfg, open(path, "w"))

    def assignments(self):
        return [c for c in self.stub.calls if "--assignee" in c or c[:1] == ["unassign"]]

    def test_create_keeps_the_column_without_assigning(self):
        self.config(assign_captain=False)
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        create = self.stub.calls[0]
        self.assertEqual(create[:2], ["cards", "create"])
        self.assertNotIn("--assignee", create)
        rec = self.cards()["a|server"]
        self.assertEqual(rec["column"], "Figuring it out")
        self.assertFalse(rec["assigned"])

    def test_update_to_a_hold_does_not_assign(self):
        self.config(assign_captain=False)
        self.sync().main([item("a")])
        self.stub.calls.clear()
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        self.assertEqual(self.assignments(), [])
        self.assertEqual(self.cards()["a|server"]["column"], "Figuring it out")

    def test_switching_off_unassigns_only_the_owner_from_held_cards(self):
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        self.assertTrue(self.cards()["a|server"]["assigned"])
        self.config(assign_captain=False)
        self.stub.calls.clear()
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        # one unassign naming only the owner, so any other assignee stays on the card
        self.assertEqual(self.assignments(), [["unassign", "501", "--card", "--from", str(CAPTAIN)]])
        rec = self.cards()["a|server"]
        self.assertFalse(rec["assigned"])
        self.assertEqual(rec["column"], "Figuring it out")
        self.stub.calls.clear()
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        self.assertEqual(self.assignments(), [])

    def test_dry_run_plans_the_unassign(self):
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        self.config(assign_captain=False)
        plan = self.sync(dry=True).main([item("a", hold="pick", hold_kind="captain")])
        self.assertEqual(plan["unassign"], 1)

    def test_default_still_assigns(self):
        self.config(assign_captain=True)
        self.sync().main([item("a", hold="pick", hold_kind="captain")])
        self.assertTrue(self.cards()["a|server"]["assigned"])

    def test_rejects_a_non_boolean(self):
        self.config(assign_captain="no")
        with self.assertRaises(ValueError):
            self.sync()


class IdMap(Base):
    def test_create_then_rerun_is_noop(self):
        self.sync().main([item("a"), item("b", repo="eng")])
        self.assertEqual(self.stub.verbs(), ["create", "create"])
        self.assertEqual(set(self.cards()), {"a|server", "b|engine"})
        self.stub.calls.clear()
        self.sync().main([item("a"), item("b", repo="eng")])
        self.assertEqual(self.stub.verbs(), [])

    def test_changed_title_updates_same_card(self):
        self.sync().main([item("a")])
        self.stub.calls.clear()
        self.sync().main([item("a", title="Renamed")])
        self.assertEqual(self.stub.verbs(), ["update"])
        self.assertEqual(self.stub.calls[0][2], "501")
        self.assertEqual(len(self.cards()), 1)

    def test_column_change_moves_card(self):
        self.sync().main([item("a")])
        self.stub.calls.clear()
        self.sync().main([item("a", "Done")])
        self.assertIn(["cards", "move", "501", "--card-table", "100", "--to", "106"], self.stub.calls)

    def test_extra_repos_make_one_card_per_board(self):
        self.side("extra-repos.json", {"a": ["engine"]})
        self.sync().main([item("a")])
        self.assertEqual(set(self.cards()), {"a|server", "a|engine"})

    def test_skip_and_unplaced(self):
        self.side("skip.json", ["a"])
        self.sync().main([item("a"), item("b", repo="elsewhere")])
        self.assertEqual(self.stub.calls, [])

    def test_dry_run_makes_no_calls_and_keeps_map(self):
        plan = self.sync(dry=True).main([item("a")])
        self.assertEqual(plan["create"], 1)
        self.assertEqual(self.stub.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "map.json")))
        self.sync().main([item("a")])
        self.stub.calls.clear()
        plan = self.sync(dry=True).main([item("a", "Done")])
        self.assertEqual((plan["create"], plan["move"]), (0, 1))
        self.assertEqual(self.stub.calls, [])

    def test_never_destructive_or_posting(self):
        self.sync().main([item("a", hold="q", hold_kind="captain")])
        self.sync().main([item("a", "Done")])
        for c in self.stub.calls:
            self.assertNotIn(c[0], ("chat", "messages", "trash", "archive"))
            self.assertFalse(c[0] == "comments" and c[1] != "list", c)
            self.assertFalse(c[0] == "cards" and c[1] not in ("create", "update", "move"), c)


class Comments(Base):
    def test_captain_comments_go_to_pending_once(self):
        self.stub.comments = {"501": [{"id": 9, "creator": {"id": CAPTAIN}, "content": "<p>yes</p>", "created_at": "t"},
                                      {"id": 10, "creator": {"id": 1}, "content": "other"}]}
        self.sync().main([item("a")])
        self.sync().main([item("a")])
        lines = open(os.path.join(self.cfgdir, "pending-comments.jsonl")).read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["text"], "yes")
        self.assertEqual(self.cards()["a|server"]["comments"], [9, 10])
        self.assertEqual(json.loads(lines[0])["kind"], "comment")


def boost(id, who=CAPTAIN, content="👍"):
    return {"id": id, "booster": {"id": who}, "content": content, "created_at": "t"}


class Boosts(Base):
    WAIT = dict(hold="pick", hold_kind="captain")

    def pending(self):
        p = os.path.join(self.cfgdir, "pending-comments.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def boost_calls(self):
        return [c for c in self.stub.calls if c[:2] == ["api", "get"] and c[2] != "/my/profile.json" and "/chats/" not in c[2]]

    def test_captain_thumbs_up_emitted_once(self):
        self.stub.boosts = {"501": [boost(7), boost(8, content="👍🏽")]}
        self.sync().main([item("a", **self.WAIT)])
        self.sync().main([item("a", **self.WAIT)])
        recs = self.pending()
        self.assertEqual([r["boost"] for r in recs], [7, 8])
        self.assertEqual(recs[0]["kind"], "approval")
        self.assertEqual((recs[0]["task"], recs[0]["repo"], recs[0]["card"], recs[0]["at"]), ("a", "server", 501, "t"))
        self.assertTrue(recs[0]["url"].endswith("/card_tables/cards/501"))
        self.assertNotIn("unassign", self.stub.verbs())
        self.assertTrue(self.cards()["a|server"]["assigned"])

    def test_others_and_other_emoji_ignored(self):
        self.stub.boosts = {"501": [boost(7, who=1), boost(8, content="🎉"), boost(9, content="👍👍")]}
        self.sync().main([item("a", **self.WAIT)])
        self.assertEqual(self.pending(), [])

    def test_unassigned_card_never_queried(self):
        self.stub.boosts = {"501": [boost(7)]}
        self.sync().main([item("a")])
        self.sync().main([item("a")])
        self.sync(dry=True).main([item("a")])
        self.assertEqual(self.boost_calls(), [])
        self.assertEqual(self.pending(), [])

    def test_dry_run_reads_but_records_nothing(self):
        self.sync().main([item("a", **self.WAIT)])
        self.stub.boosts = {"501": [boost(7)]}
        self.sync(dry=True).main([item("a", **self.WAIT)])
        self.assertEqual(len(self.boost_calls()), 2)
        self.assertEqual(self.pending(), [])
        self.assertNotIn("boosts", self.cards()["a|server"])


class Profile(Base):
    WAIT = dict(hold="pick", hold_kind="captain")

    def set_profile(self, name):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg["profile"] = name
        json.dump(cfg, open(p, "w"))

    def run_all_kinds(self):
        self.stub.comments = {"501": []}
        self.sync().main([item("a", **self.WAIT)])
        self.sync().main([item("a")])
        kinds = {tuple(c[:2]) for c in self.stub.calls}
        self.assertTrue({("cards", "create"), ("comments", "list"), ("api", "get"), ("unassign", "501")} <= kinds, kinds)

    def test_profile_passed_on_every_call(self):
        self.set_profile("firstmate")
        self.run_all_kinds()
        self.assertEqual(set(self.stub.profiles), {"firstmate"})

    def test_no_profile_flag_when_unset(self):
        self.run_all_kinds()
        self.assertEqual(set(self.stub.profiles), {None})

    def test_acting_user_comments_and_boosts_ignored(self):
        self.set_profile("firstmate")
        self.stub.comments = {"501": [{"id": 9, "creator": {"id": ACTING}, "content": "<p>mine</p>", "created_at": "t"}]}
        self.stub.boosts = {"501": [boost(7, who=ACTING)]}
        self.sync().main([item("a", **self.WAIT)])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "pending-comments.jsonl")))
        self.assertIn(["cards", "create"], [c[:2] for c in self.stub.calls])
        create = next(c for c in self.stub.calls if c[:2] == ["cards", "create"])
        self.assertEqual(create[create.index("--assignee") + 1], str(CAPTAIN))


class NoteHtml(Base):
    def test_blocks_have_no_raw_newlines(self):
        self.pr("a")
        it = dict(item("a", "In flight", hold="line one\nline two"), blocked_by=["b"], links=["https://github.com/o/r/pull/2"])
        body = self.sync().body_for(it, "Ready for QA", False, {})
        self.assertNotIn("\n", body.replace("line one\nline two", ""))
        self.assertTrue(body.startswith("<div>") and body.endswith("</div>"))
        self.assertIn("<ul><li><a href=\"https://github.com/o/r/pull/2\">", body)
        self.assertIn("<li><a href=\"https://github.com/o/r/pull/1\">", body)

    def test_decision_renders_numbered_list(self):
        self.side("decisions.json", {"a": {"question": "Pick <one>", "items": ["first", "second & more"], "note": "rec first"}})
        self.sync().main([item("a", hold="raw hold", hold_kind="captain")])
        body = self.stub.calls[0][3]
        self.assertIn("<div><strong>Waiting on you</strong>: Pick &lt;one&gt;</div>"
                      "<ol><li>first</li><li>second &amp; more</li></ol><div>rec first</div>", body)
        self.assertNotIn("raw hold", body)
        self.assertNotIn("\n", body)

    def test_decision_ignored_when_not_waiting(self):
        body = self.sync().body_for(item("a"), "Triage", False, {}, {"a": {"question": "Q", "items": ["x"]}})
        self.assertNotIn("<ol>", body)


URL = "http://lavish.example:4387/session/"


class Boards(Base):
    def sessions(self, *rows):
        head = "bin: ~/lavish\ndescription: x\nsessions[%d]{file,status,url,pending_prompts,listener}:\n" % len(rows)
        body = "".join(f'  {f},{st},"{u}",0,none\n' for f, st, u in rows)
        self.stub.lavish = head + body + "visual_guidance[1]: x\n"

    def board(self, task, name="review/index.html"):
        return os.path.join(self.home, "data", task, name)

    def body(self):
        return [c for c in self.stub.calls if c[:2] in (["cards", "create"], ["cards", "update"])][-1]

    def test_open_board_lands_on_card(self):
        self.sessions((self.board("a"), "open", URL + "aa"), (self.board("a", "b.html"), "open", URL + "ab"),
                      (self.board("other"), "open", URL + "zz"))
        self.sync().main([item("a")])
        note = self.stub.calls[0][3]
        self.assertIn(f'<strong>Plan board</strong>: <a href="{URL}aa">{URL}aa</a>, <a href="{URL}ab">', note)
        self.assertNotIn(URL + "zz", note)
        self.assertEqual(self.stub.lavish_calls, 1)
        self.assertLess(note.index("Plan board"), note.index("Kept in sync"))

    def test_closed_or_ended_session_is_skipped_and_link_drops(self):
        self.sessions((self.board("a"), "open", URL + "aa"))
        self.sync().main([item("a")])
        self.sessions((self.board("a"), "ended", URL + "aa"), (self.board("a", "x.html"), "feedback", URL + "ax"))
        self.sync().main([item("a")])
        self.assertEqual(self.body()[:2], ["cards", "update"])
        self.assertNotIn("Plan board", self.body()[-1])

    def test_boards_json_maps_scout_boards(self):
        self.side("boards.json", {"a": ["a-scout"]})
        self.sessions((self.board("a-scout"), "open", URL + "sc"))
        self.sync().main([item("a"), item("b")])
        notes = [c[3] for c in self.stub.calls if c[:2] == ["cards", "create"]]
        self.assertIn(URL + "sc", notes[0])
        self.assertNotIn(URL + "sc", notes[1])

    def test_lavish_failure_keeps_links_and_run_succeeds(self):
        self.sessions((self.board("a"), "open", URL + "aa"))
        self.sync().main([item("a")])
        n = len(self.stub.calls)
        for err in (FileNotFoundError("lavish-axi"), sync.subprocess.TimeoutExpired("lavish-axi", 15)):
            self.stub.lavish = err
            self.sync().main([item("a")])
        self.assertEqual([c for c in self.stub.calls[n:] if c[0] == "cards"], [])
        self.assertIn(URL + "aa", self.cards()["a|server"]["boards"])
        self.assertIn("lavish-axi unavailable", open(os.path.join(self.cfgdir, "sync.log")).read())

    def test_unchanged_link_makes_no_update(self):
        self.sessions((self.board("a"), "open", URL + "aa"))
        self.sync().main([item("a")])
        n = len(self.stub.calls)
        self.sync().main([item("a")])
        self.assertEqual([c for c in self.stub.calls[n:] if c[0] == "cards"], [])


class TasksAxi(unittest.TestCase):
    def test_show_parsing_and_mapping(self):
        text = '''task:
  id: ta-x
  title: "Fix it https://github.com/o/r/pull/7 (repo: srv) (kind: chore)"
  state: in_flight
  hold_reason: "Pick one"
  hold_kind: captain
  hold_until: "-"
  kind: ship
  repo: srv
  deps: "blocked-by:ta-a,blocked-by:ta-b"
  links: "pr:https://github.com/o/r/pull/7,pr:https://github.com/o/r/pull/8"
'''
        it = sync.task_item(sync.parse_show(text))
        self.assertEqual(it["title"], "Fix it")
        self.assertEqual(it["section"], "In flight")
        self.assertEqual(it["links"], ["https://github.com/o/r/pull/7", "https://github.com/o/r/pull/8"])
        self.assertEqual(it["blocked_by"], ["ta-a", "ta-b"])
        self.assertEqual((it["hold"], it["hold_kind"], it["until"]), ("Pick one", "captain", None))


if __name__ == "__main__":
    unittest.main()


class Acknowledge(Base):
    WAIT = dict(hold="pick", hold_kind="captain")
    CARD = "/buckets/22222222/recordings/501/boosts.json"
    COMMENT = "/buckets/22222222/recordings/9/boosts.json"

    def setUp(self):
        super().setUp()
        self.set_profile("firstmate")

    def set_profile(self, name):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg["profile"] = name
        json.dump(cfg, open(p, "w"))

    def comment(self):
        self.stub.comments = {"501": [{"id": 9, "creator": {"id": CAPTAIN}, "content": "yes", "created_at": "t"}]}

    def pending(self):
        p = os.path.join(self.cfgdir, "pending-comments.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def test_comment_boosted_once(self):
        self.comment()
        self.sync().main([item("a")])
        self.assertEqual(self.stub.posts(), [self.COMMENT])
        post = next(c for c in self.stub.calls if c[:2] == ["api", "post"])
        self.assertEqual(json.loads(post[4]), {"content": "👍"})
        self.assertEqual([a[:2] for a in self.cards()["a|server"]["acked"]], [[9, "👍"]])

    def test_approval_boosts_card(self):
        self.stub.boosts = {"501": [boost(7)]}
        self.sync().main([item("a", **self.WAIT)])
        self.assertEqual(self.stub.posts(), [self.CARD])

    def test_rerun_never_boosts_twice(self):
        self.comment()
        self.stub.boosts = {"501": [boost(7)]}
        for _ in range(3):
            self.sync().main([item("a", **self.WAIT)])
        self.assertEqual(sorted(self.stub.posts()), sorted([self.CARD, self.COMMENT]))

    def test_existing_acting_thumbs_up_skips_post(self):
        self.comment()
        self.stub.boosts = {"9": [boost(3, who=ACTING)]}
        self.sync().main([item("a")])
        self.assertEqual(self.stub.posts(), [])
        self.assertEqual(self.cards()["a|server"]["acked"], [[9, "👍", 3]])

    def test_acting_is_captain_or_no_profile_never_boosts(self):
        self.comment()
        self.stub.me = CAPTAIN
        self.sync().main([item("a")])
        self.set_profile(None)
        self.stub.comments["501"].append({"id": 10, "creator": {"id": CAPTAIN}, "content": "more"})
        self.sync().main([item("a")])
        self.assertEqual(self.stub.posts(), [])
        self.assertEqual(len(self.pending()), 2)
        self.assertNotIn("/my/profile.json", [c[2] for c in self.stub.calls if c[:2] == ["api", "get"]][1:])

    def test_failure_keeps_record_and_retries(self):
        self.comment()
        self.stub.fail_post = True
        self.sync().main([item("a")])
        self.assertEqual(len(self.pending()), 1)
        self.assertNotIn("acked", self.cards()["a|server"])
        self.stub.fail_post = False
        self.sync().main([item("a")])
        self.assertEqual(self.stub.posts(), [self.COMMENT, self.COMMENT])
        self.assertEqual([a[:2] for a in self.cards()["a|server"]["acked"]], [[9, "👍"]])
        self.assertEqual(len(self.pending()), 1)

    def test_identity_looked_up_once_per_run(self):
        self.comment()
        self.stub.comments["502"] = [{"id": 11, "creator": {"id": CAPTAIN}, "content": "x"}]
        self.sync().main([item("a"), item("b")])
        self.assertEqual(sum(c[:3] == ["api", "get", "/my/profile.json"] for c in self.stub.calls), 1)
        self.assertEqual(len(self.stub.posts()), 2)

    def test_dry_run_plans_without_posting(self):
        self.sync().main([item("a", **self.WAIT)])
        self.stub.boosts = {"501": [boost(7)]}
        self.sync(dry=True).main([item("a", **self.WAIT)])
        self.assertEqual(self.stub.posts(), [])
        log = open(os.path.join(self.cfgdir, "sync.log")).read()
        self.assertIn("dry a|server: acknowledge 501 with 👍", log)
        self.assertNotIn("ack", self.cards()["a|server"])

    def test_own_boost_never_read_as_approval(self):
        self.stub.boosts = {"501": [boost(7)]}
        self.sync().main([item("a", **self.WAIT)])
        self.sync().main([item("a", **self.WAIT)])
        self.assertTrue(any(b["booster"]["id"] == ACTING for b in self.stub.boosts["501"]))
        self.assertEqual([r["boost"] for r in self.pending()], [7])

    def question(self):
        self.stub.comments = {"501": [{"id": 9, "creator": {"id": CAPTAIN}, "content": "<p>why &amp; how?</p>", "created_at": "t"}]}

    def reply(self, text="Because.\n\nSee <here> & there", **kw):
        return self.sync(dry=kw.pop("dry", False)).reply(9, text, **kw)

    def test_question_gets_eyes_not_thumbs(self):
        self.question()
        self.sync().main([item("a")])
        self.sync().main([item("a")])
        self.assertEqual(self.stub.posted(), [("9", "👀")])
        self.assertEqual(self.pending()[0]["kind"], "question")

    def test_non_question_gets_thumbs(self):
        self.comment()
        self.sync().main([item("a")])
        self.assertEqual(self.stub.posted(), [("9", "👍")])
        self.assertEqual(self.pending()[0]["kind"], "comment")

    def test_reply_posts_once_then_removes_eyes(self):
        self.question()
        self.sync().main([item("a")])
        eyes = self.stub.boosts["9"][0]["id"]
        self.assertTrue(self.reply())
        self.assertEqual(self.stub.replies(), [("501", "<div>Because.</div><div>See &lt;here&gt; &amp; there</div>")])
        self.assertEqual(self.stub.deletes(), [f"/buckets/22222222/boosts/{eyes}.json"])
        self.assertEqual(self.stub.boosts["9"], [])
        self.assertEqual(self.stub.posted(), [("9", "👀")])
        self.assertEqual(self.cards()["a|server"]["replied"], [9])
        call_order = [c[1] for c in self.stub.calls if c[:1] == ["api"] and c[1] in ("post", "delete")]
        self.assertEqual(call_order[-2:], ["post", "delete"])

    def test_second_reply_refused_without_again(self):
        self.question()
        self.sync().main([item("a")])
        self.reply()
        self.assertFalse(self.reply())
        self.assertEqual(len(self.stub.replies()), 1)
        self.assertTrue(self.reply(again=True))
        self.assertEqual(len(self.stub.replies()), 2)

    def test_reply_failure_keeps_eyes_and_records_nothing(self):
        self.question()
        self.sync().main([item("a")])
        self.stub.fail_post = True
        with self.assertRaises(RuntimeError):
            self.reply()
        self.assertEqual([b["content"] for b in self.stub.boosts["9"]], ["👀"])
        self.assertEqual(self.stub.deletes(), [])
        self.assertNotIn("replied", self.cards()["a|server"])

    def test_no_reply_without_profile(self):
        self.question()
        self.sync().main([item("a")])
        self.set_profile(None)
        self.assertFalse(self.reply())
        self.assertEqual(self.stub.replies(), [])

    def test_reply_dry_run_posts_nothing(self):
        self.question()
        self.sync().main([item("a")])
        self.reply(dry=True)
        self.assertEqual((self.stub.replies(), self.stub.deletes()), ([], []))

    def test_own_reply_never_captured(self):
        self.question()
        self.sync().main([item("a")])
        self.reply()
        self.sync().main([item("a")])
        self.assertEqual([r["comment"] for r in self.pending()], [9])
        self.assertEqual(len(self.cards()["a|server"]["comments"]), 2)

    def test_sync_never_posts_comments(self):
        self.question()
        self.stub.boosts = {"501": [boost(7)]}
        for _ in range(2):
            self.sync().main([item("a", **self.WAIT)])
        self.assertEqual(self.stub.replies(), [])

    def test_acting_captain_gets_no_boosts_at_all(self):
        self.question()
        self.stub.me = CAPTAIN
        self.sync().main([item("a")])
        self.assertFalse(self.reply())
        self.assertEqual((self.stub.deletes(), self.stub.posted(), self.stub.replies()), ([], [], []))


def line(id, who=CAPTAIN, content="where is it?"):
    return {"id": id, "creator": {"id": who}, "content": content, "created_at": "t", "app_url": f"https://x/chats/1@{id}"}


class Chats(Base):
    set_profile, pending = Acknowledge.set_profile, Acknowledge.pending

    def setUp(self):
        super().setUp()
        self.set_profile("firstmate")
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg["chats"] = [77]
        json.dump(cfg, open(p, "w"))
        self.stub.lines = {"77": [line(1, content="old?")]}
        self.sync().main([])  # the first run only sets the cursor

    def state(self):
        return json.load(open(os.path.join(self.cfgdir, "chats.json")))["77"]

    def test_first_run_sets_cursor_without_capturing(self):
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.state()["cursor"], 1)

    def test_captain_question_captured_with_eyes_once(self):
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.sync().main([])
        recs = self.pending()
        self.assertEqual(len(recs), 1)
        self.assertEqual({k: recs[0][k] for k in ("kind", "chat", "line", "url", "text")},
                         {"kind": "chat-question", "chat": 77, "line": 2, "url": "https://x/chats/1@2", "text": "where is it?"})
        self.assertEqual(self.stub.posted(), [("2", "👀")])

    def test_mention_without_question_mark_captured(self):
        self.stub.lines["77"] += [line(2, content=f'<bc-attachment sgid="sgid-{ACTING}"></bc-attachment> look at this'),
                                  line(3, content="just chatting")]
        self.sync().main([])
        self.assertEqual([r["line"] for r in self.pending()], [2])

    def test_other_people_and_acting_user_ignored(self):
        self.stub.lines["77"] += [line(2, who=1), line(3, who=ACTING)]
        self.sync().main([])
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.posted(), [])
        self.assertEqual(self.state()["cursor"], 3)

    def test_reply_posts_in_chat_and_removes_eyes(self):
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.assertTrue(self.sync().reply(2, "Here."))
        self.assertEqual(self.stub.chat_posts(), [("77", "<div>Here.</div>")])
        self.assertEqual(self.stub.boosts["2"], [])
        self.assertEqual(self.stub.replies(), [])
        self.assertFalse(self.sync().reply(2, "Here."))
        self.sync().main([])
        self.assertEqual(len(self.pending()), 1)

    def test_reply_turns_at_names_into_mentions(self):
        self.stub.pingable = [{"id": 1, "name": "Caleb LeNoir", "attachable_sgid": "sg-caleb"},
                              {"id": 2, "name": "Fitz", "attachable_sgid": "sg-fitz"},
                              {"id": 3, "name": "Randy Johnson", "attachable_sgid": "sg-randy"},
                              {"id": 4, "name": "Randy Smith", "attachable_sgid": "sg-randys"}]
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.assertTrue(self.sync().reply(2, "@Caleb.LeNoir and @fitz: ask @Randy, @Nobody, a@b.io or `@Fitz`."))
        tag = '<bc-attachment sgid="{}" content-type="application/vnd.basecamp.mention"></bc-attachment>'
        self.assertEqual(self.stub.chat_posts(), [("77", f"<div>{tag.format('sg-caleb')} and {tag.format('sg-fitz')}: "
                                                         "ask @Randy, @Nobody, a@b.io or @Fitz.</div>")])
        self.assertEqual(sum(c[:2] == ["people", "pingable"] for c in self.stub.calls), 1)

    def test_reply_never_mentions_someone_outside_the_project(self):
        project = str(json.load(open(os.path.join(self.cfgdir, "config.json")))["project"])
        self.stub.pingable = [{"id": 1, "name": "Caleb LeNoir", "attachable_sgid": "sg-caleb"},
                              {"id": 9, "name": "Randy Outsider", "attachable_sgid": "sg-randy"}]
        self.stub.project_people = {project: [{"id": 1, "name": "Caleb LeNoir"}, {"id": ACTING, "name": "Agent"}]}
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.assertTrue(self.sync().reply(2, "@Caleb, ask @Randy or @Randy.Outsider."))
        tag = '<bc-attachment sgid="sg-caleb" content-type="application/vnd.basecamp.mention"></bc-attachment>'
        self.assertEqual(self.stub.chat_posts(), [("77", f"<div>{tag}, ask @Randy or @Randy.Outsider.</div>")])
        self.assertEqual(self.stub.calls.count(["api", "get", f"/projects/{project}/people.json"]), 1)

    def test_reply_keeps_at_names_as_text_when_the_project_people_cannot_be_read(self):
        project = str(json.load(open(os.path.join(self.cfgdir, "config.json")))["project"])
        self.stub.pingable = [{"id": 1, "name": "Caleb LeNoir", "attachable_sgid": "sg-caleb"}]
        self.stub.project_people = {project: None}
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.assertTrue(self.sync().reply(2, "@Caleb done."))
        self.assertEqual(self.stub.chat_posts(), [("77", "<div>@Caleb done.</div>")])

    def test_reply_in_another_projects_chat_mentions_only_that_projects_people(self):
        self.stub.pingable = [{"id": 1, "name": "Caleb LeNoir", "attachable_sgid": "sg-caleb"},
                              {"id": 9, "name": "Randy Outsider", "attachable_sgid": "sg-randy"}]
        self.stub.project_people = {"555": [{"id": 9, "name": "Randy Outsider"}]}
        state = json.load(open(os.path.join(self.cfgdir, "chats.json")))
        state["77"]["bucket"] = 555
        json.dump(state, open(os.path.join(self.cfgdir, "chats.json"), "w"))
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.assertTrue(self.sync().reply(2, "@Caleb, ask @Randy."))
        tag = '<bc-attachment sgid="sg-randy" content-type="application/vnd.basecamp.mention"></bc-attachment>'
        self.assertEqual(self.stub.chat_posts(), [("77", f"<div>@Caleb, ask {tag}.</div>")])
        self.assertIn(["api", "get", "/projects/555/people.json"], self.stub.calls)

    def test_reply_without_at_names_reads_no_people(self):
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.sync().reply(2, "Mail x@y.io.")
        self.assertEqual(self.stub.chat_posts(), [("77", "<div>Mail x@y.io.</div>")])
        self.assertNotIn(["people", "pingable"], [c[:2] for c in self.stub.calls])
        self.assertFalse(any(c[:2] == ["api", "get"] and c[2].endswith("/people.json") for c in self.stub.calls))

    def test_chat_reply_failure_keeps_eyes(self):
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.stub.fail_post = True
        with self.assertRaises(RuntimeError):
            self.sync().reply(2, "Here.")
        self.assertEqual([b["content"] for b in self.stub.boosts["2"]], ["👀"])
        self.assertNotIn("replied", self.state())

    def test_acting_captain_captures_but_never_boosts(self):
        self.stub.me = CAPTAIN
        self.stub.lines["77"].append(line(2))
        self.sync().main([])
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.stub.posted(), [])
        self.assertFalse(self.sync().reply(2, "x"))
        self.assertEqual(self.stub.chat_posts(), [])

    def test_sync_never_posts_to_chat(self):
        self.stub.lines["77"].append(line(2))
        for _ in range(2):
            self.sync().main([])
        self.assertEqual(self.stub.chat_posts(), [])

    def test_every_line_chat_captures_plain_lines(self):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg["chats"] = [{"chat": 77, "every_line": True}]
        json.dump(cfg, open(p, "w"))
        self.stub.lines["77"] += [line(2, content="just chatting"), line(3, who=1, content="hi")]
        self.sync().main([])
        self.sync().main([])
        self.assertEqual([(r["kind"], r["line"]) for r in self.pending()], [("chat-question", 2)])
        self.assertEqual(self.stub.posted(), [("2", "👀")])

    def test_plain_lines_skipped_by_default(self):
        self.stub.lines["77"].append(line(2, content="just chatting"))
        self.sync().main([])
        self.assertEqual(self.pending(), [])


class Ask(Base):
    set_profile = Acknowledge.set_profile

    def setUp(self):
        super().setUp()
        self.set_profile("agent")
        self.cfg(ask_chat=77)

    def cfg(self, **kw):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg.update(kw)
        json.dump(cfg, open(p, "w"))

    def test_posts_line_mentioning_owner(self):
        self.assertTrue(self.sync().ask("Ship it?\n\nOr wait."))
        [(chat, body)] = self.stub.chat_posts()
        self.assertEqual(chat, "77")
        self.assertEqual(body, '<div><bc-attachment sgid="sgid-owner" content-type="application/vnd.basecamp.mention">'
                               '</bc-attachment> Ship it?</div><div>Or wait.</div>')
        post = next(a for a in self.stub.calls if a[:2] == ["api", "post"])
        self.assertEqual(json.loads(post[4])["content_type"], "text/html")

    def test_refused_as_owner_without_profile_or_in_dry_run(self):
        self.stub.me = CAPTAIN
        self.assertFalse(self.sync().ask("x"))
        self.stub.me = ACTING
        self.assertFalse(self.sync(dry=True).ask("x"))
        self.cfg(profile=None)
        self.assertFalse(self.sync().ask("x"))
        self.assertEqual(self.stub.chat_posts(), [])

    def test_unset_ask_chat_fails(self):
        self.cfg(ask_chat=None)
        with self.assertRaises(RuntimeError):
            self.sync().ask("x")

    def test_sync_never_asks(self):
        self.sync().main([item("a")])
        self.assertEqual(self.stub.chat_posts(), [])


class Checkins(Base):
    set_profile, pending = Acknowledge.set_profile, Acknowledge.pending

    def setUp(self):
        super().setUp()
        self.set_profile("agent")
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg["checkins"] = {"questionnaires": [55], "timezone": "UTC"}
        json.dump(cfg, open(p, "w"))
        self.now = sync.datetime(2026, 10, 2, 9, 30, tzinfo=sync.timezone.utc)  # a Friday
        self.stub.questions = [self.q(1)]

    def q(self, id, days=(1, 2, 3, 4, 5), hour=9, minute=0, start="2026-01-01", paused=False):
        return {"id": id, "title": "Open issues?", "app_url": f"https://x/questions/{id}", "paused": paused,
                "schedule": {"frequency": "every_week", "days": list(days), "hour": hour, "minute": minute, "start_date": start}}

    def sync(self, dry=False):
        s = super().sync(dry)
        s.today = lambda: self.now
        return s

    def test_due_question_recorded_once_per_day(self):
        self.sync().main([])
        self.sync().main([])
        recs = self.pending()
        self.assertEqual([(r["kind"], r["question"], r["date"], r["questionnaire"]) for r in recs],
                         [("checkin", 1, "2026-10-02", 55)])
        self.now = self.now.replace(day=5)  # Monday
        self.sync().main([])
        self.assertEqual([r["date"] for r in self.pending()], ["2026-10-02", "2026-10-05"])

    def test_not_due(self):
        self.stub.questions = [self.q(1, hour=10), self.q(2, days=(0, 6)), self.q(3, start="2026-10-03"),
                               self.q(4, paused=True), self.q(5, minute=31)]
        self.sync().main([])
        self.assertEqual(self.pending(), [])

    def test_sunday_is_zero(self):
        self.now = self.now.replace(day=4)  # Sunday
        self.stub.questions = [self.q(1, days=(0,))]
        self.sync().main([])
        self.assertEqual(len(self.pending()), 1)

    def test_already_answered_today_not_recorded(self):
        self.stub.answers["1"] = [{"creator": {"id": ACTING}, "group_on": "2026-10-02"}]
        self.sync().main([])
        self.assertEqual(self.pending(), [])

    def test_answer_once_per_day(self):
        self.sync().main([])
        self.assertTrue(self.sync().answer(1, "None today."))
        creates = [a for a in self.stub.calls if a[:3] == ["checkins", "answer", "create"]]
        self.assertEqual(creates, [["checkins", "answer", "create", "1", "<div>None today.</div>", "--date", "2026-10-02"]])
        self.assertFalse(self.sync().answer(1, "again"))
        os.remove(os.path.join(self.cfgdir, "checkins.json"))
        self.assertFalse(self.sync().answer(1, "again"))  # Basecamp already has today's answer
        self.assertEqual(len([a for a in self.stub.calls if a[:3] == ["checkins", "answer", "create"]]), 1)

    def test_answer_renders_markdown(self):
        self.sync().main([])
        self.assertTrue(self.sync().answer(1, "**Shipped**: the *map* fix\n- one `<b>`\n- [PR](https://x.io/1)"))
        create, = [a for a in self.stub.calls if a[:3] == ["checkins", "answer", "create"]]
        self.assertEqual(create[4], '<div><strong>Shipped</strong>: the <em>map</em> fix</div>'
                                    '<ul><li>one &lt;b&gt;</li><li><a href="https://x.io/1">PR</a></li></ul>')

    def test_answer_refused_as_owner_or_dry(self):
        self.stub.me = CAPTAIN
        self.assertFalse(self.sync().answer(1, "x"))
        self.stub.me = ACTING
        self.assertFalse(self.sync(dry=True).answer(1, "x"))
        self.assertEqual([a for a in self.stub.calls if a[0] == "checkins"], [])

    def test_sync_never_answers_and_dry_records_nothing(self):
        self.sync(dry=True).main([])
        self.assertEqual(self.pending(), [])
        self.sync().main([])
        self.assertEqual([a for a in self.stub.calls if a[0] == "checkins"], [])

    def test_off_by_default(self):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        del cfg["checkins"]
        json.dump(cfg, open(p, "w"))
        self.sync().main([])
        self.assertEqual(self.pending(), [])
        self.assertFalse([a for a in self.stub.calls if "/questionnaires/" in " ".join(a)])


class Releases(Base):
    """GitHub release announcements on the Message Board; gh and basecamp are stubbed."""

    def setUp(self):
        super().setUp()
        cfg = json.load(open(os.path.join(self.cfgdir, "config.json")))
        cfg["releases"] = {"board": "77", "repos": {"acme/terminal": {"name": "terminal", "note": "Run `ta upgrade` to install."}}}
        json.dump(cfg, open(os.path.join(self.cfgdir, "config.json"), "w"))
        self.rels = [self.rel("v0.1.0", "2026-01-01T00:00:00Z")]
        self.messages, self.fail_message = [], False

    def rel(self, tag, at, draft=False, pre=False):
        return {"tagName": tag, "publishedAt": at, "isDraft": draft, "isPrerelease": pre}

    def runner(self, cmd, **kw):
        if cmd[0] == "gh":
            if cmd[2] == "list":
                return SimpleNamespace(stdout=json.dumps(self.rels), stderr="", returncode=0)
            tag = cmd[3]
            return SimpleNamespace(stdout=json.dumps({"tagName": tag, "url": f"https://github.com/acme/terminal/releases/tag/{tag}",
                                                      "body": "## What's Changed\n* Faster **maps** in https://github.com/acme/terminal/pull/9 <b>"}),
                                   stderr="", returncode=0)
        if cmd[0] != "basecamp":
            return self.stub(cmd, **kw)
        args = cmd[5:-3] if cmd[3] == "-P" else cmd[3:-3]
        if args[:2] == ["api", "post"] and args[2].endswith("/messages.json"):
            self.stub.calls.append(args)
            if self.fail_message:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            self.messages.append((args[2], json.loads(args[4])))
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": {"id": 1000 + len(self.messages)}}), stderr="", returncode=0)
        return self.stub(cmd, **kw)

    def run_sync(self, dry=False, pre=False):
        sync.Sync(self.home, os.path.join(self.cfgdir, "config.json"), dry=dry, runner=self.runner, prereleases=pre).main([])

    def test_seeding_announces_nothing(self):
        self.run_sync()
        self.assertEqual(self.messages, [])
        state = json.load(open(os.path.join(self.cfgdir, "releases.json")))
        self.assertEqual(state["acme/terminal"]["seeded"], ["v0.1.0"])

    def test_new_release_posts_once(self):
        self.run_sync()
        self.rels.insert(0, self.rel("v0.2.0", "2999-01-01T00:00:00Z"))
        self.run_sync()
        (path, msg), = self.messages
        self.assertEqual(path, f"/buckets/{json.load(open(os.path.join(self.cfgdir, 'config.json')))['project']}/message_boards/77/messages.json")
        self.assertEqual(msg["subject"], "Terminal v0.2.0 released")
        self.assertIn("<div><strong>What&#x27;s Changed</strong></div><ul><li>Faster <strong>maps</strong> in "
                      '<a href="https://github.com/acme/terminal/pull/9">', msg["content"])
        self.assertIn("&lt;b&gt;", msg["content"])
        self.assertIn('<a href="https://github.com/acme/terminal/releases/tag/v0.2.0">', msg["content"])
        self.assertIn("Run ta upgrade to install.", msg["content"])
        self.assertNotIn("\n", msg["content"])
        self.run_sync()
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(json.load(open(os.path.join(self.cfgdir, "releases.json")))["acme/terminal"]["announced"], {"v0.2.0": 1001})

    def test_drafts_and_prereleases_skipped(self):
        self.run_sync()
        self.rels += [self.rel("v0.3.0", None, draft=True), self.rel("v0.3.0-rc1", "2999-01-01T00:00:00Z", pre=True)]
        self.run_sync()
        self.assertEqual(self.messages, [])
        self.run_sync(pre=True)
        self.assertEqual([m["subject"] for _, m in self.messages], ["Terminal v0.3.0-rc1 released"])

    def test_post_failure_retried(self):
        self.run_sync()
        self.rels.insert(0, self.rel("v0.2.0", "2999-01-01T00:00:00Z"))
        self.fail_message = True
        self.run_sync()
        self.assertEqual(self.messages, [])
        self.assertIn("retrying next run", open(os.path.join(self.cfgdir, "sync.log")).read())
        self.fail_message = False
        self.run_sync()
        self.assertEqual(len(self.messages), 1)

    def test_posts_even_as_captain_and_dry_run_posts_nothing(self):
        self.stub.me = CAPTAIN
        self.run_sync(dry=True)
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "releases.json")))
        self.run_sync()
        self.rels.insert(0, self.rel("v0.2.0", "2999-01-01T00:00:00Z"))
        self.run_sync(dry=True)
        self.assertEqual(self.messages, [])
        self.run_sync()
        self.assertEqual(len(self.messages), 1)


class CardsOff(Releases):
    """A config without the card mirror runs every other piece and never reads the backlog or touches cards."""
    pending = Acknowledge.pending

    def setUp(self):
        super().setUp()
        self.cfg(drop=("cards", "tables", "repos"), profile="agent", chats=[77], ask_chat=77,
                 checkins={"questionnaires": [55], "timezone": "UTC"})
        self.stub.lines = {"77": [line(1, content="old?")]}
        self.stub.questions = [Checkins.q(self, 1)]
        self.now = sync.datetime(2026, 10, 2, 9, 30, tzinfo=sync.timezone.utc)

    def cfg(self, drop=(), **kw):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        for k in drop:
            cfg.pop(k)
        cfg.update(kw)
        json.dump(cfg, open(p, "w"))

    def make(self, dry=False):
        s = sync.Sync(self.home, os.path.join(self.cfgdir, "config.json"), dry=dry, runner=self.runner)
        s.today = lambda: self.now
        return s

    def card_calls(self):
        return [a for a in self.stub.calls if a[0] in ("cards", "comments", "unassign")
                or "/card_tables/" in " ".join(a) or "/recordings/" in " ".join(a) and "/boosts.json" not in a[2]]

    def test_every_other_piece_runs_without_cards(self):
        self.make().main()  # no items passed: tasks-axi would raise in the stub if it were called
        self.stub.lines["77"].append(line(2))
        self.rels.insert(0, self.rel("v0.2.0", "2999-01-01T00:00:00Z"))
        self.make().main()
        self.assertEqual(sorted(r["kind"] for r in self.pending()), ["chat-question", "checkin"])
        self.assertEqual(self.stub.posted(), [("2", "👀")])
        self.assertEqual([m["subject"] for _, m in self.messages], ["Terminal v0.2.0 released"])
        self.assertTrue(self.make().reply(2, "Here."))
        self.assertTrue(self.make().ask("Ship it?"))
        self.assertTrue(self.make().answer(1, "None today."))
        self.assertEqual(self.card_calls(), [])
        self.assertEqual(self.stub.lavish_calls, 0)
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "map.json")))

    def test_cards_false_ignores_configured_tables(self):
        self.cfg(cards=False, tables={"server": {"table": "100"}}, repos={"srv": "server"})
        s = self.make()
        self.assertEqual((s.cards, s.tables, s.repo_map), (False, {}, {}))
        self.make().main()
        self.make(dry=True).main()
        self.assertEqual(self.card_calls(), [])

    def test_cards_on_without_tables_refused(self):
        self.cfg(cards=True)
        with self.assertRaises(ValueError):
            self.make()

    def test_tables_mean_cards_on_by_default(self):
        self.cfg(tables={"server": {"table": "100", **{c: str(101 + n) for n, c in enumerate(sync.COLUMNS)}}},
                 repos={"srv": "server"})
        self.assertTrue(self.make().cards)
        self.make().main([item("a")])
        self.assertIn(["cards", "create"], [a[:2] for a in self.stub.calls])


class RenderReply(unittest.TestCase):
    def test_plain_text_unchanged(self):
        self.assertEqual(sync.render_reply("On it.\nSoon <b> & more\n\nDone."),
                         "<div>On it.<br>Soon &lt;b&gt; &amp; more</div><div>Done.</div>")

    def test_inline_markdown(self):
        self.assertEqual(sync.render_reply("**Bold** and __b__, *it* and _it_, `a *b* <c>`, ~~gone~~, snake_case_name"),
                         "<div><strong>Bold</strong> and <strong>b</strong>, <em>it</em> and <em>it</em>, "
                         "a *b* &lt;c&gt;, <strike>gone</strike>, snake_case_name</div>")

    def test_links(self):
        self.assertEqual(sync.render_reply('[the "PR"](https://github.com/o/r/pull/2) or https://x.io/a_b'),
                         '<div><a href="https://github.com/o/r/pull/2">the &quot;PR&quot;</a> or '
                         '<a href="https://x.io/a_b">https://x.io/a_b</a></div>')

    def test_lists_headings_quotes_and_code(self):
        md = "## Today\nDid:\n- one\n* two\n1. first\n2) second\n\n> quoted\n```\nx < y\n  z\n```\nafter"
        self.assertEqual(sync.render_reply(md),
                         "<div><strong>Today</strong></div><div>Did:</div><ul><li>one</li><li>two</li></ul>"
                         "<ol><li>first</li><li>second</li></ol><blockquote>quoted</blockquote>"
                         "<pre>x &lt; y<br>  z</pre><div>after</div>")

    def test_mentions_outside_code_links_and_emails(self):
        tags = {"Caleb.LeNoir": "<M1>", "Fitz": "<M2>"}
        body = sync.render_reply("@Caleb.LeNoir, **@Fitz**'s turn.\n- see https://x.io/@Fitz and [@Fitz](https://x.io)\n"
                                 "> me@Fitz.io `@Fitz`\n```\n@Fitz\n```", tags.get)
        self.assertEqual(body, '<div><M1>, <strong><M2></strong>&#x27;s turn.</div><ul><li>see '
                               '<a href="https://x.io/@Fitz">https://x.io/@Fitz</a> and <a href="https://x.io">@Fitz</a>'
                               '</li></ul><blockquote>me@Fitz.io @Fitz</blockquote><pre>@Fitz</pre>')
        self.assertEqual(sync.render_reply("@Fitz"), "<div>@Fitz</div>")

    def test_no_mentions_in_quotes(self):
        tags = {"Fitz": "<M2>"}.get
        self.assertEqual(sync.render_reply("> @Fitz can you check?\n>@Fitz\n\n@Fitz", tags),
                         "<blockquote>@Fitz can you check?</blockquote><blockquote>@Fitz</blockquote><div><M2></div>")
        self.assertEqual(sync.render_reply("<blockquote>@Fitz said</blockquote> @Fitz", tags),
                         "<div>&lt;blockquote&gt;@Fitz said&lt;/blockquote&gt; @Fitz</div>")
        self.assertEqual(sync.render_reply("<BLOCKQUOTE cite='x'>\n@Fitz said\n</blockquote>\n\n@Fitz", tags),
                         "<div>&lt;BLOCKQUOTE cite=&#x27;x&#x27;&gt;<br>@Fitz said<br>&lt;/blockquote&gt;</div><div><M2></div>")
        self.assertEqual(sync.render_reply("- <blockquote>@Fitz\n- still quoted</blockquote>\n- @Fitz", tags),
                         "<ul><li>&lt;blockquote&gt;@Fitz</li><li>still quoted&lt;/blockquote&gt;</li><li><M2></li></ul>")

    def test_mentions_inside_emphasis_but_not_in_emails(self):
        tags = {"Caleb.LeNoir": "<M1>", "Fitz": "<M2>"}
        body = sync.render_reply("__@Caleb.LeNoir__ _@Fitz_ *@Fitz* ~~@Fitz~~ @Fitz-, snake_case@Fitz.io a_@Fitz.io "
                                 "b__@Fitz.io `_@Fitz_`", tags.get)
        self.assertEqual(body, "<div><strong><M1></strong> <em><M2></em> <em><M2></em> <strike><M2></strike> <M2>-, "
                               "snake_case@Fitz.io a_@Fitz.io b__@Fitz.io _@Fitz_</div>")

    def test_mentions_with_apostrophes(self):
        tags = {"Sean.O'Connor": "<M1>", "Fitz": "<M2>"}
        body = sync.render_reply("@Sean.O'Connor, @Sean.O’Connor's and @Fitz's or @Fitz’s turn, @Sean.O'Nobody",
                                 lambda h: tags.get(h.replace("’", "'")))
        self.assertEqual(body, "<div><M1>, <M1>&#x27;s and <M2>&#x27;s or <M2>’s turn, @Sean.O&#x27;Nobody</div>")

    def test_no_raw_html_or_newlines(self):
        body = sync.render_reply("<script>x</script>\n- <i>a</i>\n\n**<b>**")
        self.assertNotIn("<script>", body)
        self.assertNotIn("<i>", body)
        self.assertNotIn("\n", body)
