==================================================
🧠 MODEL THINKING / REASONING:
==================================================
The user wants a Python daemon for Phase 1 of the plan, one that connects to Asterisk ARI, handles StasisStart events, and manages the connection lifecycle. The earlier context has the plan and notes, and I'll keep the odd reasoning blocks out of my answer.

I'm noticing the plan's Phase 1 is really a scaffold, with a config loader, logging, SQLite, a supervisor with a health endpoint, and a systemd unit, and it never mentions ARI. The spec's integration runs through AudioSocket in the dialplan rather than Stasis, so the request to handle StasisStart events doesn't match this phase. I should point out that mismatch before building anything.

I'm weighing whether to build the ARI/StasisStart version the user asked for, but Phase 1 of the plan is really a scaffold for the daemon lifecycle, and ARI isn't in it at all. The spec uses AudioSocket via the dialplan, so going the ARI route would be a real deviation that needs a decision first.

I'm laying out the rest of Phase 1: the scaffolding milestones M1.1 through M1.6, starting with the repo layout and pyproject.toml. I'm also noting that the plan says Phase 1 depends on P0 discovery being done first.

Next I'm laying out the Phase 1 pieces: a pydantic-based config loader for eureka.yaml, guardrails.yaml, and .env, a logging setup with a PII redaction filter, and a SQLite layer with a single async-safe writer for calls, guardrail events, and feedback, with retention purging.

Next I'm setting up the HTTP health endpoint with a bare asyncio server bound to 127.0.0.1 only, so I don't need an extra dependency. Then I'll move on to the systemd unit, the test coverage for config, redaction, the database, health, and shutdown, and the initial config files like guardrails.yaml.

Next I'm defining the config loader in config.py, which reads the YAML files and .env, then validates them with pydantic models.

Still writing the config models, with a loopback-only check on the health host.

Still writing the config model, with a loopback-only check on the health host.

Next I'm writing the loader for eureka.yaml and guardrails.yaml, with the .env values pulled in first, and checking the parsed result against pydantic models so bad config fails with a clear error.

Still writing the config module: I'm adding a helper that reads a required environment variable and fails if it is missing, though Phase 1 may not need it, and loading `.env` is the only thing I'm doing there. Next I'm setting up the phone-number redaction pattern for the logging filter, a regex covering optional country code and common separators, with a lookahead so longer digit runs don't get partially matched.

Now I'm working out the redaction filter, with a regex that matches phone numbers in a few common formats, plus email addresses. The filter will format each log message first, then replace the matches in the text before the record goes out.

Writing the database layer next: a single-threaded executor will serialize SQLite writes, with async wrappers around each call, and a schema version table to handle migrations.

Writing the database tables now: calls, guardrail events, and feedback, each with the columns I need for call stats and retention.

Next I'm defining the purge step: it deletes calls older than the cutoff and removes their guardrail events and feedback by call ID, since there's no cascade. Then I'm opening the SQLite connection with foreign keys on and WAL mode, pinning every later operation to that same single-thread executor so the connection stays on one thread.

Still writing the database class: it runs on one worker thread, opens the SQLite connection with WAL and foreign keys on, and wraps the calls to start and end a call.

Next I'm defining the database methods: insert_call, finish_call, and purge_expired, with a test using tmp_path. Then I'm writing the migrations as a list of SQL strings, with a migrate step that reads the user_version pragma to see which schema version the database is already on.

Next I'm writing the migration step: it reads the schema version from user_version and runs each pending migration script in order, then bumps the version.

Now I'm writing the health server: a bare asyncio listener that answers GET /health with a small JSON status, including uptime and a draining flag, and returns 404 for anything else, with a short read timeout and a header size cap.

I'm settling on the supervisor design for the lifecycle: a state enum moving through starting, running, draining, and stopped, with a registry for background tasks. On shutdown it stops accepting new connections, waits up to a grace period for tracked tasks, cancels whatever is left, and then closes the database.

Still writing the supervisor class, with a state enum and a set of tracked tasks.

Still writing the supervisor: a spawn helper for tracked tasks, a run method that opens the database, runs the startup purge, starts the health server, and waits for a stop request. The shutdown step is starting to take shape, and I'm working out how the health server should stop.

Still writing the supervisor's shutdown path: the health server stays up until the end so monitoring sees the draining state, then tracked tasks get a grace period before any stragglers are cancelled and the database closes.

I'm settling the error handling: a tracked task that raises gets its exception logged from a done callback. For startup, if the health server can't bind, that OSError should make the process exit non-zero, with the database closed in a finally block. I'm also mapping exit codes, 1 for a runtime failure and 78 for a config error, and `main` will take the config directory and env file as arguments.

Writing the health endpoint's request handler now: it reads the request line and headers with a timeout and a header-count cap, then answers GET /health with a status that returns 503 while draining.

Still writing the health endpoint: a response helper that builds the status line and headers with `Connection: close`, plus a guard for an empty request line so a blank read doesn't crash the parser. The snapshot will report the state, uptime, and active task count, and the server stays bound to loopback.

Still writing the logging module: a JSON formatter that puts the timestamp, level, and context fields into each record, and runs the redaction over the exception text as well.

Still working through the logging setup: I'm adding a redaction filter to the handler so every record passes through it, covering phone numbers and emails, and the same scrub will apply to exception text.

Next I'm settling the config file layout: a service section for the health address and shutdown grace period, a storage section for the database path and retention, and a logging section for level and format. I'll flag the default health port as a proposal, since the spec doesn't fix one, and leave the guardrails section for after this.

Next I'm defining the config models: a guardrails schema with a version field that has to be at least 1 and allows extra keys, with the whole settings bundle returned as a single object that holds both the main config and the guardrails. I'll parse the YAML with the safe loader.

Still writing the config validation: the health host has to be an IP literal and must be a loopback address, so the endpoint can't be exposed beyond the machine.

Next I'm making the validation errors readable by joining each field path with its message, and checking that a YAML file parses into a mapping before anything else. A missing config file raises a ConfigError naming the path, and then the tests get sketched out: config loading, redaction of phone numbers, emails, and SIP URIs, and database inserts and finishes.

I'm wrapping up the test plan: a database test for migrations running twice safely, a health server test that binds to port 0 and reads back the real port, and supervisor tests covering graceful stop, forced cancellation after the grace period, and a SIGTERM path.

Still working on the test plan: I'm making the supervisor take its database as an injected dependency so the tests can drive it, and I'm weighing whether the health port should allow 0 for tests that need an OS-assigned port.

Still writing the supervisor's constructor: it keeps the config's grace period, the injected database, and a stop event, and starts in the STARTING state.

Still writing the supervisor's constructor and snapshot: it stores the grace period, the database, and the tracked task set, and builds the health server around a snapshot method. The health response will return 200 while running and 503 otherwise.

Still writing the supervisor's task-spawning helper: it refuses new tasks unless the supervisor is running, and it tracks each task and hooks up a done callback.

Still writing the supervisor's task-done callback: it removes the finished task from the tracked set, skips anything cancelled, and logs the exception for any task that failed.

