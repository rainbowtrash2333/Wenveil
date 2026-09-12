from __future__ import annotations

import re
from pathlib import Path

import pytest

from desensitize import Desensitizer, MappingVault, load_config, restore_text


ROOT = Path(__file__).resolve().parents[1]


def test_xxx_md_default_policy_and_full_restore():
    result = Desensitizer(load_config()).anonymize_file(ROOT / "xxx.md")

    assert result.normalized_text == Desensitizer(load_config()).normalize(
        (ROOT / "xxx.md").read_text(encoding="utf-8")
    )
    assert result.normalized_text.count("中国人民财产保险股份有限公司") == 3
    assert re.search(r"⟦机构\d+⟧", result.masked_text)
    assert "⟦人员" in result.masked_text
    assert "⟦证件" in result.masked_text
    assert "1,234,567.89元" in result.masked_text
    assert "100万元" in result.masked_text
    assert "交易编号:2025" in result.masked_text
    assert "⟦数字" not in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text

    org_tokens = [token for token, value in result.vault.token_to_surface.items() if value == "中国人民财产保险股份有限公司"]
    assert len(org_tokens) == 1
    assert len({token for token, value in result.vault.token_to_surface.items() if value == "张三"}) == 1
    person_tokens = {token for token, value in result.vault.token_to_surface.items() if value in {"张三", "李四"}}
    assert len(person_tokens) == 2
    assert len(result.vault.token_to_surface) >= 4


def test_organization_alias_and_subsidiary_tokens_keep_public_relation_id():
    source = (
        "中国人保资产管理有限公司（以下简称‘人保资产’）。"
        "人保资产北京公司与人保资产签署协议。"
    )

    result = Desensitizer(load_config()).anonymize(source, source_name="finance-note.md")

    assert "人保资产" not in result.masked_text
    assert "人保资产北京公司" not in result.masked_text
    assert re.search(r"⟦机构\d+⟧", result.masked_text)
    assert re.search(r"⟦机构\d+-别名\d+⟧", result.masked_text)
    assert re.search(r"⟦机构\d+-子公司\d+⟧", result.masked_text)
    assert all(len(token) <= 12 for token in result.vault.token_to_surface)

    relation_values = list(result.vault.alias_relations.values())
    assert {item["relation"] for item in relation_values} >= {"ALIAS_OF", "SUBSIDIARY_OF"}
    assert len({item["public_entity_id"] for item in relation_values}) == 1
    assert result.report["organization_relations"]["accepted"] >= 3
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_public_organization_whitelist_is_preserved():
    source = (
        "中国保险监督管理委员会与中国金融监管局发布通知，"
        "中国人保资产管理有限公司提交报告。"
    )
    result = Desensitizer(load_config()).anonymize(source)

    assert "中国保险监督管理委员会" in result.masked_text
    assert "中国金融监管局" in result.masked_text
    assert "中国人保资产管理有限公司" not in result.masked_text
    assert result.report["whitelist"] == {"detected": 2, "accepted": 2, "protected": 2}
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_whitelist_substring_does_not_exempt_larger_private_organization():
    source = "中国人民银行发布通知，中国人民银行征信中心有限公司提供服务。"
    result = Desensitizer(load_config()).anonymize(source)

    assert result.masked_text.count("中国人民银行") == 1
    assert "中国人民银行征信中心有限公司" not in result.masked_text
    assert "⟦机构" in result.masked_text
    assert result.report["whitelist"]["protected"] == 1
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_ambiguous_alias_is_masked_without_false_subject_link():
    source = (
        "中国人民财产保险股份有限公司（以下简称人保财险）。"
        "人保资本保险资产管理有限公司（以下简称人保财险）。"
        "两者均出现在材料中。"
        "后文再次提到人保财险。"
    )

    result = Desensitizer(load_config()).anonymize(source)

    assert "人保财险" not in result.masked_text
    assert any(value == "人保财险" for value in result.vault.token_to_surface.values())
    assert not any(
        value == "人保财险" and relation.get("canonical_id")
        for token, value in result.vault.token_to_surface.items()
        for relation in [result.vault.alias_relations.get(token, {})]
    )
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_person_lists_and_department_rosters_are_masked():
    source = (
        "共有4名董事、赵启明、钱思远、孙海峰、李哲参加会议。"
        "投资部周安、吴晨；某机构投资部郑源、王珂。"
    )
    result = Desensitizer(load_config()).anonymize(source)

    for name in ("赵启明", "钱思远", "孙海峰", "李哲", "周安", "吴晨", "郑源", "王珂"):
        assert name not in result.masked_text
    assert result.report["PERSON"]["masked"] >= 8
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_repeated_person_surface_is_masked_in_unlabelled_mentions():
    source = "联系人：赵启明、钱思远；会议记录再次提到钱思远。"
    result = Desensitizer(load_config()).anonymize(source)

    assert result.masked_text.count("钱思远") == 0
    assert result.masked_text.count("⟦人员") >= 3
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_custom_rules_and_enabled_amounts(tmp_path: Path):
    config_path = tmp_path / "custom.yaml"
    config_path.write_text(
        """
version: 1
normalization:
  unicode_nfkc: true
  remove_han_spaces: true
  merge_broken_lines: true
  repair_id_card: true
  repair_amount: true
entities:
  PERSON: {detect: true, anonymize: true, priority: 70}
  ID_CARD: {detect: true, anonymize: true, priority: 100}
  ORG: {detect: true, anonymize: true, priority: 85}
  AMOUNT: {detect: true, anonymize: true, protect_when_disabled: true, priority: 80}
  NUMBER: {detect: true, anonymize: true, priority: 10}
dictionaries:
  persons: []
  organizations: []
custom:
  - name: project
    type: PROJECT
    matcher: literal
    values: [华电资本增资扩股引战项目]
    priority: 95
  - name: project_field
    type: PROJECT_FIELD
    matcher: field
    labels: [项目名称]
    priority: 96
  - name: contract
    type: CONTRACT_ID
    matcher: regex
    patterns: ['HT-[0-9]{4}-[0-9]+']
    priority: 95
""",
        encoding="utf-8",
    )
    result = Desensitizer(load_config(config_path)).anonymize_file(ROOT / "xxx.md")
    assert "100万元" not in result.masked_text
    assert "⟦金额" in result.masked_text
    assert "⟦数字" in result.masked_text
    assert "⟦合同" in result.masked_text
    assert "⟦项目" in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_literal_token_like_text_does_not_break_restore():
    source = "原文中有一个普通标记 ⟦PERSON:000001⟧。\n姓名：张三"
    result = Desensitizer(load_config()).anonymize(source)
    assert "⟦PERSON:000001⟧" in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_literal_compact_token_like_text_does_not_break_restore():
    source = "原文中有一个普通标记 ⟦人员1⟧。姓名：张三"
    result = Desensitizer(load_config()).anonymize(source)

    assert "⟦人员1⟧" in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_missing_mapping_entry_is_an_error():
    result = Desensitizer(load_config()).anonymize_file(ROOT / "xxx.md")
    token = next(iter(result.vault.token_to_surface))
    reduced = MappingVault(
        schema_version=result.vault.schema_version,
        job_id=result.vault.job_id,
        source_hash=result.vault.source_hash,
        normalized_hash=result.vault.normalized_hash,
        masked_hash=result.vault.masked_hash,
        config_hash=result.vault.config_hash,
        token_to_surface={key: value for key, value in result.vault.token_to_surface.items() if key != token},
        entity_counts=result.vault.entity_counts,
    )
    with pytest.raises(ValueError):
        restore_text(result.masked_text, reduced)


