# Release acceptance

Run from the repository root, with Python 3.11+, uv, git, gh, ffmpeg and ffprobe.
macOS uses Homebrew Python and sandbox-exec. Linux CI uses the host venv without
the macOS sandbox. The ordinary pytest invocation collects only `tests/`.

```sh
acceptance/run.sh --host upstream --plugin <ref> --out <empty-output-dir> --layers a
acceptance/run.sh --host evaos --plugin <ref> --out <another-empty-dir> --layers a
```

`--hermes-ref` overrides the runtime pin; `--port` overrides 9361/9362.
The upstream release resolves at run time. Runtime and candidate commit identities
are in `host.json`; evaOS Desktop resolves the newest app tag independently.
Host archives and venvs are cached under `~/.cache/fish-audio-acceptance/`.
Only the cache clone is fetched; existing evaOS and plugin checkouts are read-only.

`with-keys.sh` is the sole local key reader. CI supplies environment secrets.
No real Fish key is entered through a form: it is written into the disposable
home by file, mode 0600. The session-token file is also mode 0600. Keep run
directories private: they contain disposable credentials and synthetic sessions.
Only fingerprints enter the key receipt. Never upload `.env`, token files,
state.db, host logs or entire run directories.

Each marked pytest row writes `rows-a.json`; `report.py` merges selected layers
against `rows.yaml` (JSON-compatible YAML). Missing required rows fail. Unavailable
host/version features need an explicit reason. Only `no LLM key` and
`billed:heavy not enabled` are allowed skips. Gate failures return exit 1.

A failure caused by a filed host bug is listed under the row's `known_host_bugs`,
pinned to the exact host builds (`refs`) it was seen on. On those builds it reports
`xfail` and does not block. Any other build re-arms the row, so every host release is
checked again. When the row passes on a listed build, the report asks for the entry to
be dropped, unless the entry says `"intermittent": true`. Rows with `"required": false`
are advisory: their failures report `advisory-fail` and never block.

Fixtures for the report gate run separately:

```sh
<cached-host>/.venv/bin/python -m pytest -q acceptance/test_report.py
```

Run one acceptance run at a time per Fish key. Accounts with less than $100 of top-ups get 5 concurrent
requests, and parallel runs (or a second lane on the same key) trip "concurrency limit" errors. A row that fails
on a Fish outage, a rate limit or the concurrency limit is retried once, 15 s later, and the report says so.

Budget is $0.30 by default, checked against the real wallet before and after each
paid probe; a spent budget refuses subsequent calls. It is a measured spending
guard, not a provider-side hard billing limit. Account credits can settle late.
`--billed-heavy` enables design/save/delete and synthetic clone/delete. Those
probes delete only voices they create. Clone needs macOS `say`; Linux reports
failure if heavy cloning is explicitly requested without it.

`hostctl.sh --run-dir <out> up|down|restart-gateway|set-real-key|set-fake-key|clear-key|wallet`
supports the Desktop driver. `set-real-key` requires FISH_API_KEY in its environment;
use `with-keys.sh` when calling it manually. Gateway shutdown signals only the
recorded process group after verifying its run-dir command identity.

`--layers a,b` also runs Layer B, `acceptance/desktop/run.mjs`; absent B rows fail.
Layer B is macOS only. It builds a Hermes Desktop dev tree for the host's release (or
the newest evaOS app tag) into the cache, with Node 24 from `/opt/homebrew/bin` or
`ACCEPTANCE_NODE_BIN`. It drives the app over CDP in both themes. The patches it applies
to the cached tree are listed in `<tree>/.acceptance-patches`: evaOS's managed-build
switch off, and the microphone permission prompt answered "granted" only when
`HERMES_ACCEPTANCE_FAKE_MIC=1`. Speech goes in through a microphone shim that plays
`say`-generated audio into `getUserMedia`, so no microphone or TCC prompt is needed.
Conversation rows need an LLM key: `with-keys.sh` supplies the GLM key.
The optional PCS overlay is not implemented: requesting it fails setup explicitly.
Release-script consumption remains external to this public repository.

Reports prove named host builds through real entry points in a throwaway profile.
They do not prove packaged/signed apps, customers, broker/relay or Intel hardware.
