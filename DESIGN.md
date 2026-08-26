---
name: Pidzoom Portable charger HW178P
description: A calm, exact bench instrument for observing and controlling a Pidzoom HW178P charger over the HWCDQ protocol.
colors:
  primary: "#00726B"
  primary-deep: "#00544E"
  background: "#FFFFFF"
  surface: "#F0F5F4"
  surface-strong: "#E1E8E7"
  ink: "#101A1C"
  muted: "#516164"
  border: "#C4CCCE"
  attention: "#C15800"
  danger: "#BE241F"
  success-pale: "#E4F4F1"
  attention-pale: "#FFF1E4"
  danger-pale: "#FCE9E7"
  selection: "#CDE8E4"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.2
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.35
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "13px"
    fontWeight: 650
    lineHeight: 1.25
  reading:
    fontFamily: "SF Mono, Menlo, monospace"
    fontSize: "25px"
    fontWeight: 650
    lineHeight: 1.15
  protocol:
    fontFamily: "SF Mono, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.35
rounded:
  sm: "4px"
  md: "5px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "18px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.background}"
    rounded: "{rounded.sm}"
    padding: "2px 12px"
    height: "30px"
  button-stop:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.background}"
    rounded: "{rounded.sm}"
    padding: "2px 18px"
    height: "38px"
  status-badge:
    backgroundColor: "{colors.surface-strong}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "5px 8px"
  input:
    backgroundColor: "{colors.background}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "1px 8px"
    height: "30px"
  reading:
    backgroundColor: "{colors.background}"
    textColor: "{colors.ink}"
    typography: "{typography.reading}"
    padding: "7px 8px"
---

# Design System: Pidzoom Portable charger HW178P

## Overview

**Creative North Star: "The Calibrated Bench Meter"**

The interface is a light, high-contrast work surface for an engineer operating
high-voltage equipment in mixed bench lighting. Information is aligned,
measured, and explicit; the application earns trust by showing connection,
authentication, freshness, provenance, and uncertainty instead of polishing
those details away.

The 1220×800 default window uses a fixed header, a stable workbench, and tabs
for transport evidence. It stays usable down to 1100×720 without fluid display
type or decorative rearrangement. It must never resemble a browser/SaaS control
panel, a decorative consumer charging app, or a dark “hacker dashboard” with
neon accents, glow, or gratuitous motion.

**Key Characteristics:**

- Pure-white working canvas and cool neutral instrument panels.
- One restrained teal identity color and sparse semantic signals.
- Large tabular numeric telemetry with compact engineering units.
- Persistent textual status, safety interlocks, and one state-aware output
  control.
- Raw protocol evidence available through progressive disclosure.
- Motion limited to native direct state feedback.

## Colors

The runtime palette was composed around a calm teal instrument marking and
converted from OKLCH to exact sRGB values because Qt Style Sheets require sRGB
colors. The frontmatter values are the normative Qt tokens.

### Primary

- **Instrument Teal** (`primary`): selection, focus, confirmed progress, and
  the one ordinary primary action.
- **Deep Instrument Teal** (`primary-deep`): teal hover state and readable
  success text.

### Secondary

- **Bench Copper** (`attention`): operator decisions, Start, and warning
  borders. It never indicates passive decoration.
- **Safety Red** (`danger`): Stop and explicit unsafe/error states, always
  accompanied by a symbol and text.

### Neutral

- **Lab Paper** (`background`): the literal white working canvas.
- **Cool Instrument Surface** (`surface`) and **Recessed Surface**
  (`surface-strong`): panels, tabs, headers, and selected working regions.
- **Graphite Ink** (`ink`): primary text at high contrast.
- **Calibration Grey** (`muted`) and **Hairline Grey** (`border`): secondary
  text and one-pixel structure.
- Pale semantic and selection tokens support state without turning the entire
  surface into a status color.

**The One Voice Rule.** Teal occupies no more than ten percent of a window and
identifies selection, focus, or safe progress only.

**The Signal Rule.** Copper and red appear only when an operator decision or a
safety condition demands attention.

## Typography

**Display Font:** none; this is a working product surface.
**Body Font:** native macOS system sans with Qt platform fallback.
**Label/Mono Font:** SF Mono with Menlo and the Qt fixed-font fallback.

