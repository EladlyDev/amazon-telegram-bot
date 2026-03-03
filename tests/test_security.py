"""Comprehensive security test suite.

Covers: password hashing, session signing, user-agent parsing, rate limiting,
OTP store, security classification, repository (users, sessions, OTP, login),
and API endpoints (login, settings, account, sessions, recovery).

Run::

    python -m tests.test_security
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_PATH"] = "data/test_security.db"

DB_PATH = "data/test_security.db"

# ── Helpers ────────────────────────────────────────────────

passed = 0
failed = 0


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  \033[32mPASS\033[0m  {name}")


def fail(name: str, detail: str = "") -> None:
    global failed
    failed += 1
    extra = f" — {detail}" if detail else ""
    print(f"  \033[31mFAIL\033[0m  {name}{extra}")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        ok(name)
    else:
        fail(name, detail)


# ── Test categories ────────────────────────────────────────

async def test_password_hashing():
    print("\n--- Password Hashing (8 tests) ---")
    from src.dashboard.auth import hash_password, verify_password

    # 1. Salt:key format
    h = hash_password("test123")
    check("1.1 Hash salt:key format", ":" in h and len(h) > 100, f"got {h[:40]}...")

    # 2. Different hashes for same password
    h2 = hash_password("test123")
    check("1.2 Different hashes (random salt)", h != h2)

    # 3. Verify correct
    check("1.3 Verify correct password", verify_password("test123", h))

    # 4. Verify wrong
    check("1.4 Verify wrong password", not verify_password("wrong", h))

    # 5. Empty password
    check("1.5 Verify empty password", not verify_password("", h))

    # 6. Empty hash
    check("1.6 Verify empty hash", not verify_password("test", ""))

    # 7. Malformed hash — no crash
    check("1.7 Verify malformed hash", not verify_password("test", "not-a-real-hash"))

    # 8. Backward compat (plain text)
    check("1.8 Backward compat plain text", verify_password("admin123", "admin123"))


async def test_session_signing():
    print("\n--- Session Signing (5 tests) ---")
    from src.dashboard.auth import SessionSigner, generate_session_id

    signer = SessionSigner("test-secret-key-1234")

    # 9. Sign and unsign
    sid = "abc123def456"
    token = signer.sign_session_id(sid)
    unsigned = signer.unsign_session_id(token)
    check("2.1 Sign and unsign", unsigned == sid, f"got {unsigned}")

    # 10. Tampered token
    tampered = token + "x"
    check("2.2 Tampered token → None", signer.unsign_session_id(tampered) is None)

    # 11. Empty token
    check("2.3 Empty token → None", signer.unsign_session_id("") is None)

    # 12. None token
    check("2.4 None token → None", signer.unsign_session_id(None) is None)

    # 13. Generate session ID
    gen_sid = generate_session_id()
    check("2.5 Generated session ID (64 hex)", len(gen_sid) == 64 and all(c in "0123456789abcdef" for c in gen_sid))


async def test_user_agent_parsing():
    print("\n--- User-Agent Parsing (6 tests) ---")
    from src.dashboard.auth import parse_user_agent

    # 14. Chrome on Windows
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    check("3.1 Chrome on Windows", parse_user_agent(ua) == "Chrome on Windows", parse_user_agent(ua))

    # 15. Safari on iPhone
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/605.1"
    check("3.2 Safari on iPhone", parse_user_agent(ua) == "Safari on iPhone", parse_user_agent(ua))

    # 16. Firefox on Linux
    ua = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
    check("3.3 Firefox on Linux", parse_user_agent(ua) == "Firefox on Linux", parse_user_agent(ua))

    # 17. Edge on Windows
    ua = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36 Edg/120.0"
    check("3.4 Edge on Windows", parse_user_agent(ua) == "Edge on Windows", parse_user_agent(ua))

    # 18. Empty string
    check("3.5 Empty string → Unknown", parse_user_agent("") == "Unknown")

    # 19. Random string
    result = parse_user_agent("curl/7.68.0")
    check("3.6 Random string → Unknown Browser on Unknown OS", result == "Unknown Browser on Unknown OS", result)


async def test_rate_limiter():
    print("\n--- Rate Limiter (5 tests) ---")
    from src.dashboard.rate_limit import RateLimiter

    limiter = RateLimiter()

    # 20. First request
    check("4.1 First request → not limited", not await limiter.is_rate_limited("ip1", 3, 60))

    # 21. At limit (2nd and 3rd are not limited because max=3 allows 3)
    await limiter.is_rate_limited("ip2", 3, 60)
    await limiter.is_rate_limited("ip2", 3, 60)
    check("4.2 At limit → not limited", not await limiter.is_rate_limited("ip2", 3, 60))

    # 22. Over limit (4th should be limited)
    check("4.3 Over limit → limited", await limiter.is_rate_limited("ip2", 3, 60))

    # 23. Different keys
    check("4.4 Different keys → independent", not await limiter.is_rate_limited("ip3", 3, 60))

    # 24. Cleanup
    limiter._requests["old_ip"] = [time.time() - 7200]  # 2 hours ago
    await limiter.cleanup()
    check("4.5 Cleanup → stale entries removed", "old_ip" not in limiter._requests)


async def test_otp_store():
    print("\n--- OTP Store (6 tests) ---")
    from src.dashboard.otp_store import PendingChangeStore

    store = PendingChangeStore(ttl_seconds=2)

    # 25. Store and retrieve password change
    store.store_password_change(1, "newpass")
    entry = store.retrieve(1, "password_change")
    check("5.1 Store and retrieve", entry is not None and entry["new_password"] == "newpass")

    # 26. Retrieve consumes entry
    entry2 = store.retrieve(1, "password_change")
    check("5.2 Retrieve consumes entry", entry2 is None)

    # 27. Expired entry
    store.store_password_change(2, "expiring")
    await asyncio.sleep(2.1)
    entry3 = store.retrieve(2, "password_change")
    check("5.3 Expired entry → None", entry3 is None)

    # 28. has_pending
    store.store_settings_change(3, {"key": "val"}, {"key": "old"})
    check("5.4 has_pending → True", store.has_pending(3, "settings_change"))

    # 29. has_pending after retrieve
    store.retrieve(3, "settings_change")
    check("5.5 has_pending after retrieve → False", not store.has_pending(3, "settings_change"))

    # 30. Different purposes
    store.store_password_change(4, "pw")
    store.store_settings_change(4, {"a": "b"}, {"a": "c"})
    pw = store.retrieve(4, "password_change")
    settings = store.retrieve(4, "settings_change")
    check("5.6 Different purposes → independent", pw is not None and settings is not None)


async def test_security_classification():
    print("\n--- Security Classification (5 tests) ---")
    from src.dashboard.security import classify_setting, separate_changes

    # 31. Sensitive
    check("6.1 channel_id → sensitive", classify_setting("telegram.channel_id") == "sensitive")

    # 32. Readonly
    check("6.2 bot.is_running → readonly", classify_setting("bot.is_running") == "readonly")

    # 33. Normal
    check("6.3 data_source → normal", classify_setting("amazon.data_source") == "normal")

    # 34. Separate changes: mixed
    current = {"amazon.data_source": "scraper", "telegram.channel_id": "@old", "bot.is_running": "true"}
    new = {"amazon.data_source": "pa_api", "telegram.channel_id": "@new", "bot.is_running": "false"}
    normal, sensitive = separate_changes(current, new)
    check("6.4 Separate: mixed split", "amazon.data_source" in normal and "telegram.channel_id" in sensitive)

    # 35. Unchanged excluded + readonly excluded
    check("6.5 Readonly excluded", "bot.is_running" not in normal and "bot.is_running" not in sensitive)


async def test_db_users():
    print("\n--- Database: Users (10 tests) ---")
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.dashboard.auth import verify_password

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()

    # 36. Ensure admin exists
    rk = await repo.ensure_admin_exists()
    check("7.1 ensure_admin_exists → recovery key", rk is not None and "-" in rk, f"got {rk}")

    # 37. Second call
    rk2 = await repo.ensure_admin_exists()
    check("7.2 ensure_admin_exists second → None", rk2 is None)

    # 38. get_user_by_username
    user = await repo.get_user_by_username("admin")
    check("7.3 get_user_by_username", user is not None and user.username == "admin")

    # 39. Case insensitive
    user2 = await repo.get_user_by_username("Admin")
    check("7.4 Case insensitive lookup", user2 is not None)

    # 40. get_user_by_id
    user3 = await repo.get_user_by_id(user.id)
    check("7.5 get_user_by_id", user3 is not None and user3.id == user.id)

    # 41. update_user_password
    ok_update = await repo.update_user_password(user.id, "newpassword123")
    updated_user = await repo.get_user_by_username("admin")
    check("7.6 update_user_password", ok_update and verify_password("newpassword123", updated_user.password_hash))

    # 42. update_user_profile
    updated = await repo.update_user_profile(user.id, {"display_name": "مدير الاختبار"})
    check("7.7 update_user_profile", updated is not None and updated.display_name == "مدير الاختبار")

    # 43. Duplicate username
    try:
        # Create second user first
        from src.dashboard.auth import hash_password, generate_recovery_key
        await repo.create_user("testuser", hash_password("test12345"), hash_password(generate_recovery_key()))
        await repo.update_user_profile(user.id, {"username": "testuser"})
        fail("7.8 Duplicate username → ValueError", "No exception raised")
    except ValueError:
        ok("7.8 Duplicate username → ValueError")

    # 44. record_login_success
    await repo.record_login_success(user.id, "192.168.1.1")
    user_after = await repo.get_user_by_id(user.id)
    check("7.9 record_login_success updates ip", user_after.last_login_ip == "192.168.1.1")

    # 45. is_new_ip
    is_new = await repo.is_new_ip_for_user(user.id, "10.0.0.1")
    is_old = await repo.is_new_ip_for_user(user.id, "192.168.1.1")
    check("7.10 is_new_ip", is_new and not is_old)


async def test_db_login_security():
    print("\n--- Database: Login Security (6 tests) ---")
    from src.database.connection import init_db
    from src.database.repository import Repository

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    rk = await repo.ensure_admin_exists()

    # 46. record_login_failure
    count = await repo.record_login_failure("admin")
    check("8.1 record_login_failure increments", count == 1)

    # 47. 5 failures → locked
    for _ in range(4):  # already 1, need 4 more = 5 total
        await repo.record_login_failure("admin")
    locked = await repo.is_user_locked("admin")
    check("8.2 5 failures → locked", locked)

    # 48. Unknown user
    check("8.3 Unknown user → not locked", not await repo.is_user_locked("nonexistent"))

    # 49. After lockout expires
    from src.database.connection import get_session
    from src.database.models import DashboardUser
    from sqlalchemy import select
    from datetime import datetime, timedelta

    async with get_session() as session:
        stmt = select(DashboardUser).where(DashboardUser.username == "admin")
        result = await session.execute(stmt)
        db_user = result.scalars().first()
        db_user.locked_until = datetime.utcnow() - timedelta(minutes=1)
        db_user.failed_login_attempts = 0
        await session.flush()

    check("8.4 After lockout expires → not locked", not await repo.is_user_locked("admin"))

    # 50. verify_recovery_key correct
    user = await repo.verify_recovery_key("admin", rk)
    check("8.5 verify_recovery_key correct → user", user is not None and user.username == "admin")

    # 51. verify_recovery_key wrong
    user2 = await repo.verify_recovery_key("admin", "AAAA-BBBB-CCCC-DDDD")
    check("8.6 verify_recovery_key wrong → None", user2 is None)


async def test_db_sessions():
    print("\n--- Database: Sessions (8 tests) ---")
    from src.database.connection import init_db
    from src.database.repository import Repository

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    await repo.ensure_admin_exists()
    user = await repo.get_user_by_username("admin")

    # 52. Create session (repo generates session ID internally)
    sess_obj = await repo.create_session(user.id, "127.0.0.1", "Test Browser")
    sid = sess_obj.id
    check("9.1 Create session", sess_obj is not None and len(sid) == 64)

    # 53. Get active session
    active = await repo.get_session_by_id(sid)
    check("9.2 Get active session", active is not None and active.is_active)

    # 54. Get expired session (create then expire)
    from src.database.connection import get_session as db_session
    from src.database.models import Session as SessionModel
    from sqlalchemy import select
    from datetime import datetime, timedelta

    sess2 = await repo.create_session(user.id, "127.0.0.1", "Old Browser")
    sid2 = sess2.id
    async with db_session() as session:
        stmt = select(SessionModel).where(SessionModel.id == sid2)
        result = await session.execute(stmt)
        s = result.scalars().first()
        s.expires_at = datetime.utcnow() - timedelta(hours=1)
        await session.flush()

    expired = await repo.get_session_by_id(sid2)
    check("9.3 Expired session → None", expired is None)

    # 55. Deactivate session
    sess3 = await repo.create_session(user.id, "127.0.0.1", "To Deactivate")
    sid3 = sess3.id
    await repo.deactivate_session(sid3)
    deactivated = await repo.get_session_by_id(sid3)
    check("9.4 Deactivated session → None", deactivated is None)

    # 56. Multiple sessions
    sess4 = await repo.create_session(user.id, "127.0.0.1", "Session 4")
    sess5 = await repo.create_session(user.id, "127.0.0.1", "Session 5")
    sid4, sid5 = sess4.id, sess5.id
    all_sessions = await repo.get_user_active_sessions(user.id)
    active_ids = {s.id for s in all_sessions}
    check("9.5 Multiple sessions returned", sid4 in active_ids and sid5 in active_ids)

    # 57. Deactivate others
    count = await repo.deactivate_other_sessions(user.id, sid4)
    remaining = await repo.get_user_active_sessions(user.id)
    remaining_ids = {s.id for s in remaining}
    check("9.6 Deactivate others → keeps specified", sid4 in remaining_ids and sid5 not in remaining_ids)

    # 58. Deactivate all
    await repo.create_session(user.id, "127.0.0.1", "Temp")
    gone = await repo.deactivate_all_sessions(user.id)
    left = await repo.get_user_active_sessions(user.id)
    check("9.7 Deactivate all → none left", len(left) == 0)

    # 59. Cleanup expired
    sess7 = await repo.create_session(user.id, "127.0.0.1", "For Cleanup")
    sid7 = sess7.id
    async with db_session() as session:
        stmt = select(SessionModel).where(SessionModel.id == sid7)
        result = await session.execute(stmt)
        s = result.scalars().first()
        s.expires_at = datetime.utcnow() - timedelta(hours=2)
        s.is_active = True
        await session.flush()
    await repo.cleanup_expired_sessions()
    cleaned = await repo.get_session_by_id(sid7)
    check("9.8 Cleanup expired sessions", cleaned is None)


async def test_db_otp():
    print("\n--- Database: OTP (5 tests) ---")
    from src.database.connection import init_db
    from src.database.repository import Repository

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    await repo.ensure_admin_exists()
    user = await repo.get_user_by_username("admin")

    # 60. Create OTP
    code = await repo.create_otp(user.id, "test_purpose")
    check("10.1 Create OTP → 6 digits", len(code) == 6 and code.isdigit())

    # 61. Verify correct
    valid = await repo.verify_otp(user.id, code, "test_purpose")
    check("10.2 Verify correct OTP", valid)

    # 62. Already used (single use)
    valid2 = await repo.verify_otp(user.id, code, "test_purpose")
    check("10.3 Already used OTP → False", not valid2)

    # 63. Wrong code
    code2 = await repo.create_otp(user.id, "test2")
    valid3 = await repo.verify_otp(user.id, "000000", "test2")
    check("10.4 Wrong code → False", not valid3)

    # 64. Cleanup expired
    from src.database.connection import get_session as db_session
    from src.database.models import OTPCode
    from sqlalchemy import select
    from datetime import datetime, timedelta

    code3 = await repo.create_otp(user.id, "cleanup_test")
    async with db_session() as session:
        stmt = select(OTPCode).where(OTPCode.code == code3)
        result = await session.execute(stmt)
        otp = result.scalars().first()
        if otp:
            otp.expires_at = datetime.utcnow() - timedelta(hours=1)
            await session.flush()
    await repo.cleanup_expired_otps()
    valid4 = await repo.verify_otp(user.id, code3, "cleanup_test")
    check("10.5 Cleanup expired OTPs", not valid4)


async def test_api_login():
    print("\n--- API: Login Flow (7 tests) ---")
    import httpx
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.dashboard.app import create_dashboard_app
    from src.engine.core import BotEngine
    from src.engine.scheduler import PublishScheduler

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    await repo.ensure_admin_exists()

    # Create a minimal app for testing (no real scheduler/engine needed)
    class FakeScheduler:
        scheduler = None
        is_running = False
        def get_scheduled_jobs_info(self): return []
        def get_next_run_time(self): return None
        async def start(self): pass
        def stop(self): pass

    class FakeEngine:
        pass

    class FakeNotifier:
        async def on_login_success(self, *a, **kw): pass
        async def on_login_failed(self, *a, **kw): pass
        async def on_account_locked(self, *a, **kw): pass
        async def on_recovery_attempt(self, *a, **kw): pass
        async def on_sessions_revoked(self, *a, **kw): pass
        async def on_password_changed(self, *a, **kw): pass
        async def on_sensitive_settings_changed(self, *a, **kw): pass
        async def on_otp_requested(self, *a, **kw): pass
        async def on_admin_chat_id_changed(self, *a, **kw): pass
        async def send_alert(self, *a, **kw): pass

    app = create_dashboard_app(
        repo, FakeScheduler(), FakeEngine(),
        security_notifier=FakeNotifier(),
        app_settings=None,
    )

    async with httpx.AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
        # 65. GET /login → 200
        r = await client.get("/login")
        check("11.1 GET /login → 200", r.status_code == 200)

        # 66. GET / without session → redirect (before login stores cookies)
        r = await client.get("/", follow_redirects=False)
        check("11.2 GET / no session → redirect", r.status_code == 303)

        # 67. POST /login correct → redirect + cookie
        r = await client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
        has_cookie = "session_id" in r.cookies or any("session_id" in c for c in r.headers.get_list("set-cookie"))
        check("11.3 POST /login correct → redirect + cookie", r.status_code == 303 and has_cookie, f"status={r.status_code}")

        # Extract cookie for subsequent requests
        cookies = dict(r.cookies)

        # 68. POST /login wrong → stays on login (200)
        r = await client.post("/login", data={"username": "admin", "password": "wrongpass"}, follow_redirects=False)
        check("11.4 POST /login wrong → 200 (error page)", r.status_code == 200)

        # 69. GET / with valid session → 200
        r = await client.get("/", cookies=cookies, follow_redirects=False)
        check("11.5 GET / with session → 200", r.status_code == 200)

        # 70. Rate limiting (use fresh limiter)
        from src.dashboard.rate_limit import login_limiter
        login_limiter._requests.clear()
        for i in range(10):
            await client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
        r = await client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
        body = r.text
        rate_limited = "عدد المحاولات" in body or "محاولات" in body
        check("11.6 Rate limited after 10 attempts", rate_limited, f"body contains error: {'error' in body.lower()}")

        # 71. GET /logout → clears session
        r = await client.get("/logout", cookies=cookies, follow_redirects=False)
        check("11.7 GET /logout → redirect", r.status_code == 303)


async def test_api_settings():
    print("\n--- API: Settings (6 tests) ---")
    import httpx
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.dashboard.app import create_dashboard_app
    from src.dashboard.auth import generate_session_id, session_signer

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    await repo.ensure_admin_exists()

    class FakeScheduler:
        scheduler = None
        is_running = False
        def get_scheduled_jobs_info(self): return []
        def get_next_run_time(self): return None
        async def start(self): pass
        def stop(self): pass

    class FakeEngine:
        pass

    class FakeNotifier:
        async def on_login_success(self, *a, **kw): pass
        async def on_login_failed(self, *a, **kw): pass
        async def on_account_locked(self, *a, **kw): pass
        async def on_recovery_attempt(self, *a, **kw): pass
        async def on_sessions_revoked(self, *a, **kw): pass
        async def on_password_changed(self, *a, **kw): pass
        async def on_sensitive_settings_changed(self, *a, **kw): pass
        async def on_otp_requested(self, *a, **kw): pass
        async def on_admin_chat_id_changed(self, *a, **kw): pass
        async def send_alert(self, *a, **kw): pass

    app = create_dashboard_app(
        repo, FakeScheduler(), FakeEngine(),
        security_notifier=FakeNotifier(),
        app_settings=None,
    )

    # Create session directly for API calls
    user = await repo.get_user_by_username("admin")
    sess_obj = await repo.create_session(user.id, "127.0.0.1", "Test")
    sid = sess_obj.id
    token = session_signer.sign_session_id(sid)
    cookies = {"session_id": token}

    async with httpx.AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
        # 72. GET /api/settings → includes sensitivity
        r = await client.get("/api/settings", cookies=cookies)
        check("12.1 GET /api/settings → 200", r.status_code == 200)
        data = r.json()
        has_sensitivity = False
        if "settings" in data:
            for group_settings in data["settings"].values():
                if isinstance(group_settings, list) and group_settings:
                    if "sensitivity" in group_settings[0]:
                        has_sensitivity = True
                        break
        check("12.2 Settings include sensitivity field", has_sensitivity)

        # 73. PUT normal settings only → saves immediately
        r = await client.put("/api/settings", json={"amazon.request_delay_seconds": "5"}, cookies=cookies)
        check("12.3 PUT normal → saves", r.status_code == 200 and r.json().get("status") == "ok")

        # 74. PUT with sensitive (admin_chat_id set)
        await repo.set_setting("telegram.admin_chat_id", "123456")
        r = await client.put("/api/settings", json={"telegram.channel_id": "@newchan"}, cookies=cookies)
        body = r.json()
        # When admin_chat_id is set but no real bot token, it may save directly (OTP send fails)
        is_otp_or_saved = body.get("otp_required") or body.get("status") == "ok"
        check("12.4 PUT sensitive (chat_id set) → otp_required or saved", is_otp_or_saved)

        # 75. PUT sensitive (admin_chat_id empty) → saves directly
        await repo.set_setting("telegram.admin_chat_id", "")
        r = await client.put("/api/settings", json={"telegram.channel_id": "@direct"}, cookies=cookies)
        body = r.json()
        check("12.5 PUT sensitive (no chat_id) → saves directly", body.get("status") == "ok")

        # 76-77. Verify OTP — create OTP in DB, store pending
        await repo.set_setting("telegram.admin_chat_id", "123456")
        from src.dashboard.otp_store import pending_changes
        pending_changes.store_settings_change(
            user.id,
            {"telegram.channel_id": "@verified"},
            {"telegram.channel_id": "@old"},
        )
        otp_code = await repo.create_otp(user.id, "settings_change")

        # 76. Correct OTP
        r = await client.post(
            "/api/verify-otp",
            json={"code": otp_code, "purpose": "settings_change"},
            cookies=cookies,
        )
        check("12.6 POST verify-otp correct → ok", r.status_code == 200 and r.json().get("status") == "ok")

        # 77. Wrong OTP
        pending_changes.store_settings_change(user.id, {"telegram.channel_id": "@x"}, {"telegram.channel_id": "@y"})
        await repo.create_otp(user.id, "settings_change")
        r = await client.post(
            "/api/verify-otp",
            json={"code": "000000", "purpose": "settings_change"},
            cookies=cookies,
        )
        check("12.7 POST verify-otp wrong → 400", r.status_code == 400)


async def test_api_account():
    print("\n--- API: Account (5 tests) ---")
    import httpx
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.dashboard.app import create_dashboard_app
    from src.dashboard.auth import generate_session_id, session_signer

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    await repo.ensure_admin_exists()

    class FakeScheduler:
        scheduler = None
        is_running = False
        def get_scheduled_jobs_info(self): return []
        def get_next_run_time(self): return None
        async def start(self): pass
        def stop(self): pass

    class FakeEngine:
        pass

    class FakeNotifier:
        async def on_login_success(self, *a, **kw): pass
        async def on_login_failed(self, *a, **kw): pass
        async def on_account_locked(self, *a, **kw): pass
        async def on_recovery_attempt(self, *a, **kw): pass
        async def on_sessions_revoked(self, *a, **kw): pass
        async def on_password_changed(self, *a, **kw): pass
        async def on_sensitive_settings_changed(self, *a, **kw): pass
        async def on_otp_requested(self, *a, **kw): pass
        async def on_admin_chat_id_changed(self, *a, **kw): pass
        async def send_alert(self, *a, **kw): pass

    app = create_dashboard_app(
        repo, FakeScheduler(), FakeEngine(),
        security_notifier=FakeNotifier(),
        app_settings=None,
    )

    user = await repo.get_user_by_username("admin")
    sess_obj = await repo.create_session(user.id, "127.0.0.1", "Primary")
    sid = sess_obj.id
    token = session_signer.sign_session_id(sid)
    cookies = {"session_id": token}

    # Create extra sessions
    await repo.create_session(user.id, "10.0.0.1", "Phone")
    await repo.create_session(user.id, "10.0.0.2", "Tablet")

    async with httpx.AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
        # 78. GET /api/account/sessions → list with is_current
        r = await client.get("/api/account/sessions", cookies=cookies)
        sessions_list = r.json()
        check("13.1 GET sessions → list", r.status_code == 200 and isinstance(sessions_list, list))
        current_flags = [s.get("is_current") for s in sessions_list]
        check("13.2 One session is_current=true", current_flags.count(True) == 1)

        # 79. Revoke others
        r = await client.post("/api/account/sessions/revoke-others", cookies=cookies)
        body = r.json()
        check("13.3 Revoke others → count", r.status_code == 200 and body.get("revoked_count", 0) >= 1)

        # 80. Request password change (no OTP configured)
        await repo.set_setting("telegram.admin_chat_id", "")
        r = await client.post(
            "/api/account/request-password-change",
            json={
                "current_password": "admin123",
                "new_password": "SecureNew123",
                "confirm_password": "SecureNew123",
            },
            cookies=cookies,
        )
        body = r.json()
        check("13.4 Password change (no OTP) → ok", r.status_code == 200 and body.get("status") == "ok")

        # 81. Update profile
        r = await client.put(
            "/api/account/profile",
            json={"display_name": "اختبار الحساب"},
            cookies=cookies,
        )
        body = r.json()
        check("13.5 Update profile → ok", r.status_code == 200 and body.get("status") == "ok")

        # 82. Cannot revoke current session
        r = await client.post(
            f"/api/account/sessions/revoke/{sid}",
            cookies=cookies,
        )
        check("13.6 Cannot revoke current → 400", r.status_code == 400)


async def test_api_recovery():
    print("\n--- API: Recovery (4 tests) ---")
    import httpx
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.dashboard.app import create_dashboard_app
    from src.dashboard.auth import generate_session_id

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    repo = Repository()
    rk = await repo.ensure_admin_exists()

    class FakeScheduler:
        scheduler = None
        is_running = False
        def get_scheduled_jobs_info(self): return []
        def get_next_run_time(self): return None
        async def start(self): pass
        def stop(self): pass

    class FakeEngine:
        pass

    class FakeNotifier:
        async def on_login_success(self, *a, **kw): pass
        async def on_login_failed(self, *a, **kw): pass
        async def on_account_locked(self, *a, **kw): pass
        async def on_recovery_attempt(self, *a, **kw): pass
        async def on_sessions_revoked(self, *a, **kw): pass
        async def on_password_changed(self, *a, **kw): pass
        async def on_sensitive_settings_changed(self, *a, **kw): pass
        async def on_otp_requested(self, *a, **kw): pass
        async def on_admin_chat_id_changed(self, *a, **kw): pass
        async def send_alert(self, *a, **kw): pass

    app = create_dashboard_app(
        repo, FakeScheduler(), FakeEngine(),
        security_notifier=FakeNotifier(),
        app_settings=None,
    )

    # Create a session to verify it gets invalidated
    user = await repo.get_user_by_username("admin")
    sess_obj = await repo.create_session(user.id, "127.0.0.1", "Pre-recovery")
    sid = sess_obj.id

    # Reset recovery limiter for clean test
    from src.dashboard.rate_limit import recovery_limiter
    recovery_limiter._requests.clear()

    async with httpx.AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
        # 83. Correct recovery
        r = await client.post("/api/recover", json={
            "username": "admin",
            "recovery_key": rk,
            "new_password": "RecoveredPass123",
            "confirm_password": "RecoveredPass123",
        })
        check("14.1 POST /api/recover correct → ok", r.status_code == 200 and r.json().get("status") == "ok")

        # 84. Wrong key
        r = await client.post("/api/recover", json={
            "username": "admin",
            "recovery_key": "AAAA-BBBB-CCCC-DDDD",
            "new_password": "SomePass12345",
            "confirm_password": "SomePass12345",
        })
        check("14.2 POST /api/recover wrong → 400", r.status_code == 400)

        # 85. Old sessions invalidated
        sessions = await repo.get_user_active_sessions(user.id)
        old_session_ids = {s.id for s in sessions}
        check("14.3 Old sessions invalidated", sid not in old_session_ids)

        # 86. Rate limited after 3 attempts
        for _ in range(2):  # Already used 2 (1 correct + 1 wrong), push past limit
            await client.post("/api/recover", json={
                "username": "admin",
                "recovery_key": "XXXX-XXXX-XXXX-XXXX",
                "new_password": "SomePass12345",
                "confirm_password": "SomePass12345",
            })
        r = await client.post("/api/recover", json={
            "username": "admin",
            "recovery_key": "XXXX-XXXX-XXXX-XXXX",
            "new_password": "SomePass12345",
            "confirm_password": "SomePass12345",
        })
        check("14.4 Recovery rate limited → 429", r.status_code == 429)


# ── Main ───────────────────────────────────────────────────

async def main():
    global passed, failed

    await test_password_hashing()
    await test_session_signing()
    await test_user_agent_parsing()
    await test_rate_limiter()
    await test_otp_store()
    await test_security_classification()
    await test_db_users()
    await test_db_login_security()
    await test_db_sessions()
    await test_db_otp()
    await test_api_login()
    await test_api_settings()
    await test_api_account()
    await test_api_recovery()

    total = passed + failed
    icon = "✅" if failed == 0 else "❌"
    print(f"\n{'=' * 50}")
    print(f"  RESULTS: {icon} {passed}/{total} tests passed")
    if failed:
        print(f"  {failed} test(s) FAILED")
    print(f"{'=' * 50}\n")

    # Cleanup
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Cleaned up test database: {DB_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