Still writing the supervisor's run method: it installs the signal handlers, then starts the database and health server before waiting on the stop event, with a catch for startup failures.

Still writing the startup path: it opens the database, purges expired rows, then starts the health server before marking the supervisor as running.

Still writing the logging formatter: I'm making it pull any extra fields passed to a log call into the JSON record, skipping the standard attributes that logging already sets.

Still writing the logging formatter: it copies any extra fields a log call attaches into the JSON record, skipping the standard LogRecord attributes so only the context fields come through.

Still writing the shutdown path: I'm wiring the signal handlers so SIGTERM and SIGINT just record a stop request and skip any platform that doesn't support them, then starting on the draining step, where the state moves to draining and the stop reason gets logged.

Still writing the shutdown path: I'm waiting out the grace period for any running tasks, cancelling whatever is still pending when it expires, and keeping the health endpoint up (returning 503 while draining) until the database is closed.

Still writing the health server: it's a small class with a request-header line limit and an I/O timeout, taking a host, a port, and a status callback.

Still writing the health server: it takes a host, a port, and a status callback, with a header-line limit and an I/O timeout. Next it starts listening through asyncio, records the port the OS actually bound, and stops by closing the server and waiting for it to finish.

Still writing the health server's connection handler: it wraps the read-and-route step in a timeout so a stalled client can't hold the connection open.

Still writing the health server's connection handler: it reads the request inside a timeout, and if that fails or the client disconnects it falls back to a 400 response.

Still writing the health server's request routing: it reads the request line and then the headers, and if the header block runs past the line limit it answers with a 431.

Still writing the health server's request routing: after the request line, it parses the method, target, and version, rejecting anything that isn't three parts with a 400.

Still writing the HTTP response helper: it builds the status line from a reason-phrase map, adds the content type, length, and no-caching headers, and appends the JSON body.

Still writing the purge routine: it selects the expired call IDs, and if there are none it returns zero, otherwise it opens a transaction to delete the related feedback rows.

Still writing the purge query: it deletes the feedback and guardrail rows for expired calls via a subquery, then the calls themselves, all inside one transaction.

Still writing the database helper: it wraps each write in an explicit BEGIN IMMEDIATE transaction that commits on success and rolls back on any error, since autocommit mode won't do that for a plain `with` block.

Still writing the migration runner: each schema version runs as one script wrapped in BEGIN and COMMIT, and the user_version pragma gets bumped in the same step.

Still writing the database API: the call functions take the call ID, prompt and guardrail versions, and timestamps, and feedback is limited to a rating of -1 or 1.

Still writing the schema: the calls table has an ID, start and end times, an end reason, prompt and guardrail versions, and token counts, with an index on the end time.

Still writing the schema: the feedback and guardrail tables reference the calls table, so the purge has to delete those child rows before removing the calls themselves.

I'm settling on how orphaned calls get cleaned up: the purge should key off the later of the end time or start time, so crashed calls that never got an end time still age out. I'm also considering a startup step that marks any call left without an end time as ended with a crash reason.

Still writing the config and packaging: the guardrails file gets a version field, and the project metadata and dependencies go into the pyproject.

Still writing the packaging: I'm pinning the dependencies, the dev tooling, the CLI entry point, and the pytest and mypy settings, then starting the entry module with its imports for argument parsing, asyncio, and config loading.

Still writing the entry point: I'm adding the argument parsing for the config directory and env file, then loading the settings and handling a config error when the load fails.

Still writing the entry point: I'm wiring the main function to load settings, configure logging, open the database, and hand the supervisor to the event loop, with config errors returning a dedicated exit code.

Still writing the logging setup: I'm building a stream handler with the context and redaction filters, picking a JSON or text formatter based on the config, and replacing the root logger's handlers with it at the configured level.

Still writing the logging setup: the remaining question is how exception tracebacks get redacted, since the text formatter builds them outside the message path, so I'm considering pre-rendering the traceback in the filter.

Still working out how to handle exceptions in the log filter: I'll pre-render the traceback there, run it through the redactor, and store it so the formatters pick it up without recomputing it.

Writing the tests for config loading next, with a helper that writes the YAML files into a temp directory.

Writing the config loader tests now, starting with a helper that drops the YAML files into a temp directory and a test that checks a valid config loads.

Writing the config loader tests now, covering a valid load, a missing required section, rejection of non-loopback hosts, and an unknown top-level key.

Writing the config loader tests now, covering a valid load, a missing section, rejection of non-loopback hosts, an unknown key, and a bad value type. I'm also adding a check that the env file gets loaded.

I'm writing the redaction tests now, parametrized over several phone number formats to check that each one gets masked.

Writing the redaction tests now, checking that phone numbers in different formats, SIP URIs, and emails get masked while ordinary log text passes through unchanged.

I'm checking the phone number patterns against the test strings: the optional country-code prefix, the area code in either form, and the separators. Each variant lines up with the regex, and the four-digit suffix in the timestamp-like numbers falls short of a full phone number, so those stay untouched.

Checking whether the email pattern can match the redacted SIP address, since the bracketed placeholder before the @ might not satisfy the character class.

Still writing the database tests: a round-trip test that starts a call, finishes it with a hangup reason and token counts, then reads it back to check the end reason.

Still writing the database tests: the purge test runs an old call and a new one, with the cutoff set fourteen days back, and checks that only the old call is removed.

Still writing the health tests: the check runs a health server on an ephemeral port, confirming a 200 while running, a 503 once the state flips to draining, and 404 and 405 for the wrong path and method.

Still writing the health endpoint tests: I'm adding a small helper that sends a raw HTTP request over a socket and parses the status code and JSON body, so the 404 and 405 cases can be checked too. Next is the supervisor test, which needs a supervisor built from the config.

Still writing the supervisor tests: I'm building the settings and supervisor, then checking that a clean stop returns exit code zero and leaves the state as stopped.

Still writing the supervisor tests: I'm checking that a stuck task gets cancelled once the grace period runs out, and that spawning is refused when the supervisor isn't running.

Still writing the tests: I'm adding a SIGTERM test that's skipped on Windows, which sends the signal to the running supervisor and checks for a clean exit.

Still writing the supervisor tests: I'm checking that a SIGTERM sent to the process triggers a clean shutdown with exit code zero, and I'm confirming the signal handlers are restored afterward.

Still writing the supervisor tests: I'm adding a startup failure case where a port is already taken, checking that `run()` returns exit code 1 and closes the database.

Checking that a startup failure, such as a port already taken, makes `run()` return 1 after cleaning up. I'm also thinking through what happens if a signal arrives while the supervisor is still starting up.

Still writing the logging setup: I'm defining context variables for the call ID, turn ID, and state, plus a context manager that sets them for the duration of a block.

Still writing the logging setup: I'm adding a context manager that sets the call ID, turn ID, and state context variables for the length of a block and resets them afterward.

Writing the redaction helpers now: a phone-number pattern, an email pattern, and a SIP URI pattern, with the SIP match replaced by a placeholder.

