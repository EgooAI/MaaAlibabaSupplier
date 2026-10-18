"""Request scaffolding around the Markdown transcript (LLM input builders)."""

from backend.app.shared.agent.inputs import (
    build_analysis_input,
    build_chat_dialog_input,
    build_reply_suggestion_input,
)

TRANSCRIPT = "**[2026-09-18 10:03:00] 买家**\n\nhello"


def test_suggestion_input_embeds_the_transcript_verbatim():
    assert build_reply_suggestion_input(TRANSCRIPT) == f"## 对话记录\n\n{TRANSCRIPT}"


def test_analysis_input_puts_the_task_before_the_transcript():
    prompt = build_analysis_input(task="判断客户阶段", transcript=TRANSCRIPT)
    assert prompt.index("## 任务") < prompt.index("## 聊天记录")
    assert prompt.endswith(TRANSCRIPT)


def test_dialog_input_labels_turns_and_ends_with_the_latest_user_message():
    prompt = build_chat_dialog_input([
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "- 回答一"},
        {"role": "user", "content": "追问"},
    ])
    assert prompt.index("**User**") < prompt.index("**Agent**")
    assert "- 回答一" in prompt
    assert prompt.rstrip().endswith("追问")
