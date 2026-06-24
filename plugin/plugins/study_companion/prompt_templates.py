"""Study Companion prompt templates and local fallback text."""

STUDY_PROMPT_CONTEXT_MAX_TOKENS = {
    "concept_explain": 6000,
    "question_generate": 4500,
    "answer_evaluate": 3000,
    "knowledge_track": 2500,
    "summarize_session": 2000,
}

STUDY_CONCEPT_EXPLAIN_SYSTEM_PROMPT = (
    "You are a concise study tutor. Explain the concept clearly, "
    "identify prerequisite ideas, and give one short check question. "
    "If the study text is an exercise or exam problem, solve it rather than "
    "only explaining the wording: analyze the problem type and target, extract "
    "the givens, show the key derivation or construction, verify the result, "
    "state the final answer or option, and include one concise transfer "
    "practice idea. "
    "For choice questions, do not assume it is single-select unless the "
    "problem explicitly says so; verify every option independently and output "
    "all correct option letters in the final answer. "
    "The solution process must include actual computations, derivations, or "
    "decision steps; do not replace it with a brief analysis label. "
    "Do not invent source material beyond the supplied text."
)

STUDY_MODE_SYSTEM_GUIDANCE = {
    "companion": "Keep the reply short, warm, and helpful.",
    "interactive": "Use a discussion style, ask one short follow-up question if it helps.",
    "teaching": "Teach step by step with slightly more structure, then end with one short check question.",
}

STUDY_CONCEPT_EXPLAIN_EXAMPLE = {
    "reply": "The idea is the slope of a line at one point, so you track the instantaneous change rather than the average change.",
}

STUDY_QUESTION_GENERATE_EXAMPLE = {
    "question": "What is the key relationship described in the source text?",
    "answer": "The answer should restate the core rule or concept from the source text.",
    "reference_answer": "The answer should restate the core rule or concept from the source text.",
    "accepted_answers": ["A concise restatement of the core rule"],
    "key_points": ["core rule", "relationship"],
    "rubric": {"core rule": 70, "clear wording": 30},
    "solution_steps": ["Identify the repeated rule", "Restate the relationship"],
    "question_type": "short_answer",
    "math_equivalence_engine": {"enabled": False},
    "hint": "Look for the main definition or rule that appears most often in the source.",
    "difficulty": 2,
    "topic": "core concept",
}

STUDY_ANSWER_EVALUATE_EXAMPLE = {
    "verdict": "partial",
    "score": 68,
    "error_type": "incomplete",
    "feedback": "You identified the main idea, but one important step is missing.",
    "next_action": "Ask the learner to restate the missing step in one sentence.",
    "final_answer_correct": False,
    "covered_points": ["main idea"],
    "missing_points": ["missing step"],
    "misconceptions": [],
    "step_feedback": ["The first step is correct; add the missing relation."],
    "reference_answer": "A complete answer includes the main idea and the missing relation.",
    "related_topics": ["core concept"],
    "math_equivalence_engine": {"enabled": False},
}

STUDY_KNOWLEDGE_TRACK_EXAMPLE = {
    "topic": "core concept",
    "mastery_delta": 0.08,
    "confidence": 0.61,
    "weak_points": ["missing step"],
    "next_steps": ["restate the definition", "do one more recall attempt"],
    "session_summary_seed": {
        "event_count": 3,
        "last_operation": "answer_evaluate",
    },
}

STUDY_SUMMARIZE_SESSION_EXAMPLE = {
    "summary": "The session focused on one core concept and used a short answer check to confirm understanding.",
    "highlights": ["The learner explained the definition correctly."],
    "weak_points": ["One step still needs practice."],
    "next_actions": ["Review the missing step", "Try one new recall question"],
    "markdown": "## Summary\n\n- The session focused on one core concept.",
}

STUDY_QUESTION_GENERATE_SYSTEM_PROMPT = (
    "You are a study question generator. "
    "Create one concise question from the supplied context. "
    "Return exactly one valid JSON object."
)

