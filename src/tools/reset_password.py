"""CLI tool for emergency password reset via SSH.

Usage::

    python -m src.tools.reset_password
    python -m src.tools.reset_password --username admin --password newpass123
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

# Ensure project root is importable
sys.path.insert(0, ".")


async def _reset(username: str, password: str, generate_recovery: bool = False) -> None:
    """Core reset logic."""
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.dashboard.auth import hash_password, generate_recovery_key

    await init_db()
    repo = Repository()

    # Find user
    user = await repo.get_user_by_username(username)
    if not user:
        print(f"\n  ❌ المستخدم '{username}' غير موجود.")
        sys.exit(1)

    # Update password
    new_hash = hash_password(password)
    await repo.update_user_password(user.id, new_hash)
    print(f"\n  ✅ تم تغيير كلمة المرور للمستخدم '{username}'.")

    # Reset lockout
    await repo.reset_user_lockout(user.id)
    print("  ✅ تم إعادة تعيين حالة القفل.")

    # Deactivate all sessions
    await repo.deactivate_all_user_sessions(user.id)
    print("  ✅ تم إنهاء جميع الجلسات النشطة.")

    # Optional: generate new recovery key
    if generate_recovery:
        recovery_key = generate_recovery_key()
        recovery_hash = hash_password(recovery_key)
        await repo.update_user_recovery_key(user.id, recovery_hash)
        print("")
        print("  ╔════════════════════════════════════════════╗")
        print(f"  ║  🔑 مفتاح الاسترداد الجديد:                ║")
        print(f"  ║  {recovery_key:<43}║")
        print("  ╚════════════════════════════════════════════╝")
        print("  ⚠️  احفظ هذا المفتاح في مكان آمن — لن يظهر مجدداً.")

    print("")


def _interactive() -> tuple[str, str, bool]:
    """Prompt the user for credentials interactively."""
    print("\n  🔑 أداة إعادة تعيين كلمة المرور")
    print("  ─────────────────────────────────\n")

    username = input("  اسم المستخدم [admin]: ").strip() or "admin"
    password = getpass.getpass("  كلمة المرور الجديدة: ")
    if not password:
        print("\n  ❌ كلمة المرور لا يمكن أن تكون فارغة.")
        sys.exit(1)
    confirm = getpass.getpass("  تأكيد كلمة المرور: ")

    if password != confirm:
        print("\n  ❌ كلمات المرور غير متطابقة.")
        sys.exit(1)
    if len(password) < 8:
        print("\n  ❌ كلمة المرور يجب أن تكون 8 أحرف على الأقل.")
        sys.exit(1)

    gen_recovery = input("\n  هل تريد إنشاء مفتاح استرداد جديد؟ [y/N]: ").strip().lower()
    return username, password, gen_recovery in ("y", "yes")


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Reset dashboard password")
    parser.add_argument("--username", "-u", default=None, help="Username (default: admin)")
    parser.add_argument("--password", "-p", default=None, help="New password")
    parser.add_argument(
        "--generate-recovery", "-r",
        action="store_true",
        default=False,
        help="Generate a new recovery key",
    )
    args = parser.parse_args()

    if args.username and args.password:
        # Non-interactive mode
        if len(args.password) < 8:
            print("\n  ❌ كلمة المرور يجب أن تكون 8 أحرف على الأقل.")
            sys.exit(1)
        username, password, gen_recovery = args.username, args.password, args.generate_recovery
    else:
        # Interactive mode
        username, password, gen_recovery = _interactive()

    asyncio.run(_reset(username, password, gen_recovery))


if __name__ == "__main__":
    main()
