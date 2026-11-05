"""The cost estimate of a cloud instance counts only the hours it was running."""
from __future__ import annotations

from app.adapters import jobs as jobstore
from app.db import connect, init_db


def _upsert(state: str, terminal: bool) -> None:
    jobstore.cloud_upsert(
        owner_id=None, provider="vast", remote_id="pause-resume", state=state,
        cost={"hourly_rate": 2.0}, terminal=terminal,
    )


def _shift(**hours: float) -> None:
    """Move the recorded timestamps into the past, as if time had passed."""
    with connect() as conn:
        for column, value in hours.items():
            conn.execute(
                f"UPDATE jobs SET {column}=datetime({column}, ?) "
                "WHERE kind='cloud_resource' AND remote_job_id='pause-resume'",
                (f"-{value} hours",),
            )


def test_a_paused_and_resumed_instance_is_not_billed_for_the_pause():
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE remote_job_id='pause-resume'")
    _upsert("running", False)
    _shift(started_at=10)               # ran 10 h
    _upsert("stopped", True)
    _shift(started_at=5, ended_at=5)    # then stayed stopped 5 h
    _upsert("running", False)
    _shift(started_at=1)                # and has run 1 h since the resume
    hours, rate = jobstore.cloud_usage("vast", "pause-resume")
    assert rate == 2.0
    assert abs(hours - 11.0) < 0.05, hours
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE remote_job_id='pause-resume'")
