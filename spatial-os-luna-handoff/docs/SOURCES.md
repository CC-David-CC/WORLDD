# Primary sources

Checked 2026-10-06. Upstream links are references, not vendored code, locked dependencies or guarantees about future releases. Protocol documents define the underlying interfaces; project-specific actor/world behavior is defined in CONTRACT.md.

[S1] XLibre official repository and releases. Version/channel discovery and base build/source provenance.
- https://github.com/X11Libre/xserver
- https://github.com/X11Libre/xserver/releases

[S2] X.Org Composite extension protocol. Redirection, backing storage and pixmap lifecycle.
- https://www.x.org/releases/X11R7.7/doc/compositeproto/compositeproto.txt

[S3] Khronos GLX_EXT_texture_from_pixmap specification. X pixmap/OpenGL binding.
- https://registry.khronos.org/OpenGL/extensions/EXT/GLX_EXT_texture_from_pixmap.txt

[S4] X.Org XInput extension version 2 protocol and MPX documentation. Master devices, focus, grabs, ClientPointer and event semantics.
- https://www.x.org/releases/current/doc/inputproto/XI2proto.txt
- https://www.x.org/Development/Documentation/MPX/

[S5] Official Git worktree documentation. Branch checkout restrictions and real worktree management.
- https://git-scm.com/docs/git-worktree

[S6] SQLite atomic commit documentation. Database durability boundary; external effects require separate reconciliation.
- https://sqlite.org/atomiccommit.html

[S7] Spacenav project and libspnav manual. Device events and supported integration paths.
- https://spacenav.sourceforge.net/
- https://spacenav.sourceforge.net/man_libspnav/

[S8] OpenAI Codex AGENTS.md documentation. Repository instructions for the driver and delegated work.
- https://developers.openai.com/codex/guides/agents-md
- https://learn.chatgpt.com/docs/agent-configuration/agents-md

[S9] OpenAI Chat Completions API reference. The helper transport is explicitly configured to a compatible interface; compatibility of a particular local server must be verified on that server.
- https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
