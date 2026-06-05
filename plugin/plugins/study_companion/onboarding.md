# Study Companion Onboarding

This guide mirrors the in-app quickstart panel and gives first-time users a
linear path through the Study Companion workflow.

## 1. Choose A Mode

- Companion: use this for quick explanations while reading or solving.
- Interactive: use this when you want the plugin to ask questions and check your answer.
- Teaching: use this for step-by-step explanations and guided practice.

Start with Companion if you are unsure. You can switch modes at any time from
the mode buttons in the study panel.

## 2. Set A Daily Goal

Open the daily goal editor and choose a target that matches the current study
session:

- Cards: for vocabulary, formulas, or short facts.
- Minutes: for focused reading or problem-solving blocks.
- Attempts: for passage recitation or repeated practice.

For memory decks, open the memory deck panel and bind a deck goal before
starting a focus session.

## 3. Record The First Study Item

Paste text into the study panel, or run OCR on the current screen. Then choose
one of the main actions:

- Explain: turn the current text into a short explanation.
- Generate Question: create a check question from the current material.
- Evaluate Answer: compare your answer with the question and get feedback.

If OCR is unavailable, check the dependency status entry and install the missing
OCR backend from the plugin UI.

## 4. Review And Export

After a session, open the summary panel to review completed and incomplete
goals. Use the note exporter to create Markdown, PDF, DOCX, or XMind notes from
recent study material.

Use the knowledge map and memory deck panels to find weak topics and due memory
cards before the next study session.

## Empty States

- Knowledge map: no topics means the plugin has not tracked enough explanations or answers yet.
- Memory deck: no cards means you need to create or import cards first.
- Study record: no recent events means no explain/question/evaluate actions have completed.
- Export: no notes means there is not enough recent study material to export.

## Error States

- OCR failed: verify screen capture permissions and OCR dependencies.
- LLM timeout: retry after checking the configured model and network.
- Export failed: retry once; if it still fails, export Markdown first and then convert externally.
- Missing answer or question: generate or enter a question before evaluating an answer.
