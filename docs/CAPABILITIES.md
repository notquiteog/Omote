# Capability audit

This is an implementation map, not a claim that Omote outperforms every competing
product. Product documentation establishes comparison areas; repository tests
establish specific Omote behaviors. A mocked service test does not establish live
provider availability or quality. No comparative performance benchmark has been
completed.

| Area | Implemented in Omote | Setup or remaining gap |
|---|---|---|
| Coding and project work | File edits, patches, shell, Git/worktrees, review, LSP, checkpoints, subagents, background tasks, project instructions and tool approvals | Language servers and project dependencies must be available. Hosted isolated coding environments are not bundled. |
| Local execution and resources | Package installation, shell/background work, per-command root/admin access, live System panel, managed GGUF and ComfyUI safetensors/workflow downloads with cancel/resume/checksums, disk/memory preflight, live ComfyUI node/model compatibility, owned llama.cpp runtime start/stop and local chat | One transfer per library and one chat runtime; Vulkan/Metal need OS drivers. Linux NVIDIA Qwen chat and Studio SD Turbo image generation tested. ComfyUI uses an existing installation plus managed model paths. Native Mac/Windows GPU runs and admin dialogs need verification; automatic multi-file diffusion packages and other runtime adapters remain future work. |
| Continuing responsibilities | Durable local scheduled tasks, recurring civil times, run history, visible approvals, cancellation and background-worker follow-through | Computer and daemon must remain available. No hosted wake-ups, event-triggered jobs or cross-channel delivery. See [Scheduled tasks](SCHEDULES.md). |
| Business and personal workflows | Browser-driven employee portals, email tools, travel planning, calendar and ledger tools, user questions and MCP integration | Connect each service; authentication, purchases and outbound communication still need the appropriate user action/approval. No universal EMS or booking API. |
| Browser and computer use | Playwright browser sessions, page inspection, screenshots, desktop stages and input tools | Browser runtime, desktop platform support and app permissions are required. Website restrictions and inaccessible UI remain limitations. |
| Team interaction | Browser and configured MCP connections can reach supported communication services | No native Slack/Teams bot identity, channel listener, channel-level policy store or enterprise team account service is bundled. |
| Voice | LLM + STT + TTS pipeline and OpenAI Realtime integration with agent tools | Requires compatible services, microphone permission and audio hardware. This is not a login to the ChatGPT Instant consumer application. |
| Memory / Brain | Opt-in vector memory, personal/project filters, local text search across pages, offline manual saves, correction, export and deletion; deduplicated automatic fact capture | Connect a compatible embedding model for semantic recall. Automatic capture uses conservative patterns; no background model-led reconciliation of contradictions or hierarchical memory browser. |
| Tool/context efficiency | Small core plus searchable on-demand schemas, bounded optional-schema cache, deferred PhotoCraft/scheduling/creative-library implementations, toolset narrowing, skill loading, compaction, batched reads and quantized vector search | MCP servers still initialize at startup. The default fixture carries 76.9% fewer schema bytes; latency/task-success and comparative oh-my-pi benchmarks remain unmeasured. See [Tool loading](EXTENDING.md#tools-load-as-needed). |
| Images and video | Provider-specific generation forms, references, job progress/cancellation and media gallery | Model/account availability, supported parameters, costs and regional access depend on providers. |
| ComfyUI | Full connected native editor, Nodes 2.0 when installed, bridge capture/load, immutable versions, comparison, schema validation and owned run/output history | Requires ComfyUI, relevant weights and custom nodes. Omote adds a workbench around its native renderer. Compatibility with every extension has not been established. |
| Characters and 3D | Reusable descriptions/reference images, generation recipes, asset associations, import/export and interactive previews | References do not guarantee identity consistency. 3D generation depends on connected models/workflows; the viewer is not a full 3D authoring application. |
| Creative applications | PhotoCraft integration, media-editing tools, desktop control and configurable MCP tools | PhotoCraft/Adobe/GIMP and their supported automation interfaces must be installed or connected. |
| Companions and onboarding | Animated companions with distinct reactions, optional tour, provider setup, responsive workspace, accessible companion menu | Behavior and accessibility have automated browser coverage, not comprehensive user research across every platform and assistive technology. |

## Verification map

- Agent behavior: `tests/test_agent.py`, `tests/test_background_tasks.py`,
  `tests/test_sessions.py`, and the approval, tool, Git and browser suites.
- Local resources and administrator access: `tests/test_resources.py`,
  `tests/test_elevation.py`, `tests/test_system.py`, and desktop/mobile System
  coverage in `tests/test_workspace_browser.py`.
- Managed models: `tests/test_local_models.py` covers persistent ranged transfers,
  corruption, interrupted connections, disk limits, archive safety, process control,
  approval enforcement and chat reconnection; `tests/test_local_models_browser.py`
  covers desktop/mobile download, cancel, resume, compatibility and runtime controls.
- Managed ComfyUI assets: `tests/test_comfy_assets.py` covers persistent model and
  workflow transfers, format validation, live node/model checks, GPU memory checks,
  authentication, filename collisions and recipe defaults. `tests/test_comfy_assets_browser.py`
  covers simple Studio controls, cancel/resume, mobile layout and preserved drafts.
  A separate live smoke test resumed the actual SD Turbo download and generated
  a 512 × 512 image through Studio/ComfyUI on an RTX 5060 Ti.
- Scheduled work and UI: `tests/test_scheduling.py`,
  `tests/test_schedules_browser.py`.
- Creative workspace and node metadata: `tests/test_workbench.py`,
  `tests/test_workbench_review.py`, `tests/test_workflow_diff.py`,
  `tests/test_creative_browser.py` and the ComfyUI bridge tests.
- User flows: `tests/test_onboarding_browser.py`, `tests/test_workspace_browser.py`,
  `tests/test_companion_browser.py`.
- Service boundaries: mail, trip, memory, PhotoCraft, voice and Realtime suites.
  Local fakes and transport fixtures avoid sending real mail or paying for test
  generations; live account verification remains separate.

## Comparison sources

Reviewed against the requested product categories on October 15, 2026. This is
a category audit, not an exhaustive enumeration of every product feature.

The reference products overlap but have different deployment and account models.
Their documented behaviors identify gaps; their private internals and every live
integration cannot be reproduced or verified from documentation alone.

- [Codex features](https://learn.chatgpt.com/docs/features) and
  [automations](https://learn.chatgpt.com/docs/automations): coding workflows and
  recurring/event-driven execution.
- [ChatGPT dots](https://learn.chatgpt.com/docs/dots) and
  [tasks and memory](https://learn.chatgpt.com/docs/dots/tasks-and-memory): persistent
  responsibilities, background tasks and continuity.
- [Claude Cowork product overview](https://claude.com/resources/guides/claude-cowork-product-guide/product-overview)
  and [scheduled tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork):
  delegated work and recurring execution.
- [Claude Tag](https://support.claude.com/en/articles/15594475-what-is-claude-tag):
  team-channel participation and shared context.
- [Claude Code overview](https://code.claude.com/docs/en/overview): terminal,
  coding, project instructions and development integrations.
- [OpenCode documentation](https://opencode.ai/docs/): coding sessions,
  permissions, MCP, LSP, skills and extensibility.
- [Open WebUI memory](https://docs.openwebui.com/features/chat-conversations/memory/):
  inspectable memory, model-managed corrections, paths and background review.
- [oh-my-pi](https://github.com/can1357/oh-my-pi): coding tools, native execution,
  LSP/debugger operations and context efficiency. Its internals should be checked
  before attributing a particular lazy-loading design to it.
- [OpenClaw automation documentation](https://docs.openclaw.ai/automation/cron-jobs):
  persistent scheduled work and delivery mechanisms.
- [ComfyUI Nodes 2.0](https://blog.comfy.org/p/comfyui-node-2-0): the native graph
  rendering system that Omote embeds rather than reimplementing.

Account permissions, regional service availability, language/model quality and
third-party policies remain real constraints. "Anyone, anywhere, any application"
is an ambition, not a tested release criterion.

## Next implementation milestones

Extend managed chat and ComfyUI assets to automatic multi-file dependency bundles
and additional runtime adapters. Single-file diffusion models and workflow JSON
are implemented and tested with real GPU inference; automatic dependency resolution
and native Mac/Windows diffusion verification remain.

1. **Extend lazy startup.** Tool-schema discovery and bounded caching are implemented.
   Next, defer optional MCP server processes/connections, then benchmark startup
   latency and task success on identical tasks. Preserve toolset restrictions,
   plan mode and approvals across that change.
2. **Improve memory continuity.** Add provenance links to the originating chat,
   model-assisted proposals for corrections, duplicate/contradiction review and
   personal-plus-active-project recall. Test opt-out during in-flight work and
   keep every change visible and reversible in Brain.
3. **Reach shared work.** Add explicitly connected team-channel listeners,
   channel-scoped context and event-triggered work with durable recovery. Verify
   identities, per-channel permissions and outbound-message authorization.
4. **Verify everyday use.** Exercise first connection → chat → coding approval →
   voice → background work → Brain on desktop and narrow screens. Actual speech,
   provider accounts and external service reliability need live integration
   checks beyond the deterministic fixture suites.
