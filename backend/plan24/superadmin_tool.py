"""Хеш пароля суперадмина для PLAN24_SUPERADMIN_PASSWORD_HASH:  python -m plan24.superadmin_tool

Пароль вводится скрыто и нигде не сохраняется; печатается только argon2-хеш. В .env и docker-compose знак «$» в значении
нужно удвоить («$$»), иначе compose примет его за подстановку переменной — утилита печатает обе формы."""
import getpass
import sys

from .security import hash_password


def main() -> int:
    p1 = getpass.getpass("Пароль суперадмина (не короче 12 символов): ")
    if len(p1) < 12:
        print("Слишком короткий пароль", file=sys.stderr)
        return 1
    if p1 != getpass.getpass("Повторите пароль: "):
        print("Пароли не совпадают", file=sys.stderr)
        return 1
    h = hash_password(p1)
    print("\nPLAN24_SUPERADMIN_PASSWORD_HASH (shell, systemd, Timeweb):\n" + h)
    print("\nТо же значение для .env / docker-compose (знаки $ удвоены):\n" + h.replace("$", "$$"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
