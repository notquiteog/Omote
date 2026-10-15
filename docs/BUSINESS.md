# Personal and business work

Omote combines tools that operate real accounts, files, browsers and desktops.
Availability depends on the installed applications, configured accounts, chosen
model, accessible network and each site's rules. It is not a universal API for
every employer, hotel, country or application, and has no demonstrated feature
parity with every commercial agent product.

## Everyday tasks

| Task | Available implementation | Setup or boundary |
| --- | --- | --- |
| Read, search, draft, send and reply to email | Native mail tool; browser webmail | Configure an account in Mail settings, or sign into webmail. Native tool does not attach files. Sends require final confirmation. |
| Employee management and administration | Playwright browser workflow, configured MCP connectors, local HR ledger | `business-admin` skill uses the actual portal; there is no bundled vendor-specific EMS connector. Local HR records do not update an employer's system. |
| Hotel research and reservations | Web research + browser + durable trip planner | Inventory, checkout and cancellation terms belong to the actual provider. Final reservations require confirmation, including pay-at-property. |
| Flights | Live airline/agent pages + browser + trip legs/booking references | Compare airports, time zones, fare class, baggage and the final total. Final checkout requires purchase confirmation; a quoted fare is not a ticket. |
| Job search and applications | `career` records, `job-search` workflow, web research, document exports and browser uploads | Save role/city/work-mode criteria and actual employer URLs. Review documents and screening answers before sharing; record submitted only after an observed receipt. |
| Resumes | Project-scoped base/tailored records; editable DOCX, PDF, HTML and TXT export; DOCX/PDF text reading | Preserve user-provided facts. PDF creation requires Chromium; PDF reading uses the docs extra. No guaranteed ATS score, interview or offer. |
| Vacation and business-trip plans | Trip intake, itinerary, budgets and calendar conflicts | Times use explicit offsets. Separate currencies stay separate unless a sourced conversion is supplied. |
| Read and operate a screen | Desktop screenshot and pointer/keyboard tools | Needs desktop dependencies, an available screen and a model that can interpret images. |
| Drive websites | Real Playwright browser and numbered live page elements | Install browser dependencies/Chromium. Native dropdowns accept exact option values or labels. Custom widgets use their observed visible controls. |
| Clarify an ambiguous task | `ask_user` and task planning tools | Questions wait for an actual answer. A timeout is not approval. |
| Edit layered graphics | PhotoCraft tool, PhotoCraft live MCP bridge, Adobe desktop workflow | Install the chosen editor; see below. |

Bundled skills are loaded by the agent when relevant and are also available as
slash commands: `/global-assistant`, `/business-admin`, `/email`, `/world-travel`,
`/job-search`, `/photocraft` and `/photoshop`. They contain concrete preparation, inspection,
review and verification steps, not promises that an account is already connected.

## Jobs, resumes and applications

Choose **Find jobs & build a résumé** in a new chat, or ask naturally: “Find
technical support jobs in Chicago within 30 km, hybrid preferred, and help me
apply using my experience.” The starter prepares an editable request and preserves
an existing draft. The agent can load `job-search` and discover the lazy `career`
tool. Use a browser-capable installation and current web sources for live listings;
the local record tool does not invent job inventory or submit applications.

Career records retain the search criteria, resume variants and a shortlist with
source URLs, notes, document paths and application status. Updates require the
current revision, so two chats cannot silently overwrite each other. Tracking
parameters and fragments are removed for duplicate detection; job identifiers in
the query remain. `ready` means prepared. `submitted` and other external outcomes
need confirmation evidence from the actual page or the user's verified report.
The record is evidence supplied to Omote, not an independent check of an employer's
database. Check the source again if a result is stale or the submission response
was lost.

Example `career` calls:

```json
{"action":"save","kind":"search","fields":{"roles":["Technical support","Customer support"],"city":"Chicago, IL","work_mode":"hybrid","radius_km":30}}
```

```json
{"action":"save","kind":"resume","fields":{"name":"Your name","contact":["City","email@example.com"],"sections":[{"name":"Experience","entries":[{"title":"Your actual role","subtitle":"Actual employer","dates":"Actual dates","bullets":["A responsibility or result you can substantiate."]}]}]}}
```

