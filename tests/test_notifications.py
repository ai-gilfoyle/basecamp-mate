"""Tests for the notifications behavior: the /my/readings.json reader and its dispatch to the thread readers, the
/my/boosts.json reader, marking read only after the record is written, unmonitored input, the timer's cadences and the
upgrade from a config with the retired "listen".

/my/readings.json, /my/boosts.json and every thread are stubbed; nothing touches the network.
"""
import base64, io, json, os, re, sys, unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, item, line  # noqa: E402
from test_tools import TodoStub, ToolBase, boost, comment  # noqa: E402
import behaviors  # noqa: E402
import sync  # noqa: E402

PROJECT = 22222222
OTHER = 44444444  # another project of the account
MENTION = f'<bc-attachment sgid="sgid-{ACTING}" content-type="application/vnd.basecamp.mention"></bc-attachment>'
EYES = "\U0001F440"
APP = "https://app.basecamp.com/1"


def note(id, kind="Comment", thread=1, anchor=None, path="todos", section="inbox", bucket=PROJECT, who=CAPTAIN,
         at="2099-01-01T00:00:01Z", title="Re: a thing", excerpt="hi"):
    """One item of /my/readings.json, shaped like Basecamp's."""
    app = (f"{APP}/circles/{bucket}" if path == "circles" else
           f"{APP}/buckets/{bucket}/{'card_tables/cards' if path == 'cards' else path}/{thread}")
    if anchor is not None and path == "chats":
        app += f"@{anchor}"
    elif anchor is not None:
        app += f"#__recording_{anchor}"
    gid = base64.b64encode(f"gid://bc3/Recording/{anchor or thread}".encode()).decode().rstrip("=")
    return {"id": id, "section": section, "type": kind, "title": title, "content_excerpt": excerpt,
            "bucket_name": f"Project {bucket}", "creator": {"id": who, "name": {CAPTAIN: "Cap"}.get(who)},
            "created_at": at, "unread_at": at, "updated_at": at, "read_at": None, "unread_count": 1,
            "readable_identifier": gid, "readable_sgid": f"sgid-{id}", "app_url": app,
            "subscription_url": f"https://3.basecampapi.com/1/buckets/{bucket}/recordings/{thread}/subscription.json"}


def my_boost(id, rid, rtype="Chat::Lines::RichText", parent=None, who=CAPTAIN, content="ok", bucket=PROJECT, title="x"):
    """One item of /my/boosts.json: a boost on one of the acting user's recordings."""
    return {"id": id, "content": content, "created_at": "2099-01-01T00:00:02Z", "booster": {"id": who, "name": "Cap"},
            "recording": {"id": rid, "type": rtype, "title": title, "app_url": f"{APP}/buckets/{bucket}/r/{rid}",
                          "bucket": {"id": bucket, "name": f"Project {bucket}", "type": "Project"}, "parent": parent or {}}}


class NotifStub(TodoStub):
    """Adds single chat lines, comments, messages and recordings refetched by id, and failing calls on demand."""

    def __init__(self):
        super().__init__()
        self.parents, self.recordings, self.messages = {}, {}, {}  # comment id -> parent; recording id -> JSON; message id -> JSON
        self.fail = set()  # a substring of a call's arguments that makes it fail
        self.on_mark = None  # called on each `notifications read`

    def __call__(self, cmd, **kw):
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            core = args[2:] if args[:1] == ["-P"] else args
            if any(f in " ".join(core) for f in self.fail):
                self.calls.append(core)
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            if core[:2] == ["notifications", "read"] and self.on_mark:
                self.on_mark()
            path = core[2] if core[:2] == ["api", "get"] else ""
            m = re.fullmatch(r"/buckets/\d+/(chats/(\d+)/lines|comments|messages|recordings)/(\d+)\.json", path)
            if m:
                self.calls.append(core)
                rid = int(m.group(3))
                if m.group(2):
                    data = next((ln for ln in self.lines.get(m.group(2), []) if ln["id"] == rid), {})
                elif m.group(1) == "comments":
                    data = {"id": rid, "parent": self.parents.get(rid, {}), **self.recordings.get(rid, {})}
                elif m.group(1) == "messages":
                    data = self.messages.get(rid, {})
                else:
                    data = self.recordings.get(rid, {})
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def gets(self):
        return [c[2] for c in self.calls if c[:2] == ["api", "get"]]


class NotifBase(ToolBase):
    def setUp(self):
        super().setUp()
        self.stub = NotifStub()
        self.cfg(profile="agent", todos={"todoset": "66"}, message_board="99", chats=[77])
        self.stub.lines = {"77": [line(1)]}
        self.poll()  # the first run seeds the chat cursor and the notifications
        self.stub.calls.clear()

    def notify(self, *items):
        self.stub.readings = {"unreads": list(items), "reads": []}

    def kinds(self):
        return [r["kind"] for r in self.pending()]

    def log(self):
        return open(os.path.join(self.cfgdir, "sync.log")).read()

    def state(self, name="notifications.json"):
        p = os.path.join(self.cfgdir, name)
        return json.load(open(p)) if os.path.exists(p) else {}


