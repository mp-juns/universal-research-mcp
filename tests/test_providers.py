from __future__ import annotations

import json
import unittest

from universal_research_mcp.providers import (
    AnthropicProvider,
    Availability,
    BudgetExceeded,
    Capability,
    CapabilityUnavailable,
    CredentialRef,
    CredentialResolver,
    EmbeddingRequest,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    HttpResponse,
    HttpTransportError,
    LocalProvider,
    LocalSentenceTransformerEmbedder,
    Message,
    OpenAIProvider,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderRouter,
    RemoteBudget,
    RemoteOptInRequired,
    RemotePolicy,
    RoutedSemanticEmbedder,
    SecretValue,
    UrllibTransport,
    provider_status,
)
from universal_research_mcp.providers.redaction import REDACTED, sanitize


class FakeTransport:
    def __init__(self, response: HttpResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def request(self, **kwargs) -> HttpResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake transport has no configured response")
        return self.response


class FakeUrlResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.closed = False
        self.read_limits: list[int] = []

    def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        return self.body[:limit]

    def close(self) -> None:
        self.closed = True


def generation_request(**overrides) -> GenerationRequest:
    values = {
        "request_id": "req-generation",
        "model": "test-generation-model",
        "messages": (Message("user", "Summarize the bounded evidence."),),
        "max_output_tokens": 64,
        "estimated_input_tokens": 20,
        "estimated_cost_micros": 100,
    }
    values.update(overrides)
    return GenerationRequest(**values)


def embedding_request(**overrides) -> EmbeddingRequest:
    values = {
        "request_id": "req-embedding",
        "model": "test-embedding-model",
        "texts": ("first passage", "second passage"),
        "dimensions": 3,
        "estimated_input_tokens": 12,
        "estimated_cost_micros": 50,
    }
    values.update(overrides)
    return EmbeddingRequest(**values)


def generous_policy(*provider_ids: str) -> RemotePolicy:
    return RemotePolicy(
        approved=True,
        allowed_provider_ids=frozenset(provider_ids),
        budget=RemoteBudget(
            max_calls=1,
            max_input_tokens=10_000,
            max_output_tokens=10_000,
            max_estimated_cost_micros=1_000_000,
        ),
    )


class CredentialAndRedactionTests(unittest.TestCase):
    def test_credential_references_accept_only_env_or_keyring(self) -> None:
        self.assertEqual(str(CredentialRef.parse("env:OPENAI_API_KEY")), "env:OPENAI_API_KEY")
        self.assertEqual(
            str(CredentialRef.parse("keyring:universal-research/openai")),
            "keyring:universal-research/openai",
        )
        for unsafe in (
            "raw:sk-live-secret",
            "plaintext:sk-live-secret",
            "argv:sk-live-secret",
            "chat:sk-live-secret",
            "sk-live-secret",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ProviderConfigurationError):
                    CredentialRef.parse(unsafe)

    def test_env_and_injected_keyring_resolution_never_render_secret(self) -> None:
        env_secret = "sk-env-super-secret"
        resolver = CredentialResolver(environ={"OPENAI_API_KEY": env_secret})
        resolved = resolver.resolve("env:OPENAI_API_KEY")
        self.assertEqual(resolved.reveal(), env_secret)
        self.assertNotIn(env_secret, str(resolved))
        self.assertNotIn(env_secret, repr(resolved))

        keyring_secret = "sk-keyring-super-secret"
        keyring_resolver = CredentialResolver(
            environ={},
            keyring_getter=lambda service, account: (
                keyring_secret if (service, account) == ("research", "openai") else None
            ),
        )
        self.assertEqual(
            keyring_resolver.resolve("keyring:research/openai").reveal(),
            keyring_secret,
        )

    def test_structured_redaction_removes_headers_keys_and_exact_values(self) -> None:
        secret = "sk-redact-me"
        value = {
            "Authorization": f"Bearer {secret}",
            "nested": [f"x-api-key: {secret}", {"token": secret}],
            "message": f"transport exposed {secret}",
        }
        rendered = json.dumps(sanitize(value, (secret,)), sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertIn(REDACTED, rendered)

    def test_transport_exception_is_sanitized(self) -> None:
        secret = "sk-transport-secret"
        transport = FakeTransport(error=RuntimeError(f"Authorization: Bearer {secret}"))
        provider = OpenAIProvider(
            transport=transport,
            credential_ref="env:OPENAI_API_KEY",
        )
        with self.assertRaises(ProviderRequestError) as caught:
            provider.invoke(generation_request(), SecretValue(secret))
        rendered = f"{caught.exception} {json.dumps(caught.exception.details, sort_keys=True)}"
        self.assertNotIn(secret, rendered)
        self.assertIn(REDACTED, rendered)

    def test_provider_status_hides_credential_locator(self) -> None:
        provider = OpenAIProvider(
            transport=FakeTransport(),
            credential_ref="env:OPENAI_API_KEY",
        )
        status = provider_status(provider)
        rendered = json.dumps(status, sort_keys=True)
        self.assertNotIn("OPENAI_API_KEY", rendered)
        self.assertEqual(status["credential"]["kind"], "env")
        self.assertEqual(status["credential"]["value"], REDACTED)


class RestAdapterTests(unittest.TestCase):
    def test_openai_embeddings_are_counted_ordered_and_dimension_checked(self) -> None:
        transport = FakeTransport(
            HttpResponse(
                200,
                {
                    "model": "resolved-embedding-model",
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                        {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    ],
                    "usage": {"prompt_tokens": 12, "total_tokens": 12},
                },
            )
        )
        provider = OpenAIProvider(transport=transport, credential_ref="env:OPENAI_API_KEY")
        result = provider.invoke(embedding_request(), SecretValue("sk-openai"))
        self.assertIsInstance(result, EmbeddingResult)
        self.assertEqual(result.vectors[0], (1.0, 0.0, 0.0))
        self.assertEqual(result.vectors[1], (0.0, 1.0, 0.0))
        self.assertEqual(result.usage.total_tokens, 12)
        self.assertEqual(transport.calls[0]["url"], "https://api.openai.com/v1/embeddings")

    def test_openai_generation_uses_injected_transport(self) -> None:
        transport = FakeTransport(
            HttpResponse(
                200,
                {
                    "model": "resolved-generation-model",
                    "choices": [{"message": {"content": "bounded answer"}}],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 3,
                        "total_tokens": 23,
                    },
                },
            )
        )
        provider = OpenAIProvider(transport=transport, credential_ref="env:OPENAI_API_KEY")
        result = provider.invoke(generation_request(), SecretValue("sk-openai"))
        self.assertIsInstance(result, GenerationResult)
        self.assertEqual(result.text, "bounded answer")
        self.assertEqual(result.usage.total_tokens, 23)
        self.assertEqual(transport.calls[0]["url"], "https://api.openai.com/v1/chat/completions")

    def test_anthropic_generation_only_rejects_embedding_before_transport(self) -> None:
        transport = FakeTransport()
        provider = AnthropicProvider(
            transport=transport,
            credential_ref="env:ANTHROPIC_API_KEY",
        )
        self.assertFalse(provider.preflight(Capability.EMBEDDING).available)
        with self.assertRaises(CapabilityUnavailable):
            provider.invoke(embedding_request(), SecretValue("sk-anthropic"))
        self.assertEqual(transport.calls, [])

    def test_anthropic_generation_uses_injected_transport(self) -> None:
        transport = FakeTransport(
            HttpResponse(
                200,
                {
                    "model": "resolved-anthropic-model",
                    "content": [{"type": "text", "text": "reviewed answer"}],
                    "usage": {"input_tokens": 20, "output_tokens": 2},
                },
            )
        )
        provider = AnthropicProvider(
            transport=transport,
            credential_ref="env:ANTHROPIC_API_KEY",
        )
        result = provider.invoke(generation_request(), SecretValue("sk-anthropic"))
        self.assertIsInstance(result, GenerationResult)
        self.assertEqual(result.text, "reviewed answer")
        self.assertEqual(result.usage.total_tokens, 22)
        self.assertEqual(transport.calls[0]["url"], "https://api.anthropic.com/v1/messages")

    def test_endpoint_allowlist_rejects_http_credentials_and_unknown_hosts(self) -> None:
        for endpoint in (
            "http://api.openai.com/v1",
            "https://evil.example/v1",
            "https://user:secret@api.openai.com/v1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ProviderConfigurationError):
                    OpenAIProvider(
                        transport=FakeTransport(),
                        credential_ref="env:OPENAI_API_KEY",
                        base_url=endpoint,
                    )


class UrllibTransportTests(unittest.TestCase):
    def test_injected_opener_performs_one_bounded_https_json_request(self) -> None:
        calls: list[tuple] = []
        response = FakeUrlResponse(
            b'{"ok":true}',
            headers={"Content-Type": "application/json"},
        )

        def opener(request, *, timeout):
            calls.append((request, timeout))
            return response

        transport = UrllibTransport(opener=opener, max_response_bytes=64)
        result = transport.request(
            method="POST",
            url="https://api.openai.com/v1/embeddings",
            headers={"Authorization": "Bearer opaque"},
            json_body={"input": ["bounded"]},
            timeout_seconds=3.5,
        )

        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(timeout, 3.5)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"input": ["bounded"]})
        self.assertEqual(result.json_body, {"ok": True})
        self.assertEqual(response.read_limits, [65])
        self.assertTrue(response.closed)

    def test_transport_rejects_non_https_before_calling_opener(self) -> None:
        calls: list[object] = []
        transport = UrllibTransport(opener=lambda *args, **kwargs: calls.append(args))
        with self.assertRaisesRegex(HttpTransportError, "HTTPS"):
            transport.request(
                method="POST",
                url="http://api.openai.com/v1/embeddings",
                headers={},
                json_body={"input": ["never sent"]},
                timeout_seconds=1,
            )
        self.assertEqual(calls, [])

    def test_oversize_response_is_terminal_and_does_not_leak_body(self) -> None:
        calls: list[int] = []
        secret = "sk-response-secret"
        response = FakeUrlResponse((secret * 10).encode())

        def opener(_request, *, timeout):
            calls.append(int(timeout))
            return response

        transport = UrllibTransport(opener=opener, max_response_bytes=16)
        with self.assertRaises(HttpTransportError) as caught:
            transport.request(
                method="POST",
                url="https://api.openai.com/v1/embeddings",
                headers={},
                json_body={"input": ["bounded"]},
                timeout_seconds=2,
            )
        self.assertEqual(calls, [2])
        self.assertNotIn(secret, str(caught.exception))
        self.assertTrue(response.closed)

    def test_opener_failure_is_sanitized_and_not_retried(self) -> None:
        calls: list[int] = []
        secret = "sk-opener-secret"

        def opener(_request, *, timeout):
            calls.append(int(timeout))
            raise RuntimeError(f"failed with Bearer {secret}")

        transport = UrllibTransport(opener=opener)
        with self.assertRaises(HttpTransportError) as caught:
            transport.request(
                method="POST",
                url="https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {secret}"},
                json_body={"input": ["bounded"]},
                timeout_seconds=2,
            )
        self.assertEqual(calls, [2])
        self.assertNotIn(secret, str(caught.exception))


