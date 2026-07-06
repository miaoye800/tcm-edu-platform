import importlib
import re
from pathlib import Path

import pytest


def test_legacy_profile_modules_are_removed() -> None:
    for module_name in ("app.profile.models", "app.profile.rules", "app.profile.storage"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)

    profile_dir = Path(__file__).resolve().parents[1] / "app" / "profile"
    assert not (profile_dir / "models.py").exists()
    assert not (profile_dir / "rules.py").exists()
    assert not (profile_dir / "storage.py").exists()


def test_profile_code_has_no_legacy_goal_dimension_key() -> None:
    profile_dir = Path(__file__).resolve().parents[1] / "app" / "profile"
    offenders: list[str] = []
    pattern = re.compile(r"\bgoal\b")

    for path in profile_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(profile_dir)}:{line_number}:{line.strip()}")

    assert offenders == []
