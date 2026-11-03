# Contributing

AMADEUS is a non-commercial fan project maintained by one person. Issues are
welcome in Chinese, English or Japanese. Please open an issue to discuss a
change before sending a pull request, so we can agree on its scope.

For the development environment and check commands, follow
[Install and check in AGENTS.md](AGENTS.md#install-and-check). Read the
[repository rules](AGENTS.md#repository-rules) before changing code. Include the
checks you ran and any platform limits in your pull request; Linux checks do
not establish Windows acceptance.

A few rules apply to every contribution:

- Isolate development data. Before importing the backend outside pytest, set
  `AMADEUS_DATA_DIR` to a fresh temporary directory and leave `AMADEUS_DB_PATH`
  unset. Never use an installed app's data directory. Keep pytest's isolation
  in place.
- Use merge commits only when merging. Do not squash or rebase merges; the
  repository's blame configuration depends on the normalization commit ID.
- Do not add audio, voice model weights, images or original game materials.
- Do not commit secrets or personal email addresses, including in commit
  metadata. Use an official GitHub noreply address.
- Let Git handle line endings as described in `AGENTS.md`; do not bulk-convert
  files.

Report security vulnerabilities privately using [SECURITY.md](SECURITY.md).