Still settling the redaction helpers: a SIP URI gets collapsed to a placeholder before the email and phone patterns run, so the digits in the address can't slip through as a phone number.

Still writing the redaction filter: the last piece is a logging filter that runs each message through the redaction helpers and also redacts the formatted traceback. I'm checking whether the cached exception text could bypass the redaction if it's set before the filter runs.

Still writing the logging formatters: a JSON formatter that pulls the timestamp, level, logger name, and message from each record, plus the call, turn, and state IDs, and folds in any extra fields the record carries.

Still writing the JSON formatter: I'm building the payload from the timestamp, level, logger, and message, plus the call, turn, and state IDs. Next I'm making sure any extra fields passed in with the record get redacted too, since the filter only covers the message and traceback.

Still settling the context helpers: the logging context variables will be None by default, and the text formatter needs a placeholder for missing IDs, so I'm weighing whether to fill that in the filter or in the formatter.

Still writing the logging setup: I'm adding a config function that builds the handler, with the redaction and context filters attached and a text or JSON formatter picked from the config.

Still writing the logging setup: I'm wiring a handler with the context and redaction filters attached, picking the JSON or text formatter from the config, and installing it on the root logger.

Setting up the database layer now: a single-worker thread pool to run the SQLite calls off the event loop, plus a helper that hands each synchronous function to it.

Setting up the database connection now: a helper that makes sure the connection is open before use, and an open routine that creates the directory and connects with row access and foreign keys on.

Still working on the database layer: I'm making the close routine shut the connection down on the worker thread, then clearing the handle. I'm also checking whether the executor shutdown after close causes trouble for reopening.

Still writing the database calls: I'm adding an insert for new calls with the prompt and guardrail versions and a timestamp, and the duplicate-ID case will raise an error.

Still writing the database calls: I'm wrapping each sync function so the worker thread gets the open connection as its first argument, and the start-call insert will go through that same path.

Still writing the purge routine: I'm opening a transaction, then deleting feedback and guardrail events for calls whose last activity is older than the cutoff, before removing those calls themselves.

Still writing the database calls: I'm closing out any orphaned calls by setting their end time and reason, using the same purge-style transaction pattern.

Still writing the database calls: I'm defining the finish routine to close out a call with its end reason and token counts, only touching rows that haven't ended yet, and reporting whether a row was actually updated.

Still writing the database layer: I'm building the migration list and a migrate routine that checks the current schema version before applying anything new.

Still writing the migration routine: I'm reading the current schema version, refusing to run if the database is newer than this build, and then applying each pending migration inside its own transaction and rolling back on a SQLite error.

Finishing the database and migration code, then moving on to the settings loader.

Still writing the settings loader: I'm reading the two YAML files from the config directory, with a clear error when one is missing or malformed, and then validating each into its config class.

Still writing the settings loader: I'm checking that the config files exist and parse as mappings, then validating each one into its config class with clear error messages that name the failing field.

Still writing the config models: the service section gets a health host, port and shutdown grace period, and the storage section takes a database path and a retention window.

Still writing the guardrails and env files: the logging level and format get fixed choices, the strict base model rejects unknown keys, and the guardrails model allows extra keys since rules come in later. I'm also putting the storage section into the default config and drafting the example env file with the API key slots.

Writing the systemd unit now, with the service running as a dedicated user and an env file for secrets.

Still writing the systemd unit: I'm adding the hardening options, resource limits and journal output, and deciding to drop the redundant command-line env file flag since the unit already loads the environment file.

Dropping the env-file flag from the unit, since the systemd environment file already covers it. I'm checking the storage path resolves to the data directory the hardening options allow writing to, and I'm noting the memory and file-descriptor limits as placeholders to tune later.

I'm going to flag a mismatch before going further: Phase 1 in the plan is the scaffold (config, logging, SQLite, supervisor, health endpoint), and the Asterisk link is AudioSocket through a dialplan `AudioSocket()` call to 127.0.0.1:9092, which belongs to Phase 2. Switching to ARI would need `ari.conf` and `http.conf` changes plus a Stasis app, and that breaks the rule that only `install.sh` touches configs, so it needs an explicit decision first.

Next I'm laying out the file list and code, then the verification steps mapped to milestones M1.1 through M1.6. I haven't run any of this, so I'll say so plainly and expect a few small fixes once the tests run. I also want to note the deviations: pydantic is added, the health port defaults to 8088, shutdown only drains spawned tasks since there are no sessions yet, and the database runs on a single writer thread.

Still writing the supervisor: I'm defining its state enum and the imports it needs for signal handling and the health server.

Still writing the supervisor: I'm setting up its state fields, the database handle, the grace period and retention settings, and a stop event that starts unset.

Still writing the supervisor: I'm setting up the health server, a state snapshot for the health endpoint, and a stop request that records its reason and only acts once.

Still writing the supervisor's spawn method: it refuses to start new tasks unless the state is running, closes the coroutine and raises if not, and otherwise creates a named task and tracks it.

Still writing the supervisor's spawn-related helpers: I'm adding a done-callback that drops finished tasks from the tracked set and logs any task that failed with an exception.

Still writing the supervisor's run loop: I'm installing signal handlers, starting the components, and waiting on the stop event, with any fatal error turning into a failure exit code.

Still writing the supervisor's start sequence: it opens the database, closes any calls orphaned by a previous run, purges expired rows, starts the health server, and then marks the state as running.

Still writing the supervisor's shutdown: it moves to draining, waits up to the grace period for tasks to finish, cancels whatever is left, and then stops the health server.

Still writing the supervisor's signal handling: I'm wiring up the handlers so that a signal triggers a stop request.

Still writing the supervisor's signal setup: I'm wiring the handlers for SIGTERM and SIGINT, and on teardown removing the ones that were actually installed.

Still writing the health server: I'm setting up its imports, a short I/O timeout, and a cap on header lines.

Still writing the health server: I'm adding the start and stop methods, with the start method binding the listener and recording the port it actually got.

Still writing the health server's stop path: I'm closing the listener and waiting briefly for it to shut down, ignoring a timeout there.

Still writing the health server's request handling: I'm reading the request line and then skipping header lines up to a fixed cap, returning a 431 if the headers run too long.

Still writing the health server's request handling: I'm checking the method and path, returning 200 or 503 from the status snapshot, and letting oversized lines fall through to a 400.

Still writing the health handler: I'm wrapping the route call in a timeout, falling back to a 400 for connection or parse errors, and then sending the response.

Still writing the health handler's response: I'm rendering the status and body into an HTTP response and writing it out, with a catch-all for clients that disconnect early and a cleanup step that closes the connection.

Still writing the health handler's response path: the rendered HTTP head is built from the status code, a JSON payload, and fixed headers that keep the connection closing.

Still writing the config and test fixtures: I'm adding a placeholder guardrails file and starting a helper that builds settings for tests.

Still writing the test helper that builds settings: I'm putting a `make_settings` function into the test setup so tests can construct a config with a temporary database path.

