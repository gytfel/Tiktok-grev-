"""Управление пользователями панели из командной строки.

python -m app.cli create-user <логин> --role admin
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Role, User
from app.security import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="создать пользователя или сменить пароль")
    create.add_argument("username")
    create.add_argument("--role", choices=Role.ALL, default=Role.OPERATOR)
    args = parser.parse_args(argv)

    password = getpass.getpass("Пароль: ")
    if len(password) < 8:
        print("Пароль должен быть не короче 8 символов", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        user = db.execute(select(User).where(User.username == args.username)).scalar_one_or_none()
        if user is None:
            user = User(username=args.username, role=args.role, password_hash="")
            db.add(user)
        user.role = args.role
        user.password_hash = hash_password(password)
        db.commit()
    print(f"Пользователь {args.username} ({args.role}) сохранён")
    return 0


if __name__ == "__main__":
    sys.exit(main())
