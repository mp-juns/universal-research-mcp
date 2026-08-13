"""Local-first, explicitly authorized inference-provider foundation."""

from .anthropic import AnthropicProvider
from .contracts import (
    Availability,
    BudgetExceeded,
    Capability,
    CapabilityUnavailable,
    EmbeddingRequest,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    Message,
    ProviderConfigurationError,
    ProviderError,
    ProviderRequestError,
    RemoteBudget,
    RemoteOptInRequired,
    RemotePolicy,
)
from .credentials import CredentialRef, CredentialResolver, SecretValue
from .http import HttpResponse, HttpTransport, HttpTransportError, UrllibTransport
from .local import LocalProvider
from .local_embedding import LocalSentenceTransformerEmbedder
from .deterministic_embedding import SignedHashingEmbedder, encode_signed_hashing
from .loopback import (
    LOOPBACK_PROVIDER_ID,
    LoopbackJsonTransport,
    OpenAICompatibleLoopbackProvider,
    validate_loopback_endpoint,
)
from .openai import OpenAIProvider
from .routing import ProviderRouter, RoutedResult, provider_status
from .semantic_embedder import RoutedSemanticEmbedder

__all__ = [
    "AnthropicProvider",
    "Availability",
    "BudgetExceeded",
    "Capability",
    "CapabilityUnavailable",
    "CredentialRef",
    "CredentialResolver",
    "EmbeddingRequest",
    "EmbeddingResult",
    "GenerationRequest",
    "GenerationResult",
    "HttpResponse",
    "HttpTransport",
    "HttpTransportError",
    "LocalProvider",
    "LocalSentenceTransformerEmbedder",
    "SignedHashingEmbedder",
    "LOOPBACK_PROVIDER_ID",
    "LoopbackJsonTransport",
    "Message",
    "OpenAIProvider",
    "OpenAICompatibleLoopbackProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderRouter",
    "provider_status",
    "RemoteBudget",
    "RemoteOptInRequired",
    "RemotePolicy",
    "RoutedResult",
    "RoutedSemanticEmbedder",
    "SecretValue",
    "UrllibTransport",
    "encode_signed_hashing",
    "validate_loopback_endpoint",
]