class Reader(NotifBase):
    def test_the_first_run_seeds_without_replaying_or_marking(self):
        self.cfg(chats=[78])
        os.remove(os.path.join(self.cfgdir, "notifications.json"))
        self.stub.lines["78"] = [line(1), line(2, content="old question?")]
        self.notify(note(5, "Chat", thread=78, path="chats", section="chats"), note(6, thread=40))
        self.stub.my_boosts = [my_boost(9, 2)]
        self.poll()
        self.assertEqual((self.pending(), self.stub.marked), ([], []))
        self.assertEqual(self.state()["items"], {"5": "2099-01-01T00:00:01Z", "6": "2099-01-01T00:00:01Z"})
        self.assertEqual(self.state()["boosts"], [9])
        self.assertIn("notifications: started; 2 notification(s) and 1 boost(s) seeded, none replayed", self.log())

    def test_a_chat_line_is_read_recorded_acknowledged_marked_and_delivered_in_one_run(self):
        self.cfg(inbox={}, chats=[77, 78])
        self.stub.lines["78"] = [line(5)]
        self.poll()  # inbox delivery and chat 78 start here
        self.stub.calls.clear()
        self.stub.lines["77"].append(line(2, content="ship it?"))
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.kinds(), ["chat-question"])
        self.assertIn(("2", EYES), self.stub.posted())
        self.assertEqual(self.stub.marked, [["10"]])
        self.assertEqual([n[0] for n in self.stub.notes], ["basecamp-chat-question-2"])
        self.assertEqual([p for p in self.stub.gets() if "/chats/" in p], [f"/buckets/{PROJECT}/chats/77/lines.json"])

    def test_an_unchanged_notification_reads_nothing_again(self):
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.poll()
        self.stub.calls.clear()
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.stub.gets(), ["/my/profile.json", "/my/readings.json", "/my/boosts.json"])
        self.assertEqual(self.stub.marked, [["10"]])

    def test_a_mention_or_a_reply_to_the_agent_in_chat_is_recorded_without_a_question_mark(self):
        self.stub.lines["77"] += [line(2, content="noted"), line(3, content="a reply to your line")]
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"),
                    note(11, "Mention", thread=77, anchor=3, path="chats"))
        self.poll()
        self.assertEqual([r["line"] for r in self.pending()], [3])
        self.assertEqual(self.stub.marked, [["10", "11"]])

    def test_a_reply_the_sweep_already_passed_is_still_recorded_once(self):
        self.stub.lines["77"].append(line(3, content="a reply to your line"))
        self.poll()  # the chat reader moves its cursor past the plain line
        self.notify(note(11, "Mention", thread=77, anchor=3, path="chats"))
        self.poll()
        self.poll()
        self.assertEqual([r["line"] for r in self.pending()], [3])

    def test_a_comment_on_a_tracked_todo_reads_that_todo_only(self):
        tid = self.create()
        self.create(key="other")
        self.stub.calls.clear()
        self.stub.comments[str(tid)] = [comment(9)]
        self.notify(note(10, thread=tid, anchor=9))
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.kinds(), ["todo-comment"])
        self.assertEqual([c[2] for c in self.stub.calls if c[:2] == ["comments", "list"]], [str(tid)])

    def test_a_comment_on_a_mirrored_card_runs_that_cards_readers_only(self):
        self.sync().main([item("a"), item("b")])  # cards 501 and 502
        self.stub.calls.clear()
        self.stub.comments = {"501": [comment(9, content="<p>why?</p>")]}
        self.notify(note(10, thread=501, anchor=9, path="cards"))
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.kinds(), ["question"])
        self.assertEqual([c for c in self.stub.calls if c[:2] == ["comments", "list"]], [["comments", "list", "501"]])
        self.assertFalse([c for c in self.stub.calls if c[0] == "cards"])  # the mirror itself never runs

    def test_a_comment_on_the_agents_message_reads_that_message_whatever_its_age(self):
        self.stub.messages[20] = {"id": 20, "creator": {"id": ACTING}, "subject": "Report", "created_at": "2001-01-01T00:00:00Z",
                                  "boosts_count": 0, "comments_count": 1, "app_url": "https://x/m/20"}
        self.stub.comments["20"] = [comment(25, content="<p>dig deeper?</p>")]
        self.notify(note(10, thread=20, anchor=25, path="messages"))
        self.poll()
        self.assertEqual([(r["kind"], r["message"], r["comment"]) for r in self.pending()], [("message-comment", 20, 25)])
        self.assertIn(("25", EYES), self.stub.posted())
        self.assertNotIn("/message_boards/", " ".join(self.stub.gets()[:3]))

    def test_a_reminder_records_the_due_checkin(self):
        self.cfg(checkins={"questionnaires": ["9"], "timezone": "UTC"})
        self.stub.questions = [{"id": 5, "title": "What did you do?", "schedule": {"days": list(range(7)), "hour": 0, "minute": 0},
                                "creator": {"id": CAPTAIN}, "app_url": "https://x/q/5"}]
        r = note(10, "Reminder", path="questions", title="Time to answer")
        r.pop("subscription_url")
        r["app_url"] = "https://3.basecampapi.com/1/questions/answers/entries/5/edit"
        self.notify(r)
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.kinds(), ["checkin"])

    def test_items_in_other_projects_and_basecamps_own_are_left_alone(self):
        self.notify(note(10, thread=40, bucket=OTHER), note(11, "Mention", thread=41, anchor=42, bucket=OTHER),
                    note(12, "BoostReport", path="todos"), note(13, "Bulletin", path="todos"))
        self.poll()
        self.assertEqual((self.pending(), self.stub.marked), ([], []))
        self.assertFalse([p for p in self.stub.gets() if "/comments/" in p or "/recordings/" in p])
        self.assertEqual(set(self.state()["items"]), {"10", "11", "12", "13"})

    def test_dry_run_records_saves_and_marks_nothing(self):
        before = self.state()
        self.stub.lines["77"].append(line(2, content="ship it?"))
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.sync(dry=True).behaviors["notifications"].run()
        self.assertEqual((self.pending(), self.stub.marked, self.state()), ([], [], before))
        self.assertIn("dry chat 77: captain question 2", self.log())

    def test_refused_as_the_owner_and_logged_once(self):
        self.stub.me = CAPTAIN
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.poll()
        self.poll()
        self.assertNotIn("/my/readings.json", self.stub.gets())
        self.assertEqual(self.log().count("notifications: the acting user is the owner"), 1)

    def test_off_without_a_profile_or_when_turned_off(self):
        for cfg in ({"profile": None}, {"notifications": False}):
            self.cfg(**cfg)
            self.assertFalse(self.sync().behaviors["notifications"].on)
            self.cfg(profile="agent", drop=("notifications",))
        for bad in ([], {"mark_read": "no"}):
            self.cfg(notifications=bad)
            with self.assertRaises(ValueError):
                self.sync()


