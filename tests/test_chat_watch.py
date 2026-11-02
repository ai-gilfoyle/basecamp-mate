"""Tests for chat watches: `sync.py watch --chat <URL>` relays the listened-to people's lines in one chat of any project
of the account from a given line on, until `sync.py unwatch` or its "until" day passes; `reply` answers there.

Notifications, chat lines and boosts are stubbed; nothing touches the network.
"""
import io, json, os, sys, threading, time, unittest
from unittest import mock
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, line  # noqa: E402
from test_tools import boost  # noqa: E402
from test_notifications import PROJECT, my_boost, note  # noqa: E402
from test_assigned_todos import AssignBase, STRANGER  # noqa: E402
import runs  # noqa: E402
import sync  # noqa: E402
import tools  # noqa: E402

ACCOUNT = 1111111
OTHER = 88888888  # another project of the account
EYES = "\U0001F440"
MENTION = f'<bc-attachment sgid="sgid-{ACTING}" content-type="application/vnd.basecamp.mention"></bc-attachment>'
WHERE = {"id": OTHER, "name": "Rails Migration", "type": "Project"}
CHAT = f"https://3.basecamp.com/{ACCOUNT}/buckets/{OTHER}/chats/78"


def there(id, who=CAPTAIN, content="is Tuesday fine?"):
    """A line in chat 78 of project OTHER, shaped like Basecamp's (which names the bucket on every recording)."""
    return dict(line(id, who=who, content=content), created_at="2099-01-01T00:00:01Z", bucket=WHERE,
                app_url=f"https://3.basecamp.com/{ACCOUNT}/buckets/{OTHER}/chats/78@{id}")


class Base(AssignBase):
    def setUp(self):
        super().setUp()
        self.cfg(drop=("assigned_todos",), inbox={})
        self.stub.lines["78"] = [there(1, who=ACTING, content="Omar, when can you look at the importer?")]
        self.poll()
        self.stub.calls.clear()
        self.stub.on.clear()

    def watch(self, *args, chat=CHAT, what="watch"):
        out = io.StringIO()
        with redirect_stdout(out):
            return sync.cli([what, "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"),
                             "--chat", chat, *args], runner=self.stub)

    def chats(self):
        return self.state("chats.json")

    def reads_there(self):
        return [p for p in self.stub.paths() if p.startswith(f"/buckets/{OTHER}/chats/")]

    def notified(self, *items):
        self.notify(*items)
        self.sync().behaviors["notifications"].run()


