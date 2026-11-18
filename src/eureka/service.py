"""Local AudioSocket service with explicitly enabled provider adapters."""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from .ami import FeedbackCorrelator, configured_client
from .mvp import (THINKING, ApprovedKnowledge, Conversation, JsonOperationalLog, ProviderFailure, ServiceConfig,
                  UnconfiguredHaiku, UnconfiguredSTT, UnconfiguredTTS, load_prompt)
from .telephony_daemon import AudioSocketServer, EurekaAudioService, PhraseStore, SessionManager

async def _maintain_logs(logger: JsonOperationalLog) -> None:
    while True:
        await asyncio.sleep(3600)
        logger.purge()


async def run() -> None:
    config = ServiceConfig.from_env()
    tts_mode = os.getenv("EUREKA_TTS_PROVIDER", "elevenlabs")
    if tts_mode not in ("legacy", "elevenlabs"):
        raise ValueError("EUREKA_TTS_PROVIDER must be elevenlabs (legacy is also supported)")
    mode = os.getenv("EUREKA_PROVIDER_MODE", "disabled")
    if mode == "disabled" and config.web_search_enabled:
        raise ValueError("web search requires live provider mode")
    if mode == "disabled" and config.weather_enabled:
        raise ValueError("weather requires live provider mode")
    phrases = PhraseStore(Path(os.environ["EUREKA_PHRASE_DIR"]))
    general_client = None
    if mode == "live":
        from .providers import OpenRouterWebPlugin, create_providers
        providers = create_providers()
        stt, haiku, tts = providers.stt, providers.haiku, providers.tts
        if config.web_search_enabled:
            general_client = OpenRouterWebPlugin(haiku.config)
    elif mode == "disabled":
        stt, haiku, tts = UnconfiguredSTT(), UnconfiguredHaiku(), UnconfiguredTTS()
    else:
        raise ValueError("EUREKA_PROVIDER_MODE must be disabled or live")
    weather_client = None
    if config.weather_enabled:
        from .weather import WeatherClient
        weather_client = WeatherClient(config.weather_user_agent)
    conversation = Conversation(stt=stt, haiku=haiku, tts=tts,
        knowledge=ApprovedKnowledge(config.knowledge_path), system_prompt=load_prompt(config.prompt_path), config=config,
        general_prompt=load_prompt(config.general_prompt_path) if config.general_knowledge_enabled else None,
        general_client=general_client, weather_client=weather_client)
    if mode == "live" and not phrases.has("thinking"):
        try:
            phrases.set_thinking(await conversation.render(THINKING))
        except ProviderFailure:
            logging.getLogger(__name__).warning("Thinking prompt unavailable; continuing without it")
    logger = JsonOperationalLog(Path(os.getenv("EUREKA_LOG_DIR", "/var/log/eureka")),
                                int(os.getenv("EUREKA_LOG_RETENTION_DAYS", "7")))
    logger.purge()
    correlator = FeedbackCorrelator()
    ami = configured_client(correlator)
    service = EurekaAudioService(conversation=conversation, phrases=phrases, sessions=SessionManager(max_concurrent_calls=1),
        logger=logger, max_call_seconds=config.max_call_seconds, inactivity_seconds=config.inactivity_seconds,
        feedback_correlator=correlator if ami is not None else None, provider_fixed_prompts=mode == "live",
        interruptions_enabled=config.interruptions_enabled)
    server = AudioSocketServer(host=os.getenv("EUREKA_HOST", "127.0.0.1"), port=int(os.getenv("EUREKA_PORT", "9092")), on_connection=service.handle)
    ami_task = None
    try:
        await server.start()
        ami_task = asyncio.create_task(ami.run()) if ami is not None else None
        await _maintain_logs(logger)
    finally:
        if ami_task is not None:
            ami_task.cancel()
            await asyncio.gather(ami_task, return_exceptions=True)
        await server.close()

def main() -> None:
    asyncio.run(run())

if __name__ == "__main__":
    main()
