"""Pre-render fixed phrases using Eureka's configured ElevenLabs voice."""
from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eureka.mvp import FRAME_BYTES, SAMPLE_RATE  # noqa: E402
from eureka.providers import ElevenLabsTTS, ProviderConfig  # noqa: E402
from eureka.telephony_daemon import PhraseStore  # noqa: E402


async def render(directory: Path) -> None:
    tts = ElevenLabsTTS(ProviderConfig.from_env())
    clips = {}
    for name, text in PhraseStore.REQUIRED.items():
        # Conversation.render uses this same adapter and enforces these audio limits.
        audio = await tts.synthesize(text, sample_rate=SAMPLE_RATE)
        if not isinstance(audio, bytes) or not audio or len(audio) % FRAME_BYTES or len(audio) > SAMPLE_RATE * 2 * 60:
            raise ValueError(f"invalid TTS audio for {name}")
        clips[name] = audio

    directory.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for name, audio in clips.items():
            target = directory / f"{name}.slin"
            with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{name}.", suffix=".stage", delete=False) as stream:
                stage = Path(stream.name)
                staged.append((stage, target))
                stream.write(audio)
                mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
                os.fchmod(stream.fileno(), mode)
        for stage, target in staged:
            os.replace(stage, target)
    finally:
        for stage, _ in staged:
            stage.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.validate_only:
            phrases = PhraseStore(args.directory)
            if not phrases.has("thinking"):
                raise ValueError("missing thinking phrase")
        else:
            asyncio.run(render(args.directory))
    except Exception:
        print("phrase validation or generation failed", file=sys.stderr)
        raise SystemExit(1) from None
    print("validated " + ", ".join(f"{name}.slin" for name in PhraseStore.REQUIRED))


if __name__ == "__main__":
    main()
