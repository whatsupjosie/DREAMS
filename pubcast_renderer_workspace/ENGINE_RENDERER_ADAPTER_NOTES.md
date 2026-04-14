# PubCast engine ↔ renderer adapter notes

This workspace is the honest next step after the engine+bridge two-node test.
It does **not** claim a successful Rust compile in this environment because the Rust toolchain was unavailable.

## What was lined up

- The Python engine's `voxel_render` path currently returns a zlib-compressed binary blob.
- That blob layout is:
  - `u32 vertex_count`
  - `u32 index_count`
  - `vertex_count * 3 * f32` positions
  - `vertex_count * 3 * f32` normals
  - `index_count * u32` indices
- The patched renderer now includes:
  - `decode_pubcast_mesh_blob(...)`
  - `upload_pubcast_mesh_blob(...)`
  - `upload_pubcast_mesh(...)`

## Important current limitation

The renderer mesh pool still allocates GPU index buffers as `u16`.
That means engine-generated meshes with indices above `65535` will need either:

1. chunk splitting on the engine side, or
2. a renderer upgrade to `u32` index buffers.

For early smoke tests, this is acceptable because the current fallback cube and small meshed chunks stay safely below the `u16` ceiling.

## Why this matters

This narrows the trio seam from:

- bridge messages
- engine remote dispatch
- "some future renderer"

down to a concrete next gate:

- decode actual engine render output in Rust
- upload one mesh
- queue one mesh handle
- open a window and draw it

## Suggested Windows sequence

1. Install Rust toolchain.
2. Open this folder.
3. Run `cargo build --release`.
4. Fix compile errors, if any.
5. Run `cargo run --example render_window`.
6. After that works, feed a real compressed mesh blob from Python into `upload_pubcast_mesh_blob(...)`.
