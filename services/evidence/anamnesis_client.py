"""
Typed HTTP client for the Anamnesis app at ``dellserver:3010``.

What is Anamnesis (in one sentence)?
====================================

Anamnesis is the household's episodic-memory and LLM-orchestration
service. It crawls files and chat logs into a vector-indexed store,
exposes semantic search over them, and provides a model-dispatch
layer that routes generation requests to whichever backend has the
GPU available (Ollama on office:RX 6800, the local AnamnesisGPT LoRA,
the d2 dialectical-optimization model, or — if configured — the
Anthropic Claude API).

Why a dedicated client class for it?
====================================

Half a dozen evidence services will eventually need to talk to
Anamnesis (the weekly summarizer, the Whisper transcriber that
ingests transcripts, future people-recognition that cross-checks
episodic memory, etc.). Each of them re-implementing ``requests.post(
"http://<LAN_IP>:3010/api/...")`` calls would be:

  * **Fragile** — the host/port might change; we'd have to hunt down
    every usage to update it.
  * **Inconsistent** — different services would pick different
    timeouts, retry policies, error-handling shapes.
  * **Hard to mock** — tests would have to monkey-patch ``requests``.

A small typed client class centralizes all of that. Future-you adds a
new endpoint method here ONCE and every service can use it.

Endpoints currently wrapped
===========================

  * ``search_episodes(...)``   — semantic search over the episode store
  * ``ingest_episode(...)``    — write a new episode
  * ``list_chat_models(...)``  — discover available models for chat/stream
  * ``chat_stream(...)``       — generate text with a chosen model+backend,
                                 streaming the response chunk by chunk

Endpoints not yet wrapped (add as needed)
=========================================

  * crawler config/status
  * jsonl ingestion config
  * file-source listing (``/api/files/...``)
  * dashboard stats

Concurrency model
=================

This client is thread-safe in the sense that ``requests.Session`` is
thread-safe, and we don't store per-request state on the instance.
A single ``AnamnesisClient`` instance can be shared across all
evidence services running in the same process.

Streaming responses (``chat_stream``) yield chunks as they arrive
from the server. Callers iterate the generator. The underlying HTTP
connection is released when the generator is exhausted or closed.
"""

# ----- standard library --------------------------------------------------
import json                                     # parsing SSE/NDJSON chunks
import logging                                  # diagnostic logging
import os                                       # env-var overrides
from typing import Any, Dict, Generator, List, Optional   # type hints

# ----- third party -------------------------------------------------------
import requests                                 # HTTP transport

logger = logging.getLogger(__name__)


# ----- module-level configuration ----------------------------------------

# Default Anamnesis app URL.
#
# Resolution priority:
#   1. ``ANAMNESIS_URL`` env var — explicit override; used by tests and
#      alternate deployments where the user runs Anamnesis locally or
#      on a different host.
#   2. ``http://anamnesis-app:3010`` — the in-container default. Works
#      when unified-nvr is attached to the anamnesis-net external
#      docker network (see docker-compose.yml). Resolves via docker
#      DNS to the anamnesis-app container.
#
# The host-network form ``http://<LAN_IP>:3010`` is NOT the
# default because it only works from the host or from a container
# with host networking — and unified-nvr does not use host networking.
# Set ``ANAMNESIS_URL=http://<LAN_IP>:3010`` explicitly when
# running the client outside the container.
DEFAULT_ANAMNESIS_URL: str = os.environ.get(
    "ANAMNESIS_URL",
    "http://anamnesis-app:3010",
)

# Default timeouts for non-streaming endpoints. Generous because some
# Anamnesis operations (semantic search over a large corpus, ingest with
# embedding generation) can legitimately take a few seconds.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Default model + backend for chat generation. ``qwen2.5:14b`` was
# confirmed by the user 2026-04-28 to run on the GPU at office (proxied
# through Anamnesis), and is the right size for narrative summarization
# tasks. Override per-call if a different one is wanted.
DEFAULT_GENERATION_MODEL: str = "qwen2.5:14b"
DEFAULT_GENERATION_BACKEND: str = "ollama"


# =========================================================================
# AnamnesisClient — the one place HTTP knowledge of Anamnesis lives
# =========================================================================

