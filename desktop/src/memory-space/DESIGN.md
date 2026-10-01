# Memory Space Design System

> Status: **DRAFT / production direction, not visual acceptance**
> Updated: 2026-08-28
> Scope: `desktop/src/memory-space/` Constellation, Index, reading surfaces,
> Archive, and their SG / β visual states.

This file translates the accepted Memory visual direction into production design
rules. It is not permission to copy prototype code, change backend semantics, or
claim the visual gate passed.

## 0. Authority and reading order

Read these sources in order before changing Memory UI:

1. Product semantics, scope, privacy, projection, and Archive governance remain
   owned by the frozen Memory contracts and backend schemas.
2. `docs/adr/0008-soul-is-identity-formation.md` owns the post-contract identity
   model: record taxonomy and SOUL identity formation are orthogonal.
3. The latest user decisions recorded in
   `docs/superpowers/plans/memory-v11-frontend-visual-grill.md` own the visual
   direction when an older visual technique conflicts.
4. `docs/amadeus-ui-design-system.md` owns Workstation chrome, global tokens,
   fonts, focus language, and single-dark-product identity.
5. This file owns Memory-specific composition, material roles, hierarchy,
   motion character, and visual acceptance gates.
6. The throwaway `agent/memory-visual-prototype` branch is evidence of the design
   discussion. Its code and exact geometry are not production specifications.

Explicitly superseded visual techniques include the retired 2D star-map clone,
blank-left-click return, fixed SOUL-target camera framing, and perpetual absolute
stillness. This does not reopen API, projection, admission, privacy, or lifecycle
contracts.

## 1. Product thesis

Memory has three complementary jobs:

- **Constellation** gives orientation, serendipity, atmosphere, and a felt sense
  of one coherent memory body.
- **Index** is the fast, semantic, keyboard-complete route for daily location.
- **Archive** is the complete reading, verification, correction, and governance
  workspace.
- **SOUL** expresses how Amadeus continuity is formed from Origin authority,
  autobiographical Experience, and supported self-understanding. It is not a
  second record taxonomy.

Constellation must never become the only usable route. Index must never look like
a degraded fallback. Archive must never look like a generic administration
dashboard.

The first five seconds of Constellation should read as:

> one Amadeus memory body, with SOUL as continuity and Topics as regions of
> attention—not a solar system, node editor, atom diagram, or particle demo.

Atmosphere calibration:

| Axis | Target | Meaning |
|---|---:|---|
| Density | 5 / 10 | Quiet Overview; denser only after deliberate focus |
| Variance | 6 / 10 | Asymmetric and composed, never random or chaotic |
| Motion | 5 / 10 | Cinematic recall, restrained ambient life |
| Instrumentality | 4 / 10 | Precise enough to trust, not a cockpit HUD |
| Emotional warmth | 5 / 10 | Amadeus memory, not a sterile graph database |

## 2. Chosen visual grammar

The production direction is **A-led A+C**, with only the useful behavioural idea
from B:

- **A / optical body is dominant.** It owns Overview, visual hierarchy, SOUL,
  spatial Topics, and the calm full-scene silhouette.
- **C / strata supplies depth after focus.** Layers become visible only when a
  Topic or record is recalled. They must not turn Overview into concentric-ring
  wallpaper.
- **B / living field survives as behaviour, not required form.** Sparse particles,
  material depth, or light may breathe or condense toward attention when they
  improve comfort. No particle field is mandatory. Large membranes, organic
  lobes, green tissue, cell walls, organ silhouettes, and biological wetness are
  banned.

The throwaway D prototype validates only this relationship. Its card geometry,
spacing, typography, fake data, and CSS effects are not approved.

### 2.1 Progressive Memory Body

Overview is a coherent porous body. Not every database row is a permanent sphere.

1. **Overview:** SOUL, visible Topics, bounded representative matter, and sparse
   truthful continuity structure.
2. **Topic focus:** the selected Topic condenses into the visual weight; its
   governed representative cognition and fragments become readable.