Writing the database tests now: I'm setting up a fixture that opens a temporary SQLite database for each test and closes it afterward.

Still writing the database tests: I'm checking that a call can be started and then finished once, with a second finish attempt being ignored.

Still writing the database tests: I'm checking that feedback with an out-of-range rating is rejected, and that purging expired data removes only the old call and its feedback while keeping the recent one.

Still writing the database tests: I'm checking that closing orphaned calls on startup marks them as ended by a service restart, and that reopening an existing database doesn't rerun the migration.

Still writing the tests: I'm checking that the health endpoint reports the current state and routes requests correctly, using a small server bound to an ephemeral port.

Still writing the health endpoint tests: I'm checking that a 200 comes back while the state is running, a 503 once it switches to draining, and that unknown paths and wrong methods get a 404 and a 405.

Checking the logging formatter next: the redaction filter scrubs string values in the extras, and I'm confirming the JSON formatter reads the message after that filter has already run.

Still writing the database helpers: I'm defining a generic wrapper that runs a function on the executor with the open connection passed in, and making sure the database is open before it runs.

Still writing the database helpers: I'm making the open and close calls go through the executor, and the query wrapper hands the connection in only once the database is open.

Now I'm checking the verification plan: installing the package with dev extras, running the test suite, and linting and type-checking with ruff and mypy. Then I'll try the daemon in staging, confirm the health port is listening, make sure SIGTERM exits cleanly, and verify the systemd unit. I should say plainly that I haven't actually run any of this code.

Now I'm checking the verification plan: installing the package with dev extras, running the tests, then linting and type-checking. After that I'd run the daemon in staging, confirm the health port is listening, check that SIGTERM exits cleanly, and verify the systemd unit. I haven't actually run any of this code yet, so I should say that plainly.

Still writing the config loader: I'm defining the validation models and a strict base class that rejects unknown keys, starting with a service section whose health host defaults to localhost and whose port can be zero for an ephemeral binding.

Still writing the config models: I'm adding a service section where the health host has to be a loopback IP address, and the port can be zero for an ephemeral binding in tests.

Still writing the config models: the guardrails section only checks the version key for now, since the rules come later, and the settings dataclass is just taking shape.

Still writing the config models: the guardrails section only checks the version key for now, and I'm settling how the logging section gets its defaults so each default is built fresh.

Still writing the database module: I'm defining the timing constant and the migration list, with a single-thread executor set up to run every database call.

Still writing the database schema: I'm defining the calls table with its timing, end reason, prompt and guardrail versions, and token counts, plus an index on the expiry expression for the purge query.

Still writing the database schema: I'm adding a guardrail events table for per-turn verdicts, and starting a feedback table keyed to each call.

Still writing the database schema: I'm finishing the feedback table, with a rating limited to -1 or 1 and an optional short comment, and then starting the Database class with its own single worker thread.

Still writing the database class: I'm setting up the connection to open on the worker thread, with the row factory set so rows can be read by column name, and planning the open routine to return the connection rather than set it from inside the thread.

Still writing the database helpers: I'm making the open call read the path from the thread and return the connection, with WAL, foreign keys, and a busy timeout set before the migrations run, and closing the connection if anything fails.

Still writing the database class: I'm making each database call run on the single worker thread, with a small helper that hands the open connection to whichever method needs it.

Still writing the database class: I'm adding the public methods that start a call, finish it with an end reason and token counts, and record feedback, with the rating checked to be -1 or 1 before it's saved.

Still writing the database class: I'm adding a helper that records guardrail verdicts per turn, and the lookup, orphan-closing, and purge methods that take the current time.

Still writing the database class: I'm adding the public methods for the rest of the call lifecycle, and a helper that reads a call back by id.

Still writing the config tests: I'm setting up the test that loads settings with the environment file turned off.

Still writing the config tests: I'm checking that a non-loopback health host gets rejected, that a bad type for the shutdown grace period raises a config error, and that an unknown top-level key is refused.

Still writing the config tests: I'm adding a dotenv check that an existing environment variable keeps its value while a new one gets loaded from the file.

Still writing the config tests: I'm wiring up a dotenv check where a value already set in the environment has to survive loading the file, while a new key from the file gets picked up, and I'm working out how to clean up that new variable so it doesn't leak into other tests.

Now I'm wrapping up the test setup: the dotenv check gets its own placeholder for the new key, so the variable gets cleaned up afterward and doesn't leak into other tests. I'm also planning the final pieces, including the pytest-asyncio auto mode in pyproject and the health module's imports.

I'm settling on how the answer should be laid out: a short bit of prose up front, then the code, then the commands to verify it and a note on what I've left out on purpose.


==================================================
💡 FINAL ANSWER:
==================================================
**Heads-up on scope.** Phase 1 of `plan.md` doesn't include ARI. Its scaffold milestones (M1.1–M1.6) cover config, logging, SQLite, a supervisor with signal handling and a localhost health endpoint, and a systemd unit. The Asterisk integration in the plan is **AudioSocket** (`AudioSocket()` in the dialplan, a TCP listener on `127.0.0.1:9092`), and that arrives in Phase 2.

ARI would be a different architecture. It needs `ari.conf` and `http.conf` changes plus a Stasis app, which breaks global rule 1 (only `install.sh` touches Asterisk config, and only to add an include line), and it doesn't carry raw PCM the way AudioSocket does. I haven't built an ARI client. If you want ARI, that's a plan change to decide first.

What follows is the Phase 1 scaffold. The supervisor's lifecycle (STARTING → RUNNING → DRAINING → STOPPED, with tracked session tasks cancelled after the grace period) is the place the Phase 2 AudioSocket listener will attach.

I haven't executed this code. Run the tests before relying on it.

