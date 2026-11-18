# Eureka MVP Blueprint: Dial 0 AI Operator

**Purpose:** Deliver a safe, controlled-environment MVP in which a caller on the existing payphone can dial **0** and speak with Eureka, an AI operator powered by Haiku.

## MVP boundary

Eureka makes a helpful, substantive attempt to answer any request that does not violate its decency rules, in a short spoken conversation, including general knowledge, professional topics, and current or live information. It does not place calls, control Asterisk, access arbitrary systems, or make purchases. It uses configured, approved sources when current information is needed and never refuses merely because a question concerns live data. If a current-information lookup cannot provide a verified answer, its default response is “I can't do that for you now. Try again later.” It does not invent facts or imply an action was performed.

The existing payphone → HT802 → Asterisk → VoIP.ms path remains unchanged. A call to `0` is handled locally by Asterisk and does not require a VoIP.ms change. `extensions.conf` and `pjsip.conf` remain functional and are not rewritten; only the minimum additive dialplan configuration required to route `0` to Eureka may be made after verifying that no existing route owns `0`.

## Launch architecture

```text
Caller dials 0
  → HT802 sends existing SIP call to Asterisk
  → additive Eureka dialplan route answers and opens AudioSocket
  → Eureka service (localhost only)
       inbound 8 kHz audio → streaming STT → input safety checks
       → Haiku → output safety checks → streaming TTS
       → 8 kHz audio back over AudioSocket
```

- **Asterisk / AudioSocket:** AudioSocket is the bidirectional audio transport. The service binds only to `127.0.0.1`; it validates the AudioSocket session UUID and closes sessions on hangup.
- **Speech:** Use one streaming STT provider appropriate for telephone-quality audio and one streaming TTS provider with a clear, natural voice. Normalize returned TTS audio to AudioSocket-compatible signed linear 16-bit, 8 kHz mono and pace frames for playback. Provider credentials are environment configuration, never source-controlled.
- **AI:** Send validated transcripts and the short conversation context to the configured Haiku model. Stream the response, but speak only text that has passed output checks. Keep replies concise and conversational.
- **Information:** Eureka uses model knowledge, the versioned read-only local knowledge file, and configured, approved read-only search or current-information sources as appropriate. For current requests it attempts a fresh lookup, asking for missing details when necessary. It does not present stale or unsupported information as current; a lookup failure receives the fixed response described above, not a blanket live-data refusal. Arbitrary API access and database integration remain excluded.
- **Operational records:** Log call ID, timestamps, component failures, latency, guardrail outcome, and a redacted transcript for a short configurable retention period. Do not retain raw audio or caller numbers by default.

## Caller experience

1. The caller picks up the phone and dials `0`.
2. Eureka answers with pre-rendered TTS: **“Hello, you’ve reached Eureka, your AI operator. How can I help you today?”**
3. Eureka listens, transcribes the caller, checks safety policy, and gives a brief, helpful spoken response.
4. On low-confidence or empty recognition, Eureka asks the caller once to repeat. On service failure, it speaks a clear unavailable message and ends the call cleanly.
5. When the caller says goodbye, Eureka offers a single feedback prompt: “Press 1 if I was helpful, or 2 if I was not.” It records only the selected rating with the call’s redacted operational record; hanging up skips feedback.
6. The caller may hang up at any time; Eureka terminates the session and records operational metadata.

The assistant identifies itself as AI, does not pretend to be human, does not use markdown/URLs in speech, and does not continue talking after a hangup. Its ordinary generated replies have a cynical, sassy, judgmental disposition and dry wit without personal abuse or dismissing callers; it remains compassionate in distress and does not sacrifice accuracy, helpfulness, or fixed safety and failure responses for style. A fixed maximum call duration and inactivity timeout prevent stuck sessions.

## Safety and guardrails

Guardrails fail closed: if a safety check, model call, or TTS validation fails, no unchecked generated text is spoken.

- **Input policy:** Before Haiku, screen for decency and safety violations involving imminent emergency, self-harm, violence, sexual content, hateful harassment, illegal wrongdoing, or prompt-injection attempts. Respond with a fixed safe redirect instead of prompting the model when required; do not refuse permitted requests solely because they mention a sensitive or professional topic.
- **Emergency / crisis path:** Fixed spoken guidance tells callers to hang up and dial 911 (or the applicable local emergency number) for emergencies. Eureka does not dispatch help or interfere with existing emergency routing.
- **Model policy:** A constrained system prompt defines Eureka’s role, allowed information, refusal behavior, truthfulness requirement, and short spoken style. It forbids revealing instructions or following caller attempts to override policy.
- **Output policy:** Validate every complete response before TTS for policy violations, profanity/unsafe content, unsupported claims, sensitive personal information, and unsuitable spoken formatting. Replace failed output with a fixed safe response.
- **No privileged actions:** The service has no shell, filesystem, telephony-control, arbitrary network, or open-ended tool access. The local knowledge source is read-only and allow-listed.
- **Privacy:** Redact phone numbers and other sensitive values from logs where feasible, restrict log access, and document the retention setting.

