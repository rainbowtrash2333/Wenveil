from __future__ import annotations

import json
from dataclasses import asdict

from desensitize.audit import AuditIssue, audit_file, audit_masked_text


def test_audit_reports_residual_shapes_without_echoing_sensitive_values():
    phone = "13800138000"
    landline = "010-12345678"
    email = "alice@example.com"
    id_card = "11010519491231002X"
    account = "6222021234567890123"
    contract = "HT-2025-001"
    address = "北京市朝阳区建国路88号"
    text = "\n".join(
        [
            f"手机：{phone}",
            f"座机：{landline}",
            f"邮箱：{email}",
            f"身份证号码：{id_card}",
            f"银行账号：{account}",
            f"合同编号：{contract}",
            f"通讯地址：{address}",
        ]
    )

    issues = audit_masked_text(text)
    categories = {issue.category for issue in issues}

    assert {"PHONE", "LANDLINE", "EMAIL", "ID_CARD", "BANK_ACCOUNT", "CONTRACT_ID", "ADDRESS"} <= categories
    assert [issue.line for issue in issues if issue.category == "PHONE"] == [1]
    assert [issue.line for issue in issues if issue.category == "ADDRESS"] == [7]

    serialized = json.dumps([asdict(issue) for issue in issues], ensure_ascii=False)
    rendered = repr(issues)
    for secret in (phone, landline, email, id_card, account, contract, address):
        assert secret not in serialized
        assert secret not in rendered
    assert all(isinstance(issue, AuditIssue) for issue in issues)


def test_masked_tokens_and_valid_markdown_table_are_clean():
    job_id = "abcdef012345"
    text = "\n".join(
        [
            "| 字段 | 值 |",
            "| --- | --- |",
            f"| 手机 | ⟦PHONE:000001:{job_id}⟧ |",
            f"| 地址 | ⟦ADDRESS:000001:{job_id}⟧ |",
            f"| 合同 | ⟦CONTRACT_ID:000001:{job_id}⟧ |",
        ]
    )

    assert audit_masked_text(text) == []


def test_audit_finds_bad_token_and_table_column_count_with_safe_summaries():
    text = "\n".join(
        [
            "⟦PHONE:1⟧",
            "| 字段 | 值 |",
            "| --- | --- |",
            "| 手机 | 已脱敏 | 多余单元格 |",
        ]
    )

    issues = audit_masked_text(text)
    by_category = {issue.category: issue for issue in issues}

    assert by_category["TOKEN"].line == 1
    assert by_category["TABLE_PIPE"].line == 4
    assert "⟦PHONE:1⟧" not in by_category["TOKEN"].summary
    assert "多余单元格" not in by_category["TABLE_PIPE"].summary


def test_address_table_cell_and_multiline_value_are_audited():
    text = "\n".join(
        [
            "| 注册地址 | 上海市浦东新区世纪大道1号 |",
            "通讯地址：",
            "广东省深圳市福田区中心路1号",
        ]
    )

    issues = audit_masked_text(text)
    assert [(issue.line, issue.category) for issue in issues] == [(1, "ADDRESS"), (3, "ADDRESS")]
    assert "上海市浦东新区世纪大道1号" not in issues[0].summary
    assert "广东省深圳市福田区中心路1号" not in issues[1].summary


def test_address_table_cell_with_separator_cell_accepts_masked_value():
    text = "| 地址 | ： | ⟦ADDRESS:000001:abcdef012345⟧ |"

    assert audit_masked_text(text) == []


def test_hyphenated_prose_before_table_separator_is_not_a_table_error():
    text = "\n".join(
        [
            "| 说明 | 甲 - 乙 |",
            "| 另一行 | 仍然是普通文本 |",
            "| 字段 | 值 |",
            "| --- | --- |",
            "| 手机 | 已脱敏 |",
        ]
    )

    assert audit_masked_text(text) == []


def test_audit_file_uses_one_based_line_numbers(tmp_path):
    path = tmp_path / "masked.md"
    path.write_text("安全内容\n\n邮箱：bob@example.com\n", encoding="utf-8")

    issues = audit_file(path)

    assert len(issues) == 1
    assert issues[0].line == 3
    assert issues[0].line_number == 3
    assert issues[0].line_no == 3
