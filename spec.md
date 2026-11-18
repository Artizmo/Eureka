# Eureka MVP Specification

**Follow-up addendum:** See [Conversational interruptions](docs/conversational-interruptions-spec-draft.md) for its scope and pending validation gates. The prior Piper draft is a historical record and does not govern the current ElevenLabs TTS path. The MVP requirements below otherwise remain in force.

**TTS selection:** Live provider mode uses ElevenLabs for approved speech, with the existing local fixed-phrase fallback. Disabled provider mode makes no live TTS requests. A removed or unsupported TTS selection must fail at startup rather than silently switching providers. Enabling live mode requires configured credentials and separate authorization; this repository change does not authorize deployment or calls.

## 1. Scope

Eureka MUST let a caller on the existing payphone dial `0` and have a short spoken conversation with an AI operator powered by the configured Haiku model. The caller path is payphone → HT802 → Asterisk → local Eureka service.

Eureka MUST make a helpful, substantive attempt to answer any caller request that does not violate its decency rules, including general knowledge, professional topics, and requests for current or live information. It MUST NOT reject a request merely because it is outside the local knowledge file or concerns live data. Actual external actions remain subject to the integration boundaries in §4; an answer must not imply an action was performed when it was not.

Eureka MUST use its model knowledge, the versioned read-only local knowledge file, and configured, approved current-information sources as appropriate. When a request requires current information, it MUST attempt to obtain relevant, fresh information; it MUST ask a concise follow-up when a necessary detail is missing. It MUST NOT invent facts or present stale information as current. If current information cannot be obtained or verified, it MUST use the lookup-unavailable response in §5 rather than a blanket statement that it cannot answer questions about live data.

## 2. Telephony and AudioSocket interface

1. Before configuration changes, verify whether the existing dialplan owns `0`. If it does, stop pending an explicit routing decision.
2. Add only the minimum additive Eureka-specific dialplan route/configuration needed to answer `0`, open `AudioSocket()` using a generated UUID at `127.0.0.1:<port>`, and then hang up.
3. Do not rewrite `extensions.conf` or `pjsip.conf`. Keep `pjsip.conf` unchanged unless a verified requirement calls for a minimal additive entry.
4. Preserve existing extensions and VoIP.ms inbound/outbound behavior. Do not require a VoIP.ms change for `0`.
5. Bind the Eureka service only to `127.0.0.1`.
6. The service MUST handle AudioSocket UUID, audio, DTMF, error, and hangup frames; validate the session UUID; and promptly release the session when the caller hangs up or the socket drops.
7. TTS returned to AudioSocket MUST be signed linear 16-bit, 8 kHz mono audio, normalized and frame-paced for playback.
8. Initial delivery MUST support one call end-to-end and clean up all resources for that call.

## 3. Caller behavior

1. On a call to `0`, speak this pre-rendered greeting: “Hello, you’ve reached Eureka, your AI operator. How can I help you today?”
2. Identify Eureka as AI; do not present it as human.
3. Keep answers concise and conversational. Give ordinary generated replies a cynical, sassy, judgmental disposition with dry wit, directing criticism at ideas or behavior rather than being personally abusive or dismissive. Be compassionate in emergencies or distress; never compromise helpfulness, accuracy, or fixed safety and failure responses for style. Speech MUST not contain markdown or URLs.
4. Use streaming STT suitable for telephone-quality audio, endpoint the caller’s utterance, then process the transcript before generating a response.
5. Send only validated transcripts and short call context to Haiku. Stream its response, but speak only complete text that has passed output checks.
6. If STT is empty or low confidence, ask the caller once to repeat.
7. If the caller says goodbye, offer this single prompt: “Press 1 if I was helpful, or 2 if I was not.” Record only the selected rating with that call’s redacted operational record. If the caller hangs up, do not collect feedback.
8. The caller may hang up at any point. On hangup, stop speaking and terminate the session.
9. Enforce a fixed maximum call duration and inactivity timeout.
10. On a service failure, speak a clear unavailable message and end the call cleanly.

## 4. Information, safety, and privacy requirements