Use `export` with that resume ID and a separate `.docx`, `.pdf`, `.html` or `.txt`
path. DOCX/HTML/TXT need only the base install. PDF needs the browser extra and
`python -m playwright install chromium`; HTML can also be printed to PDF manually.
The layout is single-column with selectable text; retain the editable DOCX and
check long sections, page breaks and fonts before sending. Original resume sources
are retained as private record notes, not printed. Tailor a separate record for a
job rather than replacing the base resume. Word imports read bounded local OOXML
body/header/footer text without running macros or fetching external resources.

`browser_upload` attaches selected files to an enabled file input observed by
`browser_read`. It confines files to allowed directories, checks nonempty files,
20 MiB total, multiple-file support and the observed accepted extensions/MIME types.
Selecting a file can send it immediately, so upload requires message approval
even before final submission. The tool never clicks Submit. Custom hidden inputs
need the browser handoff. Final application buttons and Enter-to-submit are graded
as messages; the model also supplies explicit message intent for unfamiliar labels.
Login, CAPTCHA and sensitive identity steps can be completed by the user in the
browser. No step guesses eligibility, identity details or optional demographic answers.

Records live under `career/<user-and-project hash>/records.sqlite3` in the Omote
data directory. They are local, readable and separate from vector memory. Inspect
them with `list`/`get`, export resume documents, and remove records with `delete` plus
the current revision. Exported files and documents already sent elsewhere are
independent copies; deleting a record does not remove those copies.

## Booking flights and hotels

Choose **Plan & book travel**, or specify origin, destination, dates, travelers,
budget and preferences in chat. `world-travel` and `trip` retain details across
turns. Search/inspect the current provider, compare complete costs and terms, then
prepare checkout. Flights retain airport codes, offset-aware local times, fare and
baggage details; hotel comparisons include actual room occupancy, nights, mandatory
fees, deposits and cancellation deadlines. Save observed booking references and
terms on itinerary legs. Use exact leg IDs when names repeat.

Final reservation and payment actions require purchase confirmation in every
mode. After approval, inspect the receipt and only then mark booked. A failed
response needs a booking-history check before retrying. Cancellations can incur
fees; inspect terms and verify cancellation before clearing booked status. The
trip tool stores the plan and evidence; actual bookings use the selected provider's
browser interface or an explicitly configured connector.

## Browser EMS workflow

Give Omote the portal URL and task, for example: “Open our employee portal, find
Tanaka's approved leave by employee ID, and prepare the timesheet correction.”
The agent reads current page state, resolves duplicate identities, checks the
form's actual values and shows the proposed changes. Sign in through the browser
handoff to keep credentials out of conversation. The agent prepares a consequential
submission, asks any missing human decision and proposes the final button for
confirmation. It then reopens the record to verify the saved status.

For leave, pay, hiring, dismissal or employee evaluation, the agent can gather
facts and execute a person's stated decision; it should not make the underlying
employment judgment from profiling or historical outcome predictions. The local
`hr` tool stores decisions and evidence only. Use a configured MCP server for a
real API when one is available, rather than inventing vendor endpoints.

## Languages and locations

Workflows preserve original-script names and distinguish language, time zone,
currency, country and citizenship. They ask when an ambiguous date or location
changes the result, use ISO dates for clarity and include offsets in itineraries.
Current legal, entry, availability and price information needs current sources.
Sites blocked or unavailable in a region remain unavailable; Omote does not bypass
CAPTCHAs, authentication or geographic access restrictions.

Browser approval classification recognizes common purchase/message/secret labels
in Japanese, simplified/traditional Chinese, Korean, several European languages,
Russian, Arabic and Thai, along with HTML password/autocomplete metadata. This
is a conservative heuristic, not a comprehensive translation system. Before a
final commitment in **any** language, the agent sets `browser_click.intent` to
`purchase` for bookings/payments or `message` for sends and consequential external
submissions. That field only raises the risk, so it cannot downgrade a payment.
Enter-to-submit is also checked against the form's submit labels and composer type.
Native mail sends and MCP tools that identify themselves as sends are covered by
the message-confirmation policy. Browser values identified as secrets are withheld
inside the page before being returned to the model.