**Files** (I can't write to disk in this session, so save each block to the path shown):

```
eureka/
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── eureka.yaml
│   └── guardrails.yaml
├── systemd/eureka.service
├── src/eureka/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── db.py
│   ├── health.py
│   ├── logging_setup.py
│   └── supervisor.py
└── tests/
    ├── test_config.py
    ├── test_db.py
    ├── test_health.py
    ├── test_redaction.py
    └── test_supervisor.py
```

---

### `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "eureka"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "pydantic>=2.5",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.4", "mypy>=1.10"]

[project.scripts]
eureka = "eureka.__main__:cli"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

### `.env.example`

```bash
# Copy to .env (gitignored). Never commit real values.
ANTHROPIC_API_KEY=
# STT_API_KEY=
# TTS_API_KEY=
```

### `.gitignore`

```gitignore
.env
data/
*.sqlite3
*.sqlite3-*
__pycache__/
.pytest_cache/
*.egg-info/
```

### `config/eureka.yaml`

```yaml
# Defaults from spec section 8 where the spec gives them.
service:
  health_host: 127.0.0.1   # must be a loopback address (validated)
  health_port: 8088        # proposed; not in the spec
  shutdown_grace_s: 5

storage:
  db_path: data/eureka.sqlite3   # relative to the working directory
  retention_days: 14            # plan section 2 default

logging:
  level: INFO
  format: json
```

### `config/guardrails.yaml`

```yaml
# Populated in M6.1. Phase 1 only validates the version key.
version: 1
```

---

### `src/eureka/__init__.py`

```python
"""Eureka: payphone voice assistant."""

__version__ = "0.1.0"
```

### `src/eureka/config.py`

```python
"""Load and validate eureka.yaml, guardrails.yaml and .env (plan M1.2)."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

T = TypeVar("T", bound=BaseModel)


class ConfigError(RuntimeError):
    """Invalid configuration. The message is meant for a human."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceCfg(_Strict):
    health_host: str = "127.0.0.1"
    # 0 = ephemeral port. Used by tests only.
    health_port: int = Field(default=8088, ge=0, le=65535)
    shutdown_grace_s: float = Field(default=5.0, gt=0, le=120)

    @field_validator("health_host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("must be an IP address literal") from exc
        if not address.is_loopback:
            raise ValueError("health endpoint must bind to a loopback address")
        return value


class StorageCfg(_Strict):
    db_path: Path
    retention_days: int = Field(default=14, ge=1)


class LoggingCfg(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"


class EurekaConfig(_Strict):
    service: ServiceCfg
    storage: StorageCfg
    logging: LoggingCfg = Field(default_factory=LoggingCfg)


class GuardrailsCfg(BaseModel):
    # Rules arrive in M6.1. Extra keys are allowed until then.
    model_config = ConfigDict(extra="allow")
    version: int = Field(ge=1)


@dataclass(frozen=True)
class Settings:
    eureka: EurekaConfig
    guardrails: GuardrailsCfg


def load_settings(config_dir: Path, env_file: Path | None = None) -> Settings:
    """Load and validate all configuration. Raises ConfigError on any problem."""
    if env_file is not None and env_file.is_file():
        # override=False: real environment variables win over the file.
        load_dotenv(env_file, override=False)

    eureka_raw = _read_yaml(config_dir / "eureka.yaml")
    guardrails_raw = _read_yaml(config_dir / "guardrails.yaml")
    return Settings(
        eureka=_validate(EurekaConfig, eureka_raw, "eureka.yaml"),
        guardrails=_validate(GuardrailsCfg, guardrails_raw, "guardrails.yaml"),
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _validate(model: type[T], raw: dict[str, Any], name: str) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{name}: {details}") from exc
```

### `src/eureka/logging_setup.py`

```python
"""Structured logging with call context and PII redaction (plan M1.3)."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextvars import ContextVar, Token
from typing import Any

from eureka.config import LoggingCfg

CALL_ID: ContextVar[str | None] = ContextVar("call_id", default=None)
TURN_ID: ContextVar[int | None] = ContextVar("turn_id", default=None)
STATE: ContextVar[str | None] = ContextVar("state", default=None)

_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SIP_RE = re.compile(r"\bsips?:[^@\s]+@")

_CONTEXT_KEYS = ("call_id", "turn_id", "state")
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}
_TEXT_FMT = (
    "%(asctime)s %(levelname)s %(name)s "
    "call=%(call_id)s turn=%(turn_id)s state=%(state)s %(message)s"
)


def redact(text: str) -> str:
    """Remove phone numbers, email addresses and SIP URI user parts."""
    text = _SIP_RE.sub("sip:[REDACTED]@", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return _PHONE_RE.sub("[REDACTED_PHONE]", text)


@contextlib.contextmanager
def log_context(
    call_id: str | None = None,
    turn_id: int | None = None,
    state: str | None = None,
) -> Iterator[None]:
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []
    if call_id is not None:
        tokens.append((CALL_ID, CALL_ID.set(call_id)))
    if turn_id is not None:
        tokens.append((TURN_ID, TURN_ID.set(turn_id)))
    if state is not None:
        tokens.append((STATE, STATE.set(state)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.call_id = CALL_ID.get() or "-"
        record.turn_id = TURN_ID.get() if TURN_ID.get() is not None else "-"
        record.state = STATE.get() or "-"
        return True


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _CONTEXT_KEYS:
            payload[key] = getattr(record, key, "-")
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key in payload:
                continue
            payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(cfg: LoggingCfg) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactionFilter())
    if cfg.format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FMT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(cfg.level)
```

### `src/eureka/db.py`

```python
"""SQLite persistence (plan M1.4).

One worker thread owns the connection, so writes never block the event loop
and never run concurrently.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")
SECONDS_PER_DAY = 86_400.0

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE calls (
        id                TEXT PRIMARY KEY,
        started_at        REAL NOT NULL,
        ended_at          REAL,
        end_reason        TEXT,
        prompt_version    TEXT NOT NULL,
        guardrail_version TEXT NOT NULL,
        input_tokens      INTEGER NOT NULL DEFAULT 0,
        output_tokens     INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX calls_expiry ON calls (COALESCE(ended_at, started_at));

    CREATE TABLE guardrail_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id    TEXT NOT NULL REFERENCES calls (id),
        turn_id    INTEGER NOT NULL,
        stage      TEXT NOT NULL,
        verdict    TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE feedback (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id         TEXT NOT NULL REFERENCES calls (id),
        rating          INTEGER NOT NULL CHECK (rating IN (-1, 1)),
        comment_snippet TEXT,
        created_at      REAL NOT NULL
    );
    """,
)


def _migrate(conn: sqlite3.Connection) -> None:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"database schema version {current} is newer than this build ({len(MIGRATIONS)})"
        )
    for version in range(current + 1, len(MIGRATIONS) + 1):
        script = MIGRATIONS[version - 1]
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _ts(now: float | None) -> float:
    return time.time() if now is None else now


class Database:
    """Async facade over sqlite3. Every query runs on the single worker thread."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eureka-db")
        self._conn: sqlite3.Connection | None = None

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self._conn = await loop.run_in_executor(self._executor, self._open_sync, self._path)

    @staticmethod
    def _open_sync(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            _migrate(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await self._offload(conn.close)
        self._executor.shutdown(wait=False)

    async def _offload(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(fn, *args))

    async def _query(self, fn: Callable[..., T], *args: Any) -> T:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return await self._offload(fn, self._conn, *args)

    # ---- public API -------------------------------------------------------

    async def start_call(
        self,
        call_id: str,
        *,
        prompt_version: str,
        guardrail_version: str,
        now: float | None = None,
    ) -> None:
        await self._query(
            _insert_call, call_id, prompt_version, guardrail_version, _ts(now)
        )

    async def finish_call(
        self,
        call_id: str,
        *,
        end_reason: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        now: float | None = None,
    ) -> bool:
        """Returns False if the call was already finished (idempotent)."""
        return await self._query(
            _finish_call, call_id, end_reason, input_tokens, output_tokens, _ts(now)
        )

    async def record_feedback(
        self,
        call_id: str,
        *,
        rating: int,
        comment_snippet: str | None = None,
        now: float | None = None,
    ) -> None:
        if rating not in (-1, 1):
            raise ValueError("rating must be -1 or 1")
        await self._query(_insert_feedback, call_id, rating, comment_snippet, _ts(now))

    async def record_guardrail_event(
        self,
        call_id: str,
        *,
        turn_id: int,
        stage: str,
        verdict: str,
        now: float | None = None,
    ) -> None:
        await self._query(_insert_guardrail, call_id, turn_id, stage, verdict, _ts(now))

    async def get_call(self, call_id: str) -> dict[str, Any] | None:
        return await self._query(_select_call, call_id)

    async def close_orphaned_calls(self, now: float | None = None) -> int:
        """Close calls left open by a crash or kill. Run once at startup."""
        return await self._query(_close_orphans, _ts(now))

    async def purge_expired(self, retention_days: int, now: float | None = None) -> int:
        """Delete calls (and their child rows) older than the retention window."""
        cutoff = _ts(now) - retention_days * SECONDS_PER_DAY
        return await self._query(_purge, cutoff)


# ---- sync helpers, executed on the worker thread ---------------------------

def _insert_call(
    conn: sqlite3.Connection, call_id: str, prompt_version: str, guardrail_version: str, ts: float
) -> None:
    conn.execute(
        "INSERT INTO calls (id, started_at, prompt_version, guardrail_version) VALUES (?, ?, ?, ?)",
        (call_id, ts, prompt_version, guardrail_version),
    )


def _finish_call(
    conn: sqlite3.Connection,
    call_id: str,
    end_reason: str,
    input_tokens: int,
    output_tokens: int,
    ts: float,
) -> bool:
    cur = conn.execute(
        "UPDATE calls SET ended_at = ?, end_reason = ?, input_tokens = ?, output_tokens = ? "
        "WHERE id = ? AND ended_at IS NULL",
        (ts, end_reason, input_tokens, output_tokens, call_id),
    )
    return cur.rowcount == 1


def _insert_feedback(
    conn: sqlite3.Connection, call_id: str, rating: int, snippet: str | None, ts: float
) -> None:
    conn.execute(
        "INSERT INTO feedback (call_id, rating, comment_snippet, created_at) VALUES (?, ?, ?, ?)",
        (call_id, rating, snippet, ts),
    )


def _insert_guardrail(
    conn: sqlite3.Connection, call_id: str, turn_id: int, stage: str, verdict: str, ts: float
) -> None:
    conn.execute(
        "INSERT INTO guardrail_events (call_id, turn_id, stage, verdict, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (call_id, turn_id, stage, verdict, ts),
    )


def _select_call(conn: sqlite3.Connection, call_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    return dict(row) if row is not None else None


def _close_orphans(conn: sqlite3.Connection, ts: float) -> int:
    cur = conn.execute(
        "UPDATE calls SET ended_at = ?, end_reason = 'service_restart' WHERE ended_at IS NULL",
        (ts,),
    )
    return cur.rowcount


def _purge(conn: sqlite3.Connection, cutoff: float) -> int:
    expired = "SELECT id FROM calls WHERE COALESCE(ended_at, started_at) < ?"
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"DELETE FROM feedback WHERE call_id IN ({expired})", (cutoff,))
        conn.execute(f"DELETE FROM guardrail_events WHERE call_id IN ({expired})", (cutoff,))
        cur = conn.execute(
            "DELETE FROM calls WHERE COALESCE(ended_at, started_at) < ?", (cutoff,)
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return cur.rowcount
```

### `src/eureka/health.py`

```python
"""Localhost-only health endpoint (plan M1.5)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("eureka.health")

_IO_TIMEOUT_S = 5.0
_MAX_HEADER_LINES = 64
_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    431: "Request Header Fields Too Large",
    503: "Service Unavailable",
}


class HealthServer:
    """Serves GET /health. Returns 200 only while the supervisor is RUNNING."""

    def __init__(
        self,
        host: str,
        port: int,
        status: Callable[[], dict[str, Any]],
    ) -> None:
        self._host = host
        self._port = port
        self._status = status
        self._server: asyncio.Server | None = None
        self.bound_port: int | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle, self._host, self._port)
        self.bound_port = self._server.sockets[0].getsockname()[1]
        log.info("health endpoint listening", extra={"host": self._host, "port": self.bound_port})

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), timeout=2.0)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                status, body = await asyncio.wait_for(self._route(reader), _IO_TIMEOUT_S)
            except (TimeoutError, ConnectionError, ValueError):
                status, body = 400, {"error": "bad request"}
            writer.write(_render(status, body))
            await writer.drain()
        except (ConnectionError, OSError):
            pass  # client went away; nothing to do
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _route(self, reader: asyncio.StreamReader) -> tuple[int, dict[str, Any]]:
        request_line = await reader.readline()
        if not request_line:
            raise ConnectionError("client closed before sending a request")
        for _ in range(_MAX_HEADER_LINES):
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        else:
            return 431, {"error": "headers too large"}

        parts = request_line.decode("ascii", errors="replace").split()
        if len(parts) != 3:
            return 400, {"error": "malformed request line"}
        method, target, _version = parts
        if method != "GET":
            return 405, {"error": "method not allowed"}
        if target.split("?", 1)[0] != "/health":
            return 404, {"error": "not found"}

        snapshot = self._status()
        code = 200 if snapshot.get("state") == "running" else 503
        return code, snapshot


def _render(status: int, body: dict[str, Any]) -> bytes:
    payload = json.dumps(body).encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {_REASONS.get(status, 'Unknown')}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    return head + payload
```

### `src/eureka/supervisor.py`

```python
"""Process lifecycle: startup, signal handling, tracked tasks, graceful drain (plan M1.5, M8.7).

Lifecycle:  STARTING -> RUNNING -> DRAINING -> STOPPED

Session tasks (Phase 2 onward) are created with `spawn()`. Shutdown waits up to
`shutdown_grace_s` for them to finish, then cancels whatever is left.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Coroutine
from enum import Enum
from typing import Any

from eureka.config import Settings
from eureka.db import Database
from eureka.health import HealthServer

log = logging.getLogger("eureka.supervisor")

EXIT_OK = 0
EXIT_FAILURE = 1


class State(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"


class Supervisor:
    def __init__(self, settings: Settings, db: Database) -> None:
        svc = settings.eureka.service
        self._db = db
        self._grace_s = svc.shutdown_grace_s
        self._retention_days = settings.eureka.storage.retention_days
        self._state = State.STARTING
        self._stop_event = asyncio.Event()
        self._stop_reason = "unset"
        self._tasks: set[asyncio.Task[Any]] = set()
        self._started_at: float | None = None
        self._installed_signals: list[signal.Signals] = []
        self._health = HealthServer(svc.health_host, svc.health_port, self.snapshot)

    @property
    def state(self) -> State:
        return self._state

    def snapshot(self) -> dict[str, Any]:
        uptime = 0.0 if self._started_at is None else time.monotonic() - self._started_at
        return {
            "state": self._state.value,
            "uptime_s": round(uptime, 1),
            "active_tasks": len(self._tasks),
        }

    def request_stop(self, reason: str = "requested") -> None:
        if self._stop_event.is_set():
            return
        self._stop_reason = reason
        log.info("stop requested", extra={"reason": reason})
        self._stop_event.set()

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        """Start a tracked task. Refused unless the service is RUNNING."""
        if self._state is not State.RUNNING:
            coro.close()
            raise RuntimeError(f"cannot spawn {name!r} while {self._state.value}")
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("tracked task failed", extra={"task": task.get_name()}, exc_info=exc)

    async def run(self) -> int:
        self._install_signal_handlers()
        exit_code = EXIT_OK
        try:
            await self._start()
            await self._stop_event.wait()
        except Exception:
            log.exception("fatal error during startup or run")
            exit_code = EXIT_FAILURE
        finally:
            await self._shutdown()
            self._remove_signal_handlers()
        return exit_code

    async def _start(self) -> None:
        await self._db.open()
        orphans = await self._db.close_orphaned_calls()
        if orphans:
            log.warning("closed calls left open by a previous run", extra={"count": orphans})
        purged = await self._db.purge_expired(self._retention_days)
        log.info("retention purge complete", extra={"rows_purged": purged})

        await self._health.start()
        self._started_at = time.monotonic()
        self._state = State.RUNNING
        log.info("eureka running", extra={"health_port": self._health.bound_port})

    async def _shutdown(self) -> None:
        if self._state is State.STOPPED:
            return
        self._state = State.DRAINING
        log.info(
            "draining",
            extra={
                "active_tasks": len(self._tasks),
                "grace_s": self._grace_s,
                "reason": self._stop_reason,
            },
        )
        if self._tasks:
            _, pending = await asyncio.wait(set(self._tasks), timeout=self._grace_s)
            if pending:
                log.warning("grace period expired; cancelling tasks", extra={"count": len(pending)})
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        # Health stays up during drain (returns 503), so monitoring sees "draining".
        await self._health.stop()
        await self._db.close()
        self._state = State.STOPPED
        log.info("stopped")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop, sig.name)
            except (NotImplementedError, RuntimeError, ValueError):
                # Not the main thread, or no signal support (e.g. Windows).
                continue
            self._installed_signals.append(sig)

    def _remove_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in self._installed_signals:
            loop.remove_signal_handler(sig)
        self._installed_signals.clear()
```

### `src/eureka/__main__.py`

```python
"""Entry point: `python -m eureka` or the `eureka` console script."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from eureka.config import ConfigError, load_settings
from eureka.db import Database
from eureka.logging_setup import configure_logging
from eureka.supervisor import Supervisor

EX_CONFIG = 78  # sysexits.h EX_CONFIG


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eureka", description="Eureka payphone assistant")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="optional .env for development; systemd uses EnvironmentFile=",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(args.config_dir, args.env_file)
    except ConfigError as exc:
        print(f"eureka: configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG

    configure_logging(settings.eureka.logging)
    db = Database(settings.eureka.storage.db_path)
    supervisor = Supervisor(settings, db)
    return asyncio.run(supervisor.run())


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
```

### `systemd/eureka.service`

```ini
[Unit]
Description=Eureka payphone voice assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=eureka
Group=eureka
# Placeholder path. Set to the real install location.
WorkingDirectory=/opt/eureka
EnvironmentFile=/opt/eureka/.env
ExecStart=/opt/eureka/venv/bin/eureka --config-dir /opt/eureka/config
KillSignal=SIGTERM
TimeoutStopSec=15
Restart=on-failure
RestartSec=3

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/opt/eureka/data
LimitNOFILE=256
MemoryMax=512M

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

### Tests

### `tests/test_config.py`

```python
import os
import textwrap
from pathlib import Path

import pytest

from eureka.config import ConfigError, load_settings

VALID = """
service:
  health_host: 127.0.0.1
  health_port: 8088
  shutdown_grace_s: 5
storage:
  db_path: data/test.sqlite3
  retention_days: 14
logging:
  level: INFO
  format: json
"""


def write_config(directory: Path, eureka_yaml: str) -> None:
    (directory / "eureka.yaml").write_text(textwrap.dedent(eureka_yaml), encoding="utf-8")
    (directory / "guardrails.yaml").write_text("version: 1\n", encoding="utf-8")


def test_valid_config_loads(tmp_path):
    write_config(tmp_path, VALID)
    settings = load_settings(tmp_path)
    assert settings.eureka.service.health_port == 8088
    assert settings.guardrails.version == 1


def test_missing_section_fails(tmp_path):
    write_config(tmp_path, "service: {health_host: 127.0.0.1}\n")
    with pytest.raises(ConfigError, match="storage"):
        load_settings(tmp_path)


def test_non_loopback_health_host_rejected(tmp_path):
    write_config(tmp_path, VALID.replace("127.0.0.1", "0.0.0.0"))
    with pytest.raises(ConfigError, match="loopback"):
        load_settings(tmp_path)


def test_bad_type_rejected(tmp_path):
    write_config(tmp_path, VALID.replace("shutdown_grace_s: 5", "shutdown_grace_s: soon"))
    with pytest.raises(ConfigError, match="shutdown_grace_s"):
        load_settings(tmp_path)


def test_unknown_key_rejected(tmp_path):
    write_config(tmp_path, VALID + "bogus: 1\n")
    with pytest.raises(ConfigError, match="bogus"):
        load_settings(tmp_path)


def test_dotenv_does_not_override_real_environment(tmp_path, monkeypatch):
    write_config(tmp_path, VALID)
    env_file = tmp_path / ".env"
    env_file.write_text("EUREKA_TEST_KEY=from_file\nEUREKA_NEW_KEY=new\n", encoding="utf-8")
    monkeypatch.setenv("EUREKA_TEST_KEY", "from_env")
    # Set then delete so monkeypatch restores the original (absent) state afterwards.
    monkeypatch.setenv("EUREKA_NEW_KEY", "placeholder")
    monkeypatch.delenv("EUREKA_NEW_KEY")

    load_settings(tmp_path, env_file)

    assert os.environ["EUREKA_TEST_KEY"] == "from_env"
    assert os.environ["EUREKA_NEW_KEY"] == "new"
```

### `tests/test_redaction.py`

```python
import pytest

from eureka.logging_setup import redact


@pytest.mark.parametrize(
    "number",
    ["+1 (555) 123-4567", "555-123-4567", "5551234567", "1-555-123-4567"],
)
def test_phone_numbers_redacted(number):
    out = redact(f"caller {number} dialed")
    assert number not in out
    assert "[REDACTED_PHONE]" in out


def test_email_redacted():
    assert "bob@example.com" not in redact("mail bob@example.com now")


def test_sip_user_part_redacted():
    out = redact("from sip:5551234567@voip.example")
    assert "5551234567" not in out


def test_non_pii_untouched():
    text = "turn 3 state=LISTENING latency 1200ms epoch 1700000000000"
    assert redact(text) == text
```

### `tests/test_db.py`

```python
import pytest

from eureka.db import Database

DAY = 86_400.0


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    await database.open()
    yield database
    await database.close()


async def test_call_lifecycle_and_idempotent_finish(db):
    await db.start_call("c1", prompt_version="p1", guardrail_version="g1", now=1000.0)
    assert await db.finish_call("c1", end_reason="hangup", input_tokens=10,
                                output_tokens=5, now=1100.0) is True
    assert await db.finish_call("c1", end_reason="again", now=1200.0) is False

    row = await db.get_call("c1")
    assert row["end_reason"] == "hangup"
    assert row["ended_at"] == 1100.0
    assert row["input_tokens"] == 10


async def test_feedback_rejects_invalid_rating(db):
    await db.start_call("c1", prompt_version="p1", guardrail_version="g1", now=0.0)
    with pytest.raises(ValueError):
        await db.record_feedback("c1", rating=0)


async def test_purge_respects_retention_and_cascades(db):
    await db.start_call("old", prompt_version="p1", guardrail_version="g1", now=0.0)
    await db.finish_call("old", end_reason="hangup", now=10.0)
    await db.record_feedback("old", rating=1, now=11.0)

    await db.start_call("new", prompt_version="p1", guardrail_version="g1", now=20 * DAY)
    await db.finish_call("new", end_reason="hangup", now=20 * DAY + 5)

    removed = await db.purge_expired(retention_days=14, now=20 * DAY)

    assert removed == 1
    assert await db.get_call("old") is None
    assert await db.get_call("new") is not None


async def test_orphaned_calls_closed_on_startup(db):
    await db.start_call("crashed", prompt_version="p1", guardrail_version="g1", now=100.0)
    assert await db.close_orphaned_calls(now=200.0) == 1
    row = await db.get_call("crashed")
    assert row["end_reason"] == "service_restart"
    assert row["ended_at"] == 200.0


async def test_reopen_does_not_rerun_migrations(tmp_path):
    path = tmp_path / "test.sqlite3"
    first = Database(path)
    await first.open()
    await first.close()

    second = Database(path)
    await second.open()
    await second.close()
```

### `tests/test_health.py`

```python
import asyncio
import json

from eureka.health import HealthServer


async def _get(port: int, path: str, method: str = "GET") -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: test\r\n\r\n".encode("ascii"))
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split()[1])
    return status, json.loads(body) if body else {}


