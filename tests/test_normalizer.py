from desensitize import load_config
from desensitize.normalizer import TextNormalizer


def test_ocr_repairs_are_readable_and_idempotent():
    normalizer = TextNormalizer(load_config().normalization)
    source = (
        "本公司于２０２５年\n"
        "１月向中国 人 民 财 产 保 险 股 份\n"
        "有限公司支付 1, 234, 567. 89 元。\n"
        "身份证：110105 1949 1231 002X\n"
    )
    normalized = normalizer.normalize(source)
    assert "中国人民财产保险股份有限公司" in normalized
    assert "1,234,567.89元" in normalized
    assert "11010519491231002X" in normalized
    assert "中国 人 民" not in normalized
    assert normalizer.normalize(normalized) == normalized


def test_markdown_table_boundaries_survive_normalization():
    normalizer = TextNormalizer(load_config().normalization)
    source = "| 公司名称  产品名称 | 金额 |\n|---|---|\n| 中国 人 民 公司 | 100 万元 |"
    normalized = normalizer.normalize(source)
    assert normalized.count("|") == source.count("|")
    assert "中国人民公司" in normalized
    assert "100万元" in normalized


def test_structured_entities_can_cross_a_field_line_break():
    normalizer = TextNormalizer(load_config().normalization)
    normalized = normalizer.normalize("身份证：110105\n1949 1231 002X\n")
    assert "身份证:11010519491231002X" in normalized
