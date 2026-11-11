# Nexthink visual references

SWARM's dashboard and companion demo app use Nexthink's public brand palette and
product interface patterns. SWARM remains labeled as an Innovathon prototype.

Sources consulted on 11 November 2026:

- [Nexthink 2024 Quick-Reference Design Guide](https://design.nexthink.com/):
  published brand colors and corporate typography.
- [Navigating the web interface](https://docs.nexthink.com/getting-started/navigating-the-web-interface):
  blue navigation rail, white header, pale workspace, bordered widgets, outlined
  inputs, restrained line icons and contextual panels. The WiFi Diagnostics
  screenshot informed the product treatment.
- [Live dashboards](https://docs.nexthink.com/user-guide/live-dashboards/getting-started-with-live-dashboards):
  KPI cards and grouped dashboard sections.

## Palette and typography

| Published color | Hex | SWARM use |
| --- | --- | --- |
| Vibrant Blue 400 | `#3194F7` | Section accents and activity markers |
| Dark Blue 800 | `#0B1441` | Dialog backdrop and subtle shadows |
| Dark Blue 400 | `#1046A8` | Primary buttons and links |
| Teal 400 | `#00B1A9` | Healthy-state accents |
| Papaya 400 | `#FF7461` | Incident accents |
| Purple 500 | `#803795` | Sandbox label |
| Text black | `#221F20` | Main text |
| Background Grey 200 | `#EEF3F7` | Workspace background |

The rail colors `#0C5497` and `#0C6FC6` were sampled from an official product
screenshot; they are **not published design tokens**. Borders, surface tints,
muted text and darker semantic text colors are SWARM adaptations. Bright teal
and papaya are decorative accents; darker variants (`#00776F` and `#A6382B`)
keep small status labels readable on light surfaces. Statuses also have text
labels, so meaning does not depend on color alone.

The guide specifies Proxima Nova for corporate typography. The app requests that
font only if it is already installed, then falls back to Segoe UI and Arial.
Product font identity has not been independently confirmed. No font, image or
stylesheet is fetched from an external service at runtime. The product-reference
screenshots and Nexthink logo are not bundled in the app.

The navigation links point to working SWARM sections. No decorative search,
filter, overflow menu or other nonfunctional control was introduced. The same
light theme covers error banners, access settings, recipe evidence, endpoint
states and the native demo application's success and failure views. The rail
becomes a compact top navigation on small screens; reduced motion and visible
keyboard focus remain supported.
