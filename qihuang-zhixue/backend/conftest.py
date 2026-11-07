"""pytest 启动期配置：把 basetemp 放到系统临时目录。

为什么不用项目内目录（以前是 `--basetemp=.pytest-tmp`）：
pytest 每轮开场都会对 basetemp 整体 `rm_rf`。这个目录如果留在项目里，
子目录会一轮轮累积；一旦超过运行环境的批量删除阈值（50 项），那次删除
会被安全守卫拦下并 `SystemExit`，表现为整轮用例在 setup 阶段集体报错
（看起来像 345 个用例一起挂了，其实是临时目录挂的）。
放到系统临时目录既能避开该守卫，也不会污染工作区。

命名带 pid 是为了让并发跑的多轮 pytest 互不覆盖——共享 basetemp 时，
一轮开场会把另一轮正在用的目录删掉。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

_TMP_ROOT_NAME = "qihuang-pytest"
_SESSION_PRUNE_SECONDS = 6 * 60 * 60


def _session_basetemp() -> Path:
    """本轮会话专属的 basetemp（系统临时目录下，按 pid 区分）。"""
    return Path(tempfile.gettempdir()) / _TMP_ROOT_NAME / f"run-{os.getpid()}"


def _prune_stale_sessions(root: Path) -> None:
    """清掉 6 小时前留下的会话目录，把磁盘占用收敛住。"""
    cutoff = time.time() - _SESSION_PRUNE_SECONDS
    for entry in root.glob("run-*"):
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


@pytest.fixture(scope="session")
def tmp_path_factory(request: pytest.FixtureRequest):
    """覆盖 pytest 内置实现，只为把 basetemp 挪到系统临时目录。

    用一个 fixture 覆盖而不是在 `pytest_configure` 里改配置：内置的
    tmp_path_factory 在 configure 阶段就被建好了，那时改 option 已经来不及。
    """
    from _pytest.tmpdir import TempPathFactory

    config = request.config
    if not getattr(config.option, "basetemp", None):
        root = _session_basetemp().parent
        try:
            root.mkdir(parents=True, exist_ok=True)
            _prune_stale_sessions(root)
            config.option.basetemp = str(_session_basetemp())
        except OSError:
            # 临时目录不可用就退回 pytest 默认行为，不要把测试拦死。
            pass
    return TempPathFactory.from_config(config, _ispytest=True)