class Watch(Base):
    def test_a_line_in_a_watched_chat_there_is_a_chat_question_carrying_its_project(self):
        self.assertEqual(self.watch(), 0)
        self.stub.calls.clear()
        self.stub.lines["78"].append(there(2))
        self.notified(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER))
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["chat"], rec["line"], rec["text"], rec["project"]),
                         ("chat-question", 78, 2, "is Tuesday fine?", {"id": OTHER, "name": "Rails Migration"}))
        self.assertEqual(self.reads_there(), [f"/buckets/{OTHER}/chats/78/lines.json"])
        self.assertIn(f"/buckets/{OTHER}/recordings/2/boosts.json", self.stub.paths("post"))  # the 👀
        self.assertEqual(self.stub.marked, [["10"]])
        [(rid, body, _, _)] = self.stub.notes
        self.assertEqual(rid, "basecamp-chat-question-2")
        self.assertIn(f"chat line from the captain in project 'Rails Migration' ({OTHER})", body)

    def test_the_same_line_in_an_unwatched_chat_stays_ignored(self):
        self.stub.lines["78"].append(there(2))
        self.notified(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER))
        self.poll()
        self.assertEqual((self.pending(), self.stub.marked, self.reads_there()), ([], [], []))

    def test_without_mentions_anywhere_a_watch_still_reads_there(self):
        self.assertFalse(self.sync().behaviors["notifications"].anywhere)
        self.watch()
        self.stub.lines["78"].append(there(2))
        self.poll()  # the sweep, without a notification
        self.assertEqual([r["line"] for r in self.pending()], [2])

    def test_the_first_read_starts_after_the_line_given_so_the_question_and_older_lines_are_never_relayed(self):
        self.stub.lines["78"] = [there(1, content="an old question?"), there(2, who=ACTING, content="when?"),
                                 there(3, content="Tuesday?")]
        self.watch("--after-line", "2")
        self.assertEqual(self.chats()["78"]["cursor"], 2)
        self.poll()
        self.assertEqual([r["line"] for r in self.pending()], [3])

    def test_the_cursor_starts_at_the_line_the_url_names_else_at_the_newest_line(self):
        self.stub.lines["78"] += [there(2, who=ACTING, content="when?"), there(3, content="later?")]
        self.watch(chat=CHAT + "@2")
        self.assertEqual(self.chats()["78"]["cursor"], 2)
        self.watch()
        self.assertEqual(self.chats()["78"]["cursor"], 3)
        self.assertEqual((self.chats()["78"]["bucket"], self.chats()["78"]["project_name"]), (OTHER, "Rails Migration"))

    def test_a_watch_relays_every_listened_to_line_not_only_questions(self):
        self.watch()
        self.stub.lines["78"] += [there(2, content="Tuesday"), there(3, content="Tuesday?"),
                                  there(4, content=MENTION + " Tuesday"), there(5, content="a reply to your line")]
        self.notified(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER),
                      note(11, "Mention", thread=78, anchor=5, path="chats", bucket=OTHER))
        self.assertEqual([r["line"] for r in self.pending()], [2, 3, 4, 5])
        self.assertNotIn("every_line", self.chats()["78"]["watch"])

    def test_a_delayed_mention_of_a_line_at_or_before_the_start_is_never_relayed(self):
        self.cfg(mentions={"scope": "account"})
        here = f"https://3.basecamp.com/{ACCOUNT}/buckets/{PROJECT}/chats/79"
        for chat, bucket, url in (("78", OTHER, CHAT), ("79", PROJECT, here)):
            self.stub.lines[chat] = [there(1, who=ACTING, content="when?"), there(2, content="an older answer"),
                                     there(3, who=ACTING, content="and now?")]
            self.watch("--after-line", "3", chat=url)
            self.notified(note(int(chat), "Mention", thread=int(chat), anchor=2, path="chats", bucket=bucket))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual((self.chats()["78"]["watch"]["after"], self.chats()["79"]["watch"]["after"]), (3, 3))

    def test_a_reply_that_arrives_as_a_mention_is_recorded_once(self):
        self.cfg(mentions={"scope": "account"})
        self.watch()
        self.stub.lines["78"].append(there(2, content="a reply to your line"))
        self.notified(note(11, "Mention", thread=78, anchor=2, path="chats", bucket=OTHER))
        self.poll()
        self.assertEqual([r["line"] for r in self.pending()], [2])
        self.assertNotIn("mention", self.pending()[0])  # the chat reader's record, not the mention reader's

    def test_an_owner_boost_on_the_agents_line_there_is_relayed(self):
        self.watch()
        self.stub.lines["78"][0]["boosts_count"] = 1
        self.stub.boosts["1"] = [boost(60, content="ok")]
        self.stub.my_boosts = [my_boost(60, 1, parent={"id": 78, "type": "Chat::Transcript"}, bucket=OTHER)]
        self.sync().behaviors["notifications"].run()
        self.assertEqual([(r["kind"], r["recording"]) for r in self.pending()], [("boost", 1)])
        self.assertIn(f"/buckets/{OTHER}/recordings/1/boosts.json", self.stub.paths())

    def test_watching_again_takes_the_boosts_there_now_as_its_baseline(self):
        self.watch()
        self.watch(what="unwatch")
        self.stub.lines["78"][0]["boosts_count"] = 1
        self.stub.boosts["1"] = [boost(60, content="ok")]
        self.watch()
        self.stub.my_boosts = [my_boost(60, 1, parent={"id": 78, "type": "Chat::Transcript"}, bucket=OTHER)]
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.chats()["78"]["boost_counts"], {"1": 1})

    def test_a_new_boost_after_the_watch_starts_relays_only_itself_not_the_boosts_before(self):
        self.stub.lines["78"][0]["boosts_count"] = 1
        self.stub.boosts["1"] = [boost(60, content="earlier")]
        self.watch()
        self.assertEqual(self.chats()["78"]["boost_seen"], [60])
        self.stub.lines["78"][0]["boosts_count"] = 2
        self.stub.boosts["1"].append(boost(61, content="ok"))
        self.stub.my_boosts = [my_boost(61, 1, parent={"id": 78, "type": "Chat::Transcript"}, bucket=OTHER)]
        self.sync().behaviors["notifications"].run()
        self.assertEqual([(r["kind"], r["boost"]) for r in self.pending()], [("boost", 61)])

    def test_reply_answers_in_the_chat_there(self):
        self.watch()
        self.stub.lines["78"].append(there(2))
        self.poll()
        self.stub.on.clear()
        self.assertTrue(self.sync().reply(2, "Tuesday then."))
        self.assertIn(f"/buckets/{OTHER}/chats/78/lines.json", self.stub.paths("post"))
        self.assertEqual(self.stub.projects("api post")[-1], str(OTHER))
        self.assertTrue(any(p.startswith(f"/buckets/{OTHER}/boosts/") for p in self.stub.paths("delete")))


