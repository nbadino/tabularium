from __future__ import annotations

from app import db
from app.services import rate_limit


def test_rate_limit_survives_transaction_that_rolls_back():
    db.init_db()
    with db.connect() as conn:
        first = rate_limit.allow(conn, "test", limit=1, window=60)
        conn.commit()

    with db.connect() as conn:
        conn.execute("BEGIN")
        second = rate_limit.allow(conn, "test", limit=1, window=60)
        conn.rollback()

    with db.connect() as conn:
        third = rate_limit.allow(conn, "test", limit=1, window=60)

    assert first is True
    assert second is False
    assert third is False