async def test_health_reflects_state_and_routes():
    state = {"state": "running"}
    server = HealthServer("127.0.0.1", 0, lambda: dict(state))
    await server.start()
    try:
        assert (await _get(server.bound_port, "/health"))[0] == 200
        assert (await _get(server.bound_port, "/health?verbose=1"))[0] == 200

        state["state"] = "draining"
        code, body = await _get(server.bound_port, "/health")
        assert code == 503
        assert body["state"] == "draining"

        assert (await _get(server.bound_port, "/nope"))[0] == 404
        assert (await _get(server.bound_port, "/health", method="POST"))[0] == 405
    finally:
        await server.stop()
```

### `tests/test_supervisor.py`

```python
import asyncio
import signal
import socket
import sys

import pytest

from eureka.config import EurekaConfig, GuardrailsCfg, Settings
from eureka.db import Database
from eureka.supervisor import State, Supervisor


def make_settings(tmp_path, *, port: int = 0, grace: float = 0.2) -> Settings:
    eureka = EurekaConfig.model_validate({
        "service": {"health_host": "127.0.0.1", "health_port": port, "shutdown_grace_s": grace},
        "storage": {"db_path": str(tmp_path / "eureka.sqlite3"), "retention_days": 14},
    })
    return Settings(eureka=eureka, guardrails=GuardrailsCfg(version=1))