class Ending(Base):
    def test_unwatch_stops_reads_and_keeps_the_lines_to_reply_to(self):
        self.watch()
        self.stub.lines["78"].append(there(2))
        self.poll()
        self.assertEqual(self.watch(what="unwatch"), 0)
        self.stub.calls.clear()
        self.stub.lines["78"].append(there(3))
        self.notified(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER))
        self.poll()
        self.assertEqual(([r["line"] for r in self.pending()], self.reads_there()), ([2], []))
        self.assertNotIn("watch", self.chats()["78"])
        self.assertTrue(self.sync().reply(2, "Thanks."))
        self.assertFalse(self.sync().behaviors["chat-watch"].on)

    def test_a_watch_past_its_until_day_reads_nothing_and_ends(self):
        self.watch("--until", "2099-01-01")
        self.assertEqual(self.chats()["78"]["watch"]["until"], "2099-01-01")
        state = self.chats()
        state["78"]["watch"]["until"] = "2000-01-01"
        json.dump(state, open(os.path.join(self.cfgdir, "chats.json"), "w"))
        self.stub.calls.clear()
        self.stub.lines["78"].append(there(2))
        self.notified(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER))
        self.poll()
        self.assertEqual((self.pending(), self.reads_there()), ([], []))
        self.assertNotIn("watch", self.chats()["78"])
        self.assertIn("watch: chat 78 no longer watched: 2000-01-01 has passed", self.log())

    def test_a_malformed_until_is_refused(self):
        for bad in ("2099-13-01", "2099-1-5", "next week", "2099-02-30"):
            self.assertEqual(self.watch("--until", bad), 1)
            self.assertIn(f"--until {bad!r} is not a date (YYYY-MM-DD)", self.log())
        self.assertNotIn("78", self.chats())

    def test_a_bad_stored_until_never_stops_a_run_and_ends_that_watch_once(self):
        self.watch()
        state = self.chats()
        state["78"]["watch"]["until"] = "soon"
        json.dump(state, open(os.path.join(self.cfgdir, "chats.json"), "w"))
        self.assertEqual(self.sync().watches(), {})
        self.stub.lines["78"].append(there(2))
        for _ in range(2):
            with redirect_stdout(io.StringIO()):
                code = sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")], runner=self.stub)
            self.assertEqual(code, 0)
        self.assertEqual(self.log().count("watch: chat 78 no longer watched: its until 'soon' is not a date"), 1)
        self.assertNotIn("FAILED", self.log())
        self.assertNotIn("watch", self.chats()["78"])
        self.assertEqual(self.pending(), [])

    def test_refused_past_until_another_account_a_configured_chat_and_a_non_chat_url(self):
        for args, chat, why in ((["--until", "2000-01-01"], CHAT, "has already passed"),
                                ([], f"https://3.basecamp.com/999/buckets/{OTHER}/chats/78", "not this home's account"),
                                ([], f"https://3.basecamp.com/{ACCOUNT}/buckets/{PROJECT}/chats/77", "reads it already"),
                                ([], f"https://3.basecamp.com/{ACCOUNT}/buckets/{OTHER}/todos/5", "not a Basecamp chat URL")):
            self.assertEqual(self.watch(*args, chat=chat), 1)
            self.assertIn(why, self.log())
        self.assertNotIn("78", self.chats())

    def test_a_ping_is_refused_since_the_ping_reader_relays_it(self):
        CIRCLE = 99999990
        self.stub.lines["91"] = [dict(line(1), bucket={"id": CIRCLE, "name": "Owner + Agent", "type": "Circle"})]
        json.dump({"pings": {"92": {"bucket": CIRCLE + 1, "chat": "92"}}}, open(os.path.join(self.cfgdir, "pings.json"), "w"))
        for chat in (f"https://3.basecamp.com/{ACCOUNT}/circles/{CIRCLE}@1",
                     f"https://3.basecamp.com/{ACCOUNT}/buckets/{CIRCLE}/chats/91",
                     f"https://3.basecamp.com/{ACCOUNT}/buckets/{CIRCLE + 1}/chats/92"):
            self.assertEqual(self.watch(chat=chat), 1)
        self.assertEqual(self.log().count("is a Ping: the Ping reader relays it"), 3)
        self.assertFalse({"91", "92"} & set(self.chats()))

    def test_dry_run_reads_and_saves_nothing(self):
        self.watch("--dry-run")
        self.assertNotIn("78", self.chats())
        self.assertIn("dry watch: would watch chat 78 in project Rails Migration (88888888) from line 1", self.log())