3. **Record focus:** the selected cognition or fragment becomes the immediate
   focal body; Topic remains softened local context and SOUL remains distant
   continuity context.
4. **Unwind:** record → Topic → Overview. Right-click, Back, and Escape share the
   semantic stack while respecting transient Index/tools/Inspector layers.

This is progressive disclosure, not frontend rematching. It must consume the
canonical projection and its typed edges.

### 2.2 Vocabulary

| Domain | Product label | Rule |
|---|---|---|
| Topic | 主题 | Stable grouping and spatial region |
| Fact | 认知 | A durable proposition; not called “事实” in ordinary UI |
| Experience | 片段 | A situated memory fragment; not called “经历” in ordinary UI |
| LIST | 索引 | Daily semantic navigation; never “兼容视图” |
| Observation | 观测 | Candidate/governance item in Archive |

Backend/API/type names remain unchanged. Product labels do not authorize schema
renames.

## 3. Composition

### 3.1 Constellation Overview

- Full-bleed scene inside the Memory shell; no giant enclosing card.
- The memory body occupies roughly 62–72% of the viewport short edge, with
  meaningful dark space inside and around it.
- SOUL is recognisable and centreable, but the overall composition may be
  optically offset. Mathematical centring is not required.
- Topics occupy a shallow 3D shell with perceptible near/mid/far depth. They do
  not form a flat equatorial disk or evenly spaced wheel.
- Overview shows no permanent reading rail. Chrome remains quiet until intent.
- Only semantic lines are drawn. Decorative field curves cannot resemble edges,
  arrows, ownership, causality, or typed relationships.
- Ungrouped evidence is not hung beside SOUL as an invented peer and is not given
  a fabricated Topic. It remains reachable through Index and Archive.

Thumbnail gate: at 64px, the still must read as **SOUL inside one memory body with
several islands**, never **sun with orbiting planets**.

### 3.2 Topic focus

- The selected Topic becomes the visual weight, not necessarily screen centre.
- Camera motion uses an interruptible arc and dolly around the selected pivot.
  A pure screen-space truck with unchanged angle is a failure.
- The Topic must grow into a legible apparent-size band; centring a tiny sphere is
  not sufficient.
- SOUL remains visible as softened distant context unless truthful safe-area
  composition makes that impossible.
- C-style strata appear around or through the selected Topic to communicate
  recursive depth. Use at most 2–4 meaningful layers; do not display decorative
  rings with no semantic reading.
- Representative cognition and fragments condense from the current live geometry.
  Edges, hit regions, anchors, and visible nodes share that geometry.

### 3.3 Record focus

- The selected cognition or fragment is the first visual read.
- Its parent Topic remains visible but cannot occlude it or dominate apparent size.
- SOUL is background continuity, not the camera target.
- Background matter recedes through softness, frequency, depth, and contrast. It
  must not collapse to black.
- The selected object remains pickable at its visible boundary after motion
  settles. Click geometry follows projected visual size and depth.

### 3.4 Reading surface

Wide 3D uses one calm scene-related reading surface beside the focal composition.
It is spatially related but remains readable and accessible DOM—not a WebGL text
mesh and not a fixed dashboard sidebar.

- Chinese verified content is primary.
- Topic/record title appears once.
- One concise summary or current evidence layer appears before diagnostics.
- Version, source, human time, expiry, and verification state follow P1R-3 rules.
- Raw identifiers and ISO timestamps remain folded in diagnostics.
- Switching between records reuses one stable container. Content transitions in
  approximately 320–420ms with no mount/unmount jump, size snap, or double card.
- On narrow layouts, the reading surface becomes the single active DOM pane; it
  never overlaps the Index or forces unreadable 3D behind it.

## 4. SOUL identity formation

SOUL represents continuity being formed, not an empty logo ball, three databases
inside a sphere, or a fabricated model of Amadeus's inner life.

### 4.1 Closed form

- Form: one sealed optical body with a restrained visible core and incomplete
  structural cuts. No large text pasted on the sphere.
