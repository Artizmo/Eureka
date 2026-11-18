# General knowledge experiment (not deployment approval)

This document records the 2026-11-16 experiment and its limitations; its original approved-facts-only default and failure behavior are historical, not the current normative requirements. See `spec.md` for the new general-answer and current-information requirements. Those requirements do not themselves authorize deployment or establish that the current implementation satisfies them.

**Repository change:** The user approved best-effort search answers without spoken sources. When search is enabled, complete search responses that pass the existing safety checks may be spoken even without citations. Neither search nor a citation establishes accuracy, support, or freshness, and provider failures return the lookup-unavailable response. The historical controlled-call reports below describe earlier code, not this change. No live service has been changed by this repository update.

The repository now defaults to general answers using the versioned
`system-v2-general.txt` prompt. Existing environment files explicitly setting
`EUREKA_GENERAL_KNOWLEDGE_ENABLED=false` retain the historical approved-fact-only
behavior and do not satisfy the current general-answer requirement. This default
change does not authorize deployment or establish safety. `EUREKA_WEB_SEARCH_ENABLED=true` is a
separate opt-in and requires general knowledge and live provider mode. With web off,
the existing Chat Completions adapter remains in use; recognized current questions
fail closed. With web on, general questions use the experimental adapter; approved
facts still use the existing exact-fact path and model. Set
`EUREKA_GENERAL_PROMPT_PATH` to the prompt when deployed. Production deployment requires separate approval. On 2026-11-16, the operator approved enabling both options on the user service for controlled tests with synthetic, non-sensitive questions only; live provider behavior and privacy terms remain unverified.

All model text is collected and checked before speech synthesis. General mode permits
medical, legal and financial questions and answers, with the prompt requesting appropriate
uncertainty and professional help for high-stakes decisions. The general prompt also asks
for a dry, cynical, opinionated voice, tempered by compassion in emergencies or distress;
model adherence to that style is not guaranteed. Its narrower input screening
still redirects apparent emergencies, prompt overrides and some explicit harmful requests;
other harmful requests rely on the model's instructions. The approved-fact-only mode
retains its original restrictions. Time, length, context and provider limits remain in place. General answers do not carry a
fixed spoken uncertainty warning and are not fact-checked against approved facts. The prompt
requests direct answers without search preambles or closing commentary, while retaining
necessary uncertainty and requested source details. The adapter also removes a narrow
"Based on the search,"-style opening from search answers before safety checks; it does
not delete arbitrary qualifiers or endings. Model compliance is not guaranteed;
answers can be inaccurate, outdated, misleading, or unsafe. Regex screening and prompts are not a
semantic safety classifier or a guarantee of prompt-injection resistance; they may
miss dangerous or time-sensitive content, or refuse harmless questions. Implicitly
current questions may evade recognition and be answered without a search. This is
not equivalent to the approved-fact-only path or a guarantee of answer fidelity.

## Search and weather

The selected search provider is Exa, through the existing experimental OpenRouter
integration. For U.S. weather, the selected sources are National Weather Service
forecasts and observations, with OpenStreetMap Nominatim for city-to-coordinate
lookup. Provider selection does not authorize live requests or deployment. Before
live use, separately approve requests and review each service's current terms,
privacy, identification, rate limits, availability and costs. A location lookup
sends the requested location to the geocoding service. Provider selection alone
does not establish that search evidence is fresh or supports an answer.

The optional weather adapter is off by default. Its initial supported questions are
explicit forecasts and current temperatures for a U.S. city and state; unsupported
or ambiguous requests fail closed. Forecasts contain the forecast period's temperature,
not a complete forecast. Observations are labeled with their time and approximate
location, and stale observations are withheld. The adapter has only offline/mock
coverage; live API compatibility has not been verified. In particular, do not enable
the public Nominatim service until its usage policy and application-wide request-rate
requirements have been reviewed and satisfied: this implementation does not provide
an application-wide rate limiter. Configure an identifying `EUREKA_WEATHER_USER_AGENT`
and separately approve live requests and deployment before enabling
`EUREKA_WEATHER_ENABLED=true` in live provider mode.

On 2026-11-17, an operator reported PASS from a single approved synthetic NASA
news check using the repository's best-effort search path. The answer was not
shared or independently verified; this does not establish accuracy, freshness,
provider privacy, deployment readiness, or phone behavior. No deployment was approved.

