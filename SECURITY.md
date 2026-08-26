# Security policy

## Scope

This repository implements an application-layer BLE protocol for the Pidzoom
Portable Charger HW178P. It contains a documented application credential
fixture because an independent client cannot perform the recovered handshake
without it. That value is a bearer credential for the sampled protocol route;
it is not a manufacturer password, proof of a blank password, or proof that
BLE link-layer pairing is disabled.

Anyone who can reach a charger over Bluetooth and obtain the credential may be
able to issue control commands. Treat the credential, device identifiers,
serial numbers, and diagnostic logs as sensitive. Do not copy them into issue
reports or telemetry systems.

## Reporting a vulnerability

Please report security issues privately to the repository owner before opening
a public issue. After this repository is published, use the planned
[GitHub private vulnerability reporting page](https://github.com/weisscorp/hwcdq-protocol/security/advisories/new);
if private reporting is not enabled, contact the owner through the GitHub
profile before opening an issue. Include a concise description, affected
component, impact, reproduction steps that do not transmit arbitrary values to
live hardware, and any proposed mitigation. Do not attach raw BLE logs or
APK/vendor binaries; offer a redacted hash and a small synthetic reproducer
instead.

This project cannot provide a security response for the charger manufacturer,
the Android application, or a particular hardware revision. If an issue is in
the device firmware or vendor service, contact the manufacturer as well.

## Safe-use boundaries

- Do not brute-force credentials or fuzz unknown opcodes.
- Do not bypass the profile limits or the session safety gates.
- Do not operate a charger unattended.
- Assume firmware revisions may change the packet contract.
- Treat an acknowledgement without readback as an ambiguous outcome.
