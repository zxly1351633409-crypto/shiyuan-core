# Privacy model

The public repository contains no user profile, conversation archive, device
path, account identifier, service token or production database.

At runtime, Shiyuan Core can store:

- candidate and confirmed memories;
- visible user/assistant history explicitly captured by a connector;
- task cards, checkpoints and compact work receipts;
- human-readable confirmed memory files in the local Vault;
- an optional local semantic index.

It deliberately does not require model-private reasoning, hidden prompts,
clipboard contents, screenshots or arbitrary file contents. History snippets
are marked as untrusted reference material before they are returned to an
Agent.

Data capture is opt-in at each connector. Set `capture_messages` or
`captureMessages` to `false` when a device may read the Core but must not send
conversation text. The company-safe package is a separate local-only mode and
ships with an empty memory snapshot.

There is no built-in telemetry or hosted control plane in this repository.
