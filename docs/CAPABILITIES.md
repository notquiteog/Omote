# Capability audit

This is an implementation map, not a claim that Omote outperforms every competing
product. Product documentation establishes comparison areas; repository tests
establish specific Omote behaviors. A mocked service test does not establish live
provider availability or quality. No comparative performance benchmark has been
completed.

| Area | Implemented in Omote | Setup or remaining gap |
|---|---|---|
| Coding and project work | File edits, patches, shell, Git/worktrees, review, LSP, checkpoints, subagents, background tasks, project instructions and tool approvals | Language servers and project dependencies must be available. Hosted isolated coding environments are not bundled. |
| Continuing responsibilities | Durable local scheduled tasks, recurring civil times, run history, visible approvals, cancellation and background-worker follow-through | Computer and daemon must remain available. No hosted wake-ups, event-triggered jobs or cross-channel delivery. See [Scheduled tasks](SCHEDULES.md). |
| Business and personal workflows | Browser-driven employee portals, email tools, travel planning, calendar and ledger tools, user questions and MCP integration | Connect each service; authentication, purchases and outbound communication still need the appropriate user action/approval. No universal EMS or booking API. |
| Browser and computer use | Playwright browser sessions, page inspection, screenshots, desktop stages and input tools | Browser runtime, desktop platform support and app permissions are required. Website restrictions and inaccessible UI remain limitations. |
| Team interaction | Browser and configured MCP connections can reach supported communication services | No native Slack/Teams bot identity, channel listener, channel-level policy store or enterprise team account service is bundled. |
| Voice | LLM + STT + TTS pipeline and OpenAI Realtime integration with agent tools | Requires compatible services, microphone permission and audio hardware. This is not a login to the ChatGPT Instant consumer application. |
| Memory | Opt-in embedding memory, user/project scope, inspection, correction, export and deletion | Connect a compatible embedding model. Quality depends on retrieval and the models used. |
| Images and video | Provider-specific generation forms, references, job progress/cancellation and media gallery | Model/account availability, supported parameters, costs and regional access depend on providers. |
| ComfyUI | Full connected native editor, Nodes 2.0 when installed, bridge capture/load, immutable versions, comparison, schema validation and owned run/output history | Requires ComfyUI, relevant weights and custom nodes. Omote adds a workbench around its native renderer. Compatibility with every extension has not been established. |
| Characters and 3D | Reusable descriptions/reference images, generation recipes, asset associations, import/export and interactive previews | References do not guarantee identity consistency. 3D generation depends on connected models/workflows; the viewer is not a full 3D authoring application. |
| Creative applications | PhotoCraft integration, media-editing tools, desktop control and configurable MCP tools | PhotoCraft/Adobe/GIMP and their supported automation interfaces must be installed or connected. |
| Companions and onboarding | Animated companions with distinct reactions, optional tour, provider setup, responsive workspace, accessible companion menu | Behavior and accessibility have automated browser coverage, not comprehensive user research across every platform and assistive technology. |

## Verification map

- Agent behavior: `tests/test_agent.py`, `tests/test_background_tasks.py`,
  `tests/test_sessions.py`, and the approval, tool, Git and browser suites.
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
- [OpenClaw automation documentation](https://docs.openclaw.ai/automation/cron-jobs):
  persistent scheduled work and delivery mechanisms.
- [ComfyUI Nodes 2.0](https://blog.comfy.org/p/comfyui-node-2-0): the native graph
  rendering system that Omote embeds rather than reimplementing.

Account permissions, regional service availability, language/model quality and
third-party policies remain real constraints. "Anyone, anywhere, any application"
is an ambition, not a tested release criterion.
