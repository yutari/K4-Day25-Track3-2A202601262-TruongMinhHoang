from __future__ import annotations

import time
from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None
    route_reason: str = ""
    estimated_cost_saved: float = 0.0


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback.

        TODO(student): Implement the full request routing pipeline:

        1. CACHE CHECK — if self.cache is not None:
           - Call self.cache.get(prompt) → (cached_text, score)
           - If cached_text is not None, return GatewayResponse with:
             route=f"cache_hit:{score:.2f}", cache_hit=True, latency=0, cost=0

        2. PROVIDER FALLBACK CHAIN — iterate self.providers in order:
           - Get the circuit breaker: self.breakers[provider.name]
           - Try breaker.call(provider.complete, prompt)
           - On success:
             a. Store in cache: self.cache.set(prompt, response.text, {"provider": provider.name})
             b. Determine route: "primary" if first provider, else "fallback"
             c. Return GatewayResponse with provider info, latency, cost
           - On ProviderError or CircuitOpenError: save error, continue to next provider

        3. STATIC FALLBACK — if all providers fail:
           - Return GatewayResponse with:
             text="The service is temporarily degraded. Please try again soon."
             route="static_fallback", error=last_error

        BONUS TODO: Add cost budget tracking — if cumulative cost exceeds a threshold,
        skip expensive providers and route to cache or cheaper fallback.
        """
        started_at = time.perf_counter()
        if self.cache is not None:
            cached_text, score, metadata = self.cache.get_with_metadata(prompt)
            if cached_text is not None:
                try:
                    cost_saved = float(metadata.get("estimated_cost", "0"))
                except ValueError:
                    cost_saved = 0.0
                source_provider = metadata.get("provider", "unknown")
                return GatewayResponse(
                    text=cached_text,
                    route=f"cache_hit:{score:.2f}",
                    provider=None,
                    cache_hit=True,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                    estimated_cost=0.0,
                    route_reason=(
                        f"semantic_cache_hit:score={score:.4f};source_provider={source_provider}"
                    ),
                    estimated_cost_saved=cost_saved,
                )

        last_error: str | None = None
        route_events: list[str] = []
        for index, provider in enumerate(self.providers):
            breaker = self.breakers.get(provider.name)
            if breaker is None:
                last_error = f"No circuit breaker configured for provider '{provider.name}'"
                route_events.append(f"{provider.name}:missing_breaker")
                continue

            try:
                response: ProviderResponse = breaker.call(provider.complete, prompt)
            except (ProviderError, CircuitOpenError) as error:
                last_error = str(error)
                event = "circuit_open" if isinstance(error, CircuitOpenError) else "provider_failure"
                route_events.append(f"{provider.name}:{event}")
                continue

            if self.cache is not None:
                self.cache.set(
                    prompt,
                    response.text,
                    {
                        "provider": provider.name,
                        "estimated_cost": str(response.estimated_cost),
                    },
                )

            route_events.append(f"{provider.name}:success")

            return GatewayResponse(
                text=response.text,
                route="primary" if index == 0 else "fallback",
                provider=provider.name,
                cache_hit=False,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                estimated_cost=response.estimated_cost,
                route_reason=";".join(route_events),
            )

        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            provider=None,
            cache_hit=False,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            estimated_cost=0.0,
            error=last_error,
            route_reason=";".join(route_events) or "no_providers_configured",
        )
