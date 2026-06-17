"""Persona specifications for the closed-loop Echo experiment.

Design principle: preferences must be IDIOSYNCRATIC and CHECKABLE — things a
strong base model will NOT do by default, so they can only be satisfied by
remembering this specific user (otherwise mimo satisfies them zero-shot and the
metric ceilings out). 15 personas span diverse domains (email, code, recipes,
fitness, legal, academic, social, support, poetry, data, translation, meetings,
product copy) to simulate many different users with different needs. Each rule
is (a) unguessable from the request and (b) mechanically verifiable by the GLM
evaluator.

The metric scores the agent's FIRST output each turn (before any revision):
"did the assistant proactively satisfy this user's known preferences?". Memory
(Echo's M5 consolidated profile) should make the first output comply more and
more over turns; a stateless agent (A) and a self-eval/frequency agent (B)
cannot.

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


# A shared instruction appended to every persona_brief so DeepSeek phrases its
# feedback as a clear, reusable directive (what makes Echo's profile learnable).
_FB = (" When the assistant misses any rule, your feedback must be a clear, "
       "specific DIRECTIVE of what you require next time (imperative; name each "
       "rule). You give thumb=1 only if ALL your rules are satisfied.")


PERSONAS: list[Persona] = [
    Persona(
        pid="signoff_quirk",
        profile="An exec with three firm, idiosyncratic email habits.",
        pref_rules=("Obeys ALL THREE: 1. ends with exactly 'Onward, R.' on its own "
                    "final line; 2. body is 60 words or fewer; 3. NO exclamation marks. "
                    "5 = all three; subtract per violation."),
        persona_brief=("You are an exec. Every email must (1) end with exactly 'Onward, "
                       "R.' on its own last line, (2) be 60 words or fewer, (3) contain no "
                       "exclamation marks." + _FB),
        task_types=[
            TaskType("email", "Write an email to {topic}.",
                     ["a client postponing a demo", "a teammate covering your shift",
                      "a vendor about a revised quote", "a candidate to schedule a call",
                      "a partner confirming Friday's deadline", "your team about a deploy slip"]),
            TaskType("note", "Write a brief note to {topic}.",
                     ["staff about the office closing early", "a mentor thanking them",
                      "a recruiter declining politely", "a customer acknowledging a bug"]),
        ],
        bad_skill=("email", "The user wants long, detailed, enthusiastic emails with "
                   "plenty of exclamation marks, signed 'Best regards, The Team'."),
    ),
    Persona(
        pid="bullet_format",
        profile="An analyst who wants summaries in one exact rigid format.",
        pref_rules=("Obeys ALL THREE: 1. EXACTLY 3 bullet points (no prose, no more, no "
                    "fewer); 2. each bullet starts with an emoji; 3. each bullet is 8 words "
                    "or fewer. 5 = all three; subtract per violation."),
        persona_brief=("You want every summary as EXACTLY 3 bullet points, each starting "
                       "with an emoji, each 8 words or fewer. No intro, no prose." + _FB),
        task_types=[
            TaskType("summary", "Summarise {topic}.",
                     ["the Q3 sales report", "yesterday's incident postmortem",
                      "the new onboarding policy", "the competitor analysis",
                      "the user research findings", "the roadmap changes"]),
            TaskType("recap", "Give a recap of {topic}.",
                     ["this week's standups", "the design review", "the budget meeting",
                      "the customer interview"]),
        ],
        bad_skill=("summary", "The user wants a flowing single-paragraph prose summary, "
                   "no bullets, as detailed as possible."),
    ),
    Persona(
        pid="british_phrase",
        profile="A writer with three fixed stylistic tics.",
        pref_rules=("Obeys ALL THREE: 1. contains the exact phrase 'per my last note'; "
                    "2. uses British spelling (organise, colour, realise); 3. NO em-dashes "
                    "('—'). 5 = all three; subtract per violation."),
        persona_brief=("You require (1) the exact phrase 'per my last note' somewhere, "
                       "(2) British spelling throughout, (3) never any em-dashes." + _FB),
        task_types=[
            TaskType("post", "Write a short post about {topic}.",
                     ["our new partnership", "a product update", "a hiring push",
                      "a community milestone", "an upcoming webinar", "a policy change"]),
            TaskType("blurb", "Write a blurb for {topic}.",
                     ["the newsletter intro", "the careers page", "a feature launch",
                      "an event invite"]),
        ],
        bad_skill=("post", "The user prefers American spelling and lots of em-dashes for "
                   "emphasis, and dislikes stock phrases."),
    ),
    Persona(
        pid="code_snake",
        profile="A Python dev with three non-negotiable code conventions.",
        pref_rules=("Obeys ALL THREE: 1. ALL identifiers are snake_case (no camelCase); "
                    "2. every function has type hints on params and return; 3. NO inline or "
                    "block comments at all. 5 = all three; subtract per violation."),
        persona_brief=("You are a Python dev. All code you receive must use snake_case for "
                       "every identifier, full type hints on every function, and contain NO "
                       "comments whatsoever." + _FB),
        task_types=[
            TaskType("func", "Write a Python function to {topic}.",
                     ["parse a CSV into a list of dicts", "compute a moving average",
                      "validate an email address", "flatten a nested list",
                      "retry a call with backoff", "merge two sorted lists"]),
            TaskType("snippet", "Write a short Python snippet that {topic}.",
                     ["reads a JSON file", "groups items by key",
                      "debounces a function", "memoises a function"]),
        ],
        bad_skill=("func", "The user likes camelCase names, heavily commented code "
                   "explaining each step, and dislikes type hints as clutter."),
    ),
    Persona(
        pid="recipe_metric",
        profile="A home cook with a strict recipe format.",
        pref_rules=("Obeys ALL THREE: 1. metric units only (g, ml, °C — no cups/oz/°F); "
                    "2. EXACTLY 5 numbered steps; 3. ends with a 'Serves N' line. 5 = all "
                    "three; subtract per violation."),
        persona_brief=("You want every recipe in metric units only (grams, ml, °C), with "
                       "EXACTLY 5 numbered steps, ending with a 'Serves N' line." + _FB),
        task_types=[
            TaskType("recipe", "Give me a recipe for {topic}.",
                     ["banana bread", "a simple tomato soup", "pancakes",
                      "garlic roast chicken", "chocolate chip cookies", "a veggie stir-fry"]),
            TaskType("bake", "How do I bake {topic}?",
                     ["a basic sourdough", "blueberry muffins", "a sponge cake",
                      "cinnamon rolls"]),
        ],
        bad_skill=("recipe", "The user prefers US customary units (cups, ounces, "
                   "Fahrenheit), long prose instructions, and no step numbering."),
    ),
    Persona(
        pid="fitness_format",
        profile="A trainer who wants plans in one fixed shape.",
        pref_rules=("Obeys ALL THREE: 1. opens with a line starting 'Warm-up:'; 2. every "
                    "exercise gives sets×reps as 'NxM' (e.g. 3x12); 3. uses NO gym equipment "
                    "(bodyweight only). 5 = all three; subtract per violation."),
        persona_brief=("You want every workout to open with a 'Warm-up:' line, express "
                       "every exercise as sets x reps like '3x12', and use bodyweight only "
                       "(no equipment/machines/weights)." + _FB),
        task_types=[
            TaskType("workout", "Give me a workout for {topic}.",
                     ["legs", "core", "upper body", "full body", "mobility", "a quick 15-min session"]),
            TaskType("routine", "Build a routine to {topic}.",
                     ["improve push-ups", "build a stronger core", "loosen tight hips",
                      "warm up before a run"]),
        ],
        bad_skill=("workout", "The user trains heavy at the gym and wants barbell and "
                   "machine exercises with weights, written as flowing paragraphs."),
    ),
    Persona(
        pid="legal_plain",
        profile="A paralegal who wants plain-English answers with guardrails.",
        pref_rules=("Obeys ALL THREE: 1. opens with the exact sentence 'Not legal advice.'; "
                    "2. NO Latin terms (no 'prima facie', 'inter alia', 'pro bono', etc.); "
                    "3. ends with 'Consult a licensed lawyer.'. 5 = all three; subtract per "
                    "violation."),
        persona_brief=("You want every legal answer to (1) open with exactly 'Not legal "
                       "advice.', (2) avoid all Latin legal terms, (3) end with 'Consult a "
                       "licensed lawyer.'." + _FB),
        task_types=[
            TaskType("explain", "Explain in plain terms: {topic}.",
                     ["what an NDA does", "what 'at-will employment' means",
                      "how small-claims court works", "what a security deposit covers",
                      "what a non-compete is", "what copyright fair use is"]),
            TaskType("answer", "Answer this question: {topic}.",
                     ["can my landlord keep my deposit?", "is a verbal contract binding?",
                      "who owns work I do as a contractor?", "can I record a call?"]),
        ],
        bad_skill=("explain", "The user enjoys precise legal Latin and formal citations, "
                   "and does not want disclaimers cluttering the answer."),
    ),
    Persona(
        pid="academic_abstract",
        profile="A researcher with a strict abstract style.",
        pref_rules=("Obeys ALL THREE: 1. EXACTLY 4 sentences; 2. NO first person (no 'I', "
                    "'we', 'our'); 3. contains at least one number or percentage. 5 = all "
                    "three; subtract per violation."),
        persona_brief=("You want every abstract to be EXACTLY 4 sentences, written with no "
                       "first person (no I/we/our), and to include at least one concrete "
                       "number or percentage." + _FB),
        task_types=[
            TaskType("abstract", "Write an abstract for a study on {topic}.",
                     ["sleep and memory in students", "a new caching algorithm",
                      "remote work and productivity", "a drug's effect on blood pressure",
                      "soil carbon and tillage", "a recommender system"]),
            TaskType("summary", "Write a research summary of {topic}.",
                     ["a clinical trial result", "a machine-learning benchmark",
                      "a survey of 500 users", "a field experiment"]),
        ],
        bad_skill=("abstract", "The user writes warm first-person narrative abstracts of "
                   "any length and avoids dry statistics."),
    ),
    Persona(
        pid="caption_tight",
        profile="A social manager with a tight caption spec.",
        pref_rules=("Obeys ALL THREE: 1. 15 words or fewer; 2. EXACTLY 2 hashtags; 3. ends "
                    "with exactly one emoji. 5 = all three; subtract per violation."),
        persona_brief=("You want every caption to be 15 words or fewer, contain EXACTLY 2 "
                       "hashtags, and end with exactly one emoji." + _FB),
        task_types=[
            TaskType("caption", "Write a caption for {topic}.",
                     ["a sunset beach photo", "a new sneaker drop", "a coffee latte art shot",
                      "a team hiking trip", "a product unboxing", "a cozy reading nook"]),
            TaskType("post", "Write a social post for {topic}.",
                     ["a flash sale", "a giveaway", "a milestone of 10k followers",
                      "a behind-the-scenes clip"]),
        ],
        bad_skill=("caption", "The user prefers long, paragraph-length captions with many "
                   "hashtags (10+) and no emoji."),
    ),
    Persona(
        pid="support_reply",
        profile="A support lead with a fixed reply template.",
        pref_rules=("Obeys ALL THREE: 1. opens with an apology; 2. includes a ticket "
                    "reference in the form [#NNNN] (four digits); 3. NEVER uses the word "
                    "'unfortunately'. 5 = all three; subtract per violation."),
        persona_brief=("You want every support reply to (1) open with an apology, (2) "
                       "include a ticket reference like [#1234], (3) never use the word "
                       "'unfortunately'." + _FB),
        task_types=[
            TaskType("reply", "Write a support reply to a customer about {topic}.",
                     ["a delayed shipment", "a double charge", "a login problem",
                      "a missing feature", "a refund request", "a broken item"]),
            TaskType("followup", "Write a follow-up message about {topic}.",
                     ["an unresolved ticket", "a resolved bug", "a feature now released",
                      "a scheduled maintenance"]),
        ],
        bad_skill=("reply", "The user likes blunt, apology-free replies and says "
                   "'unfortunately' often; ticket numbers are unnecessary."),
    ),
    Persona(
        pid="haiku_season",
        profile="A poet who only wants one tiny form.",
        pref_rules=("Obeys ALL THREE: 1. EXACTLY 3 lines; 2. no end rhyme; 3. names a "
                    "season (spring/summer/autumn/fall/winter). 5 = all three; subtract per "
                    "violation."),
        persona_brief=("You want every poem to be EXACTLY 3 lines, with no end rhyme, and "
                       "to name a season." + _FB),
        task_types=[
            TaskType("poem", "Write a short poem about {topic}.",
                     ["a quiet morning", "the ocean", "an old friend", "city rain",
                      "a mountain trail", "a cup of tea"]),
            TaskType("verse", "Write a few lines about {topic}.",
                     ["falling leaves", "first snow", "a blooming garden", "a long night"]),
        ],
        bad_skill=("poem", "The user loves long rhyming poems of many stanzas and dislikes "
                   "minimalist forms."),
    ),
    Persona(
        pid="report_numbers",
        profile="A data lead who wants numbers-first, adjective-free reports.",
        pref_rules=("Obeys ALL THREE: 1. first sentence states the headline number; 2. uses "
                    "an abbreviation like 'YoY' or 'MoM' at least once; 3. NO subjective "
                    "adjectives (great, strong, impressive, healthy). 5 = all three; subtract "
                    "per violation."),
        persona_brief=("You want every report to lead its first sentence with the headline "
                       "number, use 'YoY' or 'MoM' at least once, and avoid all subjective "
                       "adjectives (no 'strong', 'great', 'healthy', etc.)." + _FB),
        task_types=[
            TaskType("report", "Write a one-paragraph report on {topic}.",
                     ["Q3 revenue", "monthly active users", "churn rate", "ad spend ROI",
                      "support ticket volume", "signup conversion"]),
            TaskType("update", "Write a metrics update on {topic}.",
                     ["weekly sales", "app crashes", "email open rates", "NPS"]),
        ],
        bad_skill=("report", "The user loves upbeat narrative reports full of adjectives "
                   "('strong growth', 'great quarter') and rarely cites raw numbers."),
    ),
    Persona(
        pid="translate_keep",
        profile="A localiser with fixed handling rules.",
        pref_rules=("Obeys ALL THREE: 1. keeps proper nouns untranslated in [brackets]; "
                    "2. NO contractions (write 'do not', not 'don't'); 3. formal register. "
                    "5 = all three; subtract per violation."),
        persona_brief=("You localise text and require: proper nouns kept untranslated in "
                       "[square brackets], no contractions at all, and a formal register." + _FB),
        task_types=[
            TaskType("localize", "Rewrite this for a formal audience: {topic}.",
                     ["a welcome message from Acme Corp", "an invite to the Lisbon office",
                      "a notice about the GreenPay app", "a thank-you from Dr. Lopez",
                      "an alert about the Orion release", "a memo from the Tokyo team"]),
            TaskType("polish", "Polish this announcement: {topic}.",
                     ["the Nimbus launch", "a Berlin meetup", "the FastCart update",
                      "a partnership with BlueRiver"]),
        ],
        bad_skill=("localize", "The user likes a casual tone with contractions and prefers "
                   "translating brand names into the target language."),
    ),
    Persona(
        pid="meeting_actions",
        profile="A PM who wants action-only notes in one shape.",
        pref_rules=("Obeys ALL THREE: 1. ONLY action items, each line '@name — task'; 2. no "
                    "narrative/prose paragraphs; 3. ends with a line starting 'Next:'. 5 = all "
                    "three; subtract per violation."),
        persona_brief=("You want meeting notes as ONLY action items, each line formatted "
                       "'@name — task', with no narrative prose, ending with a line that "
                       "starts 'Next:'." + _FB),
        task_types=[
            TaskType("notes", "Write meeting notes for {topic}.",
                     ["the sprint planning", "the launch retro", "the design review",
                      "the budget sync", "the hiring debrief", "the roadmap session"]),
            TaskType("actions", "List the action items from {topic}.",
                     ["the client call", "the incident review", "the QBR", "the standup"]),
        ],
        bad_skill=("notes", "The user prefers detailed narrative minutes capturing the full "
                   "discussion in flowing paragraphs, without action-item formatting."),
    ),
    Persona(
        pid="product_you",
        profile="A copywriter with a strict product-blurb spec.",
        pref_rules=("Obeys ALL THREE: 1. second person (addresses 'you'); 2. EXACTLY 3 "
                    "benefit points; 3. NO superlatives (best, greatest, #1, ultimate). 5 = "
                    "all three; subtract per violation."),
        persona_brief=("You want product copy written in second person ('you'), with "
                       "EXACTLY 3 benefit points, and no superlatives (no 'best', 'greatest', "
                       "'#1', 'ultimate')." + _FB),
        task_types=[
            TaskType("blurb", "Write product copy for {topic}.",
                     ["a noise-cancelling headphone", "a standing desk", "a meal-kit service",
                      "a budgeting app", "a travel backpack", "a smart water bottle"]),
            TaskType("pitch", "Write a short pitch for {topic}.",
                     ["a password manager", "an e-bike", "a language app", "a robot vacuum"]),
        ],
        bad_skill=("blurb", "The user loves third-person copy packed with superlatives "
                   "('the best', '#1', 'ultimate') and a long list of features."),
    ),
]


def get_personas() -> list[Persona]:
    return PERSONAS
