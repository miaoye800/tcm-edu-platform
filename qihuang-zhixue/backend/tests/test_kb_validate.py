from pathlib import Path

from app.kb.models import Flavor, Herb
from app.kb.validate import validate_herb_files


def test_herb_model_rejects_invalid_flavor() -> None:
    data = {
        "id": "herb_test",
        "name": "测试药",
        "pinyin": "ce shi yao",
        "category": "解表药",
        "subcategory": "发散风寒药",
        "nature": "温",
        "flavor": ["香"],
        "meridians": ["肺"],
        "effects": ["发散风寒"],
        "indications": [{"syndrome": "风寒表证", "description": "测试"}],
        "dosage": {"text": "3~6g", "min_g": 3, "max_g": 6},
        "sources": [{"type": "教材", "ref": "测试来源"}],
    }

    try:
        Herb.model_validate(data)
    except ValueError as exc:
        assert "flavor" in str(exc)
    else:
        raise AssertionError("非法五味应校验失败")


def test_herb_seed_file_passes_validation() -> None:
    report = validate_herb_files(Path("data/herbs"))

    assert report.total == 20
    assert report.passed == 20
    assert report.issues == []


def test_herb_model_accepts_enum_values() -> None:
    herb = Herb.model_validate(
        {
            "id": "herb_mahuang_min",
            "name": "麻黄",
            "pinyin": "ma huang",
            "category": "解表药",
            "subcategory": "发散风寒药",
            "nature": "温",
            "flavor": ["辛", "苦"],
            "meridians": ["肺", "膀胱"],
            "effects": ["发汗解表"],
            "indications": [{"syndrome": "风寒表实证", "description": "恶寒发热无汗"}],
            "dosage": {"text": "2~10g", "min_g": 2, "max_g": 10},
            "sources": [{"type": "教材", "ref": "《中药学》规划教材·发散风寒药"}],
        }
    )

    assert herb.flavor == [Flavor.XIN, Flavor.KU]