class Threads(NotifBase):
    """@mentions on any other thread the agent follows are relayed by that thread's own reader (test_relay_filter covers
    the plain comments there)."""

    def doc_comment(self, id, who=CAPTAIN, content=f"<p>{MENTION} tighten the intro</p>", boosts=0):
        return {"id": id, "creator": {"id": who, "name": "Cap"}, "content": content, "created_at": f"t{id}",
                "app_url": f"https://x/docs/4#c{id}", "boosts_count": boosts,
                "parent": {"id": 4, "type": "Document", "title": "Roadmap", "app_url": "https://x/docs/4"}}

    def test_comments_on_a_document_from_the_first_unread_are_relayed_once_and_answered_there(self):
        self.stub.comments["4"] = [self.doc_comment(7, content=f"<p>{MENTION} read before</p>"), self.doc_comment(9),
                                   self.doc_comment(10, who=ACTING, content="<p>on it</p>"),
                                   self.doc_comment(11, content=f"<p>{MENTION} and the end?</p>")]
        self.notify(note(10, thread=4, anchor=9, path="documents", title="Re: Roadmap"))
        self.poll()
        self.poll()
        self.assertEqual([(r["kind"], r["comment"], r["parent_type"], r["title"], r["question"]) for r in self.pending()],
                         [("mention", 9, "Document", "Roadmap", False), ("mention", 11, "Document", "Roadmap", True)])
        self.assertEqual(sorted(self.stub.posted()), [("11", EYES), ("9", EYES)])
        self.assertEqual(self.stub.marked, [["10"]])
        rid, body = behaviors.inbox_note(self.pending()[1], "1", str(PROJECT))
        self.assertEqual(rid, "basecamp-mention-11")
        self.assertIn("@mention of you from the captain on Document 'Roadmap'", body)
        self.assertTrue(self.sync().reply(11, "Done."))
        self.assertEqual(self.stub.made("comments create"), [["comments", "create", "4", "-"]])
        self.assertEqual(self.stub.boosts["11"], [])
        self.assertFalse(self.sync().reply(11, "again"))
        self.stub.comments["4"].append(self.doc_comment(12, content=f"<p>{MENTION} thanks</p>"))
        self.stub.readings["unreads"][0].update(unread_at="2099-01-01T00:00:09Z", readable_identifier=note(0, anchor=12)["readable_identifier"])
        self.poll()
        self.assertEqual([r["comment"] for r in self.pending()], [9, 11, 12])

    def test_a_mention_is_a_mention_record(self):
        self.stub.comments["4"] = [self.doc_comment(9, content="<p>@Agent can you check this?</p>")]
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual({k: rec[k] for k in ("kind", "bucket", "parent", "parent_type", "title", "comment", "question", "text")},
                         {"kind": "mention", "bucket": PROJECT, "parent": 4, "parent_type": "Document", "title": "Roadmap",
                          "comment": 9, "question": True, "text": "@Agent can you check this?"})
        self.assertEqual(self.stub.posted(), [("9", EYES)])
        rid, body = behaviors.inbox_note(rec, "1", str(PROJECT))
        self.assertEqual(rid, "basecamp-mention-9")
        self.assertIn("@mention of you from the captain", body)

    def test_a_comment_on_a_card_whose_task_left_the_backlog_is_still_relayed(self):
        """Regression: the mirror leaves such a card as it is and stops reading it; its comments still arrive."""
        self.sync().main([item("a")])  # card 501
        self.sync().main([])  # the task is done and gone from the live backlog: the card is left as it is
        self.assertIn("left-as-is 1 cards no longer in the backlog", self.log())
        self.stub.calls.clear()
        self.stub.comments["501"] = [comment(9, content="<p>one more thing</p>"), comment(10, content="<p>and this?</p>"),
                                     comment(11, content="<p>hello?</p>")]
        self.notify(note(20, thread=501, anchor=9, path="cards", title="Re: A task"))
        self.sync().behaviors["notifications"].run()
        self.assertEqual([(r["kind"], r["card"], r["comment"]) for r in self.pending()],
                         [("comment", 501, 9), ("question", 501, 10), ("question", 501, 11)])
        self.assertEqual(self.stub.marked, [["20"]])

    def test_a_mention_on_a_card_nothing_mirrors_is_relayed_by_the_thread_reader(self):
        self.stub.comments["600"] = [comment(9, content=f"<p>{MENTION} see this</p>")]
        self.notify(note(20, thread=600, anchor=9, path="cards", title="Re: Hand-made card"))
        self.poll()
        self.assertEqual([(r["kind"], r["parent"], r["title"]) for r in self.pending()],
                         [("mention", 600, "Hand-made card")])

    def test_an_owner_boost_on_the_agents_reply_in_a_thread_is_relayed(self):
        self.stub.comments["4"] = [self.doc_comment(9)]
        self.notify(note(10, thread=4, anchor=9, path="documents"))
        self.poll()  # the thread is known from here; its boost counts are seeded
        self.stub.comments["4"].append(self.doc_comment(15, who=ACTING, boosts=1))
        self.stub.boosts["15"] = [boost(90, content="yes")]
        self.stub.my_boosts = [my_boost(90, 15, "Comment", parent={"id": 4, "type": "Document", "title": "Roadmap"})]
        self.poll()
        self.assertEqual([(r["kind"], r.get("surface"), r.get("recording")) for r in self.pending()],
                         [("mention", None, None), ("boost", "thread-comment", 15)])

    def test_a_mention_in_a_chat_nothing_relays_is_a_chat_question_answered_in_that_chat(self):
        self.stub.lines["78"] = [line(1), line(3, content="@Agent look")]
        self.notify(note(10, "Mention", thread=78, anchor=3, path="chats"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["chat"], rec["line"], rec["mention"]), ("chat-question", 78, 3, True))
        self.assertTrue(self.sync().reply(3, "Looking."))
        self.assertEqual([c for c, _ in self.stub.chat_posts()], ["78"])

    def test_comments_by_someone_not_listened_to_are_not_relayed(self):
        self.stub.comments["4"] = [self.doc_comment(9, who=1)]
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents", who=1))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.marked, [["10"]])


