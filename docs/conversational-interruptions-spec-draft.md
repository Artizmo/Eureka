# Conversational interruptions — specification draft

**Status: approved for implementation on 2026-11-16; controlled-phone validation and deployment approval remain open.** This is an addendum to the existing Eureka specification, not a claim about current behavior. The original MVP documents list barge-in as deferred; this addendum brings only the behavior defined here into scope. Existing opt-in general-knowledge/search experiments retain their separate boundaries and approvals. Numerical targets are initial controlled-test targets and require calibration against the authorized phone setup.

## Goal and scope

A caller may correct or replace a request while Eureka is preparing or speaking its answer. Eureka should stop the obsolete response, recognize the new utterance including its beginning, apply the existing safety checks, and respond. Without a qualifying interruption, the existing conversation flow must remain unchanged. This feature does not add new providers, information sources, tools, telephony controls, persistent audio recording, or multi-call support.

## Approved behavioral contract

1. **Eligible periods.** Listen for interruption during response generation, lookup, synthesis, and playback of a conversational answer, including a validated substitute answer. Exception: greeting, repeat, safety/emergency guidance, unavailable and feedback prompts finish without interruption; hangup and call limits still apply. The feedback stage keeps its existing DTMF behavior.
2. **Speech qualification.** Use sustained speech-like inbound evidence, not a single sample or frame, to distinguish an interruption from silence and brief noise. During playback, also account for the outgoing audio and delayed/attenuated echo; continuous background noise must not routinely cause a trigger. Detection must still work when a caller speaks over playback. These are competing goals; thresholds and echo handling need controlled-phone calibration, not assumptions from synthetic tests alone.
3. **Preserve the onset.** Keep a bounded, in-memory pre-roll of incoming audio and feed the beginning of the interruption to the normal streaming recognizer along with subsequent audio. Do not store raw audio. Continue to endpoint and validate recognition normally; empty or low-confidence results retain the existing repeat behavior.
4. **Supersede the old turn.** On a qualified interruption, stop sending obsolete playback as promptly as practical, cancel and await cancellable model/search/TTS work, and discard any late result. Bytes already delivered to Asterisk or the handset cannot be recalled. Never speak an obsolete response after its turn is superseded, and do not let a late result alter the new turn's history or state.
5. **Conversation context.** Retain prior completed turns and the caller's interrupted request as appropriate, but do not represent an interrupted answer as fully heard. Unless a reliable delivered-text boundary is available, omit the assistant answer rather than invent a partial transcript. Keep any weather/search follow-up state consistent with what was actually delivered. Context and pending work are isolated by call; cleanup prevents leakage into the next call.
6. **Safety.** Pass the revised transcript through the existing input checks before any model or lookup. Apply all existing output checks to the complete revised answer before TTS; never stream unchecked model text to the caller. Emergency/crisis requests retain their fixed guidance. Interruption is not a way to bypass these controls.
7. **Lifecycle and failure.** Hangup, socket drop and maximum duration take precedence in every state and release all tasks and resources; no further speech is sent. Preserve inactivity behavior, ordinary follow-ups, fixed prompts, feedback, and existing provider-failure handling. An error from already superseded work must not terminate the new turn; a genuine failure in the active turn follows the existing unavailable-and-end behavior. Define and test simultaneous interruption, completion, failure and hangup races.
8. **Privacy and operations.** Keep existing redacted logging and retention policies. Avoid logging raw audio or caller numbers; any interruption metrics must contain only non-sensitive operational data. No configuration changes, service restart, or live-call tests without operator authorization.

## Approved initial acceptance measures — require calibration

- Measure stop latency from the start of a clearly audible caller utterance to the last obsolete audio heard at the controlled handset. Proposed target: at most 500 ms for at least 18 of 20 attempts, measured separately for generation and playback; report all misses and exact measurement method. During generation, measure onset to cancellation/no obsolete playback instead. Network, buffering and handset delay may make this target infeasible; revise it explicitly rather than claiming success from unit tests.
- In 20 playback attempts with varied normal-volume phrases and interruption positions, at least 18 should produce a correctly recognized and actionable correction including its initial words. Test quieter speech separately and record failures. These are initial controlled-test targets, not guarantees for arbitrary callers or environments.
- In at least 10 minutes total across silence, representative background noise and playback echo without caller speech, target zero interruption triggers. Describe the handset positions, volume, noise and playback used. A small controlled sample cannot establish a general false-trigger rate.
- Review safety-sensitive corrections, hangup and cleanup cases manually; no unchecked speech, continued obsolete speech after teardown, or cross-call context leakage is acceptable.

## Verification matrix

| Case | Automated evidence | Controlled-phone evidence |
|---|---|---|
| Generation/lookup/synthesis interrupted | Block each provider, trigger speech, verify cancellation or late-result discard and retained onset | Correct request while Eureka is thinking; verify new answer and no old answer |
| Playback interrupted | Paced frames stop, onset reaches STT, late work cannot resume playback | Interrupt at beginning, middle and end; measure audible stop latency |
| Ordinary follow-up and correction | No interruption preserves completed history; interrupted answer is not recorded as fully heard; correction uses prior context | Follow up after an interrupted and an uninterrupted answer |
| False triggers | Silence, isolated spikes, sustained noise and simulated delayed/attenuated echo; real speech over echo | Silence, ambient noise, handset echo and varied volume/position |
| Safety | Revised emergency, disallowed and injection inputs follow existing fixed paths; unsafe model output never reaches TTS | Use approved synthetic safety scripts and verify fixed guidance |
| Lifecycle | Hangup, socket drop, duration, inactivity, provider errors and cancellation races; no leaked tasks or cross-call state; fixed prompts and feedback regressions | Hang up during response and after interruption; check timeout, goodbye and feedback separately |

Automated audio fixtures must include varied speech onsets and echo/noise examples; simple amplitude-only fixtures cannot prove real-world robustness. Run the relevant existing transport, provider, conversation, safety, search and general-knowledge tests alongside focused interruption tests. Record test results separately from proposed targets.

## Rollout and remaining validation decisions

- Interruption support is behind a feature flag that defaults to off. Enable it only for authorized controlled validation; turning the flag off restores the existing non-interruptible behavior.
- Calibrate detector parameters and the initial numerical targets against authorized, representative synthetic or controlled test audio. Record trade-offs between missed quiet speech and false triggers; revise targets explicitly if needed.
- Identify the operator authorized to run phone tests, approve provider data handling and deploy. Do not infer exactly what the caller heard from text alone.

## Delivery gates

1. Scope, prompt exceptions, history policy and initial acceptance criteria approved; rollout flag defaults to off.
2. Document an implementation plan and trace each requirement to focused automated tests; implement without altering unrelated telephony configuration.
3. Run focused and relevant existing suites; investigate regressions and document limitations.
4. With operator authorization, run the controlled-phone matrix, record results without sensitive data, and approve deployment or adjust the design. Automated tests alone do not constitute phone validation.
