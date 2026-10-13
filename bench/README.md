# Weekly regression alarm

`python bench/weekly.py --dry-run` uses fakes without a key, network or ffmpeg.
Live CI runs weekly on Wednesday at 06:41 UTC, or by manual dispatch. The owner sets
`FISH_API_KEY`; only the benchmark step receives it. Caps: 5,000 TTS characters and
600 billed ASR seconds, including retries, and $0.10 per run; calls crossing a cap are refused.
TTS prices per million UTF-8 bytes: s2.1-pro $15, Drama 3 **estimated** $15 (no listed
price), s2.1-pro-free $0; both transcribe-1 models cost $0.36/ASR hour.
New gates: Drama first audio (3-call median; warn >1200 ms, fail >2500 ms), spoken tag
words (max of 2 runs; warn >0, fail >1), non-empty free-model audio, transcribe-1 MP3
roundtrip (warn >.03, fail >.06 error rate), and paced live caption lag (20 ms frames,
250 ms polling; warn >2000 ms, fail >4000 ms). The final live transcript still gets scored.
Positive control: `--tag-model s2.1-pro` must fail `drama-tags` (also supported by fakes).
`--history DIR` reads earlier `results.json` files recursively: timing passes exceeding
1.5x the median of the last 2–4 runs warn; malformed history is ignored. CI fetches four
previous artifacts and retains results for 90 days. Results include versions, models and caps.
Warnings/failures update one `fish-bench` issue; passes only write the job summary.
This measures runner regressions on synthetic speech, not user latency or naturalness.
The directory ships with the plugin's source tree (about 300 KB of clips) but the plugin never imports it.