class Replays(NotifBase):
    """Regression: on 2026-10-15 two @mention notifications from 2026-10-04 and -05 on a decision to-do completed on
    the 5th came back with no cursor. The to-do reader no longer reads a completed to-do, so the thread reader adopted it
    as a new thread from the older mention and replayed all three owner comments the to-do reader had relayed and the
    agent had answered, as a thread-comment and two mentions."""

    OLD = ("2026-10-04T02:29:55.081Z", "2026-10-05T02:05:46.376Z")

    def answered_and_completed(self):
        """The decision to-do: the owner's "merge", two @mentions, each relayed once and answered, then completed."""
        tid = self.create(key="ta-speech-bystander-prose")
        self.stub.comments[str(tid)] = [comment(9, content="<p>merge</p>")]
        self.notify(note(10, thread=tid, anchor=9))
        self.poll()
        self.stub.comments[str(tid)] += [comment(11, content="<p>@Agent are these ready to merge now?</p>"),
                                         comment(12, content="<p>@Agent these merged. Why isn't this closed?</p>")]
        self.notify(note(10, thread=tid, anchor=9), note(20, "Mention", thread=tid, anchor=11, at="2099-01-01T00:00:02Z"),
                    note(21, "Mention", thread=tid, anchor=12, at="2099-01-01T00:00:03Z"))
        self.poll()
        self.assertTrue(self.sync().reply(12, "Closing it now."))
        self.assertTrue(self.sync().todo_complete("ta-speech-bystander-prose"))
        self.assertEqual([(r["kind"], r["comment"]) for r in self.pending()],
                         [("todo-comment", 9), ("todo-comment", 11), ("todo-comment", 12)])
        return tid

    def forget(self, *ids):
        """The notifications' cursors lose items `ids`, as when they drop out of one read of /my/readings.json."""
        state = self.state()
        for i in ids:
            state["items"].pop(str(i))
        with open(os.path.join(self.cfgdir, "notifications.json"), "w") as f:
            json.dump(state, f)

    def test_old_mentions_back_without_a_cursor_are_seeded_not_replayed(self):
        tid = self.answered_and_completed()
        self.forget(20, 21)
        self.stub.calls.clear()
        self.notify(note(20, "Mention", thread=tid, anchor=11, at=self.OLD[0]),
                    note(21, "Mention", thread=tid, anchor=12, at=self.OLD[1]))
        self.poll()
        self.assertEqual(len(self.pending()), 3)
        self.assertNotIn(["comments", "list", str(tid)], [c[:3] for c in self.stub.calls])
        self.assertEqual(self.state("threads.json"), {})
        self.assertEqual((self.state()["items"]["20"], self.state()["items"]["21"]), self.OLD)
        self.assertIn("notifications: 2 notification(s) from before the last read seeded, not replayed: 20, 21", self.log())

    def test_a_completed_todo_adopted_as_a_thread_starts_after_what_the_todo_reader_read(self):
        tid = self.answered_and_completed()
        self.forget(20, 21)
        self.notify(note(20, "Mention", thread=tid, anchor=11, at="2099-01-01T00:00:02Z"),
                    note(21, "Mention", thread=tid, anchor=12, at="2099-01-01T00:00:03Z"))
        self.poll()  # the thread reader takes the completed to-do over: nothing the to-do reader read is new
        self.assertEqual(len(self.pending()), 3)
        self.assertNotIn("comments", self.state("threads.json")[f"{PROJECT}:{tid}"])
        self.stub.comments[str(tid)].append(comment(900, content=f"<p>{MENTION} thanks, and one more thing</p>"))
        self.notify(note(22, thread=tid, anchor=900, at="2099-01-01T00:00:04Z"))
        self.poll()  # a comment after the to-do was done is still relayed, once
        self.poll()
        self.assertEqual([(r["kind"], r["comment"]) for r in self.pending()[3:]], [("mention", 900)])

    def test_a_thread_adopted_without_a_comment_to_start_from_starts_when_the_notification_did(self):
        def doc(id, at):
            return {"id": id, "creator": {"id": CAPTAIN, "name": "Cap"}, "content": f"<p>{MENTION} note {id}</p>", "created_at": at,
                    "app_url": f"https://x/docs/4#c{id}", "parent": {"id": 4, "type": "Document", "title": "Roadmap"}}
        self.stub.comments["4"] = [doc(7, "2099-01-01T00:00:00Z"), doc(8, "2099-01-01T00:10:00Z")]
        self.notify(note(10, thread=4, path="documents", at="2099-01-01T00:10:00.100Z"))  # names the document, no comment
        self.poll()
        self.assertEqual([r["comment"] for r in self.pending()], [8])


