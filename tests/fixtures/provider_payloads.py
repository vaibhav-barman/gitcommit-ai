"""Model-listing payloads per provider, trimmed to the fields the catalogues read.

Each list holds at least one entry of every kind a filter rule drops, so every
rule is exercised.
"""

from __future__ import annotations

from typing import Any

OPENAI_MODELS: dict[str, Any] = {
    "data": [
        {"id": "gpt-5", "created": 300},
        {"id": "gpt-5-mini", "created": 310},
        {"id": "gpt-5-2026-08-07", "created": 305},
        {"id": "o4-mini", "created": 200},
        {"id": "chatgpt-4o-latest", "created": 100},
        {"id": "gpt-4o-mini-tts", "created": 290},
        {"id": "gpt-image-1", "created": 280},
        {"id": "gpt-4o-realtime-preview", "created": 270},
        {"id": "gpt-4o-audio-preview", "created": 265},
        {"id": "gpt-4o-transcribe", "created": 260},
        {"id": "gpt-4o-search-preview", "created": 255},
        {"id": "gpt-3.5-turbo-instruct", "created": 50},
        {"id": "gpt-5-codex", "created": 315},
        {"id": "text-embedding-3-large", "created": 250},
        {"id": "whisper-1", "created": 40},
        {"id": "dall-e-3", "created": 30},
        {"id": "omni-moderation-latest", "created": 20},
        {"id": "tts-1", "created": 10},
        {"id": "davinci-002", "created": 5},
    ]
}

ANTHROPIC_PAGES: list[dict[str, Any]] = [
    {
        "data": [
            {"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"},
            {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"},
        ],
        "has_more": True,
        "last_id": "claude-opus-5-5",
    },
    {
        "data": [
            {
                "id": "claude-3-haiku-20240307",
                "display_name": "Claude Haiku 3",
                "lifecycle": "deprecated",
            },
            {"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5"},
        ],
        "has_more": True,
        "last_id": "claude-haiku-5-5",
    },
    {
        "data": [{"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"}],
        "has_more": False,
        "last_id": "claude-sonnet-4-5",
    },
]


def _gemini(name: str, display: str, *methods: str) -> dict[str, Any]:
    return {
        "name": f"models/{name}",
        "displayName": display,
        "supportedGenerationMethods": list(methods or ("generateContent",)),
    }


GEMINI_PAGES: list[dict[str, Any]] = [
    {
        "models": [
            _gemini("gemini-2.5-flash", "Gemini 2.5 Flash"),
            _gemini("gemini-3.0-pro-preview", "Gemini 3.0 Pro Preview"),
            _gemini("text-embedding-004", "Text Embedding", "embedContent"),
            _gemini("gemini-embedding-001", "Gemini Embedding", "generateContent"),
        ],
        "nextPageToken": "page-2",
    },
    {
        "models": [
            _gemini("gemini-2.5-pro", "Gemini 2.5 Pro"),
            _gemini("gemini-3.0-flash", "Gemini 3.0 Flash"),
            _gemini("imagen-4.0-generate", "Imagen 4"),
            _gemini("veo-3.0-generate", "Veo 3"),
            _gemini("gemini-2.5-flash-preview-tts", "TTS"),
            _gemini("gemini-2.5-flash-image", "Image"),
        ],
        "nextPageToken": "page-3",
    },
    {
        "models": [
            _gemini("gemini-2.5-flash-native-audio", "Native audio"),
            _gemini("gemini-live-2.5-flash", "Live"),
            _gemini("aqa", "Attributed QA"),
            _gemini("gemini-2.0-flash-exp", "Gemini 2.0 Flash Experimental"),
            _gemini("gemma-3-27b-it", "Gemma 3 27B"),
        ]
    },
]


def _router(
    model_id: str,
    name: str,
    created: int,
    inputs: tuple[str, ...] = ("text",),
    outputs: tuple[str, ...] = ("text",),
    price: str = "0.000001",
    expiration: str | None = None,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": name,
        "created": created,
        "architecture": {"input_modalities": list(inputs), "output_modalities": list(outputs)},
        "pricing": {"prompt": price, "completion": price},
        "expiration_date": expiration,
    }


OPENROUTER_MODELS: dict[str, Any] = {
    "data": [
        _router("anthropic/claude-sonnet-5.5", "Anthropic: Claude Sonnet 5.5", 500),
        _router("qwen/qwen3.8-27b", "Qwen: Qwen3.8 27B", 450, price="0"),
        _router("qwen/qwen3.8-flash", "Qwen: Qwen3.8 Flash", 480),
        _router("openai/gpt-5:batch", "OpenAI: GPT-5 (batch)", 490),
        _router("meta/llama-guard-4", "Meta: Llama Guard 4", 470),
        _router("openai/text-embed-3", "OpenAI: Embeddings", 460),
        _router("google/image-gen", "Image generator", 440, outputs=("image",)),
        _router("vision/only", "Image-only input", 430, inputs=("image",)),
        _router("old/expired", "Expired model", 420, expiration="2026-01-01"),
        _router("still/valid", "Expiring later", 410, expiration="2099-01-01"),
    ]
}

HUGGINGFACE_MODELS: dict[str, Any] = {
    "data": [
        {
            "id": "deepseek-ai/DeepSeek-V4.1-Flash",
            "created": 900,
            "architecture": {"output_modalities": ["text"]},
            "providers": [
                {"provider": "a", "status": "live", "pricing": {"output": 0.9}},
                {"provider": "b", "status": "live", "pricing": {"output": 0.4}},
            ],
        },
        {
            "id": "Qwen/Qwen3-Coder",
            "created": 950,
            "architecture": {"output_modalities": ["text"]},
            "providers": [{"provider": "a", "status": "live"}],
        },
        {
            "id": "staging/only",
            "created": 990,
            "architecture": {"output_modalities": ["text"]},
            "providers": [{"provider": "a", "status": "staging"}],
        },
        {
            "id": "black-forest/flux",
            "created": 980,
            "architecture": {"output_modalities": ["image"]},
            "providers": [{"provider": "a", "status": "live"}],
        },
    ]
}

OLLAMA_TAGS: dict[str, Any] = {
    "models": [
        {
            "name": "gemma3:4b",
            "modified_at": "2026-10-01T10:00:00Z",
            "size": 3_300_000_000,
            "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "vision"],
        },
        {
            "name": "qwen3:8b",
            "modified_at": "2026-10-20T10:00:00Z",
            "size": 5_200_000_000,
            "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "tools"],
        },
        {
            "name": "nomic-embed-text:latest",
            "modified_at": "2026-09-01T10:00:00Z",
            "size": 270_000_000,
            "details": {"family": "nomic-bert", "families": ["nomic-bert"]},
            "capabilities": ["embedding"],
        },
    ]
}

OLLAMA_OLD_TAGS: dict[str, Any] = {
    "models": [
        {"name": "llama3.1:latest", "modified_at": "2026-01-01T00:00:00Z", "details": {}},
        {"name": "all-minilm:latest", "details": {"family": "bert", "families": ["bert"]}},
        {"name": "mxbai-embed-large:latest", "details": {"family": "x"}},
    ]
}
