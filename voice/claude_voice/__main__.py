"""`python -m claude_voice` — talk to Claude Code."""

from __future__ import annotations

import argparse
import asyncio
import os

from .agent import DEFAULT_AUTO_ALLOW, ClaudeCodeAgent, VoicePermissionGate, build_options
from .audio import KeyboardInput, MicListener
from .conversation import VoiceConversation
from .events import InputEvent
from .stt import default_stt_name, make_stt
from .tts import Speaker, default_tts_name, make_tts
from .ui import Console


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="claude-voice", description=__doc__)
    p.add_argument("--text", action="store_true", help="type instead of talk (no mic, no STT)")
    p.add_argument("--mute", action="store_true", help="print replies instead of speaking them")

    g = p.add_argument_group("Claude Code")
    g.add_argument("--cwd", default=os.getcwd(), help="working directory for the agent")
    g.add_argument("--model", help="model override, e.g. claude-opus-5-5")
    g.add_argument(
        "--permission-mode",
        choices=["default", "acceptEdits", "plan", "auto", "bypassPermissions"],
        default="default",
        help="Claude Code permission mode; tool prompts become spoken yes/no questions",
    )
    g.add_argument("--auto-allow", default=",".join(sorted(DEFAULT_AUTO_ALLOW)),
                   help="comma-separated tools that never need a spoken approval")
    g.add_argument("-c", "--continue", dest="continue_conversation", action="store_true",
                   help="continue the most recent Claude Code conversation in --cwd")
    g.add_argument("-r", "--resume", metavar="SESSION_ID", help="resume a specific session")
    g.add_argument("--system-extra", help="extra text appended to the voice system prompt")

    g = p.add_argument_group("speech")
    g.add_argument("--stt", choices=["mlx", "faster-whisper"], default=None)
    g.add_argument("--stt-model", help="whisper model name or HF repo")
    g.add_argument("--vocab", help="words to bias transcription toward, e.g. host and project names")
    g.add_argument("--tts", choices=["say", "espeak", "kokoro", "print"], default=None)
    g.add_argument("--voice", help="TTS voice (say -v '?' lists macOS voices)")
    g.add_argument("--rate", type=int, help="speaking rate (words per minute for say/espeak)")
    g.add_argument("--input-device", help="sounddevice input device index or name")

    g = p.add_argument_group("turn-taking")
    g.add_argument("--end-silence-ms", type=int, default=700,
                   help="silence that ends your turn (raise if it cuts you off mid-thought)")
    g.add_argument("--barge-in-ms", type=int, default=350,
                   help="speech needed to interrupt Claude (raise if its own voice triggers it)")
    g.add_argument("--vad", type=int, default=2, choices=[0, 1, 2, 3],
                   help="VAD aggressiveness, 3 = most eager to call things noise")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


async def amain(args: argparse.Namespace) -> None:
    console = Console(verbose=args.verbose)
    events: asyncio.Queue[InputEvent] = asyncio.Queue()

    tts_name = "print" if (args.mute or args.text and args.tts is None) else (args.tts or default_tts_name())
    speaker = Speaker(make_tts(tts_name, args.voice, args.rate))

    gate = VoicePermissionGate(
        ask=speaker.say,
        show=console.permission,
        auto_allow=frozenset(t.strip() for t in args.auto_allow.split(",") if t.strip()),
    )
    agent = ClaudeCodeAgent(
        build_options(
            gate,
            cwd=args.cwd,
            model=args.model,
            permission_mode=args.permission_mode,
            continue_conversation=args.continue_conversation,
            resume=args.resume,
            extra_system_prompt=args.system_extra,
        )
    )

    if args.text:
        stt = None
        source = KeyboardInput(events)
    else:
        stt_name = args.stt or default_stt_name()
        console.status(f"loading speech recognition ({stt_name})")
        stt = make_stt(stt_name, args.stt_model, args.vocab)
        device = args.input_device
        if device is not None and device.isdigit():
            device = int(device)
        source = MicListener(
            events,
            vad_aggressiveness=args.vad,
            end_silence_ms=args.end_silence_ms,
            barge_in_ms=args.barge_in_ms,
            is_assistant_talking=lambda: speaker.talking,
            device=device,
        )

    console.status(f"connecting to Claude Code in {args.cwd}")
    await agent.connect()
    source.start()
    console.status("ready — just start talking" if not args.text else "ready — type a message")
    try:
        await VoiceConversation(agent, speaker, stt, events, gate, console).run()
    finally:
        source.stop()
        await agent.close()
        if agent.session_id:
            console.status(f"session {agent.session_id} (resume with --resume {agent.session_id})")


def main(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(amain(parse_args(argv)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
