# Phase 1 baseline and blocker

**Recorded:** 2026-11-14 UTC

## File-level observations

- `/etc/asterisk/extensions.conf` is a 479-byte configuration containing only the `from-voipms` and `from-internal` contexts. Its `from-internal` extensions are `_1NXXNXXXXXX` and `_NXXNXXXXXX`; no static `0` extension or include was found.
- `/etc/asterisk/pjsip.conf` exists but is readable only by the `asterisk` user. It was not changed.
- Asterisk is running as the `asterisk` user. Its control socket is `/run/asterisk/asterisk.ctl`, owned by `asterisk:asterisk` and not accessible to the deployment user.
- The project has pre-existing timestamped backups of both configuration files under `.asterisk-backups/`; this task did not modify either file.
- No readable HT802 reference was found under `/etc/asterisk`.

## Live verification

An authorized Asterisk operator completed the required live checks on 2026-11-14:

- Asterisk is `18.10.0~dfsg+~cs6.10.40431411-2`.
- `app_audiosocket.so`, `chan_audiosocket.so`, and `res_audiosocket.so` are loaded and running.
- `dialplan show 0` reported no existing extension.
- The active `from-internal` context contains only the two existing ten- and eleven-digit outbound patterns.
- Baseline checksums are `extensions.conf`: `7fa75dc8f105fb94daedad0fa31235bf105b9a9f30c915d0597c4384f0e61112`; `pjsip.conf`: `684c17d20d0f2262571f8b2a3997c37506189472d0b019b85b43152b84e4625e`.

This confirms that `0` is unused and AudioSocket is available. The deployment user still cannot perform privileged changes non-interactively, so no Asterisk configuration has been changed by this task and `pjsip.conf` remains untouched.

## Required controlled-host application

On the controlled host, the authorized operator must create a fresh backup, install the repository's additive context, include that file at configuration scope, route the existing internal context to it, and reload the dialplan:

```sh
sudo install -d -m 0755 /etc/asterisk/eureka-backups
sudo cp -a /etc/asterisk/extensions.conf /etc/asterisk/eureka-backups/extensions.conf.$(date -u +%Y%m%dT%H%M%SZ).bak
sudo install -o root -g root -m 0644 deploy/asterisk/eureka-extensions.conf /etc/asterisk/eureka-extensions.conf
```

The required additive lines are configuration-specific: `#include "eureka-extensions.conf"` must be at configuration scope (before the first context), and `include => eureka-operator` must be inside `[from-internal]`. The controlled procedure should make those two changes once, then run:

```sh
sudo asterisk -rx 'dialplan reload'
sudo asterisk -rx 'dialplan show 0'
```

`deploy/asterisk/eureka-extensions.conf` defines only `eureka-operator,0`: it answers, generates a UUID, opens `AudioSocket()` to `127.0.0.1:9092`, and hangs up. It does not change the existing dialplan, provider path, or `pjsip.conf`.

The operator must use the Asterisk version's documented `AudioSocket()` argument syntax if it differs, then validate the route and controlled calls. Do not change `pjsip.conf`.

## Rollback

Remove only the added include, `0` route, and `/etc/asterisk/eureka-extensions.conf`; run `asterisk -rx 'dialplan reload'`; then verify `dialplan show 0` and regression-test existing internal, VoIP.ms inbound, and VoIP.ms outbound calls. Restore the fresh pre-change backup only if the additive rollback cannot restore the baseline.

## Controlled validation results

- The additive `eureka-operator` context loaded with extension `0` and the `Answer`, UUID, `AudioSocket(127.0.0.1:9092)`, and `Hangup` priorities.
- `from-internal` retains its two pre-existing outbound patterns and includes `eureka-operator`.
- A controlled dial of `0` interrupted dial tone, entered the new route, then returned busy. This was initially attributed to the absent Phase 2 service; subsequent testing revealed that the original `UUID()` dialplan function was not registered, so this observation did not validate AudioSocket connectivity.
- Controlled existing outbound and VoIP.ms inbound/outbound regression calls passed.

## Status

The additive route and regression checks were completed. On 2026-11-15, the operator confirmed that `RAND()` and `SPRINTF()` were registered, installed the corrected route, and confirmed dial 0 reached the greeting. `pjsip.conf` was not changed.
