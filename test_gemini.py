"""
LLM API 连接验证脚本 — 自动适配当前 provider。

使用方法:
1. 确保 .env 中已配置 API Key
2. 运行: python test_gemini.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from core.llm_client import LLMClient

# provider → 环境变量映射
KEY_ENV_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}

MODEL_ENV_MAP = {
    "deepseek": "DEEPSEEK_MODEL",
    "gemini": "GEMINI_MODEL",
    "claude": "CLAUDE_MODEL",
}


def test_non_stream(provider, model):
    print("\n" + "=" * 60)
    print(f"  测试 1: 非流式对话 ({provider} / {model})")
    print("=" * 60)

    client = LLMClient()

    response = client.chat(
        messages=[
            {"role": "user", "content": "你好！请用一句话介绍你自己，并说明你能在销售培训中帮到什么。"}
        ],
        system="你是「销售神队友」AI 培训助手，专门帮助线下业务员提升话术能力。回答简洁专业。",
    )

    print(f"\nAI 回复:\n{response}\n")
    print("非流式测试通过!")


def test_stream(provider, model):
    print("\n" + "=" * 60)
    print(f"  测试 2: 流式对话 ({provider} / {model})")
    print("=" * 60)

    client = LLMClient()

    print("\nAI 流式回复:")
    for chunk in client.chat(
        messages=[
            {"role": "user", "content": "客户说'太贵了'，我怎么回应比较好？给一个简短示例。"}
        ],
        system="你是资深销售培训师，给出专业、可执行的话术建议。",
        stream=True,
    ):
        print(chunk, end="", flush=True)

    print("\n\n流式测试通过!")


def test_multi_turn(provider, model):
    print("\n" + "=" * 60)
    print(f"  测试 3: 多轮对话 ({provider} / {model})")
    print("=" * 60)

    client = LLMClient()

    messages = [
        {"role": "user", "content": "你好，我是新来的销售，想学学怎么打陌拜电话。"},
        {"role": "assistant", "content": "你好！陌拜电话最重要的是前15秒——快速表明身份、说明来意、给出价值。你想先从哪个行业场景练起？"},
        {"role": "user", "content": "我卖企业软件的，先教我第一句话怎么开口吧。"},
    ]

    response = client.chat(
        messages=messages,
        system="你是销售培训教练，用实战话术帮助学员。每次回复给出一条可直接使用的话术。",
    )

    print(f"\nAI 回复:\n{response}\n")
    print("多轮对话测试通过!")


def main():
    provider = os.getenv("LLM_PROVIDER", "deepseek")
    model = os.getenv(MODEL_ENV_MAP.get(provider, ""), "unknown")
    key_env = KEY_ENV_MAP.get(provider, "")
    api_key = os.getenv(key_env, "")

    print("\n" + "=" * 60)
    print("  Sales-Skill-AI · LLM 连接验证")
    print("=" * 60)

    if not api_key:
        print(f"\n未检测到 {key_env}！")
        print(f"   请在 .env 文件中设置 {key_env}=你的密钥\n")
        sys.exit(1)

    print(f"\nProvider: {provider}")
    print(f"Model:    {model}")
    print(f"API Key:  {key_env} (长度: {len(api_key)} 字符)")

    try:
        test_non_stream(provider, model)
        test_stream(provider, model)
        test_multi_turn(provider, model)
    except Exception as e:
        print(f"\n测试失败: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"  全部测试通过! {provider.upper()} 连接正常。")
    print("=" * 60)


if __name__ == "__main__":
    main()
