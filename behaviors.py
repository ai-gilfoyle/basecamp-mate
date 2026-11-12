"""The behaviors layer: opt-in, per-home workflows composed from tools.py.

A behavior is on when its config keys are set, and off (making no calls at all)
otherwise. It holds the policy: which readers run on the timer and how often, what
the card mirror shows, when a release is announced. The timer runs every 30 seconds;
each behavior's step runs when its `every` has passed: notifications and inbox
delivery every run, the card mirror, release announcements and check-ins every 5
minutes (and the to-do request sweep), and the security-advisory reader, the timesheet and
the readers notifications take over hourly, as the repair sweep. Some
behaviors have no timer step at all and are carried out by the agent with the tools
(chat asks); the agent's side of every behavior is in prompts/base.md.

Behaviors never call a CLI themselves; every Basecamp, GitHub and backlog read or
write goes through a Tools method.
"""
import hashlib, html, json, math, os, re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tools import AUTHORITY, THREADS, history, now_iso, parse_reading, recording_type

COLUMNS = ("Triage", "Not now", "Figuring it out", "In progress", "Ready for QA", "Done")
SLOW, SWEEP, DAY = 300, 3600, 86400  # seconds: the card mirror's, releases' and check-ins' cadence; the hourly repair sweep's; the backup's


class Behavior:
    name = ""  # as the README and `sync.py behaviors` name it
    keys = ()  # the config keys that turn it on or configure it
    timer = True  # False: the agent carries it out with the tools; nothing runs on the timer
    every = 0  # seconds between its timer steps; 0: every timer run (every 30 seconds)

    def __init__(self, t, cfg):
        self.t, self.on = t, False

    @property
    def runs(self):
        """Where and how often it runs, as `sync.py behaviors` prints it."""
        if not self.timer:
            return "agent"
        return {0: "each run", SLOW: "5 min", SWEEP: "hourly", DAY: "daily"}.get(self.every, f"{self.every}s")

    def run(self, items=None):
        pass


class CardMirror(Behavior):
    """Mirror the backlog onto card tables; relay the listened-to people's card comments and the captain's (or an
    operator's) 👍 approvals."""
    name, keys, every = "card-mirror", ("tables", "repos", "cards", "assign_captain"), SLOW

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        # On when the config has card tables, unless "cards" is false.
        self.on = cfg.get("cards", bool(cfg.get("tables")))
        if self.on and not cfg.get("tables"):
            raise ValueError('config "cards" is on but there are no "tables"')
        self.tables = cfg.get("tables", {}) if self.on else {}
        self.repo_map = cfg.get("repos", {}) if self.on else {}
        # "assign_captain": false keeps a captain-held card in Figuring it out without assigning the owner (and
        # unassigns the owner from cards it assigned): decisions then reach them as to-dos from the main home instead.
        self.assign = cfg.get("assign_captain", True)
        if not isinstance(self.assign, bool):
            raise ValueError('config "assign_captain" must be true or false')
        for board, tb in self.tables.items():
            missing = [c for c in ("table", *COLUMNS) if c not in tb]
            if missing:
                raise ValueError(f"config table {board} lacks {missing}")
        for repo, board in self.repo_map.items():
            if board not in self.tables:
                raise ValueError(f"config repo {repo} names unknown board {board}")

    def column_for(self, it, notnow):
        if it["section"] != "Done" and it["id"] in notnow and not (it["hold"] and it["hold_kind"] == "captain"):
            return "Not now", False
        parked = it["hold_kind"] == "parked" or (it["hold"] or "").startswith("parked")
        if it["section"] == "Done":
            return "Done", False
        if it["hold"] and it["hold_kind"] == "captain":
            return ("Not now", False) if it["until"] else ("Figuring it out", True)
        if parked or it["until"]:
            return "Not now", False
        if it["section"] == "In flight":
            return ("Ready for QA", False) if self.t.meta_pr(it["id"]) else ("In progress", False)
        return "Triage", False

    def body_for(self, it, col, waiting, notnow, decisions=None, boards=()):
        decisions = decisions or {}
        parts = [f"<div><strong>Task</strong>: {html.escape(it['id'])} &middot; <strong>Status</strong>: {col}</div>"]
        if boards:
            links = ", ".join(f'<a href="{html.escape(u)}">{html.escape(u)}</a>' for u in boards)
            parts.append(f"<div><strong>Plan board</strong>: {links}</div>")
        if waiting and it["id"] in decisions:
            parts.append(render_decision(decisions[it["id"]]))
        elif waiting:
            parts.append(f"<div><strong>Waiting on you</strong>: {html.escape(it['hold'])}</div>")
        elif it["id"] in notnow:
            parts.append(f"<div><strong>Not now</strong>: {html.escape(notnow[it['id']])}</div>")
        elif it["hold"]:
            parts.append(f"<div><strong>On hold</strong>: {html.escape(it['hold'])}</div>")
        if it["blocked_by"]:
            parts.append(f"<div><strong>After</strong>: {html.escape(', '.join(it['blocked_by']))}</div>")
        pr = self.t.meta_pr(it["id"])
        prs = list(dict.fromkeys(it["links"] + ([pr] if pr else [])))
        if prs:
            parts.append("<div><strong>PRs</strong>:</div><ul>" + "".join(f'<li><a href="{html.escape(u)}">{html.escape(u)}</a></li>' for u in prs) + "</ul>")
        parts.append("<div><em>Kept in sync from the backlog; edits to this text are overwritten. Comments are read and relayed.</em></div>")
        return "".join(parts)

    def run(self, items=None):
        t = self.t
        cards = t.load("map.json", {})
        extra = t.load("extra-repos.json", {})
        notnow = t.load("not-now.json", {})
        figuring = t.load("figuring.json", {})
        skip = set(t.load("skip.json", []))
        decisions = t.load("decisions.json", {})
        board_owners = t.load("boards.json", {})
        live = t.lavish_boards()
        counts, unplaced, wanted = {}, [], set()
        plan = {"create": 0, "update": 0, "move": 0, "assign": 0, "unassign": 0}
        for it in (t.read_backlog() if items is None else items):
            if it["id"] in skip:
                continue
            repos = []
            if it["repo"] in self.repo_map:
                repos.append(self.repo_map[it["repo"]])
            repos += [r for r in extra.get(it["id"], []) if r not in repos]
            if not repos:
                unplaced.append(f"{it['id']} (repo {it['repo'] or 'none'})")
                continue
            col, waiting = self.column_for(it, notnow)
            assign = waiting and self.assign
            if col == "Triage" and it["id"] in figuring:
                col = "Figuring it out"
                it = dict(it, hold=it["hold"] or figuring[it["id"]])
            if live is not None:
                owners = [it["id"]] + [o for o in board_owners.get(it["id"], []) if o != it["id"]]
                task_boards = list(dict.fromkeys(u for o in owners for u in live.get(o, [])))
            for repo in repos:
                key = f"{it['id']}|{repo}"
                wanted.add(key)
                tb = self.tables[repo]
                title = it["title"][:240]
                rec = cards.get(key)
                boards = task_boards if live is not None else (rec or {}).get("boards", [])
                body = self.body_for(it, col, waiting, notnow, decisions, boards)
                digest = hashlib.sha256((title + body).encode()).hexdigest()
                if t.dry:
                    if rec is None:
                        plan["create"] += 1
                        t.log(f"dry {key}: create -> {col}{' (assign)' if assign else ''}")
                    else:
                        acts = []
                        if rec.get("digest") != digest:
                            acts.append("update")
                        if rec.get("column") != col:
                            acts.append(f"move {rec.get('column')} -> {col}")
                        if bool(rec.get("assigned")) != assign:
                            acts.append("assign" if assign else "unassign")
                        for a in acts:
                            plan[a.split()[0]] += 1
                        t.log(f"dry {key}: {', '.join(acts) or 'unchanged'} [{col}]")
                    if rec is not None and rec.get("assigned"):
                        t.read_card_boosts(it["id"], repo, key, rec)
                    if rec is not None:
                        t.acknowledge(key, rec)
                    counts[repo] = counts.get(repo, 0) + 1
                    continue
                if rec is None:
                    card = t.card_create(tb["table"], tb[col], title, body, t.captain if assign else None)
                    rec = {"card": card, "column": col, "assigned": assign, "digest": digest, "comments": [], "boards": boards}
                    cards[key] = rec
                    t.log(f"created {key} card {card} in {col}")
                else:
                    if rec.get("digest") != digest:
                        t.card_update(rec["card"], tb["table"], title, body)
                        rec["digest"] = digest
                        rec["boards"] = boards
                        t.log(f"updated {key}")
                    if rec.get("column") != col:
                        t.card_move(rec["card"], tb["table"], tb[col])
                        t.log(f"moved {key} {rec.get('column')} -> {col}")
                        rec["column"] = col
                    if bool(rec.get("assigned")) != assign:
                        if assign:
                            t.card_assign(rec["card"], tb["table"])
                        else:
                            t.card_unassign(rec["card"])
                        rec["assigned"] = assign
                        t.log(f"{'assigned' if assign else 'unassigned'} {key}")
                self.read_card(it["id"], repo, key, rec)
                counts[repo] = counts.get(repo, 0) + 1
                t.save_json("map.json", cards)
        stale = sorted(k for k in cards if k not in wanted)
        t.log(("dry plan " + json.dumps(plan, sort_keys=True) + " " if t.dry else "")
              + "counts " + json.dumps(counts, sort_keys=True) + (f" unplaced {unplaced}" if unplaced else "")
              + (f" left-as-is {len(stale)} cards no longer in the backlog" if stale else ""))
        return plan

    def read_card(self, task, repo, key, rec):
        """The card readers for one mirrored card: the listened-to people's comments, and boosts when it is assigned."""
        self.t.read_card_comments(task, repo, key, rec)
        if rec.get("assigned"):
            self.t.read_card_boosts(task, repo, key, rec)
        self.t.acknowledge(key, rec)

    def relay(self, keys):
        """Run the card readers for the mirrored cards `keys` (task|board) alone, without touching the cards.

        A dry run reads only the boosts of an assigned card, as the mirror's does.
        """
        t = self.t
        cards = t.load("map.json", {})
        for key in sorted(keys):
            rec = cards.get(key)
            if rec is None:
                continue
            task, repo = key.split("|", 1)
            if t.dry:
                if rec.get("assigned"):
                    t.read_card_boosts(task, repo, key, rec)
                t.acknowledge(key, rec)
                continue
            self.read_card(task, repo, key, rec)
            t.save_json("map.json", cards)


