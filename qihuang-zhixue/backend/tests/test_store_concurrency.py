from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

from app.profile.schema import EvidenceCard
from app.profile.store import add_evidence, connect, init_db


def test_wal_mode_enabled(tmp_path: Path) -> None:
    db_path = tmp_path / "profile-concurrency.db"

    with closing(connect(db_path)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"


def test_concurrent_writes_do_not_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "profile-concurrent-writes.db"
    worker_count = 12
    writes_per_worker = 4
    init_db(db_path)
    barrier = Barrier(worker_count)

    def write_evidence(worker_index: int) -> int:
        barrier.wait()
        for write_index in range(writes_per_worker):
            add_evidence(
                "concurrent-user",
                EvidenceCard(
                    dimension="knowledge_base",
                    source="concurrency-test",
                    fact=f"worker-{worker_index}-fact-{write_index}",
                    objective_signal={"worker": worker_index, "write": write_index},
                    weight=1.0,
                ),
                db_path=db_path,
            )
        return writes_per_worker

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(write_evidence, range(worker_count)))

    with closing(connect(db_path)) as conn:
        row_count = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE user_id = ?",
            ("concurrent-user",),
        ).fetchone()[0]

    assert sum(results) == worker_count * writes_per_worker
    assert row_count == worker_count * writes_per_worker
