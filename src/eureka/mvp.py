"""Safe, local-only Eureka MVP conversation components.

Network provider implementations deliberately do not live here.  Deployments inject
configured streaming adapters; the default adapters fail closed.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import stat
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

SAMPLE_RATE = 8000
SAMPLES_PER_FRAME = 160
FRAME_BYTES = SAMPLES_PER_FRAME * 2
GREETING = "Hello, you've reached Eureka, your AI operator. How can I help you today?"
THINKING = "Let me see."
UNAVAILABLE = "Eureka is unavailable right now. Please try again later. Goodbye."
REPEAT = "I did not catch that. Please say it once more."
FEEDBACK = "Press 1 if I was helpful, or 2 if I was not."
EMERGENCY = "If this is an emergency, please hang up and dial 911 now."
SAFE_REDIRECT = "I cannot help with that. Please ask me something else."
UNVERIFIED = "I cannot verify that information from my approved operator information."
SAFE_OUTPUT = "I cannot provide that response. Please ask me something else."
ASK_CITY = "What city would you like the weather for?"
ASK_CITY_STATE = "What city and state would you like the weather for?"
LIVE_UNAVAILABLE = "I can't do that for you now. Try again later."
MODEL_RETRY = "I couldn't answer that. Please try asking again."
INACTIVITY_GOODBYE = "You're taking too long! I gotta go!"


def _central_now() -> datetime:
    return datetime.now(ZoneInfo("America/Chicago"))


def _central_clock_answer(text: str) -> str | None:
    question = text.lower().strip().rstrip("?.! ")
    date_question = re.fullmatch(r"(?:what(?:'s| is) (?:the |today's )?(?:date|day)|what day is (?:it|today)|what is today(?:'s date)?)", question)
    time_question = re.fullmatch(r"(?:what(?:'s| is) the time|what time is it|what time is it (?:here|in central time))", question)
    if not date_question and not time_question:
        return None
    now = _central_now()
    if date_question:
        return f"Today is {now:%A, %B} {now.day}, {now.year}, Central Time."
    hour = now.hour % 12 or 12
    period = "a.m." if now.hour < 12 else "p.m."
    return f"It is {hour}:{now.minute:02d} {period} Central Time."


_SERVICE_IDENTITY_QUESTIONS = frozenset({
    "what is eureka", "what's eureka", "who is eureka", "who's eureka",
    "tell me about eureka", "can you tell me about eureka", "explain eureka",
    "what is eureka ai", "what is eureka ai operator", "what is the eureka ai operator",
    "what is the eureka service", "what is this service", "who are you", "what are you",
})


def _service_identity_question(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower().replace("’", "'").strip())
    return normalized.rstrip(".?! ") in _SERVICE_IDENTITY_QUESTIONS


class ProviderFailure(RuntimeError):
    """A configured STT, model, or TTS provider did not return safe output."""

    def __init__(self, message: str, *, category: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.category = category
        self.http_status = http_status


@dataclass(frozen=True)
class Recognition:
    transcript: str
    confidence: float


class StreamingSTT(Protocol):
    async def recognize(self, audio: bytes, *, sample_rate: int) -> Recognition: ...


class HaikuClient(Protocol):
    async def stream(self, *, system_prompt: str, context: tuple[str, ...], user_text: str) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class GeneralResult:
    text: str
    search_used: bool


class GeneralSearchFailure(ProviderFailure):
    """The search response was failed, incomplete or ambiguous."""


SEARCH_FAILURE_CATEGORIES = frozenset({"outer_timeout", "provider_timeout", "http_error", "provider_error",
                                       "invalid_decision", "invalid_evidence", "unverified_evidence", "invalid_response"})


class GeneralClient(Protocol):
    async def answer(self, *, system_prompt: str, context: tuple[str, ...], user_text: str,
                     force_search: bool = False, on_search_start: Callable[[], None] | None = None) -> GeneralResult: ...


class StreamingTTS(Protocol):
    async def synthesize(self, text: str, *, sample_rate: int) -> bytes: ...


class UnconfiguredSTT:
    async def recognize(self, audio: bytes, *, sample_rate: int) -> Recognition:
        raise ProviderFailure("STT provider is not configured")


class UnconfiguredHaiku:
    async def stream(self, **_: object) -> AsyncIterator[str]:
        raise ProviderFailure("Haiku provider is not configured")
        yield ""  # pragma: no cover - establishes an async generator


class UnconfiguredTTS:
    async def synthesize(self, text: str, *, sample_rate: int) -> bytes:
        raise ProviderFailure("TTS provider is not configured")


def valid_weather_user_agent(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._()/+@:%?&=~\-]{2,199}', value))


@dataclass(frozen=True)
class ServiceConfig:
    max_call_seconds: float = 300.0
    inactivity_seconds: float = 30.0
    confidence_threshold: float = 0.70
    max_context_turns: int = 6
    knowledge_path: Path = Path("knowledge/approved-facts-v1.json")
    prompt_path: Path = Path("prompts/system-v1.txt")
    general_prompt_path: Path = Path("prompts/system-v2-general.txt")
    max_transcript_chars: int = 500
    max_response_chars: int = 420
    model_timeout_seconds: float = 15.0
    max_model_chunks: int = 128
    general_knowledge_enabled: bool = True
    web_search_enabled: bool = False
    weather_enabled: bool = False
    weather_user_agent: str = ""
    interruptions_enabled: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (self.general_knowledge_enabled, self.web_search_enabled,
                                                     self.weather_enabled, self.interruptions_enabled)):
            raise ValueError("feature settings must be booleans")
        if (self.web_search_enabled or self.weather_enabled) and not self.general_knowledge_enabled:
            raise ValueError("live information requires general knowledge opt-in")
        if not isinstance(self.weather_user_agent, str) or (self.weather_user_agent and not valid_weather_user_agent(self.weather_user_agent)) or (self.weather_enabled and not self.weather_user_agent):
            raise ValueError("invalid weather user agent")
        for name in ("max_call_seconds", "inactivity_seconds", "model_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        value = self.confidence_threshold
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("confidence_threshold must be between zero and one")
        for name, maximum in (("max_context_turns", 20), ("max_transcript_chars", 2000),
                              ("max_response_chars", 420), ("max_model_chunks", 1024)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")
        for name in ("knowledge_path", "prompt_path", "general_prompt_path"):
            if not isinstance(getattr(self, name), Path) or str(getattr(self, name)) in ("", "."):
                raise ValueError(f"{name} must be a file path")

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        general = os.getenv("EUREKA_GENERAL_KNOWLEDGE_ENABLED", "true")
        web = os.getenv("EUREKA_WEB_SEARCH_ENABLED", "false")
        weather = os.getenv("EUREKA_WEATHER_ENABLED", "false")
        interruptions = os.getenv("EUREKA_INTERRUPTION_ENABLED", "false")
        if any(value not in ("true", "false") for value in (general, web, weather, interruptions)):
            raise ValueError("feature settings must be true or false")
        return cls(
            general_knowledge_enabled=general == "true",
            web_search_enabled=web == "true",
            weather_enabled=weather == "true",
            weather_user_agent=os.getenv("EUREKA_WEATHER_USER_AGENT", ""),
            interruptions_enabled=interruptions == "true",
            max_call_seconds=float(os.getenv("EUREKA_MAX_CALL_SECONDS", "300")),
            inactivity_seconds=float(os.getenv("EUREKA_INACTIVITY_SECONDS", "30")),
            confidence_threshold=float(os.getenv("EUREKA_STT_CONFIDENCE", "0.70")),
            max_context_turns=int(os.getenv("EUREKA_MAX_CONTEXT_TURNS", "6")),
            max_transcript_chars=int(os.getenv("EUREKA_MAX_TRANSCRIPT_CHARS", "500")),
            max_response_chars=int(os.getenv("EUREKA_MAX_RESPONSE_CHARS", "420")),
            model_timeout_seconds=float(os.getenv("EUREKA_MODEL_TIMEOUT_SECONDS", "15")),
            max_model_chunks=int(os.getenv("EUREKA_MAX_MODEL_CHUNKS", "128")),
            knowledge_path=Path(os.getenv("EUREKA_KNOWLEDGE_PATH", "knowledge/approved-facts-v1.json")),
            prompt_path=Path(os.getenv("EUREKA_PROMPT_PATH", "prompts/system-v1.txt")),
            general_prompt_path=Path(os.getenv("EUREKA_GENERAL_PROMPT_PATH", "prompts/system-v2-general.txt")),
        )


class ApprovedKnowledge:
    """A small, immutable, explicitly allow-listed local fact set."""

    _filler = frozenset("a an are about can could do does explain for how i is me my of on please tell the to us what who you your".split())

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {"version", "facts"}
                or not isinstance(data["version"], str) or not data["version"].strip()
                or not isinstance(data["facts"], dict)):
            raise ValueError("knowledge must contain a version and facts")
        self.version = data["version"]
        self._facts: dict[tuple[str, ...], str] = {}
        for key, value in data["facts"].items():
            if (not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9]+(?: [a-zA-Z0-9]+)*", key)
                    or not isinstance(value, str) or not value.strip() or len(value) > 420):
                raise ValueError("invalid approved fact")
            tokens = tuple(key.lower().split())
            if tokens in self._facts:
                raise ValueError("duplicate approved fact")
            self._facts[tokens] = value.strip()

    def lookup(self, question: str) -> str | None:
        # All non-key words must be ordinary question framing, not an extra claim.
        if not re.fullmatch(r"[a-zA-Z0-9 ,.!?]+", question):
            return None
        words = re.findall(r"[a-z0-9]+", question.lower())
        matches = []
        for key, value in self._facts.items():
            for start in range(len(words) - len(key) + 1):
                if tuple(words[start:start + len(key)]) == key and all(
                        word in self._filler for word in words[:start] + words[start + len(key):]):
                    matches.append(value)
                    break
        return matches[0] if len(matches) == 1 else None


class Guardrails:
    """Deterministic checks.  Any ambiguity is a refusal, never a model request."""
    _emergency = re.compile(r"\b(?:emergency|crisis|911|suicid\w*|self[ -]?harm|kill myself|hurt myself|cut myself|end my life|end it all|want to die|overdos\w*|fire|heart attack|can't breathe|cannot breathe|not breathing|bleeding|stroke|(?:i(?:'ve| have| was| am| was just) (?:been )?(?:shot|stabbed|attacked))|immediate danger)\b", re.I)
    _unsafe = re.compile(r"\b(?:kill|murder|shoot|stab|hurt (?:him|her|them|someone|people)|attack|assault|violence|bomb|explosive|weapon|gun|sexual|sex|porn\w*|nude|naked|rape|hate\w*|harass\w*|racial|slur|steal|theft|fraud|scam|hack\w*|bypass|illegal|ignore|disregard|override|forget|pretend|roleplay|act as|jailbreak|developer|system prompt|instructions|prompt|diagnos\w*|prescri\w*|symptoms?|medic\w*|treatment|dosage|doctor|aspirin|lawyer|legal|lawsuit|invest\w*|financial|finance|tax|stock|credit card|password|social security)\b", re.I)
    _output_bad = re.compile(r"(?:https?://|www\.|[<>`*_#\[\]{}]|\b(?:damn|fuck\w*|shit\w*)\b|\b\d{3}[-. )]\d{3}[-.]\d{4}\b|[\r\n]|[\x00-\x1f\x7f])", re.I)

    def check_input(self, text: str) -> str | None:
        normalized = text.strip()
        if not normalized:
            return "empty"
        if self._emergency.search(normalized):
            return "emergency"
        if self._unsafe.search(normalized):
            return "unsafe"
        return None

    def check_output(self, text: str) -> bool:
        text = text.strip()
        return bool(text and len(text) <= 420 and not self._output_bad.search(text))


class GeneralGuardrails(Guardrails):
    """Allow discussion of sensitive subjects while blocking clear harmful requests."""
    _emergency = re.compile(
        r"\b(?:emergency|crisis|911|suicid\w*|self[ -]?harm|kill myself|hurt myself|"
        r"cut myself|end my life|end it all|want to die|(?:i|someone|they|he|she) (?:have |has |am |is |just )?(?:overdos\w*|(?:having |had |suffering )?(?:a )?(?:heart attack|stroke)|(?:been )?(?:shot|stabbed|attacked))|"
        r"(?:there is|there's|there was|we have) a fire|can't breathe|cannot breathe|not breathing|(?:i am|i'm|someone is|they are|he is|she is) bleeding|(?:i'm|i am) having (?:a )?(?:heart attack|stroke)|immediate danger)\b", re.I)
    _unsafe = re.compile(
        r"\b(?:(?:ignore|disregard|override|forget) (?:(?:the|previous|prior|your|all) )*(?:instructions|rules)|"
        r"(?:reveal|show|repeat|print) (?:the |your )?(?:system prompt|instructions)|jailbreak|"
        r"(?:how (?:can|do) i|help me|instructions to) (?:kill|murder|shoot|stab|attack|assault|steal|commit fraud|hack)\b|"
        r"(?:how (?:can|do) i|help me|instructions to) (?:make|build|construct) (?:a |an )?(?:bomb|explosive|weapon)\b|"
        r"(?:give|show|write|generate|describe) (?:me )?(?:an? |some )?(?:explicit )?sexual (?:content|material|story|scene)\b|"
        r"(?:write|generate|give me) (?:an? )?(?:racial|racist) (?:slur|harassment)\b)", re.I)


# This deliberately conservative filter cannot establish that a claim is timeless.
_CURRENT = re.compile(r"\b(?:today|tonight|tomorrow|yesterday|now|current\w*|latest|recent\w*|live|this (?:year|week|month|morning|evening)|up[ -]to[ -]date|weather|forecast|price|cost|rate|population|president|prime minister|leader|election|winner|won|score|news|opening hours|open today|schedule|traffic|flight|available|availability|release|version)\b|\b20\d{2}\b", re.I)
_FRESH_CLAIM = re.compile(r"\b(?:today|tonight|tomorrow|yesterday|now|current\w*|latest|recent\w*|live|this (?:year|week|month|morning|evening)|up[ -]to[ -]date|as of|just announced|breaking news|i (?:searched|checked|found|looked up)|according to (?:the web|a (?:website|search)))\b|\b20\d{2}\b", re.I)
_WEATHER_NON_CITY = re.compile(r"^(?:today|tonight|tomorrow|yesterday|now|current|this|the|my|our|here|there|me|us|a|an|next|last|later|it|outside|outdoor|local|nearby|near|anywhere|weekend|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|no|yes|please)\b", re.I)
_WEATHER = re.compile(r"\b(?:weather|forecast|raining|snowing|(?:current|outside|outdoor) temperature|how (?:hot|cold) is it)\b|\bwhat(?:'s| is) (?:the )?temperature\b|\b(?:will|is|going to|chance of)\b[^?]*\b(?:rain|snow)\b|^temperature[?.! ]*$", re.I)
_CITY = re.compile(r"[^\W\d_]+(?:[.'’\-][^\W\d_]+)*\.?(?:[ ,]+[^\W\d_]+(?:[.'’\-][^\W\d_]+)*\.?){0,4}", re.UNICODE)
_NEW_QUESTION = re.compile(r"^(?:what|when|where|why|how|who|which|can|could|would|will|is|are|do|does|tell|explain|describe|weather|forecast|nevermind|never mind|instead|cancel)\b", re.I)


def _city_reply(text: str) -> str | None:
    candidate = re.sub(r"^(?:in|for|at)\s+", "", text.strip(), flags=re.I).strip(" ?!")
    if len(candidate) > 80 or _NEW_QUESTION.match(candidate) or _WEATHER_NON_CITY.match(candidate):
        return None
    return candidate if _CITY.fullmatch(candidate) else None


def _has_weather_city(text: str) -> bool:
    prefix = re.match(r"^(.+?)\s+(?:weather|forecast)\b", text, re.I)
    if prefix and _city_reply(prefix.group(1)):
        return True
    for match in re.finditer(r"\b(?:in|for|at)\s+", text, re.I):
        candidate = re.split(r"[?!]", text[match.end():], 1)[0].strip()
        if candidate and not _WEATHER_NON_CITY.match(candidate) and _CITY.fullmatch(candidate):
            return True
    return False

_PHONE = re.compile(r"(?<!\w)(?:\+?1[-. ]?)?(?:\(?\d{3}\)?[-. ]?)\d{3}[-. ]?\d{4}(?!\w)")
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_SENSITIVE_NUMBER = re.compile(r"(?<!\d)(?:\d{3}[- ]\d{2}[- ]\d{4}|(?:\d[ -]?){13,19})(?!\d)")


def redact(text: str) -> str:
    return _SENSITIVE_NUMBER.sub("[redacted-number]", _EMAIL.sub("[redacted-email]", _PHONE.sub("[redacted-phone]", text)))


def _safe_event(name: str, fields: Mapping[str, object]) -> dict[str, object]:
    # Free-form caller and provider text can contain names, addresses or secrets.
    result: dict[str, object] = {"event": name if re.fullmatch(r"[a-z_]{1,40}", name) else "other"}
    for key, value in fields.items():
        if key == "transcript":
            result[key] = "[redacted]" if value else ""
        elif key == "guardrail" and isinstance(value, str) and value in {"answered", "repeat", "unavailable", "emergency", "unsafe", "goodbye", "unverified", "output-blocked"}:
            result[key] = value
        elif key in {"rating", "latency_ms", "time"} and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = value
        elif key == "component" and isinstance(value, str) and value in {"stt", "haiku", "model", "conversation", "tts", "guardrails", "transport", "logging"}:
            result[key] = value
        elif key == "failure_category" and isinstance(value, str) and value in {"http_400", "http_401", "http_403", "http_404", "http_422", "http_429", "http_5xx", "http_other", "http_error", "network", "timeout", "invalid_audio", "invalid_response", "invalid_decision", "invalid_evidence", "unverified_evidence", "incomplete_response", "provider_error"}:
            result[key] = value
        elif key == "search_failure" and isinstance(value, str) and value in SEARCH_FAILURE_CATEGORIES:
            result[key] = value
        elif key == "interruption_phase" and isinstance(value, str) and value in {"generation", "playback"}:
            result[key] = value
        elif key in {"stt_audio_frames", "stt_active_frames", "stt_results", "stt_nonempty_results", "stt_final_results"} and type(value) is int and 0 <= value <= 1000000:
            result[key] = value
        elif key == "http_status" and type(value) is int and 100 <= value <= 599:
            result[key] = value
        elif key in {"component", "detail"}:
            result[key] = "[redacted]"
    return result


@dataclass
class OperationalRecord:
    call_id: str
    started_at: float
    events: list[dict[str, object]] = field(default_factory=list)
    rating: int | None = None
    ended_at: float | None = None
    end_reason: str | None = None

    def event(self, name: str, **fields: object) -> None:
        self.events.append({"time": time.time(), **_safe_event(name, fields)})

    def finish(self, reason: str) -> None:
        if self.ended_at is None:
            self.ended_at, self.end_reason = time.time(), reason


class JsonOperationalLog:
    """Short-retention structured logs; only redacted operational text is persisted."""
    def __init__(self, directory: Path, retention_days: int = 7):
        self.directory, self.retention_days = directory, retention_days
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be between 1 and 365")

    def purge(self) -> None:
        """Remove expired daily logs; call periodically even when no calls occur."""
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("log directory must be a real directory")
        os.chmod(self.directory, 0o700)
        for path in self.directory.glob("calls-*.jsonl"):
            if not re.fullmatch(r"calls-\d{4}-\d{2}-\d{2}\.jsonl", path.name):
                continue
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_mtime < time.time() - self.retention_days * 86400:
                    path.unlink()
            except FileNotFoundError:
                pass

    def write(self, record: OperationalRecord) -> None:
        self.purge()
        events = [_safe_event(str(event.get("event", "other")), event) for event in record.events]
        # UUIDs are the only caller identifiers accepted in persistent records.
        try:
            call_id = str(uuid.UUID(record.call_id))
        except (ValueError, TypeError, AttributeError):
            call_id = "invalid"
        reason = record.end_reason
        if reason is not None and not re.fullmatch(r"[a-z_]{1,40}", reason):
            reason = "other"
        payload = {"call_id": call_id, "started_at": record.started_at, "ended_at": record.ended_at,
                   "end_reason": reason, "rating": record.rating if record.rating in (1, 2) else None, "events": events}
        target = self.directory / f"calls-{time.strftime('%Y-%m-%d')}.jsonl"
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("log target must be a regular file")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                fd = -1
                stream.write(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
        finally:
            if fd != -1:
                os.close(fd)


class Conversation:
    """Coordinates one utterance without exposing tools, network, or host capabilities."""
    def __init__(self, *, stt: StreamingSTT, haiku: HaikuClient, tts: StreamingTTS, knowledge: ApprovedKnowledge,
                 system_prompt: str, config: ServiceConfig, guardrails: Guardrails | None = None,
                 general_prompt: str | None = None, general_client: GeneralClient | None = None,
                 weather_client: object | None = None):
        self.stt, self.haiku, self.tts = stt, haiku, tts
        self.knowledge, self.system_prompt, self.config = knowledge, system_prompt, config
        if config.general_knowledge_enabled and (not isinstance(general_prompt, str) or not general_prompt.strip()):
            raise ValueError("general knowledge requires its versioned prompt")
        if config.web_search_enabled and general_client is None:
            raise ValueError("web search requires a configured general client")
        if config.weather_enabled and weather_client is None:
            raise ValueError("weather requires a configured client")
        self.weather_client = weather_client
        self.general_prompt, self.general_client = general_prompt, general_client
        self.guardrails = guardrails or (GeneralGuardrails() if config.general_knowledge_enabled else Guardrails())
        # The Haiku adapter accepts at most six alternating user/assistant entries.
        self.context: deque[str] = deque(maxlen=2 * min(config.max_context_turns, 3))
        self.reset()

    def _checked_input(self, text: str) -> str | None:
        decision = self.guardrails.check_input(text)
        if decision is not None and (type(decision) is not str or decision not in ("empty", "emergency", "unsafe")):
            raise ValueError("invalid input safety decision")
        return decision

    def _checked_output(self, text: str) -> bool:
        decision = self.guardrails.check_output(text)
        if type(decision) is not bool:
            raise ValueError("invalid output safety decision")
        return decision

    def reset(self) -> None:
        """Clear all state before (and after) a call; never share it between callers."""
        self.context.clear()
        self.repeat_used = False
        self.last_transcript = ""
        self.last_search_failure: str | None = None
        self._approved_response: str | None = None
        self._pending_weather: str | None = None
        self._delivery: dict | None = None

    def begin_delivery(self) -> None:
        self._delivery = {"context": tuple(self.context), "weather": self._pending_weather,
                          "repeat": self.repeat_used, "request": None}

    def finish_delivery(self, *, interrupted: bool = False) -> None:
        delivery, self._delivery = self._delivery, None
        if delivery is None:
            return
        if interrupted:
            self.context.clear()
            self.context.extend(delivery["context"])
            self._pending_weather = delivery["weather"]
            self.repeat_used = delivery["repeat"]
            self._approved_response = None
            if delivery["request"] is not None:
                self.context.extend((delivery["request"], "[Response interrupted; no complete answer delivered.]"))

    async def reply_to_audio(self, audio: bytes) -> tuple[str, str]:
        self.last_search_failure = None
        try:
            recognition = await self.stt.recognize(audio, sample_rate=SAMPLE_RATE)
        except Exception as exc:
            raise ProviderFailure("STT request failed") from exc
        return await self.reply_to_recognition(recognition)

    async def reply_to_recognition_with_prompt(self, recognition: Recognition,
                                               before_slow_request: Callable[[], Awaitable[None]]) -> tuple[str, str]:
        return await self.reply_to_recognition(recognition, before_slow_request=before_slow_request)

    async def reply_to_recognition(self, recognition: Recognition, *,
                                   before_slow_request: Callable[[], Awaitable[None]] | None = None) -> tuple[str, str]:
        self._approved_response = None
        self.last_search_failure = None
        text = getattr(recognition, "transcript", None)
        confidence = getattr(recognition, "confidence", None)
        self.last_transcript = "[redacted]" if isinstance(text, str) and text else ""
        valid = (isinstance(text, str) and len(text) <= self.config.max_transcript_chars
                 and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                 and self.config.confidence_threshold <= confidence <= 1 and math.isfinite(confidence)
                 and bool(text.strip()) and not re.search(r"[\x00-\x1f\x7f]", text))
        if not valid:
            if not self.repeat_used:
                self.repeat_used = True
                return "repeat", REPEAT
            self._pending_weather = None
            return "unavailable", UNAVAILABLE
        transcript = text.strip()
        self.repeat_used = False
        pending, self._pending_weather = self._pending_weather, None
        weather = False
        weather_request = None
        try:
            decision = self._checked_input(transcript)
            if decision:
                return ("emergency", EMERGENCY) if decision == "emergency" else ("unsafe", SAFE_REDIRECT)
            if self._delivery is not None:
                self._delivery["request"] = transcript
            if re.fullmatch(r"(?:(?:thank you|thanks|okay|ok)(?: for your help)?[ ,.!]+)?(?:goodbye|bye|hang up)[.! ]*", transcript, re.I):
                return "goodbye", FEEDBACK
            identity_fact = self.knowledge.lookup("eureka") if _service_identity_question(transcript) else None
            identity = identity_fact is not None
            approved = identity_fact if identity else self.knowledge.lookup(transcript)
            if self.config.general_knowledge_enabled and approved is None and self.config.weather_enabled and ((pending and _city_reply(transcript)) or _WEATHER.search(transcript) or re.search(r'\b(?:temperature|rain|snow|humidity|wind|storm)\b', transcript, re.I)):
                if self._delivery is not None:
                    self._delivery["request"] = None
                from .weather import request as parse_weather_request
                weather_request = parse_weather_request(transcript, followup=pending) if pending and _city_reply(transcript) else parse_weather_request(transcript)
                if weather_request is None:
                    if not pending and _WEATHER.search(transcript) and not _has_weather_city(transcript) and len(transcript) <= 200:
                        self._pending_weather = transcript
                        return "unverified", ASK_CITY_STATE
                    return "unverified", LIVE_UNAVAILABLE
            elif self.config.general_knowledge_enabled and approved is None:
                city = _city_reply(transcript) if pending else None
                if city:
                    transcript = pending + " Location: " + city
                    if self._checked_input(transcript):
                        return "unsafe", SAFE_REDIRECT
                    weather = True
                elif _WEATHER.search(transcript):
                    weather = True
                    if not _has_weather_city(transcript):
                        if len(transcript) > 200:
                            return "unverified", LIVE_UNAVAILABLE
                        self._pending_weather = transcript
                        return "unverified", ASK_CITY
        except Exception as exc:
            raise ProviderFailure("Input safety check failed") from exc
        if weather_request is not None:
            if before_slow_request is not None:
                await before_slow_request()
            try:
                kind, city, state = weather_request
                response = await asyncio.wait_for(self.weather_client.answer(kind=kind, city=city, state=state), timeout=15)
                if (not isinstance(response, str) or len(response) > self.config.max_response_chars
                        or not self._checked_output(response) or self._checked_input(response) is not None
                        or redact(response) != response):
                    return "unverified", LIVE_UNAVAILABLE
            except Exception:
                return "unverified", LIVE_UNAVAILABLE
            self._approved_response = response
            return "answered", response
        general = approved is None and self.config.general_knowledge_enabled
        if approved is None and not general:
            return "unverified", UNVERIFIED
        needs_current = bool(weather or _CURRENT.search(transcript))
        if general and needs_current and not self.config.web_search_enabled:
            return "unverified", LIVE_UNAVAILABLE
        if (before_slow_request is not None and general
                and (needs_current or len(transcript.split()) >= 12)):
            await before_slow_request()

        async def collect() -> str | None:
            parts: list[str] = []
            size = 0
            stream = self.haiku.stream(
                system_prompt=(self.general_prompt if general else self.system_prompt + "\nThe only approved answer for this turn is the following JSON string. Reply with it verbatim, without additions: " + json.dumps(approved, ensure_ascii=True)),
                context=tuple(self.context), user_text=transcript)
            try:
                async for part in stream:
                    if not isinstance(part, str):
                        raise ProviderFailure("Invalid model stream")
                    size += len(part)
                    if size > self.config.max_response_chars or len(parts) >= self.config.max_model_chunks:
                        return None
                    parts.append(part)
                return "".join(parts).strip()
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()

        searched = False
        search_started = False

        def mark_search_started() -> None:
            nonlocal search_started
            search_started = True

        try:
            if identity:
                response = approved
            elif general and self.config.web_search_enabled:
                result = await asyncio.wait_for(self.general_client.answer(
                    system_prompt=self.general_prompt, context=tuple(self.context), user_text=transcript,
                    force_search=needs_current, on_search_start=mark_search_started),
                    timeout=self.config.model_timeout_seconds)
                if not isinstance(result, GeneralResult) or type(result.search_used) is not bool or not isinstance(result.text, str):
                    raise ProviderFailure("Invalid general response")
                response, searched = result.text.strip(), result.search_used
            else:
                response = await asyncio.wait_for(collect(), timeout=self.config.model_timeout_seconds)
        except GeneralSearchFailure as exc:
            self.last_search_failure = (exc.category if isinstance(exc.category, str)
                                        and exc.category in SEARCH_FAILURE_CATEGORIES else "provider_error")
            return "unverified", LIVE_UNAVAILABLE
        except TimeoutError as exc:
            if search_started:
                self.last_search_failure = "outer_timeout"
                return "unverified", LIVE_UNAVAILABLE
            raise ProviderFailure("Haiku request timed out", category="timeout") from exc
        except ProviderFailure:
            raise
        except Exception as exc:
            raise ProviderFailure("Haiku request failed") from exc
        if general and needs_current and not searched:
            self.last_search_failure = "invalid_response"
            return "unverified", LIVE_UNAVAILABLE
        try:
            # Approved facts remain exact; general answers are not fact-checked.
            safe = (response is not None and (general or response == approved)
                    and self._checked_output(response)
                    and self._checked_input(response) is None
                    and redact(response) == response
                    and (not general or ((searched or (not _FRESH_CLAIM.search(response) and not _CURRENT.search(response)))
                         and len(response) <= self.config.max_response_chars)))
        except Exception as exc:
            raise ProviderFailure("Output safety check failed") from exc
        if not safe:
            return "output-blocked", SAFE_OUTPUT
        self.context.extend((transcript, response))
        self._approved_response = response
        return "answered", response

    async def render(self, text: str) -> bytes:
        fixed = {GREETING, THINKING, UNAVAILABLE, REPEAT, FEEDBACK, EMERGENCY, SAFE_REDIRECT, UNVERIFIED, SAFE_OUTPUT, ASK_CITY, ASK_CITY_STATE, LIVE_UNAVAILABLE, MODEL_RETRY, INACTIVITY_GOODBYE}
        if not isinstance(text, str) or (text not in fixed and text != self._approved_response):
            raise ProviderFailure("Unapproved text cannot be synthesized")
        try:
            audio = await self.tts.synthesize(text, sample_rate=SAMPLE_RATE)
        except ProviderFailure:
            raise
        except Exception as exc:
            raise ProviderFailure("TTS request failed") from exc
        if not isinstance(audio, bytes) or not audio or len(audio) % FRAME_BYTES or len(audio) > SAMPLE_RATE * 2 * 60:
            raise ProviderFailure("TTS returned invalid slin16 audio")
        return audio


def load_prompt(path: Path) -> str:
    prompt = path.read_text(encoding="utf-8").strip()
    if not prompt or "approved" not in prompt.lower():
        raise ValueError("system prompt is missing required constraints")
    return prompt


def new_call_id(raw_uuid: bytes) -> str:
    return str(uuid.UUID(bytes=raw_uuid))