- The closed body may imply latent depth but must not display three labelled
  chambers, equal rings, or decorative strata that look like available content.
- No decorative latitude/longitude wires, meaningless spokes, or white equator
  labels.
- Hover affects SOUL only. It must not brighten every Topic.
- The same identity silhouette survives SG and β; material history changes, not
  topology or semantics.

### 4.2 Opening choreography

- Opening is one progressive reveal. The shell separates in weighted plates,
  fragments, or bounded particles while preserving a legible core and a clear
  reverse path. It is not an explosion and does not scatter semantic objects.
- The camera and shell motion remain interruptible. Back, Escape, or right-click
  reverse from current live geometry rather than replaying a canned close.
- Reduced motion reaches the same open composition immediately or through a short
  opacity/contrast transition; no plate flight or particle travel remains.
- Visual choreography may be prototyped before backend work, but production must
  not merge semantic labels or identity content until the corresponding read
  model exists.

### 4.3 Semantic formation

The concepts are not three peer tabs:

| Meaning | Visual role | Data rule |
|---|---|---|
| Origin / 起源 | Stable central core | Reviewed authority summaries; never ordinary Memory nodes |
| Lived / 亲历 | Qualifying Experience fragments or trajectories gathering around the core | Reuses the canonical Experience and provenance; never a second store or node kind |
| Reflection / 自我理解 | A supported structure emerging from Origin and autobiographical evidence | Versioned and recomputable; absent until governed evidence exists |

- Product navigation does not expose an `ORIGIN / LIVED / SELF MODEL` layer
  selector. The open composition unfolds from core to evidence to synthesis.
- Missing capability remains absent, not an empty labelled plate, disabled tab,
  fake percentage, or decorative sphere.
- Origin authoring categories do not require five visible cards.
- Reflection is not scattered through Overview as ordinary Fact-like nodes. Its
  supporting records remain navigable through Constellation, Index, and Archive.

## 5. Material and color roles

Workstation chrome continues to use the canonical global tokens from
`desktop/src/design-system/tokens.css`. Memory scene materials are local rendering
roles; they do not retokenize the rest of the application.

### 5.1 Shared foundation

| Role | Anchor | Use |
|---|---:|---|
| Memory void | `#040607` | Deep scene background; never pure black |
| Optical body | `#071118` | SOUL and selected-body shadow |
| Primary scene text | `#dce7eb` | Labels and readable scene copy |
| Secondary scene text | `rgba(196,214,221,.58)` | Metadata with practical contrast |
| Quiet structure | `rgba(174,207,219,.12)` | Non-interactive shell and layer edges |
| Hidden structure | `rgba(174,207,219,.05)` | Depth cues only; never essential meaning |

### 5.2 SG Memory scene

SG is controlled, coherent, and optical:

| Role | Anchor | Use |
|---|---:|---|
| SG matter | `#0b202a` | Cool blue-black volume |
| SG refracted surface | `#214b5d` | Low-opacity glass depth |
| SG scene signal | `#a9cfdd` | Selected sphere, semantic beam, live core |
| SG bright point | `#dceef4` | Rare specular/focal peak |

Pale blue is a Memory material signal, not a global blue UI accent. DOM controls,
focus rings, warnings, and Workstation telemetry continue to use canonical global
tokens. No electric cyan, blue filter, or neon cyberpunk wash.

### 5.3 β Memory scene

β uses the same geometry with a worn physical history:

| Role | Anchor | Use |
|---|---:|---|
| β matter | `#1a100b` | Warm graphite body |
| β oxidised surface | `#5d3322` | Sparse roughness and layer edge |
| β scene signal | `#bd784c` | Selected semantic matter |
| β bright point | `#e5b17e` | Rare focal peak |

β is not orange SG and not a full rust-red wash. Static screenshots must remain
distinguishable through surface response, roughness, edge continuity, and sparse
signal—not only animation or a global hue filter.

### 5.4 Signal restraint

