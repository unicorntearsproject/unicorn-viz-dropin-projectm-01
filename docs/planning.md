# projectm-01 Planning

Owner: Drop-in Maintainers
Status: draft
Last updated: 2026-06-03

## Purpose

Design a ProjectM effects manager for Unicorn Viz that can browse a very large
preset library, bulk-toggle presets by scope, and safely remove entries while
leaving room for future preset import workflows.

## Working Assumptions

- projectM preset packs do not expose a universal tag schema today.
- The strongest categorization signal is the on-disk folder hierarchy inside
  each preset pack.
- The existing `scan_presets.py` helper already identifies corrupt presets,
  HLSL-incompatible presets, and missing texture references, so the manager can
  reuse those health signals instead of inventing new ones.
- `ProjectMEffect.TAGS` describes the effect itself, not the individual presets.

## Categorization Strategy

Use a layered taxonomy instead of betting on one metadata source:

1. Path hierarchy, highest confidence.
   - Existing packs already organize presets into folders such as `Dancer`,
     `Drawing`, `Fractal`, `Geometric`, `Hypnotic`, `Particles`, `Reaction`,
     `Sparkle`, `Supernova`, and `Waveform`.
   - Treat the top-level folder as the primary category and nested folders as
     subcategories.
2. Pack identity, medium confidence.
   - Keep the source pack name as a separate facet so users can filter by pack
     without confusing it with artistic category.
3. Filename tokens, lower confidence.
   - Use only for search and suggestions, not as the primary taxonomy.
4. Optional sidecar metadata, future-ready.
   - If we later add a manifest or JSON sidecar, it can override or enrich the
     inferred category tree without changing the UI contract.

Recommended normalized fields per preset:

- `path`
- `display_name`
- `pack_name`
- `category_path`
- `tags`
- `enabled`
- `health_state`
- `delete_state`

## UI Shape

Use a modal overlay, not a dedicated full-screen scene. The right mental model
is a blend of the current audio/MIDI selectors and the help overlay:

- same dimmed-underlay modal treatment
- neon panel framing and large, legible text
- keyboard-first navigation with mouse support as a bonus
- persistent action bar at the bottom for shortcuts

Suggested layout:

- Left pane: category tree with counts and tri-state toggles.
- Center pane: preset browser with search, sort, and per-preset enable state.
- Right pane or footer: details for the selected preset, including source pack,
  category path, health warnings, and delete status.

For large libraries, keep the browser virtualized or paged so the overlay stays
responsive when the library contains thousands of presets.

## Core Actions

The manager should support three scopes for toggles:

- All presets.
- One category or subtree.
- One or more selected presets.

Required bulk actions:

- enable all
- disable all
- enable category
- disable category
- isolate category
- enable selected
- disable selected
- delete selected

Recommended delete semantics:

- Treat delete as a deliberate content-management action, not a casual toggle.
- Confirm destructive actions before touching the filesystem.
- Prefer a quarantine or trash-style move for user-managed preset files so
  accidental deletes can be recovered.
- Do not delete an entire preset pack repository implicitly unless the user has
  explicitly chosen that pack as owned content.

## Data Flow

Build the manager around a cached catalog index rather than rescanning the
filesystem every time the overlay opens.

Proposed flow:

1. Scan pack roots and build a preset catalog.
2. Derive categories from path segments and attach health metadata from the
   existing preset scanner.
3. Cache the catalog in memory for the active session.
4. Rebuild or diff the index when the user refreshes, imports, or deletes
   content.

This lets the overlay open quickly and keeps the browser usable even when the
projectM library grows into the tens of thousands of presets.

## Future Import Path

Keep import out of the first interaction pass, but design for it now.

Likely future entry points:

- add a local folder of presets
- clone or mount a curated preset pack
- rescan the catalog without restarting Unicorn Viz

If we later support loading new presets from the UI, the manager should record
the pack source and ownership so delete behavior stays safe.

## Implementation Phases

### Phase 1: Inventory and index

- Collect all available presets and infer categories from the path tree.
- Attach health badges from the preset scanner.
- Expose counts for all / enabled / disabled / broken presets.

### Phase 2: Modal browser

- Add the overlay shell and category tree.
- Add search, selection, and keyboard navigation.
- Reuse the existing audio/MIDI selector interaction pattern for modal focus.

### Phase 3: Bulk state management

- Add enable/disable all and by-category operations.
- Persist manager state in a local manifest or catalog file.
- Show tri-state category toggles and selection counts.

### Phase 4: Deletion and recovery

- Add delete confirmation and trash/quarantine handling.
- Show deleted items as recoverable until a cleanup action runs.
- Prevent accidental repository-wide deletion.

### Phase 5: Import and enrichment

- Add local import or pack registration.
- Allow future metadata sidecars and richer tags.
- Add manual tags or favorites only after the basic catalog workflow is stable.

## Open Questions

- Should disable state be stored per user catalog, per pack, or both?
- Should deletion operate on the live pack files or on a curated local cache?
- Do we want enabled/disabled to affect playback order, visibility, or both?
- Should category tags be editable by hand, or only inferred from paths?

## Success Criteria

- Users can browse the full preset library in a modal overlay.
- Users can enable, disable, or isolate presets by all / none / category.
- Users can delete presets safely with a clear confirmation path.
- The design leaves a clean path for later preset import and richer metadata.