async def wait_for_state(sup: Supervisor, state: State, timeout: float = 5.0) -> None:
    async def poll() -> None:
        while sup.state is not state:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), timeout)


async def test_clean_stop_exits_zero(tmp_path):
    sup = Supervisor(make_settings(tmp_path), Database(tmp_path / "db.sqlite3"))
    run = asyncio.create_task(sup.run())
    await wait_for_state(sup, State.RUNNING)

    sup.request_stop("test")

    assert await asyncio.wait_for(run, 5) == 0
    assert sup.state is State.STOPPED


async def test_stuck_task_is_cancelled_after_grace(tmp_path):
    sup = Supervisor(make_settings(tmp_path, grace=0.1), Database(tmp_path / "db.sqlite3"))
    run = asyncio.create_task(sup.run())
    await wait_for_state(sup, State.RUNNING)

    started = asyncio.Event()

    async def stuck() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = sup.spawn(stuck(), name="stuck")
    await started.wait()

    sup.request_stop("test")

    assert await asyncio.wait_for(run, 5) == 0
    assert task.cancelled()


async def test_spawn_refused_when_not_running(tmp_path):
    sup = Supervisor(make_settings(tmp_path), Database(tmp_path / "db.sqlite3"))
    with pytest.raises(RuntimeError):
        sup.spawn(asyncio.sleep(0), name="too-early")


