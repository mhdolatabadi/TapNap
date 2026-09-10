"""Account approval CLI -- new signups start unapproved (see
db.create_user) and can't log in until an admin approves them. No admin
password needed here (unlike the /api/admin/* HTTP endpoints): this runs
inside the container, which is already the trust boundary.

Run from the host:
    docker exec -it tapnap python -m app.manage_users list
    docker exec -it tapnap python -m app.manage_users approve <email>
    docker exec -it tapnap python -m app.manage_users reject <email>
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


def main():
    db.init_db()
    args = sys.argv[1:]
    if not args or args[0] not in ("list", "approve", "reject"):
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    command, rest = args[0], args[1:]
    if command == "list":
        cmd_list()
    elif command in ("approve", "reject") and len(rest) == 1:
        (cmd_approve if command == "approve" else cmd_reject)(rest[0])
    else:
        print(f"usage: python -m app.manage_users {command} <email>", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
