# Contributing to hwcdq-protocol

Thanks for helping document and implement the HWCDQ protocol. This is an
interoperability project for owner-operated Pidzoom HW178P hardware, not a
place to experiment with an unknown charger by trial and error.

## Before opening a change

- Read [`README.md`](README.md), [`docs/protocol.md`](docs/protocol.md), and
  [`SECURITY.md`](SECURITY.md).
- Keep changes scoped to the library, contract, documentation, or software
  tests. Do not include APKs, unpacked vendor binaries, raw diagnostic logs,
  private Bluetooth identifiers, or credentials other than the documented
  protocol fixture.
- Never fuzz unknown opcodes, bypass profile limits, or test a mutating command
  without explicit owner authorization and a safe electrical setup.

## Protocol changes

Every new protocol claim must include:

1. an evidence source and confidence label (`confirmed`, `high confidence`,
   `tentative`, or `unknown`);
2. a language-neutral vector under [`contract/v1`](contract/v1) when the field
   or packet is deterministic;
3. Python, Swift, and Kotlin conformance coverage when the shared contract is
   affected; and
4. an update to [`docs/commands.md`](docs/commands.md) or
   [`docs/protocol.md`](docs/protocol.md).

Do not silently replace a live capture with synthetic bytes. Synthetic layout
vectors must say so explicitly, and identity-bearing captures must be removed
from the public evidence rather than rewritten as if they were live.

## Software changes

The Python package is the canonical runtime. Keep the transport boundary
small, make mutations fail closed, and never replay an ambiguous mutating
write. Swift and Kotlin wrappers must continue to consume the same contract
vectors without introducing a dependency on Python or the desktop UI.

Run the relevant checks before submitting a change:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/validate_contract.py --check
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
cd native/swift && swift test
cd ../kotlin/hwcdq-protocol && ./gradlew test
```

The GitHub Actions workflow is software-only. It must not require a charger,
download the original APK, upload diagnostic logs, or print credentials.

## Pull requests

Describe the user-visible or protocol-visible effect, the evidence and
confidence behind it, and the checks you ran. Keep public examples read-only
unless the example is clearly marked as an advanced, safety-critical control
operation.
