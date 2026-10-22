# Scheduled tasks

Open **Tasks** in the sidebar and choose **New recurring task**, or start with
**Morning briefing**, **Project check-in** or **Research update**. Starters only
prepare an editable draft; they never start work or change permissions. Give the
task a name and instructions, then choose when it should run. The current default
workspace is filled in; change it under **Project folder and run limit**.
A connected chat provider is needed to execute a task. Saving a task does not call
that provider. **Run now** starts a run immediately, including for a paused task.

Available schedules are daily, selected weekdays, intervals in minutes, once,
and manual. Daily and weekly tasks use the IANA time zone you enter, such as
`Asia/Tokyo`, `America/Chicago`, `Europe/Paris` or `Asia/Shanghai`. One-time tasks
use the date and time shown in your device's zone. Their offset is saved with them.

Each run is a normal Omote agent chat with the chosen project, tools, model and
permissions. It can use configured coding, browser, desktop, creative and MCP
tools. **Ask first** is the default. Questions and tool approvals appear in the
run's chat; **Needs you → Open chat** brings you to them. A schedule does not
approve purchases, messages or credentials on your behalf. Providers may charge
for model and tool usage. The run time limit includes time waiting for your input.

**Model and tools** can pin a provider ID and model, and restrict the worker to
comma-separated tool names or groups. Blank model options use the current chat
defaults at run time; a blank tool list enables all available tools under the
chosen permission mode. Requests made through the agent's `schedule` tool inherit
the current chat's provider, model, project and available tools, use Ask first,
and exclude the scheduling tool itself. Creating or changing scheduled work is
subject to the normal execution approval policy. Ask for recurring work explicitly;
the tool's instructions prohibit unsolicited follow-ups.

## Following and controlling work

- **Tasks** combines active chats, Studio jobs and scheduled runs. Filter by
  **Needs you**, **Working** or **Results**; requests for input and failures appear
  first. Studio includes all active generations and the 20 newest finished jobs.
  Ordinary idle chats stay in conversation history. This index displays status
  and links; it never copies messages between chats or grants approvals.
- Scheduled runs show status, result summaries and errors. **Run history** on a
  task shows up to 100 recent runs. Open the corresponding chat for the transcript,
  tool results and approvals; completed chats can be reopened after a restart.
- **Pause** prevents future automatic runs. It does not cancel work already running.
  **Stop run** interrupts that run and closes its background workers and resources.
- Edits apply to future runs. A started run retains a snapshot of its instructions
  and settings, so editing the task does not rewrite its history.
- **Delete** removes the task and its run metadata. Stop active work first. Its
  chats remain in conversation history for export. To remove a saved transcript,
  close its chat and use `omote sessions delete <session-id>`.
- The runner follows background work to completion and asks the model to report
  its results. The time limit also applies to background work.
- Closing Tasks keeps an unsaved form draft for this page session. **Cancel**
  discards it. A page reload does not preserve an unsaved draft. Automatic refresh
  pauses while editing or reading expanded results, and stops when Tasks closes.

## Saved results and recovery

**Tasks → Results** finds saved chat replies, scheduled reports and generated
media after a restart. Search by title, reply text or filename, expand a saved
reply, download a file or open its original chat. Results load only when you open
this view or choose Refresh; they do not poll while you read. **Show more results**
loads another page. Browsing and downloading do not need a connected AI provider.

Chats interrupted by an unclean restart appear under **Needs you**. **Review saved
work** opens their result; **Review saved conversation** displays recent messages
without rebuilding an agent or calling a model. **Save conversation** downloads
the full saved transcript, including tool logs. **Continue in chat** reopens the
original chat with its own context; work starts only after you send a message.
An interrupted schedule stays paused until you explicitly resume it.

Results use existing transcripts, schedule records and media files, rather than
keeping a second copy. Deleting the original removes it from the inbox. A chat
result is its latest saved reply, not an archive of every turn. The inbox examines
the latest 500 records in each source. Reply previews read at most the last 64 KiB
of a transcript and show up to 6,000 characters; a very long message may have no
preview. Recovery previews show up to 12 recent text messages, 2,000 characters
each. Older messages, reasoning and tool logs remain in the full transcript.

Up to eight existing local files explicitly linked in a reply can be downloaded.
They must resolve inside that chat's original project; URLs, missing files and
symlinks outside the project are excluded. The boundary is checked again when
downloading. These are the current files, not immutable copies of an earlier
version. Files outside the project and files mentioned without a Markdown link
remain accessible through the original chat's usual tools, not this inbox.

## Local execution and recovery

Keep this computer awake and Omote running. There is no hosted runner or delivery
to Slack/email built into schedules. Up to three tasks run concurrently; each task
can have only one active run. Other due work waits for a slot. No real account or
model call is needed to test the scheduling implementation.

Missed times are combined into one catch-up run when the runner becomes available.
Intervals advance from that run; daily and weekly tasks retain their wall-clock
time. During daylight-saving changes, a repeated time runs once, and a nonexistent
spring time moves forward by the size of the gap. A one-time task has no next run
after its automatic execution.

An unclean shutdown marks unfinished work interrupted and pauses affected tasks
on startup. Review the chat and resume explicitly: an interrupted action may already
have changed something. Failed runs are not retried immediately; an enabled
recurring task can run again at its next scheduled time. A process lock prevents
two daemons sharing a data folder from executing the same schedules.

Tasks and run snapshots are stored locally in `schedules.db` in Omote's data folder.
Provider keys remain in the connection store; schedules contain provider IDs and
model names, not copies of credentials. Prompts and outputs may contain personal
information, just like chat. Avoid putting secrets into instructions.

## Verification

`tests/test_scheduling.py` covers civil times, DST, persistence, duplicate runners,
missed schedules, approvals, cancellation, background completion and tool scope.
`tests/test_schedules_browser.py` exercises task creation/editing/pause/deletion,
responsive forms, completed results and reopening a saved chat against an isolated
daemon and local fake model. `tests/test_tasks_browser.py` covers status filters,
editable starters, draft preservation, polling cleanup and small-screen navigation.
`tests/test_task_activity.py` covers active-job retention and scheduled-chat
deduplication. These tests do not prove a particular third-party
website will allow an unattended workflow.

`tests/test_results.py` covers restart persistence, source deletion, bounded
previews, deduplication, pagination and project-file boundaries.
`tests/test_results_browser.py` checks actual downloads, offline recovery,
read-only browsing, search, late responses and narrow screens. The UI workflow
also runs these cases on Windows/Chromium and macOS/WebKit.