class ChatInbox(Behavior):
    """Relay the listened-to people's chat questions, or every line they write in an "every_line" chat.

    Notifications run the chat reader for a chat as soon as it has a new line; the timer's
    step is the repair sweep (hourly with notifications on, else every 5 minutes).
    """
    name, keys = "chat-inbox", ("chats",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        # A chat is an id, or {"chat": id, "every_line": true} to relay every owner line.
        self.chats, self.every_line = [], set()
        for c in cfg.get("chats", []):
            cid = str(c["chat"] if isinstance(c, dict) else c)
            self.chats.append(cid)
            if isinstance(c, dict) and c.get("every_line"):
                self.every_line.add(cid)
        self.on = bool(self.chats)

    def run(self, items=None):
        self.t.read_chats(self.chats, self.every_line)


class ChatWatch(Behavior):
    """Relay the listened-to people's lines in the chats watched with `sync.py watch`, in any project of the account.

    A watch lives in chats.json, not in the config, so the agent starts one when it asks a
    question in a chat outside the project, and `sync.py unwatch` (or its "until" day
    passing) ends it. On while chats.json holds a watch. Notifications run the chat reader
    for a watched chat as soon as it has a new line; the timer's step is the repair sweep
    (hourly with notifications on, else every 5 minutes), which also ends the watches
    whose day has passed.
    """
    name, keys = "chat-watch", ("chats.json watches: sync.py watch",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        self.on = any(isinstance(r, dict) and "watch" in r for r in t.load("chats.json", {}).values())

    def run(self, items=None):
        self.t.end_watches()
        watched = self.t.watches()
        if watched:
            self.t.read_chats(sorted(watched), set(watched))


class ChatAsks(Behavior):
    """The agent puts questions to the owner in chat with `sync.py ask`."""
    name, keys, timer = "chat-asks", ("ask_chat",), False

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        self.on = cfg.get("ask_chat") is not None


class ReleaseAnnouncements(Behavior):
    """Post one Message Board announcement per new GitHub release: the sync's one automatic post."""
    name, keys, every = "release-announcements", ("releases",), SLOW

    def __init__(self, t, cfg, prereleases=False):
        super().__init__(t, cfg)
        self.releases = cfg.get("releases")
        if self.releases is not None:
            if not self.releases.get("board") or not isinstance(self.releases.get("repos"), dict):
                raise ValueError('config "releases" needs "board" (message board id) and "repos" ({"owner/name": ...})')
        self.prereleases = prereleases or bool((self.releases or {}).get("prereleases"))
        self.on = bool(self.releases)

    def run(self, items=None):
        """Per repo, releases.json keeps "since" (when the watch started), "seeded" (tags that
        existed then, never announced) and "announced" (tag -> message id). The first run
        only seeds. Drafts are always skipped and prereleases unless enabled. A failed read
        or post is logged and retried next run; it never fails the sync. Messages are never
        edited or deleted. Posted as the acting user whoever that is: an announcement is
        not an acknowledgement. A dry run reads and logs only.
        """
        t = self.t
        state = t.load("releases.json", {})
        board = str(self.releases["board"])
        for repo, spec in self.releases["repos"].items():
            spec = spec if isinstance(spec, dict) else {"name": spec}
            name = spec.get("name") or repo.split("/")[-1]
            name = name[:1].upper() + name[1:]
            rec = state.get(repo)
            try:
                listed = t.gh("release", "list", "-R", repo, "--limit", "30",
                              "--json", "tagName,isDraft,isPrerelease,publishedAt")
            except Exception as e:
                t.log(f"releases {repo}: {type(e).__name__}: {e}")
                continue
            if rec is None:
                tags = sorted(r["tagName"] for r in listed if not r.get("isDraft"))
                if t.dry:
                    t.log(f"dry releases {repo}: would seed the watch with {', '.join(tags) or 'no releases'}")
                    continue
                state[repo] = {"since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "seeded": tags, "announced": {}}
                t.save_json("releases.json", state)
                t.log(f"releases {repo}: watch started, seeded {', '.join(tags) or 'no releases'}")
                continue
            done = set(rec.get("seeded", [])) | set(rec.get("announced", {}))
            new = [r for r in listed if not r.get("isDraft") and (self.prereleases or not r.get("isPrerelease"))
                   and r["tagName"] not in done and (r.get("publishedAt") or "") > rec["since"]]
            for r in sorted(new, key=lambda r: r["publishedAt"]):
                tag = r["tagName"]
                subject = f"{name} {tag} released"
                if t.dry:
                    t.log(f"dry releases {repo}: post {subject!r}")
                    continue
                try:
                    rel = t.gh("release", "view", tag, "-R", repo, "--json", "tagName,body,url,isDraft,isPrerelease")
                    msg = t.message(board, subject, render_release(rel.get("body") or "", rel["url"], spec.get("note")))
                except Exception as e:
                    t.log(f"releases {repo} {tag}: announcement failed, retrying next run: {type(e).__name__}: {e}")
                    continue
                rec.setdefault("announced", {})[tag] = msg.get("id")
                t.save_json("releases.json", state)
                t.log(f"releases {repo}: announced {tag} as message {msg.get('id')}")


class SecurityAdvisories(Behavior):
    """Record each GitHub repository security advisory once, the first time it is seen in triage or draft: work for the
    agent to route to the repository's owner, never to act on by itself.

    "advisories": {"repos": ["owner/name", ...]} turns it on. Hourly, each repo's advisories are listed through
    `gh api repos/<repo>/security-advisories`; advisories.json keeps each one's last seen state, so an advisory is
    recorded once (kind "advisory") and again only when it leaves triage or draft for published or closed (kind
    "advisory-update"). A repo the login cannot read advisories for (HTTP 403 or 404) is recorded once as
    "advisory-unreadable" and stays quiet until it is readable again; any other failed read is logged and retried next
    run. The same repos' open Dependabot alerts are read on the same cadence (see dependabot). Nothing on GitHub is
    ever changed. A dry run reads and logs only.
    """
    name, keys, every = "security-advisories", ("advisories",), SWEEP

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        opts = cfg.get("advisories")
        if opts is not None and (not isinstance(opts, dict) or not isinstance(opts.get("repos"), list)
                                 or not all(isinstance(r, str) and re.fullmatch(r"[^/\s]+/[^/\s]+", r) for r in opts["repos"])):
            raise ValueError('config "advisories" needs "repos" (a list of "owner/name" GitHub repositories)')
        self.repos = list(opts["repos"]) if opts else []
        self.on = bool(self.repos)

    def run(self, items=None):
        t = self.t
        state = t.load("advisories.json", {"seen": {}, "unreadable": {}})
        for repo in self.repos:
            try:
                listed = t.gh("api", f"repos/{repo}/security-advisories?per_page=100")
            except Exception as e:
                denied = re.search(r"HTTP (403|404)", str(e))
                if not denied:
                    t.log(f"advisories {repo}: {type(e).__name__}: {e}")
                elif repo not in state["unreadable"]:
                    t.log(f"advisories {repo}: cannot read its security advisories (HTTP {denied.group(1)}); reported once")
                    if not t.dry:
                        state["unreadable"][repo] = denied.group(1)
                        t.record({"kind": "advisory-unreadable", "repo": repo, "status": denied.group(1),
                                  "url": f"https://github.com/{repo}/security/advisories"})
                        t.save_json("advisories.json", state)
                continue
            if state["unreadable"].pop(repo, None) is not None and not t.dry:
                t.save_json("advisories.json", state)
            for a in sorted(listed, key=lambda a: a.get("created_at") or ""):
                ghsa, now = a.get("ghsa_id"), a.get("state")
                key = f"{repo}#{ghsa}"
                before = state["seen"].get(key)
                if before == now or (before is None and now not in ("triage", "draft")):
                    continue
                kind = ("advisory" if before is None else
                        "advisory-update" if before in ("triage", "draft") and now in ("published", "closed") else None)
                if kind is None:  # another move, such as triage to draft: kept, not worth a record
                    if not t.dry:
                        state["seen"][key] = now
                        t.save_json("advisories.json", state)
                    continue
                if t.dry:
                    t.log(f"dry advisories {repo}: would record {kind} {ghsa} ({now})")
                    continue
                reporter = next(((c.get("user") or {}).get("login") for c in a.get("credits_detailed") or []
                                 if c.get("type") == "reporter"), None)
                t.record({"kind": kind, "repo": repo, "ghsa": ghsa, "summary": a.get("summary"),
                          "severity": a.get("severity"), "state": now, "previous": before,
                          "reporter": reporter or (a.get("author") or {}).get("login"),
                          "created_at": a.get("created_at"), "url": a.get("html_url")})
                state["seen"][key] = now
                t.save_json("advisories.json", state)
                t.log(f"advisories {repo}: recorded {kind} {ghsa} ({now})")
            self.dependabot(state, repo)

    def dependabot(self, state, repo):
        """Record each open Dependabot alert of repo once (kind "dependabot-alert"), and again if it reopens after
        leaving the open list (fixed or dismissed). The first read of a repo is a baseline: one "dependabot-baseline"
        record counts the alerts already open by severity and marks them seen, so a repo with many alerts is one note.
        A repo whose alerts cannot be read (HTTP 403 or 404, or a missing token scope, such as alerts turned off) is
        recorded once as "dependabot-unreadable"."""
        t = self.t
        seen, unreadable = state.setdefault("alerts", {}), state.setdefault("alerts_unreadable", {})
        try:
            listed = t.gh("api", f"repos/{repo}/dependabot/alerts?state=open&per_page=100")
        except Exception as e:
            denied = re.search(r"HTTP (403|404)", str(e))
            scope = re.search(r'needs the "?([\w:]+)"? scope', str(e))
            status = denied.group(1) if denied else f"missing scope {scope.group(1)}" if scope else None
            if status is None:
                t.log(f"dependabot {repo}: {type(e).__name__}: {e}")
            elif repo not in unreadable:
                t.log(f"dependabot {repo}: cannot read its Dependabot alerts ({status}); reported once")
                if not t.dry:
                    unreadable[repo] = status
                    t.record({"kind": "dependabot-unreadable", "repo": repo, "status": status,
                              "url": f"https://github.com/{repo}/security/dependabot"})
                    t.save_json("advisories.json", state)
            return
        if t.dry:
            for a in listed:
                if seen.get(f"{repo}#{a.get('number')}") != "open":
                    t.log(f"dry dependabot {repo}: would record alert {a.get('number')}")
            return
        unreadable.pop(repo, None)
        baselined = state.setdefault("alerts_baselined", [])
        if repo not in baselined:
            counts = {}
            for a in listed:
                sev = (a.get("security_vulnerability") or {}).get("severity") or \
                    (a.get("security_advisory") or {}).get("severity") or "unknown"
                counts[sev] = counts.get(sev, 0) + 1
                seen[f"{repo}#{a.get('number')}"] = "open"
            baselined.append(repo)
            t.record({"kind": "dependabot-baseline", "repo": repo, "open": len(listed), "by_severity": counts,
                      "url": f"https://github.com/{repo}/security/dependabot"})
            t.save_json("advisories.json", state)
            t.log(f"dependabot {repo}: baseline of {len(listed)} open alert(s) recorded")
            return
        open_now = {f"{repo}#{a.get('number')}" for a in listed}
        for key in [k for k, v in seen.items() if k.startswith(f"{repo}#") and v == "open" and k not in open_now]:
            seen[key] = "closed"  # fixed or dismissed: a later reopen is recorded again
        for a in sorted(listed, key=lambda a: a.get("number") or 0):
            key = f"{repo}#{a.get('number')}"
            before = seen.get(key)
            if before == "open":
                continue
            adv, vuln = a.get("security_advisory") or {}, a.get("security_vulnerability") or {}
            t.record({"kind": "dependabot-alert", "repo": repo, "number": a.get("number"),
                      "severity": vuln.get("severity") or adv.get("severity"),
                      "package": (vuln.get("package") or {}).get("name"),
                      "ecosystem": (vuln.get("package") or {}).get("ecosystem"),
                      "fixed_in": (vuln.get("first_patched_version") or {}).get("identifier"),
                      "manifest": (a.get("dependency") or {}).get("manifest_path"),
                      "summary": adv.get("summary"), "ghsa": adv.get("ghsa_id"), "reopened": before is not None,
                      "created_at": a.get("created_at"), "url": a.get("html_url")})
            seen[key] = "open"
            t.log(f"dependabot {repo}: recorded alert {a.get('number')}")
        t.save_json("advisories.json", state)


class CheckinAnswering(Behavior):
    """Record due check-in questions; the agent answers each once a day with `sync.py answer`."""
    name, keys, every = "checkin-answering", ("checkins",), SLOW

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        self.checkins = cfg.get("checkins")
        if self.checkins is not None and not isinstance(self.checkins.get("questionnaires"), list):
            raise ValueError('config "checkins" needs "questionnaires" (a list of questionnaire ids)')
        self.on = bool(self.checkins)

    def run(self, items=None):
        self.t.read_checkins(self.checkins["questionnaires"])
        self.t.read_answer_boosts(self.checkins["questionnaires"])


class DecisionTodos(Behavior):
    """Decisions for the owner as assigned to-dos: the agent creates and completes them; the owner's comments are relayed.

    Notifications run a to-do's reader when it has a new comment; the timer's step reads
    every open one as the repair sweep (hourly with notifications on, else every 5 minutes).
    """
    name, keys = "decision-todos", ("todos",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        todos = cfg.get("todos")
        if todos is not None and (not isinstance(todos, dict) or set(todos) - {"todoset", "list", "project"}):
            raise ValueError('config "todos" must be an object: {}, {"todoset": id} or {"list": id}, '
                             'with "project": id when they are in another project')
        self.on = todos is not None

    def run(self, items=None):
        self.t.read_todo_comments(requests=False)


class AssignedTodos(Behavior):
    """To-dos the listened-to people assign to the agent's login, as requests: recorded once, acknowledged with 👀 and
    tracked, so their comments, boosts, edits and closing arrive as records of the request; the agent completes each
    with `sync.py todo complete` when the work is done. To-dos assigned to anyone else are ignored. A request carries its
    due date and Basecamp's repetition rule; completing a repeating one makes Basecamp create the next occurrence, which
    no event announces: this sweep records it once as a new request that follows the completed one.

    "assigned_todos": {} turns it on; {"limit": n} fetches at most n newly assigned to-dos a
    sweep (default 10). {"scope": "account"} takes to-dos assigned to the agent in every
    project of the account, each request carrying its project; the default, "project", only
    the configured project's. Only one home per login should run "account" (the main home,
    which routes each request to the right domain). Notifications see the assignment,
    comments and completion first; the timer's sweep every 5 minutes, of the agent's open
    assignments and every open request, is the repair sweep and the only reader of a
    request's edits and the owner's boosts on it, which reach no notification.
    """
    name, keys, every = "assigned-todos", ("assigned_todos",), SLOW
    things = "to-dos"

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        key = self.keys[0]
        opts = cfg.get(key)
        if opts is not None and not isinstance(opts, (bool, dict)):
            raise ValueError(f'config "{key}" must be {{}} or {{"limit": <newly assigned {self.things} read per sweep>, '
                             '"scope": "project" | "account"}')
        self.on = opts is not None and opts is not False
        opts = opts if isinstance(opts, dict) else {}
        self.limit = int(opts.get("limit", 10))
        if self.limit < 1:
            raise ValueError(f'config "{key}" "limit" must be at least 1')
        self.scope = opts.get("scope", "project")
        if self.scope not in ("project", "account"):
            raise ValueError(f'config "{key}" "scope" must be "project" (the default) or "account"')
        self.account_wide = self.on and self.scope == "account"

    def run(self, items=None):
        """The sweep: new requests, then the open ones' comments and boosts, then their edits and closing."""
        t, cards = self.t, self.name == "assigned-cards"
        t.discover_todo_requests(self.limit, cards)
        t.read_todo_comments(keys=t.request_keys(cards), requests=True)
        for key in t.request_keys(cards):
            t.refresh_todo_request(key)


class AssignedCards(AssignedTodos):
    """Cards the listened-to people assign to the agent's login, as requests, read like to-do requests: recorded once as
    a `card-request`, acknowledged with 👀 and tracked in todos.json ("type": "card"), so their comments, boosts, edits
    and closing arrive as records of the request; the agent comments with `sync.py todo comment` and completes each with
    `sync.py todo complete`, which Basecamp shows by moving the card to Done. A card the card mirror made from the
    backlog is never a request.

    "assigned_cards": {} turns it on, with the same "limit" and "scope" as "assigned_todos".
    Notifications see the assignment (an "Event" on the card), comments and completion
    first; the timer's sweep every 5 minutes is the repair sweep and the only reader of a
    request's edits and boosts.
    """
    name, keys, every = "assigned-cards", ("assigned_cards",), SLOW
    things = "cards"


class Reports(Behavior):
    """The agent posts reports to the Message Board with `sync.py post-message`; the owner's comments and boosts on them are relayed.

    Notifications run a message's reader when it has a new comment or boost; the timer's
    step reads the agent's recent posts as the repair sweep.
    """
    name, keys = "reports", ("message_board",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        self.on = cfg.get("message_board") is not None
        self.board = str(cfg.get("message_board"))

    def run(self, items=None):
        self.t.read_messages(self.board)


class Pings(Behavior):
    """Relay every line the owner writes in a Ping (a direct message) with the agent's login; the agent answers with `sync.py reply`.

    "pings": {} turns it on; {"limit": n} reads at most n Pings a sweep (default 10), the
    most recently active first. Notifications run the reader for a Ping as soon as it has
    a new line; the timer's step is the repair sweep.
    """
    name, keys = "pings", ("pings",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        pings = cfg.get("pings")
        if pings is not None and not isinstance(pings, (bool, dict)):
            raise ValueError('config "pings" must be {} or {"limit": <Pings read per run>}')
        self.on = pings is not None and pings is not False
        self.limit = int((pings if isinstance(pings, dict) else {}).get("limit", 10))
        if self.limit < 1:
            raise ValueError('config "pings" "limit" must be at least 1')

    def run(self, items=None):
        self.t.read_pings(self.limit)


class BlockedReplies(Behavior):
    """Answer someone the sync does not listen to, once, when they address the agent; nothing they write reaches it.

    "blocked": {} turns it on with the default reply, {"reply": "<text>"} with that text;
    absent or false, a blocked sender hears nothing (as before). Either way no record,
    inbox note or 👀 is made for them and the block is logged. The readers post the reply
    where the sender wrote (Tools.block): to an @mention of the agent in a chat line or a
    comment, a Ping line, or a to-do or card they assigned it, at most once per person
    and conversation a day. The timer step only starts the clock the first time, so
    nothing from before it was on is answered.
    """
    name, keys = "blocked-replies", ("blocked",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        self.on = t.blocked_reply is not None

    def run(self, items=None):
        self.t.blocked_since()


class InboxDelivery(Behavior):
    """Deliver each new pending record to the firstmate inbox as a note: the wake for the agent.

    "inbox": {} delivers to the --home's bin/fm-inbox.sh; {"fm_home": "<home>"} to another
    home's. Records are delivered in order from a line cursor in inbox.json; the first
    run starts at the records that existed when it began, so history is not delivered.
    Each note's request id (basecamp-<kind>-<id>) makes a replay not a new note. A failed
    note stops the run's delivery, is logged (FAILED once per request id, so the wake
    check sees it) and retried next run; it never fails the sync. A dry run logs only.
    """
    name, keys = "inbox-delivery", ("inbox",)

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        inbox = cfg.get("inbox")
        if inbox is not None and inbox is not True and not isinstance(inbox, dict):
            raise ValueError('config "inbox" must be {} or {"fm_home": "<firstmate home>"}')
        self.on = inbox is not None and inbox is not False
        self.fm_home = os.path.abspath((inbox if isinstance(inbox, dict) else {}).get("fm_home") or t.home)
        # Where a first run starts: the records that exist before this run's readers append.
        self.start = len(t.pending_lines()) if self.on else 0

    def run(self, items=None):
        self.deliver()

    def deliver(self):
        t = self.t
        state = t.load("inbox.json", None)
        if state is None:
            state = {"cursor": self.start, "failing": []}
        lines = t.pending_lines()
        for i in range(state["cursor"], len(lines)):
            try:
                rec = json.loads(lines[i])
            except ValueError:
                t.log(f"inbox: line {i + 1} of pending-comments.jsonl is not JSON, skipped")
                state["cursor"] = i + 1
                continue
            rid, body = inbox_note(rec, t.account, t.project)
            if t.dry:
                t.log(f"dry inbox: note {rid}")
                continue
            try:
                outcome = t.inbox_note(self.fm_home, rid, body)
            except Exception as e:
                if rid in state["failing"]:
                    t.log(f"inbox: note {rid} still failing, retrying next run: {type(e).__name__}: {e}")
                else:
                    state["failing"].append(rid)
                    t.log(f"FAILED inbox note {rid}, retrying next run: {type(e).__name__}: {e}")
                break
            state["cursor"] = i + 1
            state["failing"] = [f for f in state["failing"] if f != rid]
            t.log(f"inbox: note {rid} {outcome or 'sent'}")
        if not t.dry:
            t.save_json("inbox.json", state)


class SessionAlert(Behavior):
    """Tell the owner in chat when the home's firstmate session is down while inbox notes wait for it.

    "session_alert": {"chat": <chat id>, "after_minutes": 15} (with "inbox" on) checks each
    minute, read-only, the inbox delivery's firstmate home: whether a live process holds its
    primary session lock (state/.lock; a lock from before this boot is stale) and how long
    its oldest pending note has waited. Once no session holds the lock and a note has waited
    "after_minutes", it posts one line in the chat, @mentioning the owner, and records it in
    session-alert.json so the next runs stay quiet; a session holding the lock again clears it.
    A failed post is logged and retried next run; it never fails the sync. A dry run logs only.
    """
    name, keys, every = "session-alert", ("session_alert",), 60

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        sa = cfg.get("session_alert")
        if sa is None or sa is False:
            return
        if not isinstance(sa, dict) or sa.get("chat") is None or set(sa) - {"chat", "after_minutes"}:
            raise ValueError('config "session_alert" must be {"chat": <chat id>, "after_minutes": <minutes>}')
        if cfg.get("inbox") in (None, False):
            raise ValueError('config "session_alert" needs "inbox": it watches the inbox delivery\'s firstmate home')
        after = sa.get("after_minutes", 15)
        if not isinstance(after, (int, float)) or isinstance(after, bool) or after < 0:
            raise ValueError('config "session_alert" "after_minutes" must be a number of minutes, 0 or more')
        self.on, self.chat, self.after = True, str(sa["chat"]), after * 60
        inbox = cfg.get("inbox")
        self.fm_home = os.path.abspath((inbox if isinstance(inbox, dict) else {}).get("fm_home") or t.home)

    def run(self, items=None):
        t = self.t
        state = t.load("session-alert.json", {})
        holder = t.session_holder(self.fm_home)
        if holder is not None:
            if state.get("alerted"):
                t.log(f"session alert cleared: pid {holder} holds the firstmate session lock again")
                if not t.dry:
                    t.save_json("session-alert.json", {})
            return
        if state.get("alerted"):
            return
        notes = t.waiting_notes(self.fm_home)
        now = datetime.now(timezone.utc).timestamp()
        if not notes or now - notes[0] < self.after:
            return
        since = t.local_time(datetime.fromtimestamp(notes[0], timezone.utc))
        if t.dry:
            t.log(f"dry session alert: post in chat {self.chat}, {len(notes)} note(s) waiting since {since}")
            return
        if t.refused("session alert", "owner"):
            t.save_json("session-alert.json", {"alerted": now_iso(), "refused": True})
            return
        text = (f"This home's firstmate session is down: no session holds its lock at {html.escape(self.fm_home)}, "
                f"and {len(notes)} Basecamp message{'s' if len(notes) != 1 else ''} "
                f"{'have' if len(notes) != 1 else 'has'} been waiting for it since {since}. "
                "Start the home's firstmate session again to handle them; I won't repeat this until it is back.")
        try:
            line = t.chat_line(self.chat, f"<div>{t.owner_mention()} {text}</div>")
        except Exception as e:
            t.log(f"FAILED session alert in chat {self.chat}, retrying next run: {type(e).__name__}: {e}")
            return
        t.save_json("session-alert.json", {"alerted": now_iso(), "line": line.get("id"), "notes": len(notes)})
        t.log(f"session alert: posted line {line.get('id')} in chat {self.chat}: no session holds the lock, "
              f"{len(notes)} note(s) waiting since {since}")


class Backup(Behavior):
    """Keep one latest snapshot of this config and its state files in a Basecamp Docs & Files folder.

    "backup": {"project": <bucket id>, "vault": <Docs & Files folder id>} turns it on. Daily, and on demand
    with `sync.py backup`, each backed-up file (BACKED_UP) whose content changed since its last upload is
    uploaded: as a new version of its existing upload (`basecamp files replace`: same id, URL and name;
    nobody is notified), or as a new upload when the folder has none of that name yet. backup.json keeps
    each file's upload id and the digest last uploaded, so an unchanged file makes no call. A file that
    holds a credential (a secret-looking config key, or a token-shaped value anywhere) is never uploaded:
    it is logged and skipped. Nothing in the folder is ever deleted or trashed. A failed upload is logged
    and retried on the next backup; it never fails the sync. A dry run logs only.
    """
    name, keys, every = "backup", ("backup",), DAY

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        b = cfg.get("backup")
        if b is None or b is False:
            return
        if not isinstance(b, dict) or set(b) != {"project", "vault"} or not all(str(b[k]).isdigit() for k in b):
            raise ValueError('config "backup" must be {"project": <bucket id>, "vault": <Docs & Files folder id>}')
        self.on, self.project, self.vault = True, str(b["project"]), str(b["vault"])

    def run(self, items=None):
        t = self.t
        state = t.load("backup.json", {})
        done = state.setdefault("files", {})
        listed = None  # the folder's uploads by name, read once, only when a file has no upload yet
        sent = same = 0
        for name, data in t.local_files(BACKED_UP):
            digest = hashlib.sha256(data).hexdigest()
            rec = done.get(name) or {}
            if rec.get("sha256") == digest and rec.get("upload"):
                same += 1
                continue
            secret = holds_secret(name, data)
            if secret:
                t.log(f"backup: skipped {name}, it holds a credential ({secret}); it is never uploaded")
                continue
            if t.dry:
                t.log(f"dry backup: upload {name} to folder {self.vault}"
                      + (f" as a new version of upload {rec['upload']}" if rec.get("upload") else ""))
                continue
            try:
                upload = rec.get("upload")
                if upload:
                    try:
                        t.upload_replace(upload, t.path(name), self.project)
                    except Exception as e:
                        if getattr(e, "code", None) != "not_found":
                            raise
                        upload = None  # its upload is gone: find another of the name, else upload anew
                if not upload:
                    if listed is None:
                        listed = {}
                        for u in t.uploads_list(self.vault, self.project):
                            listed.setdefault(u.get("filename") or u.get("title"), u.get("id"))
                    upload = listed.get(name)
                    if upload:
                        t.upload_replace(upload, t.path(name), self.project)
                    else:
                        upload = t.upload_create(t.path(name), self.vault, self.project).get("id")
            except Exception as e:
                t.log(f"FAILED backup {name}, retrying on the next backup: {type(e).__name__}: {e}")
                continue
            done[name] = {"upload": upload, "sha256": digest, "at": now_iso()}
            t.save_json("backup.json", state)
            sent += 1
        t.log(f"backup to folder {self.vault}: {sent} uploaded, {same} unchanged" + (" (dry run)" if t.dry else ""))


class Timesheet(Behavior):
    """Post the agent's active time on each piece of work to the Basecamp timesheet of its to-do or card, once per day.

    "timesheet": {} turns it on. The agent says what it works on with `sync.py timesheet start
    --key <key> [--on <to-do or card URL>] --workdir <dir>` and `timesheet stop --key <key>`
    (timesheet.json "items"). Active time is read from the Claude Code transcripts of each
    source, only between its start and stop: a gap of at most GAP between two events of a
    transcript is active, and so is a whole wait of at most TOOL from a tool call the agent
    made to its result; the
    spans of all a key's transcripts are merged (time is never counted twice), split by local
    day (the config "timezone", default America/New_York) and rounded to the nearest quarter
    hour. Hourly, each finished day (TOOL past its local midnight, so its last tool wait is
    known) with a quarter hour or more is posted as one entry on the key's
    to-do or card: the "posted" ledger keeps each key's posted days, and before each post the
    to-do's or card's entries are read, so a day this login already has this key's entry for there
    (DESCRIPTION and the key, that date) is only added to the ledger; another key's entry there
    does not stop the post. A key with no to-do or card is
    skipped and reported once (a `timesheet-unmapped` record); its days post once it has one.
    A project whose timesheet refuses an entry (forbidden, invalid) is reported once
    (a `timesheet-refused` record, one FAILED line); from then on it is tried with one entry (a
    probe) at most once a local day, logged without FAILED, until an entry there posts again.
    A key whose to-do or card is not found is reported and tried the same way, alone
    (a `timesheet-missing` record, state "missing"), so the project's other keys still post: a
    not found never uses up the project's daily try, also during it.
    A stopped key whose days wait only on such a refusal keeps them as "unposted" (its
    sources marked "counted", never read again; `timesheet start` adds new sources to them)
    and posts them with the daily probe, settling once all are posted. Any other failed entry is tried again on the next run. A key with a transcript that cannot be read is neither posted nor settled that
    run, only logged, so no day saves a partial total. Nothing is posted without a profile that is not the owner's. A dry
    run, and `sync.py timesheet post --dry-run`, only prints what it would post.
    """
    name, keys, every = "timesheet", ("timesheet",), SWEEP

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        ts = cfg.get("timesheet")
        if ts is None or ts is False:
            return
        if ts != {}:
            raise ValueError('config "timesheet" must be {}')
        self.on = True
        self.zone = ZoneInfo(str(cfg.get("timezone") or DEFAULT_ZONE))  # the config checked "timezone" already

    def hours(self, item, now):
        """{local day (YYYY-MM-DD): hours, in quarters} of the active time in `item`'s sources not yet "counted" into
        its "unposted" days; days under 1/8 h left out."""
        spans = []
        for src in item.get("sources") or []:
            if src.get("counted"):
                continue
            lo = datetime.fromisoformat(src["from"].replace("Z", "+00:00"))
            hi = datetime.fromisoformat(src["to"].replace("Z", "+00:00")) if src.get("to") else now
            for times, waits in self.t.transcript_events(src["path"], lo).values():
                for a, b in active_spans(times, waits, GAP, TOOL):
                    a, b = max(a, lo), min(b, hi)
                    if a < b:
                        spans.append((a, b))
        days = {}
        for a, b in merged(spans):
            for day, dur in local_days(a, b, self.zone):
                days[day] = days.get(day, timedelta(0)) + dur
        out = {d.isoformat(): quarters(dur) for d, dur in sorted(days.items())}
        return {d: h for d, h in out.items() if h > 0}

    def target(self, key, item):
        """Where `key`'s entries go: (bucket, recording id, URL), from its "on", else a to-do tracked under the key
        (todos.json), else its mirrored card (map.json); None when it has none."""
        t = self.t
        on = item.get("on")
        if on:
            return str(on["bucket"]), on["recording"], on.get("url")
        rec = t.load("todos.json", {}).get(key)
        if rec and rec.get("todo"):
            return str(rec.get("bucket") or t.project), rec["todo"], rec.get("url")
        rec = t.load("map.json", {}).get(key)
        if rec and rec.get("card"):
            return (str(t.project), rec["card"],
                    f"https://3.basecamp.com/{t.account}/buckets/{t.project}/card_tables/cards/{rec['card']}")
        return None

    def run(self, items=None):
        t = self.t
        state = t.load("timesheet.json", {})
        tracked = state.get("items") or {}
        if not tracked:
            return
        now = datetime.now(timezone.utc)
        today = now.astimezone(self.zone).date().isoformat()
        final = (now - TOOL).astimezone(self.zone).date().isoformat()  # days before it can gain no more time
        posted, refused = state.setdefault("posted", {}), state.setdefault("refused", {})
        missing = state.setdefault("missing", {})
        unmapped = state.setdefault("unmapped", {})
        dry, planned, me = "dry " if t.dry else "", 0, None
        tried = set()  # refused projects and missing keys whose one entry for this run has been tried

        def save():
            if not t.dry:
                t.save_json("timesheet.json", state)

        for key, item in sorted(tracked.items()):
            if item.get("settled"):
                continue
            try:
                days = dict(item.get("unposted") or {})
                for d, h in self.hours(item, now).items():
                    days[d] = days.get(d, 0) + h
            except OSError as e:  # a transcript unreadable now: post and settle nothing, so no day saves a partial total
                t.log(f"FAILED timesheet: {key} not posted, a transcript could not be read, retrying next run: {e}")
                continue
            due = {d: h for d, h in days.items() if d < final and d not in (posted.get(key) or {})}
            sources = item.get("sources") or []
            stopped = bool(sources) and all(s.get("to") and local_day(s["to"], self.zone) < final for s in sources)
            if not due:
                if stopped:
                    item["settled"] = True  # stopped before a final day, every day with time posted: never read again
                    item.pop("unposted", None)
                    save()
                continue
            target = self.target(key, item)
            if target is None:
                total = sum(due.values())
                if t.dry or key not in unmapped:  # logged and recorded once; a dry run always says it
                    t.log(f"{dry}timesheet: {key} has no to-do or card, so {total:g} h on {', '.join(sorted(due))} "
                          f"is not posted; sync.py timesheet start --key {key} --on <to-do or card URL> gives it one")
                if not t.dry and key not in unmapped:
                    unmapped[key] = now_iso()
                    t.record({"kind": "timesheet-unmapped", "key": key, "days": due, "hours": total, "at": unmapped[key]})
                    save()
                continue
            if unmapped.pop(key, None):
                save()
            bucket, rid, url = target
            for day, h in sorted(due.items()):
                pscope, kscope, proj, gone = ("project", bucket), ("key", key), refused.get(bucket), missing.get(key)
                held = [(w, sc, r) for w, sc, r in ((f"project {bucket}", pscope, proj),
                                                    (f"{key}'s to-do or card", kscope, gone)) if r is not None]
                waiting = [w for w, sc, r in held if sc in tried or r.get("probed") == today]
                if waiting:
                    if t.dry:
                        t.log(f"dry timesheet: {key} {h:g} h on {day} waits: {waiting[0]} refused entries; "
                              f"it is tried again once a day")
                    continue
                what = held[0][0] if held else None
                planned += 1
                if t.dry:
                    t.log(f"dry timesheet: {'try ' + what + ' again with ' if what else 'post '}"
                          f"{h:g} h on {day} for {key} on {url or rid}")
                    tried.update(sc for _, sc, _ in held)
                    continue
                if me is None:
                    try:
                        if t.refused("timesheet", "owner"):
                            return
                    except RuntimeError as e:
                        t.log(f"timesheet: nothing posted, retrying next run: {e}")
                        return
                    me = t.acting_id()
                if gone is not None:
                    tried.add(kscope)
                    gone["probed"] = today
                    save()
                try:
                    entry = next((e for e in t.timesheet_entries(bucket, rid, day, me)
                                  if e.get("date") == day and (e.get("creator") or {}).get("id") == me
                                  and str(e.get("description") or "").strip() == f"{DESCRIPTION}{key}"), None)
                    found = entry is not None
                    entry = entry or t.timesheet_entry(bucket, rid, day, h, f"{DESCRIPTION}{key}")
                except Exception as e:
                    code = getattr(e, "code", None)
                    if code == "not_found":  # about this to-do or card only: the project's daily try is not used up
                        tried.add(kscope)
                        if gone is None:
                            missing[key] = {"at": now_iso(), "error": str(e), "code": code, "date": day, "probed": today}
                            t.record({"kind": "timesheet-missing", "key": key, "bucket": int(bucket), "date": day,
                                      "hours": h, "url": url, "error": str(e), "code": code, "at": missing[key]["at"]})
                            t.log(f"FAILED timesheet: {key}'s to-do or card {url or rid} was not found for its {h:g} h "
                                  f"on {day} ({type(e).__name__}: {e}); its entries are tried again once a day")
                        else:
                            t.log(f"timesheet: {key}'s to-do or card {url or rid} still not found ({h:g} h on {day}: "
                                  f"{type(e).__name__}: {e}); tried again tomorrow")
                        save()
                        continue
                    if proj is None and code in REFUSED:
                        refused[bucket] = {"at": now_iso(), "error": str(e), "code": code, "key": key, "date": day,
                                           "probed": today}
                        tried.add(pscope)
                        t.record({"kind": "timesheet-refused", "bucket": int(bucket), "key": key, "date": day, "hours": h,
                                  "url": url, "error": str(e), "code": code, "at": refused[bucket]["at"]})
                        t.log(f"FAILED timesheet: project {bucket}'s timesheet refused {key}'s {h:g} h on {day} on "
                              f"{url or rid} ({type(e).__name__}: {e}); its entries are tried again once a day")
                        save()
                    elif what:
                        if proj is not None:
                            tried.add(pscope)
                            proj["probed"] = today
                            save()
                        t.log(f"timesheet: {what} still refuses entries ({key} {h:g} h on {day}: "
                              f"{type(e).__name__}: {e}); tried again tomorrow")
                    else:
                        t.log(f"FAILED timesheet: {key} {h:g} h on {day} on {url or rid}, retrying next run: "
                              f"{type(e).__name__}: {e}")
                    continue
                if refused.pop(bucket, None) is not None:
                    t.log(f"timesheet: project {bucket} accepts entries again")
                if missing.pop(key, None) is not None:
                    t.log(f"timesheet: {key}'s to-do or card accepts entries again")
                posted.setdefault(key, {})[day] = {"hours": h, "entry": entry.get("id"), "url": entry.get("app_url"),
                                                   "at": now_iso()}
                t.log(f"timesheet: {'found an entry already posted' if found else 'posted'} {h:g} h on {day} "
                      f"for {key} on {url or rid}")
                if stopped and all(d in posted[key] for d in due):
                    item["settled"] = True
                    item.pop("unposted", None)
                save()
            left = {d: h for d, h in due.items() if d not in (posted.get(key) or {})}
            fresh = [s for s in sources if not s.get("counted")]
            if stopped and left and (bucket in refused or key in missing) and not t.dry and fresh:
                # Stopped, and its days wait only on a refusal: kept, so its transcripts are not read again.
                item["unposted"] = left
                for s in fresh:
                    s["counted"] = True
                t.log(f"timesheet: {key} stopped with {sum(left.values()):g} h on {', '.join(sorted(left))} not posted: "
                      f"{'project ' + bucket if bucket in refused else 'its to-do or card'} refuses entries; "
                      f"they are tried again once a day")
                save()
        if t.dry and not planned:
            t.log("dry timesheet: nothing to post")


REFUSED = ("forbidden", "validation")  # basecamp CLI error codes: the project's timesheet refuses the entry
DEFAULT_ZONE = "America/New_York"  # the day active time is counted and posted by, without a config "timezone"
GAP, TOOL = timedelta(minutes=5), timedelta(minutes=60)  # the longest active gap between events, and on a tool call
DESCRIPTION = "Active agent time: "  # what each entry's description starts with, before its key


def active_spans(times, waits, gap, tool):
    """The active spans of one transcript: each gap of at most `gap` between its sorted event `times`, and each tool
    wait in `waits` (call time, result time) of at most `tool` in all; a longer wait adds nothing."""
    return ([(a, b) for a, b in zip(times, times[1:]) if timedelta(0) < b - a <= gap]
            + [(a, b) for a, b in waits if timedelta(0) < b - a <= tool])


def merged(spans):
    """`spans` with overlapping ones joined, in order, so no time counts twice."""
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def local_days(a, b, zone):
    """(local date, duration) of the span from `a` to `b`, cut at each midnight in `zone`."""
    while a < b:
        day = a.astimezone(zone).date()
        midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(timezone.utc)
        end = min(b, midnight)
        yield day, end - a
        a = end


def local_day(at, zone):
    """The day (YYYY-MM-DD) in `zone` of ISO time `at`."""
    return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(zone).date().isoformat()


def quarters(dur):
    """A duration in hours, rounded to the nearest quarter hour (an eighth rounds up)."""
    return math.floor(dur.total_seconds() / 900 + 0.5) / 4


# The files a backup uploads: the config, the JSON and JSONL state beside it and the home's workflow defaults
# (defaults.md); never a lock, a .tmp, sync.log, a hand copy (config.json.bak-...) or backup.json itself, which changes
# with every backup.
BACKED_UP = re.compile(r"(?!backup\.json$)[\w.-]+\.jsonl?|defaults\.md")
# A config key that names a credential, and a value shaped like a token (GitHub, Slack, OpenAI-style, bearer, PEM).
SECRET_KEY = re.compile(r"(?i)(token|secret|passw(or)?d|api[_-]?key|credentials?|private[_-]?key|auth[_-]?header)")
SECRET_VALUE = re.compile(r"\b(gh[opusr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[abprs]-[\w-]{10,}|sk-[\w-]{20,})"
                          r"|Bearer [\w.~+/-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")


def holds_secret(name, data):
    """Why the file `name` with content `data` must not be uploaded (it holds a credential), or None.

    Any file is refused for a token-shaped value; config.json, which should hold only ids and settings, also
    for a key that names a credential. State files are not key-checked: their keys are task names and ids.
    """
    text = data.decode("utf-8", "replace")
    m = SECRET_VALUE.search(text)
    if m:
        return "a token-shaped value"
    if name == "config.json":
        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield str(k)
                    yield from keys(v)
            elif isinstance(o, list):
                for v in o:
                    yield from keys(v)
        try:
            found = [k for k in keys(json.loads(text)) if SECRET_KEY.search(k)]
        except ValueError:
            return "it is not valid JSON, so it cannot be checked"
        if found:
            return f"key {found[0]!r}"
    return None


class Notifications(Behavior):
    """Read the agent login's Basecamp notifications and boosts every run, and run the reader for each thread that changed.

    "Owner" below means anyone the sync listens to (the captain, "operators", "people" and admitted "participants").
    On whenever the config has a "profile" ("notifications": false turns it off):
    /my/readings.json and /my/boosts.json are the acting user's, so it reads nothing
    while the acting user is the owner, unset or unknown. It takes over reading the
    owner's input from chat-inbox, pings, decision-todos, reports, assigned-todos and the
    card mirror's comments, whose own readers then run as the hourly repair sweep.

    Each run makes one read of /my/readings.json (unread items and the first page of read
    ones, every project) and keeps a cursor per item in notifications.json (item id ->
    its unread_at). An item is a pointer to a whole thread ("this to-do has 2 new
    comments", "this chat has a new line"), so for each one whose cursor moved, the
    existing reader for that thread runs once, records what is new by its own cursor and
    acknowledges it, exactly as the sweep would:

      - a project chat in "chats", or a chat watched with `sync.py watch` (in any project
        of the account): the chat reader, for that chat; an @mention or a Campfire reply
        to the agent's line there is recorded even without "?";
      - a Ping with an owner in it (pings on): the Ping reader, for that Ping;
      - a comment or @mention on a mirrored card, an open tracked to-do or the agent's
        own message: that card's, to-do's or message's reader;
      - a to-do assigned to the agent, or completed (assigned-todos on; in any project
        with "scope": "account"): the to-do request reader, or the request's edit and
        closing check;
      - a check-in Reminder (checkin-answering on): the check-in reader;
      - an owner's @mention of the agent in a thread nothing tracks: a `chat-question`
        (in a chat) or `mention` record of its own; any other comment there only when the
        thread awaits an answer (`reply --awaiting`), else it is skipped
        (Tools.read_thread).

    Owner input in the project that none of these handles (a comment on a Document, a
    line in a chat not in "chats", a to-do assigned with assigned-todos off) is an
    `unmonitored` record, once per kind, unless "notifications" has "unmonitored": false
    (or an old "listen" had it). Items in other projects are another home's: never read
    and never marked read here, except a Ping, an account-wide to-do request, a line in a
    watched chat, and, with
    "mentions": {"scope": "account"}, an @mention of the agent or a comment on a thread it
    follows there, which its thread reader (or, for a chat line, the mention reader)
    records as it would in the project, carrying that project.

    Boosts: one read of /my/boosts.json, the boosts on the agent's own recordings. Each new
    owner boost runs the reader whose state holds the boosted recording, which records it
    as a `boost` (or the captain's 👍 on an assigned card as an `approval`) once this same
    read lists it (Tools.confirmed); a boost on
    something nothing monitors in the project is unmonitored. A boost on a to-do request
    (the owner's own to-do) is not listed there and waits for the sweep.

    Mark read: once an item is handled with no failed call, it is marked read
    ("mark_read": false keeps them unread), so the unread list stays under Basecamp's
    100 and the sidebar honest. The cursor, never the read flag, decides what is new, so
    someone marking everything read loses nothing, and an item whose reader failed keeps
    its old cursor and is retried next run. The first run only seeds the cursors and the
    boosts seen, so history is not replayed; so is any later item with no cursor that was
    unread more than five minutes before the previous read (it dropped out of a read and came
    back), since its thread was read then. A dry run reads, logs and saves nothing.
    """
    name, keys = "notifications", ("notifications",)
    QUIET = ("BoostReport", "Bulletin", "Onboarding")  # Basecamp's own items: never owner input

    def __init__(self, t, cfg):
        super().__init__(t, cfg)
        opts = cfg.get("notifications")
        if opts is not None and not isinstance(opts, (bool, dict)):
            raise ValueError('config "notifications" must be false or {"mark_read": false, "unmonitored": false}')
        self.on = bool(t.profile) and opts is not False
        opts = opts if isinstance(opts, dict) else {}
        old = cfg.get("listen") if isinstance(cfg.get("listen"), dict) else {}
        for k in ("mark_read", "unmonitored"):
            if not isinstance(opts.get(k, True), bool):
                raise ValueError(f'config "notifications" "{k}" must be true or false')
        self.mark = opts.get("mark_read", True)
        self.unmonitored = self.on and opts.get("unmonitored", old.get("unmonitored", True) is not False)
        mentions = cfg.get("mentions")
        if mentions is not None and (not isinstance(mentions, dict) or set(mentions) - {"scope"}
                                     or mentions.get("scope", "project") not in ("project", "account")):
            raise ValueError('config "mentions" must be {} or {"scope": "project" | "account"}')
        self.anywhere = self.on and (mentions or {}).get("scope") == "account"
        self.others = {}  # every behavior by name, set by configure()
        self.watched = {}  # the chats watched this run (`sync.py watch`): chat id -> its chats.json record
        self.held = {}  # the chats watched but skipped this run (`watch_held`), left to the home that reads their project

    def run(self, items=None):
        """One pass: the changed notifications and new boosts, each thread's reader once, then inbox delivery."""
        t = self.t
        state = t.load("notifications.json", {})
        if t.agent() is None:
            if not state.get("refused") and not t.dry:
                t.log("notifications: the acting user is the owner, unset or unknown; notifications not read")
                t.save_json("notifications.json", dict(state, refused=True))
            return
        state.pop("refused", None)
        prev, now = state.get("read_at"), now_iso()  # taken before the read, so nothing new can predate it
        try:
            data = t.readings()
        except RuntimeError as e:
            t.log(f"notifications: {e}")
            return
        received = t.received()  # the same read the boost readers confirm boosts against this run
        boosts = None if received is None else list(received.values())
        unread = {str(r.get("id")) for r in data.get("unreads") or []}
        readings = sorted((data.get("unreads") or []) + (data.get("reads") or []), key=stamp)
        first, first_boosts = "items" not in state, "boosts" not in state  # boosts unread on the first run seed later
        cursors, seen = state.get("items") or {}, set(state.get("boosts") or [])
        self.watched = t.watches() if self.others["chat-watch"].on else {}
        self.held = {c: r for c, r in t.watches(held=True).items() if str(r.get("bucket") or t.project) in t.watch_held}
        want = {"cards": set(), "chats": {}, "todos": set(), "requests": {}, "checkins": False, "answers": False,
                "messages": set(), "pings": {}, "mentions": {}, "threads": {}}
        work, old = [], set()  # work: (ref, mine, [group], check), one per changed notification or new boost
        for r in readings:
            rid = str(r.get("id"))
            if first or cursors.get(rid) == stamp(r):
                continue
            if rid not in cursors and prev and history(stamp(r), prev, 300):
                old.add(rid)  # unread before the last read yet not in it (it fell out of a read and came back): seed it
                continue
            work.append((rid, *self.classify(r, want, data)))
        for b in boosts or []:
            if b.get("id") not in seen and not first_boosts:
                work.append((("boost", b.get("id")), *self.classify_boost(b, want, data)))
        failed = self.read(want, {g for w in work for g in w[2]})
        done = []
        for ref, mine, groups, check in work:
            if any(g in failed for g in groups):
                continue
            if check is not None:
                before = t.errors
                check()
                if t.errors > before:
                    failed.add(f"check {ref}")
                    continue
            done.append((ref, mine))
        if failed:
            t.log(f"notifications: retrying next run: {', '.join(sorted(map(str, failed)))}")
        if t.dry:
            return
        handled = {ref for ref, _ in done} | old
        # A new item whose reader failed keeps an empty cursor, so it is retried, never seeded as old.
        state["items"] = {str(r.get("id")): stamp(r) if first or str(r.get("id")) in handled else cursors.get(str(r.get("id")), "")
                          for r in readings}
        state["read_at"] = now
        if boosts is not None:
            listed = {b.get("id") for b in boosts}
            took = listed if first_boosts else seen | {ref[1] for ref in handled if isinstance(ref, tuple)}
            state["boosts"] = sorted(took & listed)
        mark = sorted({ref for ref, mine in done if mine and ref in unread} | (set(state.get("unmarked") or []) & unread))
        state["unmarked"] = []
        t.save_json("notifications.json", state)
        if self.mark and mark:
            try:
                t.mark_read(mark)
            except RuntimeError as e:
                t.log(f"notifications: marking {len(mark)} read failed, retrying next run: {e}")
                state["unmarked"] = mark
                t.save_json("notifications.json", state)
        if old:
            t.log(f"notifications: {len(old)} notification(s) from before the last read seeded, not replayed: {', '.join(sorted(old))}")
        if first:
            t.log(f"notifications: started; {len(readings)} notification(s) and {len(boosts or [])} boost(s) seeded, none replayed")
        if done and self.others["inbox-delivery"].on:
            self.others["inbox-delivery"].deliver()

    def read(self, want, groups):
        """Run each reader `want` names once, in order; returns the groups whose reader had a failed call."""
        b, t, failed = self.others, self.t, set()

        def step(group, call):
            if group not in groups:
                return
            before = t.errors
            call()
            if t.errors > before:
                failed.add(group)
        step("cards", lambda: b["card-mirror"].relay(want["cards"]))
        every_line = b["chat-inbox"].every_line | set(self.watched)
        for chat, addressed in sorted(want["chats"].items()):
            step(f"chat {chat}", lambda c=chat, a=addressed: t.read_chats([c], every_line, a))
        step("todos", lambda: t.read_todo_comments(want["todos"]))
        # After the comment readers, so a comment made just before the closing is relayed first.
        for tid, req in sorted(want["requests"].items()):
            step(f"request {tid}", lambda r={tid: req}: self.read_requests(r))
        step("checkins", lambda: t.read_checkins(b["checkin-answering"].checkins["questionnaires"]))
        step("answers", lambda: t.read_answer_boosts(b["checkin-answering"].checkins["questionnaires"]))
        for mid in sorted(want["messages"]):
            step(f"message {mid}", lambda m=mid: t.read_messages(b["reports"].board, only=m))
        for chat, conv in sorted(want["pings"].items()):
            step(f"ping {chat}", lambda c=conv: t.read_pings(convs=[c]))
        for rid, (bucket, chat) in sorted(want["mentions"].items()):
            step(f"mention {rid}", lambda r=rid, bk=bucket, c=chat: t.read_mention(bk, c, r))
        for thread, (bucket, since, mentions, ptype, title, after) in sorted(want["threads"].items()):
            step(f"thread {thread}", lambda th=thread, bk=bucket, sn=since, m=mentions, pt=ptype, ti=title, af=after:
                 t.read_thread(bk, th, sn, m, pt, ti, af))
        return failed

    def read_requests(self, requests):
        """For each to-do in `requests` ({to-do id: (what happened, who, bucket)}): an open request's edit and closing
        check, else a new request when it was assigned. Returns what ran, for the log."""
        t, ran = self.t, []
        for tid, (kind, by, bucket) in sorted(requests.items()):
            key = f"request-{tid}"
            if key in t.request_keys():
                done = t.refresh_todo_request(key, by)
            elif kind == "todo.assignment_changed":
                done = "todo-request" if t.read_todo_request(tid, by, bucket) else None
            elif kind == "card":  # any event on a card: a request when it is now assigned to the agent
                done = "card-request" if t.read_todo_request(tid, by, bucket, card=True) else None
            else:
                done = None
            ran.append(f"request {tid}" + (f" -> {done}" if done else ""))
        return ran

    def classify(self, r, want, data):
        """One changed notification: (this home's?, the reader groups it needs, an unmonitored check to run after them)."""
        b, t = self.others, self.t
        kind, where = r.get("type"), parse_reading(r)
        bucket, thread, anchor, path = where["bucket"], where["thread"], where["anchor"], where["path"]
        who = r.get("creator") or {}
        if kind in self.QUIET:
            return False, [], None
        if kind == "Reminder":  # a check-in question asked of the agent: a faster due signal than the schedule
            if b["checkin-answering"].on:
                want["checkins"] = True
                return True, ["checkins"], None
            return False, [], None
        if r.get("section") == "pings":
            conv = next((c for c in t.ping_conversations(data) if str(c["chat"]) == str(thread)), None)
            if not b["pings"].on or conv is None:
                return False, [], None
            want["pings"][str(thread)] = conv
            return True, [f"ping {thread}"], None
        here, todos = bucket == t.project, t.load("todos.json", {})
        cards = b["assigned-cards"]
        if path == "cards" and kind not in ("Comment", "Mention") and any(
                rec.get("todo") == thread and rec.get("type") == "card" and not rec.get("request")
                and str(rec.get("bucket") or t.project) == bucket for rec in todos.values()):
            return True, [], None  # a decision card's own event (moved, completed): nothing to relay
        if path == "cards" and kind not in ("Comment", "Mention") and (
                f"request-{thread}" in t.request_keys(cards=True)
                or cards.on and (here or cards.account_wide) and not t.mirrored_card(thread, bucket)):
            # An event on a card (assigned, completed, moved, edited): a new request, or its request's edit or closing;
            # one that is neither stays unmonitored, as before.
            want["requests"].setdefault(thread, ("card", who.get("id"), None if here else bucket))
            check = self.check(kind, None, thread, r) if here and t.hears(who, admitted=False) else None
            return True, [f"request {thread}"], check and (
                lambda: None if f"request-{thread}" in t.load("todos.json", {}) else check())
        if kind in ("Assignment", "Completion"):  # thread and anchor are the to-do
            what = "todo.assignment_changed" if kind == "Assignment" else "todo.completed"
            tracked = f"request-{thread}" in t.request_keys()
            if tracked or b["assigned-todos"].on and (here or b["assigned-todos"].account_wide):
                want["requests"].setdefault(thread, (what, who.get("id"), None if here else bucket))
                return True, [f"request {thread}"], None
            if not here:  # another project's: this home's only when it is a decision to-do it put there
                return any(rec.get("todo") == thread and str(rec.get("bucket")) == bucket for rec in todos.values()), [], None
            if any(rec.get("todo") == thread for rec in todos.values()) or not t.hears(who, admitted=False):
                return True, [], None  # a decision to-do's own closing, or not from someone listened to
            return True, [], self.check(what, "Todo", thread, r)
        if (r.get("section") == "chats" or path == "chats") and self.watching(bucket, thread, self.held):
            return False, [], None  # a watched chat skipped this run (`watch_held`): the home that reads its project relays it
        if not here:
            keys = {k for k, rec in todos.items() if rec.get("todo") == thread and path in ("todos", "cards")
                    and str(rec.get("bucket")) == bucket and not rec.get("completed") and not rec.get("closed")
                    and self.reads(rec)}
            if keys and kind in ("Comment", "Mention"):
                want["todos"] |= keys
                return True, ["todos"], None
            if (r.get("section") == "chats" or path == "chats") and self.watching(bucket, thread):
                # A chat watched there (`sync.py watch`): its reader, as for a chat in "chats". A Campfire reply to the
                # agent's line may come as a Mention (its line is addressed) or as a Chat notification; both read it.
                want["chats"].setdefault(str(thread), set()).update([anchor] if kind == "Mention" and anchor else [])
                return True, [f"chat {thread}"], None
            if not self.anywhere or kind not in ("Comment", "Mention"):
                return False, [], None
            # "mentions": {"scope": "account"}: an @mention of the agent, or a comment on a thread it follows, read as in
            # the project; a chat line there that does not mention the agent stays another home's.
            if r.get("section") == "chats" or path == "chats":
                if kind != "Mention":
                    return False, [], None
                want["mentions"][anchor] = (bucket, thread)
                return True, [f"mention {anchor}"], None
            self.thread(want, bucket, thread, anchor, kind == "Mention", THREADS.get(path), topic(r), stamp(r))
            return True, [f"thread {thread}"], None
        mention = kind == "Mention"
        if r.get("section") == "chats" or path == "chats":
            ci = b["chat-inbox"]
            if ci.on and str(thread) in ci.chats or self.watching(bucket, thread):
                want["chats"].setdefault(str(thread), set()).update([anchor] if mention else [])
                return True, [f"chat {thread}"], None
            if mention and (t.hears(who) or t.blocked_reply is not None):  # anyone else's mention: the automatic reply
                want["mentions"][anchor] = (bucket, thread)
                return True, [f"mention {anchor}"], None
            return True, [], self.check("chat.line.created", "Chat::Lines", thread, r) if t.hears(who, admitted=False) else None
        if kind not in ("Comment", "Mention"):  # a kind Basecamp adds later: unmonitored when an owner did it
            return True, [], self.check(f"{kind}", None, thread, r) if t.hears(who, admitted=False) else None
        if path == "cards" and b["card-mirror"].on:
            keys = {k for k, rec in t.load("map.json", {}).items() if rec.get("card") == thread}
            if keys:
                want["cards"] |= keys
                return True, ["cards"], None
        if path in ("todos", "cards"):
            mine = {k: rec for k, rec in todos.items() if rec.get("todo") == thread}
            keys = {k for k, rec in mine.items() if not rec.get("completed") and not rec.get("closed") and self.reads(rec)}
            if keys:
                want["todos"] |= keys
                return True, ["todos"], None
        # Any other thread the agent is subscribed to or mentioned in: its own reader, whatever the backlog says.
        if path == "messages" and b["reports"].on:
            want["messages"].add(thread)

            def after():  # the agent's own message was read above; anyone else's is a thread like any other
                if not self.agent_message(thread):
                    t.read_thread(bucket, thread, anchor, {anchor} if mention else (), "Message", topic(r), stamp(r))
            return True, [f"message {thread}"], after
        self.thread(want, bucket, thread, anchor, mention, THREADS.get(path), topic(r), stamp(r))
        return True, [f"thread {thread}"], None

    def watching(self, bucket, chat, watched=None):
        """True when chat `chat` in `bucket` is watched this run (`sync.py watch`), or is in `watched` when given."""
        rec = (self.watched if watched is None else watched).get(str(chat))
        return rec is not None and str(rec.get("bucket") or self.t.project) == str(bucket)

    def reads(self, rec):
        """True when the behavior that owns tracked to-do `rec` is on: a card request's, a to-do request's, or (a decision
        to-do or decision card) decision-todos'."""
        return self.others["assigned-cards" if rec.get("request") and rec.get("type") == "card" else
                           "assigned-todos" if rec.get("request") else "decision-todos"].on

    def thread(self, want, bucket, thread, anchor, mention, ptype, title, after=None):
        """Add thread `thread` to the threads to read: from its first unread comment `anchor`, `anchor` a mention or not,
        or, without one, from ISO time `after` (when the notification became unread)."""
        bk, since, mentions, pt, ti, af = want["threads"].get(thread, (bucket, anchor, set(), ptype, title, after))
        want["threads"][thread] = (bk, min(x for x in (since, anchor) if x) if since or anchor else None,
                                   mentions | ({anchor} if mention else set()), pt, ti,
                                   min(x for x in (af, after) if x) if af or after else None)

    def classify_boost(self, bst, want, data):
        """One new boost on the agent's own recording: (this home's?, the reader groups it needs, an unmonitored check)."""
        b, t = self.others, self.t
        rec, booster = bst.get("recording") or {}, bst.get("booster") or {}
        rid, rtype = rec.get("id"), recording_type(rec.get("type"))
        bucket, parent = str((rec.get("bucket") or {}).get("id") or ""), rec.get("parent") or {}
        if not t.hears(booster, admitted=False, bucket=bucket or None):
            return False, [], None
        on_todo = rid if rtype == "Todo" else parent.get("id") if rtype == "Comment" and parent.get("type") == "Todo" else None
        on_card = (rid if rtype == "Kanban::Card" else
                   parent.get("id") if rtype == "Comment" and parent.get("type") == "Kanban::Card" else None)
        todos = t.load("todos.json", {})
        keys = {k for k, r in todos.items() if (on_todo or on_card) is not None and r.get("todo") == (on_todo or on_card)
                and (on_todo is not None or r.get("type") == "card")
                and str(r.get("bucket") or t.project) == bucket}
        if rtype == "Chat::Lines" and self.watching(bucket, parent.get("id")):
            want["chats"].setdefault(str(parent.get("id")), set())
            return True, [f"chat {parent.get('id')}"], None
        if rtype == "Chat::Lines" and bucket != t.project:
            convs = {str(c["chat"]): c for c in t.ping_conversations(data)}
            circles = {str(c["bucket"]) for c in convs.values()} | {
                str(p.get("bucket")) for p in t.load("pings.json", {}).get("pings", {}).values()}
            if not b["pings"].on or bucket not in circles:
                return False, [], None  # a line in another project's chat is another home's
            chat = str(parent.get("id"))
            want["pings"].setdefault(chat, convs.get(chat) or {"bucket": bucket, "chat": chat, "url": rec.get("app_url"),
                                                               "title": (rec.get("bucket") or {}).get("name")})
            return True, [f"ping {chat}"], None
        if keys:
            open_keys = {k for k in keys if not todos[k].get("completed") and not todos[k].get("closed")}
            want["todos"] |= open_keys
            return True, ["todos"] if open_keys else [], None
        known = f"{bucket}:{parent.get('id')}" in t.load("threads.json", {}) if rtype == "Comment" else False
        if bucket != t.project and not (known and self.anywhere):
            return False, [], None  # another project's, unless a thread followed there with "mentions" account-wide
        if bucket != t.project:
            self.thread(want, bucket, parent.get("id"), None, False, parent.get("type"), parent.get("title"))
            return True, [f"thread {parent.get('id')}"], None
        if rtype == "Chat::Lines" and b["chat-inbox"].on and str(parent.get("id")) in b["chat-inbox"].chats:
            want["chats"].setdefault(str(parent.get("id")), set())
            return True, [f"chat {parent.get('id')}"], None
        card = rid if rtype == "Kanban::Card" else parent.get("id") if parent.get("type") == "Kanban::Card" else None
        cards = {k for k, r in t.load("map.json", {}).items() if card is not None and r.get("card") == card}
        if cards and b["card-mirror"].on:
            want["cards"] |= cards
            return True, ["cards"], None
        if rtype == "Question::Answer" and b["checkin-answering"].on:
            want["answers"] = True
            return True, ["answers"], None
        msg = rid if rtype == "Message" else parent.get("id") if parent.get("type") == "Message" else None
        if msg is not None and b["reports"].on:
            want["messages"].add(msg)
            return True, [f"message {msg}"], None
        if known:
            self.thread(want, bucket, parent.get("id"), None, False, parent.get("type"), parent.get("title"))
            return True, [f"thread {parent.get('id')}"], None
        if on_todo is not None and b["assigned-todos"].on:
            return True, [], None  # a to-do the agent made, assigned to anyone else
        what = rtype if rtype != "Comment" else f"Comment on {parent.get('type') or 'unknown'}"
        return True, [], self.check("boost.created", what or "unknown", rid, None, booster=booster, at=bst.get("created_at"),
                                    ref=bst.get("id"), on=parent if rtype == "Comment" else None, rec=rec)

    def check(self, what, rtype, rid, r, booster=None, at=None, ref=None, on=None, rec=None):
        """The unmonitored check for notification `r` (or a boost): a call that records it once per kind, or None when off.

        A notification's own title, excerpt and link stand in for the recording, which is
        refetched only when its type is unknown (rtype None).
        """
        if not self.unmonitored:
            return None
        t = self.t
        if r is not None:
            who, at, ref, bucket = r.get("creator"), r.get("unread_at") or r.get("created_at"), r.get("id"), parse_reading(r)["bucket"]
            rec = None if rtype is None else {"title": r.get("title"), "content": r.get("content_excerpt"), "app_url": r.get("app_url")}
        else:
            who, bucket = booster, None
        return lambda: t.record_unmonitored(what, rtype, rid, who, at=at, rec=rec, on=on, bucket=bucket, ref=ref)

    def agent_message(self, mid):
        """True when the reports reader knows message `mid` as one of the agent's."""
        msgs = self.t.load("messages.json", {})
        return str(mid) in msgs.get("posts", {}) or str(mid) in (msgs.get("messages") or {}).get("boost_counts", {})


def topic(r):
    """A notification's thread title: its title without the "Re: " a comment's carries."""
    return re.sub(r"^Re: ", "", r.get("title") or "") or None


def stamp(r):
    """A notification's cursor: when it last became unread (new activity), else when it changed."""
    return r.get("unread_at") or r.get("updated_at") or r.get("created_at") or ""


# The timer runs the "on" behaviors that are due in this order: blocked replies' clock, then notifications, so the
# owner's input is not held up by the card mirror; inbox delivery after every reader and the timesheet (whose reports
# it delivers the same run), then the session alert, which watches what it delivered.
ALL = (BlockedReplies, Notifications, CardMirror, ChatInbox, ChatWatch, ChatAsks, ReleaseAnnouncements, SecurityAdvisories, CheckinAnswering, DecisionTodos,
       AssignedTodos, AssignedCards, Reports, Pings, Timesheet, InboxDelivery, SessionAlert, Backup)
# The readers notifications take over: with notifications on they run as the hourly repair sweep.
# assigned-todos keeps its 5-minute sweep: a request's edits and the owner's boosts on it reach no notification.
SWEPT = ("chat-inbox", "chat-watch", "decision-todos", "reports", "pings")


WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def scheduled(rec):
    """A to-do request's schedule for its note: ", due <date>" and ", repeating <rule>", or ""."""
    out = f", due {rec['due_on']}" if rec.get("due_on") else ""
    rule = rec.get("repeats")
    if rule:
        days = [WEEKDAYS[d] for d in rule.get("days") or [] if isinstance(d, int) and 0 <= d < 7]
        out += f", repeating {str(rule.get('frequency')).replace('_', ' ')}" + (f" on {', '.join(days)}" if days else "")
        out += f" until {rule['end_date']}" if rule.get("end_date") else ""
    return out


def timing(rec):
    """A scheduled request's `when` for its note, as a fact: the answer known for its list, or that none is yet."""
    which = "repeating" if rec.get("repeats") else "dated"
    lst = rec.get("list")
    remember = f"sync.py schedule set --list {lst} --{'recurring' if rec.get('repeats') else 'dated'} due|before"
    return {"ask": f"no one has said yet whether this list's {which} to-dos are done on the due date or before it "
                   f"({remember} remembers the answer)",
            "due": f"this list's {which} to-dos are done on their due date",
            "before": f"this list's {which} to-dos are done before their due date"}.get(rec["when"], f"when: {rec['when']}")


def inbox_note(rec, account, project):
    """A pending record as an inbox note: (request id, short plain body)."""
    kind = rec.get("kind") or "comment"
    # A card request (assigned-cards) is handled as a to-do request is; only what it is called differs.
    noun = "card" if kind.startswith("card-request") or rec.get("type") == "card" else "to-do"
    step = "todo-" + kind[5:] if kind.startswith("card-request") else kind
    card_url = f"https://app.basecamp.com/{account}/buckets/{project}/card_tables/cards/{rec.get('card')}"
    text = " ".join(str(rec.get("text") or "").split())
    if len(text) > 600:
        text = text[:600] + "..."
    # A record without "captain" predates the people list, when only the captain was relayed; one without "role" predates
    # operators, when only the captain's word counted. `captain` below means the word authorizes: the captain's or an operator's.
    role = rec.get("role", "captain" if rec.get("captain", True) else None)
    captain = role in AUTHORITY
    author = rec.get("author") or {}
    name = author.get("name") or (f"person {author['id']}" if author.get("id") else "someone")
    who = ("the captain" if role == "captain" else f"{name} (an operator: their word counts as the captain's)"
           if role == "operator" else f"{name} (not the captain)")
    # A record from another project of the account (a to-do request with assigned-todos "scope": "account", or a mention
    # or followed thread with "mentions" "scope": "account") names it, for routing.
    proj = rec.get("project") or {}
    elsewhere = (f" in project {proj.get('name') or proj.get('id')!r} ({proj.get('id')})"
                 if proj.get("id") and str(proj.get("id")) != str(project) else "")
    # The note says what happened, the facts that matter (who, whose word authorizes, dates Basecamp owns) and the
    # commands that act on it. How to handle it is the home's defaults (data/basecamp-sync/defaults.md), never the note.
    facts, cmds, key = [], [], rec.get("key")
    if kind in ("comment", "question"):
        rid = rec.get("comment")
        what = f"Basecamp card {kind} from {who} on task {rec.get('task')}"
        cmds, url = [f"sync.py reply --recording {rid}"], card_url
    elif kind == "approval":
        rid = rec.get("boost")
        mark = f"the 👍 of {who}" if role == "operator" else "the captain's 👍"
        what = f"Basecamp card approval ({mark}) on task {rec.get('task')}"
        text = text or "approve every recommendation on the card as recommended"
        facts, url = ["a 👍 on an assigned card approves every recommendation on it"], rec.get("url") or card_url
    elif kind == "chat-question":
        rid = rec.get("line")
        what, cmds, url = f"Basecamp chat line from {who}{elsewhere}", [f"sync.py reply --recording {rid} (answers in the chat)"], rec.get("url")
    elif kind == "ping":
        rid = rec.get("line")
        what = f"Basecamp Ping (a direct message) from {who}" + (f" in {rec.get('title')!r}" if rec.get("title") else "")
        cmds, url = [f"sync.py reply --recording {rid} (answers in the Ping)"], rec.get("url")
    elif kind == "message-comment":
        rid = rec.get("comment")
        what = f"Basecamp comment from {who} on your message {rec.get('subject')!r}"
        cmds, url = [f"sync.py reply --recording {rid}"], rec.get("url")
    elif kind == "todo-comment" and rec.get("request"):
        rid = rec.get("comment")
        what = f"Basecamp comment from {who} on {noun} request {rec.get('title')!r} ({key}){elsewhere}"
        facts = ["part of the request"]
        cmds, url = [f"sync.py reply --recording {rid}", f"sync.py todo complete --todo {key}"], rec.get("url")
    elif kind == "todo-comment":
        rid = rec.get("comment")
        what = (f"Basecamp comment from {who} on {rec['on']!r}, where you put a decision step ({key})" if rec.get("on")
                else f"Basecamp comment from {who} on decision to-do {key}")
        facts = [] if captain else ["their word is input on the captain's decision, never the decision itself"]
        cmds = [f"sync.py reply --recording {rid}"] + ([f"sync.py todo complete --todo {key}"] if captain else [])
        url = rec.get("url")
    elif kind == "boost":
        rid = rec.get("boost")
        todo = (f"{noun} request {rec.get('title')!r} ({key}){elsewhere}" if rec.get("request")
                else f"decision to-do {key}")
        where = {"todo": todo, "todo-comment": f"a comment on {todo}",
                 "subtask": f"step {rec.get('step')} ({rec.get('question')!r}) of {todo}",
                 "card": f"the card for task {rec.get('task')}",
                 "card-comment": f"a comment on the card for task {rec.get('task')}", "chat": "a chat line",
                 "ping": "a line in a Ping",
                 "checkin-answer": f"your check-in answer to question {rec.get('question')}",
                 "message": f"your message {rec.get('subject')!r}",
                 "message-comment": f"a comment on your message {rec.get('subject')!r}",
                 "thread-comment": f"a comment on {rec.get('title')!r}" if rec.get("title") else "a comment"}.get(rec.get("surface"), rec.get("surface"))
        what = f"Basecamp boost from {who} on {where} (recording {rec.get('recording')})"
        facts = ["a boost answers what was boosted, like a comment there"] + (
            ["part of the request"] if rec.get("request") and rec.get("surface") in ("todo", "todo-comment") else []) + (
            ["it is on your reply that awaited an answer (reply --awaiting): it is that answer, and the thread no "
             "longer awaits one"] if rec.get("why") == "awaiting" else [])
        if rec.get("surface") == "subtask":
            facts.append("a boost on a decision step is the answer to that decision")
            cmds = [f"sync.py todo comment --todo {key}"] + (
                [f"sync.py todo subtask --todo {key} --number {rec.get('step')}"] if captain else [])
        elif rec.get("surface") in ("todo", "todo-comment"):
            cmds = [f"sync.py todo comment --todo {key}"] + ([f"sync.py todo complete --todo {key}"] if captain else [])
        url = rec.get("url")
    elif step == "todo-request":
        rid = f"{rec.get('todo')}-{rec.get('n', 1)}"
        assigned = ("the next occurrence of a repeating to-do, asked for" if rec.get("follows") else
                    "assigned to you again" if rec.get("reopened") else "assigned to you")
        what = (f"Basecamp {noun} {assigned} by {who}, a request: {rec.get('title')!r} ({key}){elsewhere}"
                + scheduled(rec))
        facts = ["from the captain or an operator, so it authorizes: captain work" if captain else
                 "a request from someone who is not the captain: their word never authorizes a decision or approval"]
        if rec.get("due_on"):
            facts.append(f"due {rec['due_on']} as read now; Basecamp owns the date and may move it (sync.py todo show "
                         f"--todo {key} reads it as it is now)")
        if rec.get("repeats"):
            facts.append("it repeats on Basecamp's schedule: once this one is completed, Basecamp makes the next "
                         "occurrence, which arrives as a new request")
        if rec.get("when"):
            facts.append(timing(rec))
        if noun == "card":
            facts.append("completing it moves the card to its table's Done column")
        if elsewhere:
            facts.append("it is in another project; comments and completion go there")
        cmds = [f"sync.py todo comment --todo {key}", f"sync.py todo complete --todo {key}"]
        url = rec.get("url")
    elif step in ("todo-request-update", "todo-request-closed"):
        # A move of only the due date or repetition is an update of its own, so the schedule is in its id when recorded.
        schedule = json.dumps([rec.get("due_on"), rec.get("repeats")], sort_keys=True) if "due_on" in rec else ""
        rid = f"{rec.get('todo')}-{rec.get('n', 1)}-" + (rec.get("reason") or hashlib.sha256(
            str(rec.get("title")).encode() + str(rec.get("text")).encode() + schedule.encode()).hexdigest()[:12])
        by = f" by {who}" if rec.get("author") else ""
        if step == "todo-request-update":
            what = (f"Basecamp {noun} request {rec.get('title')!r} ({key}){elsewhere} edited{by}"
                    + (scheduled(rec) or (", now undated" if "due_on" in rec else "")) + "; it now reads")
            text = text or "(no description)"
            facts = ["the request changed"]
            if rec.get("due_on") or rec.get("repeats"):
                facts.append("its due date and repetition are Basecamp's and may move again")
            if rec.get("when"):
                facts.append(timing(rec))
            cmds = [f"sync.py todo show --todo {key}"]
        else:
            reason = rec.get("reason")
            what = (f"Basecamp {noun} request {rec.get('title')!r} ({key}){elsewhere} "
                    + {"completed": f"completed{by}", "unassigned": f"no longer assigned to you{by}"}.get(reason, f"{reason}{by}"))
            facts = ["the request is closed and no longer tracked; there is nothing to complete"]
            if reason == "completed" and rec.get("repeats"):
                facts.append("it repeats, so Basecamp makes the next occurrence, which arrives as a new request")
        url = rec.get("url")
    elif kind in ("mention", "thread-comment"):
        rid = rec.get("comment")
        what = (f"Basecamp {'@mention of you' if kind == 'mention' else 'comment'} from {who} on "
                f"{rec.get('parent_type') or 'a recording'}" + (f" {rec.get('title')!r}" if rec.get("title") else "") + elsewhere)
        facts = ["addressed to you" if kind == "mention" else
                 "the first comment there after your reply that awaited an answer (reply --awaiting); "
                 "the thread no longer awaits one" if rec.get("why") == "awaiting" else
                 "on something you made" if rec.get("why") == "yours" else "on something you follow"]
        cmds, url = [f"sync.py reply --recording {rid} (answers there)"], rec.get("url")
    elif kind == "decision-step-done":
        rid = f"{rec.get('todo')}-{rec.get('step')}"
        what = (f"Basecamp decision step {rec.get('question')!r} on {rec.get('title')!r} ({key}) checked by {who}")
        facts = ["the step was the decision put to them; their comment there, if any, says what they decided"]
        cmds, url = [f"sync.py todo comment --todo {key}"], rec.get("url")
    elif kind == "unmonitored":
        rid = f"{key}-{rec.get('notification') or rec.get('event')}"
        what = (f"Basecamp activity from {who} that nothing monitors: {rec.get('event_type')} on "
                f"{rec.get('recording_type')}" + (f" {rec.get('title')!r}" if rec.get("title") else ""))
        facts = [f"one note per kind of thing ({key}), not per event"]
        cmds = [f"sync.py unmonitored handle --key '{key}' --decision <what to do about it> (keeps this kind quiet)",
                f"sync.py unmonitored forget --key '{key}' (asks again next time)"]
        url = rec.get("url")
    elif kind == "checkin":
        rid = f"{rec.get('question')}-{rec.get('date')}"
        what = f"Basecamp check-in due {rec.get('date')}" + ("" if captain else f", asked by {who}")
        text = text or str(rec.get("title") or "")
        facts = ["one answer a day; a second is refused"]
        cmds, url = [f"sync.py answer --question {rec.get('question')}"], rec.get("url")
    elif kind in ("advisory", "advisory-update"):
        rid = f"{rec.get('repo')}-{rec.get('ghsa')}" + ("" if kind == "advisory" else f"-{rec.get('state')}")
        sev = f", severity {rec['severity']}" if rec.get("severity") else ", no severity set"
        what = (f"GitHub security advisory {rec.get('ghsa')} on {rec.get('repo')} "
                + (f"in {rec.get('state')}{sev}, reported by {rec.get('reporter') or 'unknown'} on {rec.get('created_at_local') or rec.get('created_at')}"
                   if kind == "advisory" else f"moved from {rec.get('previous')} to {rec.get('state')}"))
        text = text or str(rec.get("summary") or "")
        facts = (["security-sensitive: its details are private until it is published"] if kind == "advisory" else [])
        url = rec.get("url")
    elif kind == "dependabot-alert":
        rid = f"{rec.get('repo')}-{rec.get('number')}" + ("-reopened" if rec.get("reopened") else "")
        what = (f"GitHub Dependabot alert {rec.get('number')} on {rec.get('repo')}"
                + (" reopened" if rec.get("reopened") else "")
                + f", severity {rec.get('severity') or 'unknown'}: {rec.get('package') or 'a dependency'}"
                + (f" ({rec.get('manifest')})" if rec.get("manifest") else "")
                + (f", fixed in {rec['fixed_in']}" if rec.get("fixed_in") else ", no fixed version yet"))
        text = text or str(rec.get("summary") or "")
        url = rec.get("url")
    elif kind == "dependabot-baseline":
        rid = rec.get("repo")
        sevs = ", ".join(f"{n} {s}" for s, n in sorted((rec.get("by_severity") or {}).items()))
        what = (f"GitHub Dependabot alerts on {rec.get('repo')} now watched: {rec.get('open')} already open"
                + (f" ({sevs})" if sevs else "") + "; only new or reopened alerts are reported one by one from here")
        url = rec.get("url")
    elif kind == "dependabot-unreadable":
        rid = rec.get("repo")
        status = rec.get("status") or ""
        what = (f"GitHub Dependabot alerts on {rec.get('repo')} are not readable from this login "
                f"({'HTTP ' + status if status.isdigit() else status}); "
                "reported once")
        facts = ["Dependabot alerts may be off for the repository, or the login needs admin or security-manager access"]
        url = rec.get("url")
    elif kind == "advisory-unreadable":
        rid = rec.get("repo")
        what = (f"GitHub security advisories on {rec.get('repo')} cannot be read by this login (HTTP {rec.get('status')}); "
                "reported once")
        facts = ["the login needs admin or security-manager access to the repository, or the repo should leave the "
                 "advisories config"]
        url = rec.get("url")
    elif kind == "timesheet-unmapped":
        rid = f"{key}-{rec.get('at')}"
        what = (f"Timesheet: your active time on {key} ({rec.get('hours'):g} h on "
                f"{', '.join(sorted(rec.get('days') or {}))}) is not posted: no to-do or card is named for it")
        facts = ["reported once; its days post once it has a to-do or card"]
        cmds = [f"sync.py timesheet start --key {key} --on <to-do or card URL> --workdir <its workdir>"]
        url = None
    elif kind == "timesheet-refused":
        rid = f"{rec.get('bucket')}-{rec.get('at')}"
        what = (f"Timesheet: project {rec.get('bucket')} refused your {rec.get('hours'):g} h on {rec.get('date')} for "
                f"{key} ({rec.get('error')})")
        facts = ["timesheet tracking may be off in that project, or your login may not log time there",
                 "reported once until an entry posts there again; the project is tried again once a day"]
        url = rec.get("url")
    elif kind == "timesheet-missing":
        rid = f"{key}-{rec.get('at')}"
        what = (f"Timesheet: the to-do or card for {key} was not found for your {rec.get('hours'):g} h on "
                f"{rec.get('date')} ({rec.get('error')})")
        facts = ["it may have been deleted, or your login may not see it",
                 "reported once until an entry posts there again; the key is tried again once a day"]
        cmds = [f"sync.py timesheet start --key {key} --on <to-do or card URL> --workdir <its workdir>"]
        url = rec.get("url")
    else:
        rid = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:16]
        what, url = f"Basecamp {kind} record", rec.get("url")
        facts = ["the full record is in pending-comments.jsonl"]
    if not captain and author and kind not in ("todo-request", "card-request") and not (
            kind == "todo-comment" and not rec.get("request")):
        facts.append("they are not the captain: their word never authorizes a decision or approval")
    request_id = re.sub(r"[^A-Za-z0-9._:-]", "-", f"basecamp-{kind}-{rid}")[:128]
    files = "; ".join(f"{a.get('filename') or 'file'} ({a.get('url')})" if a.get("url") else str(a.get("filename") or "file")
                      for a in rec.get("attachments") or [])
    lines = [what + ":", text, ("Attachments: " + files) if files else "", url or "", ("Facts: " + "; ".join(facts) + ".") if facts else "",
             ("Commands: " + "; ".join(cmds) + ".") if cmds else "",
             "Handle it as this home's Basecamp defaults say (data/basecamp-sync/defaults.md), then ack this note with "
             "fm-inbox.sh drain --ack <note id>."]
    return request_id, "\n".join(x for x in lines if x)


def configure(t, cfg, prereleases=False):
    """Every behavior, configured from `cfg` (on or off); a malformed config raises ValueError."""
    out = {b.name: (b(t, cfg, prereleases=prereleases) if b is ReleaseAnnouncements else b(t, cfg)) for b in ALL}
    out["notifications"].others = out
    for name in SWEPT:
        out[name].every = SWEEP if out["notifications"].on else SLOW
    return out


def render_decision(d):
    """A decision card body from decisions.json: a plain question, then a numbered list."""
    out = f"<div><strong>Waiting on you</strong>: {html.escape(d['question'])}</div>"
    if d.get("items"):
        out += "<ol>" + "".join(f"<li>{html.escape(i)}</li>" for i in d["items"]) + "</ol>"
    if d.get("note"):
        out += f"<div>{html.escape(d['note'])}</div>"
    return out


def inline_md(text):
    """One line of Markdown to escaped HTML: links, bare URLs, **bold**, `code` as plain text."""
    out, pos = [], 0
    for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s<>()]+)", text):
        out.append(html.escape(text[pos:m.start()]))
        url = m.group(2) or m.group(3)
        out.append(f'<a href="{html.escape(url)}">{html.escape(m.group(1) or url)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    s = "".join(out)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`([^`]+)`", r"\1", s)


def render_release(notes, url, note=None):
    """Release notes (GitHub Markdown) to Basecamp rich text: headings bold, lists, a link to the release."""
    parts, items = [], []

    def flush():
        if items:
            parts.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()
    for line in notes.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        m = re.match(r"[*+-]\s+(.*)", line)
        if m:
            items.append(inline_md(m.group(1)))
            continue
        flush()
        if not line:
            continue
        h = re.match(r"#{1,6}\s+(.*)", line)
        parts.append(f"<div><strong>{inline_md(h.group(1))}</strong></div>" if h else f"<div>{inline_md(line)}</div>")
    flush()
    parts.append(f'<div><a href="{html.escape(url)}">{html.escape(url)}</a></div>')
    if note:
        parts.append(f"<div>{inline_md(note)}</div>")
    return "".join(parts)
