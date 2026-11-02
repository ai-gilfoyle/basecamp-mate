"""Which sync runs on this computer watch what in Basecamp, so two homes never process the same events.

Two homes whose configs watch the same project with the same Basecamp login would each
read the same notifications, record the same owner input, acknowledge it twice and
deliver it to two agents. The shared state lock cannot stop that: it is per config. So
every timer run claims what it watches here before it reads anything, and refuses to run
beside a live claim that overlaps, naming the other home. Two runs of the same config
never collide here: they share one claim, and the state lock serializes them.

What a config watches (`watches`): its project, and, account-wide, the Pings of its login
("pings"), the to-dos assigned to its login anywhere ("assigned_todos" with "scope":
"account") and its @mentions and followed threads anywhere ("mentions" with "scope":
"account"). Two claims overlap when they share one of those with the same login: the same
Basecamp person when both know it, else the same CLI profile.

A chat its home watches with `sync.py watch` never makes a run refuse: its project is
not claimed. Instead, when another live home on the same login claims that project,
`claim` reports it as held and the run skips that chat (Sync.claim), so the home that
owns the project reads it alone; this also covers a home that starts after the watch.

Each config's claim is one file, `<digest of the config path>.json`, in the directory
$BASECAMP_MATE_RUNS (default ~/.local/state/basecamp-mate/runs): the config, the home,
the login, the watches, the run's pid and its process start time, and when it last ran.
A claim is live while its run is still going (the pid probe, checked against the recorded
start time because pids are recycled) or it ran within STALE seconds and its config still
exists; the timer runs every 30 seconds, so a home whose timer was stopped, whose run was
killed or whose config was removed stops blocking within STALE. Checking and claiming are
one step under a lock, so two homes starting together cannot both claim.

The override is deliberate: "allow_duplicate": true in the config, or `--allow-duplicate`
on one run. `sync.py status` (also `basecamp-mate status`) lists every claim, live or
stale, and any sync.py process running that holds none.
"""
import contextlib, fcntl, hashlib, json, os, time

STALE = 600  # seconds since a claim's last run after which it no longer blocks another home
MODE = 0o700


def directory():
    return os.environ.get("BASECAMP_MATE_RUNS") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "basecamp-mate", "runs")


def watches(cfg):
    """What a config watches in Basecamp, as tokens two configs can share."""
    account = str(cfg["account"])
    out = [f"project {account}/{cfg['project']}"]
    if cfg.get("pings") not in (None, False):
        out.append(f"pings {account}")
    opts = cfg.get("assigned_todos")
    if isinstance(opts, dict) and opts.get("scope") == "account":
        out.append(f"requests {account}")
    opts = cfg.get("assigned_cards")
    if isinstance(opts, dict) and opts.get("scope") == "account":
        out.append(f"card-requests {account}")
    opts = cfg.get("mentions")
    if isinstance(opts, dict) and opts.get("scope") == "account":
        out.append(f"mentions {account}")
    return out


def same_login(a, b):
    """True when claims `a` and `b` read Basecamp as the same person: by person id when both know it, else by profile."""
    if a.get("person") and b.get("person"):
        return a["person"] == b["person"]
    return (a.get("profile") or "") == (b.get("profile") or "")


def overlap(a, b):
    """The watches claims `a` and `b` share with the same login, sorted; empty when they can both run."""
    return sorted(set(a.get("watches") or []) & set(b.get("watches") or [])) if same_login(a, b) else []


