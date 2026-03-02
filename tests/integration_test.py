#!/usr/bin/env python3
"""End-to-end integration test for the Amazon-Telegram bot.

Validates the full pipeline — database CRUD, formatter, rotation,
dedup, and a dry-run publish cycle — using a throwaway test database.

Usage::

    python tests/integration_test.py
"""

from __future__ import annotations

# ── Must set DB path BEFORE any project imports ────────────
import os, sys

os.environ["DATABASE_PATH"] = "data/test_bot.db"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from src.amazon.base import AmazonClient
from src.amazon.models import Product
from src.database.repository import Repository
from src.engine.formatter import MessageFormatter
from src.engine.rotation import CategoryRotator
from src.engine.dedup import DuplicateChecker

# ── Flags ──────────────────────────────────────────────────
LIVE_TEST = False  # set True to hit real Amazon (scraper)


# ── Mock classes ───────────────────────────────────────────

def _make_product(i: int, keywords: str = "test") -> Product:
    return Product(
        asin=f"B09MOCK{i:03d}",
        title=f"منتج تجريبي {i} - سماعات بلوتوث لاسلكية عالية الجودة",
        brand="TestBrand",
        original_price=500.0,
        current_price=350.0,
        currency="SAR",
        savings_percent=30.0,
        savings_amount=150.0,
        features=[
            "ميزة أولى رائعة جداً",
            "ميزة ثانية ممتازة",
            "ميزة ثالثة عالية الجودة",
        ],
        image_url="https://via.placeholder.com/300",
        affiliate_url=f"https://amazon.sa/dp/B09MOCK{i:03d}?tag=test",
        is_prime=True,
        rating=4.5,
        reviews_count=1234,
        keyword_used=keywords,
    )


class MockAmazonClient(AmazonClient):
    """Returns deterministic sample products."""

    async def search_products(self, keywords: str, **kwargs) -> list[Product]:
        return [_make_product(i, keywords) for i in range(5)]

    async def close(self) -> None:
        pass


class MockPublisher:
    """Records publish calls instead of sending to Telegram."""

    def __init__(self):
        self.published: list[dict] = []

    async def publish(
        self,
        channel_id: str,
        text: str,
        image_url: str | None = None,
        parse_mode: str = "HTML",
    ) -> int:
        self.published.append({
            "channel": channel_id,
            "text": text,
            "image": image_url,
            "parse_mode": parse_mode,
        })
        return len(self.published)  # fake message_id

    async def close(self) -> None:
        pass


class MockNotifier:
    """Silently absorbs notification calls."""

    async def send_alert(self, *a, **kw) -> None:
        pass

    async def send_error(self, *a, **kw) -> None:
        pass

    async def send_startup_notification(self) -> None:
        pass

    async def send_shutdown_notification(self) -> None:
        pass

    async def close(self) -> None:
        pass


# ── Test harness ───────────────────────────────────────────

_passed = 0
_failed = 0
_total = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed, _total
    _total += 1
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


# ── Test sections ──────────────────────────────────────────

