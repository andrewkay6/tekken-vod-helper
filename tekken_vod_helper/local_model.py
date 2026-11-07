"""Shared defaults for the local Qwen server used by boundary tooling.

An endpoint may be written as a bare origin (http://host:port), a /v1 root, or a full
chat-completions URL; all three normalize to the same request path. QWEN_BASE_URL and
QWEN_MODEL override the defaults so a relocated server needs no code edit.
"""
import os

DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
DEFAULT_MODEL = "qwen3.8-flash-next"
ENDPOINT_ENV = "QWEN_BASE_URL"
MODEL_ENV = "QWEN_MODEL"


def default_endpoint():
    # type: () -> str
    return (os.environ.get(ENDPOINT_ENV) or "").strip() or DEFAULT_ENDPOINT


def default_model():
    # type: () -> str
    return (os.environ.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL


def chat_completions_url(endpoint):
    # type: (str) -> str
    base = (endpoint or "").strip().rstrip("/")
    if not base:
        raise ValueError("model endpoint is empty")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"
