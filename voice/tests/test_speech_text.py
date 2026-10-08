from claude_voice.speech_text import CODE_NOTICE, TABLE_NOTICE, SpeechChunker, clean_for_speech


def stream(text: str, step: int = 3) -> list[str]:
    chunker = SpeechChunker()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += chunker.feed(text[i : i + step])
    return out + chunker.flush()


def test_sentences_are_emitted_before_the_reply_ends():
    chunker = SpeechChunker()
    assert chunker.feed("Checking your Tailscale status now. Th") == [
        "Checking your Tailscale status now."
    ]
    assert chunker.feed("ree nodes are online.") == []
    assert chunker.flush() == ["Three nodes are online."]


def test_short_fragments_wait_for_more_text():
    assert stream("Sure. I'll look at the config file first. Done!") == [
        "Sure. I'll look at the config file first.",
        "Done!",
    ]


def test_code_blocks_are_not_read_aloud():
    text = "Here's the fix:\n```python\nprint('hi')\nx = 1\n```\nThat should work.\n"
    assert stream(text) == ["Here's the fix:", CODE_NOTICE, "That should work."]


def test_tables_and_markdown_are_cleaned():
    text = (
        "## Status\n"
        "| host | state |\n|---|---|\n| nas | up |\n"
        "- **nas** is up, see [the docs](https://tailscale.com/kb/1080/cli).\n"
    )
    assert stream(text) == ["Status", TABLE_NOTICE, "nas is up, see the docs."]


def test_urls_are_reduced_to_their_domain():
    assert clean_for_speech("Docs at https://code.claude.com/docs/en/agent-sdk ok") == (
        "Docs at code.claude.com ok"
    )


def test_inline_code_keeps_its_content():
    assert clean_for_speech("Run `tailscale status` to check.") == "Run tailscale status to check."


def test_long_runs_without_punctuation_are_split():
    chunker = SpeechChunker(max_chars=60)
    out = chunker.feed("word " * 30)
    assert out and all(len(s) <= 60 for s in out)


def test_split_across_every_possible_delta_boundary():
    text = "First sentence is here. Second one follows.\n- a list item\n"
    expected = ["First sentence is here.", "Second one follows.", "a list item"]
    for step in range(1, len(text) + 1):
        assert stream(text, step) == expected, step


def test_screen_notice_skipped_when_claude_already_said_it():
    text = "The full path is on screen.\n```\n/tmp/x\n```\n"
    assert stream(text) == ["The full path is on screen."]