The experimental adapter makes one Chat Completions request for a structured
model decision: answer directly or search. A recognized current or weather question
that is not handled by the optional weather adapter forces search even if the model selects a direct answer. Only if search is needed,
it makes one additional Chat Completions request with the **deprecated** web plugin,
explicitly using Exa and `max_results=3`. There are no application retries or
recursive tool calls, and neither request enables server tools. OpenRouter describes
the plugin as running once per request; this provider-managed behavior and its
result limit have not been independently verified. Search and model tokens incur
charges; a second LLM request can increase cost and latency. Provider behavior,
account and model compatibility, and search-engine behavior need controlled-host
validation before any separately approved deployment.

The adapter bounds HTTP bytes and requires one complete assistant response and strict
decision JSON. Search citations are optional; if present, their nested URL annotations
must be valid. A citation must have either a nonempty in-message span or, for offsets exactly 0–0,
a nonempty source excerpt. The latter may carry OpenRouter's `uncited: true` flag
and is treated only as a returned source record, not a citation tied to any claim. This limited-evidence policy was approved for the
experiment; it does not verify that a source supports the answer. Provider errors,
incomplete responses and malformed annotations fail closed, but absent citations do not.
A failure during the initial model decision
uses the required service-unavailable-and-end path; a failure after the search request
starts uses the lookup-unavailable response and permits the call to continue. An outer
timeout follows the same distinction. Neither kind of annotation proves the
answer is grounded, fresh, safe, or correct. Strict parsing and avoiding spoken links
may reject otherwise valid provider responses. Search snippets are untrusted, but
neither prompting nor deterministic checks can guarantee resistance to malicious
content. Search-answer CR/LF line breaks are converted to spaces before the existing
output checks; other control characters remain blocked. Source URLs and metadata are
not spoken or logged. The search failure path records only allowlisted diagnostic
categories (such as timeout, invalid evidence or provider error), not provider errors,
responses, queries or source data.

Weather without an identifiable city asks for one, keeping only a short pending
request in memory; the optional weather adapter requires both city and state. The
following location is not assumed or stored as a default. Depending on the enabled
path, the request and city may be sent to OpenRouter and Exa, or only the city and
state to Nominatim and the resulting coordinates to NWS. A new
question, safety refusal, goodbye, reset or call termination clears the pending
request. City and weather recognition are heuristic and can miss or misclassify
phrasing. No city, caller transcript, audio, query, raw provider event or metadata
is written to the operational log.

Sources: [OpenRouter web plugin documentation](https://openrouter.ai/docs/guides/features/plugins/web-search)
and [Chat Completions API](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion).
OpenRouter, the model provider and Exa may receive question or search text and have
different retention policies; review those policies and costs before deployment.
The user service was configured and restarted on 2026-11-16 for controlled testing. The operator initially reported tests 8 and 9 failed and clarified that test 10 also returned a cannot-check response rather than weather. Bounded synthetic provider diagnostics found fenced decision JSON rejected by the deployed adapter; after the fix was deployed, the operator confirmed test 8 and reported that tests 9 and 10 still failed. Search responses carried citation offsets of 0–0, rejected by the deployed evidence check; even an explicit inline-citation prompt did not resolve the observed offset issue. Subsequent synthetic diagnostics found zero-offset excerpts with an `uncited` field. A synthetic weather-follow-up check using the updated limited-evidence policy reached the output guardrail but was blocked; a second redacted check identified line breaks as the blocker. After CR/LF normalization, a single synthetic weather-follow-up provider check produced the city prompt and an `answered` outcome. The answer text was not inspected or independently checked for weather accuracy. The operator approved deployment for controlled testing; the service was restarted after an idle check and its listener and AMI connection were verified. After deployment the operator reported tests 9 and 10 still failed. A subsequent deployment added limited, allowlisted failure diagnostics and direct-answer prompting. The operator then reported that tests 9 and 10 each returned weather confirmed against an independent source; the test 9 response had no preamble or closing commentary. These successful calls do not establish the cause of earlier failures or guarantee future search reliability.

A separate controlled staging call on 2026-11-16 simulated a failure at the web-search request. A staging-only marker confirmed the injected failure, and the redacted turn record showed `unverified` and `provider_error`. The operator reported the cannot-check response without invented weather, in a natural voice, and reported removing the temporary extension. The staging instance was stopped; the normal Eureka service remained active. This simulation does not cover every real provider failure mode.
