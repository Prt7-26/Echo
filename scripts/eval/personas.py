"""Persona specifications for the closed-loop Echo experiment.

Design principle (learned from a pilot): the preferences must be IDIOSYNCRATIC
and CHECKABLE — things a strong base model will NOT do by default, so they can
only be satisfied by remembering this specific user. Generic "be concise/polite"
preferences are satisfied zero-shot by mimo and produce a ceiling effect where
memory adds nothing. Each rule here is (a) unguessable from the request alone and
(b) mechanically verifiable by the GLM evaluator.

The metric scores the agent's FIRST output each turn (before any revision) — i.e.
"did the assistant proactively satisfy this user's known preferences?". Memory
(Echo's M5) should make the first output comply more and more over turns; a
stateless agent (A) and a self-eval/frequency agent (B) cannot.

Four-model isolation: persona_brief is what the DeepSeek persona embodies;
pref_rules is the rubric the independent GLM evaluator scores against.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskType:
    key: str
    template: str
    topics: list[str]


@dataclass
class Persona:
    pid: str
    profile: str
    pref_rules: str        # checkable rubric for the GLM evaluator
    persona_brief: str     # what the DeepSeek persona embodies + how it gives feedback
    task_types: list[TaskType]
    bad_skill: tuple[str, str] = field(default=("", ""))  # (task_key, a WRONG remembered 'preference')


PERSONAS: list[Persona] = [
    Persona(
        pid="signoff_quirk",
        profile="An exec with three firm, idiosyncratic email habits.",
        pref_rules=(
            "Score how well the email obeys ALL THREE of this user's fixed rules:\n"
            "1. It MUST end with exactly this sign-off on its own final line: 'Onward, R.'\n"
            "2. The body MUST be 60 words or fewer.\n"
            "3. It MUST contain NO exclamation marks at all.\n"
            "5 = all three obeyed; subtract for each violated. 1 = none obeyed."
        ),
        persona_brief=(
            "You are an exec with three NON-NEGOTIABLE email habits: (1) every email "
            "ends with exactly 'Onward, R.' on its own last line; (2) the body is 60 "
            "words or fewer; (3) absolutely no exclamation marks. A cold assistant "
            "won't know these. When it violates any, your feedback must state EXACTLY "
            "which rule(s) it missed (quote the sign-off, the word limit, the no-'!'). "
            "You only give thumb=1 if ALL THREE are obeyed."
        ),
        task_types=[
            TaskType("email", "Write an email to {topic}.",
                     ["a client postponing a demo", "a teammate covering your shift",
                      "a vendor about a revised quote", "a candidate to schedule a call",
                      "a partner confirming Friday's deadline", "your team about a deploy slip"]),
            TaskType("note", "Write a brief note to {topic}.",
                     ["staff about the office closing early", "a mentor thanking them",
                      "a recruiter declining politely", "a customer acknowledging a bug report"]),
        ],
        bad_skill=("email",
                   "The user wants long, detailed, enthusiastic emails with plenty of "
                   "exclamation marks, signed off 'Best regards, The Team'."),
    ),
    Persona(
        pid="bullet_format",
        profile="An analyst who wants summaries in one exact rigid format.",
        pref_rules=(
            "Score how well the summary obeys ALL THREE fixed-format rules:\n"
            "1. It MUST be EXACTLY 3 bullet points (no more, no fewer; no prose).\n"
            "2. Each bullet MUST start with an emoji.\n"
            "3. Each bullet MUST be 8 words or fewer.\n"
            "5 = all obeyed; subtract per violation. 1 = none."
        ),
        persona_brief=(
            "You insist every summary is EXACTLY 3 bullet points, each starting with an "
            "emoji, each 8 words or fewer. No intro, no prose, no extra bullets. A cold "
            "assistant won't guess this. When it deviates, your feedback must say exactly "
            "what was wrong (count of bullets, missing emoji, too-long bullets). thumb=1 "
            "only if all three are obeyed."
        ),
        task_types=[
            TaskType("summary", "Summarise {topic}.",
                     ["the Q3 sales report", "yesterday's incident postmortem",
                      "the new onboarding policy", "the competitor analysis",
                      "the user research findings", "the roadmap changes"]),
            TaskType("recap", "Give a recap of {topic}.",
                     ["this week's standups", "the design review", "the budget meeting",
                      "the customer interview"]),
        ],
        bad_skill=("summary",
                   "The user wants a flowing single-paragraph prose summary, no bullets, "
                   "as detailed and complete as possible."),
    ),
    Persona(
        pid="british_phrase",
        profile="A writer with three fixed stylistic tics.",
        pref_rules=(
            "Score how well the text obeys ALL THREE fixed style rules:\n"
            "1. It MUST contain the exact phrase 'per my last note' somewhere.\n"
            "2. It MUST use British spelling (e.g. organise, colour, realise, favour).\n"
            "3. It MUST contain NO em-dashes (the '—' character).\n"
            "5 = all obeyed; subtract per violation. 1 = none."
        ),
        persona_brief=(
            "You have three fixed writing tics: (1) the exact phrase 'per my last note' "
            "must appear; (2) British spelling throughout (organise, colour, realise); "
            "(3) never any em-dashes ('—'). A cold assistant won't know these. When it "
            "misses any, your feedback states exactly which. thumb=1 only if all three hold."
        ),
        task_types=[
            TaskType("post", "Write a short post about {topic}.",
                     ["our new partnership", "a product update", "a hiring push",
                      "a community milestone", "an upcoming webinar", "a policy change"]),
            TaskType("blurb", "Write a blurb for {topic}.",
                     ["the newsletter intro", "the careers page", "a feature launch",
                      "an event invite"]),
        ],
        bad_skill=("post",
                   "The user prefers American spelling and lots of em-dashes for emphasis, "
                   "and dislikes stock phrases."),
    ),
]


def get_personas() -> list[Persona]:
    return PERSONAS
