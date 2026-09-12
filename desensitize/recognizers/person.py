from __future__ import annotations

import re

from ..models import Span
from .dictionary import DictionaryRecognizer


DEFAULT_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄麹家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公万俟司马上官欧阳夏侯诸葛闻人东方赫连皇甫尉迟公羊澹台公冶宗政濮阳淳于单于太叔申屠公孙仲孙轩辕令狐钟离宇文长孙慕容司徒司空"
)


class PersonRecognizer:
    _context_pattern = re.compile(
        r"(?:经办/代理人姓名|法定代表人/负责人或授权代表|法定代表人或授权代表|法定代表人/负责人|"
        r"法定代表人|姓名|负责人|联系人|经办人|代理人|董事长|总经理|被保险人|投保人|受益人)"
        r"\s*[:：为]\s*(?!(?:[\u3400-\u9fff]{2,8})\s*[:：])[【\[]?(?P<name>[\u3400-\u9fff]{2,4})[】\]]?"
    )
    _table_pattern = re.compile(
        r"(?:法定代表人|负责人|联系人|经办/代理人姓名|经办人姓名)\s*(?:\|\s*){1,2}"
        r"(?:类型|姓名|名称)?\s*[【\[]?"
        r"(?P<name>[\u3400-\u9fff]{2,6})[】\]]?(?=\s*[|]|\s*$)"
    )
    _role_value_pattern = re.compile(
        r"\|\s*(?P<name>[\u3400-\u9fff]{2,4})\s*\|\s*"
        r"(?:财务负责人|风险管理部负责人|公司分管风险管理负责人|法律合规部部门负责人|"
        r"股权与实物资产投资部分部负责人|股权与不动产投资部分部负责人|部门负责人|"
        r"法定代表人|负责人|董事长|总经理|投资总监|分部负责人|经理|监事|合伙人)"
        r"(?=\s*[|：:]|\s*$)"
    )
    _list_pattern = re.compile(
        r"(?:会务联系人|联系人|经办人|代理人)\s*[:：]\s*"
        r"(?P<values>[\u3400-\u9fff]{2,4}(?:\s*[、,，]\s*[\u3400-\u9fff]{2,4})+)"
    )
    _meeting_list_pattern = re.compile(
        r"(?:共有|共|包括|参会|出席|与会)?[ \t]*(?:\d+[ \t]*)?(?:名)?"
        r"(?:董事会成员|参会董事|出席人员|参会人员|与会人员|董事)"
        r"[ \t]*[：:、，,|]?[ \t]*"
        r"(?P<values>[^\n。；;]{2,160})"
    )
    _department_list_pattern = re.compile(
        r"(?:投资管理部|投后管理部|股权投资部|不动产投资部|实物资产投资部|"
        r"风险管理部|法律合规部|财务部|审计部|运营部|研究部|市场部|"
        r"人力资源部|战略发展部|董事会办公室|监事会办公室|办公室|投资部)"
        r"[ \t]*[：:、，,|]?[ \t]*"
        r"(?P<values>[^\n。；;]{2,160})"
    )
    _role_list_pattern = re.compile(
        r"(?:总经理|监事|投资总监|分部负责人|部门负责人|签字人|签署人)"
        r"[ \t]*[：:、，,|][ \t]*"
        r"(?P<values>[^\n。；;]{2,160})"
    )
    _signature_pattern = re.compile(
        r"(?:法定代表人/负责人或授权代表|法定代表人或授权代表|法定代表人/负责人|签字人|签署人)"
        r"\s*(?:[:：])?\s*(?!(?:[\u3400-\u9fff]{2,8})\s*[:：])[【\[]?(?P<name>[\u3400-\u9fff]{2,4})[】\]]?"
        r"(?=\s*[:：|，,；;]|\s*$)"
    )
    _role_prose_pattern = re.compile(
        r"(?:负责人|董事长|总经理|投资总监|分部负责人|部门负责人|法定代表人|授权代表|联系人|经办人|代理人)"
        r"(?P<name>[\u3400-\u9fff]{2,4})"
        r"(?=\s*(?:的|、|，|,|担任|签字|签章|出席|参加|负责|汇报|列席|$))"
    )
    _title_pattern = re.compile(
        r"(?:(?:公司领导|本集团|集团|公司)(?P<name>[\u4e00-\u9fff][\u4e00-\u9fff]{1,2})|"
        r"(?<![\u3400-\u9fff])(?P<standalone>[\u4e00-\u9fff][\u4e00-\u9fff]{1,2}))"
        r"(?=总裁|副总裁|董事长|副董事长|总经理|副总经理|投资总监|部长|主任|经理|监事|行长|局长|书记|先生|女士)"
    )

    def __init__(
        self,
        values: tuple[str, ...] = (),
        *,
        priority: int = 70,
        anonymize: bool = True,
        protect: bool = False,
        surnames: frozenset[str] | None = None,
    ):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect
        self.surnames = surnames or DEFAULT_SURNAMES
        self.dictionary = DictionaryRecognizer(
            "PERSON",
            values,
            priority=priority + 2,
            source="person_dictionary",
            rule_id="person_dictionary",
            anonymize=anonymize,
            protect=protect,
        )

    def recognize(self, text: str) -> list[Span]:
        result = self.dictionary.recognize(text)
        seen: set[tuple[int, int]] = set()
        for pattern, source, rule_id, score in (
            (self._context_pattern, "person_context", "person_context_label", 0.96),
            (self._table_pattern, "person_table", "person_table_label", 0.98),
            (self._role_value_pattern, "person_role_value", "person_role_value", 0.97),
            (self._signature_pattern, "person_signature", "person_signature_label", 0.97),
            (self._role_prose_pattern, "person_role_prose", "person_role_prose", 0.95),
        ):
            for match in pattern.finditer(text):
                name = match.group("name")
                start, end = match.span("name")
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                if not name or name[0] not in self.surnames:
                    continue
                result.append(
                    Span(
                        start,
                        end,
                        "PERSON",
                        name,
                        score=score,
                        priority=self.priority,
                        source=source,
                        rule_id=rule_id,
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )
        for match in self._title_pattern.finditer(text):
            group = "name" if match.group("name") is not None else "standalone"
            name = match.group(group)
            if name[0] not in self.surnames:
                continue
            start, end = match.span(group)
            if (start, end) in seen:
                continue
            seen.add((start, end))
            result.append(
                Span(
                    start,
                    end,
                    "PERSON",
                    name,
                    score=0.91,
                    priority=self.priority,
                    source="person_title",
                    rule_id="person_job_title",
                    anonymize=self.anonymize,
                    protect=self.protect,
                )
            )
        for match in self._list_pattern.finditer(text):
            values_start, _ = match.span("values")
            for value_match in re.finditer(r"[\u3400-\u9fff]{2,4}", match.group("values")):
                name = value_match.group(0)
                if name[0] not in self.surnames:
                    continue
                start = values_start + value_match.start()
                end = values_start + value_match.end()
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                result.append(
                    Span(
                        start,
                        end,
                        "PERSON",
                        name,
                        score=0.95,
                        priority=self.priority,
                        source="person_list",
                        rule_id="person_list_label",
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )
        for pattern, source, rule_id, score in (
            (self._meeting_list_pattern, "person_meeting_list", "person_meeting_list", 0.96),
            (self._department_list_pattern, "person_department_list", "person_department_list", 0.94),
            (self._role_list_pattern, "person_role_list", "person_role_list", 0.94),
        ):
            for match in pattern.finditer(text):
                for start, end, name in self._named_list_spans(
                    match.group("values"),
                    match.start("values"),
                ):
                    if (start, end) in seen:
                        continue
                    seen.add((start, end))
                    result.append(
                        Span(
                            start,
                            end,
                            "PERSON",
                            name,
                            score=score,
                            priority=self.priority,
                            source=source,
                            rule_id=rule_id,
                            anonymize=self.anonymize,
                            protect=self.protect,
                    )
                )
        self._recognize_structured_contexts(text, result, seen)
        self._propagate_repeated_person_surfaces(text, result, seen)
        return result

    def _propagate_repeated_person_surfaces(
        self,
        text: str,
        result: list[Span],
        seen: set[tuple[int, int]],
    ) -> None:
        """Reuse a confidently detected person surface throughout one document.

        OCR frequently drops the delimiter or role label on a later mention of
        a name.  If the same short CJK surface was already detected as PERSON
        and occurs more than once in this document, treating all of its exact
        occurrences as PERSON closes that recall gap without turning every
        isolated surname-looking word into a person.
        """

        propagated_sources = {
            "person_list",
            "person_table",
            "person_meeting_list",
            "person_department_list",
            "person_role_list",
        }
        surfaces = sorted(
            {
                span.surface
                for span in result
                if span.entity_type == "PERSON"
                and span.source in propagated_sources
                and 2 <= len(span.surface) <= 4
                and all("\u3400" <= char <= "\u9fff" for char in span.surface)
                and text.count(span.surface) >= 2
            },
            key=lambda value: (-len(value), value),
        )
        for name in surfaces:
            for match in re.finditer(re.escape(name), text):
                start, end = match.span()
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                result.append(
                    Span(
                        start,
                        end,
                        "PERSON",
                        name,
                        score=0.88,
                        priority=self.priority,
                        source="person_document_propagation",
                        rule_id="person_repeated_surface",
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )

    def _named_list_spans(self, values: str, offset: int) -> list[tuple[int, int, str]]:
        """Extract conservative name items from a role/department list."""

        result: list[tuple[int, int, str]] = []
        for item_match in re.finditer(r"[^、,，|]+", values):
            raw_item = item_match.group(0)
            leading = len(raw_item) - len(raw_item.lstrip(" \t:：【[（("))
            item = raw_item[leading:]
            if not item:
                continue
            name: str | None = None
            for length in (4, 3, 2):
                if len(item) < length or not all("\u3400" <= char <= "\u9fff" for char in item[:length]):
                    continue
                candidate = item[:length]
                if candidate[0] not in self.surnames:
                    continue
                tail = item[length:].lstrip(" \t")
                if tail and not tail.startswith(("（", "(", "等", "参加", "出席", "列席", "担任", "负责", "签字", "签章", "先生", "女士")):
                    continue
                name = candidate
                break
            if name is None:
                continue
            start = offset + item_match.start() + leading
            result.append((start, start + len(name), name))
        return result

    def _recognize_structured_contexts(
        self,
        text: str,
        result: list[Span],
        seen: set[tuple[int, int]],
    ) -> None:
        """Catch high-recall names in rosters, fields, parentheses and cells."""

        def append(start: int, end: int, name: str, rule_id: str) -> None:
            if (start, end) in seen or not name or name[0] not in self.surnames:
                return
            seen.add((start, end))
            result.append(
                Span(
                    start,
                    end,
                    "PERSON",
                    name,
                    score=0.90,
                    priority=self.priority,
                    source="person_structured_context",
                    rule_id=rule_id,
                    anonymize=self.anonymize,
                    protect=self.protect,
                )
            )

        cursor = 0
        cjk_item = re.compile(r"(?<![\u3400-\u9fff])(?P<name>[\u3400-\u9fff]{2,3})(?![\u3400-\u9fff])")
        parenthesized = re.compile(r"[（(][ \t]*(?P<name>[\u3400-\u9fff]{2,3})[ \t]*[）)]")
        colon_value = re.compile(
            r"(?:^|[：:])[ \t]*[【\[]?(?P<name>[\u3400-\u9fff]{2,3})[】\]]?"
            r"(?=[ \t]*(?:$|[,，。；;|<>&]))"
        )
        # Do not take the tail of an ordinary field label (for example,
        # ``交易编号:`` or ``公司名称:``) as a person's name.
        name_label = re.compile(
            r"(?<![\u3400-\u9fff])(?P<name>[\u3400-\u9fff]{2,3})[ \t]*[：:]"
        )
        for raw_line in text.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            line_start = cursor
            cursor += len(raw_line)
            if not line:
                continue
            candidates = list(cjk_item.finditer(line))
            name_candidates = [match for match in candidates if match.group("name")[0] in self.surnames]
            separators = "、,，;；|/／"
            if len(name_candidates) >= 2 and any(char in line for char in separators):
                for match in name_candidates:
                    append(
                        line_start + match.start("name"),
                        line_start + match.end("name"),
                        match.group("name"),
                        "person_roster_delimited",
                    )

            for pattern, rule_id in (
                (parenthesized, "person_parenthesized"),
                (colon_value, "person_colon_value"),
                (name_label, "person_name_label"),
            ):
                for match in pattern.finditer(line):
                    append(
                        line_start + match.start("name"),
                        line_start + match.end("name"),
                        match.group("name"),
                        rule_id,
                    )

            if name_candidates:
                first = name_candidates[0]
                after = line[first.end("name") : first.end("name") + 8]
                if first.start("name") == 0 and after.startswith((" ", "\t", "<", "&", "(", "（")):
                    append(
                        line_start + first.start("name"),
                        line_start + first.end("name"),
                        first.group("name"),
                        "person_line_leading",
                    )

            for slash_segment in re.finditer(r"(?:^|[/／])(?P<segment>[^/／\n]+)", line):
                segment = slash_segment.group("segment")
                tail = re.search(r"(?P<name>[\u3400-\u9fff]{2,3})(?=[ \t]*(?:$|[<>&,，;；]))", segment)
                if tail and tail.group("name")[0] in self.surnames:
                    append(
                        line_start + slash_segment.start("segment") + tail.start("name"),
                        line_start + slash_segment.start("segment") + tail.end("name"),
                        tail.group("name"),
                        "person_slash_segment",
                    )
