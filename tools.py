"""The tools layer: small, single-purpose Basecamp operations, with no policy.

A tool does one explicit thing to the configured account and project, through the
`basecamp` CLI (or `gh`, `tasks-axi`, `lavish-axi` for the local and GitHub reads):

  - readers poll Basecamp and append what they find to pending-comments.jsonl, once
    each, with a cursor or seen-list kept beside the config: card comments and
    approvals, chat lines, the owner's lines in Pings (direct messages to the acting
    login, each in a bucket of its own), due check-in questions, comments on tracked
    to-dos, to-dos the owner assigns to the acting login (as requests, then their edits
    and closing), the owner's comments and @mentions on any other thread the acting login is
    subscribed to or mentioned in;
    the notification and boost readers (`/my/readings.json`, `/my/boosts.json`) only
    hand what changed to a behavior, `mark_read` marks notifications read, and the
    unmonitored recorder records, once per kind, the owner input no behavior handles;
    a boost on the acting login's own recording is recorded only once this run's read of
    its received boosts lists it (`confirmed`); who is heard, and whose word authorizes,
    is `role` (the captain, "operators", "people", "project_people" in their project and
    admitted "participants"; `access` prints them), and
    anyone else who addresses the acting login is dropped, with one automatic reply
    when "blocked" is on (`block`);
  - commands post exactly what the agent hands them: `reply`, `ask`, `answer`,
    `todo create|track|comment|complete`, `post-message`; `unmonitored handle|forget`
    only edit local state;
  - primitives the behaviors compose: card create/update/move/assign/unassign, a
    Message Board post, acknowledgement boosts.

A tool never decides to act on what it reads. Which readers run on the timer, and
what the agent does with a pending record, is the behaviors layer (behaviors.py,
and prompts/base.md for the agent's side).

Every command that speaks as the agent (reply, ask, answer, the to-do commands and
post-message) posts nothing when no profile is set, or when the profile signs in as
the owner, and `--dry-run` only logs. All state lives in the directory holding the
config.
"""
import base64, contextlib, csv, fcntl, glob, hashlib, html, json, os, re, subprocess, time, tomllib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

THUMBS, EYES = "\U0001F44D", "\U0001F440"
AUTHORITY = ("captain", "operator")  # the roles whose word authorizes the agent: a decision or an approval
# When a scheduled request is done ("schedule" in the config, schedule.json per list): ask the owner, on its due date, or
# before it.
WHEN = ("ask", "due", "before")
# The automatic reply to someone the sync does not listen to who addresses the agent ("blocked": {} in the config).
BLOCKED_REPLY = "Sorry, I can't act on this: I only take requests from the people I work for. Please ask one of them to pass it on."


class BasecampError(RuntimeError):
    """A failed basecamp CLI call, keeping the CLI's error `code` and text."""

    def __init__(self, msg, code=None, error=None):
        super().__init__(msg)
        self.code, self.error = code, error or ""


