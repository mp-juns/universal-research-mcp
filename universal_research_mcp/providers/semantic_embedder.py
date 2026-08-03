"""Budget-tracked semantic embedder backed by an approved provider router."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING
import threading

from .contracts import (
    BudgetExceeded,
    EmbeddingRequest,
    EmbeddingResult,
    RemotePolicy,
)
from .routing import ProviderRouter


class RoutedSemanticEmbedder:
    """Track aggregate remote calls/tokens/cost and never retry a batch."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        remote_policy: RemotePolicy,
        provider_id: str,
        cost_per_million_tokens_usd: str | float,
    ) -> None:
        if remote_policy.budget is None:
            raise ValueError("remote semantic embedding requires an explicit budget")
        try:
            price = Decimal(str(cost_per_million_tokens_usd))
        except InvalidOperation as exc:
            raise ValueError("embedding token price must be numeric") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError("embedding token price must be positive and finite")
        self.router = router
        self.remote_policy = remote_policy
        self.provider_id = provider_id
        self.price = price
        self._calls = 0
        self._input_tokens = 0
        self._estimated_cost_micros = 0
        self._lock = threading.Lock()

    @staticmethod
    def _estimate_tokens(texts: tuple[str, ...]) -> int:
        # Conservative tokenizer-independent estimate. The declared budget is
        # still an upper bound, not an exact bill.
        return max(1, sum((len(text.encode("utf-8")) + 2) // 3 for text in texts))

    def _estimate_cost_micros(self, tokens: int) -> int:
        # USD per million tokens × tokens × 1e6 micro-USD/USD ÷ 1e6.
        return int((self.price * Decimal(tokens)).to_integral_value(rounding=ROUND_CEILING))

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model: str,
        dimensions: int | None,
    ) -> EmbeddingResult:
        tokens = self._estimate_tokens(texts)
        cost_micros = self._estimate_cost_micros(tokens)
        budget = self.remote_policy.budget
        assert budget is not None
        with self._lock:
            next_calls = self._calls + 1
            next_tokens = self._input_tokens + tokens
            next_cost = self._estimated_cost_micros + cost_micros
            exceeded = []
            if next_calls > budget.max_calls:
                exceeded.append("calls")
            if next_tokens > budget.max_input_tokens:
                exceeded.append("input tokens")
            if next_cost > budget.max_estimated_cost_micros:
                exceeded.append("estimated cost")
            if exceeded:
                raise BudgetExceeded(
                    "semantic refresh exceeds its aggregate remote budget",
                    details={"exceeded": exceeded},
                )
            # Reserve before issuing the request. An ambiguous timeout still
            # consumes the reservation and is never retried automatically.
            self._calls = next_calls
            self._input_tokens = next_tokens
            self._estimated_cost_micros = next_cost
            request_id = f"semantic-{next_calls}"
        request = EmbeddingRequest(
            request_id=request_id,
            model=model,
            texts=texts,
            dimensions=dimensions,
            estimated_input_tokens=tokens,
            estimated_cost_micros=cost_micros,
        )
        routed = self.router.execute(request, remote_policy=self.remote_policy)
        if not routed.remote or routed.provider_id != self.provider_id:
            raise RuntimeError("semantic request did not use the approved remote provider")
        if not isinstance(routed.result, EmbeddingResult):
            raise RuntimeError("semantic provider returned a non-embedding result")
        return routed.result

    def usage_snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self._calls,
                "estimated_input_tokens": self._input_tokens,
                "estimated_cost_micros": self._estimated_cost_micros,
            }
