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
Fixtures for the report gate run separately:

```sh
<cached-host>/.venv/bin/python -m pytest -q acceptance/test_report.py
```

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

`--layers a,b` calls `acceptance/desktop/run.mjs` if supplied; absent B rows fail.
The optional PCS overlay is not implemented: requesting it fails setup explicitly.
Release-script consumption remains external to this public repository.

Reports prove named host builds through real entry points in a throwaway profile.
They do not prove packaged/signed apps, customers, broker/relay or Intel hardware.