async def test_startup_failure_returns_nonzero(tmp_path):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    try:
        sup = Supervisor(make_settings(tmp_path, port=port), Database(tmp_path / "db.sqlite3"))
        assert await asyncio.wait_for(sup.run(), 5) == 1
        assert sup.state is State.STOPPED
    finally:
        blocker.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
async def test_sigterm_triggers_clean_exit(tmp_path):
    import os

    sup = Supervisor(make_settings(tmp_path), Database(tmp_path / "db.sqlite3"))
    run = asyncio.create_task(sup.run())
    await wait_for_state(sup, State.RUNNING)

    os.kill(os.getpid(), signal.SIGTERM)

    assert await asyncio.wait_for(run, 5) == 0
```

---

### Verification (plan M1.1–M1.6)

```bash
# M1.1: install and check the toolchain
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q

# M1.2: config errors are clear and exit 78
python -m eureka --config-dir /tmp/empty; echo "exit=$?"

# M1.5: run on staging, then probe
python -m eureka --config-dir config &
curl -s http://127.0.0.1:8088/health          # expect 200, state "running"
ss -ltn | grep 8088                           # expect 127.0.0.1:8088 only
kill -TERM %1; wait %1; echo "exit=$?"        # expect exit=0 within 5 s

# M1.6: unit file on staging
systemd-analyze verify systemd/eureka.service
sudo systemctl start eureka && sudo systemctl kill -s SIGKILL eureka
sleep 5 && systemctl is-active eureka         # expect "active" (Restart=on-failure)
```

### Not covered yet, and open items

- **ARI:** not implemented, see the scope note above. The AudioSocket listener and `spawn()` integration land in M2.2.
- **Dependency choices:** pydantic is added for config validation. The context file listed it as proposed, so confirm it's acceptable.
- **Health port 8088** and the `MemoryMax`/`LimitNOFILE` values are placeholders to tune on staging.
- **Migration 1** is a single schema version. Later schema changes should be added as new `MIGRATIONS` entries, never by editing this one.
- **Retention purge** runs at startup only. A periodic job can be added in M6.8.
