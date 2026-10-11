# Weekly regression alarm

`python bench/weekly.py --dry-run` uses fakes without a key, network or ffmpeg.
Live CI runs weekly on Wednesday at 06:41 UTC, or by manual dispatch. The owner sets
`FISH_API_KEY`; only the benchmark step receives it. Caps: 5,000 TTS characters and
600 billed ASR seconds, including retries. Models: s2.1-pro and transcribe-1-pro.
Outputs are numeric gates and estimated USD ($15/million UTF-8 bytes, $0.36/ASR hour).
Warnings/failures update one `fish-bench` issue; passes only write the job summary.
This measures runner regressions on synthetic speech, not user latency or naturalness.
The directory ships with the plugin's source tree (about 300 KB of clips) but the plugin never imports it.
