from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

from app.profile.schema import EvidenceCard
from app.profile.store import add_evidence, connect, init_db


def test_wal_mode_enabled(tmp_path: Path) -> None:
    """WAL 由 `init_db` 负责切换，`connect` 刻意不碰它。

    这里必须先 `init_db`：把切换挪出 `connect` 正是为了让并发建连不再抢独占锁。
    断言不变——库建好之后每个连接读到的都该是 wal。
    """

    db_path = tmp_path / "profile-concurrency.db"
    init_db(db_path)

    with closing(connect(db_path)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"


def test_concurrent_writes_do_not_lock(tmp_path: Path) -> None:
    """12 线程 × 4 次写，一次都不能丢。

    这条测试踩过两次坑，都记在这里：
    1. `connect()` 里每次都执行 `PRAGMA journal_mode=WAL`——那是切换操作，要独占
       锁，并发时抢输的一方把连接降级成只读（报 readonly 而不是 locked）。
       已改成只在 `init_db` 里切一次。
    2. 即便如此，SQLite 在并发重建 `-shm` 索引时仍会偶发只读。纯 sqlite3 对照
       实验（不经本项目任何代码）同样每轮失败 2–7 次，所以这不是封装 bug。
       `add_evidence` 现在对瞬时只读/锁冲突重试 5 次，实测连续 3 轮 48/48。
    """

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
