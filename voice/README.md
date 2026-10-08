# claude-voice

Talk to Claude Code like a phone call: you speak, it answers out loud, and you
can cut in whenever you like. Underneath it's still the full Claude Code agent
running on your machine, with your filesystem, shell, `ssh`/`tailscale` to your
nodes, your MCP servers, your `CLAUDE.md` and your permission rules. Voice is a
layer on top. Nothing about the agent changes.

```
 mic ─► VAD ─► Whisper (local) ─► ClaudeSDKClient ─► sentence chunker ─► TTS ─► speakers
        │                              ▲    │
        └── "user started talking" ────┘    └── tool approvals become "Okay?" / "yes"
            (stop audio now; interrupt the agent if it was real speech)
```

## How the conversation works

| You do | What happens |
|---|---|
| Start talking while Claude is speaking | Its voice stops within ~350 ms. The rest of the reply is held. |
| …and it was just "mm-hm", "go on" or a cough | It picks up again from the start of the sentence it was on. |
| …and you said "stop", "never mind", "wait" | The agent is interrupted (`client.interrupt()`) and goes quiet. |
| …and you said something real | The agent is interrupted and gets your new message, plus a note on the last sentence you actually heard, so it knows where you cut in. |
| Claude wants to run something not on your allowlist | It asks out loud ("I'd like to run a command to restart the container. Okay?") and prints the full command. Say yes or no. Anything else ("no, use rsync") is passed back to Claude as the reason. |

What gets spoken: Claude is told it's on a voice call (short sentences, no
markdown, say what it's about to do before slow tool calls). Code blocks,
tables and URLs go to the terminal but aren't read aloud. The terminal always
shows the full transcript and every tool call.

## Setup (macOS, Apple Silicon)

You need the `claude` CLI signed in (the SDK uses the same login and bundles a
CLI of its own), and Python 3.10+.

```bash
cd voice
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[mac]'          # mlx-whisper: Whisper on the GPU
# optional, much nicer voice:    pip install -e '.[mac,kokoro]'
```

The first run downloads the Whisper model (~500 MB for small.en). macOS will
ask for microphone permission for your terminal app.

```bash
# Run from the directory you want Claude to work in (or pass --cwd):
claude-voice --cwd ~/Obsidian/Vault \
  --vocab "Tailscale, Obsidian, nas, pihole, homelab" \
  --voice Samantha --rate 200
```

**Echo cancellation (no headphones needed).** Build the small Mac helper once:

```bash
sh macos/build.sh
```

After that, `claude-voice` uses it automatically and prints "echo cancellation
on". It uses Apple's Voice Processing I/O, the echo cancellation FaceTime uses,
so Claude's voice is subtracted from what the mic hears and you can interrupt
it over the laptop speakers. The helper also does the speaking, using the same
voices as `say` (`--voice`, `--rate`), because the Mac can only cancel sound it
plays itself. While it runs, macOS may turn down other audio such as music, as
it does on a FaceTime call. `--no-aec` turns it off. Without the helper, use
headphones, or raise `--barge-in-ms` if Claude interrupts itself.

### Other setups

| | |
|---|---|
| Linux / Intel Mac | `pip install -e '.[whisper]'` (faster-whisper). TTS defaults to `espeak-ng`; `--tts kokoro` sounds better. |
| No mic / testing | `claude-voice --text`: type lines instead. Replies print, paced like speech, and typing mid-reply interrupts it. |
| Keep a session | `-c` continues the last Claude Code conversation in `--cwd`; `-r <id>` resumes one. The session id is printed on exit, and it's a normal Claude Code session, so `claude -r <id>` opens it in the terminal too. |

### Useful flags

| Flag | Default | |
|---|---|---|
| `--end-silence-ms` | 700 | How long a pause ends your turn. Raise it if you get cut off mid-thought. |
| `--barge-in-ms` | 250 / 350 | How much speech it takes to interrupt Claude. |
| `--permission-mode` | `default` | `acceptEdits` skips approvals for file edits. Approvals for shell commands are still spoken. |
| `--auto-allow` | read-only tools | Tools that never need a spoken yes. Allow rules in `~/.claude/settings.json` apply as well. |
| `--vocab` | | Words to bias Whisper toward: hostnames, project names, jargon. |
| `--tts` / `--voice` / `--rate` | `say` on macOS | `say -v '?'` lists voices. Premium/Siri voices sound much better (System Settings → Accessibility → Spoken Content). |

## Layout

| File | Role |
|---|---|
| `claude_voice/agent.py` | `ClaudeSDKClient` session (`include_partial_messages` for token streaming), the voice system prompt, and the spoken permission gate (`can_use_tool`). |
| `claude_voice/conversation.py` | Turn-taking state machine: hold, resume or interrupt, approvals, and the "what you heard" note. |
| `claude_voice/audio.py` | Mic capture with WebRTC VAD in 30 ms frames, and the keyboard stand-in. |
| `claude_voice/macaudio.py` + `macos/AudioHelper.swift` | Echo-cancelled mic and speech on macOS. |
| `claude_voice/stt.py` | mlx-whisper / faster-whisper, with Whisper's silence hallucinations filtered out. |
| `claude_voice/tts.py` | `say` / `espeak` / Kokoro backends and the interruptible `Speaker` queue. |
| `claude_voice/speech_text.py` | Turns streamed markdown into speakable sentences. |

Run the tests with `pip install -e '.[dev]' && pytest`. They cover the chunker
and the barge-in / approval logic, using a scripted agent and a fake voice.

## Scope: what v1 is and isn't

**In:** full-duplex-ish turn-taking with barge-in, echo cancellation on macOS, streaming speech (the first
sentence plays while Claude is still writing), spoken tool approvals, local
STT, local TTS, session continuity with the regular CLI.

**Not yet, roughly in order of payoff:**

1. **Streaming STT.** Right now transcription starts after you stop talking
   (~0.3–0.8 s on an M-series Mac with small.en). A streaming recogniser would
   remove most of that delay and allow smarter end-of-turn detection, so a
   pause after "and then…" doesn't end your turn.
2. **TTS prefetch.** Synthesise sentence N+1 while N plays. This matters for
   Kokoro and cloud voices, less for `say`.
3. **Wake word / push-to-talk** for leaving it running all day (e.g.
   openWakeWord, or a global hotkey that unmutes `MicListener.muted`).
4. **Phone and away-from-desk access**: put a WebRTC or SIP front-end in front
   of the same `VoiceConversation`, reachable over your tailnet. The input and
   output sides are already separate, so this means a new `audio.py` source and
   `tts.py` sink, not a rewrite.

Cost note: each turn is a normal Claude Code turn. Talking is chattier than
typing, so expect more turns. `-v` prints the cost of each one.
