# Scheduled tasks

Open **Scheduled** in the sidebar and choose **New task**. Give the task a name,
instructions and an existing project folder, then choose when it should run.
A connected chat provider is needed to execute a task. Saving a task does not call
that provider. **Run now** starts a run immediately, including for a paused task.

Available schedules are daily, selected weekdays, intervals in minutes, once,
and manual. Daily and weekly tasks use the IANA time zone you enter, such as
`Asia/Tokyo`, `America/Chicago`, `Europe/Paris` or `Asia/Shanghai`. One-time tasks
use the date and time shown in your device's zone. Their offset is saved with them.

Each run is a normal Omote agent chat with the chosen project, tools, model and
permissions. It can use configured coding, browser, desktop, creative and MCP
tools. **Ask first** is the default. Questions and tool approvals appear in the
run's chat; **Recent runs → Open chat** brings you to them. A schedule does not
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

- **Recent runs** shows status, result summaries and errors. **Run history** on a
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
daemon and local fake model. These tests do not prove a particular third-party
website will allow an unattended workflow.
