# M08 actor file-operation regressions

Base: 44c2bf26336769cb65680edefd46fcd95ae2d5c9 on m08/gateway-storage-integration-20261018.

The actor-to-broker gateway fixture now covers real workspace effects for edit, rename, move, trash, restore, and permanent delete. Each denial case gives the second authenticated actor every supported non-copy file right except the exact operation right. The owner then performs the same valid action with a fresh operation ID, proving the denied request left its source unchanged. Permanent delete remains separately authorized from trash.

The cases use registered cabinet presentations, authenticated SessionPeer.act requests, and broker-backed workspace files. Fixture setup creates only the destination directory needed by move. Existing create-only, collision, private diagnostic, and copy UNIMPLEMENTED assertions remain unchanged.

Compile-only checks: Python AST parsing verified syntax and all six test methods; CMake source inspection confirmed fixture registration. Both passed. Runtime fixture execution remains UNRUN and belongs on sb16 and sb16-2.