- One scene accent per worldline.
- Glow represents active matter or semantic signal, never every border.
- Bloom is optional, low, and shed before semantic content.
- Hover raises one node one level. It does not light all Topics or crush the
  background.
- Error red is reserved for actual error/destructive state and is paired with
  text.

## 6. Typography

Use only shipped self-hosted families:

- **Chinese UI and reading:** `var(--font-ui)` / Noto Sans SC.
- **Structural Latin marks:** `var(--font-brand)` / Oxanium, sparingly.
- **Time, counts, raw IDs, diagnostics:** `var(--font-mono)` / IBM Plex Mono.
- **Japanese content:** `var(--font-ja)` with `lang="ja"`.

No runtime font CDN. No Inter. No generic serif display face. Prototype use of
Georgia is explicitly non-production.

| Role | Size guidance | Treatment |
|---|---:|---|
| Memory mode title | 15–18px | UI/brand, controlled tracking |
| Topic focal title | `clamp(24px, 2.5vw, 38px)` | Chinese UI, regular/medium weight |
| Reading title | 20–28px | Chinese UI, no duplicate eyebrow title |
| Reading body | 14–15px | 1.65–1.8 line height, max 65ch |
| Topic label | 11–13px | Chinese first; at most seven visible |
| Telemetry/meta | 8–10px | Mono; never essential copy at low contrast |

Uppercase English is limited to short structural marks such as `MEMORY`, `SOUL`,
and `TOPIC`. Player-facing explanations and actions remain Chinese.

## 7. Archive visual system

Archive is an editorial observation record, not a dashboard and not a 2D
Constellation.

### 7.1 Wide layout

- Stable index/detail relationship without one giant outer box.
- Index width targets 34–40%; detail owns the remaining reading width.
- Rows use dividers and negative space rather than individual rounded cards.
- Default row hierarchy: title → Topic → human time → lifecycle state.
- Raw IDs, diagnostics, source IDs, and ISO timestamps stay folded.
- Search, kind tabs, Topic filter, and pinned filter remain visible and calm.
- Governance actions live in the selected record context, not on every row.
- Pending observations form a compact verification queue, not KPI cards.

### 7.2 Narrow layout

- One pane at a time: index or detail.
- Back returns to the same filters, scroll position, and focused row.
- Kind tabs are true single panels.
- No horizontal scrolling and no content hidden beneath truncation/tool overlays.
- Unsaved drafts require explicit discard; saving blocks conflicting navigation
  with honest feedback.

### 7.3 Archive states

- Loading skeletons match real row/detail geometry; no generic spinner.
- Empty copy distinguishes truly empty scope from filters with no match.
- Partial pending failure does not masquerade as empty.
- 404/inaccessible copy does not infer deleted, missing, or cross-scope cause.
- Delete sync suppresses stale readable content until the backend projection is
  authoritative again.

## 8. Index and recovery

Index is the deterministic semantic navigator and the recovery surface when 3D is
unavailable. The retired 2D star-map imitation does not return.

- Closed by default in wide Constellation; invoked deliberately.
- Full keyboard path with roving focus separated from activation.
- Roving alone does not select or request details.
- Enter/Space/click activates the canonical record.
- Directional and screen-reader order is deterministic and independent of camera.
- The Index can reach every eligible record even when no 3D scene exists.
- WebGL failure, sticky capability fallback, or user preference opens Index with
  selection and scope preserved; it does not render an inferior fake galaxy.

## 9. Motion and interaction

Motion communicates recall depth. It must feel weighted, interruptible, and calm.

### 9.1 Timing hierarchy

| Motion | Initial tuning band | Rule |
|---|---:|---|
| Hover/focus feedback | 140–200ms | Immediate; no camera movement |
| Reading-content exchange | 320–420ms | Stable container, opacity/short depth only |
| Topic/record recall | 420–650ms | Arc + dolly; distance-aware |
| Unwind | 360–560ms | Reverses from current live pose |
| Archive navigation | 180–280ms | No spatial flight |
| Ambient life | 12–20s period | Low amplitude, bounded, pausable |

