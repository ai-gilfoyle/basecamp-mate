"""Renew the Basecamp login before a timer run, and keep the stored login from being lost.

`run.sh` runs `sync.py renew` before each timer run. It renews the profile's OAuth token
(`basecamp auth refresh -P <profile>`) when the token is near expiry, and guards the file
store the `basecamp` CLI keeps every login in (`credentials.json` in its config directory,
used with BASECAMP_NO_KEYRING=1):

  - Before a renewal, a copy of the file is kept beside it (`credentials.json.basecamp-mate-backup`,
    mode 0600), but only while the file still holds the profile. After a renewal that worked,
    the copy is taken again, so it never holds a refresh token the renewal just spent. The copy
    is shared by every home and login, so it is merged: an entry the file no longer holds stays
    in the copy, and each entry the file does hold replaces the copy's.
  - Only the file store is guarded (BASECAMP_NO_KEYRING set, or `auth status` reporting
    "storage": "file"). A login kept in the system keyring is only renewed: the file does not
    hold it, so its absence there is no loss and no failure.
  - If after the renewal the profile is missing (the CLI deletes a login whose refresh is
    refused, and a store it could not read is saved back as `{}`), the missing entries are
    restored from the copy, and one line says so. Only the profile's own entry is put back
    while the file still holds other logins; when the whole file was emptied, every entry
    the copy holds is.
  - A copy is restored at most once (its fingerprint is kept in
    `credentials.json.basecamp-mate-restored`). If the restored login is refused again, it is
    not put back again: Basecamp bans a client and address after a handful of refused
    renewals, so the run logs one FAILED line saying to sign in and stops renewing.
  - After a renewal the file and its directory are flushed to disk (fsync): the CLI renames
    a fresh file into place without one, and a power cut soon after can lose the rotated
    refresh token, leaving the spent one on disk.
  - A run that cannot reach Basecamp (the boot window, before the network is up) leaves the
    stored login alone, does not count as a login failure, and skips the sync; one line
    notes the outage when it starts and one when it ends.

The CLI's own lock files (`locks/` in its config directory) are taken around each read and
write of the file, so neither side writes while the other is part-way through.
Nothing here prints or logs a token: only key names, whether a key is present, and hashes.
"""
import contextlib, fcntl, hashlib, json, os, socket, subprocess, tempfile, time
from datetime import datetime

RENEW_BEFORE = 15 * 60  # seconds: renew when the access token expires within this; it lasts about an hour
REACH = (("3.basecampapi.com", 443), ("launchpad.37signals.com", 443))  # the API and the token endpoints
REACH_TIMEOUT = 5  # seconds per host
AUTH_TIMEOUT = 30  # seconds per `basecamp auth` call
BACKUP = "credentials.json.basecamp-mate-backup"
RESTORED = "credentials.json.basecamp-mate-restored"
OFFLINE = 75  # `sync.py renew` exit code: Basecamp unreachable, skip this run (EX_TEMPFAIL)


