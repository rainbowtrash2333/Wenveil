"""Generate synthetic finance-domain organization alias/linking data.

The generator intentionally uses fictional organization names.  It creates
lossless NER JSONL plus a separate entity-linking JSONL so the same local
backbone can be fine-tuned for mention detection first and canonical linking
later.  No source document or real customer value is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from .build_dataset import write_jsonl
from .samples import sample_from_spans
from .types import TrainingSample


FICTIONAL_ENTITIES = (
    {
        "entity_id": "FIN_ASSET_001",
        "canonical": "华澜保险资产管理有限公司",
        "aliases": ("华澜资管", "华澜资产"),
        "locations": ("北京", "上海", "深圳"),
    },
    {
        "entity_id": "FIN_PROPERTY_001",
        "canonical": "中衡财产保险股份有限公司",
        "aliases": ("中衡财险", "中衡保险"),
        "locations": ("杭州", "南京", "武汉"),
    },
    {
        "entity_id": "FIN_TRUST_001",
        "canonical": "嘉成信托有限责任公司",
        "aliases": ("嘉成信托", "嘉成信"),
        "locations": ("北京", "成都", "西安"),
    },
    {
        "entity_id": "FIN_BROKER_001",
        "canonical": "东岳证券股份有限公司",
        "aliases": ("东岳证券", "东岳股份"),
        "locations": ("广州", "厦门", "天津"),
    },
    {
        "entity_id": "FIN_FUND_001",
        "canonical": "新曜股权投资基金管理有限公司",
        "aliases": ("新曜基金", "新曜投资"),
        "locations": ("北京", "苏州", "合肥"),
    },
    {
        "entity_id": "FIN_GROUP_001",
        "canonical": "安澜保险集团股份有限公司",
        "aliases": ("安澜保险", "安澜集团"),
        "locations": ("北京", "重庆", "青岛"),
    },
)

COUNTERPARTIES = (
    "远景银行股份有限公司",
    "启明基金管理有限公司",
    "恒岳资本管理有限公司",
    "清源投资合伙企业（有限合伙）",
)


def _spans_for(text: str, value: str, label: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = 0
    while True:
        start = text.find(value, cursor)
        if start < 0:
            break
        result.append(
            {
                "start": start,
                "end": start + len(value),
                "entity_type": label,
                "surface": value,
            }
        )
        cursor = start + len(value)
    return result


def _sample(document_id: str, text: str, values: list[tuple[str, str]], metadata: dict[str, Any]) -> TrainingSample:
    candidates: list[dict[str, Any]] = []
    for value, label in values:
        candidates.extend(_spans_for(text, value, label))
    # Token-classification annotations cannot overlap.  Prefer the longest
    # organization mention when an alias is embedded in a canonical name, or
    # when a location is embedded in a subsidiary/branch mention.  The linking
    # JSONL keeps the relation and location separately for the second stage.
    spans: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            int(item["start"]),
            -(int(item["end"]) - int(item["start"])),
            str(item["entity_type"]),
        ),
    ):
        if any(
            int(existing["start"]) < int(candidate["end"])
            and int(candidate["start"]) < int(existing["end"])
            for existing in spans
        ):
            continue
        spans.append(candidate)
    spans.sort(key=lambda item: (int(item["start"]), int(item["end"])))
    return sample_from_spans(text, spans, document_id=document_id, metadata=metadata)


def _link_record(
    document_id: str,
    mention: str,
    entity: dict[str, Any],
    candidates: list[dict[str, Any]],
    relation: str,
    location: str = "",
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "mention": mention,
        "relation": relation,
        "location": location,
        "candidates": candidates,
        "label": entity["entity_id"],
        "metadata": {"synthetic": True, "domain": "finance"},
    }


def generate(samples: int = 600, *, seed: int = 20260912) -> tuple[list[TrainingSample], list[dict[str, Any]]]:
    rng = random.Random(seed)
    ner_samples: list[TrainingSample] = []
    link_samples: list[dict[str, Any]] = []
    templates = ("declaration", "management", "subsidiary", "branch", "related", "fund")

    for index in range(samples):
        entity = FICTIONAL_ENTITIES[index % len(FICTIONAL_ENTITIES)]
        alias = entity["aliases"][index % len(entity["aliases"])]
        location = entity["locations"][index % len(entity["locations"])]
        other = rng.choice([item for item in FICTIONAL_ENTITIES if item["entity_id"] != entity["entity_id"]])
        counterparty = rng.choice(COUNTERPARTIES)
        template = templates[index % len(templates)]

        if template == "declaration":
            text = f'{entity["canonical"]}（以下简称“{alias}”）负责本次股权投资计划。'
            values = [(entity["canonical"], "ORG_FULL"), (alias, "ORG_ALIAS")]
            relation = "ALIAS_OF"
            link_mention = alias
        elif template == "management":
            text = f"管理人：{entity['canonical']}；受托人：{alias}；托管机构：{counterparty}。"
            values = [
                (entity["canonical"], "ORG_FULL"),
                (alias, "ORG_ALIAS"),
                (counterparty, "ORG_FULL"),
            ]
            relation = "ALIAS_OF"
            link_mention = alias
        elif template == "subsidiary":
            mention = f"{alias}{location}子公司"
            text = f"{mention}负责投后管理和风险监测。"
            values = [(mention, "ORG_SUBSIDIARY"), (location, "LOCATION")]
            relation = "SUBSIDIARY_OF"
            link_mention = mention
        elif template == "branch":
            mention = f"{alias}{location}分公司"
            text = f"{mention}作为委托人参与认购并提交投资指令。"
            values = [(mention, "ORG_BRANCH"), (location, "LOCATION")]
            relation = "BRANCH_OF"
            link_mention = mention
        elif template == "related":
            text = f"本次关联交易涉及{alias}与{other['canonical']}，双方应履行内部审批程序。"
            values = [(alias, "ORG_ALIAS"), (other["canonical"], "ORG_FULL")]
            relation = "ALIAS_OF"
            link_mention = alias
        else:
            project = f"{location}新兴产业股权投资计划"
            text = f"{alias}拟设立《{project}》，并与{counterparty}签署受托合同。"
            values = [(alias, "ORG_ALIAS"), (project, "PROJECT"), (counterparty, "ORG_FULL")]
            relation = "ALIAS_OF"
            link_mention = alias

        document_id = f"finance-synthetic-{index:06d}"
        ner_samples.append(
            _sample(
                document_id,
                text,
                values,
                {
                    "synthetic": True,
                    "domain": "finance",
                    "template": template,
                    "canonical_entity_id": entity["entity_id"],
                    "relation": relation,
                },
            )
        )

        negative = rng.choice(
            [candidate for candidate in FICTIONAL_ENTITIES if candidate["entity_id"] != entity["entity_id"]]
        )
        candidates = [
            {
                "entity_id": entity["entity_id"],
                "canonical": entity["canonical"],
                "aliases": list(entity["aliases"]),
            },
            {
                "entity_id": negative["entity_id"],
                "canonical": negative["canonical"],
                "aliases": list(negative["aliases"]),
            },
        ]
        rng.shuffle(candidates)
        link_samples.append(
            _link_record(document_id, link_mention, entity, candidates, relation, location)
        )

    return ner_samples, link_samples


def write_generated(output_dir: str | Path, *, samples: int = 600, seed: int = 20260912) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ner_samples, link_samples = generate(samples, seed=seed)
    ner_path = output / "financial_alias_ner.jsonl"
    link_path = output / "financial_alias_linking.jsonl"
    ner_count = write_jsonl(ner_path, ner_samples)
    with link_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in link_samples:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {
        "schema_version": 1,
        "seed": seed,
        "synthetic": True,
        "domain": "finance",
        "ner_samples": ner_count,
        "link_samples": len(link_samples),
        "ner_sha256": hashlib.sha256(ner_path.read_bytes()).hexdigest(),
        "link_sha256": hashlib.sha256(link_path.read_bytes()).hexdigest(),
        "labels": ["ORG_FULL", "ORG_ALIAS", "ORG_SUBSIDIARY", "ORG_BRANCH", "LOCATION", "PROJECT"],
        "relations": ["ALIAS_OF", "SUBSIDIARY_OF", "BRANCH_OF"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="generate synthetic finance alias/linking data")
    parser.add_argument("--output-dir", type=Path, default=Path("training/generated/financial_alias"))
    parser.add_argument("--samples", type=int, default=600)
    parser.add_argument("--seed", type=int, default=20260912)
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error("--samples must be positive")
    manifest = write_generated(args.output_dir, samples=args.samples, seed=args.seed)
    print(json.dumps({key: manifest[key] for key in ("ner_samples", "link_samples", "seed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
