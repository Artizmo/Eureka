# Phases 2–4 controlled validation

## Automated results

Run from the repository root with the project test environment:

```sh
.venv/bin/python -m pytest -q
```

**Result:** 116 passed in the full suite after the AMI feedback implementation. After adding explicit call-termination signaling, 26 focused AMI and Phase 2/3 transport tests passed. The automated suite covers AudioSocket framing and UUID admission, loopback binding, 20 ms frame pacing, phrase conversion, actual service greeting, inbound audio, fallback playback, and prompt cleanup on hangup or socket loss. Automated tests do not establish phone audio quality or replace the controlled call.

## Manual controlled-host checklist

On 2026-11-16, the operator reported confirming the greeting and approved answer, unknown-information response, the three listed safety prompts, both feedback ratings and automatic hangup, and hangup followed by a successful new call. The original local greeting sounded robotic. After deploying the ElevenLabs greeting, the operator confirmed its voice and reported that the goodbye/feedback prompt still sounded robotic. Following deployment of the ElevenLabs feedback prompt, the operator confirmed the voice and both rating-and-hangup checks; the matching rating records for this retest have not been independently checked. The operator also reported a quiet call ending after approximately 30 seconds and a maximum-duration test ending after approximately five minutes. These new timings are operator observations; the associated redacted log end reasons have not yet been checked, so neither limit outcome is verified.

On 2026-11-15, the operator confirmed the controlled-phone checks: clear, unclipped greeting on dial 0; unavailable fallback and call end after speaking; hangup during greeting stops playback; and a subsequent call connects. Device and operator identity were not recorded. The listener was reported bound to `127.0.0.1:9092`. Socket-drop, non-local connection, and remaining scenarios below are not confirmed by this report.

Run only after the additive Phase 1 route is installed by the authorized Asterisk operator.

| Scenario | Expected result | Status |
| --- | --- | --- |
| `0` routing | Existing additive route answers; greeting is clear | Operator confirmed 2026-11-15 |
| Ordinary approved fact | Brief correct spoken answer | Operator reported spoken answer with live providers 2026-11-15; log recorded `answered` |
| Unknown fact | Exact honest inability-to-verify response | Operator confirmed response 2026-11-15; log recorded `unverified` |
| Low STT confidence twice | Repeat once, then unavailable and end | Controlled call 2026-11-15 logged `repeat`, `unverified`, `repeat`, `unavailable`, then `recognition_failure`. The final two invalid recognitions followed the expected repeat/end sequence; the intervening valid recognition resets the counter. Confidence values are not logged, so two specifically low-confidence results are not confirmed. Operator reported hearing three repeat prompts; reconcile that observation on retest. |
| Inactivity limit | End the call after the configured inactivity period | Two controlled quiet calls on 2026-11-15 ended, but both logged STT `provider_failure`, not `inactivity_timeout`. In the second, the full logged session lasted about 40 seconds including the greeting; the operator estimated about 30 seconds. Provider timeout and inactivity limit are both configured to 30 seconds; investigate and retest. |
| Maximum call duration | End the call at the configured 300-second limit | The operator reported a call ended after approximately five minutes on 2026-11-15, but the corresponding log shows about 62 seconds and `recognition_failure`, not `max_duration`. A timed controlled retest is required. |
| Local log privacy | Restricted access, redacted transcripts, configured retention | On 2026-11-15, directory mode 0700, log mode 0600, redacted events and seven-day configured retention were checked. Actual expiry and provider-side retention remain pending verification. |
| `1` / `2` after goodbye | Rating only in redacted operational log; call ends | Initial controlled tests on 2026-11-15 failed to record either rating because Asterisk's AudioSocket application does not forward keypad events. After deploying the optional localhost AMI bridge, both ratings were recorded but the operator reported the calls stayed connected. After adding an explicit AudioSocket hangup frame, two further controlled calls recorded `1` and `2` respectively, and the operator confirmed each call ended. AMI filters still require verification that unrelated calls are excluded. |
| Caller hangup/socket loss | Prompt release, no later audio | Hangup confirmed 2026-11-15; socket loss pending controlled test |
| STT/Haiku/TTS fault | Pre-rendered unavailable phrase then clean end | Unconfigured STT fallback confirmed 2026-11-15. Controlled invalid-voice TTS fault on 2026-11-15 returned HTTP 404; the operator confirmed the unavailable phrase and clean end. Normal voice configuration was restored, and a post-restore call produced a spoken answer with an `answered` log event. Other provider faults remain pending. |
| Emergency, unsafe, injection, professional advice scripts | Historical criteria: fixed approved response; no model call. Current criteria require helpful responses to permitted professional questions. | Operator confirmed emergency, instruction-override, medical, and legal refusal responses 2026-11-15; logs recorded `emergency` and `unsafe`. These professional refusals do not validate the current requirement. Other scripts and provider-side proof of no model call remain pending. |
| Non-local connection | Cannot connect; listener is `127.0.0.1` only | On 2026-11-15 the listener was loopback-only and a connection to the host's non-loopback IPv4 address from the same host was refused. The operator also reported the separate-host test was blocked (refused or timed out). |
| Regressions | Existing extensions, HT802 registration, VoIP.ms inbound/outbound unchanged | Operator confirmed existing inbound and outbound calls with two-way audio and reported the payphone contact available in Asterisk on 2026-11-15. Other existing extensions remain pending. |

On this controlled host, the approved feedback deployment uses `/etc/asterisk/manager.d/eureka-feedback.conf`, the protected user environment file `~/.config/eureka/ami-feedback.env`, and the user-service drop-in `~/.config/systemd/user/eureka.service.d/50-eureka-feedback.conf`. The pre-change dialplan backup is `/var/backups/eureka-feedback-7j9fn7mr/eureka-extensions.conf`; use the rollback runbook and verify no active calls before changes. The AMI connection and both listeners were confirmed on loopback. Filter isolation from unrelated calls remains unverified.

The current user service is enabled and running, but user lingering was disabled when checked on 2026-11-15; persistence after logout or reboot is not verified. Do not mark these as passed based on unit tests or mocked providers. Record the phone/softphone used, date, operator, observed audio quality, and Asterisk CLI output with no caller numbers. Test both ratings independently, then review feedback with controlled-test outcomes to prioritize knowledge, prompt, and safety-test updates. Verify provider data-handling settings before using caller audio or transcripts. Deployment, end-to-end provider calls, and the controlled safety suite remain pending until actually exercised.
