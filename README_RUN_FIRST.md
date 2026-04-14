PubCast Trio Locked Bundle
==========================

What this bundle fixes
- The package layout is real and runnable as shipped.
- The harness imports from triotestpkg_close/ instead of depending on missing loose files.
- The engine + bridge pairing was verified in this packaged layout before zipping.

Quick start (Python-only two-node proof)
1. Open PowerShell in this folder.
2. Run:
   python .\pubcast_trio_close_loop_harness.py

Expected result shape:
   {'registered': True, 'render_completed': True, 'vertex_count': 1536, 'index_count': 2304, 'rust_dll_configured': False}

Rust renderer path (optional next proof)
1. Extract pubcast_renderer_workspace_fixed2.zip
2. In the extracted pubcast_renderer_workspace folder run:
   cargo build --release
3. Set the DLL path and rerun the harness:
   $env:PUBCAST_RENDERER_DLL = "FULL\PATH\TO\target\release\pubcast_renderer.dll"
   python .\pubcast_trio_close_loop_harness.py

What is actually proven in this bundle
- Bridge + engine two-node registration works.
- Remote voxel_render work dispatch works.
- Remote mesh result returns valid counts.

What is not faked
- This zip does not pretend the Rust GPU window path was proven inside this environment.
- The bridge is improved, but still fundamentally UDP-oriented.