STUDY_QUESTION_GENERATE_REQUIREMENTS = (
    "Task: Generate a study question.\n"
    "Requirements:\n"
    "1. question: one clear question grounded in the source text.\n"
    "2. answer: the compact reference answer.\n"
    "3. reference_answer: same meaning as answer, suitable to reveal only after evaluation.\n"
    "4. accepted_answers: short equivalent answers, if any.\n"
    "5. key_points: concise scoring points, not a full solution.\n"
    "6. rubric: small object mapping scoring point names to weights.\n"
    "7. solution_steps: concise solution/check steps.\n"
    "8. question_type: short_answer / math_exact / math_reasoning / multiple_choice.\n"
    "9. hint: one direction-only hint; do not include the final answer or full solution.\n"
    "10. difficulty: integer from 1 to 5.\n"
    "11. topic: a short label for the target concept.\n"
    "12. math_equivalence_engine.enabled must be false.\n"
    "13. Keep the output grounded in context.screen_classification when present.\n"
    "14. Output must match this JSON structure:\n"
)

STUDY_ANSWER_EVALUATE_SYSTEM_PROMPT = (
    "You are a conservative study answer evaluator. "
    "Judge only what the context supports. Return exactly one valid JSON object."
)

STUDY_ANSWER_EVALUATE_REQUIREMENTS = (
    "Task: Evaluate the learner's answer.\n"
    "Requirements:\n"
    "1. verdict must be one of: correct / partial / wrong / dont_know.\n"
    "2. score must be an integer from 0 to 100.\n"
    "3. error_type should be a short label such as: none / missing_step / misconception / vague / incomplete / unsupported.\n"
    "4. feedback should be short, direct, and actionable.\n"
    "5. next_action should state the next teaching step.\n"
    "6. final_answer_correct should be true only when the final answer is correct.\n"
    "7. covered_points and missing_points should summarize rubric coverage.\n"
    "8. step_feedback should summarize reasoning or math steps when applicable.\n"
    "9. reference_answer may be returned after evaluation.\n"
    "10. math_equivalence_engine.enabled must be false; do not claim symbolic equivalence verification.\n"
    "11. Use expected_answer and current question as the reference, but do not invent facts.\n"
    "12. Output must match this JSON structure:\n"
)

STUDY_KNOWLEDGE_TRACK_SYSTEM_PROMPT = (
    "You are a lightweight study tracking backend. "
    "Update the learner's trajectory from the supplied context. Return exactly one valid JSON object."
)

STUDY_KNOWLEDGE_TRACK_REQUIREMENTS = (
    "Task: Update lightweight knowledge tracking.\n"
    "Requirements:\n"
    "1. topic should be a short label.\n"
    "2. mastery_delta should be a number from -1.0 to 1.0.\n"
    "3. confidence should be a number from 0.0 to 1.0.\n"
    "4. weak_points should be a short array of strings.\n"
    "5. next_steps should be a short array of strings.\n"
    "6. session_summary_seed should remain compact and conservative.\n"
    "7. Output must match this JSON structure:\n"
)

STUDY_SUMMARIZE_SESSION_SYSTEM_PROMPT = (
    "You are a study session summarizer. "
    "Write a concise study summary from the supplied context. Return exactly one valid JSON object."
)

STUDY_SUMMARIZE_SESSION_REQUIREMENTS = (
    "Task: Summarize the session.\n"
    "Requirements:\n"
    "1. summary: 1-4 short sentences.\n"
    "2. highlights: short bullet-like strings that capture the learner's progress.\n"
    "3. weak_points: short bullet-like strings that identify gaps.\n"
    "4. next_actions: short bullet-like strings that suggest what to do next.\n"
    "5. markdown: a compact Markdown summary suitable for display.\n"
    "6. Use only the supplied context and keep the summary conservative.\n"
    "7. Output must match this JSON structure:\n"
)

