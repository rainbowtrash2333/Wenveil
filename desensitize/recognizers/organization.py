from __future__ import annotations

import re
from dataclasses import replace

from ..models import Span
from ..organization_registry import OrganizationRegistry
from .dictionary import DictionaryRecognizer


class OrganizationRecognizer:
    _suffix = re.compile(
        r"(?:股份有限公司|有限责任公司|有限公司|集团公司|集团|保险公司|证券公司|银行|基金管理公司|"
        r"合伙企业(?:（有限合伙）|\(有限合伙\))?|研究院|医院|大学)"
    )
    _allowed = re.compile(r"[\u3400-\u9fffA-Za-z0-9·&（）()\-]")
    _leading_connectors = re.compile(r"^(?:受托人|受益人|委托人|发起人|根据|按照|由于|与|及|和|或|向|由|在|将|对|为|本|该|其|等|经|被|同|于|受|故|自|原|认定|通过|依据|以)")
    _alias_declaration = re.compile(
        r"[\u3400-\u9fffA-Za-z0-9·（）()\-]{3,80}(?:股份有限公司|有限责任公司|有限公司|集团公司|集团|保险公司|证券公司|银行|"
        r"基金管理公司|合伙企业(?:（有限合伙）|\(有限合伙\))?)"
        r"\s*[（(]\s*(?:以下简称|简称|下称|以下称|简称为)\s*[“\"「『]?"
        r"(?P<alias>[\u3400-\u9fffA-Za-z0-9·]{2,20})"
    )
    _generic_bank_prefix = re.compile(r"^(?:开户|受益人|持有人|付款人|收款人|托管人|托管|关联方|代理人|对方|相关)(?:银行)?$")
    _generic_aliases = frozenset({"公司", "本公司", "我公司", "银行", "基金", "协会", "计划", "本计划", "本股权计划", "受托人"})
    _default_bank_roots = (
        "中国银行",
        "招商银行",
        "中信银行",
        "工商银行",
        "建设银行",
        "农业银行",
        "交通银行",
        "浦发银行",
        "兴业银行",
        "民生银行",
        "光大银行",
        "华夏银行",
        "平安银行",
        "北京银行",
        "上海银行",
    )
    _non_org_words = (
        "审议",
        "批准",
        "承受能力",
        "托管账户",
        "缴纳",
        "符合",
        "推进",
        "获得",
        "认购",
        "处分",
        "收到",
        "接收",
        "合计",
        "所有",
        "届时",
        "分红",
        "认定",
        "通过",
        "依据",
        "聘用",
        "完成",
        "提交",
        "尚待",
        "抽水蓄能",
        "抽蓄",
        "的约定",
        "背靠",
        "召开",
        "汇报",
        "部分",
        "查阅",
        "代表",
        "保障",
        "保证",
        "缩短",
        "原",
        "根据",
        "聘请",
        "包括",
        "关联方",
    )

    def __init__(
        self,
        values: tuple[str, ...] = (),
        *,
        priority: int = 85,
        dictionary_priority: int | None = None,
        anonymize: bool = True,
        protect: bool = False,
        registry: dict | None = None,
    ):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect
        self.registry_data = dict(registry or {})
        self.values = tuple(values)
        self.dictionary = DictionaryRecognizer(
            "ORG",
            values,
            priority=dictionary_priority if dictionary_priority is not None else priority + 5,
            source="org_dictionary",
            rule_id="org_dictionary",
            anonymize=anonymize,
            protect=protect,
        )
        roots = set(self._default_bank_roots)
        for value in values:
            if "银行" in value:
                roots.add(value[: value.find("银行") + 2])
        bank_roots = "|".join(sorted((re.escape(root) for root in roots), key=len, reverse=True))
        self._bank_branch = re.compile(
            rf"(?:{bank_roots})(?P<branch>[\u3400-\u9fffA-Za-z0-9·()（）]{{2,24}}(?:分行|支行))"
        )

    def recognize(self, text: str) -> list[Span]:
        registry = OrganizationRegistry.from_config(self.registry_data, self.values)
        registry.learn_from_text(text)

        result = []
        for span in self.dictionary.recognize(text):
            canonical_id = registry.canonical_id_for(span.surface)
            if canonical_id:
                result.append(
                    replace(
                        span,
                        canonical_id=canonical_id,
                        canonical_name=span.surface,
                        relation="ORG_OF",
                    )
                )
            else:
                result.append(span)

        for start, end, canonical_id, canonical in registry.canonical_bindings(text):
            result.append(
                Span(
                    start,
                    end,
                    "ORG",
                    text[start:end],
                    score=0.995,
                    priority=self.priority + 4,
                    source="org_registry_canonical",
                    rule_id="org_registry_canonical",
                    anonymize=self.anonymize,
                    protect=self.protect,
                    canonical_id=canonical_id,
                    canonical_name=canonical,
                    relation="ORG_OF",
                )
            )

        for subsidiary in registry.subsidiary_bindings(text):
            result.append(
                Span(
                    subsidiary.start,
                    subsidiary.end,
                    "ORG_SUBSIDIARY",
                    subsidiary.surface,
                    score=subsidiary.confidence,
                    priority=self.priority + 2,
                    source=subsidiary.source,
                    rule_id="organization_subsidiary_pattern",
                    anonymize=self.anonymize,
                    protect=self.protect,
                    canonical_id=subsidiary.canonical_id,
                    canonical_name=subsidiary.canonical,
                    relation="SUBSIDIARY_OF",
                    alias_index=subsidiary.alias_index,
                    location=subsidiary.location,
                )
            )

        for binding in registry.alias_bindings():
            for match in re.finditer(re.escape(binding.alias), text):
                result.append(
                    Span(
                        match.start(),
                        match.end(),
                        "ORG_ALIAS",
                        match.group(0),
                        score=0.93,
                        priority=max(1, self.priority - 1),
                        source="org_alias",
                        rule_id=f"org_alias:{binding.source}",
                        anonymize=self.anonymize,
                        protect=self.protect,
                        canonical_id=binding.canonical_id,
                        canonical_name=binding.canonical,
                        relation="ALIAS_OF",
                        alias_index=binding.alias_index,
                    )
                )
        for alias, _bindings in registry.ambiguous_alias_bindings():
            for match in re.finditer(re.escape(alias), text):
                result.append(
                    Span(
                        match.start(),
                        match.end(),
                        "ORG_ALIAS",
                        match.group(0),
                        score=0.88,
                        priority=max(1, self.priority - 1),
                        source="org_alias_ambiguous",
                        rule_id="org_alias_ambiguous",
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )
        for match in self._bank_branch.finditer(text):
            start, end = match.span("branch")
            branch = text[start:end]
            canonical_id = registry.canonical_id_for(branch)
            result.append(
                Span(
                    start,
                    end,
                    "ORG",
                    branch,
                    score=0.96,
                    priority=self.priority + 1,
                    source="org_bank_branch",
                    rule_id="org_bank_branch",
                    anonymize=self.anonymize,
                    protect=self.protect,
                    canonical_id=canonical_id or "",
                    canonical_name=branch if canonical_id else "",
                    relation="ORG_OF" if canonical_id else "",
                )
            )
        for suffix in self._suffix.finditer(text):
            end = suffix.end()
            start = suffix.start()
            while start > 0 and end - start < 80 and self._allowed.fullmatch(text[start - 1]):
                start -= 1
            candidate = text[start:end]
            candidate_start = start
            candidate, candidate_start = self._trim_leading(candidate, candidate_start)
            if len(candidate) < 3:
                continue
            if candidate.startswith(("(", "（")):
                candidate = candidate[1:]
                candidate_start += 1
            suffix_text = suffix.group(0)
            prefix_text = candidate[: -len(suffix_text)] if candidate.endswith(suffix_text) else candidate
            if suffix_text in {"集团", "集团公司", "合伙企业", "有限合伙企业", "保险公司", "银行", "有限公司", "股份有限公司"} and len(prefix_text) < 2:
                continue
            if any(word in candidate for word in self._non_org_words):
                continue
            if candidate in {"合伙企业", "有限合伙企业", "有限责任公司", "有限公司", "股份有限公司", "保险公司", "集团公司", "商业银行", "投资银行", "政策性银行"}:
                continue
            if re.match(r"^[0-9一二三四五六七八九十]+[)）]", candidate) or candidate[:1].isdigit():
                continue
            if candidate.endswith("银行") and re.search(r"(?:市|区|县|路|街|道|号)", candidate):
                continue
            if candidate in {"开户银行", "受益人银行", "持有人银行", "付款人银行", "收款人银行", "托管银行"}:
                continue
            if candidate.endswith("银行") and self._generic_bank_prefix.fullmatch(candidate):
                continue
            canonical_id = registry.canonical_id_for(candidate)
            result.append(
                Span(
                    candidate_start,
                    end,
                    "ORG",
                    candidate,
                    score=0.9,
                    priority=self.priority,
                    source="org_suffix_rule",
                    rule_id="org_suffix",
                    anonymize=self.anonymize,
                    protect=self.protect,
                    canonical_id=canonical_id or "",
                    canonical_name=candidate if canonical_id else "",
                    relation="ORG_OF" if canonical_id else "",
                )
            )
        return result

    @classmethod
    def _learn_aliases(cls, text: str) -> tuple[str, ...]:
        values: list[str] = []
        for match in cls._alias_declaration.finditer(text):
            alias = match.group("alias")
            if alias and alias not in values and len(alias) >= 2 and alias not in cls._generic_aliases:
                values.append(alias)
        return tuple(values)

    @classmethod
    def _trim_leading(cls, candidate: str, start: int) -> tuple[str, int]:
        original = candidate
        while True:
            match = cls._leading_connectors.match(candidate)
            if not match:
                break
            start += match.end()
            candidate = candidate[match.end() :]
        # If OCR omitted punctuation before an organization, use the last
        # obvious connective as a conservative boundary.
        positions = [
            match.end()
            for match in re.finditer(r"(?:受托人|受益人|委托人|发起人|根据|按照|由于|与|及|和|向|由|在|将|对|为|经|被|于|等|受|故|自|原|认定|通过|依据|以|或)", candidate[:-1])
        ]
        if positions:
            offset = max(positions)
            start += offset
            candidate = candidate[offset:]
        return candidate if candidate else original, start if candidate else start - len(original) + len(candidate)
