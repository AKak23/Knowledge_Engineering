from ontology_construct.extractor import LocalExtractor, render_extract_prompt
from ontology_construct.llm import MockLLMClient
from ontology_construct.models import Chunk


def test_local_extractor_uses_llm_response() -> None:
    import asyncio

    chunk = Chunk(
        id="chunk_0001",
        text="苹果公司成立于1976年。",
        start_char=0,
        end_char=12,
        token_count=12,
    )
    client = MockLLMClient(
        '{"entities": [{"id": "e1", "name": "苹果公司"}], "triples": [{"subject": "e1", "predicate": "成立年份", "object": 1976, "object_type": "literal"}]}'
    )

    ontology = asyncio.run(LocalExtractor(client).extract(chunk))

    assert ontology.entities[0].name == "苹果公司"
    assert ontology.triples[0].object_value == 1976


def test_render_extract_prompt_includes_supplemental_prompt(tmp_path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("规则\n文本：\n{chunk_text}", encoding="utf-8")
    chunk = Chunk(
        id="chunk_0001",
        text="张三在北京工作。",
        start_char=0,
        end_char=8,
        token_count=8,
    )

    prompt = render_extract_prompt(
        chunk,
        prompt_path,
        supplemental_prompt="这是一部小说，重点抽取人物、地点和人物关系。",
    )

    assert "用户补充说明" in prompt
    assert "重点抽取人物、地点和人物关系" in prompt
    assert prompt.index("用户补充说明") < prompt.index("文本：")
    assert prompt.endswith("张三在北京工作。")