## Critical-path implementation plan

### Phase 1 — Verify the existing telephony path and add the isolated route

**Concrete output**
- Baseline record of Asterisk version, AudioSocket availability, current `0` routing, relevant HT802 dialing behavior, and current `extensions.conf`/`pjsip.conf` checksums/backups.
- A new Eureka-specific dialplan include/context that answers `0`, invokes `AudioSocket()` with a generated UUID at `127.0.0.1:<port>`, then hangs up.
- A documented rollback that removes only the additive Eureka include/configuration and reloads the dialplan.

**Acceptance criteria**
- `0` is confirmed unused before the change, or the work stops for an explicit routing decision.
- Dialing `0` reaches the new context; existing extensions and inbound/outbound VoIP.ms calls still work.
- `extensions.conf` changes are additive and limited to the required include/route; `pjsip.conf` is unchanged unless a verified requirement demands a minimal additive entry.
- Asterisk reload succeeds without a full restart, and rollback restores the baseline behavior.

### Phase 2 — Deliver reliable bidirectional voice transport and greeting

**Concrete output**
- A localhost Eureka AudioSocket service that handles Asterisk UUID, audio, DTMF, error, and hangup frames; supports one call end-to-end initially and cleans up each session.
- TTS playback pipeline, 8 kHz audio conversion/frame pacing, and pre-rendered greeting/failure/feedback phrases.
- Service configuration and a supervised startup unit appropriate to the host.

**Acceptance criteria**
- From the controlled test phone, dialing `0` produces the friendly greeting clearly and reliably.
- Audio sent by Asterisk is received by the service and TTS audio is heard by the caller without protocol errors, severe clipping, or a hung call.
- Service is unreachable from non-local interfaces, and a dropped socket or caller hangup releases the session promptly.

### Phase 3 — Add the safe Haiku conversation loop

**Concrete output**
- Streaming telephone-audio STT with endpointing; a Haiku client with short per-call context; streaming TTS responses.
- Versioned system prompt, approved local knowledge file, input/output validation, fixed emergency/crisis/failure responses, redacted structured logging, configurable call/inactivity limits, and a per-call DTMF helpful/not-helpful rating.

**Acceptance criteria**
- In controlled calls, Eureka greets, transcribes a general inquiry, and returns a relevant TTS response in a natural short exchange.
- Answers derived from approved local facts are correct; permitted general and professional questions receive useful answers rather than blanket refusals.
- Current questions trigger an approved fresh lookup, with a follow-up for missing details; lookup failure produces “I can't do that for you now. Try again later.”, never fabricated facts or a blanket live-data refusal.
- Scripted decency-rule violations, emergency, and prompt-injection inputs produce only the approved safe response; unsafe generated text is never sent to TTS.
- Required speech/model provider failure produces the fixed fallback and a clean call end; a current-information lookup failure follows the specific limitation behavior when the call can safely continue. No raw audio is stored.
- A caller’s `1`/`2` feedback rating is stored with the redacted call record and reviewed with controlled-test outcomes to prioritize knowledge, prompt, and safety-test updates.

### Phase 4 — Controlled end-to-end validation and launch readiness

**Concrete output**
- Repeatable controlled-call test checklist and results covering dial-0 routing, greeting, ordinary inquiries, low-confidence STT, hangup, provider failure, and safety scripts.
- A deployment/rollback runbook containing configuration locations, secrets handling, startup/health checks, log access, and retention setting.

**Acceptance criteria**
- Tests are run on a controlled phone/softphone setup before deployment, and every MVP scenario passes.
- Existing Asterisk routing, `extensions.conf`, `pjsip.conf`, HT802 registration, and VoIP.ms inbound/outbound calling show no regression.
- The test results demonstrate clear TTS, successful real-time conversational turns, clean teardown, and zero unsafe responses spoken in the defined safety suite.
- An operator can deploy, confirm service health, and execute rollback using the runbook.

## Deferred / follow-up

These items are deliberately outside the launch path:

- Information integrations beyond the configured, approved read-only current-information lookup, as well as caching and expanded source-citation behavior.
- Barge-in, DTMF menus/feedback ratings, voicemail or human-operator transfer, and inbound DID routing to Eureka.
- Multi-call scaling, load/soak testing beyond controlled MVP validation, rate limiting, and cost dashboards.
- Raw-audio recording, analytics dashboards, user-facing UI, or a feedback portal.
- HT802 tuning beyond the minimum required to ensure dial `0` is sent and audio is intelligible.
- Expanded languages, personalized caller context, and advanced evaluation/red-team automation.