class ProviderRouterTests(unittest.TestCase):
    def test_local_provider_wins_even_when_remote_is_approved(self) -> None:
        local_calls: list[str] = []

        def local_handler(request):
            local_calls.append(request.request_id)
            return GenerationResult(request.request_id, "local", request.model, "local answer")

        local = LocalProvider({Capability.GENERATION: local_handler})
        remote_transport = FakeTransport(
            HttpResponse(200, {"choices": [{"message": {"content": "remote"}}]})
        )
        remote = OpenAIProvider(
            transport=remote_transport,
            credential_ref="env:OPENAI_API_KEY",
        )
        router = ProviderRouter(
            local=local,
            remotes=(remote,),
            credentials=CredentialResolver(environ={"OPENAI_API_KEY": "sk-openai"}),
        )
        request = generation_request()
        self.assertEqual(router.preflight(request, remote_policy=generous_policy("openai"))["route"], "local")
        routed = router.execute(request, remote_policy=generous_policy("openai"))
        self.assertFalse(routed.remote)
        self.assertEqual(local_calls, [request.request_id])
        self.assertEqual(remote_transport.calls, [])

    def test_remote_requires_explicit_opt_in_and_budget(self) -> None:
        request = generation_request()
        remote = OpenAIProvider(
            transport=FakeTransport(),
            credential_ref="env:OPENAI_API_KEY",
        )
        router = ProviderRouter(local=None, remotes=(remote,))
        status = router.preflight(request)
        self.assertFalse(status["executable"])
        self.assertEqual(status["reason"], "remote_opt_in_required")
        with self.assertRaises(RemoteOptInRequired):
            router.execute(request)
        with self.assertRaises(RemoteOptInRequired):
            router.execute(
                request,
                remote_policy=RemotePolicy(True, frozenset({"openai"}), None),
            )

    def test_budget_is_checked_before_credential_or_transport(self) -> None:
        transport = FakeTransport()
        remote = OpenAIProvider(transport=transport, credential_ref="env:OPENAI_API_KEY")
        router = ProviderRouter(local=None, remotes=(remote,), credentials=CredentialResolver(environ={}))
        policy = RemotePolicy(
            approved=True,
            allowed_provider_ids=frozenset({"openai"}),
            budget=RemoteBudget(1, 1, 1, 1),
        )
        status = router.preflight(generation_request(), remote_policy=policy)
        self.assertFalse(status["executable"])
        self.assertEqual(status["reason"], BudgetExceeded.code)
        with self.assertRaises(BudgetExceeded):
            router.execute(generation_request(), remote_policy=policy)
        self.assertEqual(transport.calls, [])

    def test_local_capability_unavailable_is_visible_in_preflight(self) -> None:
        local = LocalProvider(
            {Capability.GENERATION: lambda request: GenerationResult(request.request_id, "local", request.model, "unused")},
            availability={Capability.GENERATION: Availability.unavailable("local model is not cached")},
        )
        router = ProviderRouter(local=local)
        status = router.preflight(generation_request())
        self.assertFalse(status["executable"])
        self.assertEqual(status["local"]["reason"], "local model is not cached")
        with self.assertRaises(RemoteOptInRequired) as caught:
            router.execute(generation_request())
        self.assertEqual(caught.exception.details["local_reason"], "local model is not cached")

    def test_remote_failure_is_terminal_and_does_not_try_second_provider(self) -> None:
        openai_secret = "sk-openai-no-leak"
        first_transport = FakeTransport(error=TimeoutError(f"Bearer {openai_secret}"))
        second_transport = FakeTransport(
            HttpResponse(200, {"content": [{"type": "text", "text": "must not run"}]})
        )
        first = OpenAIProvider(
            transport=first_transport,
            credential_ref="env:OPENAI_API_KEY",
        )
        second = AnthropicProvider(
            transport=second_transport,
            credential_ref="env:ANTHROPIC_API_KEY",
        )
        router = ProviderRouter(
            local=None,
            remotes=(first, second),
            credentials=CredentialResolver(
                environ={
                    "OPENAI_API_KEY": openai_secret,
                    "ANTHROPIC_API_KEY": "sk-anthropic",
                }
            ),
        )
        with self.assertRaises(ProviderRequestError) as caught:
            router.execute(
                generation_request(),
                remote_policy=generous_policy("openai", "anthropic"),
            )
        self.assertEqual(len(first_transport.calls), 1)
        self.assertEqual(second_transport.calls, [])
        self.assertNotIn(openai_secret, json.dumps(caught.exception.details, sort_keys=True))

    def test_router_status_is_redacted(self) -> None:
        remote = OpenAIProvider(
            transport=FakeTransport(),
            credential_ref="keyring:research/openai",
        )
        rendered = json.dumps(ProviderRouter(local=None, remotes=(remote,)).provider_status(), sort_keys=True)
        self.assertNotIn("research/openai", rendered)
        self.assertIn(REDACTED, rendered)

    def test_routed_semantic_embedder_enforces_aggregate_budget_without_retry(self) -> None:
        transport = FakeTransport(
            HttpResponse(200, {
                "model": "text-embedding-fixture",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
            })
        )
        remote = OpenAIProvider(
            transport=transport, credential_ref="env:OPENAI_API_KEY",
        )
        router = ProviderRouter(
            local=None,
            remotes=(remote,),
            credentials=CredentialResolver(environ={"OPENAI_API_KEY": "sk-fixture"}),
        )
        policy = RemotePolicy(
            approved=True,
            allowed_provider_ids=frozenset({"openai"}),
            budget=RemoteBudget(1, 1000, 0, 1000),
        )
        embedder = RoutedSemanticEmbedder(
            router=router,
            remote_policy=policy,
            provider_id="openai",
            cost_per_million_tokens_usd="0.02",
        )
        first = embedder.embed(
            ("bounded text",), model="text-embedding-fixture", dimensions=3,
        )
        self.assertEqual(first.vectors[0], (1.0, 0.0, 0.0))
        with self.assertRaises(BudgetExceeded):
            embedder.embed(
                ("must not run",), model="text-embedding-fixture", dimensions=3,
            )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(embedder.usage_snapshot()["calls"], 1)

    def test_local_embedding_preflight_never_downloads_a_missing_snapshot(self) -> None:
        embedder = LocalSentenceTransformerEmbedder("/definitely/missing/model")
        status = embedder.preflight()
        self.assertFalse(status.available)
        self.assertIn("does not exist", status.reason)


if __name__ == "__main__":
    unittest.main()