class Boosts(NotifBase):
    def test_an_owner_boost_on_the_agents_chat_line_runs_the_chat_reader_once(self):
        self.stub.lines["77"][0]["boosts_count"] = 1
        self.stub.boosts["1"] = [boost(60, content="ok")]
        self.stub.my_boosts = [my_boost(60, 1, parent={"id": 77, "type": "Chat::Transcript"}),
                               my_boost(61, 1, parent={"id": 77, "type": "Chat::Transcript"}, who=1)]
        self.sync().behaviors["notifications"].run()
        self.sync().behaviors["notifications"].run()
        self.assertEqual([(r["surface"], r["recording"], r["boost"]) for r in self.pending()], [("chat", 1, 60)])
        self.assertEqual(self.state()["boosts"], [60, 61])
        self.assertFalse([p for p in self.stub.gets() if "/message_boards/" in p])

    def test_an_owner_boost_on_the_agents_decision_todo_runs_its_reader(self):
        tid = self.create()
        self.poll()  # seeds the to-do's boosts
        self.stub.calls.clear()
        self.stub.boosts[str(tid)] = [boost(70, content="yes")]
        self.stub.my_boosts = [my_boost(70, tid, "Todo", parent={"id": 66, "type": "Todoset"}, content="yes")]
        self.sync().behaviors["notifications"].run()
        self.assertEqual([(r["kind"], r["surface"], r["text"]) for r in self.pending()], [("boost", "todo", "yes")])
        self.assertEqual([c[2] for c in self.stub.calls if c[:2] == ["comments", "list"]], [str(tid)])

    def test_a_boost_on_something_nothing_monitors_is_unmonitored(self):
        self.stub.my_boosts = [my_boost(80, 31, "Comment", parent={"id": 4, "type": "Document", "title": "Roadmap"}, content="👍")]
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["key"], rec["title"], rec["notification"]), ("boost.created/Comment on Document", "Roadmap", 80))

    def test_boosts_unreadable_on_the_first_run_are_seeded_when_they_can_be_read(self):
        os.remove(os.path.join(self.cfgdir, "notifications.json"))
        self.stub.fail.add("/my/boosts.json")
        self.poll()
        self.assertNotIn("boosts", self.state())
        self.stub.fail.clear()
        self.stub.my_boosts = [my_boost(80, 31, "Comment", parent={"id": 4, "type": "Document"})]
        self.poll()
        self.assertEqual((self.pending(), self.state()["boosts"]), ([], [80]))

    def test_a_boost_in_another_project_is_left_alone(self):
        self.stub.my_boosts = [my_boost(81, 31, "Comment", parent={"id": 4, "type": "Document"}, bucket=OTHER)]
        self.poll()
        self.assertEqual(self.pending(), [])