class Access(Base):
    def test_a_blocked_senders_line_gets_the_automatic_reply_only_when_it_addresses_the_agent(self):
        self.cfg(blocked={})
        self.poll()  # starts the clock
        self.watch()
        self.stub.lines["78"].append(there(2, who=STRANGER, content="Tuesday"))
        self.poll()
        self.assertEqual((self.pending(), self.stub.chat_posts()), ([], []))
        self.stub.lines["78"].append(there(3, who=STRANGER, content=MENTION + " Tuesday"))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.chat_posts(), [("78", tools.BLOCKED_REPLY)])
        self.assertEqual(self.stub.projects("api post")[-1], str(OTHER))


class Claim(Base):
    def other_home(self, profile=None, last_run=None):
        """Record a live claim of another home on this login (or `profile`) that reads project OTHER as its own."""
        reg = runs.Registry()
        os.makedirs(reg.dir, exist_ok=True)
        other = os.path.join(self.tmp, "other.json")
        cfg = dict(json.load(open(os.path.join(self.cfgdir, "config.json"))), project=OTHER)
        if profile is not None:
            cfg["profile"] = profile
        json.dump(cfg, open(other, "w"))
        with open(reg.file(other), "w") as f:
            json.dump({"config": os.path.realpath(other), "home": "/other-home", "profile": cfg.get("profile") or "",
                       "person": None, "watches": runs.watches(cfg), "pid": None,
                       "last_run": time.time() if last_run is None else last_run}, f)
        return reg.file(other)

    def timer(self):
        """One timer run, with the watch's hourly sweep due."""
        state = self.state("timer.json")
        state.get("ran", {}).pop("chat-watch", None)
        json.dump(state, open(os.path.join(self.cfgdir, "timer.json"), "w"))
        with redirect_stdout(io.StringIO()):
            return sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")], runner=self.stub)

    def test_a_watched_chat_in_a_project_another_home_reads_with_this_login_is_skipped_never_the_run(self):
        self.watch()
        claim = self.other_home()  # it started after the watch: the next run sees it
        self.stub.lines["78"].append(there(2))
        self.stub.calls.clear()
        self.assertEqual(self.timer(), 0)
        self.assertEqual(self.timer(), 0)
        self.assertEqual((self.pending(), self.reads_there()), ([], []))
        self.assertEqual(self.log().count("watch: skipping watched chat 78 in project 88888888: another home's live run "
                                          "reads that project with this login (/other-home); an @mention of the agent there "
                                          "reaches that home, but a plain answer reaches no agent until it stops"), 1)
        self.assertNotIn("FAILED", self.log())
        self.assertIn("watch", self.chats()["78"])  # skipped, not ended
        [mine] = [c for c in runs.Registry().claims() if c["home"] == self.home]
        self.assertEqual(mine["watches"], [f"project {ACCOUNT}/{PROJECT}"])  # a watch never claims, so never refuses it
        os.remove(claim)
        self.assertEqual(self.timer(), 0)
        self.assertEqual([r["line"] for r in self.pending()], [2])
        self.assertIn("watch: reading the watched chats in project 88888888 again: no other home's live run reads it now",
                      self.log())

    def test_a_notification_for_a_skipped_chat_is_left_to_the_other_home(self):
        self.watch()
        self.other_home()
        self.stub.lines["78"].append(there(2))
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER))
        self.assertEqual(self.timer(), 0)
        self.assertEqual((self.pending(), self.stub.marked), ([], []))

    def test_a_delayed_mention_in_a_skipped_chat_is_left_to_the_other_home_with_mentions_anywhere(self):
        self.stub.lines["78"] = [there(1, who=ACTING, content="when?"), there(10, content=MENTION + " an older answer"),
                                 there(20, who=ACTING, content="and now?")]
        self.watch("--after-line", "20")
        self.other_home()
        self.cfg(mentions={"scope": "account"})
        self.stub.calls.clear()
        self.notify(note(10, "Mention", thread=78, anchor=10, path="chats", bucket=OTHER))
        self.assertEqual(self.timer(), 0)
        self.assertEqual((self.pending(), self.stub.marked, self.reads_there()), ([], [], []))
        self.assertNotIn("lines", self.chats()["78"])
        self.assertNotIn(f"/buckets/{OTHER}/recordings/10/boosts.json", self.stub.paths("post"))

    def test_read_mention_never_records_a_watched_chats_line_at_or_before_its_start(self):
        self.stub.lines["78"] = [there(1, who=ACTING, content="when?"), there(10, content=MENTION + " an older answer"),
                                 there(20, who=ACTING, content="and now?")]
        self.watch("--after-line", "20")
        self.assertFalse(self.sync().read_mention(str(OTHER), 78, 10))
        self.assertEqual((self.pending(), self.chats()["78"].get("lines")), ([], None))

    def test_watch_warns_up_front_when_another_home_on_this_login_reads_that_project(self):
        self.other_home()
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli(["watch", "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"),
                             "--chat", CHAT], runner=self.stub)
        self.assertEqual(code, 0)
        said = (f"watch: warning: another home's live run reads project {OTHER} with this login (/other-home), so chat 78 "
                "is skipped while it runs: an @mention of the agent there reaches that home, but a plain answer reaches no agent")
        self.assertIn(said, out.getvalue())
        self.assertIn(said, self.log())
        self.assertIn("watch", self.chats()["78"])  # still watched: read again once that home stops

    def test_no_warning_for_another_login_or_a_stale_claim(self):
        for kw in ({"profile": "someone-else"}, {"last_run": time.time() - 2 * runs.STALE}):
            claim = self.other_home(**kw)
            self.watch()
            os.remove(claim)
        self.assertNotIn("watch: warning", self.log())

    def test_another_login_or_a_stale_claim_does_not_skip_it(self):
        self.watch()
        for kw in ({"profile": "someone-else"}, {"last_run": time.time() - 2 * runs.STALE}):
            claim = self.other_home(**kw)
            self.stub.lines["78"].append(there(len(self.stub.lines["78"]) + 1))
            self.assertEqual(self.timer(), 0)
            os.remove(claim)
        self.assertEqual([r["line"] for r in self.pending()], [2, 3])
        self.assertNotIn("skipping watched chat", self.log())

    def test_a_watch_started_while_the_timer_claims_waits_so_the_run_reads_only_what_it_claimed(self):
        started = []
        claim = runs.Registry.claim

        def racing(reg, *a, **kw):
            t = threading.Thread(target=lambda: started.append(self.watch()))
            t.start()
            t.join(0.5)
            self.assertTrue(t.is_alive())
            self.threads.append(t)
            return claim(reg, *a, **kw)

        self.threads = []
        self.stub.lines["78"].append(there(2))
        with mock.patch.object(runs.Registry, "claim", racing), redirect_stdout(io.StringIO()):
            code = sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")], runner=self.stub)
        self.threads[0].join(5)
        self.assertEqual((code, started), (0, [0]))
        [claimed] = runs.Registry().claims()
        self.assertEqual(claimed["watches"], [f"project {ACCOUNT}/{PROJECT}"])
        self.assertEqual(self.reads_there(), [f"/buckets/{OTHER}/chats/78/lines.json"])  # the watch command's own read


class Behavior(Base):
    def test_off_until_a_watch_then_on_and_swept_hourly_behind_notifications(self):
        self.assertFalse(self.sync().behaviors["chat-watch"].on)
        self.watch()
        s = self.sync()
        self.assertEqual((s.behaviors["chat-watch"].on, s.behaviors["chat-watch"].runs), (True, "hourly"))
        self.cfg(notifications=False)
        self.assertEqual(self.sync().behaviors["chat-watch"].runs, "5 min")


if __name__ == "__main__":
    unittest.main()
