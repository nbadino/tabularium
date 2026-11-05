"""The training status is polled every few seconds: an unchanged train.log
(tens of MB with progress bars) must not be read and decoded again each time."""
from __future__ import annotations

from pathlib import Path

from app.services import trainer_metrics


def test_an_unchanged_log_is_read_once(tmp_path, monkeypatch):
    log = tmp_path / "train.log"
    log.write_text("{'loss': 1.0, 'step': 1}\n", encoding="utf-8")
    reads: list[Path] = []
    real = Path.read_text

    def counting(self, *a, **k):
        if self.name == "train.log":
            reads.append(self)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    first = trainer_metrics._log_tail_logfile(tmp_path)
    assert trainer_metrics._log_tail_logfile(tmp_path) == first
    assert len(reads) == 1
    with log.open("a", encoding="utf-8") as fh:
        fh.write("{'loss': 0.5, 'step': 2}\n")
    assert len(trainer_metrics._log_tail_logfile(tmp_path)) == 2
    assert len(reads) == 2