## PhotoCraft

PhotoCraft is a separate open-source editor. Install its CLI from the
[official project](https://github.com/storytold/photocraft), put `photocraft-cli`
on PATH, or set an absolute executable path:

```dotenv
OMOTE_PHOTOCRAFT_BIN=/absolute/path/to/photocraft-cli
```

The `photocraft` tool uses its documented headless MCP interface. It offers:

- `status`: checks executable discovery without editing anything.
- `commands`: discovers command IDs and parameter descriptions, optionally filtered.
- `inspect`: opens a source file and returns the layer/document report and PNG preview.
- `edit`: opens a source or creates a document, executes ordered engine commands,
  saves to a separate output, verifies a nonempty file and returns a report/preview.

Example tool arguments after inspecting the actual command catalog:

```json
{
  "action": "edit",
  "source": "portrait.psd",
  "commands": [{"id": "layer.new.layer", "params": {"name": "Retouch"}}],
  "output": "portrait-working.psd"
}
```

Paths are checked against the Omote session's allowed directories. The subprocess
receives only the source parent as its read root and the output parent as its
write root, using PhotoCraft's automation flags; inspect has no write root.
Commands use JSON RPC, not interpolated shell commands. File/preference/script
commands are refused by the wrapper, and the installed PhotoCraft engine enforces
its own automation rules on nested operations. Existing outputs require explicit
`overwrite` and a destructive assessment. The original source cannot be replaced.

Each call ends its engine process, including on cancellation or failure. Save PSD
or `.pcraft` for editable state and reopen that file to continue. PNG/JPEG exports
may flatten layers; import/export warnings are returned and must be reviewed.
A command failure prevents the final save; a preview failure after a successful
save does not undo that saved file. PhotoCraft's format fidelity and command
support are determined by the installed release.

For persistent editing of the actual desktop app, configure PhotoCraft as a normal
MCP server. Start the desktop with an authenticated control channel and explicit
roots, then configure the bridge using the
[official MCP instructions](https://github.com/storytold/photocraft/blob/main/docs/mcp.md)
and [development guide](https://github.com/storytold/photocraft/blob/main/docs/development.md):

```json
{
  "mcpServers": {
    "photocraft-live": {
      "command": "/absolute/path/to/photocraft-cli",
      "args": ["mcp", "--bridge", "127.0.0.1:7878", "--control-token-file", "/private/path/control.token"],
      "trustHints": false
    }
  }
}
```

The token stays in the private file. The desktop process owns its configured
read/write roots. Omote discovers the server's actual tools for UI inspection,
layer edits, commands, pointer actions and screenshots. No Adobe installation is
required for PhotoCraft. Adobe Photoshop remains a separate installed-app workflow.

## Validation and limits

Automated tests cover PhotoCraft's actual stdio transport with a deterministic
fixture, output confinement, original preservation, failure-before-save, preview
forwarding and subprocess cleanup. Chromium tests exercise a localized secret
field, form submission risk and a native dropdown. Regional action labels and
message approval are regression-tested across approval modes.

A manual integration smoke test used the official PhotoCraft 0.6.0 Linux CLI,
verified against its published archive checksum: command discovery, creation of a
layered PSD containing a Japanese layer name, reopening and inspecting that PSD,
PNG export and image preview all succeeded. Full command discovery and a
512×512 noisy image preview (767,408 decoded PNG bytes) also succeeded, exercising
MCP framing beyond its previous 64 KiB limit. The export's layer-flattening warning
was returned. This validates the adapter's basic real behavior, not every engine
command, file format, platform or live desktop bridge.

No test sends real mail, changes an employer record or books travel. Those require
configured accounts, live availability and user confirmation at the final step.

Career tests also render a real selectable-text PDF, reopen DOCX text, check revision
conflicts/project isolation/deduplication and run a real Chromium job-search →
document-upload → application-receipt walkthrough against a local fixture. A flight
fixture verifies fare details, final purchase review and receipt inspection. These
fixtures make no external job applications or purchases and do not establish every
vendor's compatibility. A separate real WordCraft test reopens the exported DOCX
and renders its page to verify an independent editor can read it.
