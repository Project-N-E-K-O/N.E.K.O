from __future__ import annotations

from dataclasses import dataclass

import pytest

from plugin.plugins.study_companion.voice_filter import VoiceFilter

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class VoiceScenario:
    name: str
    transcript: str
    screen_text: str
    expected_relay: bool
    screen_type: str = "question"
    subject: str = "default"


def _relays(result: dict[str, object] | None) -> bool:
    return result is None or bool(result.get("should_relay"))


def test_voice_filter_fifty_learning_scenarios_acceptance() -> None:
    cases = [
        VoiceScenario(
            "named algebra step",
            "Yui why does the sign flip here",
            "2x - 5 = 9",
            True,
            subject="math",
        ),
        VoiceScenario(
            "named derivative check",
            "Yui help me check the derivative",
            "f(x)=x^3+3x^2-9x+1",
            True,
            subject="math",
        ),
        VoiceScenario(
            "named geometry hint",
            "Yui what theorem fits this triangle",
            "Triangle ABC has AB = AC and angle B = 52",
            True,
            subject="math",
        ),
        VoiceScenario(
            "named physics setup",
            "Yui which force should I draw first",
            "A block slides down an incline with friction",
            True,
            subject="physics",
        ),
        VoiceScenario(
            "named chemistry balance",
            "Yui how do I balance the oxygen atoms",
            "C2H6 + O2 -> CO2 + H2O",
            True,
            subject="chemistry",
        ),
        VoiceScenario(
            "named reading summary",
            "Yui summarize the main claim",
            "The passage compares two views of memory.",
            True,
            screen_type="reading",
        ),
        VoiceScenario(
            "named notes question",
            "Yui is this note missing a condition",
            "Continuity is listed before differentiability.",
            True,
            screen_type="notes",
        ),
        VoiceScenario(
            "named answer review",
            "Yui did I skip a justification",
            "Student answer: because the two angles are equal.",
            True,
            screen_type="answering",
        ),
        VoiceScenario(
            "named short followup",
            "Yui why",
            "x = 2 is substituted into the equation",
            True,
            subject="math",
        ),
        VoiceScenario(
            "named high overlap still user intent",
            "Yui f x equals x squared why",
            "f(x)=x squared",
            True,
            subject="math",
        ),
        VoiceScenario(
            "unnamed strategy question",
            "how should I start solving it",
            "f(x)=x^2+3x+2",
            True,
            subject="math",
        ),
        VoiceScenario(
            "unnamed proof planning",
            "help me outline the proof",
            "Triangle ABC has AB = AC.",
            True,
            subject="math",
        ),
        VoiceScenario(
            "unnamed concept check",
            "can you identify the tested concept",
            "A cart moves with constant acceleration.",
            True,
            subject="physics",
        ),
        VoiceScenario(
            "unnamed error diagnosis",
            "where did my reasoning go wrong",
            "Student answer uses the same variable twice.",
            True,
        ),
        VoiceScenario(
            "unnamed hint request",
            "give me a small hint first",
            "Solve the system shown in the worksheet.",
            True,
        ),
        VoiceScenario(
            "unnamed compare methods",
            "help me decide substitution or elimination",
            "x + y = 7 and x - y = 1",
            True,
            subject="math",
        ),
        VoiceScenario(
            "unnamed units question",
            "help me choose final units",
            "Distance is measured in meters and time in seconds.",
            True,
            subject="physics",
        ),
        VoiceScenario(
            "unnamed reaction reasoning",
            "explain why the product is written on the right",
            "Reactants form products in the equation.",
            True,
            subject="chemistry",
        ),
        VoiceScenario(
            "unnamed passage inference",
            "help me find the supporting sentence",
            "The author contrasts direct evidence with speculation.",
            True,
            screen_type="reading",
        ),
        VoiceScenario(
            "unnamed schedule wording",
            "turn this into a simpler reminder",
            "Review chapter three before Friday.",
            True,
            screen_type="notes",
        ),
        VoiceScenario(
            "unnamed summary gap",
            "help me find the missing detail",
            "Summary: the experiment changed temperature only.",
            True,
            screen_type="summary",
        ),
        VoiceScenario(
            "unnamed next step",
            "what should I try next after this",
            "The first attempt isolated x.",
            True,
            subject="math",
        ),
        VoiceScenario(
            "unnamed definition help",
            "explain the key definition in plain words",
            "A function maps each input to exactly one output.",
            True,
            subject="math",
        ),
        VoiceScenario(
            "unnamed answer confidence",
            "does this final answer look reasonable",
            "The result is 42 meters.",
            True,
            subject="physics",
        ),
        VoiceScenario(
            "unnamed study plan",
            "make a quick plan for this topic",
            "Topic list: limits, derivatives, applications.",
            True,
            screen_type="review",
            subject="math",
        ),
        VoiceScenario("short filler ok", "ok", "Solve x + 2 = 5.", False),
        VoiceScenario("short filler uh", "uh", "Read the passage.", False),
        VoiceScenario("short filler hmm", "hmm", "Balance H2O.", False),
        VoiceScenario("short filler wait", "wait", "Draw the diagram.", False),
        VoiceScenario("short filler no", "no?", "Review notes.", False),
        VoiceScenario(
            "default reading aloud",
            "the passage compares two views of memory",
            "The passage compares two views of memory.",
            False,
            screen_type="reading",
        ),
        VoiceScenario(
            "default prompt aloud",
            "choose the correct statement below",
            "Choose the correct statement below.",
            False,
        ),
        VoiceScenario(
            "default notes aloud",
            "continuity is listed before differentiability",
            "Continuity is listed before differentiability.",
            False,
            screen_type="notes",
        ),
        VoiceScenario(
            "default answer aloud",
            "because the two angles are equal",
            "Student answer: because the two angles are equal.",
            False,
            screen_type="answering",
        ),
        VoiceScenario(
            "default summary aloud",
            "the experiment changed temperature only",
            "Summary: the experiment changed temperature only.",
            False,
            screen_type="summary",
        ),
        VoiceScenario(
            "math expression aloud",
            "f x equals x squared plus three x plus two",
            "f(x)=x squared plus three x plus two",
            False,
            subject="math",
        ),
        VoiceScenario(
            "math number sequence aloud",
            "one two three four five",
            "Numbers: one two three four five",
            False,
            subject="math",
        ),
        VoiceScenario(
            "math equation aloud",
            "x plus y equals seven and x minus y equals one",
            "x + y equals seven and x - y equals one",
            False,
            subject="math",
        ),
        VoiceScenario(
            "math derivative aloud",
            "derivative of x cubed is three x squared",
            "Derivative of x cubed is three x squared.",
            False,
            subject="math",
        ),
        VoiceScenario(
            "math geometry aloud",
            "triangle a b c has a b equal a c",
            "Triangle ABC has AB equal AC.",
            False,
            subject="math",
        ),
        VoiceScenario(
            "physics force prompt aloud",
            "a block slides down an incline with friction",
            "A block slides down an incline with friction.",
            False,
            subject="physics",
        ),
        VoiceScenario(
            "physics velocity aloud",
            "velocity changes by two meters per second",
            "Velocity changes by two meters per second.",
            False,
            subject="physics",
        ),
        VoiceScenario(
            "physics energy aloud",
            "kinetic energy depends on mass and speed",
            "Kinetic energy depends on mass and speed.",
            False,
            subject="physics",
        ),
        VoiceScenario(
            "physics units aloud",
            "distance is measured in meters and time in seconds",
            "Distance is measured in meters and time in seconds.",
            False,
            subject="physics",
        ),
        VoiceScenario(
            "physics diagram aloud",
            "draw the normal force perpendicular to the surface",
            "Draw the normal force perpendicular to the surface.",
            False,
            subject="physics",
        ),
        VoiceScenario(
            "chemistry equation aloud",
            "c two h six plus o two forms carbon dioxide and water",
            "C two H six plus O two forms carbon dioxide and water.",
            False,
            subject="chemistry",
        ),
        VoiceScenario(
            "chemistry mole prompt aloud",
            "calculate the number of moles from mass",
            "Calculate the number of moles from mass.",
            False,
            subject="chemistry",
        ),
        VoiceScenario(
            "chemistry ion prompt aloud",
            "sodium ion combines with chloride ion",
            "Sodium ion combines with chloride ion.",
            False,
            subject="chemistry",
        ),
        VoiceScenario(
            "chemistry conservation aloud",
            "atoms are conserved during the reaction",
            "Atoms are conserved during the reaction.",
            False,
            subject="chemistry",
        ),
        VoiceScenario(
            "chemistry lab note aloud",
            "the solution turns blue after heating",
            "The solution turns blue after heating.",
            False,
            subject="chemistry",
        ),
    ]
    expected_drop_count = sum(1 for case in cases if not case.expected_relay)
    false_negatives: list[str] = []
    false_positives: list[str] = []

    assert len(cases) == 50

    for case in cases:
        actual_relay = _relays(
            VoiceFilter(names=["Yui"]).filter(
                case.transcript,
                screen_text=case.screen_text,
                screen_type=case.screen_type,
                subject=case.subject,
            )
        )
        if case.expected_relay and not actual_relay:
            false_negatives.append(case.name)
        if not case.expected_relay and actual_relay:
            false_positives.append(case.name)

    assert false_negatives == []
    assert len(false_positives) / expected_drop_count < 0.15
