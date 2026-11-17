"""Administration from the command line.

    python -m backend.app.cli create-user --email you@example.com --name 你的名字 --role admin
    docker compose exec api python -m backend.app.cli create-user --email you@example.com --role admin

The password is asked for interactively, or read from the variable named by --password-env
(for scripts). The first admin also takes over watchlist entries made before accounts existed.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select


def _create_user(args) -> int:
    from .auth import create_user
    from .db import init_db, session

    if args.password_env:
        password = os.environ.get(args.password_env) or ""
        if not password:
            print(f"環境變數 {args.password_env} 沒有內容。", file=sys.stderr)
            return 2
    else:
        password = getpass.getpass("密碼：")
        if password != getpass.getpass("再輸入一次："):
            print("兩次輸入的密碼不同。", file=sys.stderr)
            return 2
    init_db()
    try:
        with session() as db:
            u = create_user(db, email=args.email, display_name=args.name or "", password=password, role=args.role)
            print(f"已建立{'管理員' if u.role == 'admin' else '用戶'}：{u.email}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _list_users(_args) -> int:
    from .db import init_db, session
    from .models import User

    init_db()
    with session() as db:
        for u in db.scalars(select(User).order_by(User.created_at)):
            print(f"{u.email:40} {u.role:7} {'啟用' if u.is_active else '停用'}  {u.display_name}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.app.cli", description="估值工作台管理指令")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="建立用戶（第一個帳號請用 --role admin）")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--role", choices=("admin", "member"), default="member")
    c.add_argument("--password-env", help="從此環境變數讀取密碼（不互動）")
    c.set_defaults(func=_create_user)
    ls = sub.add_parser("list-users", help="列出所有用戶")
    ls.set_defaults(func=_list_users)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