These are tuning bands, not excuses to queue transitions. New intent cancels old
motion and starts from current live geometry.

### 9.2 Ambient life

- Begin only after roughly four seconds of true idle Overview.
- Use bounded, recoverable precession or yaw oscillation; never cumulative
  `yaw += k`.
- Pause immediately for hover, selection, drag, wheel, hidden document, blur, or
  reduced motion.
- Resume from the current pose after a new idle delay. Never snap to a canonical
  rest camera.
- Ambient particle life is sparse and subordinate to semantic matter.

### 9.3 Pointer and keyboard

- Visible sphere geometry and pick geometry agree at every depth and zoom.
- Blank left-click is a no-op. It never collapses Topic or returns to SOUL.
- Right-click unwinds one semantic layer and suppresses the context menu unless it
  was a right-button drag.
- Left drag owns manual orbit and cancels system motion immediately.
- Hover is preview only: no semantic selection, no details request, no camera.
- Direction keys rove; Enter/Space/click activate.
- Re-selecting the active node may deliberately rebuild comfortable framing.

### 9.4 Reduced motion

Under `prefers-reduced-motion: reduce`:

- No ambient drift, inertia, camera flight, particle travel, pulse loop, or layer
  rotation.
- Resolve the same final semantic composition immediately.
- Permit at most a short ≤120ms opacity/contrast transition.
- Do not force Index solely because motion is reduced.

Animate `transform` and `opacity` only on hot paths. Never animate layout size,
canvas host dimensions, or DOM anchors every frame.

## 10. Responsive and safe-area rules

- Desktop-first, full viewport, no document scrolling.
- Validate at 1904×1080, 1366×768, 1080-wide Tauri, and a compact approximately
  504px layout simulation.
- Selected matter and the active reading/auxiliary surface share the measured
  safe area during system framing.
- Manual user orbit is not clamped to fight the user; the auxiliary surface stays
  viewport-stable.
- At narrow width, one semantic pane owns vertical scrolling.
- Minimum interactive target is 40px desktop and 44px compact/touch-like layout.
- Essential Chinese body text never drops below 14px in reading surfaces.

Safe areas come from real host and pane geometry. No hard-coded `64px`, `42vh`,
or window-only approximation may become production authority.

## 11. Accessibility

- 3D is enhancement, never an access gate.
- Constellation exposes one coherent composite keyboard experience; Index remains
  the complete semantic route.
- All icon-only controls have accessible names and visible focus.
- Focus, selection, error, lifecycle, and worldline never rely on colour alone.
- Essential text uses practical contrast; dim text is decoration or metadata only.
- Reading and Archive headings preserve a logical document outline.
- Live announcements describe loading, success, unavailable, and navigation
  outcomes without claiming hidden causes.
- Screen-reader users receive the same selected record, scope, reading payload,
  and back-stack as pointer users.

## 12. Explicit bans

Never ship:

- a generic starfield with unrelated floating balls;
- a sun-and-planets, atom, wagon-wheel, or flat Saturn-disk silhouette;
- decorative lines whose meaning cannot be stated;
- every Topic glowing when only one node is hovered;
- universal orange spheres or colour-only kind encoding;
- fixed right-side dashboard cards covering the graph;
- giant rounded SaaS cards, glassmorphism panels, or pill-button walls;
- sci-fi HUD filler, fake percentages, fake performance metrics, or debug chrome;
- biological membranes, organ lobes, tissue-green surfaces, or wet cell imagery;
- concentric-ring wallpaper visible at all times;
- three equal SOUL chambers, a permanent identity-layer tab bar, or placeholder
  Origin/Lived/Reflection content;
- self-understanding rendered as ordinary Fact-like nodes without its governed
  support and lifecycle;
- raw UUIDs/ISO timestamps in the default reading layer;
- all-node label clouds or English-first product copy;
- bloom used to hide weak composition;
- a production copy of the throwaway D prototype;
- visual completion claims based only on tests, build, or screenshots from fixtures.

