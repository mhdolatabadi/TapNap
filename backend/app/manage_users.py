"""Account approval CLI -- new signups start unapproved (see
db.create_user) and can't log in until an admin approves them. No admin
password needed here (unlike the /api/admin/* HTTP endpoints): this runs
inside the container, which is already the trust boundary.

Also doubles as the premium toggle: price alerts (Bale bot) and API key
issuance (see main.py's _require_premium) are gated on users.is_premium,
which has no billing integration yet -- this is the only way to flip it.

Run from the host:
    docker exec -it tapnap python -m app.manage_users list
    docker exec -it tapnap python -m app.manage_users approve <email>
    docker exec -it tapnap python -m app.manage_users reject <email>
    docker exec -it tapnap python -m app.manage_users premium <email>
    docker exec -it tapnap python -m app.manage_users unpremium <email>
"""
import sys

from . import db


def cmd_list():
    pending = db.get_pending_users()
    if not pending:
        print("هیچ درخواست در انتظاری نیست.")
        return
    for u in pending:
        print(f"{u['id']}\t{u['email']}\t{u['created_at']}")


def _find_pending(email: str) -> dict | None:
    return next((u for u in db.get_pending_users() if u["email"] == email.strip().lower()), None)


def cmd_approve(email: str):
    user = _find_pending(email)
    if user is None:
        print(f"درخواستی برای {email} در انتظار تایید نیست.", file=sys.stderr)
        sys.exit(1)
    db.approve_user(user["id"])
    print(f"{email} تایید شد.")


def cmd_reject(email: str):
    user = _find_pending(email)
    if user is None:
        print(f"درخواستی برای {email} در انتظار تایید نیست.", file=sys.stderr)
        sys.exit(1)
    db.reject_user(user["id"])
    print(f"{email} رد شد.")


def _set_premium(email: str, is_premium: bool):
    user = db.get_user_by_email(email.strip().lower())
    if user is None:
        print(f"حسابی با ایمیل {email} پیدا نشد.", file=sys.stderr)
        sys.exit(1)
    db.set_user_premium(user["id"], is_premium)
    print(f"{email} {'premium شد' if is_premium else 'از premium خارج شد'}.")


def cmd_premium(email: str):
    _set_premium(email, True)


def cmd_unpremium(email: str):
    _set_premium(email, False)


def main():
    db.init_db()
    args = sys.argv[1:]
    commands = ("list", "approve", "reject", "premium", "unpremium")
    if not args or args[0] not in commands:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    command, rest = args[0], args[1:]
    if command == "list":
        cmd_list()
    elif command in commands[1:] and len(rest) == 1:
        {"approve": cmd_approve, "reject": cmd_reject, "premium": cmd_premium, "unpremium": cmd_unpremium}[command](
            rest[0]
        )
    else:
        print(f"usage: python -m app.manage_users {command} <email>", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