async def test_1_database(repo: Repository) -> None:
    """Test 1: Database CRUD operations."""
    print("\n--- Test 1: Database Operations ---")

    # Categories
    cat1 = await repo.create_category({
        "name": "Electronics",
        "name_ar": "إلكترونيات",
        "amazon_search_index": "Electronics",
        "is_active": True,
        "sort_order": 0,
    })
    _check("1.1 Create category", cat1 is not None and cat1.id > 0)

    cat2 = await repo.create_category({
        "name": "Clothes",
        "name_ar": "ملابس",
        "amazon_search_index": "Fashion",
        "is_active": True,
        "sort_order": 1,
    })
    cat3 = await repo.create_category({
        "name": "Temporary",
        "name_ar": "مؤقت",
        "is_active": False,
        "sort_order": 99,
    })

    # Read
    fetched = await repo.get_category(cat1.id)
    _check("1.2 Read category", fetched is not None and fetched.name == "Electronics")

    # Update
    updated = await repo.update_category(cat1.id, {"name_ar": "أجهزة إلكترونية"})
    _check("1.3 Update category", updated is not None and updated.name_ar == "أجهزة إلكترونية")

    # Delete
    deleted = await repo.delete_category(cat3.id)
    _check("1.4 Delete category", deleted is True)
    gone = await repo.get_category(cat3.id)
    _check("1.5 Verify deletion", gone is None)

    # List
    all_cats = await repo.get_all_categories(include_inactive=True)
    _check("1.6 List categories", len(all_cats) >= 2)

    # Keywords
    kw1 = await repo.create_keyword(cat1.id, "سماعة", sort_order=0)
    kw2 = await repo.create_keyword(cat1.id, "شاحن", sort_order=1)
    kw3 = await repo.create_keyword(cat1.id, "شاشة", sort_order=2)
    _check("1.7 Create keywords", kw1.id > 0 and kw2.id > 0 and kw3.id > 0)

    kw4 = await repo.create_keyword(cat2.id, "رجالي", sort_order=0)
    kw5 = await repo.create_keyword(cat2.id, "حريمي", sort_order=1)

    keywords = await repo.get_keywords_for_category(cat1.id)
    _check("1.8 List keywords", len(keywords) == 3)

    toggled = await repo.update_keyword(kw3.id, {"is_active": False})
    _check("1.9 Toggle keyword", toggled is not None and not toggled.is_active)
    toggled_back = await repo.update_keyword(kw3.id, {"is_active": True})
    _check("1.10 Re-toggle keyword", toggled_back.is_active is True)

    # Schedule
    sched = await repo.update_schedule({
        "schedule_type": "fixed_times",
        "fixed_times": '["09:00","15:00","21:00"]',
        "interval_minutes": 0,
        "timezone": "Africa/Cairo",
        "is_active": True,
        "products_per_batch": 1,
    })
    _check("1.11 Update schedule", sched is not None and sched.is_active)

    active_sched = await repo.get_active_schedule()
    _check("1.12 Get active schedule", active_sched is not None)

    # Templates (note: init_db seeds defaults)
    templates = await repo.get_all_templates()
    _check("1.13 Templates exist (seeded)", len(templates) > 0)

    active_tpl = await repo.get_active_template()
    _check("1.14 Active template exists", active_tpl is not None)

    # Settings
    await repo.set_setting("bot.is_running", "true")
    val = await repo.get_setting("bot.is_running")
    _check("1.15 Set/get setting", val == "true")

    await repo.set_setting("telegram.channel_id", "@test_channel")
    ch = await repo.get_setting("telegram.channel_id")
    _check("1.16 Channel setting", ch == "@test_channel")

    # Logs
    await repo.log_event(
        level="INFO",
        component="test",
        action="test_action",
        message="Integration test log entry",
    )
    logs = await repo.get_logs(limit=1)
    _check("1.17 Write/read log", len(logs) >= 1 and logs[0].action == "test_action")

    count = await repo.get_logs_count()
    _check("1.18 Logs count", count >= 1)

    # Dashboard stats
    stats = await repo.get_dashboard_stats()
    _check("1.19 Dashboard stats", isinstance(stats, dict))

    print(f"  Database tests done ({len(all_cats)} categories, {len(keywords)} keywords)")


