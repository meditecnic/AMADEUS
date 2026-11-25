"""Diary dialogue as passed to the model provider."""

import json

import pytest

from app.services import credentials
from app.services.memory_v11.diary_generation import default_diary_completion
from app.services.provider_runtime import deepseek_service


@pytest.fixture
def diary_dialogue(monkeypatch):
    requests = []

    async def capture_completion(*, messages, **_kwargs):
        requests.append(json.loads(messages[1]["content"])["messages"])
        return {"body_ja": "今日の話が心に残った。", "body_zh": "今天的话留在心里。"}

    credentials.credential_store.set("deepseek", "valid-key-for-offline-transport")
    monkeypatch.setattr(deepseek_service, "complete_json", capture_completion)

    async def request(identity_mode, source):
        await default_diary_completion(
            identity_mode=identity_mode,
            worldline="steins_gate",
            provider_id="deepseek",
            model_id="deepseek-flash",
            messages=source,
        )
        [dialogue] = requests
        return dialogue

    return request


@pytest.mark.parametrize(
    ("identity_mode", "other_speaker"),
    [("okabe", "岡部"), ("self", "相手")],
    ids=["okabe", "self"],
)
async def test_diary_completion_labels_both_speakers(
    diary_dialogue, identity_mode, other_speaker
):
    dialogue = await diary_dialogue(
        identity_mode,
        [
            {"role": "user", "text": "明天再聊。"},
            {"role": "assistant", "text": "そうね。"},
        ],
    )

    assert dialogue == [
        {"turn": 1, "speaker": other_speaker, "text": "明天再聊。"},
        {"turn": 1, "speaker": "私（紅莉栖）", "text": "そうね。"},
    ]


async def test_diary_completion_numbers_each_user_reply_pair(diary_dialogue):
    dialogue = await diary_dialogue(
        "okabe",
        [
            {"id": 41, "role": "user", "text": "今天吃过午饭了。"},
            {"id": 42, "role": "assistant", "text": "なら安心したわ。"},
            {"id": 43, "role": "user", "text": "我去散步。"},
            {"id": 44, "role": "assistant", "text": "いってらっしゃい。"},
        ],
    )

    assert [row["turn"] for row in dialogue] == [1, 1, 2, 2]


async def test_diary_completion_numbers_leading_assistant_from_one(diary_dialogue):
    dialogue = await diary_dialogue(
        "self",
        [
            {"id": 40, "role": "assistant", "text": "外は寒いわね。"},
            {"id": 41, "role": "user", "text": "今天吃过午饭了。"},
            {"id": 42, "role": "assistant", "text": "なら安心したわ。"},
        ],
    )

    assert [row["turn"] for row in dialogue] == [1, 1, 1]