## 13. Production quality gates

A visual slice is not accepted until all applicable gates pass:

1. **Five-second read:** an uninstructed user can identify SOUL, Topics, selected
   matter, and the available next action.
2. **Thumbnail read:** Overview survives at 64px without becoming sun-and-planets.
3. **Hierarchy:** selected record wins over Topic; Topic wins over background;
   SOUL remains continuity without retaking attention.
4. **Interaction feel:** Topic/record selection, rapid switching, unwind, hover,
   picking, and reading-content exchange receive real user review.
5. **Truthful density:** populated/empty/truncated/error/loading/expired/versioned
   states use governed data or explicitly labelled fixtures.
6. **Worldlines:** SG and β are distinguishable in static wide and narrow captures
   while remaining one product.
7. **Accessibility:** keyboard, Index, visible focus, announcements, and reduced
   motion are verified in runtime.
8. **Runtime parity:** Chromium and Tauri/WebView2 cover the critical visual path.
9. **Performance:** hidden/blur throttling cannot trigger false fallback; active
   motion remains responsive at production budgets.
10. **User visual gate:** only explicit user acceptance closes the slice. “方向不错”
    or “骨架可用” is not acceptance.

Minimum capture board:

| Surface | Required states |
|---|---|
| Constellation SG | Overview, Topic focus, cognition focus, fragment focus |
| Constellation β | Overview, Topic focus, record focus |
| Index | populated, search, truncated, empty, capability recovery |
| Archive | list, detail, edit/conflict, delete sync, pending/retry |
| Layout | wide, 1366×768, 1080-wide, compact single-pane |
| Motion | normal, reduced, hidden→visible recovery |

## 14. Implementation discipline

- Work one visual slice at a time. Do not perform a global CSS rewrite while
  changing one Memory state.
- Reuse global tokens and shipped fonts before adding local roles.
- Memory-specific scene roles use a single owner; do not scatter literal colours
  across React components and renderer modules.
- 3D renderer, DOM labels, reading surface, and picking consume the same live frame
  and measured safe area.
- Visual-only changes need before/after captures at the same scope, viewport, and
  worldline.
- Interaction changes remain RED-first. Pure material tuning does not require a
  test for every pixel.
- Build and automated tests prove integrity, not beauty.
- Prototype branches remain primary-source evidence; losing variants do not enter
  production.
- Do not stage, commit, push, or update completion ledgers without explicit
  authorization.

## 15. Recommended production slice order

### Existing truthful surfaces

1. **Scene substrate:** Memory-local material roles, background depth, closed SOUL
   form, and clean Overview silhouette.
2. **Progressive body:** Topic hierarchy, representative matter, labels, truthful
   semantic lines, hover, and picking.
3. **Recall composition:** A-led arc/dolly, C-style focused strata, occlusion, and
   stable reading exchange.
4. **Index and Archive visual pass:** capability recovery, keyboard flow,
   compact/narrow panes, editorial hierarchy, and governance states.
5. **Worldline and acceptance pass:** SG/β static distinction, micro-motion,
   real-data density, Chromium/WebView2 captures, and explicit user review.

### Capability-gated SOUL formation

6. **Opening choreography prototype:** throwaway shell-separation/core-reveal
   study with no fake Origin, Lived, or Reflection records.
7. **Origin vertical slice:** reviewed Origin read model plus the first production
   SOUL opening. Missing authority fails closed.
8. **Autobiographical qualification:** subject/participant authority first, then
   existing Experience may appear as 亲历 without duplication.
9. **Reflection:** governed synthesis, challenge, and supersession only after real
   autobiographical evidence exists. Add no default Overview node kind.

Person identity resolution remains Archive-first. A 3D Person Anchor is a later
value hypothesis, not a prerequisite for SOUL formation.

Each slice must leave the product usable. “We will make it beautiful later” is not
an acceptance strategy after this document exists.
