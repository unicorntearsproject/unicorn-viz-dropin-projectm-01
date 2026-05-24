# projectm-01 Integration

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-05-24

## Purpose

Describe ProjectM drop-in integration with Unicorn Viz rendering/audio systems.

## Integration Points

- Implements a standard effect class in playlist flow.
- Feeds live waveform PCM to projectM bridge each frame.
- Renders into active framebuffer and participates in transitions like other effects.

## Dependency Boundary

- Native dependency: libprojectM (external runtime library).
- Graceful fallback shader ensures playlist stability when native layer is unavailable.

## Related Docs

- ../README.md
- operations.md
- configuration.md
- troubleshooting.md
