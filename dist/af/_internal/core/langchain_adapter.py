"""
LangChain Adapter Layer for Agent Factory.

Provides graceful degradation when LangChain is not installed.
All consumers should check LANGCHAIN_AVAILABLE before using LangChain features.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

# ── Graceful Import ──────────────────────────────────────────────────
LANGCHAIN_AVAILABLE = False
_BaseTool = None
_PydanticOutputParser = None

try:
    from langchain_core.tools import BaseTool as _BaseTool  # type: ignore[assignment]
    from langchain_core.output_parsers import PydanticOutputParser as _PydanticOutputParser  # type: ignore[assignment]
    LANGCHAIN_AVAILABLE = True
except ImportError:
    pass


# ── LangChainToolAdapter ────────────────────────────────────────────
class LangChainToolAdapter:
    """
    Wraps a LangChain BaseTool so it looks like an agent-factory skill module.

    The adapter exposes a callable that matches the agent-factory tool signature:
        func(ctx: dict, **kwargs) -> str
    """

    def __init__(self, lc_tool: Any):
        if not LANGCHAIN_AVAILABLE or _BaseTool is None:
            raise RuntimeError("LangChain is not installed")
        if not isinstance(lc_tool, _BaseTool):
            raise TypeError(f"Expected BaseTool, got {type(lc_tool).__name__}")
        self._tool = lc_tool
        # Expose as a pseudo-module with a single callable
        self.__skill_id__ = lc_tool.name

    def __call__(self, ctx: dict, **kwargs: Any) -> str:
        return self._tool.run(kwargs)

    # Make it look like a module with a single exported function
    @property
    def __name__(self) -> str:
        return self._tool.name

    @property
    def __doc__(self) -> str | None:
        return self._tool.description


# ── LangChainChatModelFactory ───────────────────────────────────────
class LangChainChatModelFactory:
    """
    Maps provider strings to LangChain ChatModel instances.
    Falls back to None if the provider is unsupported or LangChain is unavailable.
    """

    _PROVIDER_MAP: dict[str, str] = {
        "gemini": "langchain_google_genai.ChatGoogleGenerativeAI",
        "openai": "langchain_openai.ChatOpenAI",
        "anthropic": "langchain_anthropic.ChatAnthropic",
    }

    @classmethod
    def create(cls, provider: str, model_name: str, **kwargs: Any) -> Any | None:
        if not LANGCHAIN_AVAILABLE:
            return None

        dotted = cls._PROVIDER_MAP.get(provider)
        if not dotted:
            return None

        module_path, class_name = dotted.rsplit(".", 1)
        try:
            import importlib
            mod = importlib.import_module(module_path)
            chat_cls = getattr(mod, class_name)
            return chat_cls(model=model_name, **kwargs)
        except Exception:
            return None


# ── PydanticOutputAdapter ───────────────────────────────────────────
class PydanticOutputAdapter:
    """
    Wraps PydanticOutputParser for robust JSON extraction.
    Falls back to regex-based extraction when LangChain is unavailable.
    """

    def __init__(self, schema: type | None = None):
        self._schema = schema
        self._parser = None
        if LANGCHAIN_AVAILABLE and _PydanticOutputParser and schema is not None:
            try:
                self._parser = _PydanticOutputParser(pydantic_object=schema)
            except Exception:
                self._parser = None

    def parse(self, text: str) -> Any:
        """Parse LLM text output into structured data."""
        # Try LangChain parser first
        if self._parser is not None:
            try:
                return self._parser.parse(text)
            except Exception:
                pass

        # Fallback: regex-based JSON extraction
        return self._extract_json_fallback(text)

    def get_format_instructions(self) -> str:
        """Return format instructions for prompts."""
        if self._parser is not None:
            try:
                return self._parser.get_format_instructions()
            except Exception:
                pass
        if self._schema is not None:
            return f"Return valid JSON matching this schema: {self._schema.__name__}"
        return "Return valid JSON."

    @staticmethod
    def _extract_json_fallback(text: str) -> dict:
        """Regex-based JSON extraction (same as LLMEngine.generate_json)."""
        if not text:
            return {}
        try:
            if "```json" in text:
                json_block = text.split("```json")[1].split("```")[0].strip()
            else:
                json_block = text.strip()
                m = re.search(r'\{[\s\S]*\}', json_block)
                if m:
                    json_block = m.group(0)
            return json.loads(json_block)
        except (json.JSONDecodeError, Exception):
            return {}
