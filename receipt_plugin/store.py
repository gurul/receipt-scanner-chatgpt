"""Transactional, account-scoped SQLite persistence. No user-supplied file paths."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time

import cost_engine as engine
from .models import BusinessConfig, Receipt, Alias


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accounts (
                    account TEXT PRIMARY KEY, config TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    receipt TEXT NOT NULL, rows TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS receipt_account ON receipts(account, id);
                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, expires INTEGER NOT NULL, data BLOB NOT NULL);
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def config(self, db, account):
        row = db.execute("SELECT config, revision FROM accounts WHERE account=?", (account,)).fetchone()
        if not row:
            raise ValueError("Business is not configured. Call configure_business with your business config first.")
        return json.loads(row["config"]), row["revision"]

    def ledger(self, db, account):
        return [row for receipt in db.execute("SELECT rows FROM receipts WHERE account=? ORDER BY id", (account,))
                for row in json.loads(receipt["rows"])]

    def snapshot(self, db, account):
        config, revision = self.config(db, account)
        ledger = self.ledger(db, account)
        prices = engine.latest_prices(ledger)
        return {"revision": revision, "ledger_rows": len(ledger), "latest_prices": prices,
                "product_costs": engine.compute_costs(prices, config),
                "unmapped_count": sum(row[4] == "Uncategorized" for row in ledger)}

    def get_business(self, account):
        with self.connect() as db:
            row = db.execute("SELECT config, revision FROM accounts WHERE account=?", (account,)).fetchone()
            return {"configured": bool(row), "revision": row["revision"] if row else 0,
                    "config": json.loads(row["config"]) if row else None}

    def remap(self, db, account, config):
        for record in db.execute("SELECT id, rows FROM receipts WHERE account=?", (account,)).fetchall():
            rows = json.loads(record["rows"])
            for row in rows:
                mapped = engine.map_item(row[2], config)
                row[3] = mapped["canonical_name"].removeprefix(engine.UNMAPPED_PREFIX)
                row[4] = mapped["category"]
            db.execute("UPDATE receipts SET rows=? WHERE id=? AND account=?",
                       (json.dumps(rows), record["id"], account))

    def configure(self, account, config: BusinessConfig, expected_revision: int):
        data = config.engine_dict()
        with self.connect(write=True) as db:
            current = db.execute("SELECT revision FROM accounts WHERE account=?", (account,)).fetchone()
            revision = current["revision"] if current else 0
            if revision != expected_revision:
                raise ValueError("Business changed. Read get_business and retry using its current revision.")
            db.execute("INSERT INTO accounts VALUES (?, ?, ?) ON CONFLICT(account) DO UPDATE SET "
                       "config=excluded.config, revision=excluded.revision",
                       (account, json.dumps(data), revision + 1))
            self.remap(db, account, data)
            return self.snapshot(db, account)

    def costs(self, account):
        with self.connect() as db:
            return self.snapshot(db, account)

    def receipt(self, account, receipt: Receipt, commit: bool):
        data = receipt.model_dump(mode="json", exclude_none=True)
        with self.connect(write=commit) as db:
            config, _ = self.config(db, account)
            ledger = self.ledger(db, account)
            before = engine.compute_costs(engine.latest_prices(ledger), config)
            rows, summary = engine.receipt_rows(data, ledger, config)
            after = engine.compute_costs(engine.latest_prices(ledger + rows), config)
            if commit:
                db.execute("INSERT INTO receipts(account, receipt, rows) VALUES (?, ?, ?)",
                           (account, json.dumps(data), json.dumps(rows)))
            return {"recorded": commit, "receipt": summary, "product_costs_before": before,
                    "product_costs_after": after, "ledger_rows": len(ledger) + (len(rows) if commit else 0)}

    def aliases(self, account, canonical: str, alias: Alias, expected_revision: int):
        with self.connect(write=True) as db:
            config, revision = self.config(db, account)
            if revision != expected_revision:
                raise ValueError("Business changed. Read get_business and retry using its current revision.")
            config["items"]["aliases"][canonical] = alias.model_dump()
            config = BusinessConfig.model_validate(config).engine_dict()
            db.execute("UPDATE accounts SET config=?, revision=revision+1 WHERE account=?",
                       (json.dumps(config), account))
            self.remap(db, account, config)
            return self.snapshot(db, account)

    def purchases(self, account, limit=50, offset=0, unmapped_only=False):
        with self.connect() as db:
            self.config(db, account)
            rows = self.ledger(db, account)
            if unmapped_only:
                rows = [r for r in rows if r[4] == "Uncategorized"]
            return {"total": len(rows), "offset": offset,
                    "items": [dict(zip(engine.PURCHASES_COLUMNS, r)) for r in rows[offset:offset+limit]]}

    def export(self, account, lifetime=300):
        with self.connect(write=True) as db:
            config, _ = self.config(db, account)
            ledger = self.ledger(db, account)
            prices = engine.latest_prices(ledger)
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "purchases.xlsx")
                engine.write_workbook(path, ledger, [r for r in ledger if r[4] == "Uncategorized"],
                                      prices, engine.compute_costs(prices, config), config)
                data = Path(path).read_bytes()
            export_id, expires = secrets.token_urlsafe(24), int(time.time()) + lifetime
            db.execute("DELETE FROM exports WHERE expires <= ?", (int(time.time()),))
            db.execute("INSERT INTO exports VALUES (?, ?, ?, ?)", (export_id, account, expires, data))
            return export_id, expires

    def download(self, account, export_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM exports WHERE account=? AND id=? AND expires>?",
                             (account, export_id, int(time.time()))).fetchone()
            return bytes(row["data"]) if row else None
