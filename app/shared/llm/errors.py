class StructuredLLMError(Exception):
    """Base error for StructuredLLM."""


class InvalidInputError(StructuredLLMError):
    """Raised when input arguments are invalid."""


class LLMRequestError(StructuredLLMError):
    """Raised when request to upstream LLM fails."""


class LLMResponseError(StructuredLLMError):
    """Raised when upstream LLM response is empty or malformed."""


class OutputParseError(StructuredLLMError):
    """Raised when LLM output cannot be parsed into target schema."""