class AnamnesisClient:
    """
    HTTP client for the Anamnesis app's REST API.

    Construct once, share across all services in the same process::

        client = AnamnesisClient()
        episodes = client.search_episodes("kitchen scream", top_k=5)
        for chunk in client.chat_stream(message="Summarize this week..."):
            print(chunk, end="", flush=True)

    Methods come in two flavors:

      * **Synchronous** (``search_episodes``, ``ingest_episode``,
        ``list_chat_models``) — block until the response arrives; return
        a parsed dict/list. Raises ``requests.HTTPError`` on non-2xx.
      * **Streaming** (``chat_stream``) — return a generator that yields
        text chunks as they arrive. Useful for showing progress and
        for not buffering 14B-parameter outputs in memory.

    All methods take a ``timeout`` override for cases where the default
    is too short (e.g. cold-loading a model into GPU memory).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: Optional[requests.Session] = None,
    ) -> None:
        """
        Parameters
        ----------
        base_url:
            Full ``http(s)://host:port`` of the Anamnesis app. Defaults
            to ``DEFAULT_ANAMNESIS_URL`` (which itself respects the
            ``ANAMNESIS_URL`` env var).
        default_timeout:
            Seconds before non-streaming requests give up. Streaming
            requests don't use this — they use a longer per-chunk
            read timeout (see ``chat_stream``).
        session:
            Inject a pre-built ``requests.Session`` for testing or
            connection-pool sharing. Defaults to a fresh session.
        """
        # Strip any trailing slash so endpoint joining is unambiguous.
        # ``urljoin`` has surprising semantics; manual concatenation is
        # less elegant but predictable.
        self.base_url: str = (base_url or DEFAULT_ANAMNESIS_URL).rstrip("/")
        self.default_timeout: float = default_timeout
        # ``Session`` reuses TCP connections, which matters when several
        # services hit Anamnesis in close succession. Cheap insurance.
        self._session: requests.Session = session or requests.Session()

    # -----------------------------------------------------------------
    # Episodes
    # -----------------------------------------------------------------

    def search_episodes(
        self,
        query_text: str,
        top_k: int = 5,
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over the episode store.

        Returns the top ``top_k`` episodes (as dicts) most similar to
        ``query_text`` according to Anamnesis's embedding model.

        Used by:
          * weekly_summary — pull the week's relevant episodes for ctx
          * (future) Whisper transcriber — find related prior utterances
          * (future) people_recognition — cross-reference visual events
            with named episodes

        The structure of each returned dict is whatever Anamnesis's
        ``/api/episodes/search`` returns; at the time of writing that
        includes ``episode_id``, ``instance``, ``project``, ``summary``,
        ``raw_exchange``, plus retrieval-time metadata. Treat all fields
        as optional for forward compat.
        """
        url = f"{self.base_url}/api/episodes/search"
        body = {"query_text": query_text, "top_k": int(top_k)}
        r = self._session.post(
            url,
            json=body,
            timeout=timeout or self.default_timeout,
        )
        r.raise_for_status()
        # The endpoint returns a JSON list directly (not wrapped in an
        # outer envelope). If that ever changes, this is the line to
        # adapt.
        return r.json()

    def recent_episodes(
        self,
        days: int,
        project: Optional[str] = None,
        instance: Optional[str] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return episodes ingested into Anamnesis within the last
        ``days`` calendar days, optionally filtered by project /
        instance / tag.

        Used by the weekly summarizer to pull the week's episodic
        signal in one call. Setting ``days=7`` and the project name
        yields exactly the per-project context we want.

        Note: the server filters by ingest time, not by event time. For
        ChatGPT-JSONL imports of older conversations this distinction
        matters — a conversation from 3 months ago that was ingested
        yesterday WILL show up here. Treat the filter as "what did
        Anamnesis learn about recently" rather than "what happened
        recently". Usually that's fine for a weekly project summary.
        """
        url = f"{self.base_url}/api/episodes/recent"
        params: Dict[str, Any] = {"days": int(days)}
        if project is not None:
            params["project"] = project
        if instance is not None:
            params["instance"] = instance
        if tag is not None:
            params["tag"] = tag
        if limit is not None:
            params["limit"] = int(limit)
        r = self._session.get(
            url,
            params=params,
            timeout=timeout or self.default_timeout,
        )
        r.raise_for_status()
        return r.json()

    def ingest_episode(
        self,
        episode: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Write a new episode into Anamnesis.

        ``episode`` should match Anamnesis's ingest schema (instance,
        project, summary, raw_exchange, etc.). Refer to the OpenAPI at
        ``/openapi.json`` for the current exact shape.

        Returns the created episode (with assigned ID, timestamp, etc.).
        """
        url = f"{self.base_url}/api/episodes"
        r = self._session.post(
            url,
            json=episode,
            timeout=timeout or self.default_timeout,
        )
        r.raise_for_status()
        return r.json()

    # -----------------------------------------------------------------
    # Chat / generation
    # -----------------------------------------------------------------

    def list_chat_models(
        self,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return the set of models currently available via Anamnesis's
        chat backend.

        Useful when a service wants to confirm a model is reachable
        before sending a (potentially long) generation request, or to
        dynamically pick a smaller model on a busy GPU.

        Response shape (as of 2026-04-28)::

            {
              "models": ["mistral:7b", "qwen2.5:14b", ...],
              "default": "llama3.2",
              "endpoint": "office (GPU)"
            }
        """
        url = f"{self.base_url}/api/chat/models"
        r = self._session.get(url, timeout=timeout or self.default_timeout)
        r.raise_for_status()
        return r.json()

    def chat_stream(
        self,
        message: str,
        model: str = DEFAULT_GENERATION_MODEL,
        backend: str = DEFAULT_GENERATION_BACKEND,
        top_k: int = 0,
        session_id: Optional[str] = None,
        per_chunk_read_timeout: float = 120.0,
    ) -> Generator[str, None, None]:
        """
        Stream a generation response from Anamnesis chunk by chunk.

        Yields strings as they arrive — typically one or a few tokens
        per yield, depending on the backend's chunking. The generator
        is exhausted when the server closes the stream.

        Parameters
        ----------
        message:
            The user prompt. For weekly summary the full templated
            prompt (with embedded git log + episode context + manifest
            stats) is passed here.
        model:
            Model identifier as known to Anamnesis. Default is
            ``qwen2.5:14b`` which is GPU-backed at office.
        backend:
            Which Anamnesis backend to dispatch through. ``"ollama"``
            (default) routes to Ollama; ``"anamnesis_gpt"`` would route
            to the LoRA-fine-tuned Hegelian model (NOT useful for
            general summarization — left here for completeness).
        top_k:
            How many episodes Anamnesis should retrieve as RAG context
            before generating. Default ``0`` because the weekly summary
            builds its own context and doesn't want extra retrieval
            soup poured in. Set to ``5`` or so for chat-style use.
        session_id:
            Optional Anamnesis chat session for continuity. Leave
            ``None`` for one-shot generations like summaries.
        per_chunk_read_timeout:
            How long to wait between chunks before giving up. 14B-param
            models on GPU can pause for several seconds when handling
            heavy RAG context, so this is generous. Lower it if you
            want fast-fail behavior.

        Streaming protocol
        ------------------
        Anamnesis returns ``text/event-stream`` (SSE) format. Each event
        is a line of the form ``data: {"chunk": "..."}\\n\\n``. We parse
        and yield only the ``chunk`` text. End-of-stream is indicated
        by ``data: [DONE]``.

        If the response format changes, adapt ``_iter_sse_chunks``.
        """
        url = f"{self.base_url}/api/chat/stream"
        body: Dict[str, Any] = {
            "message": message,
            "backend": backend,
            "model": model,
            "top_k": int(top_k),
        }
        if session_id is not None:
            body["session_id"] = session_id

        # ``stream=True`` is critical: without it ``requests`` buffers
        # the entire response into memory before returning, defeating
        # the point of a streaming endpoint. With it, ``iter_lines`` /
        # ``iter_content`` give us the chunks as they arrive.
        with self._session.post(
            url,
            json=body,
            stream=True,
            # The "connect, read" tuple — connect should be fast; the
            # read timeout caps inter-chunk silence, not total runtime.
            timeout=(self.default_timeout, per_chunk_read_timeout),
        ) as response:
            response.raise_for_status()
            yield from self._iter_sse_chunks(response)

    # -----------------------------------------------------------------
    # SSE parsing — small but fiddly, kept private
    # -----------------------------------------------------------------

    @staticmethod
    def _iter_sse_chunks(
        response: requests.Response,
    ) -> Generator[str, None, None]:
        """
        Parse a Server-Sent-Events stream into text chunks.

        SSE is a simple line-based format:

            data: {"chunk": "Hello"}
            data: {"chunk": " world"}
            data: [DONE]

        Each event ends with a blank line. We accept two variants here:

          * JSON payloads with a ``chunk`` field (Anamnesis's format)
          * Plain text payloads (in case the format changes to OpenAI-
            style raw text); we yield the raw line minus the ``data: ``
            prefix.

        Lines that aren't ``data: `` events (comments, retry hints,
        keep-alive ``:`` lines) are silently skipped — that's standard
        SSE behavior.
        """
        for raw_line in response.iter_lines(decode_unicode=True):
            # ``iter_lines`` strips the line terminator. Empty lines are
            # SSE event boundaries and are ignored at the parser level.
            if not raw_line:
                continue

            # SSE comments start with a colon and are heartbeats / keep-
            # alives. Skip them — they're not data.
            if raw_line.startswith(":"):
                continue

            # We only care about ``data: ...`` events. Everything else
            # (event:, id:, retry:) is metadata we can safely ignore for
            # the weekly-summary use case.
            if not raw_line.startswith("data:"):
                continue

            # Strip the prefix. ``data:`` may be followed by a single
            # space per spec, but tolerate the no-space case too.
            payload = raw_line[len("data:"):].lstrip()

            # End-of-stream sentinel. Stop iterating; the ``with`` block
            # in ``chat_stream`` will close the response.
            if payload == "[DONE]":
                return

            # Try to parse as JSON. If that fails, yield the raw text —
            # this protects us against minor format drift on the server.
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                yield payload
                continue

            # Anamnesis's known shape: ``{"chunk": "<text>"}``. Tolerate
            # other shapes by yielding any string field we recognize.
            if isinstance(obj, dict):
                if "chunk" in obj and isinstance(obj["chunk"], str):
                    yield obj["chunk"]
                elif "content" in obj and isinstance(obj["content"], str):
                    yield obj["content"]
                elif "text" in obj and isinstance(obj["text"], str):
                    yield obj["text"]
                # Unknown dict shape — log at DEBUG so we notice if the
                # API changes, but don't spam at INFO.
                else:
                    logger.debug("anamnesis SSE unknown event shape: %s",
                                 list(obj.keys()))
            elif isinstance(obj, str):
                # Some servers send plain JSON-quoted strings.
                yield obj
            # Anything else (numbers, lists at top level) is skipped.