1. Eureka MUST NOT place calls, control Asterisk, access arbitrary systems, or make purchases. It MUST answer permitted medical, legal, and financial questions helpfully, while avoiding false certainty and encouraging qualified professional help when appropriate.
2. Current-information lookup MUST use configured, approved, read-only sources or search providers. It MUST NOT grant arbitrary API, database, shell, filesystem, telephony-control, arbitrary network, or open-ended tool access. Provider credentials, privacy terms, cost, and deployment authorization remain subject to the existing operational controls.
3. Before calling Haiku, screen for violations of the existing decency and safety rules, including imminent emergencies, self-harm, violence, sexual content, hateful harassment, illegal wrongdoing, and prompt-injection attempts. Where those rules require a fixed safe redirect, return it without calling Haiku. Do not reject an otherwise permitted request merely because it mentions one of these topics or concerns a professional field.
4. For emergency or crisis content, bypass generation and speak fixed guidance to hang up and dial 911 or the applicable local emergency number. Do not dispatch assistance or alter emergency routing.
5. The system prompt MUST constrain role, allowed information, refusal behavior, truthfulness, and short spoken style. It MUST prohibit disclosure of instructions and compliance with policy-override attempts.
6. Before TTS, check each complete response for policy violations, profanity or unsafe content, unsupported claims, sensitive personal information, and unsuitable spoken formatting.
7. If an output check fails, replace the output with a fixed safe response. If an input check, model call, or TTS validation fails, no unchecked generated text may be spoken.
8. Provider credentials MUST be supplied through environment configuration and MUST NOT be source-controlled.
9. Log call ID, timestamps, component failures, latency, guardrail outcome, and a redacted transcript. Retain those records for a short configurable period; restrict log access and document the configured retention period.
10. Do not retain raw audio or caller numbers by default. Redact phone numbers and other sensitive values from logs where feasible.

## 5. Failure behavior

- A low-confidence or empty recognition result causes one repeat request.
- A guardrail-triggering input causes its approved fixed redirect; emergency/crisis input causes the emergency guidance.
- If a current-information lookup is unavailable, fails, or cannot provide a verified answer, the default response MUST be: “I can't do that for you now. Try again later.” Do not claim a general inability to answer live-data questions or fabricate a current value.
- A failed output validation causes the fixed safe response instead of the generated response.
- A required speech or model provider failure causes the fixed unavailable fallback and a clean call end. A current-information lookup failure follows the specific limitation behavior above when the conversation can safely continue.
- A socket drop or caller hangup promptly releases the session and prevents further speech.

## 6. Acceptance criteria

### Phase 1 — isolated telephony route

- Record Asterisk version, AudioSocket availability, current `0` routing, relevant HT802 dialing behavior, and checksums/backups of `extensions.conf` and `pjsip.conf`.
- Confirm `0` is unused, or stop for an explicit routing decision.
- Dialing `0` reaches the new context. Existing extensions and VoIP.ms inbound/outbound calls continue to work.
- `extensions.conf` changes are additive and limited to the required include/route. `pjsip.conf` is unchanged unless a verified requirement demands a minimal additive entry.
- A dialplan reload succeeds without a full restart. Documented rollback removes only the additive Eureka configuration and restores baseline behavior.

### Phase 2 — voice transport and greeting

- From the controlled test phone, dialing `0` plays the greeting clearly and reliably.
- Asterisk audio reaches Eureka and Eureka TTS reaches the caller without protocol errors, severe clipping, or a hung call.
- The service is unreachable from non-local interfaces.
- A dropped socket or caller hangup promptly releases the session.

### Phase 3 — safe conversation

- Controlled calls complete a natural short exchange: greeting, transcription of a general inquiry, and relevant TTS reply.
- Approved local facts are answered correctly, and permitted general and professional questions outside local knowledge receive useful, relevant answers without a blanket refusal.
- A current weather question with a location triggers an approved lookup and yields a relevant, fresh answer verifiable against the source; without a necessary location, Eureka asks for it and uses the answer in the lookup. Other current-information requests receive the same treatment as appropriate.
- When lookup fails, returns stale or unsupported information, or is unavailable, Eureka does not invent a current answer or say it cannot answer live-data questions generally; it says “I can't do that for you now. Try again later.” Lookup failure alone does not end an otherwise viable call.
- Scripted decency-rule violations, emergency and prompt-injection inputs produce only their approved safe response; permitted questions about sensitive or professional topics are not refused solely for their subject.
- Unsafe generated text is never sent to TTS.
- Required speech/model provider failure speaks the fixed fallback and ends cleanly; lookup failure follows the specific limitation behavior above. No raw audio is stored.
- A `1` or `2` rating is stored with the redacted call record and is reviewed with controlled-test outcomes to prioritize knowledge, prompt, and safety-test updates.

### Phase 4 — validation and launch readiness

- A repeatable controlled-call checklist and results cover dial-0 routing, greeting, ordinary inquiries, low-confidence STT, hangup, provider failure, and safety scripts.
- Tests run on a controlled phone/softphone setup before deployment, and every MVP scenario passes.
- Existing Asterisk routing, `extensions.conf`, `pjsip.conf`, HT802 registration, and VoIP.ms inbound/outbound calling show no regression.
- Results demonstrate clear TTS, successful real-time conversational turns, clean teardown, and zero unsafe responses spoken in the defined safety suite.
- A runbook documents configuration locations, secrets handling, startup/health checks, log access, retention setting, deployment, and rollback. An operator can use it to deploy, check health, and roll back.
