from pathlib import Path

from app.kb.ingest import load_herbs
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
    data_dir = Path("data/herbs")
    report = validate_herb_files(data_dir)
    expected_total = len(load_herbs(data_dir))

    assert report.total == expected_total
    assert report.passed == expected_total
    assert report.issues == []


def test_chenpi_seed_entry_is_present_and_typed_as_qi_regulating() -> None:
    """陈皮 was whitelisted for the photo quiz but had no seed file, so the
    apprentice story silently asked four questions instead of five."""

    herbs = load_herbs(Path("data/herbs"))
    chenpi = next((herb for herb in herbs if herb.id == "herb_chenpi"), None)

    assert chenpi is not None, "data/herbs 里缺少 herb_chenpi"
    assert chenpi.category == "理气药"
    assert chenpi.effects[0] == "理气健脾", "effects[0] 是出题时的正确答案"


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
