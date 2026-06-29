"""CLI over the versioned migration runner (database/migrations/runner.py).

python -m database.migrate up   [--to REV]
python -m database.migrate down  --to REV
python -m database.migrate status
python -m database.migrate stamp REV
python -m database.migrate new   <slug>
"""

import argparse
import sys

from database.migrations import runner

_TEMPLATE = '''"""{slug} — describe the change here."""

revision = "{rev}"
name = "{slug}"

from database.migrations.runner import IrreversibleMigration


def up(cursor, dialect):
    # dialect in {{"sqlite", "postgresql"}}
    raise NotImplementedError("write the up migration")


def down(cursor, dialect):
    raise IrreversibleMigration("{rev}_{slug} cannot be downgraded")
'''


def _next_revision():
    revs = [m.revision for m in runner.discover()]
    nxt = (max(int(r) for r in revs) + 1) if revs else 1
    return f"{nxt:04d}"


def cmd_new(slug):
    rev = _next_revision()
    path = runner._MIGRATIONS_DIR / f"{rev}_{slug}.py"
    path.write_text(_TEMPLATE.format(rev=rev, slug=slug))
    print(f"Created {path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="database.migrate")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="apply pending migrations")
    up.add_argument("--to", default="head")
    down = sub.add_parser("down", help="revert migrations down to --to REV")
    down.add_argument("--to", required=True)
    sub.add_parser("status", help="show applied/pending migrations")
    st = sub.add_parser("stamp", help="record a revision applied without running it")
    st.add_argument("rev")
    nw = sub.add_parser("new", help="scaffold a new migration file")
    nw.add_argument("slug")

    args = parser.parse_args(argv)

    if args.command == "up":
        applied = runner.upgrade(target=args.to)
        print(f"Applied: {applied or 'nothing (already up to date)'}")
        return 0
    if args.command == "down":
        reverted = runner.downgrade(args.to)
        print(f"Reverted: {reverted or 'nothing'}")
        return 0
    if args.command == "status":
        s = runner.status()
        print(f"current: {s['current']}")
        print(f"applied: {s['applied']}")
        print(f"pending: {s['pending']}")
        return 0
    if args.command == "stamp":
        runner.stamp(args.rev)
        print(f"Stamped: {args.rev}")
        return 0
    if args.command == "new":
        return cmd_new(args.slug)
    return 1


if __name__ == "__main__":
    sys.exit(main())