def process_start(pid):
    """Process `pid`'s start time (clock ticks since boot, /proc/<pid>/stat field 22), or None where unreadable."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
        return stat[stat.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def running(claim):
    """True while the run that wrote `claim` is still going."""
    pid = claim.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # another user's process: alive enough
    live = process_start(pid)
    return claim.get("process_start") is None or live is None or live == claim["process_start"]


def live(claim, now):
    """True when `claim` still blocks an overlapping run: its run is going, or it ran within STALE and its config exists."""
    if running(claim):
        return True
    return os.path.exists(claim.get("config") or "") and now - (claim.get("last_run") or 0) < STALE


def describe(claim, now):
    """One line naming a claim: its home, config, login, when it last ran and whether it is running now."""
    login = f"profile {claim['profile']!r}" if claim.get("profile") else "the default login"
    if claim.get("person"):
        login += f" (person {claim['person']})"
    ago = int(now - (claim.get("last_run") or 0))
    state = f"running now as pid {claim['pid']}" if running(claim) else f"last ran {ago // 60}m{ago % 60:02d}s ago"
    return f"home {claim.get('home')} (config {claim.get('config')}), {login}, {state}"


class Registry:
    def __init__(self, path=None, now=time.time):
        self.dir, self.now = path or directory(), now
        self.held = {}  # set by claim: watched project id -> the live claims of other homes on this login that hold it

    def file(self, config):
        return os.path.join(self.dir, hashlib.sha256(os.path.realpath(config).encode()).hexdigest()[:16] + ".json")

    def claims(self):
        out = []
        for name in sorted(os.listdir(self.dir)) if os.path.isdir(self.dir) else ():
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.dir, name)) as f:
                    claim = json.load(f)
            except (OSError, ValueError):
                continue  # a half-written or foreign file says nothing; leave it
            if isinstance(claim, dict):
                out.append(claim)
        return out

    @contextlib.contextmanager
    def locked(self):
        os.makedirs(self.dir, mode=MODE, exist_ok=True)
        with open(os.path.join(self.dir, ".lock"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def holders(self, config, cfg, person, bucket):
        """The live claims of other configs on `cfg`'s login that claim project `bucket` as their own."""
        config = os.path.realpath(config)
        mine = {"profile": cfg.get("profile") or "", "person": person if isinstance(person, int) else None}
        token, now = f"project {cfg['account']}/{bucket}", self.now()
        return [c for c in self.claims() if c.get("config") != config and same_login(mine, c) and live(c, now)
                and token in (c.get("watches") or [])]

    def claim(self, config, home, cfg, person, allow=False, dry=False, buckets=()):
        """Claim `cfg`'s watches for this run: the live overlapping claims of other configs, each with what it shares.

        Nothing is claimed when any overlap and `allow` is false; the caller refuses to run.
        A dry run checks and claims nothing. Claims whose config is gone and whose run is
        over are removed. `buckets` are the projects of the chats this home watches (`sync.py
        watch`): never claimed and never an overlap; each one another live home on this login
        claims as its project is left in `self.held` (project id -> those claims), for the
        caller to skip.
        """
        config = os.path.realpath(config)
        mine = {"config": config, "home": os.path.abspath(home), "profile": cfg.get("profile") or "",
                "person": person if isinstance(person, int) else None, "watches": watches(cfg)}
        account, self.held = str(cfg["account"]), {}
        with self.locked():
            now = self.now()
            others = []
            for c in self.claims():
                if c.get("config") == config:
                    continue
                if not dry and not os.path.exists(c.get("config") or "") and not running(c):
                    with contextlib.suppress(OSError):
                        os.remove(self.file(c["config"]))
                    continue
                shared = overlap(mine, c)
                if shared and live(c, now):
                    others.append((c, shared))
                if same_login(mine, c) and live(c, now):
                    for b in buckets:
                        if str(b) != str(cfg["project"]) and f"project {account}/{b}" in (c.get("watches") or []):
                            self.held.setdefault(str(b), []).append(c)
            if (others and not allow) or dry:
                return others
            pid = os.getpid()
            mine.update(pid=pid, process_start=process_start(pid), last_run=now)
            tmp = self.file(config) + f".{pid}.tmp"
            with open(tmp, "w") as f:
                json.dump(mine, f, indent=1, sort_keys=True)
            os.replace(tmp, self.file(config))
            return others


def sync_processes():
    """(pid, config) for each running `sync.py` timer run on this computer (Linux /proc), this process excluded."""
    out = []
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else ():
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if not any(os.path.basename(a) == "sync.py" for a in argv[:3]) or "--config" not in argv or "status" in argv:
            continue
        i = argv.index("--config")
        if i + 1 < len(argv):
            out.append((int(pid), os.path.realpath(argv[i + 1])))
    return out


def status(registry=None, processes=sync_processes, out=print):
    """`sync.py status`: every claim here, live or stale, and each running sync.py whose config holds no claim."""
    reg = registry or Registry()
    now = reg.now()
    claims = reg.claims()
    if not claims:
        out(f"No sync runs recorded in {reg.dir}.")
    for c in sorted(claims, key=lambda c: c.get("config") or ""):
        out(f"{'live ' if live(c, now) else 'stale'}  {describe(c, now)}")
        out(f"       watches: {', '.join(c.get('watches') or []) or 'nothing'}")
        dupes = [o for o in claims if o is not c and overlap(c, o) and live(o, now)]
        if dupes and live(c, now):
            out(f"       overlaps with: {', '.join(o.get('home') or '?' for o in dupes)}")
    known = {c.get("config") for c in claims}
    for pid, config in processes():
        if config not in known:
            out(f"unrecorded  pid {pid} runs sync.py with config {config}, which holds no claim here")
    return 0
