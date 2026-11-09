#!/usr/bin/env python3
"""Pull and apply the T3 Code master config on one machine.

This is the client-side half of "automatic pull". Git cannot force a remote
machine to update, so each machine runs this on a timer or at T3 launch.

Guarantees:
  * fast-forward only; never resets or discards local changes
  * refuses a dirty tracked worktree
  * requires the main branch
  * one sync at a time via a lock file
  * T3 settings are deferred while T3 is running (unless --no-defer-t3)
  * read-only Cursor Plus guard runs every time, even while T3 is open
  * --strict fails if T3 was deferred, guard drift, or a doctor FAIL
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
ALL_COMPONENTS = {"t3", "omp", "opencode"}


def parse_components(value: str) -> set[str]:
    if value.strip() == "all":
        return set(ALL_COMPONENTS)
    requested = {item.strip() for item in value.split(",") if item.strip()}
    unknown = requested - ALL_COMPONENTS
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown components: {sorted(unknown)}")
    return requested


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def log(message: str, log_path: Path | None = None) -> None:
    line = f"[{now()}] {message}"
    print(line, flush=True)
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def acquire_lock(lock_path: Path, force: bool) -> int | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lock_path.stat().st_mtime
        if force or age > 7200:
            lock_path.unlink(missing_ok=True)
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        return None


def release_lock(fd: int | None, lock_path: Path) -> None:
    if fd is not None:
        os.close(fd)
    lock_path.unlink(missing_ok=True)


def pull_main(strict_clean: bool, log_path: Path) -> int:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        log(f"refusing to sync: branch is {branch!r}, expected 'main'", log_path)
        return 2

    dirty = git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        log("refusing to sync: tracked files are modified:\n" + dirty, log_path)
        return 2

    before = git("rev-parse", "HEAD").stdout.strip()
    fetch = git("fetch", "origin", "main")
    if fetch.returncode != 0:
        log("git fetch failed:\n" + fetch.stderr.strip(), log_path)
        return 2

    pull = git("pull", "--ff-only", "origin", "main")
    if pull.returncode != 0:
        log("git pull --ff-only failed:\n" + pull.stderr.strip(), log_path)
        return 2

    after = git("rev-parse", "HEAD").stdout.strip()
    if before != after:
        log(f"updated {before[:12]} -> {after[:12]}", log_path)
        if strict_clean:
            changed = git("diff", "--name-only", before, after).stdout.strip()
            if changed:
                log("changed files:\n" + changed, log_path)
    else:
        log(f"already at {after[:12]}", log_path)
    return 0


def guard_log(profile: dict, log_path: Path) -> int:
    """Read-only Cursor Plus drift check; safe to run while T3 is open.

    Returns 1 if the live settings are in the 'Model unavailable on Plus'
    state, 0 otherwise. Never writes anything.
    """
    import t3cfg  # noqa: PLC0415 - resolved after the pull inside main()

    cursor, _ = t3cfg.load_catalogs()
    settings_path = t3cfg.find_t3_settings(profile)
    if not settings_path.is_file():
        log(f"guard skip: no T3 settings at {settings_path}", log_path)
        return 0
    try:
        settings = t3cfg.load_json(settings_path)
        defects = t3cfg.cursor_plus_defects(settings, cursor)
    except (OSError, ValueError) as error:
        log(f"guard could not read {settings_path}: {error}", log_path)
        return 0
    if defects:
        for defect in defects:
            log(f"GUARD FAIL: {defect}", log_path)
        log("Fix with: quit T3, then ./bin/t3cfg apply --profile {} --only t3".format(profile["id"]), log_path)
        return 1
    log("guard: Cursor Plus favorites carry their fixed options", log_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted(p.stem for p in PROFILES.glob("*.json")))
    parser.add_argument("--apply", action="store_true", help="Apply after pulling; default is pull + plan only")
    parser.add_argument("--components", type=parse_components, default=set(ALL_COMPONENTS))
    parser.add_argument("--no-pull", action="store_true", help="Use the current checkout without fetching")
    parser.add_argument("--strict", action="store_true", help="Fail if T3 settings are deferred or doctor fails")
    parser.add_argument("--no-doctor", action="store_true")
    parser.add_argument("--no-defer-t3", action="store_true", help="Fail instead of skipping T3 settings while T3 is open")
    parser.add_argument("--force-lock", action="store_true")
    args = parser.parse_args(argv)

    state = Path.home() / ".config" / "t3-master"
    log_path = state / "sync.log"
    lock_path = state / "sync.lock"
    lock_fd = acquire_lock(lock_path, args.force_lock)
    if lock_fd is None:
        log(f"another sync is running (lock: {lock_path})", log_path)
        return 0

    try:
        if not args.no_pull:
            code = pull_main(strict_clean=True, log_path=log_path)
            if code != 0:
                return code
        else:
            log("--no-pull: using current checkout", log_path)

        # Import after the pull so a newly pulled tool version is used.
        sys.path.insert(0, str(ROOT / "scripts"))
        import t3cfg  # type: ignore  # noqa: PLC0415

        profile = t3cfg.load_profile(args.profile)
        log(
            f"profile={args.profile} apply={args.apply} components={','.join(sorted(args.components))}",
            log_path,
        )

        components = set(args.components)
        deferred_t3 = False
        if "t3" in components and t3cfg.t3_running():
            if args.no_defer_t3:
                log("T3 is running and --no-defer-t3 was requested; refusing", log_path)
                return 1
            components.discard("t3")
            deferred_t3 = True
            log(
                "T3 is running; deferred T3 settings. Quit T3 and run again for Cursor/config changes.",
                log_path,
            )

        if not args.apply:
            t3cfg.plan(profile)
            guard_code = guard_log(profile, log_path)
            if args.strict and (deferred_t3 or guard_code != 0):
                log("--strict: deferred T3 settings or Cursor Plus drift count as a failure", log_path)
                return 1
            return 0

        # Render first so a broken template fails before any live write.
        rendered = t3cfg.render_profile(profile, t3cfg.BUILD / profile["id"])
        log(f"rendered {rendered}", log_path)

        try:
            if "t3" in components:
                t3cfg.apply_t3(
                    profile,
                    t3cfg.build_t3_patch(profile),
                    dry_run=False,
                    force=False,
                    override=None,
                )
            if "omp" in components:
                t3cfg.apply_omp(profile, dry_run=False)
            if "opencode" in components:
                t3cfg.apply_opencode(
                    profile, t3cfg.build_opencode_patch(profile), dry_run=False
                )
        except SystemExit as error:
            log(f"apply failed: {error}", log_path)
            return 2
        except Exception as error:  # noqa: BLE001 - one machine's failure should be logged
            log(f"apply failed: {type(error).__name__}: {error}", log_path)
            return 2

        if args.strict and deferred_t3:
            log("--strict: deferred T3 settings count as a failure", log_path)
            return 1
        guard_code = guard_log(profile, log_path)
        if args.strict and guard_code != 0:
            log("--strict: guard detected Cursor Plus drift after apply", log_path)
            return 1
        if args.no_doctor:
            return 0
        doctor_code = t3cfg.doctor(profile, False)
        if args.strict and doctor_code != 0:
            log("--strict: doctor reported FAIL", log_path)
            return 1
        return 0
    finally:
        release_lock(lock_fd, lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