def config_dir():
    """The `basecamp` CLI's config directory, as it finds it: $XDG_CONFIG_HOME/basecamp, else ~/.config/basecamp."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "basecamp")


def reachable(hosts=REACH, timeout=REACH_TIMEOUT):
    """True when a TCP connection to any of `hosts` opens (name lookup included)."""
    for host, port in hosts:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def fingerprint(blob):
    """A stable hash of one stored login, to tell copies apart without keeping or showing its tokens."""
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


class Login:
    """The renewal step for one config: `renew()` returns 0 to go on with the run, OFFLINE to skip it."""

    def __init__(self, tools, runner=subprocess.run, reach=None, now=time.time, cred_dir=None):
        self.t, self.run, self.reach, self.now = tools, runner, reach or reachable, now
        self.profile = tools.cfg.get("profile")
        self.dir = cred_dir or config_dir()
        self.creds = os.path.join(self.dir, "credentials.json")
        self.backup = os.path.join(self.dir, BACKUP)
        self.restored = os.path.join(self.dir, RESTORED)

    # --- the CLI's file store ---

    @property
    def key(self):
        """The credential's key in credentials.json: "profile:<name>", or None for the CLI's default login."""
        return f"profile:{self.profile}" if self.profile else None

    def holds(self, store):
        """True when `store` (a parsed credentials.json) still holds this config's login."""
        if not isinstance(store, dict) or not store:
            return False
        return self.key in store if self.key else True

    def read(self, path):
        """The JSON object at `path`, or None when it is missing, empty or not an object."""
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def write(self, path, data):
        """Write `data` to `path` atomically, mode 0600, flushed to disk with its directory."""
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".basecamp-mate-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        self.flush_dir()

    def flush(self, path):
        """fsync `path` and its directory, so a rename the CLI made reaches the disk now, not at the next commit."""
        with contextlib.suppress(OSError):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.flush_dir()

    def flush_dir(self):
        with contextlib.suppress(OSError):
            fd = os.open(self.dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @contextlib.contextmanager
    def cli_lock(self, name, shared=False):
        """Hold one of the CLI's own lock files (locks/<name>), as the CLI does: flock, shared for a read."""
        locks = os.path.join(self.dir, "locks")
        os.makedirs(locks, mode=0o700, exist_ok=True)
        with open(os.path.join(locks, name), "a") as f:
            fcntl.flock(f, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield

    def key_lock_name(self):
        """The CLI's per-credential lock file: key-<sha256 of the key>.lock."""
        return "key-" + hashlib.sha256((self.key or "").encode()).hexdigest() + ".lock"

    def read_store(self):
        with self.cli_lock("store.lock", shared=True):
            return self.read(self.creds)

    # --- the CLI ---

    def auth(self, *args):
        """`basecamp auth <args> [-P profile] --json`: its parsed output, or {"ok": False, ...} on any failure."""
        prof = ["-P", self.profile] if self.profile else []
        try:
            r = self.run(["basecamp", "auth", *args, *prof, "--json"], capture_output=True, text=True,
                         timeout=AUTH_TIMEOUT, stdin=subprocess.DEVNULL)
            out = json.loads(r.stdout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": "timeout", "error": f"no answer in {AUTH_TIMEOUT}s"}
        except (OSError, ValueError) as e:
            return {"ok": False, "error": type(e).__name__}
        return out if isinstance(out, dict) else {"ok": False, "error": "not a JSON object"}

    def file_store(self, st):
        """Whether the CLI keeps this login in credentials.json: BASECAMP_NO_KEYRING is set (the CLI then uses the file
        without probing the keyring), `auth status` (`st`) says so, or status names no store and the backup holds this
        login (a backup is only taken from the file store, and a lost login's status names no storage; a status that
        names the keyring wins over an old backup). A keyring login is not guarded: the
        file does not hold it, so a missing entry there is no loss."""
        storage = (st.get("data") or {}).get("storage")
        if os.environ.get("BASECAMP_NO_KEYRING") or storage == "file":
            return True
        if storage:
            return False  # status names another store (the keyring, the environment): an old file backup is moot
        return bool(self.key) and self.holds(self.read(self.backup))

    def due(self, st):
        """Whether the token needs renewing, from `auth status` (`st`): it expires within RENEW_BEFORE, by its own
        expires_at (renew when unknown)."""
        data = st.get("data") or {}
        if st.get("ok") and data.get("authenticated") is False:
            return False  # nothing stored to renew; the check after says what is missing
        try:
            at = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            return True
        return at - self.now() < RENEW_BEFORE

    # --- the step ---

    def state(self):
        return self.t.load("login.json", {})

    def note(self, **kw):
        """Change this home's login state (login.json beside the config); a None value removes the key."""
        st = self.state()
        for k, v in kw.items():
            if v is None:
                st.pop(k, None)
            else:
                st[k] = v
        self.t.save_json("login.json", st)

    def renew(self):
        if not self.reach():
            if not self.state().get("offline"):
                self.t.log("network: Basecamp cannot be reached; this run leaves the stored login alone and skips the "
                           "sync until it can (not a login failure)")
                self.note(offline=int(self.now()))
            return OFFLINE
        if self.state().get("offline"):
            self.t.log("network: Basecamp can be reached again; the sync runs")
            self.note(offline=None)
        st = self.auth("status")
        guard = self.file_store(st)
        if guard:
            self.keep()
        renewed = None
        if self.due(st):
            out = self.auth("refresh")
            renewed = bool(out.get("ok"))
            if guard:
                self.flush(self.creds)
        if guard and not self.holds(self.read_store()):
            if self.restore():
                return 0
            return self.failed("the stored login is missing and no backup copy can be restored")
        if renewed is False:
            return self.failed("basecamp auth refresh did not succeed")
        if renewed and guard:
            self.keep()
            self.forget_restored()
        if self.state().get("failed"):
            self.t.log("login: renewal works again")
            self.note(failed=None)
        return 0

    def keep(self):
        """Back up credentials.json beside it (mode 0600) while it holds this profile. The backup is shared by every
        home and login, so it is merged, not replaced: an entry the file no longer holds (another profile already
        lost) stays in the backup, and every entry the file holds replaces the backup's. Read and written under the
        CLI's store lock, so neither a CLI write nor another home's backup lands part-way through."""
        try:
            with self.cli_lock("store.lock"):
                now = self.read(self.creds)
                if not self.holds(now):
                    return
                self.write(self.backup, {**(self.read(self.backup) or {}), **now})
        except OSError as e:
            self.t.log(f"login: could not back up {self.creds}: {type(e).__name__}")

    def restore(self):
        """Put the missing login back from the backup, once per backup copy; True when it was put back.

        The whole read-check-write (the file, the backup, and the marks of what was restored) runs under the CLI's
        key and store locks, so two homes restoring at once neither both restore nor drop each other's mark."""
        with self.cli_lock(self.key_lock_name()), self.cli_lock("store.lock"):
            now = self.read(self.creds)
            if self.holds(now):
                return True  # something else put it back meanwhile
            backup = self.read(self.backup)
            if not self.holds(backup):
                return False
            done = self.read(self.restored) or {}
            # Each entry is restored at most once per copy, by its own mark: one restored already and lost again
            # would only be refused again, whichever home's restore would bring it back.
            fresh = {k: v for k, v in backup.items() if done.get(k) != fingerprint(v)}
            if now:  # other logins are still there (only a profile's own can be missing from a non-empty file)
                fresh = {k: v for k, v in fresh.items() if k == self.key}
            if (self.key not in fresh) if self.key else not fresh:
                return False
            if now:
                what = f"{self.key} was missing; put it back"
            else:
                what = f"the file was emptied; put back {', '.join(sorted(fresh))}"
                skipped = sorted(set(backup) - set(fresh))
                what += f" (not {', '.join(skipped)}: restored once already)" if skipped else ""
            self.write(self.creds, {**(now or {}), **fresh})
            self.write(self.restored, {**done, **{k: fingerprint(v) for k, v in fresh.items()}})
        self.t.log(f"login: restored {self.creds} from the copy kept before the renewal ({what})")
        return True

    def forget_restored(self):
        """A renewal worked, so the login is live again: a later loss may be restored once more."""
        with self.cli_lock("store.lock"):
            done = self.read(self.restored)
            if done and (self.key or "") in done:
                done.pop(self.key or "")
                self.write(self.restored, done)

    def failed(self, why):
        """Log the FAILED line once per cause (and hourly while it lasts), not on every run; the sync still runs."""
        login = f"basecamp auth login{' -P ' + self.profile if self.profile else ''}"
        line = f"FAILED token refresh: {why}; run {login}"
        st = self.state().get("failed") or {}
        if st.get("line") != line or self.now() - st.get("at", 0) >= 3600:
            self.t.log(line)
            self.note(failed={"line": line, "at": int(self.now())})
        return 0
