# Eureka MVP Context

## Purpose and architecture

Eureka is a controlled-environment AI operator for the existing payphone. A caller dials `0`, and the unchanged path is:

```text
Payphone → HT802 → Asterisk → Eureka AudioSocket service on localhost
                              → streaming STT → input safety checks
                              → configured Haiku model → output safety checks
                              → streaming TTS → 8 kHz audio to AudioSocket
```

Eureka provides brief spoken answers to general informational inquiries. It identifies itself as AI and uses a short conversation context.

## Existing-system constraints

- The payphone, HT802, Asterisk, and VoIP.ms path already exist and remain operational.
- A call to `0` is handled locally by Asterisk; it requires no VoIP.ms change.
- Before any route is added, confirm that no existing route owns `0`. Stop for an explicit routing decision if it does.
- Keep `extensions.conf` and `pjsip.conf` functional. Add only the minimum dialplan configuration needed to route `0` to Eureka; do not rewrite either file. `pjsip.conf` remains unchanged unless a verified requirement needs a minimal additive entry.
- The Eureka service accepts AudioSocket traffic only on `127.0.0.1`, validates the AudioSocket session UUID, and closes its session on hangup.
- AudioSocket is the bidirectional transport. Returned TTS audio must be signed linear 16-bit, 8 kHz, mono, paced for playback.
- Provider credentials are environment configuration and must never be source-controlled.

## MVP components and operational assumptions

- Use one streaming STT provider appropriate for telephone-quality audio and one streaming TTS provider with a clear, natural voice.
- Send validated transcripts and short call context to the configured Haiku model. Stream responses, but send only output-checked text to TTS.
- Eureka attempts to answer all requests permitted by its decency rules, using model knowledge, approved local facts, and configured, approved read-only current-information sources as appropriate.
- Current requests require a fresh lookup when needed and a follow-up for missing details. If a lookup cannot provide verified current information, the default response is “I can't do that for you now. Try again later.” Do not fabricate facts or issue a blanket live-data refusal.
- The local knowledge source is read-only and allow-listed.
- Pre-render the greeting, failure, and feedback phrases.
- The service initially supports one call end-to-end, cleans up every session, and has a host-appropriate supervised startup unit.
- Apply a fixed maximum call duration and inactivity timeout to prevent stuck sessions.
- Log call ID, timestamps, component failures, latency, guardrail outcome, and a redacted transcript for a short configurable retention period. Do not retain raw audio or caller numbers by default. Restrict log access and document the retention setting.

## Caller interaction boundary

- On dial `0`, answer with: “Hello, you’ve reached Eureka, your AI operator. How can I help you today?”
- On low-confidence or empty recognition, ask once for a repeat.
- On goodbye, offer once: “Press 1 if I was helpful, or 2 if I was not.” Store only the selected rating with the redacted operational record. A hangup skips feedback.
- The caller may hang up at any time. Eureka must stop speaking, terminate the session, and record operational metadata.
- Use a cynical, sassy, judgmental disposition and dry wit in ordinary generated replies without personal abuse or dismissiveness. Be compassionate in distress; prioritize accuracy, helpfulness, and fixed safety and failure responses over style.
- Do not use markdown or URLs in speech, and do not imply a human identity.

## Safety and integration boundaries

- Eureka does not place calls, control Asterisk, access arbitrary systems, or make purchases. Permitted professional questions receive helpful answers with appropriate uncertainty and referrals to qualified professionals when warranted.
- Approved, configured read-only search or current-information sources are permitted; arbitrary API access, database integration, shell access, filesystem access, telephony-control access, arbitrary network access, and open-ended tool access remain excluded.
- Before Haiku, screen for decency and safety violations involving imminent emergency, self-harm, violence, sexual content, hateful harassment, illegal wrongdoing, and prompt injection. Use a fixed safe redirect when required, not merely because a permitted request mentions a sensitive or professional topic.
- For emergency or crisis content, provide fixed guidance to hang up and dial 911 or the applicable local emergency number. Do not dispatch help or change existing emergency routing.
- The constrained system prompt defines role, approved information, refusal behavior, truthfulness, and short spoken style; it must reject attempts to reveal or override instructions.
- Before TTS, validate each complete response for policy violations, profanity or unsafe content, unsupported claims, sensitive personal information, and unsuitable spoken formatting. Replace failed output with a fixed safe response.
- Guardrails fail closed: if a safety check, model call, or TTS validation fails, do not speak unchecked generated text.

## Out of scope for launch

Do not introduce information integrations beyond configured, approved read-only current-information lookup, caching, expanded source citations, barge-in except as covered by the approved interruption addendum, DTMF menus beyond the specified feedback rating, voicemail, human transfer, inbound DID routing, multi-call scaling, load/soak work beyond controlled MVP validation, rate limiting, cost dashboards, raw-audio recording, analytics or user-facing UI, feedback portal, HT802 tuning beyond what is needed for `0` and intelligible audio, expanded languages, personalized caller context, or advanced evaluation/red-team automation.
