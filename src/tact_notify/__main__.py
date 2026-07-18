from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="tact_notify")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "daily", "test"):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true", help="print payloads, no Slack post / state write")
    sub.add_parser("probe")
    sub.add_parser("totp", help="print the current TOTP code (for registration / debugging)")
    args = parser.parse_args()

    if args.command == "totp":
        from . import config
        from .auth import totp_code

        secret = config.MS_TOTP_SECRET()
        if not secret:
            raise SystemExit("MS_TOTP_SECRET is not set in .env")
        print(totp_code(secret))
        return

    if args.command == "probe":
        from . import probe

        probe.run()
    elif args.command == "check":
        from . import check

        check.run(dry_run=args.dry_run)
    elif args.command == "daily":
        from . import daily

        daily.run(dry_run=args.dry_run)
    elif args.command == "test":
        from . import selftest

        selftest.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