async def test_2_formatter(repo: Repository) -> None:
    """Test 2: Message formatting."""
    print("\n--- Test 2: Formatter ---")

    formatter = MessageFormatter()
    template = await repo.get_active_template()
    assert template is not None, "No active template"

    # Full product
    product = _make_product(1, "سماعات")
    product.category = "إلكترونيات"
    rendered = formatter.render(template.body, product, parse_mode=template.parse_mode)
    _check("2.1 Render with full product", len(rendered) > 50)
    _check("2.2 No unreplaced vars", "{" not in rendered or "}" not in rendered.replace("{", ""))

    # Check {title} was substituted
    _check("2.3 Title present", "منتج تجريبي" in rendered)

    print(f"  --- Rendered message ({len(rendered)} chars) ---")
    for line in rendered.split("\n")[:8]:
        print(f"  | {line}")
    print("  | ...")

    # Empty fields
    empty_product = Product(
        asin="B09EMPTY",
        title="منتج بدون تفاصيل",
        original_price=100.0,
        current_price=100.0,
        currency="SAR",
        affiliate_url="https://amazon.sa/dp/B09EMPTY",
    )
    rendered_empty = formatter.render(template.body, empty_product, parse_mode=template.parse_mode)
    _check("2.4 Empty fields (no crash)", len(rendered_empty) > 0)

    # Very long title
    long_product = _make_product(2)
    long_product.title = "أ" * 500
    rendered_long = formatter.render(template.body, long_product, parse_mode=template.parse_mode)
    _check("2.5 Long title (no crash)", len(rendered_long) > 0)


async def test_3_rotation(repo: Repository) -> None:
    """Test 3: Category rotation round-robin."""
    print("\n--- Test 3: Rotation ---")

    rotator = CategoryRotator(repo)
    sequence: list[str] = []

    for step in range(10):
        cat, kw = await rotator.get_next()
        if cat is None or kw is None:
            sequence.append("(none)")
        else:
            label = f"[{cat.name}] {kw.keyword}"
            sequence.append(label)
            print(f"  Step {step + 1}: {label}")
        await rotator.advance()

    _check("3.1 Got 10 rotation steps", len(sequence) == 10)
    _check("3.2 No empty steps", "(none)" not in sequence)
    # First two should be from same category (Electronics has 3 keywords)
    _check(
        "3.3 Round-robin starts with first category",
        "[Electronics]" in sequence[0] or "[Clothes]" in sequence[0],
    )
    # After all keywords in cat1, should switch to cat2
    _check("3.4 Category switching happens", len(set(sequence)) > 2)


async def test_4_dedup(repo: Repository) -> None:
    """Test 4: Duplicate detection."""
    print("\n--- Test 4: Dedup ---")

    checker = DuplicateChecker(repo)

    # Save a "published" product
    await repo.save_published_product({
        "asin": "B09TEST001",
        "title": "Test duplicate product",
        "brand": "TestBrand",
        "original_price": 100.0,
        "current_price": 80.0,
        "currency": "SAR",
        "savings_percent": 20.0,
        "savings_amount": 20.0,
        "image_url": "",
        "affiliate_url": "",
        "status": "published",
    })
    await repo.save_published_product({
        "asin": "B09TEST002",
        "title": "Another duplicate",
        "brand": "TestBrand",
        "original_price": 200.0,
        "current_price": 150.0,
        "currency": "SAR",
        "savings_percent": 25.0,
        "savings_amount": 50.0,
        "image_url": "",
        "affiliate_url": "",
        "status": "published",
    })

    is_dup = await checker.is_duplicate("B09TEST001")
    _check("4.1 Known ASIN is duplicate", is_dup is True)

    is_new = await checker.is_duplicate("B09OTHER99")
    _check("4.2 Unknown ASIN is not duplicate", is_new is False)

    # Filter a mixed list
    products = [
        _make_product(1),   # B09MOCK001 — new
        Product(asin="B09TEST001", title="x", affiliate_url="", image_url=""),  # dup
        _make_product(3),   # B09MOCK003 — new
        Product(asin="B09TEST002", title="y", affiliate_url="", image_url=""),  # dup
        _make_product(5),   # B09MOCK005 — new
    ]
    filtered = await checker.filter_new(products)
    _check("4.3 filter_new count", len(filtered) == 3, f"got {len(filtered)}")
    _check(
        "4.4 Correct ASINs remain",
        {p.asin for p in filtered} == {"B09MOCK001", "B09MOCK003", "B09MOCK005"},
    )


