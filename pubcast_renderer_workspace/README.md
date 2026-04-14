# PubCast Renderer Workspace Scaffold

This folder is a practical scaffold around the Round 4 renderer candidate.

What it includes:
- a Rust crate layout
- camera and scene stubs
- a minimal WGSL shader
- an example window runner
- a patched renderer file with a first-pass adapter for PubCast engine mesh blobs

What it does **not** include:
- a verified compile result from this environment
- a Python↔Rust FFI layer
- a finished production scene/asset pipeline

## Files of note

- `src/renderer.rs` — Round 4 renderer plus adapter helpers
- `ENGINE_RENDERER_ADAPTER_NOTES.md` — exact engine blob contract and current limits
- `examples/render_window.rs` — fallback-cube smoke test entry point

## Why this exists

The bridge and engine already passed a real two-node remote render-job test.
This scaffold is the shortest honest path to turning the renderer from a donor candidate into a real tested member of the trio.
