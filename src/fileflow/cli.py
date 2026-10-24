from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

from fileflow import __version__
from fileflow.demo import create_demo
from fileflow.errors import FileFlowError
from fileflow.journal import apply, history, resume
from fileflow.models import Plan, Settings
from fileflow.planner import duplicates, make_plan
from fileflow.watcher import Watcher


def emit(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=True))


def settings_for(args) -> Settings:
    if args.config:
        raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
        return Settings.from_dict(raw)
    return Settings(
        group_by=args.group_by,
        destination=args.destination,
        rename_template=args.rename_template,
        recursive=args.recursive,
    )


def add_settings(parser) -> None:
    parser.add_argument("--config", help="JSON settings; when supplied, replaces all rule flags")
    parser.add_argument("--group-by", choices=["type", "date", "none"], default="type")
    parser.add_argument("--destination", default="Organized")
    parser.add_argument("--rename-template", help="Fields: {stem}, {suffix}, {index:03d}, {date}")
    parser.add_argument("--recursive", action="store_true")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="fileflow",
        description="Preview-first file organization. Apply/undo require explicit confirmation.",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    preview = commands.add_parser(
        "preview", help="Read/hash files and write a new reviewable plan; never moves files"
    )
    preview.add_argument("folder", type=Path)
    preview.add_argument(
        "--save", type=Path, help="New JSON file; an existing file is never replaced"
    )
    add_settings(preview)
    run = commands.add_parser(
        "apply", help="Apply exactly the saved plan after revalidating file identities"
    )
    run.add_argument("plan", type=Path)
    run.add_argument("--yes", action="store_true", required=True)
    for verb in ["undo", "recover"]:
        cmd = commands.add_parser(verb)
        cmd.add_argument("folder", type=Path)
        cmd.add_argument("--run", default="latest")
        cmd.add_argument("--yes", action="store_true", required=True)
    listing = commands.add_parser("history")
    listing.add_argument("folder", type=Path)
    dup = commands.add_parser(
        "duplicates", help="Read-only SHA-256 duplicate groups; never deletes files"
    )
    dup.add_argument("folder", type=Path)
    add_settings(dup)
    watch = commands.add_parser(
        "watch",
        help="Preview new stable files; --execute --yes explicitly opts into automatic moves",
    )
    watch.add_argument("folder", type=Path)
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("--settle", type=float, default=2.0)
    watch.add_argument("--iterations", type=int, help="Limit poll count for bounded demos")
    watch.add_argument("--execute", action="store_true")
    watch.add_argument("--yes", action="store_true")
    add_settings(watch)
    demo = commands.add_parser("demo", help="Create original synthetic files in a new folder")
    demo.add_argument("folder", type=Path)
    commands.add_parser("gui", help="Open the optional Qt desktop application")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "preview":
            plan = make_plan(args.folder, settings_for(args))
            if args.save:
                plan.save(args.save)
            emit({**plan.to_dict(), "digest": plan.digest})
        elif args.command == "apply":
            emit({"run": apply(Plan.load(args.plan)), "state": "applied"})
        elif args.command in {"undo", "recover"}:
            emit(
                {
                    "run": resume(args.folder, args.run, undo=args.command == "undo"),
                    "action": args.command,
                }
            )
        elif args.command == "history":
            emit(history(args.folder))
        elif args.command == "duplicates":
            emit({"groups": duplicates(args.folder, settings_for(args)), "mode": "read_only"})
        elif args.command == "demo":
            emit({"folder": str(create_demo(args.folder).resolve()), "mode": "synthetic_demo"})
        elif args.command == "gui":
            from fileflow.desktop import main as desktop_main

            return desktop_main([])
        elif args.command == "watch":
            if (
                not math.isfinite(args.interval)
                or not math.isfinite(args.settle)
                or args.interval < 0.1
                or args.settle < 0
                or (args.iterations is not None and args.iterations < 1)
            ):
                raise FileFlowError("Interval must be >= 0.1, settle >= 0, and iterations >= 1")
            if args.execute and not args.yes:
                raise FileFlowError("Automatic moves require both --execute and --yes")
            watcher = Watcher(args.folder, settings_for(args), args.settle)
            print(
                "Watching only new files after startup. Ctrl+C stops the watcher.", file=sys.stderr
            )
            count = 0
            while args.iterations is None or count < args.iterations:
                ready = watcher.poll(time.monotonic())
                if ready:
                    plan = make_plan(watcher.root, watcher.settings, only=ready)
                    result = {
                        "mode": "automatic_apply" if args.execute else "preview_only",
                        "plan": plan.to_dict(),
                    }
                    if args.execute and plan.moves:
                        result["run"] = apply(plan)
                    emit(result)
                    sys.stdout.flush()
                    watcher.acknowledge(ready)
                count += 1
                if args.iterations is None or count < args.iterations:
                    time.sleep(args.interval)
        return 0
    except KeyboardInterrupt:
        return 130
    except (FileFlowError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"FileFlow: {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        print(
            f"Desktop dependency missing: {exc}. Install with pip install '.[desktop]'",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
