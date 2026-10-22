# Capability audit

This is an implementation map, not a claim that Omote outperforms every competing
product. Product documentation establishes comparison areas; repository tests
establish specific Omote behaviors. A mocked service test does not establish live
provider availability or quality. No comparative performance benchmark has been
completed.

| Area | Implemented in Omote | Setup or remaining gap |
|---|---|---|
| Coding and project work | File edits, patches, shell, Git/worktrees, review, LSP, checkpoints, subagents, background tasks, project instructions and tool approvals | Language servers and project dependencies must be available. Hosted isolated coding environments are not bundled. |
| Local execution and resources | Package installation, shell/background work, per-command root/admin access, live System panel, managed GGUF and ComfyUI safetensors/workflow downloads with cancel/resume/checksums, disk/memory preflight, live ComfyUI node/model compatibility, owned llama.cpp runtime start/stop and local chat | One transfer per library and one chat runtime; Vulkan/Metal need OS drivers. Linux NVIDIA Qwen chat and Studio SD Turbo image generation tested. ComfyUI can use an existing installation or a managed private runtime. Native Mac/Windows GPU runs and admin dialogs need verification; automatic multi-file diffusion packages and other runtime adapters remain future work. |
| Continuing responsibilities | Unified Tasks view for active chats, Studio jobs and scheduled runs; attention filters, editable briefing/project/research starters, durable local schedules, run history, approvals, cancellation and background-worker follow-through | Computer and daemon must remain available. No hosted wake-ups, event-triggered jobs or cross-channel delivery. See [Scheduled tasks](SCHEDULES.md). |
| Business and personal workflows | Browser-driven employee portals, email tools, travel planning, calendar and ledger tools, user questions and MCP integration | Connect each service; authentication, purchases and outbound communication still need the appropriate user action/approval. No universal EMS or booking API. |
| Jobs and resumes | Role/city/work-mode search records, source-linked job shortlist, application tracking, base/tailored resumes, DOCX/PDF/HTML/TXT exports, DOCX reading and approved browser uploads | Live listings use web/browser tools. PDF creation needs Chromium; PDF reading needs the docs extra. Local-fixture search/upload/application receipt tested; no live employer submission. See [Personal assistant map](PERSONAL-ASSISTANT.md). |
| Browser and computer use | Playwright browser sessions, page inspection, screenshots, desktop stages and input tools | Browser runtime, desktop platform support and app permissions are required. Website restrictions and inaccessible UI remain limitations. |
| Guided tool setup | In-app ComfyUI and Playwright installation/configuration, private runtimes, progress, cancellation, service start/stop and connection checks | Install recipes cover macOS, Linux and Windows; native CI verifies installers and UI. Actual GPU/driver compatibility on every device and ComfyUI extension is not established. |
| Team interaction | Browser and configured MCP connections can reach supported communication services | No native Slack/Teams bot identity, channel listener, channel-level policy store or enterprise team account service is bundled. |
| Voice | LLM + STT + TTS pipeline and OpenAI Realtime integration with agent tools | Requires compatible services, microphone permission and audio hardware. This is not a login to the ChatGPT Instant consumer application. |
| Memory / Brain | Opt-in vector memory, personal/project filters, local text search across pages, offline manual saves, correction, export and deletion; deduplicated automatic fact capture | Connect a compatible embedding model for semantic recall. Automatic capture uses conservative patterns; no background model-led reconciliation of contradictions or hierarchical memory browser. |
| Tool/context efficiency | Small core plus searchable on-demand schemas, bounded optional-schema cache, deferred creative/scheduling implementations, selected native creative engines start on demand, toolset narrowing, skill loading, compaction, batched reads and quantized vector search | Operator-configured MCP servers still initialize at startup. Latency/task-success and comparative oh-my-pi benchmarks remain unmeasured. See [Tool loading](EXTENDING.md#tools-load-as-needed). |
| Images and video | Provider-specific generation forms, references, job progress/cancellation and media gallery | Model/account availability, supported parameters, costs and regional access depend on providers. |
| ComfyUI | Full connected native editor, Nodes 2.0 when installed, API-to-native graph import, agent authoring/atomic patches, immutable versions, comparison, live schema validation, exact-revision runs and output history | Requires ComfyUI, relevant weights and custom nodes. Real agent-authored graph/native-editor/GPU roundtrip verified on Linux. Compatibility with every extension has not been established. |
| Characters and 3D | Reusable descriptions/reference images, generation recipes, asset associations, import/export and interactive previews | References do not guarantee identity consistency. 3D generation depends on connected models/workflows; the viewer is not a full 3D authoring application. |
| Creative applications | Stateful access to all 13 native ArtCraft engines' actual MCP catalogs, creative MCP connectors, native editing/export tools, image/SVG checks and FFmpeg media inspection/full decoding | Engines/apps must be installed; Adobe needs a supported bridge or desktop control and its license. All 13 engine connections tested; Photo/Vector/Film/Effect real edit/save/reopen/export tested. Every format, command and model has not been verified. See [Creative apps](CREATIVE.md#creative-apps-and-export-checks). |
| Companions and onboarding | Animated companions with distinct reactions, optional tour, provider setup, responsive workspace, accessible companion menu | Behavior and accessibility have automated browser coverage, not comprehensive user research across every platform and assistive technology. |

## Verification map

- Agent behavior: `tests/test_agent.py`, `tests/test_background_tasks.py`,
  `tests/test_sessions.py`, and the approval, tool, Git and browser suites.
- Local resources and administrator access: `tests/test_resources.py`,
  `tests/test_elevation.py`, `tests/test_system.py`, and desktop/mobile System
  coverage in `tests/test_workspace_browser.py`.
- Guided setup: `tests/test_tool_setup.py`, `tests/test_tool_setup_browser.py`;
  `tests/test_onboarding_browser.py` holds editor discovery pending while checking
  that Studio's connection action is usable.
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
- Scheduled work and UI: `tests/test_scheduling.py`, `tests/test_task_activity.py`,
  `tests/test_schedules_browser.py`, `tests/test_tasks_browser.py`.
- Creative workspace and node metadata: `tests/test_workbench.py`,
  `tests/test_workbench_review.py`, `tests/test_workflow_diff.py`,
  `tests/test_creative_browser.py` and the ComfyUI bridge tests.
- Creative engines and agent authoring: `tests/test_creative_apps.py`,
  `tests/test_creative_exports.py`, `tests/test_comfy_workflow_tool.py`,
  `tests/test_creative_apps_browser.py` and `tests/test_mcp_creative_cancellation.py`
  cover persistent sessions, permissions, lazy cleanup, atomic edits, export
  fidelity checks, mobile layout, preserved drafts and actual RPC cancellation.
  Separate live tests connected 13 native engines, edited/reopened/exported with
  four, and authored/edited/captured a seven-node graph in ComfyUI's official
  frontend before generating and retaining an SD Turbo image on an RTX 5060 Ti.
  No live Adobe bridge or model-specific creative benchmark was performed.
- User flows: `tests/test_onboarding_browser.py`, `tests/test_workspace_browser.py`,
  `tests/test_companion_browser.py`.
- Personal workflows: `tests/test_career.py`, `tests/test_personal_workflows_browser.py`
  and `tests/test_personal_starters_browser.py` verify revision-safe records,
  project/user isolation, actual resume exports, browser uploads, application and
  flight receipts, final-action review, mobile starters and draft preservation.
  The exported DOCX also reopened/rendered in a real WordCraft engine. Native
  Android device services and full Android/Gemini feature parity are not implemented;
  the [comparison map](PERSONAL-ASSISTANT.md) records those gaps explicitly.
- Service boundaries: mail, trip, memory, PhotoCraft, voice and Realtime suites.
  Local fakes and transport fixtures avoid sending real mail or paying for test
  generations; live account verification remains separate.

## Comparison sources

Reviewed against the requested product categories on October 15, 2026. This is
a category audit, not an exhaustive enumeration of every product feature.

The Duet, dots, OpenClaw and Grok Bot comparison below was refreshed on October 22,
2026. These products change quickly; the linked documentation is the source for
their advertised behavior, not proof that Omote already matches it.

| Reference | Useful product pattern | Omote status and remaining work |
|---|---|---|
| [Claude Duet](https://support.claude.com/en/articles/17631495-get-started-with-claude-duet) | Ongoing assistant work, briefings, voice, connected services, a cloud computer and a place to review work needing input | Tasks now surfaces requests for input and starter routines. Omote runs locally; a hosted computer that continues after the user's computer shuts down is not bundled. |
| [ChatGPT dots](https://learn.chatgpt.com/docs/dots/tasks-and-memory) | Persistent responsibilities, delegated background work, relevant memory and visible activity | Local scheduling, background workers and memory exist. Event-triggered responsibilities, hosted execution and a unified deliverables inbox remain gaps. Each chat keeps its own transcript. |
| [OpenClaw](https://docs.openclaw.ai/concepts/features) | Channel adapters, isolated sessions, cron/heartbeats, voice, plugins and paired devices | Omote has tools/MCP, browser and desktop control, voice and local schedules. Native messaging-channel listeners, channel policies, phone pairing and cross-device synchronization need implementation. |
| [Grok Bot](https://docs.x.ai/grok-bot/overview) and [skills/routines](https://docs.x.ai/grok-bot/skills-routines-and-automations) | Persistent assistants, voice, a cloud computer, routines and skills taught by browser demonstration | Reusable prompts, skills and local routines exist. Recording a demonstrated browser workflow into a reviewable skill, persistent hosted computers and event-triggered routines do not. |

The October 22 UI changes prioritize one message box on the home screen, a Tasks
view with filters and editable routine starters, and advanced settings behind
disclosures. Opening a task index does not resume work, approve actions or merge
chat context. Studio's setup action loads independently of local editor discovery;
editor probing runs outside the server event loop, including on Windows.

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

Prioritize successful everyday tasks over additional top-level panels:

1. **Reliable follow-through.** Add a durable deliverables inbox with clear source
   chats, recovery after interruption, and explicitly configured notifications.
   Extend scheduling to event triggers only with deduplication and visible control.
2. **Guided connections and resources.** Bring model, browser, creative-app and
   messaging setup into understandable checklists with connection tests. Add
   resource limits to managed inference across platforms; the Linux test launcher
   is not yet a cross-platform in-app memory guarantee.
3. **Memory users can trust.** Link every captured fact to its source, propose
   corrections and resolve conflicts visibly. Personal-plus-active-project recall
   already exists; automatic contradiction review and reversible edit history do not.
4. **Speech and reach.** Verify interruption, wake-word behavior and recovery with
   real microphones and providers, then add opt-in channel adapters and paired
   devices with explicit per-channel context boundaries.

Earlier engineering milestones remain applicable:

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
   memory change history. Test opt-out during in-flight work and
   keep every change visible and reversible in Brain.
3. **Reach shared work.** Add explicitly connected team-channel listeners,
   channel-scoped context and event-triggered work with durable recovery. Verify
   identities, per-channel permissions and outbound-message authorization.
4. **Verify everyday use.** Exercise first connection → chat → coding approval →
   voice → background work → Brain on desktop and narrow screens. Actual speech,
   provider accounts and external service reliability need live integration
   checks beyond the deterministic fixture suites.
