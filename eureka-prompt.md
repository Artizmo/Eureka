# Eureka MVP Implementation Prompt

You are implementing the Eureka MVP in `/home/phony-admin/projects/eureka`.

## Authoritative documents

Read and follow these in order:

1. `blueprint.md` — source of truth
2. `context.md` — current MVP architecture, existing-system constraints, and integration boundaries
3. `spec.md` — normative, testable requirements and acceptance criteria
4. `implementation-plan.md` — the required four-phase delivery sequence

If these documents conflict, follow `blueprint.md`. Do not infer requirements from older files or comments that conflict with it.

## Objective

Deliver the controlled-environment MVP in which the existing payphone caller dials `0` and speaks with Eureka, an AI operator using the configured Haiku model:

```text
payphone → HT802 → Asterisk → localhost AudioSocket service
         → streaming STT → input safety checks → Haiku
         → output safety checks → streaming TTS → AudioSocket
```

## Required execution order

Implement and validate only these critical-path phases, in order:

1. Verify the existing telephony path and add the isolated additive `0` route.
2. Deliver reliable localhost bidirectional AudioSocket voice transport and the pre-rendered greeting.
3. Add the safe STT → Haiku → TTS conversation loop, approved local knowledge, guardrails, redacted operational logging, limits, and the single `1`/`2` feedback rating.
4. Run controlled end-to-end validation and produce the deployment/rollback runbook.

Do not proceed past a phase until its specified acceptance criteria are met or a real environment dependency is explicitly documented as blocking it.

## Non-negotiable constraints

- Before changing routing, verify that no existing route owns `0`; stop for an explicit decision if it does.
- Keep the existing payphone → HT802 → Asterisk → VoIP.ms path working. A call to `0` stays local to Asterisk and requires no VoIP.ms change.
- Make only the minimum additive dialplan configuration. Do not rewrite `extensions.conf` or `pjsip.conf`; leave `pjsip.conf` unchanged unless a verified requirement needs a minimal additive entry.
- Bind Eureka only to `127.0.0.1`. Validate AudioSocket session UUIDs and release sessions promptly on hangup or socket loss.
- Return AudioSocket-compatible signed linear 16-bit, 8 kHz mono TTS audio and pace playback frames.
- Provider credentials must be environment configuration and must never be committed.
- Attempt to answer every request permitted by the decency rules, including professional and live-data questions. Use model knowledge, approved local facts, and configured, approved read-only current-information sources as appropriate.
- Attempt a fresh lookup for current questions and ask for missing details when necessary. If a lookup cannot provide verified current information, default to “I can't do that for you now. Try again later.” Never fabricate current facts or issue a blanket live-data refusal.
- Do not implement arbitrary APIs, database integrations, shell/filesystem/telephony-control access, arbitrary network access, or open-ended tools. Approved read-only search/current-information lookup is permitted.
- Implement input safety checks before Haiku and complete-response output checks before TTS. Guardrails fail closed: no unchecked generated text may be spoken.
- Use fixed emergency/crisis guidance that tells callers to hang up and dial 911 or the applicable local emergency number. Do not dispatch help or interfere with emergency routing.
- Do not retain raw audio or caller numbers by default. Log only the specified redacted operational records with configurable short retention.
- Do not add anything listed in `implementation-plan.md` under **Deferred / follow-up**.

## Working method

- Inspect the repository and existing configuration before editing.
- Make small, reversible, phase-scoped changes.
- Add focused tests only for behavior introduced by the change.
- Run the smallest relevant validation after each change, then run the phase acceptance checks.
- Never execute a production Asterisk configuration change without verified backups, a documented rollback, and confirmation that the target environment is controlled.
- Record exact commands, observed results, and blockers in the applicable runbook/test artifacts required by Phase 4.

## Completion criteria

Completion requires all Phase 1–4 acceptance criteria in `spec.md` to be satisfied, including controlled-call evidence of routing, greeting, ordinary inquiries, low-confidence STT, hangup, provider failure, safety scripts, no regressions to existing telephony, clean teardown, and zero unsafe responses spoken in the defined safety suite.