async def test_5_scraper() -> None:
    """Test 5: Live Amazon scraper (optional)."""
    print("\n--- Test 5: Amazon Scraper ---")

    if not LIVE_TEST:
        print("  SKIP  Live scraper test disabled (set LIVE_TEST=True to enable)")
        return

    from src.amazon.scraper import AmazonScraper

    scraper = AmazonScraper(partner_tag="test-tag")
    try:
        products = await scraper.search_products(
            keywords="سماعات بلوتوث",
            search_index="All",
            item_count=5,
        )
        _check("5.1 Scraper returned products", len(products) > 0)
        for i, p in enumerate(products[:3]):
            _check(
                f"5.{i+2} Product {p.asin} valid",
                bool(p.asin) and bool(p.title) and p.current_price > 0,
                f"asin={p.asin}, price={p.current_price}",
            )
            print(f"       {p.asin} | {p.title[:50]} | {p.current_price} SAR")
    except Exception as exc:
        _check("5.1 Scraper error", False, str(exc))
    finally:
        await scraper.close()


async def test_6_pipeline(repo: Repository) -> None:
    """Test 6: Full pipeline dry run."""
    print("\n--- Test 6: Full Pipeline (dry run) ---")

    from src.engine.core import BotEngine

    mock_amazon = MockAmazonClient()
    mock_publisher = MockPublisher()
    mock_notifier = MockNotifier()
    formatter = MessageFormatter()

    engine = BotEngine(
        amazon_client=mock_amazon,
        publisher=mock_publisher,
        notifier=mock_notifier,
        repository=repo,
        formatter=formatter,
    )

    # Ensure settings are correct for the test
    await repo.set_setting("bot.is_running", "true")
    await repo.set_setting("telegram.channel_id", "@test_channel")

    # Run one publish cycle
    published_before = await repo.get_published_count()
    await engine.run_publish_cycle()
    published_after = await repo.get_published_count()

    _check(
        "6.1 Publisher received call(s)",
        len(mock_publisher.published) >= 1,
        f"got {len(mock_publisher.published)} calls",
    )
    _check(
        "6.2 Product saved to DB",
        published_after > published_before,
        f"before={published_before}, after={published_after}",
    )

    if mock_publisher.published:
        msg = mock_publisher.published[0]
        _check("6.3 Published to correct channel", msg["channel"] == "@test_channel")
        _check("6.4 Message has content", len(msg["text"]) > 50)

        print(f"  --- Published message ({len(msg['text'])} chars) ---")
        for line in msg["text"].split("\n")[:8]:
            print(f"  | {line}")
        print("  | ...")

    # Check stats updated
    total = await repo.get_setting_int("bot.total_published")
    _check("6.5 Total published counter updated", total >= 1, f"total={total}")

    last_at = await repo.get_setting("bot.last_publish_at")
    _check("6.6 Last publish timestamp set", last_at is not None and len(last_at) > 0)


# ── Runner ─────────────────────────────────────────────────

async def run_all() -> None:
    global _passed, _failed, _total

    # Clean slate
    db_path = Path("data/test_bot.db")
    if db_path.exists():
        db_path.unlink()
        print(f"Removed existing test database: {db_path}")

    # Init DB
    from src.database.connection import init_db

    await init_db()
    repo = Repository()
    print("Test database initialised.\n")

    # Run test sections
    await test_1_database(repo)
    await test_2_formatter(repo)
    await test_3_rotation(repo)
    await test_4_dedup(repo)
    await test_5_scraper()
    await test_6_pipeline(repo)

    # Summary
    print("\n" + "=" * 50)
    print(f"  RESULTS: {_passed}/{_total} tests passed")
    if _failed:
        print(f"  {_failed} test(s) FAILED")
    else:
        print("  All tests passed!")
    print("=" * 50)

    # Cleanup
    if db_path.exists():
        db_path.unlink()
        print(f"\nCleaned up test database: {db_path}")

    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_all())
