# Memory Space Design System

> Status: **production direction, not visual acceptance**
> Updated: 2026-11-03
> Scope: `desktop/src/memory-space/`: the chart (light cone and its plate),
> Archive, reading surfaces, and their SG / β states.

This file is the short production summary of the Memory direction. **Details are
owned by `amadeus-ops/handoff/memory-space-step1-20261102/DIRECTION.md`** (round
two, sections 13–21, override round one where they conflict) and its locked copy
table `COPY-TABLE.md`. When this file and DIRECTION disagree, DIRECTION wins and
this file is the one to fix.

## 0. Authority and reading order

1. Product semantics, scope, privacy and Archive governance stay owned by the
   frozen Memory contracts and backend schemas. Nothing here renames an API.
2. `docs/adr/0008-soul-is-identity-formation.md` owns the identity model: record
   taxonomy and SOUL identity formation are orthogonal.
3. `DIRECTION.md` (above) owns layout, encoding, states, motion and copy.
4. `docs/amadeus-ui-design-system.md` owns Workstation chrome, global tokens,
   fonts and focus language.
5. This file owns the Memory-specific summary and the acceptance gates.

The WebGL glass orrery (`ConstellationStage`, `orreryRenderer`, overview motion,
cognition materials, filaments) is retired and deleted. Its "3D unavailable, switched
to the full list" fallback is gone with it. `three` stays as a dependency for the
light cone; `renderCapability.ts` stays as the base for the fallback rules.

## 1. Product thesis

> Everything she remembers hangs below "today", forming a light cone: the lower,
> the earlier; the apex is the day of your first conversation. Seen from straight
> above, it is the plate.

- **Chart (星图)** answers "she remembers me" and "how she remembers". Every star
  opens its source first (your own words and the date), then what she wrote down.
- **Archive (档案)** is where you see, find and change things. Every star links to
  its Archive row; every row links back to its place on the chart.
- **SOUL** is her continuity: the centre of the plate, the apex of the cone. It is
  never a camera target and never connected to stars by lines.

The chart has two renderings of **one** coordinate system:

| Rendering | Role |
|---|---|
| Light cone (WebGL 3D, `three`) | Main line. Plate on top is today; memories hang below at their dates |
| Plate (SVG/DOM) | The cone seen from straight above, and the fallback when 3D cannot run (DIRECTION §16) |

Switching between them interpolates the same points; selection, focused topic and
the reading surface do not move. The plate is not a lesser view: it carries every
meaning except look-back (回看), which only the cone has.

## 2. Shell

- The Workstation header stays. The 记忆 key shows a pressed state (accent hairline
  below, small `ESC` beside it). The three-column area is replaced by a sheet under
  the header; the Workstation stays mounted so the conversation is untouched.
- Sheet: frame with zone numbers on all four edges (1–8 / A–F, same as login);
  top left 回到对话 / 回退, tabs 星图 CHART｜档案 INDEX, breadcrumbs (总览 / 主题 /
  No.xxx); top right title block `PLATE`, `SCOPE`, `SINCE`, `STARS` (认知 + 片段),
  `DATE`, all from real readings. SCOPE switches what you are browsing, never the
  conversation.
- Leave: `Esc` and right-click unwind one layer (record → topic/month → overview →
  back to the conversation); 回到对话; or press 记忆 again. Leaving only fades the
  sheet (≤150ms). No iris mask over the header.
- `/` jumps to the Archive search.
- States: loading, empty (no memory), new user (a few facts, each labelled, no
  invented topics), unavailable, error, truncated. Copy is in COPY-TABLE.

## 3. Vocabulary

| Domain | Product label | Rule |
|---|---|---|
| Topic | 主题 | Stable bearing on the chart |
| Fact | 认知 | Not called “事实” in user-facing UI |
| Experience | 片段 | Not called “经历” in user-facing UI |
| Ungrouped fact | 未归组 | Never given an invented topic |
| Observation | 观测 / 待处理 | Archive only |

Backend, API and type names keep their names (`fact`, `experience`).

## 4. Coordinates

Implemented once in `chartCoordinates.ts` (pure functions, shared by the plate and
the cone). Positions depend only on dates, ids and topic order, so the same memory
is always in the same place.

| Quantity | Rule |
|---|---|
| Origin | SOUL; the first conversation in this scope (`origin_at`, else the earliest record) |
| θ, distance from SOUL | `θ = 9 + 6.2 × √(days since origin)`: each day covers the same area. On the cone, θ is also the height |
| Plate scale | `max(θ_today, θ at 42 days)` maps to 96% of the plate radius; a young chart is drawn at six weeks |
| φ, bearing | Topics in creation order: `φ = −28° + index × 137.508°`; adding a topic never moves older ones. Records scatter ±26px around their topic (at most ±24°) by id hash |
| Spacing | A star closer than 4.6 θ to an earlier one moves only around its ring; its date never moves and earlier stars never move |
| Ungrouped facts, fragments | In the widest three gaps between topics that existed when they were written |
| Lines | Only within one topic, in the order written. No lines between topics or to SOUL |
| Size | Fact `1.9 + 0.55 × min(4, times you mentioned it)` px; ungrouped 2.2; fragment hollow 3.4; pinned adds a ring at r + 3.4 |
| New | Written after this device's last open of this scope: pen cross plus `新 · MM/DD` in her colour |
| Month rings | One per month start; labels along the time ruler in the widest empty wedge; the ring too close to today is skipped |
| Zone | The frame cell (A–F × 1–8) a star falls in on the overview; shown in the reading surface |