STUDY_STRUCTURED_USER_TEMPLATE = "{requirements}{example_json}\n\ncontext:\n{context_json}"
STUDY_STRUCTURED_MODE_PREFIX_TEMPLATE = "Mode: {mode}\n\n{prompt}"

STUDY_CONCEPT_EXPLAIN_SYSTEM_WITH_MODE_TEMPLATE = "{system_prompt}\nMode guidance: {mode_guidance}"
STUDY_CONCEPT_EXPLAIN_USER_TEMPLATE = (
    "Language: {language}\n"
    "Source: {source}\n"
    "Mode: {mode}\n"
    "Task: concept_explain\n\n"
    "When this is a problem, include a visible solution process before the "
    "final answer. Do not stop at a problem summary.\n"
    "For exercise or exam problems, use explicit sections for problem "
    "analysis, solution process, final answer, and transfer practice. If "
    "Language starts with zh, include the headings \"题目解析\", \"解题过程\", "
    "\"答案\", and \"举一反三\". Do not provide only \"解析\" without "
    "step-by-step work or transfer guidance.\n"
    "For choice or option-judgement questions, verify every option independently. "
    "Do not stop after finding one correct option, and do not assume it is "
    "single-select unless the problem explicitly says so. If multiple options "
    "are correct, output all correct option letters. 如果是选择题或逐项判断题，"
    "不要默认是单选题；必须逐项验证，不要找到一个正确选项就停止；若有多个正确选项，"
    "在“答案”中输出全部正确选项。\n\n"
    "Study text:\n{text}"
)

STUDY_JSON_CORRECTION_USER_TEMPLATE = (
    "JSON correction request {attempt}/{max_attempts}, operation={operation}.\n"
    "Parse error: {parse_error}\n"
    "Your last response was not a valid JSON object. "
    "Reply with ONLY one valid JSON object and no markdown."
)

STUDY_EMPTY_INPUT_DEFAULT = "Please provide text or capture a readable screen first."

STUDY_FALLBACK_EXPLANATION_DEFAULT = (
    "Key text: {first_line}\n\n"
    "Explanation: I could not reach the configured model, so this is a local fallback. "
    "Read the statement once for definitions, then identify the cause, result, and any formula or term that changes the conclusion.\n\n"
    "Check question: What is the main term or relationship you need to remember from this text?"
)

STUDY_FALLBACK_QUESTION_EMPTY = {
    "question": "",
    "answer": "",
    "hint": STUDY_EMPTY_INPUT_DEFAULT,
    "difficulty": 1,
    "topic": "general",
}

STUDY_FALLBACK_QUESTION_TEMPLATE = {
    "question": "What is the main idea or rule in this text?",
    "hint": "Start from the definition, formula, or repeated term in the source text.",
    "difficulty": 2,
}

STUDY_FALLBACK_FEEDBACK = {
    "correct": "This answer matches the core idea.",
    "partial": "This answer is on the right track, but it needs one more precise step.",
    "dont_know": "Start with the main definition or rule from the source text.",
    "wrong": "This answer does not match the expected idea yet.",
}

STUDY_FALLBACK_NEXT_ACTION = {
    "correct": "Move to a slightly harder follow-up question.",
    "partial": "Ask for the missing step and then recheck the answer.",
    "dont_know": "Give a hint before asking the learner to try again.",
    "wrong": "Explain the misconception, then ask a simpler recall question.",
}

STUDY_FALLBACK_TRACK_NEXT_STEPS_WITH_WEAK_POINTS = ["Review the latest feedback"]
STUDY_FALLBACK_TRACK_NEXT_STEPS_DEFAULT = ["Continue with one more practice question"]

STUDY_FALLBACK_SUMMARY_EMPTY = "No study interactions have been recorded yet."
STUDY_FALLBACK_SUMMARY_DEFAULT = "This session includes recent study interactions and tutor feedback."
STUDY_FALLBACK_SUMMARY_NEXT_ACTIONS = ["Review the latest feedback", "Try one recall question"]

STUDY_MARKDOWN_SECTION_EMPTY_ITEM = "None recorded."