def test_mapping_encryption_rejects_wrong_password(tmp_path: Path):
    result = Desensitizer(load_config()).anonymize_file(ROOT / "xxx.md")
    path = tmp_path / "sample.mapping.enc"
    result.vault.save(path, "correct horse")
    loaded = MappingVault.load(path, "correct horse")
    assert restore_text(result.masked_text, loaded) == result.normalized_text
    with pytest.raises(ValueError):
        MappingVault.load(path, "wrong password")


def test_custom_dictionary_file_is_loaded_relative_to_config(tmp_path: Path):
    (tmp_path / "departments.txt").write_text("内部机构甲\n内部机构乙\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        """
version: 1
entities: {}
dictionaries: {}
custom:
  - name: departments
    type: DEPARTMENT
    matcher: dictionary
    file: departments.txt
""",
        encoding="utf-8",
    )
    engine = Desensitizer(load_config(tmp_path / "config.yaml"))
    result = engine.anonymize("内部机构甲和内部机构乙")
    assert "⟦部门" in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_large_repeated_input_completes_without_quadratic_replacement():
    source = (ROOT / "xxx.md").read_text(encoding="utf-8") * 250
    result = Desensitizer(load_config()).anonymize(source)
    assert result.masked_text != source
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_address_table_cells_and_unified_credit_code_are_covered():
    source = "|住所|北京市西城区西长安街88号|9111000007169867X5 号院一区1号楼2层|北京市海淀区北清路81号|"
    result = Desensitizer(load_config()).anonymize(source)

    assert "北京市西城区西长安街88号" not in result.masked_text
    assert "9111000007169867X5" not in result.masked_text
    assert "号院一区1号楼2层" not in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_bank_branch_suffix_is_masked_after_bank_root():
    result = Desensitizer(load_config()).anonymize("招商银行深圳分行；中国银行北京中银大厦支行")

    assert "招商银行" not in result.masked_text
    assert "深圳分行" not in result.masked_text
    assert "中国银行" not in result.masked_text
    assert "北京中银大厦支行" not in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text


def test_ocr_separators_in_email_phone_and_account_are_masked():
    source = "\n".join(
        [
            "邮箱：alice @ example.com",
            "座机：010-1234 5678",
            "账号：6222 0212 3456 7890 123",
        ]
    )

    result = Desensitizer(load_config()).anonymize(source)

    assert "alice @ example.com" not in result.masked_text
    assert "010-1234 5678" not in result.masked_text
    assert "6222 0212 3456 7890 123" not in result.masked_text
    assert "⟦邮箱" in result.masked_text
    assert "⟦电话" in result.masked_text
    assert "⟦账号" in result.masked_text
    assert restore_text(result.masked_text, result.vault) == result.normalized_text
