"""Управление пользователями панели из командной строки.

python -m app.cli create-user <логин> --role admin
python -m app.cli reset-2fa <логин>      # если потерян телефон с кодами
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app import twofactor
from app.db import get_sessionmaker
from app.models import Role, User
from app.security import hash_password

MIN_PASSWORD = 10


def create_user(username: str, role: str) -> int:
    password = getpass.getpass("Пароль: ")
    if len(password) < MIN_PASSWORD:
        print(f"Пароль должен быть не короче {MIN_PASSWORD} символов", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            user = User(username=username, role=role, password_hash="")
            db.add(user)
        else:
            user.session_version += 1
        user.role = role
        user.password_hash = hash_password(password)
        db.commit()
    print(f"Пользователь {username} ({role}) сохранён")
    return 0


def reset_two_factor(username: str) -> int:
    with get_sessionmaker()() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            print("Пользователь не найден", file=sys.stderr)
            return 1
        twofactor.disable(user)
        user.session_version += 1
        db.commit()
    print(f"Двухфакторный вход для {username} отключён. Включите его заново в разделе «Безопасность».")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="создать пользователя или сменить пароль")
    create.add_argument("username")
    create.add_argument("--role", choices=Role.ALL, default=Role.OPERATOR)
    reset = sub.add_parser("reset-2fa", help="отключить двухфакторный вход пользователю")
    reset.add_argument("username")
    args = parser.parse_args(argv)

    if args.command == "create-user":
        return create_user(args.username, args.role)
    return reset_two_factor(args.username)


if __name__ == "__main__":
    sys.exit(main())