class MarkRead(NotifBase):
    def test_marked_read_only_after_the_record_is_written(self):
        self.stub.lines["77"].append(line(2, content="ship it?"))
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        at_mark = []
        self.stub.on_mark = lambda: at_mark.append(self.kinds())
        self.poll()
        self.assertEqual(at_mark, [["chat-question"]])

    def test_a_failed_read_is_neither_marked_nor_advanced_and_is_retried(self):
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9)]
        self.notify(note(10, thread=tid, anchor=9))
        self.stub.fail.add(f"comments list {tid}")
        self.poll()
        self.assertEqual((self.pending(), self.stub.marked), ([], []))
        self.assertEqual(self.state()["items"]["10"], "")  # known, not advanced: retried however old it gets
        self.assertIn("notifications: retrying next run: todos", self.log())
        self.stub.fail.clear()
        self.poll()
        self.assertEqual(self.kinds(), ["todo-comment"])
        self.assertEqual(self.stub.marked, [["10"]])

    def test_a_failed_mark_is_retried_next_run(self):
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.stub.fail.add("notifications read")
        self.poll()
        self.assertEqual(self.state()["unmarked"], ["10"])
        self.stub.fail.clear()
        self.poll()
        self.assertEqual(self.stub.marked, [["10"]])
        self.assertEqual(self.state()["unmarked"], [])

    def test_mark_read_false_keeps_them_unread(self):
        self.cfg(notifications={"mark_read": False})
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.poll()
        self.assertEqual(self.stub.marked, [])
        self.assertIn("10", self.state()["items"])

    def test_someone_marking_it_read_loses_nothing(self):
        self.stub.lines["77"].append(line(2, content="ship it?"))
        r = note(10, "Chat", thread=77, path="chats", section="chats")
        r["read_at"] = "2099-01-01T00:00:05Z"
        self.stub.readings = {"unreads": [], "reads": [r]}
        self.poll()
        self.assertEqual(self.kinds(), ["chat-question"])
        self.assertEqual(self.stub.marked, [])  # already read


