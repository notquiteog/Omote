# Personal assistant capabilities

Omote supports multi-step personal work through chat, voice, web research,
browser/desktop control, configured services, local records and scheduled work.
Start with **Find jobs & build a résumé**, **Plan & book travel**, or a natural
request. Starters prepare editable messages and preserve drafts. See
[Jobs and travel](BUSINESS.md) for setup, document exports and verified workflows.

Reviewed October 15, 2026 against Google's current Android/Gemini documentation.
Android version alone does not determine assistant availability: account, device,
language, region, app support and rollout state also matter. Google's
[screen automation guide](https://support.google.com/gemini/answer/16940971?hl=en)
describes multi-step shopping, food, rides, travel and ticket tasks in supported
apps, with progress monitoring, stopping and transaction handoff. The table below
maps those categories to actual Omote capabilities, rather than claiming universal
parity from similar feature names.

| Capability | Omote implementation | Verification or remaining work |
|---|---|---|
| Jobs by role/location | Live web/browser research plus persistent `career` search criteria, employer source URLs, shortlist, resume variants and application evidence | Real browser local-fixture role/city search, document upload and application receipt tested. Live employer accounts/ATS sites need individual verification. |
| Resume and document creation | Editable DOCX and selectable-text PDF, HTML/TXT, DOCX body/header/footer reading and optional PDF reading | Actual PDF text/page count tested; exported DOCX independently reopened/rendered in WordCraft. Long documents and fonts still need preview. |
| Flights and hotels | Browser booking workflow plus durable trip intake, prices, time zones, receipt references and budget/calendar checks | Hotel walkthrough and flight checkout/receipt tested with local sites. No external booking or card charge performed. |
| Shopping, food, rides and tickets | Existing browser/desktop tools and configured MCP connectors, message/purchase review, visible task progress and Stop | General tools are available; each provider, service region, account and checkout flow still needs live testing. No bundled universal inventory/ordering API. |
| Conversation and voice | Chat, STT/TTS and Realtime voice integration with the agent's allowed tools | Requires compatible models/services, microphone permission and audio devices. No Android wake-word service. |
| Screen context and actions | Browser reads/screenshots, desktop screenshots/input and isolated stages where supported | Works on the connected computer. No native Android overlay or phone screen-control service. |
| Connected services | Native mail/calendar features, web applications and operator-configured MCP servers | Requires connected accounts and their supported interfaces. A connector is not evidence of access to every consumer app. |
| Background and recurring work | Autopilot/background jobs, local scheduled tasks, visible progress, approvals and cancellation | Computer/daemon must remain running. No hosted always-on service while the machine is off, general event subscriptions or push-notification service. |
| Memory and personalization | Opt-in vector memory/Brain plus project-scoped local workflow records | Users can inspect/correct/export/delete memory. Career data is stored separately and is not automatically copied into Brain. |
| Native calls, SMS, contacts, alarms, phone notifications/settings | Not implemented as native phone services | Needs an explicitly paired Android companion, device permissions and per-action review. Browser equivalents or configured services are separate capabilities. |
| Smart home and device media | Configured service connectors or available desktop/browser interfaces | No bundled Google Home integration, Android media session service or universal local-device discovery. |
| Native Android app functions | Not implemented | Android's [AppFunctions library](https://developer.android.com/jetpack/androidx/releases/appfunctions?hl=en) lets device assistants discover/execute app capabilities. Omote currently uses MCP/browser/desktop tools instead. |
| Coding, local models and creative work | Coding/Git/LSP, managed local-model downloads and runtimes, system monitoring, 13 native ArtCraft engines, ComfyUI authoring and native editor | These are additional Omote work areas; they do not establish that every Android feature is surpassed. See [capability audit](CAPABILITIES.md). |

Google separately documents Android-specific
[connected apps](https://support.google.com/gemini/answer/13695044),
[device assistance](https://support.google.com/gemini/answer/15235441?hl=en-mp),
and [calling](https://support.google.com/gemini/answer/15575143?hl=en-0).
Those OS integrations cannot be supplied by a desktop web page alone. Google's
[Gemini Spark announcement](https://blog.google/innovation-and-ai/products/gemini-app/next-evolution-gemini-app/)
also describes cloud-backed proactive workflows; Omote's current scheduler is local.

For end-to-end work, prepare and review the concrete result before the final
external commitment. Uploads can share files immediately and require message
approval; application sends use message intent, and travel/commerce commitments
use purchase intent. Inspect the actual receipt/status before marking success,
and check history before retrying an uncertain submission. Credentials and identity
steps can be completed through the browser handoff. Model judgment, vendor account
access and live inventory remain separate from deterministic tool correctness.

Tests: `tests/test_career.py`, `tests/test_personal_workflows_browser.py`,
`tests/test_personal_starters_browser.py`, `tests/test_global_workflows.py`,
`tests/test_trip.py`, `tests/test_booking_walkthrough.py` and the browser approval
suites. They verify concrete behaviors, not an exhaustive comparative benchmark.
