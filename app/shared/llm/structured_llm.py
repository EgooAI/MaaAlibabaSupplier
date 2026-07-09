import re
from typing import Any, Dict, List, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.shared.utils.env import get_env_str, load_workdir_env
from .errors import (
    InvalidInputError,
    LLMRequestError,
    LLMResponseError,
    OutputParseError,
)

load_workdir_env()

DEFAULT_MODEL = get_env_str("OPENAI_MODEL", "GPT-5")

DEFAULT_BASE_URL = get_env_str("OPENAI_BASE_URL", "") or None

DEFAULT_API_KEY = get_env_str("OPENAI_API_KEY", "")

PROMPT_TEMPLATE = """You are requested to generate a JSON output based on the following task.

## Task

{prompt}

## Fields

Above are the JSON fields you should output.
{fields}

## Requirement

1. The output must start with {{ and end with }}.
2. The JSON content must:
    - Use 2 spaces for indent.
    - Have no JSON comment.
    - Properly handle special characters like " and \\n in JSON string values.
"""

T = TypeVar("T", bound=BaseModel)


class StructuredLLM:
    def __init__(
        self,
        *,
        model: str = None,
        base_url: str = None,
        api_key: str = None,
        timeout: float = 60.0,
    ) -> None:
        model = model or DEFAULT_MODEL
        api_key = api_key or DEFAULT_API_KEY
        base_url = base_url or DEFAULT_BASE_URL

        if not isinstance(model, str) or not model.strip():
            raise InvalidInputError("`model` must be a non-empty string.")
        if not isinstance(api_key, str) or not api_key.strip():
            raise InvalidInputError("`api_key` must be provided.")
        if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
            raise InvalidInputError("`base_url` must be a non-empty string when provided.")
        if timeout <= 0:
            raise InvalidInputError("`timeout` must be greater than 0.")

        self.model = model.strip()
        self.api_key = api_key.strip()
        self.base_url = base_url.strip() if isinstance(base_url, str) else None
        self.timeout = timeout

        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def process(self, prompt: str, cls: Type[T]) -> T:
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidInputError("`prompt` must be a non-empty string.")
        if not isinstance(cls, type) or not issubclass(cls, BaseModel):
            raise InvalidInputError("`cls` must be a subclass of pydantic.BaseModel.")

        # Build instruction
        fields = self._build_fields_instruction(cls)
        full_prompt = PROMPT_TEMPLATE.format(
            prompt=prompt.strip(),
            fields=fields,
        )

        # Send to LLM
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": full_prompt}],
            )
        except Exception as exc:
            raise LLMRequestError(f"Failed to call LLM: {exc}") from exc

        # Extract LLM response
        content = self._extract_text(completion)
        if not content:
            raise LLMResponseError("LLM returned empty content.")
        content = self._strip_code_fence(content)
        if not content.startswith("{") or not content.endswith("}"):
            raise OutputParseError(
                f"LLM output is not a JSON object string. Raw output: {content}"
            )

        # Parse data
        try:
            return cls.model_validate_json(content)
        except ValidationError as exc:
            raise OutputParseError(
                f"Failed to parse LLM output into {cls.__name__}: {exc}. Raw output: {content}"
            ) from exc
        except Exception as exc:
            raise OutputParseError(
                f"Failed to parse LLM output into {cls.__name__}: {exc}. Raw output: {content}"
            ) from exc

    @staticmethod
    def _extract_text(completion: object) -> str:
        choices = getattr(completion, "choices", None)
        if not choices:
            raise LLMResponseError("LLM response does not contain choices.")

        first_message = getattr(choices[0], "message", None)
        if first_message is None:
            raise LLMResponseError("LLM response first choice has no message.")

        content = getattr(first_message, "content", None)
        if isinstance(content, str):
            return content.strip()

        # Defensive handling for SDKs returning content blocks.
        if isinstance(content, list):
            text_parts = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
            return "".join(text_parts).strip()

        return ""

    _CODE_FENCE_RE = re.compile(
        r"^\s*```(?:json)?\s*[\r\n]*(.*?)[\r\n]*\s*```\s*$",
        re.DOTALL,
    )

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Strip ```json ... ``` or ``` ... ``` wrapping from *text*."""
        m = StructuredLLM._CODE_FENCE_RE.match(text)
        return m.group(1).strip() if m else text

    @staticmethod
    def _build_fields_instruction(cls: Type[T]) -> str:
        schema = StructuredLLM._get_model_json_schema(cls)
        if not isinstance(schema, dict):
            return "- Use the schema defined by the provided model."

        properties = schema.get("properties")
        required_fields = set(schema.get("required", []))
        if not isinstance(properties, dict) or not properties:
            return "- Use the schema defined by the provided model."

        lines: List[str] = []
        for field_name, field_schema in properties.items():
            if not isinstance(field_name, str):
                continue
            if not isinstance(field_schema, dict):
                field_schema = {}

            field_type = StructuredLLM._schema_type_to_text(field_schema)
            is_required = "required" if field_name in required_fields else "optional"
            description = field_schema.get("description")

            line = f"- `{field_name}` ({field_type}, {is_required})"
            if isinstance(description, str) and description.strip():
                line += f": {description.strip()}"
            lines.append(line)

        if not lines:
            return "- Use the schema defined by the provided model."
        return "\n".join(lines)

    @staticmethod
    def _get_model_json_schema(cls: Type[T]) -> Dict[str, Any]:
        # pydantic v2 have migrated `schema` to `model_json_schema`
        schema = cls.model_json_schema()
        if isinstance(schema, dict):
            return schema

    @staticmethod
    def _schema_type_to_text(field_schema: Dict[str, Any]) -> str:
        type_value = field_schema.get("type")
        if isinstance(type_value, str) and type_value:
            return type_value

        any_of = field_schema.get("anyOf")
        if isinstance(any_of, list):
            collected: List[str] = []
            for item in any_of:
                if isinstance(item, dict):
                    t = item.get("type")
                    if isinstance(t, str) and t and t not in collected:
                        collected.append(t)
            if collected:
                return " | ".join(collected)

        enum_values = field_schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            return "enum"

        ref = field_schema.get("$ref")
        if isinstance(ref, str) and ref:
            return ref.split("/")[-1]

        return "unknown"
