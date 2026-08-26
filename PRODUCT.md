# Pidzoom Portable charger HW178P — Product Definition

## Product

Pidzoom Portable charger HW178P is a native macOS utility for inspecting and
safely controlling the owner's HW178P charger over Bluetooth Low Energy. It
turns the reverse-engineered HWCDQ protocol into a visible, testable instrument
without depending on the vendor Android application, an Alipay mini-app, or a
cloud service.

The HW178P application profile accepts target voltage only from `50.00` through
`178.00 V` and target current only from `0.01` through `14.00 A`. A narrower
valid maximum reported by the connected device always wins. The `50 V` model
minimum and model identity are owner-supplied; `0.01 A` is a conservative
positive application floor, not a hardware-proven minimum.

## Users

The primary user is the charger owner working at an engineering bench. They
understand voltage, current, electrical limits, and the consequences of
energizing an attached battery or load. The application is single-operator and
local-first; it is not a fleet-management product.

## Core purpose

- Discover and connect to a selected HWCDQ charger.
- Make the actual GATT topology and every protocol exchange observable.
- Receive and decode device identity, configuration, and live telemetry.
- Apply only protocol operations whose wire format is supported by evidence.
- Let the operator validate the reverse-engineered protocol incrementally and
  preserve useful, redacted evidence.
- Fail closed whenever topology, limits, authentication, or command outcome is
  ambiguous.

## Product personality

Precise, calm, legible, and trustworthy. The interface should feel like a
well-calibrated bench instrument: dense enough for engineering work, but never
noisy or theatrical. State, provenance, uncertainty, and safety interlocks are
always visible.

## Product principles

1. **Show the state, not an illusion.** Connected, authenticated, stale,
   awaiting response, outcome unknown, and disconnected are distinct states.
2. **Evidence before confidence.** Confirmed protocol facts are presented as
   facts; simulator behavior and unverified live assumptions are labelled.
3. **De-energizing wins within explicit control mode.** Stop has the highest
   mutation priority and no modal confirmation, but is enabled only after the
   operator opts into output controls and fresh telemetry explicitly reports
   the output ON.
4. **No silent action.** The application never reconnects and replays a
   state-changing command, starts charging automatically, or guesses a limit.
5. **Progressive disclosure.** Live readings and essential controls lead;
   topology, decoded fields, and raw frames remain one click away.
6. **Keyboard and color independence.** Every status has text or an icon in
   addition to color, and all essential actions are reachable by keyboard.

## Safety boundary

Monitoring mode may transmit only non-mutating authentication and read-request
frames. Voltage, current, Start, and Stop all require explicit command-line
opt-in. Stop additionally requires fresh telemetry explicitly reporting ON,
but does not depend on Start readiness or setpoint validity. Unknown,
calibration, factory, password-change, firmware-update, and arbitrary raw
commands are not exposed.

Setters and Start must satisfy both the HW178P application envelope and any
narrower valid maximum received from the device.

## Anti-references

- A browser or SaaS control panel.
- A decorative consumer charging app with gauges that hide exact values.
- A dark “hacker dashboard” with neon accents, glow, or gratuitous motion.
- Hidden automation, magic reconnect behavior, or optimistic success states.
- Color-only alarms and tiny low-contrast telemetry.
- A raw opcode console capable of transmitting unsupported commands.

## Success criteria

The operator can run the application without hardware in simulation, then use
the same interface to inspect a real charger, connect, authenticate, receive
telemetry, set evidence-backed voltage/current targets, start, and stop. The
application visibly distinguishes simulator evidence from observations made on
real hardware.
