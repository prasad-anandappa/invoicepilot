"""Migration round-trip test: alembic upgrade head creates the full v1
schema, downgrade base removes it, and upgrade works again afterwards —
the tested rollback path the execution pack requires."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "users",
    "business_profiles",
    "clients",
    "invoices",
    "invoice_items",
    "paypal_orders",
    "reminders",
    "agent_events",
    "idempotency_keys",
}


def _config(db_url: str) -> Config:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_upgrade_downgrade_roundtrip(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/migrated.db"
    # migrations/env.py prefers the app's DATABASE_URL; point it at the
    # scratch database so nothing touches dev/prod data.
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _config(db_url)
    engine = create_engine(db_url)

    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables

    command.downgrade(cfg, "base")
    tables = set(inspect(engine).get_table_names())
    assert not (EXPECTED_TABLES & tables)

    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