## 5. Materials

Geometry, layout and copy are identical across worldlines; only materials change.

| | STEINS;GATE | β |
|---|---|---|
| Paper / ink | `#0a0b0d` / `#ecebe7` | `#0b0b0c` / `#ede6dd` |
| Ground | Fine grid (80px / 16px, as login) | 16px dot grid plus faint grid |
| Month rings | Dashed 5/6 | Dotted 1/4 |
| Accent (selection, today) | `#8fdcff` | `#e98a4f` |
| Her colour (SOUL core, new) | `#ff9cc3` | `#c9a063` |
| Motion | 120 / 200 / 320ms, `cubic-bezier(.2,0,0,1)` | 156 / 260 / 416ms, `cubic-bezier(.5,0,.15,1.12)` |

No orange on the SG sheet (the header nixie readout is existing Workstation chrome).
SG has no glow. Light-cone line work, hidden lines and β glow follow DIRECTION §15.6.

Fonts: only shipped self-hosted families (`--font-ui`, `--font-brand`,
`--font-mono`, `--font-ja`). No CDN fonts.

## 6. Reading surface

One DOM panel on the right (about 372px), beside the plate, never over it. For a
record: number · kind · topic · zone; your words and date first (corrected facts
show the words that caused the revision); 你还提过 N 次 folded; 她记成; a readings
table; 在档案中打开 ↗. No edit or delete on the chart. Topic, month and SOUL have
their own readings (DIRECTION §2 Q2). Switching content reuses the container.

## 7. Archive

Archive is the index of the plate, not a dashboard. It currently keeps its
list/detail layout inside the sheet frame; the redesign (DIRECTION Q3) is a separate
task. Until then:

- Rows use dividers and space, not cards; raw ids and ISO timestamps stay folded.
- Governance actions live in the selected record, never on the chart.
- Loading, empty, filtered-empty, partial failure and delete sync stay honest.

## 8. Motion

- At most two areas move at once: the chart, and the reading surface.
- No permanent blinking, particles, breathing or pulses. SOUL does not spin.
- Plate entrance: paper first (≤200ms), the pen draws month rings outward
  (≤900ms), stars appear in the order written, then new stars one by one.
- Light-cone entrance, camera, inertia, idle turn and look-back: DIRECTION §15.4–15.7.
- Reduced motion: the plate appears directly (≤120ms opacity); no drawing, no
  camera moves; new marks are static. Reduced motion also selects the plate by
  default (DIRECTION §16.1).

## 9. Interaction

- Hover previews (label, number, date); it never selects or fetches.
- Click a star: reticle and reading. Click a topic label: other topics drop to 20%,
  the view eases toward that topic. Click a month label: that month's review. Click
  SOUL: continuity summary.
- Blank left-click does nothing. Right-click unwinds one layer.
- Keyboard: stars, topics, months and SOUL are focusable with Enter/Space. The full
  keyboard route is still the Archive; 3D never carries accessibility alone.

## 10. Accessibility

- Every star, label and reading is DOM/SVG with an accessible name.
- Focus, selection, error and worldline never rely on colour alone.
- The chart is never the only route to a record.
- Live regions announce loading, unavailable and navigation outcomes without
  claiming hidden causes.

## 11. Explicit bans

Never ship:

- a glass sphere, neon rim light, or sun-and-planets silhouette;
- lines whose meaning cannot be stated, including topic-to-SOUL spokes;
- every topic lighting up when one is hovered;
- colour-only kind encoding;
- invented topics for ungrouped facts, or an empty chart for a new user;
- edit or delete controls on the chart;
- achievements, completion meters, fake percentages or sci-fi HUD filler;
- raw UUIDs or ISO timestamps in the default reading layer;
- English-first product copy;
- copies of original game UI or materials.

## 12. Acceptance gates

1. Five-second read: SOUL, topics, the selected star and the next action are clear.
2. Thumbnail read at 64px: a drawn plate, not sun-and-planets.
3. Every mark can be explained by section 4.
4. SG and β are distinguishable in static captures and stay one product.
5. Populated, new, empty, loading, unavailable, error and truncated states use
   fixtures or governed data, labelled as such.
6. Keyboard, Archive route, focus, announcements and reduced motion are checked in
   runtime.
7. The plate matches the reference mockup (`mockup/amadeus-memory-r2-cone.html`,
   plate fallback) on the same sample data; differences are written down.
8. Only explicit user acceptance closes a visual slice. Tests, builds and fixture
   screenshots prove integrity, not beauty.