class Unmonitored(NotifBase):
    DOC = {"id": 4, "type": "Document", "title": "Roadmap", "app_url": "https://x/docs/4"}

    def setUp(self):
        super().setUp()
        self.cfg(inbox={})
        self.poll()  # inbox delivery starts after the existing records
        self.stub.calls.clear()

    def unmonitored(self):
        return self.state("unmonitored.json")

    def test_a_line_in_a_chat_nothing_relays_is_recorded_once_per_kind_delivered_and_marked(self):
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats", title="Chat", excerpt="tighten the intro"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual(rec, {"kind": "unmonitored", "key": "chat.line.created/Chat::Lines", "event_type": "chat.line.created",
                               "recording_type": "Chat::Lines", "notification": 10, "recording": 78, "title": "Chat",
                               "text": "tighten the intro", "creator": {"id": CAPTAIN, "name": "Cap"},
                               "author": {"id": CAPTAIN, "name": "Cap"}, "captain": True, "role": "captain",
                               "url": f"{APP}/buckets/{PROJECT}/chats/78", "at": "2099-01-01T00:00:01Z",
                               "at_local": "2099-01-01 00:00 UTC"})
        [(rid, body, *_)] = self.stub.notes
        self.assertEqual(rid, "basecamp-unmonitored-chat.line.created-Chat::Lines-10")
        self.assertIn("nothing monitors: chat.line.created on Chat::Lines 'Chat'", body)
        self.assertIn("sync.py unmonitored handle --key 'chat.line.created/Chat::Lines'", body)
        self.assertEqual(self.stub.marked, [["10"]])
        self.assertFalse([p for p in self.stub.gets() if "/recordings/" in p])  # the notification says enough

    def test_one_record_per_kind_until_forgotten(self):
        key = "todo.assignment_changed/Todo"
        self.notify(note(10, "Assignment", thread=40), note(11, "Assignment", thread=41))
        self.poll()
        self.assertEqual([r["key"] for r in self.pending()], [key])
        self.assertEqual(self.unmonitored()[key]["seen"], 2)
        self.sync().unmonitored_handle(key, "ignore them")
        self.notify(note(12, "Assignment", thread=42))
        self.poll()
        self.assertEqual(len(self.pending()), 1)  # handled: still quiet
        self.sync().unmonitored_forget(key)
        self.notify(note(13, "Assignment", thread=43))
        self.poll()
        self.assertEqual([r["notification"] for r in self.pending()], [10, 13])

    def test_keys_recorded_by_the_old_listener_still_apply(self):
        json.dump({"chat.line.created/Chat::Lines": {"event": 1, "recording": 2, "seen": 1, "handled": {"decision": "ignore"}}},
                  open(os.path.join(self.cfgdir, "unmonitored.json"), "w"))
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats"))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.unmonitored()["chat.line.created/Chat::Lines"]["seen"], 2)

    def test_a_line_in_a_chat_nothing_relays_and_an_assignment_with_requests_off(self):
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats"), note(11, "Assignment", thread=40, title="Assigned you: Paint"))
        self.poll()
        self.assertEqual([r["key"] for r in self.pending()], ["chat.line.created/Chat::Lines", "todo.assignment_changed/Todo"])

    def test_a_kind_of_notification_basecamp_adds_later_refetches_the_recording_for_its_type(self):
        self.stub.recordings[4] = {"id": 4, "type": "Vault", "title": "Files", "app_url": "https://x/v/4"}
        self.notify(note(10, "Applause", thread=4, path="documents"))
        self.poll()
        self.assertEqual([(r["key"], r["title"]) for r in self.pending()], [("Applause/Vault", "Files")])

    def test_handled_input_records_nothing_unmonitored(self):
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9)]
        self.stub.lines["77"].append(line(2, content="ship it?"))
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"), note(11, thread=tid, anchor=9))
        self.poll()
        self.assertEqual(self.kinds(), ["chat-question", "todo-comment"])
        self.assertEqual(self.unmonitored(), {})

    def test_off_records_nothing(self):
        self.cfg(notifications={"unmonitored": False})
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats"))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.marked, [["10"]])

    def test_cli_list_handle_forget(self):
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats"))
        self.poll()
        cfg = ["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")]
        key = "chat.line.created/Chat::Lines"
        self.assertEqual(sync.cli(["unmonitored", "handle", *cfg, "--key", key, "--decision", "ignore"], runner=self.stub), 0)
        self.assertEqual(sync.cli(["unmonitored", "handle", *cfg, "--key", "nope/Todo", "--decision", "x"], runner=self.stub), 1)
        self.assertIn("no unmonitored key 'nope/Todo'", self.log())
        self.assertEqual(sync.cli(["unmonitored", "list", *cfg], runner=self.stub), 0)
        self.assertEqual(sync.cli(["unmonitored", "forget", *cfg, "--key", key], runner=self.stub), 0)
        self.assertEqual(self.unmonitored(), {})
        self.assertEqual(sync.cli(["unmonitored", "bogus"], runner=self.stub), 2)