class Tools:
    def __init__(self, home, config_path, dry=False, runner=subprocess.run):
        self.home = os.path.abspath(home)
        self.config_path = os.path.abspath(config_path)
        self.dir = os.path.dirname(self.config_path)
        self.held = set()
        # Projects of watched chats (`sync.py watch`) another live home on this login reads as its project, set by
        # Sync.claim each run: those chats are skipped, so that home reads them alone.
        self.watch_held = set()
        self.cfg = cfg = json.load(open(config_path))
        self.account, self.project = str(cfg["account"]), str(cfg["project"])
        self.captain = int(cfg["captain"])  # the owner: assignee, and the first person whose word is a decision
        # Operators: named people whose word authorizes the agent as the captain's does.
        operators = cfg.get("operators", [])
        if not isinstance(operators, list):
            raise ValueError('config "operators" must be a list of Basecamp person ids')
        self.operators = {int(p) for p in operators} - {self.captain}
        # The people whose lines, comments and boosts are relayed: "people", always with the captain and the operators.
        people = cfg.get("people", [])
        if not isinstance(people, list):
            raise ValueError('config "people" must be a list of Basecamp person ids')
        self.people = {self.captain, *self.operators, *(int(p) for p in people)}
        # "project_people": named participants heard only in one project of the account (its id -> person ids).
        per = cfg.get("project_people", {})
        if not isinstance(per, dict) or not all(isinstance(v, list) for v in per.values()):
            raise ValueError('config "project_people" must be {"<project id>": [<person id>, ...], ...}')
        self.project_people = {str(int(b)): {int(p) for p in ps} for b, ps in per.items()}
        # Participants admitted by rule: anyone on the project, with an email at a domain, or in a company. They may ask;
        # never decide.
        parts = cfg.get("participants")
        company = (parts or {}).get("company") if isinstance(parts, dict) else None
        if parts is not None and (not isinstance(parts, dict) or set(parts) - {"project", "domains", "company"}
                                  or not isinstance(parts.get("project", False), bool)
                                  or not isinstance(parts.get("domains", []), list)
                                  or not (company is None or company is True or isinstance(company, int)
                                          and not isinstance(company, bool))):
            raise ValueError('config "participants" must be {"project": true}, {"domains": ["example.com", ...]} and/or '
                             '{"company": true or <company id>}')
        parts = parts or {}
        self.participant_project = parts.get("project", False)
        self.participant_domains = {str(d).strip().lower().lstrip("@") for d in parts.get("domains", []) if str(d).strip()}
        self.participant_company = company  # True: the captain's company; an id: that company; None: no company rule
        self._members = None  # the project's non-client person ids, for "participants": {"project": true}
        self._companies = None  # person id -> company id, as last read (companies.json), for "participants": {"company"}
        self._company_read, self._unverified = set(), set()  # people read this run; people whose company is unknown
        # "blocked": anyone else who addresses the agent gets one short automatic reply; absent or false, none (as before).
        blocked = cfg.get("blocked")
        if blocked is not None and blocked is not False and not (
                isinstance(blocked, dict) and set(blocked) <= {"reply"} and isinstance(blocked.get("reply", ""), str)):
            raise ValueError('config "blocked" must be false, {} or {"reply": "<the automatic reply>"}')
        self.blocked_reply = (blocked.get("reply") or "").strip() or BLOCKED_REPLY if isinstance(blocked, dict) else None
        # "schedule": when a dated or repeating request is done by default; per list in schedule.json (`schedule_set`).
        schedule = cfg.get("schedule")
        if schedule is not None and not (isinstance(schedule, dict) and set(schedule) <= {"dated", "recurring"}
                                         and all(v in WHEN for v in schedule.values())):
            raise ValueError('config "schedule" must be {"dated": "ask"|"due"|"before", "recurring": "ask"|"due"|"before"}')
        self.schedule = schedule or {}
        # "acknowledge": the boost put on what is read ("read", every item), and on a deciding comment once the decision
        # is carried out ("decided"). Absent: today's 👀 on a question and 👍 on anything else, nothing after deciding.
        ack = cfg.get("acknowledge")
        if ack is not None and not (isinstance(ack, dict) and set(ack) <= {"read", "decided"}
                                    and all(isinstance(v, str) and v.strip() or v is False for v in ack.values())):
            raise ValueError('config "acknowledge" must be {"read": "<emoji>", "decided": "<emoji>" or false}')
        self.ack_read = ((ack or {}).get("read") or "").strip() or None
        self.ack_decided = ((ack or {}).get("decided") or "").strip() or None
        self.profile = cfg.get("profile")
        self.ask_chat = str(cfg["ask_chat"]) if cfg.get("ask_chat") is not None else None
        self.checkins = cfg.get("checkins")
        self.todos = cfg.get("todos")
        self.assigned_todos = cfg.get("assigned_todos") not in (None, False)
        # "account": to-dos assigned to the acting user in any project of the account are requests; else this project's.
        self.assigned_scope = (cfg.get("assigned_todos") or {}).get("scope", "project") \
            if isinstance(cfg.get("assigned_todos"), dict) else "project"
        # "assigned_cards": cards assigned to the acting user are requests too, read like to-do requests, with their own scope.
        self.assigned_cards = cfg.get("assigned_cards") not in (None, False)
        self.card_scope = (cfg.get("assigned_cards") or {}).get("scope", "project") \
            if isinstance(cfg.get("assigned_cards"), dict) else "project"
        self.message_board = str(cfg["message_board"]) if cfg.get("message_board") is not None else None
        # "timezone": the IANA zone times written for people are shown in; unset, the captain's Basecamp time zone.
        self.timezone = cfg.get("timezone")
        if self.timezone is not None:
            try:
                ZoneInfo(str(self.timezone))
            except (ValueError, KeyError):
                raise ValueError('config "timezone" must be an IANA time zone name, like "America/Chicago"')
        self._zone = None  # the resolved zone, once a run
        self.dry, self.run = dry, runner
        self._acting = False  # acting person id once resolved; None when it can't be
        self._acting_sgid = None  # the acting person's mention sgid, to spot @mentions in chat
        self._received = False  # this run's read of the acting person's received boosts, by id; None when it failed
        self._creators = {}  # (bucket, recording id) -> its creator's id, refetched once a run to tell the agent's own
        self.errors = 0  # failed basecamp calls this run, so a caller can tell whether a reader it ran fully worked
        self.data = os.path.join(self.home, "data")
        self.state = os.path.join(self.home, "state")

    # --- plumbing: files, log, CLIs ---

    def path(self, name):
        return os.path.join(self.dir, name)

    def load(self, name, default):
        p = self.path(name)
        return json.load(open(p)) if os.path.exists(p) else default

    def local_files(self, pattern):
        """(name, content bytes) of each file beside the config whose whole name matches regex `pattern`, by name."""
        out = []
        for name in sorted(os.listdir(self.dir)):
            if pattern.fullmatch(name) and os.path.isfile(self.path(name)):
                with open(self.path(name), "rb") as f:
                    out.append((name, f.read()))
        return out

    def save_json(self, name, value):
        tmp = self.path(name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(value, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path(name))

    @contextlib.contextmanager
    def locked(self, name):
        """Hold an exclusive lock on state file `name` while it is read, changed and saved; a dry run writes no lock file.

        locked("sync") is the shared state lock: a timer run, each listener cycle and
        each command hold it throughout, so two of them never write state at once. Taking
        a lock this instance already holds just goes on holding it.
        """
        if self.dry or name in self.held:
            yield
            return
        with open(self.path(name + ".lock"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.held.add(name)
            try:
                yield
            finally:
                self.held.discard(name)

    def log(self, msg):
        line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
        print(line)
        with open(self.path("sync.log"), "a") as f:
            f.write(line + "\n")

    def pending_lines(self):
        """The lines of pending-comments.jsonl, oldest first."""
        p = self.path("pending-comments.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()

    def zone(self):
        """The zone times written for people are shown in: config "timezone", else the captain's Basecamp time zone, else UTC."""
        if self._zone is None:
            name = self.timezone
            if name is None:
                try:
                    name = (self.bc("api", "get", f"/people/{self.captain}.json") or {}).get("time_zone")
                except Exception as e:
                    self.errors -= isinstance(e, BasecampError)  # a missing zone is no failed reader
                    self.log(f"time zone: could not read person {self.captain}, using UTC: {type(e).__name__}: {e}")
            try:
                self._zone = ZoneInfo(str(name)) if name else timezone.utc
            except (ValueError, KeyError):
                self.log(f"time zone: {name!r} is not an IANA zone, using UTC")
                self._zone = timezone.utc
        return self._zone

    def local_time(self, at):
        """ISO time `at` (or a datetime) in the people's zone, like "2026-10-21 09:14 CDT"; `at` as is when unreadable."""
        when = local_time(at, self.zone())
        return when if when is not None else at

    def record(self, rec):
        """Append one pending record for the agent, with its times also in the people's zone ("at_local", ...)."""
        for k in ("at", "created_at"):
            if rec.get(k) and local_time(rec[k], timezone.utc) is not None:
                rec = {**rec, k + "_local": self.local_time(rec[k])}
        with open(self.path("pending-comments.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")

    def bc(self, *args, input=None, project=None, tries=3):
        """Run the basecamp CLI on the configured project, or on `project` (another bucket of the account) when given;
        a retryable failure is tried again, up to `tries` runs in all."""
        prof = ["-P", self.profile] if self.profile else []
        cmd = ["basecamp", "-a", self.account, *prof, *args, "-p", str(project or self.project), "--json"]
        for attempt in range(tries):
            kw = {"input": input} if input is not None else {}
            r = self.run(cmd, capture_output=True, text=True, timeout=120, **kw)
            try:
                out = json.loads(r.stdout)
            except ValueError:
                out = {"ok": False, "error": (r.stdout + r.stderr)[:300]}
            if out.get("ok"):
                return out.get("data")
            if not out.get("retryable") or attempt == tries - 1:
                self.errors += 1
                raise BasecampError(f"basecamp {' '.join(args[:3])}: {out.get('error')}", out.get("code"), out.get("error"))
            time.sleep(3)

    def gh(self, *args):
        r = self.run(["gh", *args], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:3])}: {(r.stdout + r.stderr)[:300]}")
        return json.loads(r.stdout)

    # --- local readers: the backlog through tasks-axi (as bin/fm-tasks-axi.sh does), lavish-axi ---

    def tasks_axi(self, *args):
        root = os.path.dirname(self.data)
        env = dict(os.environ)
        backend = "markdown"
        toml = os.path.join(root, ".tasks.toml")
        if os.path.exists(toml):
            with open(toml, "rb") as f:
                backend = tomllib.load(f).get("backend", "markdown")
        if backend == "markdown":
            env["TASKS_AXI_FILE"] = os.path.join(self.data, "backlog.md")
        else:
            env.pop("TASKS_AXI_FILE", None)
        r = self.run(["tasks-axi", *args], capture_output=True, text=True, timeout=120, cwd=root, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"tasks-axi {' '.join(args)}: {(r.stdout + r.stderr)[:300]}")
        return r.stdout

    def read_backlog(self):
        rows = self.tasks_axi("list", "--limit", "100000").splitlines()
        ids = [ln.strip().split(",", 1)[0] for ln in rows if ln.startswith("  ") and "," in ln]
        return [task_item(parse_show(self.tasks_axi("show", i, "--full"))) for i in ids]

    def meta_pr(self, task):
        path = os.path.join(self.state, f"{task}.meta")
        if not os.path.exists(path):
            return None
        for line in open(path):
            if line.startswith("pr="):
                return line[3:].strip()
        return None

    def lavish_boards(self):
        """Open Lavish sessions by owning task: {task: [url, ...]}, or None when unreadable.

        lavish-axi has no machine-readable listing, so its plain `sessions[N]{...}` table
        is parsed. URLs are kept exactly as printed.
        """
        try:
            r = self.run(["lavish-axi"], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise RuntimeError((r.stdout + r.stderr)[:300])
            return parse_lavish(r.stdout, self.data)
        except Exception as e:
            self.log(f"lavish-axi unavailable, keeping existing board links: {type(e).__name__}: {e}")
            return None

    # --- identity and acknowledgement boosts ---

    def acting_id(self):
        """The person the profile signs in as, looked up once per run: an id, None to skip, or "retry"."""
        if self._acting is False:
            self._acting = None
            if self.profile:
                try:
                    me = self.bc("api", "get", "/my/profile.json") or {}
                    self._acting, self._acting_sgid = me.get("id"), me.get("attachable_sgid")
                except RuntimeError as e:
                    self.log(f"acting identity unknown, acknowledgement boosts wait for the next run: {e}")
                    self._acting = "retry"
        return self._acting

    def refused(self, what, who="captain"):
        """True (logged) when a command must not post: no profile, or the profile is the owner."""
        me = self.acting_id()
        if me == "retry":
            raise RuntimeError("acting identity unknown")
        if me is None or me == self.captain:
            self.log(f"{what}: acting user is the {who} or unset, not posting")
            return True
        return False

    def read_ack(self, question=True):
        """The boost that says an item was read: the config's "acknowledge" "read" on everything, else 👀 on a question
        (an @mention, a Ping or chat question, a request) and 👍 ("got it") on anything else."""
        return self.ack_read or (EYES if question else THUMBS)

    def acknowledge(self, key, rec, bucket=None):
        """Boost each queued recording (an owner comment or line, or the card for an approval) once.

        rec["ack"] holds [recording, emoji] pairs still to boost: 👀 for a question,
        👍 otherwise. Each moves to rec["acked"] as [recording, emoji, boost id] once
        boosted, so a failure is retried next run and a re-run never boosts twice.
        Nothing is boosted when no profile is set or it signs in as the captain
        himself; the queue is dropped. 👀 is only ever removed by the `reply` command.
        """
        done = [a[:2] for a in rec.get("acked", [])]
        queue = [q for q in rec.get("ack", []) if q not in done]
        if not queue:
            return
        me = self.acting_id()
        if me == "retry":
            return
        if me is None or me == self.captain:
            rec.pop("ack", None)
            return
        for rid, emoji in queue:
            if self.dry:
                self.log(f"dry {key}: acknowledge {rid} with {emoji}")
                continue
            try:
                bid = self.boost(me, rid, emoji, bucket)
                self.log(f"acknowledged {key}: {emoji} on {rid}")
            except RuntimeError as e:
                self.log(f"acknowledge {key} {rid} failed, retrying next run: {e}")
                continue
            rec.setdefault("acked", []).append([rid, emoji, bid])
            rec["ack"].remove([rid, emoji])

    def boosts_by(self, me, rid, emoji, bucket=None):
        path = f"/buckets/{bucket or self.project}/recordings/{rid}/boosts.json"
        return [b for b in self.bc("api", "get", path) or []
                if (b.get("booster") or {}).get("id") == me and is_emoji(b.get("content", ""), emoji)]

    def boost(self, me, rid, emoji, bucket=None):
        """The acting user's boost id for `emoji` on `rid` (in `bucket`, default the project), posting one only when absent."""
        have = self.boosts_by(me, rid, emoji, bucket)
        if have:
            return have[0].get("id")
        path = f"/buckets/{bucket or self.project}/recordings/{rid}/boosts.json"
        return (self.bc("api", "post", path, "-d", json.dumps({"content": emoji})) or {}).get("id")

    # --- card primitives (composed by the card mirror behavior; never run by hand) ---

    def card_create(self, table, column, title, body, assignee=None):
        args = ["cards", "create", title, body, "--card-table", table, "--column", column]
        if assignee is not None:
            args += ["--assignee", str(assignee)]
        return self.bc(*args)["id"]

    def card_update(self, card, table, title, body):
        self.bc("cards", "update", str(card), "--card-table", table, "--title", title, "--body", body)

    def card_move(self, card, table, column):
        self.bc("cards", "move", str(card), "--card-table", table, "--to", column)

    def card_assign(self, card, table):
        self.bc("cards", "update", str(card), "--card-table", table, "--assignee", str(self.captain))

    def card_unassign(self, card):
        self.bc("unassign", str(card), "--card", "--from", str(self.captain))

    def message(self, board, subject, content):
        """Post an HTML message on Message Board `board`; returns the message. No refusal rules."""
        return self.bc("api", "post", f"/buckets/{self.project}/message_boards/{board}/messages.json",
                       "-d", json.dumps({"subject": subject, "content": content, "status": "active"})) or {}

    def uploads_list(self, vault, project):
        """The uploads in Docs & Files folder `vault` of bucket `project`."""
        return self.bc("uploads", "list", "--vault", str(vault), "--all", project=project) or []

    def upload_create(self, path, vault, project):
        """Upload local file `path` into Docs & Files folder `vault` of bucket `project`; returns the upload."""
        return self.bc("uploads", "create", path, "--vault", str(vault), project=project) or {}

    def upload_replace(self, upload, path, project):
        """Replace upload `upload`'s file with local file `path` as a new version: same id, URL and name; nobody is notified."""
        return self.bc("files", "replace", str(upload), path, project=project) or {}

    def inbox_note(self, fm_home, request_id, body):
        """Queue `body` as a note in firstmate home `fm_home`'s inbox through its bin/fm-inbox.sh.

        Idempotent by `request_id`: a repeat replays the original note instead of adding
        one. Returns the outcome ("created" or "replay"); raises on any failure,
        including exit 3 (saved but firstmate not woken), which a retry with the same
        request id repairs.
        """
        cmd = [os.path.join(fm_home, "bin", "fm-inbox.sh"), "note", "--request-id", request_id, "--json", "-"]
        env = {k: v for k, v in os.environ.items() if k not in ("FM_ROOT_OVERRIDE", "FM_STATE_OVERRIDE")}
        env["FM_HOME"] = fm_home
        r = self.run(cmd, input=body, capture_output=True, text=True, timeout=30, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"fm-inbox.sh note exited {r.returncode}: {(r.stdout + r.stderr).strip()[:300]}")
        try:
            return json.loads(r.stdout).get("outcome")
        except ValueError:
            return None

    def session_holder(self, fm_home):
        """The pid holding firstmate home `fm_home`'s primary session lock, or None when no live process holds it.

        Read-only: line 1 of <fm_home>/state/.lock is the holder's pid. A lock written
        before this boot is stale even when its pid now names some other process, which
        is just what a reboot leaves behind.
        """
        p = os.path.join(fm_home, "state", ".lock")
        try:
            with open(p) as f:
                pid = int((f.readline() or "").strip())
            written = os.path.getmtime(p)
        except (OSError, ValueError):
            return None
        boot = boot_time()
        if pid <= 0 or (boot is not None and written < boot):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass  # another user's process: alive enough
        return pid

    def waiting_notes(self, fm_home):
        """When each note still pending in firstmate home `fm_home`'s inbox was queued (epoch seconds), oldest first.

        Read-only: a pending note is state/inbox/<epoch>-<suffix>.note; handled ones are in handled/.
        """
        d = os.path.join(fm_home, "state", "inbox")
        try:
            names = os.listdir(d)
        except OSError:
            return []
        out = []
        for n in names:
            m = re.match(r"(\d+)-[^/]*\.note$", n)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def chat_line(self, chat, content):
        """Post HTML `content` as a new line in chat `chat`; returns the line. No refusal rules."""
        return self.bc("api", "post", f"/buckets/{self.project}/chats/{chat}/lines.json",
                       "-d", json.dumps({"content": content, "content_type": "text/html"})) or {}

    # --- readers: poll Basecamp, append pending records, acknowledge ---

    def read_boosts(self, rec, surface, context, counted=None, always=(), bucket=None):
        """Record the owner's new boosts on recordings as `boost` records: a boost is an answer.

        `counted` is [(recording id, url, boosts_count, creator id)] (see `boost_items`), each
        read only when its count differs from rec["boost_counts"]; `always` is [(recording
        id, url)], read every run. Boost ids already recorded are in rec["boost_seen"]. The
        first time a record has no "boost_counts" (for counted) or no "boost_seen" (for
        always), they are seeded without recording, so turning this on never replays
        history. Each new boost is recorded only as `confirmed` allows: one to retry leaves
        its recording to be read again next run. `bucket` is where the recordings are
        (default the project). A dry run records nothing.
        """
        counts, seen = rec.get("boost_counts"), rec.get("boost_seen")
        seed_counts, seed_seen = counts is None, seen is None
        counts, seen = dict(counts or {}), list(seen or [])
        items = [(rid, url, None, None) for rid, url in always] + list(counted or [])
        for rid, url, count, creator in items:
            if count is not None:
                if seed_counts:
                    counts[str(rid)] = count
                    continue
                if count == counts.get(str(rid), 0):
                    continue
            try:
                boosts = self.bc("api", "get", f"/buckets/{bucket or self.project}/recordings/{rid}/boosts.json") or []
            except RuntimeError as e:
                self.log(f"boosts {surface} {rid}: {e}")
                continue
            retry = False
            for b in sorted(boosts, key=lambda b: b.get("id", 0)):
                bid = b.get("id")
                if bid in seen or not self.hears(b.get("booster"), admitted=False, bucket=bucket):
                    continue
                if self.dry:
                    self.log(f"dry {surface} {rid}: owner boost {bid}")
                    continue
                if not (count is None and seed_seen):
                    b = self.confirmed(b, rid, creator, bucket)
                    if b == "retry":
                        retry = True
                        continue
                seen.append(bid)
                if count is None and seed_seen or b is None:
                    continue
                self.record({"kind": "boost", "surface": surface, **context, "recording": rid, "boost": bid,
                             "text": html.unescape(re.sub(r"<[^>]+>", "", b.get("content", ""))).strip(),
                             "url": url, "at": b.get("created_at"), **self.author(b.get("booster"), bucket)})
                self.log(f"new owner boost on {surface} {rid}: {bid}")
            if count is not None and not retry:
                counts[str(rid)] = count
        if counted is not None:
            rec["boost_counts"] = counts
        if always or seen != list(rec.get("boost_seen") or []):
            rec["boost_seen"] = seen

    def received(self):
        """The acting person's received boosts by id: one fresh read of /my/boosts.json a run, shared by notifications
        and every boost reader; None when that read failed (logged)."""
        if self._received is False:
            try:
                self._received = {b.get("id"): b for b in self.my_boosts()}
            except RuntimeError as e:
                self.log(f"boosts: the agent's received boosts could not be read: {e}")
                self._received = None
        return self._received

    def creator_of(self, rid, bucket=None):
        """The id of recording `rid`'s creator, refetched once a run; "retry" when the read failed."""
        key = (str(bucket or self.project), rid)
        if key not in self._creators:
            try:
                self._creators[key] = ((self.recording(rid, bucket) or {}).get("creator") or {}).get("id")
            except RuntimeError as e:
                self.log(f"boosts: recording {rid}: {e}")
                return "retry"
        return self._creators[key]

    def confirmed(self, b, rid, creator=None, bucket=None):
        """Boost `b`, read from recording `rid`'s boosts, as it may be recorded: the boost, None to drop it, or "retry".

        A boost on the acting person's own recording counts only when this run's fresh read
        of its received boosts (/my/boosts.json) lists it, on that recording and by the same
        booster. Basecamp files a boost there under the person it was aimed at, so that
        proves both that the boost exists and that it was aimed at the agent; the booster,
        content and time are then taken from that read. One it does not list is retried
        while it is younger than RECENT seconds (it may have landed after the read), and
        then dropped, logged. A boost on anyone else's recording is not aimed at the agent
        and is recorded as read, and so is every boost when no agent acts (no profile, or the
        owner's). `creator` is the recording's creator id when the caller knows it; else the
        recording is refetched, only for a boost the read does not list.
        """
        me = self.agent()
        if me is None:
            return b
        feed = self.received()
        got = (feed or {}).get(b.get("id")) or {}
        if (got.get("recording") or {}).get("id") == rid and (got.get("booster") or {}).get("id") == (b.get("booster") or {}).get("id"):
            return {**b, "booster": got["booster"], "content": got.get("content", ""), "created_at": got.get("created_at")}
        creator = self.creator_of(rid, bucket) if creator is None else creator
        if creator == "retry":
            return "retry"
        if creator != me:
            return b
        if feed is None or recent(b.get("created_at")):
            return "retry"
        self.log(f"boost {b.get('id')} on {rid} is not among the agent's received boosts; not recorded")
        return None

    def read_card_comments(self, task, repo, key, rec):
        """Record the new comments of the people listened to on card rec["card"]: "question" when it has "?", else "comment"."""
        try:
            comments = self.bc("comments", "list", str(rec["card"])) or []
        except RuntimeError as e:
            self.log(f"comments {key}: {e}")
            comments = []
        for c in comments:
            cid = c.get("id")
            if cid in rec.setdefault("comments", []):
                continue
            rec["comments"].append(cid)
            if not self.hears(c.get("creator")):
                if self.addressed(c.get("content")):
                    self.block(c.get("creator"), self.project, rec["card"], cid, c.get("created_at"), what="comment")
            else:
                text = re.sub(r"<[^>]+>", "", c.get("content", ""))
                kind = "question" if "?" in html.unescape(text) else "comment"
                self.record({"kind": kind, "task": task, "repo": repo, "card": rec["card"], "comment": cid,
                             "at": c.get("created_at"), "text": text, **self.author(c.get("creator"))})
                rec.setdefault("ack", []).append([cid, self.read_ack(kind == "question")])
                self.log(f"new captain {kind} on {key}: {cid}")
        self.read_boosts(rec, "card-comment", {"task": task, "repo": repo, "card": rec["card"]},
                         counted=boost_items(comments))

    def read_card_boosts(self, task, repo, key, rec):
        """Record the captain's or an operator's 👍 on card rec["card"] as an approval, and any other boost by someone
        listened to as a `boost`.

        Only the captain and the operators approve: a participant's 👍 is a `boost` like any other.
        A dry run reads and logs only. Other boosts already on the card the first time
        it is read (no rec["card_boost_seen"]) are seeded, not recorded. Every other boost,
        an approval included, is recorded only as `confirmed` allows; one dropped there is
        kept in rec["card_boost_seen"].
        """
        path = f"/buckets/{self.project}/recordings/{rec['card']}/boosts.json"
        try:
            boosts = self.bc("api", "get", path) or []
        except RuntimeError as e:
            self.log(f"boosts {key}: {e}")
            return
        url = f"https://app.basecamp.com/{self.account}/buckets/{self.project}/card_tables/cards/{rec['card']}"
        seed = "card_boost_seen" not in rec
        for b in boosts:
            bid = b.get("id")
            if bid in rec.get("boosts", []) or bid in rec.get("card_boost_seen", []):
                continue
            if not self.hears(b.get("booster"), admitted=False):
                continue
            if not is_thumbs_up(b.get("content", "")) or self.role(b.get("booster"), admitted=False) not in AUTHORITY:
                if self.dry:
                    self.log(f"dry {key}: owner boost {bid}")
                    continue
                b = b if seed else self.confirmed(b, rec["card"])
                if b == "retry":
                    continue
                rec.setdefault("card_boost_seen", []).append(bid)
                if not seed and b is not None:
                    self.record({"kind": "boost", "surface": "card", "task": task, "repo": repo, "card": rec["card"],
                                 "recording": rec["card"], "boost": bid,
                                 "text": html.unescape(re.sub(r"<[^>]+>", "", b.get("content", ""))).strip(),
                                 "url": url, "at": b.get("created_at"), **self.author(b.get("booster"))})
                    self.log(f"new owner boost on {key}: {bid}")
                continue
            if self.dry:
                self.log(f"dry {key}: captain approval boost {bid}")
                self.acknowledge(key, dict(rec, ack=[*rec.get("ack", []), [rec["card"], THUMBS]]))
                continue
            b = self.confirmed(b, rec["card"])
            if b == "retry":
                continue
            if b is None:
                rec.setdefault("card_boost_seen", []).append(bid)
                continue
            rec.setdefault("boosts", []).append(bid)
            self.record({"kind": "approval", "task": task, "repo": repo, "card": rec["card"], "url": url,
                         "boost": bid, "text": re.sub(r"<[^>]+>", "", b.get("content", "")).strip(), "at": b.get("created_at"),
                         **self.author(b.get("booster"))})
            if [rec["card"], THUMBS] not in rec.setdefault("ack", []):
                rec["ack"].append([rec["card"], THUMBS])
            self.log(f"captain approval on {key}: boost {bid}")
        if not self.dry:
            rec.setdefault("card_boost_seen", [])

    def read_chats(self, chats, every_line=(), addressed=()):
        """Record the chat lines of the people listened to in `chats` as chat-question records.

        Per chat, chats.json keeps a cursor (the newest line id seen). The first run
        only sets the cursor, so old history is not relayed. A listened-to line that
        mentions the acting user or contains "?" is recorded and queued for a 👀; every
        other line is skipped, unless the chat is in `every_line`, where every such
        line is recorded. `addressed` holds line ids a notification says address the
        acting user (an @mention, or a Campfire reply to one of its lines, which carries
        no mention): each is recorded like a question, even when the cursor has passed
        it, unless it already was. A chat whose chats.json record has a "bucket" (a chat
        watched or mentioned in another project of the account) is read there, and its
        records carry that `project`; a line already recorded is never recorded again, and
        a watched chat never records a line at or before its watch's starting line ("after"),
        whatever notification names it. A dry run reads and logs only.
        """
        state = self.load("chats.json", {})
        for chat in chats:
            rec = dict(state.get(chat, {}))
            bucket = str(rec.get("bucket") or self.project)
            try:
                lines = self.bc("api", "get", f"/buckets/{bucket}/chats/{chat}/lines.json", project=bucket) or []
            except RuntimeError as e:
                self.log(f"chat {chat}: {e}")
                continue
            lines = sorted(lines, key=lambda l: l.get("id", 0))
            first = "cursor" not in rec
            cursor = rec.get("cursor", 0)
            floor = (rec.get("watch") or {}).get("after", 0)
            for ln in lines:
                lid = ln.get("id", 0)
                if lid <= floor:
                    continue
                late = lid <= cursor and lid in addressed and lid not in rec.get("lines", []) and not first
                if lid <= cursor and not late:
                    continue
                cursor = max(cursor, lid)
                if first or lid in rec.get("lines", []):
                    continue
                content = ln.get("content", "")
                if not self.hears(ln.get("creator"), bucket=bucket):
                    if lid in addressed or self.addressed(content):
                        self.block(ln.get("creator"), bucket, chat, lid, ln.get("created_at"), chat=True, what="chat line")
                    continue
                text = html.unescape(re.sub(r"<[^>]+>", "", content)).strip()
                if "?" not in text and chat not in every_line and lid not in addressed and not self.addressed(content):
                    continue
                if self.dry:
                    self.log(f"dry chat {chat}: captain question {lid}")
                    self.acknowledge(f"chat {chat}", dict(rec, ack=[*rec.get("ack", []), [lid, self.read_ack()]]), bucket)
                    continue
                self.record({"kind": "chat-question", "chat": int(chat), "line": lid,
                             "url": ln.get("app_url") or f"https://3.basecamp.com/{self.account}/buckets/{bucket}/chats/{chat}@{lid}",
                             "text": text, "at": ln.get("created_at"), **self.elsewhere(bucket, ln, rec),
                             **self.author(ln.get("creator"), bucket)})
                rec.setdefault("lines", []).append(lid)
                rec.setdefault("ack", []).append([lid, self.read_ack()])
                self.log(f"new captain chat question in {chat}: {lid}")
            self.read_boosts(rec, "chat", {"chat": int(chat)},
                             counted=boost_items(lines), bucket=bucket)
            if self.dry:
                if first:
                    self.log(f"dry chat {chat}: would start the cursor at {cursor}")
                self.acknowledge(f"chat {chat}", rec, bucket)
                continue
            rec["cursor"] = cursor
            state[chat] = rec
            self.acknowledge(f"chat {chat}", rec, bucket)
            self.save_json("chats.json", state)

    def today(self):
        tz = (self.checkins or {}).get("timezone")
        return datetime.now(ZoneInfo(tz) if tz else None)

    def read_checkins(self, questionnaires):
        """Record each check-in question that is due today and not yet answered by the acting user.

        Due: not paused, today's weekday in the schedule's "days" (0 = Sunday), the
        start_date reached, and the schedule's hour:minute passed in the configured
        "timezone" (the machine's local time when unset). Each question is recorded
        once per day as a `checkin`, with who wrote the question; nothing is answered here.
        checkins.json keeps question id -> {"recorded": [dates], "answered": [dates]}.
        A dry run reads and logs only.
        """
        now = self.today()
        date = now.strftime("%Y-%m-%d")
        state = self.load("checkins.json", {})
        for qn in questionnaires:
            try:
                questions = self.bc("api", "get", f"/buckets/{self.project}/questionnaires/{qn}/questions.json") or []
            except RuntimeError as e:
                self.log(f"checkins {qn}: {e}")
                continue
            for q in questions:
                qid = str(q.get("id"))
                rec = state.get(qid, {})
                if date in rec.get("recorded", []) or not is_due(q, now):
                    continue
                me = self.acting_id()
                if me == "retry":
                    return
                try:
                    if self.answered_today(q["id"], me, date):
                        continue
                except RuntimeError as e:
                    self.log(f"checkin {qid}: {e}")
                    continue
                if self.dry:
                    self.log(f"dry checkin {qid}: due {date}")
                    continue
                self.record({"kind": "checkin", "questionnaire": int(qn), "question": q["id"], "date": date,
                             "title": q.get("title"), "url": q.get("app_url"), "at": now.isoformat(timespec="seconds"),
                             **self.author(q.get("creator"))})
                rec.setdefault("recorded", []).append(date)
                state[qid] = rec
                self.save_json("checkins.json", state)
                self.log(f"checkin {qid} due {date}")

    def agent(self):
        """The acting user's id when it is someone other than the owner, else None."""
        me = self.acting_id()
        return None if me in (None, "retry", self.captain) else me

    def role(self, person, admitted=True, bucket=None):
        """Who `person` (a creator or booster object) is to the sync: "captain", "operator", "participant", or None.

        The captain's and an operator's word authorizes the agent; a participant's never
        does. A participant is someone in "people", or in "project_people" for `bucket`
        (where they wrote; default the configured project, so a Ping's own bucket matches
        no project), or, when `admitted` (what they wrote, not a boost, an assignment or
        unmonitored input), someone "participants" admits: on the project (not a client),
        with an email at a listed domain, or in the company. Anyone but the captain is None
        when they are the acting user itself, so the agent's own lines are never relayed
        back to it even when listed.
        """
        person = person or {}
        pid = person.get("id")
        if pid == self.captain:
            return "captain"
        named = pid in self.people or pid in self.project_people.get(str(bucket or self.project), ())
        role = "operator" if pid in self.operators else "participant" if named else None
        if role is None and not (admitted and (self.participant_project or self.participant_domains
                                               or self.participant_company is not None)):
            return None
        if pid == self.acting_id():
            return None
        return role or ("participant" if self.admitted(person) else None)

    def hears(self, person, admitted=True, bucket=None):
        """True when `person` is someone the sync listens to (see role); `admitted` False hears only named people."""
        return self.role(person, admitted, bucket) is not None

    def admitted(self, person):
        """True when "participants" admits `person`: an email at a listed domain, someone in the company, or (with
        "project") a project member.

        Basecamp masks other people's email addresses unless the login is an account admin,
        so a masked address matches no domain. A client is never admitted.
        """
        pid = person.get("id")
        if pid is None or person.get("client") is True:
            return False
        email = str(person.get("email_address") or "").lower()
        if self.participant_domains and "@" in email and email.rsplit("@", 1)[1] in self.participant_domains:
            return True
        if self.participant_company is not None:
            company = self.company()
            if company is not None and self.company_of(person) == company:
                return True
        return self.participant_project and pid in self.members()

    def company(self):
        """The company "participants": {"company"} admits: its id, or with true the captain's company; None if unknown."""
        if self.participant_company is True:
            return self.company_of({"id": self.captain})
        return self.participant_company

    def company_of(self, person):
        """`person`'s company id: from the person object when it carries one, else from their person record, read at
        most once a run. companies.json keeps the last id read for each person, which stands in when a read fails; with
        none, the person is unknown (None), so the company admits nobody it cannot check and they get no automatic
        reply (Tools.block)."""
        if self._companies is None:
            self._companies = self.load("companies.json", {})
        pid = person.get("id")
        company = (person.get("company") or {}).get("id")
        if company is None and pid not in self._company_read:
            self._company_read.add(pid)
            try:
                company = ((self.bc("api", "get", f"/people/{pid}.json") or {}).get("company") or {}).get("id")
            except RuntimeError as e:
                self.log(f"participants: person {pid}'s company could not be read, using the last one known: {e}")
        if company is not None and self._companies.get(str(pid)) != company:
            self._companies[str(pid)] = company
            if not self.dry:
                self.save_json("companies.json", self._companies)
        company = self._companies.get(str(pid)) if company is None else company
        if company is None:
            self._unverified.add(pid)
        return company

    def members(self):
        """The project's people who are not clients, as ids: read at most once a run, when first needed, and kept in
        participants.json, whose last list stands in when the read fails."""
        if self._members is None:
            try:
                people = self.bc("api", "get", f"/projects/{self.project}/people.json") or []
                self._members = {p.get("id") for p in people if p.get("client") is False}
                if not self.dry:
                    self.save_json("participants.json", sorted(self._members))
            except RuntimeError as e:
                self._members = set(self.load("participants.json", []))
                self.log(f"participants: the project's people could not be read, using the last list "
                         f"({len(self._members)} people): {e}")
        return self._members

    def author(self, person, bucket=None):
        """A record's author fields: who wrote it ({"id", "name"}), whether that is the captain, and their role (in
        `bucket`, default the project)."""
        person = person or {}
        return {"author": {"id": person.get("id"), "name": person.get("name")}, "captain": person.get("id") == self.captain,
                "role": self.role(person, bucket=bucket) if person.get("id") is not None else None}

    # --- blocked senders: someone the sync does not listen to who addresses the agent ---

    def addressed(self, content):
        """True when HTML `content` @mentions the acting user."""
        self.acting_id()
        return bool(self._acting_sgid and self._acting_sgid in (content or ""))

    def blocked_since(self):
        """When automatic replies started (blocked.json "since"), set the first time; nothing from before it is answered."""
        state = self.load("blocked.json", {})
        if not state.get("since") and not self.dry:
            state["since"] = now_iso()
            self.save_json("blocked.json", state)
        return state.get("since")

    def block(self, person, bucket, thread, item, at, chat=False, what="message"):
        """Someone the sync does not listen to addressed the agent: an @mention, a Ping line, a to-do or card assigned.

        Nothing of theirs reaches the agent: no record, no inbox note, no 👀; the block is
        logged. With "blocked" on, the readers call this so they get one short automatic
        reply where they wrote (a new line in chat `thread` when `chat`, else a comment on
        recording `thread`, in `bucket`), at most once per person and conversation a day,
        and only for `item`s made (ISO time `at`) after blocked.json "since". Never the
        captain, the acting user or a system sender. A failed reply is logged and not
        retried (it does not count as a failed call, so the notification is not re-read
        for it). A dry run only logs. Returns True when a reply was posted.
        """
        person = person or {}
        pid = person.get("id")
        if not pid or pid == self.captain or pid == self.acting_id():
            return False
        who = f"{person.get('name') or 'person'} ({pid})"
        where = f"{what} {item} in {bucket}/{thread}"
        if self.blocked_reply is None:
            self.log(f"blocked {who}: {where} not relayed, not someone the sync listens to (no automatic reply: \"blocked\" is off)")
            return False
        state = self.load("blocked.json", {})
        since, key = state.get("since"), f"{pid}:{bucket}:{thread}"
        last = state.get("replied", {}).get(key)
        if (self.agent() is None or pid in self._unverified or not since or not later(at, since)
                or (last and recent(last, within=86400))):
            why = ("the acting user is the owner or unset" if self.agent() is None else
                   "their company could not be checked" if pid in self._unverified else
                   "from before automatic replies started" if not since or not later(at, since) else "already answered today")
            self.log(f"blocked {who}: {where} not relayed, not someone the sync listens to (no reply: {why})")
            return False
        if self.dry:
            self.log(f"dry blocked {who}: {where}: would post the automatic reply")
            return False
        errors = self.errors
        try:
            if chat:
                self.bc("api", "post", f"/buckets/{bucket}/chats/{thread}/lines.json",
                        "-d", json.dumps({"content": html.escape(self.blocked_reply, quote=False), "content_type": "text/html"}),
                        project=bucket)
            else:
                self.bc("comments", "create", str(thread), "-", input=self.blocked_reply, project=bucket)
        except RuntimeError as e:
            self.errors = errors
            self.log(f"blocked {who}: {where} not relayed; the automatic reply failed, not retried: {e}")
            return False
        replied = {k: v for k, v in state.get("replied", {}).items() if recent(v, within=2 * 86400)}
        replied[key] = now_iso()
        state["replied"] = replied
        self.save_json("blocked.json", state)
        self.log(f"blocked {who}: {where} not relayed, not someone the sync listens to; automatic reply posted")
        return True

    def read_answer_boosts(self, questionnaires, days=7):
        """The owner's boosts on check-in answers the acting user posted in the last `days` days.

        One answers read per question that has answers, then a boosts read per answer
        whose boosts_count changed. State is the question's entry in checkins.json.
        """
        me = self.agent()
        if me is None:
            return
        since = (self.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        state = self.load("checkins.json", {})
        for qn in questionnaires:
            try:
                questions = self.bc("api", "get", f"/buckets/{self.project}/questionnaires/{qn}/questions.json") or []
                for q in questions:
                    if not q.get("answers_count"):
                        continue
                    answers = self.bc("api", "get", f"/buckets/{self.project}/questions/{q['id']}/answers.json") or []
                    mine = [a for a in answers if (a.get("creator") or {}).get("id") == me
                            and (a.get("group_on") or (a.get("created_at") or "")[:10]) >= since]
                    rec = state.setdefault(str(q["id"]), {})
                    self.read_boosts(rec, "checkin-answer", {"question": q["id"]},
                                     counted=boost_items(mine))
            except RuntimeError as e:
                self.log(f"checkin answer boosts {qn}: {e}")
        if not self.dry:
            self.save_json("checkins.json", state)

    def read_messages(self, board, days=14, only=None):
        """The owner's comments and boosts on messages the acting user posted on `board` in the last `days` days.

        One read of the board's newest messages; a boosts read per message whose
        boosts_count changed; a comments read per such message that has comments, and a
        boosts read per comment whose count changed. Each owner comment newer than the
        message's cursor is recorded once as a `message-comment`, acknowledged like a card
        comment (👀 when it contains "?", 👍 otherwise). A message has no cursor until its
        first read, so feedback already on a recent post is relayed, not skipped. `only`
        (a message id, from a notification) reads that one message instead of the board,
        whatever its age, when the acting user posted it. State is messages.json. A dry
        run reads and logs only.
        """
        me = self.agent()
        if me is None:
            return
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            if only is not None:
                msgs = [self.bc("api", "get", f"/buckets/{self.project}/messages/{only}.json") or {}]
            else:
                msgs = self.bc("api", "get", f"/buckets/{self.project}/message_boards/{board}/messages.json") or []
        except RuntimeError as e:
            self.log(f"messages {only or board}: {e}")
            return
        mine = [m for m in msgs if (m.get("creator") or {}).get("id") == me
                and (only is not None or (m.get("created_at") or "") >= since)]
        with self.locked("messages.json"):
            state = self.load("messages.json", {})
            for m in mine:
                ctx = {"message": m["id"], "subject": m.get("subject")}
                self.read_boosts(state.setdefault("messages", {}), "message", ctx,
                                 counted=boost_items([m]))
                if not m.get("comments_count"):
                    continue
                try:
                    comments = self.bc("comments", "list", str(m["id"])) or []
                except RuntimeError as e:
                    self.log(f"message {m['id']} comments: {e}")
                    continue
                post = state.setdefault("posts", {}).setdefault(str(m["id"]), {"cursor": 0})
                cursor = post["cursor"]
                for c in sorted(comments, key=lambda c: c.get("id", 0)):
                    cid = c.get("id", 0)
                    if cid <= cursor:
                        continue
                    cursor = cid
                    if not self.hears(c.get("creator")):
                        if self.addressed(c.get("content")):
                            self.block(c.get("creator"), self.project, m["id"], cid, c.get("created_at"), what="comment")
                        continue
                    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c.get("content", "")))).strip()
                    question = "?" in text
                    if self.dry:
                        self.log(f"dry message {m['id']}: owner comment {cid}")
                        continue
                    self.record({"kind": "message-comment", "message": m["id"], "subject": m.get("subject"),
                                 "comment": cid, "question": question, "url": c.get("app_url") or m.get("app_url"),
                                 "text": text, "at": c.get("created_at"), **self.author(c.get("creator"))})
                    post.setdefault("comments", []).append(cid)
                    post.setdefault("ack", []).append([cid, self.read_ack(question)])
                    self.log(f"new owner comment on message {m['id']}: {cid}")
                self.acknowledge(f"message {m['id']}", post)
                if not self.dry:
                    post["cursor"] = cursor
                self.read_boosts(state.setdefault("comments", {}), "message-comment", ctx,
                                 counted=boost_items(comments))
            if not self.dry:
                self.save_json("messages.json", state)

    def answered_today(self, qid, me, date):
        """True when `me` already answered question `qid` for `date` in Basecamp."""
        if me is None:
            return False
        answers = self.bc("api", "get", f"/buckets/{self.project}/questions/{qid}/answers.json") or []
        return any((a.get("creator") or {}).get("id") == me and (a.get("group_on") or (a.get("created_at") or "")[:10]) == date
                   for a in answers)

    def read_todo_comments(self, keys=None, requests=None):
        """Record the owner's new comments on each open tracked to-do (or only those in `keys`) as `todo-comment` records.

        todos.json keeps, per key, the to-do id and a cursor (the newest comment id
        seen). Each comment by someone listened to newer than the cursor is recorded once
        and queued for a 👀 when it contains "?", a 👍 otherwise; other people's comments,
        the agent's own included, only move the cursor. Completed or closed to-dos are
        not read. `requests` True reads only to-do requests (records carry "request":
        true), False only the agent's decision to-dos, None both. A dry run reads and
        logs only.
        """
        for key in sorted(self.load("todos.json", {})):
            if keys is not None and key not in keys:
                continue
            with self.locked("todos.json"):
                state = self.load("todos.json", {})
                rec = state.get(key)
                if rec is None or rec.get("completed") or rec.get("closed"):
                    continue
                if requests is not None and bool(rec.get("request")) != requests:
                    continue
                try:
                    comments = self.bc("comments", "list", str(rec["todo"]), project=rec.get("bucket")) or []
                except RuntimeError as e:
                    self.log(f"todo {key}: {e}")
                    continue
                if self.dry:
                    rec = json.loads(json.dumps(rec))
                cursor = rec.get("cursor", 0)
                for c in sorted(comments, key=lambda c: c.get("id", 0)):
                    cid = c.get("id", 0)
                    if cid <= cursor:
                        continue
                    cursor = cid
                    if not self.hears(c.get("creator"), bucket=rec.get("bucket")):
                        if self.addressed(c.get("content")):
                            self.block(c.get("creator"), rec.get("bucket") or self.project, rec["todo"], cid,
                                       c.get("created_at"), what="comment")
                        continue
                    text = html.unescape(re.sub(r"<[^>]+>", " ", c.get("content", "")))
                    text = re.sub(r"\s+", " ", text).strip()
                    question = "?" in text
                    rec.setdefault("ack", []).append([cid, self.read_ack(question)])
                    if self.dry:
                        self.log(f"dry todo {key}: owner comment {cid}")
                        continue
                    self.record({"kind": "todo-comment", "key": key, "todo": rec["todo"], "comment": cid,
                                 "question": question, "url": c.get("app_url") or rec.get("url"),
                                 "text": text, "at": c.get("created_at"), **request_fields(rec),
                                 **({"on": rec.get("title")} if rec.get("on") else {}),
                                 **self.author(c.get("creator"), rec.get("bucket"))})
                    rec.setdefault("comments", []).append(cid)
                    if not rec.get("request") and self.role(c.get("creator"), bucket=rec.get("bucket")) in AUTHORITY:
                        rec.setdefault("deciding", []).append(cid)  # acknowledged as decided once it is carried out
                    self.log(f"new owner comment on todo {key}: {cid}")
                self.read_todo_boosts(key, rec, comments)
                self.read_steps(key, rec)
                self.acknowledge(f"todo {key}", rec, rec.get("bucket"))
                if not self.dry:
                    rec["cursor"] = cursor
                    self.save_json("todos.json", state)

    def read_steps(self, key, rec):
        """Record each open decision step (a subtask `decide` assigned to someone) that someone other than the acting
        user checked, once, as a `decision-step-done` record with who checked it, read from the step's events (a
        subtask's own record does not say who completed it); the step is then marked done in rec. A dry run reads only."""
        bucket = rec.get("bucket") or self.project
        for n, step in enumerate(rec.get("subtasks") or [], 1):
            if not step.get("decider") or step.get("done"):
                continue
            try:
                events = self.bc("api", "get", f"/buckets/{bucket}/recordings/{step['id']}/events.json", project=bucket) or []
            except RuntimeError as e:
                self.log(f"todo {key}: step {n}: {e}")
                continue
            last = max((e for e in events if e.get("action") in ("completed", "uncompleted")),
                       key=lambda e: e.get("created_at") or "", default=None)
            if not last or last.get("action") != "completed":
                continue
            who = last.get("creator") or {}
            if self.dry:
                self.log(f"dry todo {key}: step {n} checked by {who.get('id')}")
                continue
            step["done"] = last.get("created_at") or now_iso()
            if who.get("id") == self.acting_id():
                continue
            self.record({"kind": "decision-step-done", "key": key, "todo": rec["todo"], "step": step["id"], "number": n,
                         "question": step.get("text"), "title": rec.get("title"), "url": rec.get("url"),
                         "at": last.get("created_at"), **self.author(who, rec.get("bucket"))})
            self.log(f"todo {key}: decision step {n} checked by {who.get('id')}")

    def read_todo_boosts(self, key, rec, comments):
        """The owner's boosts on the to-do (read every run), on each open subtask or decision step (read every run: a
        boost there answers that decision), and on its comments (when their count changes)."""
        ctx, bucket = {"key": key, "todo": rec["todo"], **request_fields(rec)}, rec.get("bucket")
        self.read_boosts(rec, "todo", ctx, always=[(rec["todo"], rec.get("url"))], bucket=bucket)
        for n, q in enumerate(rec.get("subtasks") or [], 1):
            if not q.get("done") and q.get("id"):
                self.read_boosts(rec, "subtask", dict(ctx, step=n, question=q.get("text")),
                                 always=[(q["id"], rec.get("url"))], bucket=bucket)
        self.read_boosts(rec, "todo-comment", ctx, bucket=bucket,
                         counted=boost_items(comments))

    def readings(self):
        """The acting user's notifications, one read of /my/readings.json: {"unreads": [...], "reads": [first page], ...}."""
        return self.bc("api", "get", "/my/readings.json") or {}

    def my_boosts(self):
        """The boosts on the acting user's own recordings (its chat lines, comments, to-dos, cards, messages, answers),
        one read of /my/boosts.json, each with its "recording" (type, bucket, parent). Not in Basecamp's API docs."""
        return self.bc("api", "get", "/my/boosts.json") or []

    def mark_read(self, ids):
        """Mark the acting user's notifications `ids` read, through `basecamp notifications read` (PUT /my/unreads.json)."""
        if ids:
            self.bc("notifications", "read", *[str(i) for i in ids])

    def ping_conversations(self, data=None):
        """The Ping conversations (direct messages, each a chat in its own "circle" bucket) the acting user is in with someone listened to.

        One read of /my/readings.json (or `data`, a read already made): its "pings" section,
        read and unread, lists each recently active Ping with a listened-to person among
        its participants or as its creator (with "blocked" on, every Ping, so anyone else's
        line there gets the automatic reply); the bucket and chat ids come from its
        subscription_url. Newest activity first.
        """
        data = self.readings() if data is None else data
        found = {}
        for r in (data.get("unreads") or []) + (data.get("reads") or []):
            m = re.search(r"/buckets/(\d+)/recordings/(\d+)/", r.get("subscription_url") or "")
            people = (r.get("participants") or []) + [r.get("creator") or {}]
            if r.get("section") != "pings" or r.get("type") != "Chat" or not m:
                continue
            # "blocked": every Ping, to answer. A Ping is a bucket of its own, so "project_people" never admits there.
            if self.blocked_reply is None and not any(self.hears(p, bucket=m.group(1)) for p in people):
                continue
            found.setdefault(m.group(2), {"bucket": m.group(1), "chat": m.group(2), "title": r.get("bucket_name"),
                                          "url": r.get("app_url"), "updated_at": r.get("updated_at") or ""})
        return sorted(found.values(), key=lambda p: p["updated_at"], reverse=True)

    def read_pings(self, limit=10, convs=None):
        """Record each new line someone listened to writes in a Ping with the acting user as a `ping` record, once.

        Pings are found with ping_conversations(), at most `limit` per run, newest activity
        first; a Ping drops out of the readings once it goes quiet, and a new line puts it
        back. `convs` (from a notification) reads those Pings instead. pings.json keeps
        "since" (when the behavior first ran) and, per Ping chat, a
        cursor (the newest line id seen): every owner line newer than both is recorded and
        queued for a 👀, so a Ping that starts after "since" is relayed from its first line
        and history from before it never is. The owner's boosts on lines in a Ping are
        `boost` records, as in a chat. Refused (logged) unless the acting user is someone
        other than the owner: /my/readings.json is the acting user's. A dry run reads and
        logs only.
        """
        if self.agent() is None:
            self.log("pings: the acting user is the owner, unset or unknown; Pings not read")
            return
        state = self.load("pings.json", {})
        since = state.get("since") or now_iso()
        try:
            convs = self.ping_conversations()[:limit] if convs is None else convs
        except RuntimeError as e:
            self.log(f"pings: {e}")
            return
        for p in convs:
            bucket, chat = p["bucket"], p["chat"]
            try:
                lines = self.bc("api", "get", f"/buckets/{bucket}/chats/{chat}/lines.json") or []
            except RuntimeError as e:
                self.log(f"ping {chat}: {e}")
                continue
            lines = sorted(lines, key=lambda l: l.get("id", 0))
            rec = json.loads(json.dumps(state.get("pings", {}).get(chat, {})))
            rec.update(bucket=int(bucket), title=p["title"], url=p["url"])
            cursor = rec.get("cursor", 0)
            for ln in lines:
                lid = ln.get("id", 0)
                if lid <= cursor:
                    continue
                cursor = lid
                if not later(ln.get("created_at"), since):
                    continue
                if not self.hears(ln.get("creator"), bucket=bucket):
                    self.block(ln.get("creator"), bucket, chat, lid, ln.get("created_at"), chat=True, what="Ping line")
                    continue
                if self.dry:
                    self.log(f"dry ping {chat}: owner line {lid}")
                    continue
                self.record({"kind": "ping", "bucket": int(bucket), "chat": int(chat), "line": lid, "title": p["title"],
                             "url": ln.get("app_url") or f"https://app.basecamp.com/{self.account}/circles/{bucket}@{lid}",
                             "text": html.unescape(re.sub(r"<[^>]+>", "", ln.get("content", ""))).strip(),
                             "at": ln.get("created_at"), **self.author(ln.get("creator"), bucket)})
                rec.setdefault("lines", []).append(lid)
                rec.setdefault("ack", []).append([lid, self.read_ack()])
                self.log(f"new owner ping line in {chat}: {lid}")
            self.read_boosts(rec, "ping", {"bucket": int(bucket), "chat": int(chat)}, bucket=bucket,
                             counted=boost_items(lines))
            self.acknowledge(f"ping {chat}", rec, bucket)
            if not self.dry:
                rec["cursor"] = cursor
                state.setdefault("pings", {})[chat] = rec
        if not self.dry:
            state["since"] = since
            self.save_json("pings.json", state)

    # --- to-do requests: to-dos the people listened to assign to the acting user ---

    def todo(self, tid, bucket=None, card=False):
        """To-do `tid` in `bucket` (default the project), refetched: content (its name), description, assignees, creator,
        completed, completion, status, due_on, repetition_schedule, its parent (list) and the bucket (project) it is in.
        `card`: card `tid` instead, in the same shape (its title as content, its content as description)."""
        if card:
            c = self.bc("api", "get", f"/buckets/{bucket or self.project}/card_tables/cards/{tid}.json", project=bucket) or {}
            return dict(c, content=c.get("title"), description=c.get("content"))
        return self.bc("api", "get", f"/buckets/{bucket or self.project}/todos/{tid}.json", project=bucket) or {}

    def assigned_todo_buckets(self, card=False):
        """{to-do id: its bucket id} for the open to-dos (or, with `card`, cards) assigned to the acting user, from one
        read of /my/assignments.json: in this project, or in every project of the account when the scope is "account"."""
        data = self.bc("api", "get", "/my/assignments.json") or {}
        items = data if isinstance(data, list) else (data.get("priorities") or []) + (data.get("non_priorities") or [])
        kind, scope = ("card", self.card_scope) if card else ("todo", self.assigned_scope)
        found = {}
        for a in items:
            bucket = str((a.get("bucket") or {}).get("id") or self.project)
            if (a.get("id") and not a.get("completed") and (a.get("type") or "todo").lower() == kind
                    and (scope == "account" or bucket == self.project)):
                found.setdefault(a["id"], bucket)
        return found

    def request_keys(self, cards=None):
        """The keys of the open to-do requests in todos.json; `cards` True only the card requests, False only the to-dos'."""
        return sorted(k for k, r in self.load("todos.json", {}).items()
                      if r.get("request") and not r.get("completed") and not r.get("closed")
                      and (cards is None or (r.get("type") == "card") == cards))

    def mirrored_card(self, cid, bucket=None):
        """True when card `cid` is one the card mirror made from the backlog (map.json), never a request."""
        return (str(bucket or self.project) == self.project
                and any(r.get("card") == cid for r in self.load("map.json", {}).values()))

    def previous_occurrence(self, tid, todo, bucket=None):
        """The completed request that repeating to-do `tid` is the next occurrence of, as (key, its todos.json entry).

        Basecamp schedules a repeating to-do itself: completing one makes the next as a
        new to-do (a new id) in the same list with the same name and repetition rule, and
        that making raises no event. The match is the completed repeating request in the
        same project and list with the same name that no later occurrence followed yet,
        the latest completed; a to-do Basecamp reused (completed, then open again) is
        its own previous occurrence. A request of the series still open here (the sweep
        reads new to-dos before it notices a completion) is read first: its last comments,
        then its closing. (None, None) for a to-do that does not repeat, or with no such
        request.
        """
        if not todo_schedule(todo)["repeats"]:
            return None, None
        parent = (todo.get("parent") or {}).get("id")

        def series(rec):
            return (rec.get("request") and rec.get("repeats") and rec.get("parent") == parent
                    and rec.get("title") == todo.get("content")
                    and str(rec.get("bucket") or self.project) == str(bucket or self.project))

        for key in self.request_keys():
            rec = self.load("todos.json", {})[key]
            if rec.get("todo") != tid and series(rec):
                self.read_todo_comments(keys=[key])
                self.refresh_todo_request(key)
        found = []
        for key, rec in self.load("todos.json", {}).items():
            if not (rec.get("request") and rec.get("repeats") and rec.get("completed")
                    and rec.get("closed") in (None, "completed")):
                continue
            same = rec.get("todo") == tid
            if same or (rec.get("next") is None and series(rec)):
                found.append((same, rec.get("completed"), key, rec))
        if not found:
            return None, None
        _, _, key, rec = max(found, key=lambda f: f[:2])
        return key, rec

    def read_todo_request(self, tid, by=None, bucket=None, card=False):
        """Record to-do `tid` as a `todo-request` once, when it is open, assigned to the acting user, and `by` is listened to.

        `by` is who assigned it (an event's creator id); None means its creator, as the
        sweep has no other way to tell. `bucket` is the project it is in (default the
        configured one); the request keeps it, with the project's name, so everything later
        read or posted on it goes there, and every record of it carries the project. A new request is tracked in todos.json as
        "request-<id>" with a cursor of 0, so the comments already on it are relayed as
        part of it, and the to-do is queued for a 👀. A closed request assigned again is
        a new request under the same key. A scheduled to-do's record carries its due date
        and, when it repeats, Basecamp's rule (`due_on`, `repeats`); the next occurrence of
        a repeating request the agent or anyone completed is a new request that `follows`
        it, asked by whoever asked for the one before (Basecamp made it, so its creator is
        not who asked). Returns True when recorded; False for an
        open request already tracked, a to-do assigned to someone else, or one not
        assigned by someone listened to (the sweep remembers those in
        assigned-todos.json, so it fetches each once). A dry run records nothing.

        `card`: `tid` is a card (assigned_cards), recorded as a `card-request` and kept with
        "type": "card"; a card the card mirror made from the backlog is never a request.
        """
        me = self.agent()
        key = f"request-{tid}"
        rec = self.load("todos.json", {}).get(key)
        if me is None or (rec is not None and not rec.get("completed") and not rec.get("closed")):
            return False
        if card and self.mirrored_card(tid, bucket):
            return False
        try:
            todo = self.todo(tid, bucket, card)
        except RuntimeError as e:
            self.log(f"todo request {tid}: {e}")
            return False
        if (todo.get("completed") or todo.get("status") not in (None, "active")
                or me not in [a.get("id") for a in todo.get("assignees") or []]):
            return False
        prev_key, prev = self.previous_occurrence(tid, todo, bucket)
        creator = todo.get("creator") or {}
        who = creator if by is None or by == creator.get("id") else {"id": by, "name": None}
        if prev is not None and by is None:
            who = (prev.get("request") or {}).get("author") or who
        if not self.hears(who, admitted=False, bucket=bucket):
            if by is None and not self.dry:
                state = self.load("assigned-todos.json", {})
                state["ignored"] = sorted({*state.get("ignored", []), tid})
                self.save_json("assigned-todos.json", state)
            self.log(f"todo request {tid}: not assigned by someone listened to, ignored")
            # Only a notification names who assigned it (the sweep only guesses: its creator), and a participant admitted
            # by rule is listened to, just not for assignments: neither is a blocked sender to answer.
            if by is not None and not self.hears(who, bucket=bucket):
                self.block(who, bucket or self.project, tid, tid, todo.get("updated_at"), what="card" if card else "to-do")
            return False
        if self.dry:
            self.log(f"dry todo request {tid}: record and acknowledge with {EYES}")
            return False
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            rec = state.get(key) or {"todo": tid, "cursor": 0, "created": todo.get("created_at")}
            for k in ("completed", "closed", "next"):
                rec.pop(k, None)
            n = rec.get("requests", 0) + 1
            rec.update(title=todo.get("content"), url=todo.get("app_url"), digest=todo_digest(todo), requests=n,
                       request=self.author(who, bucket), bucket=int(bucket or self.project),
                       project_name=(todo.get("bucket") or {}).get("name") or rec.get("project_name"),
                       parent=(todo.get("parent") or {}).get("id"), **todo_schedule(todo))
            if card:
                rec["type"] = "card"
            if prev_key is not None and prev_key != key:
                state[prev_key]["next"] = tid
            if [tid, self.read_ack()] not in [a[:2] for a in rec.get("acked", [])] + rec.get("ack", []):
                rec.setdefault("ack", []).append([tid, self.read_ack()])
            self.record({"kind": "card-request" if card else "todo-request", "key": key, "todo": tid, "n": n, "reopened": n > 1 and prev is None,
                         "follows": prev["todo"] if prev else None, **todo_schedule(todo), **self.timing(todo),
                         "title": todo.get("content"), "text": plain(todo.get("description")),
                         "assignees": [a.get("name") for a in todo.get("assignees") or []],
                         "url": todo.get("app_url"), "at": todo.get("updated_at") or todo.get("created_at"),
                         "project": request_project(rec), **self.author(who, bucket)})
            self.log(f"todo request {key}: todo {tid} in project {rec['bucket']} assigned by {who.get('id')}")
            self.acknowledge(f"todo {key}", rec, rec["bucket"])
            state[key] = rec
            self.save_json("todos.json", state)
        return True

    def discover_todo_requests(self, limit=10, cards=False):
        """The sweep: run read_todo_request for each open to-do assigned to the acting user that is not yet an open request.

        Only this project's to-dos, unless the scope is "account": then every project's.
        At most `limit` to-dos are fetched a run; one already found not to be assigned by
        someone listened to is never fetched again. Refused (logged) unless the acting
        user is someone other than the owner: /my/assignments.json is the acting user's.
        `cards`: the cards assigned to the acting user instead (assigned_cards).
        """
        if self.agent() is None:
            self.log("todo requests: the acting user is the owner, unset or unknown; assigned to-dos not read")
            return
        try:
            found = self.assigned_todo_buckets(cards)
        except RuntimeError as e:
            self.log(f"todo requests: {e}")
            return
        tracked = {self.load("todos.json", {})[k]["todo"] for k in self.request_keys()}
        ignored = set(self.load("assigned-todos.json", {}).get("ignored", []))
        for tid in [i for i in found if i not in tracked and i not in ignored][:limit]:
            self.read_todo_request(tid, bucket=found[tid], card=cards)

    def refresh_todo_request(self, key, by=None):
        """Record what changed on open to-do request `key` since it was last read: an edit, or its closing.

        Completed (by anyone), trashed or archived, or no longer assigned to the acting
        user: a `todo-request-closed` record (`reason` completed, trashed, archived or
        unassigned, and who completed it when Basecamp says), and the request stops
        being read; completed also marks it completed, so `todo complete` is not needed.
        Its name or description changed, or (for one tracked with its schedule) its due date
        or repetition rule: a `todo-request-update` record with the new text and schedule.
        `by` is who did it, when an event says; otherwise Basecamp does not say who edited it. A dry run records nothing. Returns the record kind, or None.
        A card request's records are `card-request-closed` and `card-request-update`.
        """
        me = self.agent()
        rec = self.load("todos.json", {}).get(key)
        if me is None or rec is None or not rec.get("request") or rec.get("completed") or rec.get("closed"):
            return None
        try:
            todo = self.todo(rec["todo"], rec.get("bucket"), rec.get("type") == "card")
        except RuntimeError as e:
            self.log(f"todo request {key}: {e}")
            return None
        who = {"id": by, "name": None} if by is not None else None
        if todo.get("completed"):
            reason = "completed"
            who = (todo.get("completion") or {}).get("creator") or who
        elif todo.get("status") not in (None, "active"):
            reason = todo.get("status")
        elif me not in [a.get("id") for a in todo.get("assignees") or []]:
            reason = "unassigned"
        elif todo_digest(todo) != rec.get("digest") or ("due_on" in rec and todo_schedule(todo) != {
                k: rec.get(k) for k in ("due_on", "repeats")}):
            reason = None
        else:
            if "due_on" not in rec and not self.dry:
                # Tracked before requests kept their schedule and list: keep them now, so a later move of the due date
                # is an update and Basecamp's next occurrence of a repeating one follows it.
                with self.locked("todos.json"):
                    state = self.load("todos.json", {})
                    state[key].update(todo_schedule(todo), parent=(todo.get("parent") or {}).get("id"))
                    self.save_json("todos.json", state)
            return None
        kind = ("card" if rec.get("type") == "card" else "todo") + ("-request-closed" if reason else "-request-update")
        if self.dry:
            self.log(f"dry todo request {key}: {kind}" + (f" ({reason})" if reason else ""))
            return None
        authored = self.author(who, rec.get("bucket")) if who else {"author": None, "captain": None}
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            rec = state[key]
            out = {"kind": kind, "key": key, "todo": rec["todo"], "n": rec.get("requests", 1),
                   "title": todo.get("content") or rec.get("title"), "url": todo.get("app_url") or rec.get("url"),
                   "at": (todo.get("completion") or {}).get("created_at") or todo.get("updated_at"),
                   "project": request_project(rec), **todo_schedule(todo), **({} if reason else self.timing(todo)), **authored}
            if reason:
                out["reason"] = reason
                rec["closed"] = reason
                if reason == "completed":
                    rec["completed"] = now_iso()
            else:
                out["text"] = plain(todo.get("description"))
                rec.update(digest=todo_digest(todo), title=todo.get("content") or rec.get("title"), **todo_schedule(todo))
            self.record(out)
            self.save_json("todos.json", state)
        self.log(f"todo request {key}: {kind}" + (f" ({reason})" if reason else ""))
        return kind

    # --- refetches, @mentions in threads nothing tracks, and unmonitored input ---

    def comment(self, cid, bucket=None):
        """Comment `cid` (in `bucket`, default the project), refetched from Basecamp; its "parent" names what it is on."""
        return self.bc("api", "get", f"/buckets/{bucket or self.project}/comments/{cid}.json", project=bucket) or {}

    def recording(self, rid, bucket=None):
        """Recording `rid` of any type (in `bucket`, default the project), refetched (type, title, content, creator, parent, app_url)."""
        return self.bc("api", "get", f"/buckets/{bucket or self.project}/recordings/{rid}.json", project=bucket) or {}

    def read_mention(self, bucket, chat, rid):
        """Record a listened-to person's line `rid` in chat `chat` that @mentions the acting user (or is a Campfire reply
        to one of its lines) when no chat reader relays that chat, once, as a `chat-question` kept in chats.json, so
        `reply` answers it there; queued for a 👀. In another project ("mentions": {"scope": "account"}) the record
        carries that `project` and chats.json its bucket. A watched chat's line at or before its watch's starting line, or
        any line of a watched chat skipped this run (`watch_held`), is never recorded. Returns True when recorded. A dry run
        records nothing."""
        known = self.load("chats.json", {}).get(str(chat), {})
        if self.agent() is None or rid in known.get("lines", []):
            return False
        if isinstance(known.get("watch"), dict) and (int(rid) <= known["watch"].get("after", 0)
                                                     or str(known.get("bucket") or self.project) in self.watch_held):
            return False
        try:
            ln = self.bc("api", "get", f"/buckets/{bucket}/chats/{chat}/lines/{rid}.json", project=bucket) or {}
        except RuntimeError as e:
            self.log(f"mention {rid} in chat {chat}: {e}")
            return False
        if not self.hears(ln.get("creator"), bucket=bucket):
            self.block(ln.get("creator"), bucket, chat, rid, ln.get("created_at"), chat=True, what="chat line")
            return False
        if self.dry:
            self.log(f"dry mention {rid} in chat {chat}: record and acknowledge with {EYES}")
            return False
        state = self.load("chats.json", {})
        rec = state.setdefault(str(chat), {})
        elsewhere = self.elsewhere(bucket, ln)
        if elsewhere:
            rec.update(bucket=int(bucket), project_name=elsewhere["project"]["name"])
        self.record({"kind": "chat-question", "chat": int(chat), "line": rid, "mention": True,
                     "url": ln.get("app_url") or f"https://3.basecamp.com/{self.account}/buckets/{bucket}/chats/{chat}@{rid}",
                     "text": plain(ln.get("content")), "at": ln.get("created_at"), **elsewhere,
                     **self.author(ln.get("creator"), bucket)})
        rec.setdefault("lines", []).append(rid)
        rec.setdefault("ack", []).append([rid, self.read_ack()])
        self.log(f"new owner mention in chat {chat}: {rid}")
        self.acknowledge(f"chat {chat}", rec, bucket)
        self.save_json("chats.json", state)
        return True

    # --- chat watches: one chat in any project of the account, read from a given line on (`sync.py watch`) ---

    def watch(self, url, after_line=None, until=None):
        """Watch the chat at `url` (any project of the account) from line `after_line` on, until `unwatch` or `until` passes.

        The watch lives in chats.json beside the chat's cursor, not in the config: the
        chat's lines are read once now (which proves the agent's login can read it), and
        the cursor starts at `after_line`, else at the line the URL names, else at the
        newest line, so neither the agent's own question nor older lines are relayed.
        From then on the chat-watch behavior and notifications read it like a chat in
        "chats" with every line relayed: each listened-to line after the starting line
        is recorded, and no line at or before it ever is, whatever notification names
        it. A record from another project carries `project`, and `reply` answers there.
        `until` (YYYY-MM-DD, in the people's zone) ends the watch once that day has
        passed. Watching again replaces the watch's options, cursor and boost baseline (the
        boost counts there now, and the ids of the boosts already on its lines).
        A chat in "chats" is refused: the chat inbox reads it already; so is a Ping (a
        circle bucket), which the Ping reader relays, so its lines would be recorded twice.
        A dry run reads and logs only.
        """
        if re.search(r"basecamp(?:api)?\.com/\d+/circles/", str(url)):
            raise ValueError(f"{url!r} is a Ping: the Ping reader relays it (\"pings\"), so it is not watched")
        bucket, chat, line = parse_chat_url(url, self.account)
        if chat in configured_chats(self.cfg):
            raise ValueError(f'chat {chat} is in the config\'s "chats": the chat inbox reads it already')
        if bucket in {str(p.get("bucket")) for p in self.load("pings.json", {}).get("pings", {}).values()}:
            raise ValueError(f"chat {chat} is a Ping: the Ping reader relays it (\"pings\"), so it is not watched")
        if until is not None:
            ends = watch_until(until)
            if ends is None:
                raise ValueError(f"--until {until!r} is not a date (YYYY-MM-DD)")
            if ends < self.local_today():
                raise ValueError(f"--until {until} has already passed")
        lines = self.bc("api", "get", f"/buckets/{bucket}/chats/{chat}/lines.json", project=bucket) or []
        if any((ln.get("bucket") or {}).get("type") == "Circle" for ln in lines):
            raise ValueError(f"chat {chat} is a Ping: the Ping reader relays it (\"pings\"), so it is not watched")
        start = int(after_line if after_line is not None else line if line is not None else
                    max([ln.get("id", 0) for ln in lines] + [0]))
        here = bucket == str(self.project)
        name = next(filter(None, ((ln.get("bucket") or {}).get("name") for ln in lines)), None)
        what = (f"chat {chat}" + ("" if here else f" in project {name + ' ' if name else ''}({bucket})")
                + f" from line {start}" + (f", until {until}" if until else ""))
        if self.dry:
            self.log(f"dry watch: would watch {what}")
            return False
        state = self.load("chats.json", {})
        rec = state.setdefault(chat, {})
        if not here:
            rec.update(bucket=int(bucket), **({"project_name": name} if name else {}))
        rec["cursor"] = start
        rec["boost_counts"] = {str(rid): count for rid, _, count, _ in boost_items(lines)}  # only boosts after this relay
        rec["boost_seen"] = sorted(set(rec.get("boost_seen") or []) | {
            b.get("id") for rid, _, count, _ in boost_items(lines) if count
            for b in self.bc("api", "get", f"/buckets/{bucket}/recordings/{rid}/boosts.json", project=bucket) or []})
        rec["watch"] = {"after": start, "until": until, "since": now_iso()}
        self.save_json("chats.json", state)
        self.log(f"watch: watching {what}")
        return True

    def unwatch(self, url):
        """Stop watching the chat at `url`; its recorded lines stay, so `reply` still answers them. A dry run logs only."""
        _, chat, _ = parse_chat_url(url, self.account)
        state = self.load("chats.json", {})
        if "watch" not in state.get(chat, {}):
            self.log(f"unwatch: chat {chat} is not watched")
            return False
        if self.dry:
            self.log(f"dry unwatch: would stop watching chat {chat}")
            return False
        state[chat].pop("watch")
        self.save_json("chats.json", state)
        self.log(f"unwatch: stopped watching chat {chat}")
        return True

    def watches(self, held=False):
        """The chats watched now, as {chat id: its chats.json record}; a watch whose "until" day has passed is left out,
        and so, unless `held`, is one in a project another home on this login reads this run (`watch_held`). A watch
        whose stored "until" is not a date is left out too, never raising, so it cannot stop a run; end_watches ends it."""
        today = None
        out = {}
        for chat, rec in self.load("chats.json", {}).items():
            w = rec.get("watch")
            if not isinstance(w, dict):
                continue
            if w.get("until") is not None:
                ends = watch_until(w["until"])
                if ends is None:
                    continue
                today = today or self.local_today()
                if ends < today:
                    continue
            if not held and str(rec.get("bucket") or self.project) in self.watch_held:
                continue
            out[chat] = rec
        return out

    def end_watches(self):
        """Drop each watch whose "until" day has passed, or whose stored "until" is not a date, from chats.json, logged;
        the chat's cursor and lines stay. A dry run only logs."""
        state, live = self.load("chats.json", {}), self.watches(held=True)
        ended = [c for c, r in state.items() if isinstance(r.get("watch"), dict) and c not in live]
        for chat in ended:
            until = state[chat]["watch"].get("until")
            self.log(("dry " if self.dry else "") + f"watch: chat {chat} no longer watched: " +
                     (f"{until} has passed" if watch_until(until) is not None else
                      f"its until {until!r} is not a date (YYYY-MM-DD); watch it again with a valid --until"))
            state[chat].pop("watch")
        if not ended or self.dry:
            return ended
        self.save_json("chats.json", state)
        return ended

    def local_today(self):
        """Today's date in the people's zone."""
        return datetime.now(self.zone()).date()

    def read_thread(self, bucket, thread, since, mentions=(), ptype=None, title=None, after=None):
        """Record the listened-to people's new comments on recording `thread` (in `bucket`), a thread no other reader
        tracks but the acting user is subscribed to or @mentioned in: a card whatever its backlog state, a document, an
        upload, someone else's message or to-do, a check-in answer.

        threads.json keeps, per "<bucket>:<thread>", a cursor (the newest comment id seen),
        the comments recorded, acknowledgements and replies. A thread's first read starts at
        comment `since` (the notification's first unread comment) or, without one, at ISO
        time `after` (when the notification became unread), and never before the newest
        comment another reader already read there (a decision to-do or to-do request, done
        or not, a mirrored card, the agent's own message), so what was already read is not
        replayed; a comment such a reader recorded or answered is never recorded here
        again. Each new comment by someone listened to is a
        `mention` record when it is in `mentions` (a notification said so) or @mentions the
        acting user, else a `thread-comment`, and is queued for a 👀 when it is a mention or
        contains "?", a 👍 otherwise. `reply` answers either with a comment on the thread.
        `ptype` and `title` name the thread when the comments do not. The owner's boosts on
        its comments (the agent's replies included) are `boost` records with surface
        `thread-comment`. A thread in another project ("mentions": {"scope": "account"}) is
        read the same way, the people heard as in "project_people" for that project, and its
        records carry that `project`. A dry run reads and logs only.
        """
        if self.agent() is None:
            return
        try:
            comments = self.bc("comments", "list", str(thread), project=bucket) or []
        except RuntimeError as e:
            self.log(f"thread {thread}: {e}")
            return
        key = f"{bucket}:{thread}"
        state = self.load("threads.json", {})
        floor, known = self.tracked_comments(bucket, thread)
        new = key not in state
        since = since if since != thread else None  # a notification on the recording itself names no comment
        rec = json.loads(json.dumps(state.get(key) or {"bucket": int(bucket), "thread": thread,
                                                       "cursor": max((since or 1) - 1, floor),
                                                       "parent_type": ptype, "title": title}))
        self.acting_id()
        cursor = rec["cursor"]
        for c in sorted(comments, key=lambda c: c.get("id", 0)):
            cid = c.get("id", 0)
            if cid <= cursor:
                continue
            cursor = cid
            if new and since is None and after and history(c.get("created_at"), after):
                continue  # there before the notification: the thread's history, not new input
            if cid in known:
                continue
            mention = cid in mentions or self.addressed(c.get("content"))
            if not self.hears(c.get("creator"), bucket=bucket):
                if mention:
                    self.block(c.get("creator"), bucket, thread, cid, c.get("created_at"), what="comment")
                continue
            text = plain(c.get("content"))
            on = c.get("parent") or {}
            if self.dry:
                self.log(f"dry thread {thread}: owner {'mention' if mention else 'comment'} {cid}")
                continue
            rec.update(parent_type=on.get("type") or rec.get("parent_type"), title=on.get("title") or rec.get("title"))
            elsewhere = self.elsewhere(bucket, c, rec)
            if elsewhere:
                rec["project_name"] = elsewhere["project"]["name"]
            self.record({"kind": "mention" if mention else "thread-comment", "bucket": int(bucket), "parent": thread,
                         "parent_type": rec.get("parent_type"), "title": rec.get("title"), "comment": cid,
                         "question": "?" in text, "url": c.get("app_url") or on.get("app_url"), "text": text,
                         "at": c.get("created_at"), **elsewhere, **self.author(c.get("creator"), bucket)})
            rec.setdefault("comments", []).append(cid)
            rec.setdefault("ack", []).append([cid, self.read_ack(mention or "?" in text)])
            self.log(f"new owner {'mention' if mention else 'comment'} on {rec.get('parent_type') or 'thread'} {thread}: {cid}")
        self.read_boosts(rec, "thread-comment", {"bucket": int(bucket), "parent": thread, "title": rec.get("title")},
                         bucket=bucket, counted=boost_items(comments))
        self.acknowledge(f"thread {thread}", rec, bucket)
        if not self.dry:
            rec["cursor"] = cursor
            state[key] = rec
            self.save_json("threads.json", state)

    def elsewhere(self, bucket, recording, rec=None):
        """{"project": {"id", "name"}} for a record of `recording` in `bucket` when that is another project of the account
        ("mentions": {"scope": "account"}), its name from the recording (else `rec`'s "project_name"); {} in the project."""
        if str(bucket) == str(self.project):
            return {}
        name = ((recording or {}).get("bucket") or {}).get("name") or (rec or {}).get("project_name")
        return {"project": {"id": int(bucket), "name": name}}

    def tracked_comments(self, bucket, thread):
        """What the other readers already read on recording `thread` in `bucket`: (the newest comment id read, the ids
        of the comments recorded, acknowledged or answered). A decision to-do or to-do request (open, done or closed),
        a mirrored card and the agent's own message each keep their own cursor and lists."""
        recs = [r for r in self.load("todos.json", {}).values()
                if r.get("todo") == thread and str(r.get("bucket") or self.project) == str(bucket)]
        if str(bucket) == str(self.project):
            recs += [r for r in self.load("map.json", {}).values() if r.get("card") == thread]
            recs += [p for p in [self.load("messages.json", {}).get("posts", {}).get(str(thread))] if p]
        known = {x[0] if isinstance(x, list) else x
                 for r in recs for f in ("comments", "replied", "ack", "acked") for x in r.get(f) or []}
        return max([r.get("cursor") or 0 for r in recs] + [x for x in known if isinstance(x, int)] + [0]), known

    def record_unmonitored(self, kind, rtype, rid, who, at=None, rec=None, on=None, bucket=None, ref=None):
        """Record owner input no behavior handles as an `unmonitored` record, once per kind of thing.

        The key is "<kind>/<recording type>", in the event feed's words so keys recorded
        before notifications replaced it still apply: `kind` is what happened
        (comment.created, chat.line.created, boost.created, todo.assignment_changed,
        todo.completed) and the type is, for a comment, the type of what it is on, e.g.
        "comment.created/Document". unmonitored.json keeps each key with the first
        notification (or boost) recorded, how many were seen and, once the agent marks it
        handled, the decision; a key already there is only counted, so the owner is asked
        once. `rtype` None means recording `rid`'s own type, refetched; otherwise the
        recording (or `rec`, already fetched) is read only for a new key, for its title,
        excerpt and link, and a failed read still records what is known. `who` is the
        person, `on` the parent a comment is on, `bucket` where `rid` is (default the
        project) and `ref` the notification or boost id. A dry run records and saves
        nothing. Returns the key when recorded.
        """
        state = self.load("unmonitored.json", {})
        if rtype is None or f"{kind}/{rtype}" not in state:
            if rec is None:
                errors = self.errors
                try:
                    rec = self.recording(rid, bucket)
                except RuntimeError as e:
                    self.log(f"unmonitored: recording {rid}: {e}")
                    rec, self.errors = {}, errors  # recorded anyway, so the caller has nothing to retry
            rtype = rtype or recording_type(rec.get("type")) or "unknown"
        key = f"{kind}/{rtype}"
        if key in state:
            if not self.dry:
                state[key]["seen"] = state[key].get("seen", 1) + 1
                self.save_json("unmonitored.json", state)
            return None
        if self.dry:
            self.log(f"dry unmonitored: {key} ({ref})")
            return None
        title = (on or {}).get("title") or rec.get("title") or rec.get("subject")
        texts = (plain(rec.get(f)) for f in ("content", "description"))  # a to-do's content is its name; its notes are the description
        text = next((x for x in texts if x and x != title), "")
        who = {"id": (who or {}).get("id"), "name": (who or {}).get("name") or (rec.get("creator") or {}).get("name")}
        self.record({"kind": "unmonitored", "key": key, "event_type": kind, "recording_type": rtype,
                     "notification": ref, "recording": rid, "title": title,
                     "text": text[:300] + ("..." if len(text) > 300 else ""), "creator": who, **self.author(who),
                     "url": rec.get("app_url") or (on or {}).get("app_url"), "at": at})
        state[key] = {"notification": ref, "recording": rid, "recorded_at": now_iso(), "seen": 1}
        self.save_json("unmonitored.json", state)
        self.log(f"unmonitored owner input: {key} ({ref})")
        return key

    # --- commands: explicit posts, run by the agent ---

    def reply(self, rid, text, again=False):
        """Answer the captain's comment or line `rid` where it was made, then take the acting user's 👀 off it.

        A card comment is answered with a comment on that card, a chat or Ping line with
        a new line in that chat or Ping, and a to-do, message or followed thread's comment (or
        mention) with a comment on that to-do, message or thread (as Markdown, rendered by the CLI). Run only by the relaying agent; the sync
        itself never posts comments. A reply is recorded in the "replied" list and a
        second one is refused unless `again`. A failed post removes nothing, so the
        👀 stays.
        """
        ping = next((c for c, r in self.load("pings.json", {}).get("pings", {}).items() if rid in r.get("lines", [])), None)
        if ping is not None:
            return self.reply_ping(ping, rid, text, again)
        chats = self.load("chats.json", {})
        chat = next((c for c, r in chats.items() if rid in r.get("lines", [])), None)
        todo_key = None
        if chat is None:
            todo_key = next((k for k, r in self.load("todos.json", {}).items() if rid in r.get("comments", [])), None)
        if todo_key is not None:
            return self.reply_todo(todo_key, rid, text, again)
        post = None
        if chat is None:
            post = next((k for k, r in self.load("messages.json", {}).get("posts", {}).items() if rid in r.get("comments", [])), None)
        if post is not None:
            return self.reply_message(post, rid, text, again)
        thread = None if chat is not None else next(
            (k for k, r in self.load("threads.json", {}).items() if rid in r.get("comments", [])), None)
        if thread is not None:
            return self.reply_thread(thread, rid, text, again)
        bucket = None
        if chat is not None:
            store, rec = ("chats.json", chats), chats[chat]
            bucket = rec.get("bucket")  # a mention in another project's chat ("mentions": {"scope": "account"})
            target = f"/buckets/{bucket or self.project}/chats/{chat}/lines.json"
        else:
            cards = self.load("map.json", {})
            rec = next((r for r in cards.values() if rid in r.get("comments", [])), None)
            if rec is None:
                raise RuntimeError(f"no card in map.json, chat in chats.json, Ping in pings.json, to-do in todos.json, "
                                   f"message in messages.json or thread in threads.json has {rid}")
            store = ("map.json", cards)
            target = f"/buckets/{self.project}/recordings/{rec['card']}/comments.json"
        where = f"chat {chat}" if chat is not None else f"card {rec['card']}"
        if rid in rec.get("replied", []) and not again:
            self.log(f"reply {rid}: already replied, pass --again to post another")
            return False
        if self.refused(f"reply {rid}"):
            return False
        body = render_reply(text)
        if self.dry:
            self.log(f"dry reply {rid}: post in {where}, then remove {EYES}")
            return False
        payload = {"content": body, "content_type": "text/html"} if chat is not None else {"content": body}
        self.bc("api", "post", target, "-d", json.dumps(payload), project=bucket)
        rec.setdefault("replied", []).append(rid)
        self.save_json(*store)
        self.log(f"reply {rid}: posted in {where}")
        self.remove_eyes(rid, bucket)
        return True

    def reply_ping(self, chat, rid, text, again):
        """Answer the owner's line `rid` in Ping chat `chat` with a new line there, in the Ping's own bucket."""
        rec = self.load("pings.json", {})["pings"][chat]
        if rid in rec.get("replied", []) and not again:
            self.log(f"reply {rid}: already replied, pass --again to post another")
            return False
        if self.refused(f"reply {rid}"):
            return False
        bucket = rec["bucket"]
        if self.dry:
            self.log(f"dry reply {rid}: post in ping {chat}, then remove {EYES}")
            return False
        self.bc("api", "post", f"/buckets/{bucket}/chats/{chat}/lines.json",
                "-d", json.dumps({"content": render_reply(text), "content_type": "text/html"}))
        state = self.load("pings.json", {})
        state["pings"][chat].setdefault("replied", []).append(rid)
        self.save_json("pings.json", state)
        self.log(f"reply {rid}: posted in ping {chat}")
        self.remove_eyes(rid, bucket)
        return True

    def reply_todo(self, key, rid, text, again):
        with self.locked("todos.json"):
            rec = self.load("todos.json", {})[key]
            if rid in rec.get("replied", []) and not again:
                self.log(f"reply {rid}: already replied, pass --again to post another")
                return False
        if self.refused(f"reply {rid}"):
            return False
        if self.dry:
            self.log(f"dry reply {rid}: comment on todo {key}, then remove {EYES}")
            return False
        self.bc("comments", "create", str(rec["todo"]), "-", input=text, project=rec.get("bucket"))
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            state[key].setdefault("replied", []).append(rid)
            self.save_json("todos.json", state)
        self.log(f"reply {rid}: posted on todo {key}")
        self.remove_eyes(rid, rec.get("bucket"))
        return True

    def reply_message(self, mid, rid, text, again):
        """Answer the owner's comment `rid` on message `mid` with a comment there (Markdown)."""
        with self.locked("messages.json"):
            rec = self.load("messages.json", {})["posts"][mid]
        if rid in rec.get("replied", []) and not again:
            self.log(f"reply {rid}: already replied, pass --again to post another")
            return False
        if self.refused(f"reply {rid}"):
            return False
        if self.dry:
            self.log(f"dry reply {rid}: comment on message {mid}, then remove {EYES}")
            return False
        self.bc("comments", "create", mid, "-", input=text)
        with self.locked("messages.json"):
            state = self.load("messages.json", {})
            state["posts"][mid].setdefault("replied", []).append(rid)
            self.save_json("messages.json", state)
        self.log(f"reply {rid}: posted on message {mid}")
        self.remove_eyes(rid)
        return True

    def reply_thread(self, key, rid, text, again):
        """Answer the owner's comment `rid` on thread `key` (threads.json) with a comment (Markdown) there, in its bucket."""
        rec = self.load("threads.json", {})[key]
        if rid in rec.get("replied", []) and not again:
            self.log(f"reply {rid}: already replied, pass --again to post another")
            return False
        if self.refused(f"reply {rid}"):
            return False
        where = f"{rec.get('parent_type') or 'thread'} {rec['thread']}"
        if self.dry:
            self.log(f"dry reply {rid}: comment on {where}, then remove {EYES}")
            return False
        self.bc("comments", "create", str(rec["thread"]), "-", input=text, project=rec.get("bucket"))
        state = self.load("threads.json", {})
        state[key].setdefault("replied", []).append(rid)
        self.save_json("threads.json", state)
        self.log(f"reply {rid}: posted on {where}")
        self.remove_eyes(rid, rec.get("bucket"))
        return True

    def remove_eyes(self, rid, bucket=None):
        """Take the acting user's read acknowledgement (👀, or the config's "acknowledge" "read") off `rid`."""
        emoji = self.read_ack()
        for b in self.boosts_by(self.acting_id(), rid, emoji, bucket):
            self.bc("api", "delete", f"/buckets/{bucket or self.project}/boosts/{b.get('id')}.json")
            self.log(f"reply {rid}: removed {emoji} boost {b.get('id')}")

    def acknowledge_decided(self, key):
        """With "acknowledge" "decided" set, boost each deciding comment on tracked `key` (the captain's or an operator's
        comment on a decision to-do or a to-do or card a decision step is on) with it, once, and take the read
        acknowledgement off: the decision was carried out. Run by `todo complete` and `todo subtask`."""
        if not self.ack_decided:
            return
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            rec = state.get(key) or {}
            done = [a[0] for a in rec.get("acked", []) if a[1:2] == [self.ack_decided]]
            todo = [cid for cid in rec.get("deciding", []) if cid not in done]
            if not todo:
                return
            for cid in todo:
                if [cid, self.ack_decided] not in rec.setdefault("ack", []):
                    rec["ack"].append([cid, self.ack_decided])
            self.acknowledge(f"todo {key}", rec, rec.get("bucket"))
            self.save_json("todos.json", state)
        for a in rec.get("acked", []):
            if a[1:2] == [self.ack_decided] and a[0] in todo and self.ack_decided != self.read_ack():
                try:
                    self.remove_eyes(a[0], rec.get("bucket"))
                except RuntimeError as e:
                    self.log(f"todo {key}: removing the read acknowledgement from {a[0]} failed: {e}")

    def owner_mention(self):
        """The owner's mention attachment, from their person record's sgid."""
        me = self.bc("api", "get", f"/people/{self.captain}.json") or {}
        if not me.get("attachable_sgid"):
            raise RuntimeError(f"person {self.captain} has no attachable_sgid")
        return f'<bc-attachment sgid="{html.escape(me["attachable_sgid"])}" content-type="application/vnd.basecamp.mention"></bc-attachment>'

    def ask(self, text):
        """Post `text` as a new line in the "ask_chat" chat, @mentioning the owner.

        Run only by the relaying agent; the sync's only own chat post is the session alert. Refused,
        like `reply`, when no profile is set or it signs in as the owner.
        """
        if self.ask_chat is None:
            raise RuntimeError('config has no "ask_chat"')
        if self.refused("ask", "owner"):
            return False
        if self.dry:
            self.log(f"dry ask: post in chat {self.ask_chat}")
            return False
        body = render_reply(text)
        body = (body.replace("<div>", "<div>" + self.owner_mention() + " ", 1) if body.startswith("<div>")
                else "<div>" + self.owner_mention() + "</div>" + body)
        line = self.bc("api", "post", f"/buckets/{self.project}/chats/{self.ask_chat}/lines.json",
                       "-d", json.dumps({"content": body, "content_type": "text/html"})) or {}
        self.log(f"ask: posted line {line.get('id')} in chat {self.ask_chat}")
        return True

    def answer(self, qid, text):
        """Answer check-in question `qid` for today as the acting user, at most once a day.

        Run only by the relaying agent; the sync itself never answers. Refused, like
        `reply`, when no profile is set or it signs in as the owner, and when the
        acting user already answered today (recorded in checkins.json or in Basecamp).
        """
        if not self.checkins:
            raise RuntimeError('config has no "checkins"')
        date = self.today().strftime("%Y-%m-%d")
        state = self.load("checkins.json", {})
        rec = state.get(str(qid), {})
        if date in rec.get("answered", []):
            self.log(f"answer {qid}: already answered {date}")
            return False
        if self.refused(f"answer {qid}", "owner"):
            return False
        if self.answered_today(qid, self.acting_id(), date):
            self.log(f"answer {qid}: already answered {date}")
            return False
        if self.dry:
            self.log(f"dry answer {qid}: answer for {date}")
            return False
        self.bc("checkins", "answer", "create", str(qid), render_reply(text), "--date", date)
        rec.setdefault("answered", []).append(date)
        state[str(qid)] = rec
        self.save_json("checkins.json", state)
        self.log(f"answer {qid}: answered for {date}")
        return True

    def need_todos(self, requests=True):
        """Refuse a to-do command the config does not allow: "todos" for any, or "assigned_todos" (or "assigned_cards")
        for a tracked request."""
        if self.todos is None and not (requests and (self.assigned_todos or self.assigned_cards)):
            raise RuntimeError('config has no "todos"' + (' or "assigned_todos"' if requests else ""))

    def todo_create(self, key, title, description="", todolist=None, due=None, subtasks=(), project=None, card=None):
        """Create a to-do (or a card) assigned to the owner and track it in todos.json under `key`.

        `subtasks` are the decisions when the to-do carries more than one, every one
        included: each becomes a native Basecamp subtask of the to-do (`basecamp
        subtasks`, CLI 0.12.0+), unassigned so the owner is notified once (by the parent), kept in the record in order,
        each completed by `todo_subtask`.

        It goes in `project` (any project of the account; default "todos": {"project": id}, else
        the configured project): loose on the configured to-do set ("todos": {"todoset": id},
        or the project's only one), or into a list (`todolist`, else "todos": {"list": id}).
        The configured to-do set and list belong to the configured decisions project, so
        another `project` takes only `todolist`. `card` ((card table id, column id)) makes it a
        card in that column instead, assigned to the owner and tracked with "type": "card";
        completing it moves it to the table's Done column. The record keeps its project
        (`bucket`), so its comments, replies and completion go there. The description is
        Markdown, rendered by the CLI. A key already tracked is refused, so a re-run never
        creates a duplicate. Returns the to-do (or card) id, or None when nothing was created.
        """
        self.need_todos(requests=False)
        if not key or not title.strip():
            raise ValueError("a to-do needs a key and a title")
        if title.startswith("-"):
            raise ValueError("a to-do title cannot start with '-'")
        subtasks = [{"text": q.strip(), "done": None} for q in subtasks]
        if any(not q["text"] for q in subtasks):
            raise ValueError("a subtask needs text")
        if len(subtasks) == 1:
            raise ValueError("one decision needs no subtask: pass every decision as a subtask, or none")
        if card and (subtasks or todolist or due):
            raise ValueError("a card takes no subtasks, list or due date")
        with self.locked("todos.json"):
            have = self.load("todos.json", {}).get(key)
        if have is not None:
            self.log(f"todo create {key}: already tracked as todo {have['todo']} {have.get('url') or ''}".rstrip())
            return None
        if self.refused(f"todo create {key}", "owner"):
            return None
        home = str(self.todos.get("project") or self.project)  # where the configured to-do set and list are
        bucket = str(project or home)
        todolist = todolist or (self.todos.get("list") if bucket == home else None)
        todoset = self.todos.get("todoset") if bucket == home else None
        where = (["--card-table", str(card[0]), "--column", str(card[1])] if card else
                 ["--list", str(todolist)] if todolist else ["--loose"] + (["--todoset", str(todoset)] if todoset else []))
        if self.dry:
            self.log(f"dry todo create {key}: {' '.join(where)} in project {bucket}, assigned to {self.captain}")
            return None
        if card:
            args = ["cards", "create", title.strip(), description.strip(), *where, "--assignee", str(self.captain)]
        else:
            args = ["todos", "create", title.strip(), *where, "--assignee", str(self.captain)]
            if description.strip():
                args.append(f"--description={description.strip()}")
            if due:
                args += ["--due", due]
        data = self.bc(*args, project=bucket) or {}
        tid = data["id"]
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            state[key] = {"todo": tid, "title": title.strip(), "url": data.get("app_url"), "cursor": 0,
                          "boost_counts": {}, "boost_seen": [], "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          **({"bucket": int(bucket)} if bucket != str(self.project) else {}),
                          **({"type": "card"} if card else {})}
            self.save_json("todos.json", state)
        self.log(f"todo create {key}: {'card' if card else 'todo'} {tid} in project {bucket} {data.get('app_url') or ''}".rstrip())
        for n, q in enumerate(subtasks, 1):  # recorded one by one, so the record matches Basecamp if one fails
            sub = self.bc("subtasks", "create", str(tid), q["text"], project=bucket) or {}
            with self.locked("todos.json"):
                state = self.load("todos.json", {})
                state[key].setdefault("subtasks", []).append(dict(q, id=sub["id"]))
                self.save_json("todos.json", state)
            self.log(f"todo create {key}: subtask {n} {sub['id']}")
        return tid

    def timing(self, todo):
        """When scheduled request `todo` is done by default: {"when": "ask" | "due" | "before", "list": its list (or
        card column)}. "recurring" applies to a repeating one, "dated" to one with only a due date: the answer remembered
        for its list (schedule.json "lists"), else the remembered default (schedule.json "default"), else the config's
        "schedule". {} for an undated to-do, or when nothing is set (the home's defaults then say)."""
        sched = todo_schedule(todo)
        if not sched["due_on"] and not sched["repeats"]:
            return {}
        which = "recurring" if sched["repeats"] else "dated"
        lst = (todo.get("parent") or {}).get("id")
        mem = self.load("schedule.json", {})
        when = (((mem.get("lists") or {}).get(str(lst)) or {}).get(which) or (mem.get("default") or {}).get(which)
                or self.schedule.get(which))
        return {"when": when, "list": lst} if when else {}

    def schedule_set(self, which, when, todolist=None):
        """Remember when `which` ("dated" or "recurring") requests are done: `when` ("ask", "due" or "before") for
        to-do list (or card column) `todolist`, or for every list without one of its own. Local state only
        (schedule.json); the config's "schedule" stays the shipped default."""
        if which not in ("dated", "recurring") or when not in WHEN:
            raise ValueError(f"schedule: {which!r} must be dated or recurring, and {when!r} one of {', '.join(WHEN)}")
        state = self.load("schedule.json", {})
        if self.dry:
            self.log(f"dry schedule: {which} requests {'in list ' + str(todolist) if todolist else 'by default'}: {when}")
            return
        target = state.setdefault("lists", {}).setdefault(str(todolist), {}) if todolist else state.setdefault("default", {})
        target[which] = when
        self.save_json("schedule.json", state)
        self.log(f"schedule: {which} requests {'in list ' + str(todolist) if todolist else 'by default'}: {when}")

    def decide(self, on, question, comment, key=None, project=None, decider=None):
        """Put a decision to `decider` (default the captain) on the to-do or card it is about: a comment there (`comment`,
        Markdown) and a step on it (a native subtask asking `question`) assigned to them.

        `on` is the to-do's or card's id or Basecamp URL, in `project` (default the URL's, else
        the configured project). The decider must be the captain or an operator: only their
        word decides. When something is already tracked there (a to-do request, a card
        request, a decision to-do), the step joins that record, under its key; otherwise a new
        record under `key` tracks the to-do or card ("on": true), and its comments and boosts
        from then on are relayed as `todo-comment` and `boost` records. Either way the step's
        checking by anyone but the acting user is recorded as `decision-step-done`. `todo
        subtask` completes the step once the decision is carried out; on an "on" record
        nothing ever completes the to-do or card itself, which is not the agent's. Returns
        the key, or None when nothing was posted.
        """
        self.need_todos(requests=False)
        if not question.strip() or not comment.strip():
            raise ValueError("a decision step needs a question and a comment")
        m = re.search(r"/buckets/(\d+)/", str(on))
        bucket = str(project or (m.group(1) if m else self.project))
        ids = re.findall(r"(?:^|/)(\d+)(?=$|[/?#.])", str(on).split("#")[0])
        if not ids:
            raise ValueError(f"{on!r} is not a to-do or card id or URL")
        rid = int(ids[-1])
        decider = int(decider or self.captain)
        if self.role({"id": decider}, admitted=False, bucket=bucket) not in AUTHORITY:
            raise ValueError(f"person {decider} is neither the captain nor an operator, so their word cannot decide")
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
        tracked = next((k for k, r in state.items() if r.get("todo") == rid and str(r.get("bucket") or self.project) == bucket
                        and not r.get("completed") and not r.get("closed")), None)
        if tracked is None and not key:
            raise ValueError(f"{rid} is not tracked yet, so the decision needs a --key")
        if tracked is None and key in state:
            raise ValueError(f"key {key} already tracks to-do {state[key]['todo']}")
        key = tracked or key
        if self.refused(f"decide {key}", "owner"):
            return None
        thing = self.recording(rid, bucket)
        if thing.get("type") not in ("Todo", "Kanban::Card"):
            raise ValueError(f"{rid} is a {thing.get('type') or 'missing recording'}, not a to-do or card")
        if self.dry:
            self.log(f"dry decide {key}: comment on and add a step for {decider} to {rid} in project {bucket}")
            return None
        if tracked is None:
            comments = self.bc("comments", "list", str(rid), project=bucket) or []
            rec = {"todo": rid, "title": thing.get("title") or thing.get("content"), "url": thing.get("app_url"), "on": True,
                   "cursor": max([c.get("id", 0) for c in comments], default=0), "boost_counts": {}, "boost_seen": [],
                   "created": now_iso(), **({"bucket": int(bucket)} if bucket != str(self.project) else {}),
                   **({"type": "card"} if thing.get("type") == "Kanban::Card" else {})}
            with self.locked("todos.json"):
                state = self.load("todos.json", {})
                state[key] = rec
                self.save_json("todos.json", state)
        c = self.bc("comments", "create", str(rid), "-", input=comment, project=bucket) or {}
        sub = self.bc("subtasks", "create", str(rid), question.strip(), "--assignees", str(decider), project=bucket) or {}
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            rec = state[key]
            rec["cursor"] = max(rec.get("cursor") or 0, c.get("id") or 0)
            rec.setdefault("subtasks", []).append({"text": question.strip(), "done": None, "id": sub["id"], "decider": decider})
            self.save_json("todos.json", state)
        self.log(f"decide {key}: comment {c.get('id')} and step {sub['id']} for {decider} on {rid} in project {bucket}")
        return key

    def todo_track(self, key, tid, project=None):
        """Track an existing to-do (in `project`, default the configured one) under `key`; its comments and boosts so far
        are not relayed. Writes nothing to Basecamp."""
        self.need_todos(requests=False)
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            if key in state or any(r["todo"] == tid for r in state.values()):
                self.log(f"todo track {key}: key or todo {tid} already tracked")
                return False
        bucket = str(project or self.project)
        todo = self.bc("api", "get", f"/buckets/{bucket}/todos/{tid}.json", project=bucket) or {}
        comments = self.bc("comments", "list", str(tid), project=bucket) or []
        cursor = max([c.get("id", 0) for c in comments], default=0)
        if self.dry:
            self.log(f"dry todo track {key}: todo {tid}, cursor {cursor}")
            return False
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            state[key] = {"todo": tid, "title": todo.get("content"), "url": todo.get("app_url"), "cursor": cursor,
                          "created": todo.get("created_at"),  # no boost state: the first read seeds it
                          **({"bucket": int(bucket)} if bucket != str(self.project) else {})}
            if todo.get("completed"):
                state[key]["completed"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save_json("todos.json", state)
        self.log(f"todo track {key}: todo {tid}, cursor {cursor}")
        return True

    def tracked(self, ref):
        """The (key, record) of a tracked to-do, by key or by to-do id."""
        state = self.load("todos.json", {})
        if ref in state:
            return ref, state[ref]
        hit = next(((k, r) for k, r in state.items() if str(r["todo"]) == str(ref)), None)
        if hit is None:
            raise RuntimeError(f"no tracked to-do {ref} in todos.json")
        return hit

    def todo_comment(self, ref, text):
        """Post `text` (Markdown) as a comment on a tracked to-do."""
        self.need_todos()
        key, rec = self.tracked(ref)
        if not text.strip():
            raise ValueError("empty comment")
        if self.refused(f"todo comment {key}", "owner"):
            return False
        if self.dry:
            self.log(f"dry todo comment {key}: comment on todo {rec['todo']}")
            return False
        c = self.bc("comments", "create", str(rec["todo"]), "-", input=text, project=rec.get("bucket")) or {}
        self.log(f"todo comment {key}: comment {c.get('id')} on todo {rec['todo']}")
        return True

    def todo_complete(self, ref):
        """Mark a tracked to-do complete; its comments are no longer read. A card request is completed
        by moving the card to its table's Done column (a card already done is recorded completed)."""
        self.need_todos()
        key, rec = self.tracked(ref)
        if rec.get("completed"):
            self.log(f"todo complete {key}: already completed {rec['completed']}")
            return False
        if rec.get("closed"):
            self.log(f"todo complete {key}: the request was closed ({rec['closed']}), not completing it")
            return False
        if self.refused(f"todo complete {key}", "owner"):
            return False
        if self.dry:
            self.log(f"dry todo complete {key}: complete todo {rec['todo']}")
            return False
        if rec.get("on"):  # a to-do or card that is not the agent's (decide): only its own open steps are completed
            for q in rec.get("subtasks") or []:
                if q.get("decider") and not q.get("done"):
                    self.bc("subtasks", "complete", str(q["id"]), project=rec.get("bucket"))
        elif rec.get("type") == "card":
            bucket = rec.get("bucket") or self.project
            c = self.bc("api", "get", f"/buckets/{bucket}/card_tables/cards/{rec['todo']}.json", project=bucket) or {}
            if c.get("completed"):
                self.log(f"todo complete {key}: card {rec['todo']} is already done in Basecamp")
            else:
                self.bc("cards", "done", str(rec["todo"]), project=bucket)
        else:
            self.bc("todos", "complete", str(rec["todo"]), project=rec.get("bucket"))
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            state[key]["completed"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save_json("todos.json", state)
        self.log(f"todo complete {key}: completed todo {rec['todo']}")
        self.acknowledge_decided(key)
        return True

    def todo_subtask(self, ref, number):
        """Complete subtask `number` (from 1) of a tracked to-do, answered, as Basecamp's native subtask (a decision step
        `decide` added included); a decision to-do is completed once every subtask is, never a request or a to-do or card
        the decision was only put on (that record just stops being tracked)."""
        self.need_todos()
        key, rec = self.tracked(ref)
        subtasks = rec.get("subtasks") or []
        if not 1 <= number <= len(subtasks):
            raise ValueError(f"to-do {key} has no subtask {number} (it has {len(subtasks)})")
        if subtasks[number - 1]["done"]:
            self.log(f"todo subtask {key} {number}: already done {subtasks[number - 1]['done']}")
            return False
        if self.refused(f"todo subtask {key} {number}", "owner"):
            return False
        if self.dry:
            self.log(f"dry todo subtask {key} {number}: complete subtask {subtasks[number - 1]['id']} on todo {rec['todo']}")
            return False
        subtasks = [dict(q) for q in subtasks]
        subtasks[number - 1]["done"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.bc("subtasks", "complete", str(subtasks[number - 1]["id"]), project=rec.get("bucket"))
        with self.locked("todos.json"):
            state = self.load("todos.json", {})
            state[key]["subtasks"] = subtasks
            self.save_json("todos.json", state)
        left = sum(not q["done"] for q in subtasks)
        self.log(f"todo subtask {key} {number}: done, {left} left on todo {rec['todo']}")
        if not left and not rec.get("request"):
            self.todo_complete(key)
        else:
            self.acknowledge_decided(key)
        return True

    def todo_show(self, ref, out=print):
        """Print a tracked to-do as Basecamp has it now, as JSON: whether it is open, its name and description, and its
        due date and repetition rule. Basecamp alone owns a to-do's schedule, so a date read earlier may have moved."""
        self.need_todos()
        key, rec = self.tracked(ref)
        todo = self.todo(rec["todo"], rec.get("bucket"), rec.get("type") == "card")
        me = self.agent()
        out(json.dumps({"key": key, "todo": rec["todo"], "title": todo.get("content"), "text": plain(todo.get("description")),
                        "open": not todo.get("completed") and todo.get("status") in (None, "active"),
                        "assigned_to_you": me is not None and me in [p.get("id") for p in todo.get("assignees") or []],
                        **todo_schedule(todo), **self.timing(todo), "url": todo.get("app_url"),
                        **({"subtasks": rec["subtasks"]} if rec.get("subtasks") else {})}, indent=1))
        return True

    def post_message(self, subject, body):
        """Post a message (`body` as Markdown, rendered by the CLI) on the configured "message_board"."""
        if self.message_board is None:
            raise RuntimeError('config has no "message_board"')
        if not subject.strip() or subject.startswith("-"):
            raise ValueError("a message needs a subject that does not start with '-'")
        if self.refused("post-message", "owner"):
            return False
        if self.dry:
            self.log(f"dry post-message: {subject.strip()!r} on board {self.message_board}")
            return False
        m = self.bc("messages", "create", subject.strip(), "-", "--message-board", self.message_board, input=body) or {}
        self.log(f"post-message: message {m.get('id')} {m.get('app_url') or ''}".rstrip())
        return True

    # --- timesheet: the work the agent counts its active time on (timesheet.json) ---

    def timesheet_start(self, key, on=None, workdirs=()):
        """Count the agent's active time on `key` from now, in the Claude Code transcripts of `workdirs`.

        `on` (a to-do's or card's URL) is the recording its timesheet entries go on; it is
        read once to check it is a to-do or a card. Without it, `key` is looked up when
        entries are posted: a to-do tracked under it (todos.json), else its mirrored card
        (map.json). A workdir is the directory the work's Claude Code session runs in; its
        transcripts are in CLAUDE_CONFIG_DIR (default ~/.claude)/projects/<the path with each
        character that is not a letter or digit as ->. A repeat for a source already counted
        adds nothing; a new one, or a stopped one, starts counting from now. Writes nothing to
        Basecamp.
        """
        sources = [d for w in workdirs for d in claude_transcript_dirs(w)]
        if not str(key).strip():
            raise ValueError("a timesheet key is needed")
        if not sources:
            raise ValueError("give --workdir: the directory the work's Claude Code session runs in")
        target = None
        if on is not None:
            bucket, rid = parse_recording(on, self.account)
            rec = self.recording(rid, bucket)
            if rec.get("type") not in ("Todo", "Kanban::Card"):
                raise ValueError(f"{on!r} is a {rec.get('type') or 'recording'}, not a to-do or card")
            target = {"bucket": int(bucket), "recording": rid, "type": rec["type"],
                      "title": rec.get("title") or rec.get("content"), "url": rec.get("app_url")}
        if self.dry:
            self.log(f"dry timesheet start {key}: " + ", ".join(sorted(set(sources)))
                     + (f" on {target['url'] or target['recording']}" if target else ""))
            return False
        with self.locked("timesheet.json"):
            state = self.load("timesheet.json", {})
            item = state.setdefault("items", {}).setdefault(key, {"sources": []})
            if target:
                item["on"] = target
            item.pop("settled", None)
            added = []
            for src in dict.fromkeys(sources):
                if not any(s["path"] == src and not s.get("to") for s in item["sources"]):
                    item["sources"].append({"path": src, "from": now_iso()})
                    added.append(src)
            self.save_json("timesheet.json", state)
        self.log(f"timesheet start {key}: " + (", ".join(added) if added else "already counting")
                 + (f" on {target['url'] or target['recording']}" if target else ""))
        return True

    def timesheet_stop(self, key):
        """Stop counting the agent's active time on `key`: each source counted ends now. Writes nothing to Basecamp."""
        with self.locked("timesheet.json"):
            state = self.load("timesheet.json", {})
            item = (state.get("items") or {}).get(key)
            if item is None:
                raise ValueError(f"no timesheet key {key!r} in timesheet.json")
            open_ = [s for s in item["sources"] if not s.get("to")]
            if self.dry:
                self.log(f"dry timesheet stop {key}: {len(open_)} source(s)")
                return False
            for s in open_:
                s["to"] = now_iso()
            self.save_json("timesheet.json", state)
        self.log(f"timesheet stop {key}: {len(open_)} source(s) stopped")
        return True

    def timesheet_entries(self, bucket, recording, date, person):
        """The timesheet entries `person` logged on day `date` (YYYY-MM-DD) on to-do or card `recording` in `bucket`."""
        return self.bc("timesheet", "item", str(recording), "--person", str(person), "--start", date, "--end", date,
                       project=bucket) or []

    def timesheet_entry(self, bucket, recording, date, hours, description):
        """Post one timesheet entry (`hours` on day `date`, YYYY-MM-DD) on to-do or card `recording` in `bucket`, tried
        once: a failure may have posted it, so the next run reads the entries before posting again."""
        body = {"date": date, "hours": f"{hours:g}", "description": description}
        return self.bc("api", "post", f"/projects/{bucket}/recordings/{recording}/timesheet/entries.json",
                       "-d", json.dumps(body), project=bucket, tries=1) or {}

    def transcript_events(self, path, since):
        """The events of each Claude Code transcript under directory `path` changed at or after `since`: {session file
        name: ([event time, ...] sorted, [(tool call time, its result's time), ...])}. A missing directory has none.

        Each line with a "timestamp" is an event. A tool wait runs from an assistant line's tool_use to the line with its
        tool_result, across the events between them; a call with no result yet has no wait.
        A session is keyed by its file name, so one seen under two paths (a symlinked home) counts once.
        """
        files = sorted(glob.glob(os.path.join(glob.escape(path), "**", "*.jsonl"), recursive=True))
        out = {}
        for f in files:
            try:
                if datetime.fromtimestamp(os.path.getmtime(f), timezone.utc) < since:
                    continue
                events, calls, results = [], {}, {}
                with open(f, errors="replace") as fh:
                    for ln in fh:
                        try:
                            o = json.loads(ln)
                            at = datetime.fromisoformat(str(o.get("timestamp") or "").replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            continue
                        if at.tzinfo is None:
                            at = at.replace(tzinfo=timezone.utc)
                        content = (o.get("message") or {}).get("content") if isinstance(o.get("message"), dict) else None
                        blocks = [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []
                        for c in blocks:
                            if c.get("type") == "tool_use" and c.get("id"):
                                calls.setdefault(c["id"], at)
                            elif c.get("type") == "tool_result" and c.get("tool_use_id"):
                                results.setdefault(c["tool_use_id"], at)
                        events.append(at)
            except OSError:
                continue
            times, waits = out.setdefault(os.path.basename(f), ([], []))
            times.extend(events)
            waits.extend((at, results[i]) for i, at in calls.items() if i in results)
        return {k: (sorted(times), waits) for k, (times, waits) in out.items()}

    def unmonitored_handle(self, key, decision):
        """Mark unmonitored key `key` handled with the owner's `decision`; it stays quiet until forgotten."""
        state = self.load("unmonitored.json", {})
        if key not in state:
            raise ValueError(f"no unmonitored key {key!r} (see sync.py unmonitored list)")
        if not decision.strip():
            raise ValueError("a decision is needed")
        if self.dry:
            self.log(f"dry unmonitored handle: {key}")
            return
        state[key]["handled"] = {"at": now_iso(), "decision": decision.strip()}
        self.save_json("unmonitored.json", state)
        self.log(f"unmonitored: {key} handled: {decision.strip()}")

    def unmonitored_forget(self, key):
        """Drop unmonitored key `key`, so the next such event is recorded (and put to the owner) again."""
        state = self.load("unmonitored.json", {})
        if key not in state:
            raise ValueError(f"no unmonitored key {key!r} (see sync.py unmonitored list)")
        if self.dry:
            self.log(f"dry unmonitored forget: {key}")
            return
        del state[key]
        self.save_json("unmonitored.json", state)
        self.log(f"unmonitored: {key} forgotten")

    def access(self, out=print):
        """Print who gets through to the agent, and where; everyone else is blocked before it. Reads each person's
        name (and the company's) from Basecamp, falling back to their id when a read fails. Read-only."""
        names = {}

        def name(pid):
            if pid not in names:
                try:
                    names[pid] = (self.bc("api", "get", f"/people/{pid}.json") or {}).get("name") or f"person {pid}"
                except RuntimeError:
                    names[pid] = f"person {pid}"
            return f"{names[pid]} ({pid})"

        def listed(ids):
            return ", ".join(name(p) for p in sorted(ids)) or "none"

        out("Who gets through to the agent (anyone else is blocked before it):")
        out(f"  captain: {name(self.captain)}, always; their word authorizes the agent")
        out(f"  operators (their word counts as the captain's: the trusted group for risky actions): {listed(self.operators)}")
        out(f"  people (participants everywhere this home reads): {listed(self.people - {self.captain} - self.operators)}")
        for bucket, ids in sorted(self.project_people.items()):
            out(f"  people in project {bucket} only (participants there): {listed(ids)}")
        rules = (["anyone on the project who is not a client"] if self.participant_project else []) + (
            [f"anyone with an email at {', '.join(sorted(self.participant_domains))}"] if self.participant_domains else [])
        if self.participant_company is not None:
            company = self.company()
            rules.append(f"anyone in company {company if company is not None else 'unknown (the captain could not be read)'}"
                         + (" (the captain's)" if self.participant_company is True else ""))
        out("  participants by rule (they may ask; never their boosts or assignments): " + ("; ".join(rules) or "none"))
        out("Blocked senders: " + (f"one automatic reply when they address the agent: {self.blocked_reply!r}"
                                   if self.blocked_reply is not None else 'dropped with no reply ("blocked" is off)'))


def request_fields(rec):
    """The fields that mark a record as part of a to-do request: its title and project, for a to-do request's record; none otherwise."""
    return {"request": True, "title": rec.get("title"), "project": request_project(rec),
            **({"type": "card"} if rec.get("type") == "card" else {})} if rec.get("request") else {}


def request_project(rec):
    """The project a to-do request is in, {"id", "name"}; a request tracked before projects were kept has no id (the configured one)."""
    return {"id": rec.get("bucket"), "name": rec.get("project_name")}


def todo_digest(todo):
    """A digest of a to-do's name and description, to notice an edit to either."""
    return hashlib.sha256(f"{todo.get('content') or ''}\n{todo.get('description') or ''}".encode()).hexdigest()


def todo_schedule(todo):
    """A to-do's schedule, for a request: `due_on` (None when undated) and `repeats`, Basecamp's repetition rule without
    its empty fields (None when it does not repeat)."""
    rule = todo.get("repetition_schedule")
    repeats = ({k: v for k, v in rule.items() if v not in (None, [], "")}
               if isinstance(rule, dict) and rule.get("frequency") else None)
    return {"due_on": todo.get("due_on"), "repeats": repeats}


def plain(content):
    """Basecamp rich text as one line of plain text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content or ""))).strip()


def parse_chat_url(url, account):
    """(bucket, chat, line or None) of a Basecamp chat URL in `account`, like
    https://3.basecamp.com/<account>/buckets/<bucket>/chats/<chat>[@<line>]; ValueError otherwise."""
    m = re.search(r"basecamp(?:api)?\.com/(\d+)/buckets/(\d+)/chats/(\d+)(?:@(\d+))?", str(url))
    if not m:
        raise ValueError(f"{url!r} is not a Basecamp chat URL (https://3.basecamp.com/<account>/buckets/<project>/chats/<chat>)")
    if m.group(1) != str(account):
        raise ValueError(f"{url!r} is in account {m.group(1)}, not this home's account {account}")
    return m.group(2), m.group(3), int(m.group(4)) if m.group(4) else None


def parse_recording(on, account):
    """(bucket, recording id) of a to-do's or card's Basecamp URL in `account`."""
    s = str(on).strip()
    m = re.search(r"basecamp(?:api)?\.com/(\d+)/(?:buckets|projects)/(\d+)/(?:[\w/]+/)?(\d+)(?=$|[/?#.])", s)
    if not m:
        raise ValueError(f"{on!r} is not a to-do or card URL "
                         "(https://3.basecamp.com/<account>/buckets/<project>/todos/<id>, or .../card_tables/cards/<id>)")
    if m.group(1) != str(account):
        raise ValueError(f"{on!r} is in account {m.group(1)}, not this home's account {account}")
    return m.group(2), int(m.group(3))


def claude_transcript_dirs(workdir):
    """The directories Claude Code keeps the transcripts of sessions run in `workdir` in: one per spelling of the path
    (as given and with symlinks resolved), each under CLAUDE_CONFIG_DIR (default ~/.claude)/projects/."""
    root = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")
    paths = dict.fromkeys((os.path.abspath(workdir), os.path.realpath(workdir)))
    return [os.path.join(root, re.sub(r"[^A-Za-z0-9]", "-", p)) for p in paths]


def watch_until(until):
    """A watch's "until" (YYYY-MM-DD) as a date; None when it is not one."""
    if not isinstance(until, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
        return None
    try:
        return datetime.strptime(until, "%Y-%m-%d").date()
    except ValueError:
        return None


def configured_chats(cfg):
    """The chat ids (as strings) in the config's "chats", which the chat inbox reads."""
    return {str(c["chat"] if isinstance(c, dict) else c) for c in cfg.get("chats", [])}


def boost_items(recordings):
    """[(id, url, boosts_count, creator id)] of listed recordings (lines, comments, answers, messages), for read_boosts."""
    return [(r.get("id"), r.get("app_url"), r.get("boosts_count") or 0, (r.get("creator") or {}).get("id")) for r in recordings]


def boot_time():
    """When this computer booted (epoch seconds, from /proc/stat's btime), or None where unreadable."""
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


RECENT = 300  # seconds a boost the received-boosts read does not list yet is retried before it is dropped


def recent(at, now=None, within=None):
    """True when ISO time `at` is less than `within` seconds (default RECENT) ago; False when it is unreadable."""
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - when).total_seconds() < (within or RECENT)


def recording_type(t):
    """A recording type as an unmonitored key names it: every kind of chat line is "Chat::Lines"."""
    return "Chat::Lines" if (t or "").startswith("Chat::Lines") else t


# The thread a notification's app_url names, by its path under /buckets/<id>/, as a recording type.
THREADS = {"todos": "Todo", "messages": "Message", "cards": "Kanban::Card", "documents": "Document",
           "uploads": "Upload", "question_answers": "Question::Answer", "questions": "Question",
           "schedule_entries": "Schedule::Entry", "chats": "Chat::Transcript"}


def parse_reading(r):
    """Where a notification (an item of /my/readings.json) points: {"bucket", "thread", "anchor", "path"}.

    The bucket and the thread (the commented recording, the chat, the to-do) come from its
    subscription_url, the anchor (the first unread comment, the mentioning line, the
    to-do) from readable_identifier (base64 of gid://bc3/Recording/<id>), and the path
    (todos, messages, cards, chats, ... or the Ping's circles) from app_url. Any part it
    lacks is None.
    """
    m = re.search(r"/buckets/(\d+)/recordings/(\d+)/", r.get("subscription_url") or "")
    ident = r.get("readable_identifier") or ""
    try:
        gid = base64.b64decode(ident + "=" * (-len(ident) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        gid = ""
    a = re.fullmatch(r"gid://bc3/Recording/(\d+)", gid)
    p = re.search(r"basecamp\.com/\d+/(?:buckets/\d+/(?:card_tables/)?)?([a-z_]+)", r.get("app_url") or "")
    return {"bucket": m.group(1) if m else None, "thread": int(m.group(2)) if m else None,
            "anchor": int(a.group(1)) if a else None, "path": p.group(1) if p else None}


def local_time(at, zone):
    """ISO time `at` (or a datetime; UTC when naive) in `zone`, like "2026-10-21 09:14 CDT"; None when unreadable."""
    if not isinstance(at, datetime) and "T" not in str(at):
        return None  # a bare date has no time of day to move
    try:
        when = at if isinstance(at, datetime) else datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def history(at, since, slack=60):
    """True when ISO time `at` is more than `slack` seconds before ISO time `since`: it was there before, not new
    since. An unreadable time is never history, so nothing is dropped."""
    try:
        a, s = (datetime.fromisoformat(str(x).replace("Z", "+00:00")) for x in (at, since))
        return (s - a).total_seconds() > slack
    except (TypeError, ValueError):
        return False


def later(at, since):
    """True when ISO time `at` is after ISO time `since`; an unreadable `at` counts as later, so nothing is dropped."""
    try:
        return datetime.fromisoformat(at) > datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return True


def is_due(q, now):
    """Whether check-in question `q` has come due by `now` (local time), from its schedule."""
    if q.get("paused"):
        return False
    sch = q.get("schedule") or {}
    if (now.isoweekday() % 7) not in sch.get("days", []):
        return False
    if (sch.get("start_date") or "") > now.strftime("%Y-%m-%d"):
        return False
    end = sch.get("end_date")
    if end and end < now.strftime("%Y-%m-%d"):
        return False
    return (now.hour, now.minute) >= (int(sch.get("hour", 0)), int(sch.get("minute", 0)))


def render_reply(text):
    """Markdown to Basecamp rich text: escaped, one <div> per paragraph, no raw newlines.

    Handles **bold**, *italic*, `code` (shown plain), links, bullet and numbered lists,
    headings (bold), > quotes and ``` fenced blocks, using only tags Basecamp keeps.
    Plain text renders as before; no HTML in the text passes through.
    """
    out, para, items, kind = [], [], [], None
    lines = text.strip().replace("\r\n", "\n").split("\n")

    def flush():
        nonlocal kind
        if para:
            out.append("<div>" + "<br>".join(para) + "</div>")
            para.clear()
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{kind}>")
            items.clear()
        kind = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line.startswith("```"):
            flush()
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre>" + "<br>".join(block) + "</pre>")
            continue
        m = re.match(r"([*+-]|\d+[.)])\s+(.*)", line)
        if m:
            want = "ul" if m.group(1) in "*+-" else "ol"
            if para or kind != want:
                flush()
            kind = want
            items.append(md_inline(m.group(2)))
            continue
        if not line:
            flush()
            continue
        if items:
            flush()
        h = re.match(r"#{1,6}\s+(.*)", line)
        q = re.match(r">\s?(.*)", line)
        if h:
            flush()
            out.append(f"<div><strong>{md_inline(h.group(1))}</strong></div>")
        elif q:
            flush()
            out.append(f"<blockquote>{md_inline(q.group(1))}</blockquote>")
        else:
            para.append(md_inline(line))
    flush()
    return "".join(out)


def md_inline(text):
    """One line of Markdown to escaped HTML: `code` kept literal, links, **bold**, *italic*, ~~strike~~."""
    codes = []

    def code(m):
        codes.append(html.escape(m.group(1)))
        return f"\0{len(codes) - 1}\0"
    text = re.sub(r"`([^`]+)`", code, text)
    out, pos = [], 0
    for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s<>()]+)", text):
        out.append(html.escape(text[pos:m.start()]))
        url = m.group(2) or m.group(3)
        out.append(f'<a href="{html.escape(url)}">{html.escape(m.group(1) or url)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    s = "".join(out)
    s = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"<strong>\2</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<strike>\1</strike>", s)
    return re.sub(r"\0(\d+)\0", lambda m: codes[int(m.group(1))], s)


def is_emoji(content, emoji):
    return is_thumbs_up(content) if emoji == THUMBS else re.sub(r"<[^>]+>", "", content).strip() == emoji


def is_thumbs_up(content):
    """True for 👍 alone, with or without a skin-tone modifier or variation selector."""
    return re.fullmatch("\U0001F44D[\U0001F3FB-\U0001F3FF]?️?", re.sub(r"<[^>]+>", "", content).strip()) is not None


def parse_lavish(text, data):
    """Map the open sessions in plain `lavish-axi` output to the task dir under `data` holding each file."""
    root = os.path.realpath(data) + os.sep
    out, inside = {}, False
    for row in csv.reader(text.splitlines()):
        if row and re.match(r"sessions\[\d+\]\{", row[0]):
            inside = True
            continue
        if not inside:
            continue
        if not row or not row[0].startswith("  "):
            break
        if len(row) < 3 or row[1].strip() != "open":
            continue
        f = os.path.realpath(row[0].strip())
        if not f.startswith(root):
            continue
        task = f[len(root):].split(os.sep)[0]
        if task and f[len(root) + len(task):].startswith(os.sep):
            out.setdefault(task, []).append(row[2].strip())
    return out


SECTIONS = {"in_flight": "In flight", "queued": "Queued", "held": "Queued", "done": "Done"}
PR_RE = r"https://github\.com/\S+?/pull/\d+"


def parse_show(text):
    """Parse `tasks-axi show --full` output: `  key: value`, strings JSON-quoted."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"  (\w+): (.*)$", line)
        if m:
            v = m.group(2)
            out[m.group(1)] = json.loads(v) if v.startswith('"') else v
    return out


def none(v):
    return None if v in (None, "", "-", "none") else v


def task_item(t):
    """Map tasks-axi fields to the item shape the column rules use.

    The one parsing fallback is the title: tasks-axi keeps PR URLs inside it, and
    on a row with repeated `(field: ...)` groups it keeps the leading ones too,
    so both are cut out here the way the original markdown parser did.
    """
    title = re.split(r" \((?:repo|kind|priority|since|merged|hold|closed)[:)]", t.get("title", ""))[0]
    links = re.findall(PR_RE, t.get("title", ""))
    for link in (none(t.get("links")) or "").split(","):
        links += re.findall(PR_RE, link)
    deps = none(t.get("deps")) or ""
    return {"id": t["id"], "section": SECTIONS.get(t.get("state"), "Queued"),
            "title": re.sub(r"\s*https://\S+", "", title).strip(),
            "links": list(dict.fromkeys(links)), "repo": none(t.get("repo")) or "",
            "kind": none(t.get("kind")) or "", "hold": none(t.get("hold_reason")),
            "hold_kind": none(t.get("hold_kind")), "until": none(t.get("hold_until")),
            "blocked_by": [d[len("blocked-by:"):] for d in deps.split(",") if d.startswith("blocked-by:")]}
