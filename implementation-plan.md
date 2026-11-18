# Eureka MVP Implementation Plan

## Phase 1 — Verify the telephony path and add the isolated route

1. Capture the baseline: Asterisk version, AudioSocket availability, current ownership of `0`, relevant HT802 dialing behavior, and checksums/backups of `extensions.conf` and `pjsip.conf`.
2. If `0` is already routed, stop for an explicit routing decision.
3. Add the smallest Eureka-specific dialplan include/context: answer `0`, invoke `AudioSocket()` with a generated UUID at `127.0.0.1:<port>`, then hang up.
4. Reload the dialplan without restarting Asterisk. Verify `0` routing and regression-test existing extensions plus VoIP.ms inbound/outbound calls.
5. Document and exercise a rollback that removes only the additive Eureka configuration and reloads the dialplan.

## Phase 2 — Deliver bidirectional voice transport and greeting

1. Implement the localhost-only AudioSocket service with UUID validation and handling for audio, DTMF, error, and hangup frames.
2. Implement 8 kHz signed-linear 16-bit mono TTS conversion and paced playback.
3. Pre-render the greeting, unavailable, and feedback phrases.
4. Add a host-appropriate supervised startup unit.
5. Test from the controlled phone that the greeting is clear, bidirectional audio has no severe clipping or protocol error, and socket drops/hangups promptly clean up the session.

## Phase 3 — Add the safe Haiku conversation loop

1. Integrate streaming telephone-audio STT with endpointing, validated transcript handling, short per-call context, configured Haiku streaming, and streaming TTS.
2. Add the versioned constrained system prompt, read-only local knowledge file, and configured, approved read-only current-information lookup. Use model knowledge for permitted general and professional questions; ask for missing lookup details and use the specified default response on lookup failure without blanket live-data refusals or fabricated facts.
3. Implement input checks, fixed emergency/crisis/failure responses, complete-response output checks before TTS, and fail-closed behavior; do not reject permitted questions solely for their topic.
4. Add configurable maximum-call and inactivity limits, redacted structured operational logging with configurable retention, and per-call `1`/`2` feedback recording.
5. Run controlled general, professional, current-information success/follow-up/failure, decency-rule, emergency, prompt-injection, provider-failure, and feedback tests.

## Phase 4 — Validate end to end and prepare launch

1. Create and execute a controlled-call checklist covering routing, greeting, ordinary inquiry, low-confidence STT, hangup, provider failure, and safety scripts.
2. Verify no regressions to Asterisk routing/configuration, HT802 registration, or VoIP.ms inbound/outbound calling.
3. Confirm clear TTS, real-time conversational turns, clean teardown, and zero unsafe responses spoken in the defined safety suite.
4. Produce a deployment/rollback runbook covering configuration locations, secrets handling, startup/health checks, log access, and retention setting; use it to deploy, verify health, and roll back.

## Approved follow-up — conversational interruptions

Implement and test against the [approved interruption addendum](docs/conversational-interruptions-spec-draft.md). Automated verification precedes an authorized controlled-phone evaluation; deployment approval remains separate.

## TTS direction — ElevenLabs

Remove the Kokoro integration and restore the existing ElevenLabs live TTS path. The Piper draft is a historical record, not an active requirement. Validate provider selection, credentials, fixed-prompt fallback, safety and call handling with automated tests; deployment and phone testing require separate authorization.

## Deferred / follow-up

- Information integrations beyond configured, approved read-only current-information lookup, caching, and expanded source citations.
- DTMF menus beyond the required rating prompt, voicemail, human-operator transfer, and inbound DID routing.
- Multi-call scaling, load/soak work beyond controlled MVP validation, rate limiting, and cost dashboards.
- Raw-audio recording, analytics dashboards, a user-facing UI, and a feedback portal.
- HT802 tuning beyond what is minimally required for dial `0` and intelligible audio.
- Expanded languages, personalized caller context, and advanced evaluation/red-team automation.

## Traceability

| Blueprint requirement area | Specification | Plan |
|---|---|---|
| Existing telephony path, additive local `0` route, and rollback | §§1–2, Phase 1 criteria | Phase 1 |
| Local AudioSocket, UUID/hangup handling, 8 kHz playback, greeting, supervision | §§2–3, Phase 2 criteria | Phase 2 |
| STT → checks → Haiku → checks → TTS, general answers and approved current-information lookup | §§1, 3–4, Phase 3 criteria | Phase 3 |
| Safety, emergency guidance, fail-closed behavior, and privacy/operational records | §§4–5, Phase 3 criteria | Phase 3 |
| Feedback, controlled validation, regression testing, runbook, and launch readiness | §§3, 6, Phase 4 criteria | Phase 3–4 |
| Explicitly excluded scope | §4 and Context “Out of scope for launch” | Deferred / follow-up |
