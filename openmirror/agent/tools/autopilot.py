"""Handing the turn over, so nobody has to be there for it.

Autopilot used to be a mode: a rail button, a goal box, and a live view of a
screen. Every one of those pushed a decision out to the person, and the
decision was "should this be a *place* or a *turn*". It is a turn. That is the
whole of the change.

**Why a tool and not a mode.** A mode says *this is now what the application
is*. A tool says *this is what I am going to do next*, and it competes on the
same terms as everything else the harness could do instead — read a file, run
the tests, search the web. That competition is the useful part: "check this is
right and then do it" and "do it and check afterwards" and "just tell me" are
three conversations with three different toolsets, and only one of them needs
a screen. Before this, the decision of *which conversation you were having* was
made by clicking a rail button, and the model had no part in it.

**What it actually changes.** Not the policy — there is still no separate
autopilot permission level, and there must not be, because that is exactly how
an agent ends up with authority nobody granted it in a mode nobody was
watching. It appends the unattended section to this session's system prompt, so
the model's *behaviour* changes (finish the task, do not check in), and the
approval mode still decides how much it may do unasked. `trusted` is the honest
default for it. `unrestricted` is available and says what it is.

**Why the stage is required and is not negotiable here.** An agent driving a
display owns that display, and an agent owning the display owns the computer.
So it asks for a screen of *its own* — a virtual X server, its own pointer —
and says so before starting if it cannot have one. Sharing the screen you are
looking at has to be asked for in words by the person, because the difference
between "it works next to you" and "it takes your mouse" is the difference
between autonomy and being locked out of your own machine.

**What the person gets.** The run is a turn in the conversation, so it can be
read afterwards — which is the thing a mode could not offer, because a mode's
whole surface was the live view and the live view is worthless five minutes
later. The card this produces shows the screen while it runs and the narration
while it runs, and then it is just a tool call in the transcript.
"""

from __future__ import annotations

from typing import Any

from openmirror.agent.approval import Mode
from openmirror.agent.autopilot import AUTOPILOT_PROMPT, Run, autopilot
from openmirror.agent.tools.base import Assessment, Output, Tool, ToolContext, ToolError
from openmirror.protocol.agent import Risk

# The section heading, so that a session can be told twice and not end up with
# the same paragraph twice. A second call within one turn is a no-op, and says
# so, rather than appending.
MARKER = '## Working unattended'

# Named, because a model that is told "mode: trusted" and has never been told
# what the modes are will guess. These five are the whole ladder; anything else
# is a typo and is refused rather than silently becoming `ask`.
MODES = {mode.value: mode for mode in Mode}


class AutopilotTool(Tool):
    name = 'autopilot'
    description = (
        'Take over this turn and work unattended until the goal is met. For a long job — a '
        'migration, a build, an audit, a browser task with many steps — where checking in every '
        'thirty seconds wastes more time than it saves. You keep every permission you already '
        'had: this changes how you work, not what you may do, and anything destructive, anything '
        'that spends money and anything that types a secret still stops for a person.\n\n'
        'You get a screen of your own rather than the one they are looking at, so they keep '
        'working while you do. Call this when the goal needs a GUI, a browser, or many steps on a '
        'form. For anything else — reading code, running tests, fetching pages — just do it; this '
        'tool buys latency you do not need at the cost of a frame stream nobody may watch.\n\n'
        'The card this produces is in their transcript: they can watch while you run and read it '
        'afterwards. Say what you are about to do in one short line first.'
    )
    input_schema = {
        'type': 'object',
        'properties': {
            'goal': {
                'type': 'string',
                'description': 'What "done" looks like, in one or two sentences. Be specific: this '
                               'is what you will be judged against when you stop.',
            },
            'mode': {
                'type': 'string',
                'enum': sorted(MODES),
                'description': 'How much you may do without asking. Defaults to trusted, which runs '
                               'commands and network calls unattended and still stops for anything '
                               'destructive, for money and for secrets. Only lower it if the person '
                               'has asked for it.',
            },
            'share_screen': {
                'type': 'boolean',
                'description': 'Drive the screen they are looking at. Off by default and almost '
                               'never right: it means taking their mouse.',
            },
        },
        'required': ['goal'],
    }

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> Assessment:
        goal = str(args.get('goal') or '').strip()
        if not goal:
            return Assessment(risk=Risk.READ, summary='', invalid='goal is required')
        asked = str(args.get('mode') or '').strip()
        if asked and asked not in MODES:
            return Assessment(
                risk=Risk.READ,
                summary='',
                invalid=f'mode must be one of {", ".join(sorted(MODES))}',
            )

        # Graded from the arguments, which is the whole point of `assess`. A
        # tool named `autopilot` is not risky or safe; *this* call is risky or
        # safe, and what makes the difference is whether it is about to take
        # somebody's mouse.
        shared = bool(args.get('share_screen'))
        risk = Risk.EXECUTE if shared else Risk.WRITE
        return Assessment(
            risk=risk,
            summary=f'work unattended until: {goal[:80]}' + (' — on YOUR screen' if shared else ''),
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> Output:
        goal = str(args['goal']).strip()
        share = bool(args.get('share_screen'))
        stage = getattr(ctx, 'stage', None)

        if stage is None:
            raise ToolError(
                'this run needs a screen and there is not one. Install Xvfb so it can have a display '
                'of its own, or set OPENMIRROR_DESKTOP=true — without a stage there is nothing for '
                'it to look at. For work that needs no screen at all (reading code, running tests, '
                'fetching pages) do that instead of calling this.'
            )
        if getattr(stage, 'shares_pointer', False) and not share:
            raise ToolError(
                'the only screen available is the one they are looking at, so running unattended '
                'would take their mouse. Install Xvfb to give it a display of its own, or call '
                'this again with share_screen true if sharing the pointer is what they want.'
            )

        chosen = MODES.get(str(args.get('mode') or '').strip(), Mode.TRUSTED)
        if ctx.set_mode is not None:
            await ctx.set_mode(chosen.value)

        session_id = ctx.session_id
        entry = autopilot.get(session_id)
        if entry is not None:
            # Already running for this session. One screen, one narration, one
            # run — and a second card showing the same frames would be a lie
            # about there being two.
            return Output(
                content=(
                    f'It is already working unattended on: {entry.goal}\n\n'
                    'Wait for it, or stop that run before starting another. There is one screen and '
                    'one set of events; a second run would not have its own.'
                ),
                display={'already': True, 'run': entry.id, 'goal': entry.goal},
            )

        run = autopilot.start(
            Run(
                id=session_id,
                goal=goal,
                session_id=session_id,
                shares_pointer=bool(getattr(stage, 'shares_pointer', False)),
                stage=getattr(stage, 'kind', 'unknown'),
            )
        )

        if ctx.unattend is not None:
            await ctx.unattend(MARKER, AUTOPILOT_PROMPT)

        return Output(
            content=(
                f'Unattended: {goal}\n\n'
                f'You are now working on your own in {chosen.value} mode until that is done. Do not '
                'ask whether to continue — there is nobody at the keyboard — and do not stop to '
                'report progress; when you stop, report what you did. Anything destructive, anything '
                'that spends money and anything that types a secret will still suspend for a person, '
                'so if one of those turns up, say plainly what you need rather than working around '
                'it. When you are done, say so in a few sentences and stop.'
            ),
            display={
                'run': run.id,
                'goal': goal,
                'mode': chosen.value,
                'screen': getattr(stage, 'describe', dict)(),
                'shares_pointer': bool(getattr(stage, 'shares_pointer', False)),
            },
        )


def tools():
    return [AutopilotTool()]