class Cadence(NotifBase):
    def ran(self):
        return self.state("timer.json")["ran"]

    def age(self, **seconds):
        """Make every step look as if it last ran this long ago."""
        timer = self.state("timer.json")
        for name, s in seconds.items():
            timer["ran"][name.replace("_", "-")] = (datetime.now(timezone.utc) - timedelta(seconds=s)).isoformat(timespec="seconds")
        json.dump(timer, open(os.path.join(self.cfgdir, "timer.json"), "w"))

    def timer_run(self):
        self.stub.calls.clear()
        self.sync().main([], due=True)
        return self.stub.gets()

    def test_notifications_every_run_the_card_mirror_every_5_minutes_and_the_readers_hourly(self):
        self.timer_run()
        gets = self.timer_run()  # 30 seconds later: only notifications
        self.assertEqual([g for g in gets if g != "/my/profile.json"], ["/my/readings.json", "/my/boosts.json"])
        self.assertEqual([c for c in self.stub.calls if c[:2] == ["comments", "list"]], [])
        self.age(card_mirror=300, decision_todos=300, chat_inbox=300, reports=300)
        gets = self.timer_run()
        self.assertNotIn(f"/buckets/{PROJECT}/chats/77/lines.json", gets)  # the sweep is hourly
        self.assertIn("counts {}", self.log())  # the card mirror ran
        self.age(chat_inbox=3600)
        self.assertIn(f"/buckets/{PROJECT}/chats/77/lines.json", self.timer_run())

    def test_without_notifications_the_readers_run_every_5_minutes(self):
        self.cfg(notifications=False)
        s = self.sync()
        self.assertEqual({n: s.behaviors[n].runs for n in ("notifications", "chat-inbox", "card-mirror", "inbox-delivery")},
                         {"notifications": "each run", "chat-inbox": "5 min", "card-mirror": "5 min", "inbox-delivery": "each run"})
        self.cfg(drop=("notifications",))
        s = self.sync()
        self.assertEqual({n: s.behaviors[n].runs for n in ("chat-inbox", "decision-todos", "reports", "pings", "assigned-todos")},
                         {"chat-inbox": "hourly", "decision-todos": "hourly", "reports": "hourly", "pings": "hourly",
                          "assigned-todos": "5 min"})

    def test_the_cards_off_heartbeat_comes_every_5_minutes(self):
        self.cfg(cards=False)
        self.timer_run()
        n = self.log().count("cards off")
        self.timer_run()
        self.assertEqual(self.log().count("cards off"), n)
        self.age(card_mirror=290)
        self.timer_run()
        self.assertEqual(self.log().count("cards off"), n + 1)

    def test_all_runs_every_step(self):
        self.cfg(cards=False)
        self.timer_run()
        self.stub.calls.clear()
        cfg = os.path.join(self.cfgdir, "config.json")
        self.assertEqual(sync.cli(["--home", self.home, "--config", cfg, "--all"], runner=self.stub), 0)
        self.assertIn(f"/buckets/{PROJECT}/chats/77/lines.json", self.stub.gets())

    def test_behaviors_listing_shows_the_cadence(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli(["behaviors", "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")],
                            runner=self.stub)
        self.assertEqual(code, 0)
        runs = {ln.split()[0]: ln.split()[2] for ln in out.getvalue().splitlines()}
        self.assertEqual({n: runs[n] for n in ("notifications", "card-mirror", "chat-inbox", "chat-asks")},
                         {"notifications": "each", "card-mirror": "5", "chat-inbox": "hourly", "chat-asks": "agent"})


class Upgrade(NotifBase):
    """A config written for the event listener keeps working: "listen" is ignored with one logged note."""

    def test_a_listen_config_runs_with_one_note_and_no_listener(self):
        self.cfg(listen={"interval": 45})
        s = self.sync()
        self.assertNotIn("owner-events", s.behaviors)
        self.assertTrue(s.behaviors["notifications"].on)
        self.poll()
        self.poll()
        self.assertEqual(self.log().count('"listen" in the config is ignored'), 1)
        self.assertIn("re-run init (or basecamp-mate setup) to remove the listener service", self.log())

    def test_its_unmonitored_false_still_applies(self):
        self.cfg(listen={"unmonitored": False})
        self.assertFalse(self.sync().behaviors["notifications"].unmonitored)
        self.poll()
        self.assertIn('its "unmonitored": false still applies', self.log())
        self.cfg(notifications={"unmonitored": True})
        self.assertTrue(self.sync().behaviors["notifications"].unmonitored)

    def test_any_listen_value_is_accepted(self):
        for value in (True, [], {"interval": 2}, "yes"):
            self.cfg(listen=value)
            self.sync()

    def test_a_leftover_listener_service_logs_and_exits_cleanly(self):
        cfg = os.path.join(self.cfgdir, "config.json")
        self.assertEqual(sync.cli(["listen", "--home", self.home, "--config", cfg, "--once"], runner=self.stub), 0)
        self.assertIn("listen: the event listener was removed", self.log())
        self.assertEqual(self.stub.gets(), [])

    def test_commands_hold_the_shared_lock(self):
        held = []
        real = sync.Sync.locked

        def spy(s, name):
            held.append(name)
            return real(s, name)
        sync.Sync.locked = spy
        try:
            f = os.path.join(self.tmp, "b.md")
            open(f, "w").write("x")
            sync.cli(["post-message", "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"),
                      "--subject", "S", "--body-file", f], runner=self.stub)
        finally:
            sync.Sync.locked = real
        self.assertEqual(held, ["sync"])


if __name__ == "__main__":
    unittest.main()