**Character:** Familiar macOS controls carry the interface; protocol bytes and
readings gain precision through monospace without making labels resemble a
terminal.

### Hierarchy

- **Title** (650, 18px): product identity in the fixed header only.
- **Body** (400, 13px): instructions, values, and operational explanations.
- **Label** (650, 13px): section titles, controls, and table headings.
- **Reading** (650, 25px): exact telemetry with tabular alignment.
- **Protocol** (400, 12px): UUIDs, raw bytes, and decoded evidence.

**The Exact Number Rule.** Never replace a measured engineering value with an
unlabelled decorative gauge.

## Elevation

The system is flat by default. Depth comes from neutral tonal separation and
one-pixel hairline borders; application surfaces have no custom shadows.
Native modal dialogs and menus retain platform elevation. Status changes never
move panels or reflow primary controls.

**The Stationary Surface Rule.** A working instrument stays spatially stable
while readings change.

## Components

### Buttons

- **Shape:** gently squared (4px), minimum 30px high, verb-first Russian labels.
- **Primary:** Instrument Teal with white text; reserved for Connect and the
  single clear next action.
- **Output control:** one fixed 38px-high header button. With fresh explicit
  OFF it uses Bench Copper and opens the Start confirmation for the exact V/I.
  With fresh explicit ON it becomes Safety Red Stop, needs no dialog, and has
  a keyboard shortcut. While OFF, a pending Start disables that same location;
  a pending Stop disables it as Stop. Fresh ON overrides pending Start and
  ordinary busy work so de-energizing stays available. Monitoring,
  stale/unknown telemetry, or an unavailable session show a concise disabled
  state instead of creating a second control.
- **Hover / Focus:** tonal hover and a strong 2px teal focus boundary; active
  state darkens without movement.
- **Disabled:** neutral surface plus a nearby reason or accessible tooltip.

### Chips

- **Style:** textual status badges use a symbol, title, and value inside a pale
  semantic surface with a one-pixel border.
- **State:** color is redundant; `✓`, `!`, `×`, and `○` preserve meaning.

### Cards / Containers

- **Corner Style:** restrained 5px group boxes.
- **Background:** Lab Paper on Cool Instrument Surface regions.
- **Shadow Strategy:** no custom shadow.
- **Border:** one-pixel Hairline Grey.
- **Internal Padding:** 10–16px from the normative spacing scale.

### Inputs / Fields

- **Style:** native spin boxes with visible V/A suffix, exact decimal value,
  adjacent effective range, 4px corners, and white background. Voltage never
  exposes less than `50.00 V` or more than `178.00 V`; current never exposes
  more than `14.00 A`. Narrower valid device-reported maxima take precedence.
- **Focus:** 2px Instrument Teal boundary.
- **Error / Disabled:** fail closed, retain the value for inspection, and state
  the interlock reason in text.

### Navigation

Native top tabs separate the working panel, GATT transport, and packet journal.
The active tab uses a thin teal top boundary rather than a filled accent.

### Telemetry Reading

Each reading has a muted sensor label, right-aligned monospaced number, fixed
unit column, stable decimal precision, and an explicit invalid placeholder.
Temperatures remain numbered until their physical sensor locations are proven.

## Do's and Don'ts

### Do:

- **Do** keep connection, authentication, freshness, and command outcome
  visible without opening a dialog.
- **Do** pair every status color with text and a simple symbol.
- **Do** align telemetry decimals and engineering units with the Reading token.
- **Do** keep one output-control location stable while its label and role follow
  fresh explicit OFF/ON state. Keep the Stop shortcut visible only when Stop is
  actually available.
- **Do** label simulator data as simulated in every relevant surface and export.
- **Do** use familiar native controls and consistent 4–5px shapes.

### Don't:

- **Don't** build a browser or SaaS control panel.
- **Don't** imitate a decorative consumer charging app with gauges that hide
  exact values.
- **Don't** use a dark “hacker dashboard” with neon accents, glow, or
  gratuitous motion.
- **Don't** hide automation, reconnect behavior, or uncertain command outcomes.
- **Don't** use color-only alarms or tiny low-contrast telemetry.
- **Don't** expose a raw opcode console capable of transmitting unsupported
  commands.
- **Don't** invent custom scrollbars, animated gauges, or non-native modal
  behavior for visual flavor.
