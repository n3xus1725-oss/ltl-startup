"""LiteLLM wrapper gateway with retries, exponential backoff, timeout, and structured error handling."""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from packages.domain.config import get_settings
from packages.domain.logging import logger


class LLMError(Exception):
    """Base exception for LLM gateway failures."""
    pass


class LLMResponse(BaseModel):
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    latency_ms: int = 0
    parsed_json: Optional[Dict[str, Any]] = None


class LLMGateway:
    """Standard gateway for all LLM calls across the AI Freight Platform."""

    def __init__(
        self,
        default_model: Optional[str] = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        mock_mode: Optional[bool] = None,
    ):
        settings = get_settings()
        self.default_model = default_model or settings.DEFAULT_MODEL
        self.reasoning_model = settings.REASONING_MODEL
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        # If explicit mock_mode not passed, auto-enable mock if no API key is present
        has_api_key = bool(
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or settings.OPENAI_API_KEY
            or settings.GEMINI_API_KEY
        )
        
        # Inject to environment for litellm if configured in settings but missing in env
        if settings.GEMINI_API_KEY and not os.environ.get("GEMINI_API_KEY"):
            os.environ["GEMINI_API_KEY"] = settings.GEMINI_API_KEY
            
        self.mock_mode = mock_mode if mock_mode is not None else (not has_api_key)

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        response_format: Optional[Dict[str, Any]] = None,
        mock_response_content: Optional[str] = None,
    ) -> LLMResponse:
        """Execute chat completion with retry, backoff, and structured timeout."""
        selected_model = model or self.default_model
        start_time = time.time()

        if self.mock_mode:
            # Deterministic mock response for testing or offline environments
            content = mock_response_content or self._generate_default_mock(messages)
            latency = int((time.time() - start_time) * 1000)
            parsed = None
            try:
                parsed = json.loads(content)
            except Exception:
                pass
            return LLMResponse(
                content=content,
                model=f"mock-{selected_model}",
                prompt_tokens=50,
                completion_tokens=25,
                total_tokens=75,
                cost_estimate=0.0001,
                latency_ms=latency,
                parsed_json=parsed,
            )

        # Real LiteLLM execution with retries and timeout
        import litellm

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    litellm.acompletion(
                        model=selected_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                    ),
                    timeout=self.timeout_seconds,
                )
                latency = int((time.time() - start_time) * 1000)
                content = response.choices[0].message.content or ""
                usage = getattr(response, "usage", None)
                p_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                c_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                t_tokens = getattr(usage, "total_tokens", 0) if usage else 0
                cost = 0.0
                try:
                    cost = litellm.completion_cost(completion_response=response) or 0.0
                except Exception:
                    cost = 0.000001 * t_tokens

                parsed = None
                try:
                    parsed = json.loads(content)
                except Exception:
                    pass

                return LLMResponse(
                    content=content,
                    model=selected_model,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=t_tokens,
                    cost_estimate=cost,
                    latency_ms=latency,
                    parsed_json=parsed,
                )
            except asyncio.TimeoutError:
                last_err = LLMError(f"LLM call timed out after {self.timeout_seconds}s (attempt {attempt}/{self.max_retries})")
                logger.warning(str(last_err))
            except Exception as e:
                last_err = LLMError(f"LLM completion error on attempt {attempt}/{self.max_retries}: {str(e)}")
                logger.warning(str(last_err))

            if attempt < self.max_retries:
                sleep_time = (self.backoff_factor ** attempt) * 0.5
                await asyncio.sleep(sleep_time)

        raise last_err or LLMError("LLM call failed after all retries")

    def _generate_default_mock(self, messages: List[Dict[str, str]]) -> str:
        """Generate a basic JSON or text structure for mock environments."""
        last_msg = messages[-1]["content"] if messages else ""
        if "classify_intent" in last_msg or "intent" in last_msg.lower():
            return json.dumps({
                "intent": "general_inquiry",
                "confidence": 0.8,
                "reasoning": "Mock classification result",
            })
        if "resolve_shipment" in last_msg or "resolver" in last_msg.lower():
            return json.dumps({
                "shipment_id": None,
                "confidence": 0.0,
                "reasoning": "No candidate found in text",
            })
        return json.dumps({"status": "success", "message": "Mock completion response"})
