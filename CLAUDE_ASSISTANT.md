# Claude Assistant (a Siri replacement)

Apple doesn't let apps replace Siri itself, so this works the way most
"Siri replacements" do: Siri (or the Action Button) just launches a Shortcut,
and the Shortcut hands your question to Claude and reads the answer out loud.

```
"Hey Siri, Ask Claude"  ->  iOS Shortcut  ->  claude-assistant.js (/ask)  ->  Claude API
                                 ^                                               |
                                 +------------- spoken reply --------------------+
```

There's also a voice web page at `/` you can use from any browser.

## 1. Run the server

You need an Anthropic API key from https://console.anthropic.com.

```bash
npm install
export ANTHROPIC_API_KEY=sk-ant-...
npm run assistant
```

Open http://127.0.0.1:8080, tap the mic, and talk.

To reach it from your phone on the same wifi, listen on all interfaces and set a
password so nobody else on the network can spend your API credits:

```bash
HOST=0.0.0.0 ASSISTANT_TOKEN=pick-a-secret npm run assistant
```

Then use your computer's local IP, e.g. `http://192.168.1.20:8080/?token=pick-a-secret`.
(For use away from home, put it behind a tunnel such as Cloudflare Tunnel or Tailscale.)

## 2. Make the "Ask Claude" Shortcut (iPhone / iPad / Mac)

In the **Shortcuts** app, create a new shortcut named **Ask Claude** with these actions:

1. **Dictate Text** (Stop Listening: *After Pause*)
2. **Get Contents of URL**
   - URL: `http://YOUR-COMPUTER-IP:8080/ask`
   - Method: `POST`
   - Headers: `X-Assistant-Token` = `pick-a-secret` (only if you set one)
   - Request Body: `JSON`, add a Text field `text` = *Dictated Text*
3. **Get Dictionary Value** — Get `Value` for key `reply` in *Contents of URL*
4. **Speak Text** — *Dictionary Value*

Now say **"Hey Siri, Ask Claude"**, ask your question, and Claude answers.

Make it feel even more like Siri:
- **Action Button** (iPhone 15 Pro and newer): Settings → Action Button → Shortcut → *Ask Claude*.
- **Back Tap**: Settings → Accessibility → Touch → Back Tap → Double Tap → *Ask Claude*.
- **Mac**: give the shortcut a keyboard shortcut in its Details panel.

Say "new chat" by calling `POST /reset` (make a second shortcut) if you want Claude to forget the conversation.

## API

| Method | Path     | Body                     | Returns                  |
|--------|----------|--------------------------|--------------------------|
| GET    | `/`      | –                        | voice web page           |
| POST   | `/ask`   | `{ "text": "question" }` | `{ "reply": "answer" }`  |
| POST   | `/reset` | –                        | clears the conversation  |

```bash
curl -X POST http://127.0.0.1:8080/ask -H "Content-Type: application/json" \
  -d '{"text":"How far is the moon?"}'
```

## Notes

- Uses `claude-opus-5-5` at `low` effort so spoken answers come back quickly;
  change `effort` in `claude-assistant.js` to `medium` or `high` for harder questions.
- Requests opt into Anthropic's server-side refusal fallback (`fallbacks: "default"`),
  so a declined request is retried on a recommended fallback model.
- Conversation history lives in memory and resets when the server restarts.
- Claude can't set alarms, send texts, or control your phone the way Siri can. You can
  add that by extending the Shortcut (e.g. pass the reply into other Shortcut actions).
