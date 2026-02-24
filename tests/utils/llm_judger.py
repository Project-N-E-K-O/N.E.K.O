import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

# Set to a specific provider to force judger selection.
# Supported values: "openai", "siliconflow", "qwen", "glm"
# Leave as "" to keep automatic fallback order.
JUDGER_PROVIDER = "qwen"


class LLMJudger:
    def __init__(self, api_keys_path: str = "tests/api_keys.json"):
        """
        Initialize the LLM Judger with API keys.
        Defaults to using OpenAI (gpt-4o) if keys are available, otherwise falls back to Qwen.
        """
        self.api_keys = self._load_api_keys(api_keys_path)
        self.llms = self._init_llms()
        self._results: List[Dict[str, Any]] = []
        self._run_tag: str = ""

    def set_run_tag(self, run_tag: str = "") -> None:
        """Set an optional run tag to be prefixed to test_name in results."""
        self._run_tag = (run_tag or "").strip()

    def _tagged_test_name(self, test_name: str) -> str:
        if not self._run_tag:
            return test_name
        if test_name:
            return f"{self._run_tag}::{test_name}"
        return self._run_tag

    def _load_api_keys(self, path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            logger.warning(f"API keys file not found at {path}. Judger might fail.")
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load API keys: {e}")
            return {}

    def _init_llms(self):
        """
        Initialize the LLM Judger, collecting multiple API providers in order of preference.
        """
        providers = [
            {
                "name": "OpenAI",
                "key_name": "assistApiKeyOpenai",
                "model": "gpt-4o",
                "base_url": "https://api.openai.com/v1"
            },
            {
                "name": "SiliconFlow",
                "key_name": "assistApiKeySilicon",
                "model": "deepseek-ai/DeepSeek-V3",
                "base_url": "https://api.siliconflow.cn/v1"
            },
            {
                "name": "Qwen",
                "key_name": "assistApiKeyQwen",
                "model": "qwen-max",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
            },
            {
                "name": "GLM",
                "key_name": "assistApiKeyGlm",
                "model": "glm-4-plus",
                "base_url": "https://open.bigmodel.cn/api/paas/v4/"
            }
        ]

        selected = (JUDGER_PROVIDER or "").strip().lower()
        if selected:
            provider_aliases = {
                "openai": "OpenAI",
                "siliconflow": "SiliconFlow",
                "silicon": "SiliconFlow",
                "qwen": "Qwen",
                "glm": "GLM",
            }
            target_name = provider_aliases.get(selected)
            if not target_name:
                logger.warning(
                    f"Unsupported JUDGER_PROVIDER='{JUDGER_PROVIDER}'. "
                    "Falling back to default provider order."
                )
            else:
                providers = [p for p in providers if p["name"] == target_name]
                logger.info(f"LLM Judger provider forced to: {target_name}")

        llms = []
        for p in providers:
            api_key = self.api_keys.get(p["key_name"])
            # Some example files use "sk-..." or similar placeholders
            if api_key and api_key != "sk-..." and not api_key.startswith("your_"):
                try:
                    llm = ChatOpenAI(
                        model=p["model"],
                        api_key=api_key,
                        base_url=p["base_url"],
                        max_retries=1,
                        request_timeout=30
                    )
                    llms.append({"llm": llm, "name": p["name"]})
                except Exception as e:
                    logger.warning(f"Failed to init {p['name']}: {e}")
                    
        if not llms:
            logger.warning("No valid API key found for LLM Judger. Auto-pass mode enabled.")
        return llms

    def _call_llm(self, prompt: str, timeout: int = 60) -> Optional[str]:
        """
        Call the first available LLM provider with a prompt. Returns the response text or None.
        """
        last_error = None
        for provider_info in self.llms:
            llm = provider_info["llm"]
            provider_name = provider_info["name"]
            try:
                logger.info(f"Calling LLM ({provider_name})...")
                response = llm.invoke([HumanMessage(content=prompt.strip())])
                return response.content.strip()
            except Exception as e:
                logger.warning(f"LLM call failed with {provider_name}: {e}")
                last_error = e
                continue
        if last_error:
            logger.error(f"All LLM providers failed. Last error: {last_error}")
        return None

    def judge(self, input_text: str, output_text: str, criteria: str,
              test_name: str = "") -> bool:
        """
        Evaluate if the output_text satisfies the criteria given the input_text.
        Records the result internally for report generation.
        Returns True if passed, False otherwise.
        """
        result_entry = {
            "timestamp": datetime.now().isoformat(),
            "test_name": self._tagged_test_name(test_name),
            "type": "single",
            "input": input_text[:1000],
            "output": output_text[:2000],
            "criteria": criteria,
            "passed": False,
            "error": None,
            "verdict": None,
            "analysis": None,
        }

        if not hasattr(self, 'llms'):
            self.llms = self._init_llms()

        if not self.llms:
            logger.warning("LLM Judger not initialized, skipping check.")
            result_entry["passed"] = True
            result_entry["error"] = "No LLM configured, auto-pass"
            self._results.append(result_entry)
            return True

        prompt = f"""
You are an impartial, strict, and highly capable judge evaluating an AI assistant's response.

[User Input]: {input_text}
[AI Response]: {output_text}

[Evaluation Criteria]: {criteria}

Carefully consider whether the AI Response satisfies all elements of the Evaluation Criteria based on the User Input.
Your final answer must be exactly one word: either "YES" or "NO". Do NOT provide any explanation or extra text.
        """
        
        last_error = None
        for provider_info in self.llms:
            llm = provider_info["llm"]
            provider_name = provider_info["name"]
            try:
                logger.info(f"Attempting judgement with {provider_name}...")
                response = llm.invoke([HumanMessage(content=prompt.strip())])
                verdict = response.content.strip().upper()
                
                # Clean up verdict just in case the model added punctuation like "YES." or "YES!"
                clean_verdict = verdict.replace(".", "").replace("!", "").replace("'", "").replace('"', "").strip()
                
                if clean_verdict.startswith("YES"):
                    passed = True
                elif clean_verdict.startswith("NO"):
                    passed = False
                else:
                    passed = "YES" in clean_verdict # Fallback
                    logger.warning(f"Unexpected LLM Judgement format from {provider_name}: '{verdict}'. Evaluated as passed={passed}.")
                    
                logger.info(f"Judgement [{test_name}] via {provider_name}: {clean_verdict} (Criteria: {criteria})")
                
                result_entry["verdict"] = verdict
                result_entry["passed"] = passed
                self._results.append(result_entry)
                return passed
            except Exception as e:
                logger.warning(f"LLM Judger failed with {provider_name}: {e}")
                last_error = e
                continue
                
        # If all providers failed
        logger.error(f"All LLM Judger providers failed. Last error: {last_error}")
        result_entry["error"] = str(last_error)
        self._results.append(result_entry)
        return False

    def judge_conversation(self, conversation: List[Dict[str, str]], criteria: str,
                           test_name: str = "") -> Dict[str, Any]:
        """
        Evaluate an entire multi-turn conversation holistically.
        
        Args:
            conversation: List of {"role": "user"|"assistant", "content": "..."} dicts
            criteria: Evaluation criteria for the whole conversation
            test_name: Name of the test
            
        Returns:
            Dict with keys: passed, scores, analysis, verdict
        """
        result_entry = {
            "timestamp": datetime.now().isoformat(),
            "test_name": self._tagged_test_name(test_name),
            "type": "conversation",
            "conversation_log": conversation,
            "criteria": criteria,
            "passed": False,
            "error": None,
            "verdict": None,
            "scores": {},
            "analysis": None,
        }

        if not self.llms:
            logger.warning("LLM Judger not initialized, auto-pass for conversation.")
            result_entry["passed"] = True
            result_entry["error"] = "No LLM configured, auto-pass"
            self._results.append(result_entry)
            return result_entry

        # Format conversation for the prompt
        conv_text = ""
        for i, turn in enumerate(conversation, 1):
            role_label = "User" if turn["role"] == "user" else "AI"
            conv_text += f"[Round {i // 2 + 1 if turn['role'] == 'assistant' else (i + 1) // 2} - {role_label}]: {turn['content']}\n"

        prompt = f"""You are an expert evaluator analyzing a multi-turn conversation between a user and an AI assistant.

=== CONVERSATION ===
{conv_text}
=== END CONVERSATION ===

[Evaluation Criteria]: {criteria}

Please evaluate this conversation on the following dimensions (score each 1-10):
1. **Coherence**: Does the AI maintain logical consistency across turns?
2. **Context Retention**: Does the AI remember and reference earlier parts of the conversation?
3. **Character Consistency**: Does the AI maintain a consistent persona/tone throughout?
4. **Response Quality**: Are the AI's responses natural, helpful, and appropriately detailed?
5. **Engagement**: Does the AI actively engage with the user's topics and show interest?

Respond in the following JSON format ONLY (no markdown code fences, no extra text):
{{
    "verdict": "YES" or "NO" (does the conversation pass the criteria overall?),
    "coherence": <score 1-10>,
    "context_retention": <score 1-10>,
    "character_consistency": <score 1-10>,
    "response_quality": <score 1-10>,
    "engagement": <score 1-10>,
    "analysis": "<2-3 sentence analysis of the conversation quality>"
}}"""

        response_text = self._call_llm(prompt)
        if response_text is None:
            result_entry["error"] = "All LLM providers failed"
            self._results.append(result_entry)
            return result_entry

        try:
            # Try to parse as JSON, handle potential markdown fences
            clean = response_text.strip()
            if clean.startswith("```"):
                # Remove markdown code fences
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                clean = clean.strip()

            data = json.loads(clean)
            
            verdict_str = str(data.get("verdict", "NO")).upper().strip()
            passed = verdict_str.startswith("YES")
            
            result_entry["passed"] = passed
            result_entry["verdict"] = verdict_str
            result_entry["scores"] = {
                "coherence": data.get("coherence", 0),
                "context_retention": data.get("context_retention", 0),
                "character_consistency": data.get("character_consistency", 0),
                "response_quality": data.get("response_quality", 0),
                "engagement": data.get("engagement", 0),
            }
            result_entry["analysis"] = data.get("analysis", "")
            
            avg = sum(result_entry["scores"].values()) / max(len(result_entry["scores"]), 1)
            logger.info(f"Conversation judgement [{test_name}]: {verdict_str} (avg score: {avg:.1f}/10)")
            
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse conversation judgement JSON: {e}. Raw: {response_text[:200]}")
            # Fallback — treat as YES/NO from raw text
            passed = "YES" in response_text.upper()
            result_entry["passed"] = passed
            result_entry["verdict"] = "YES" if passed else "NO"
            result_entry["analysis"] = response_text[:500]
            result_entry["error"] = f"JSON parse failed: {e}"

        self._results.append(result_entry)
        return result_entry

    @property
    def results(self) -> List[Dict[str, Any]]:
        return self._results

    def generate_report(self, output_dir: str = "tests/reports") -> Optional[str]:
        """
        Generate a comprehensive report of all judged results.
        
        1. Writes a JSON data file with all raw results.
        2. Calls the LLM to generate a narrative markdown report.
        3. Falls back to a table-based report if the LLM call fails.
        
        Returns the path to the Markdown report, or None if no results.
        """
        if not self._results:
            logger.info("No LLM Judger results to report.")
            return None

        report_dir = Path(output_dir)
        report_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = report_dir / f"test_report_{ts}.md"
        json_path = report_dir / f"test_report_{ts}.json"

        total = len(self._results)
        passed = sum(1 for r in self._results if r["passed"])
        failed = total - passed

        # --- JSON report (always written) ---
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "summary": {"total": total, "passed": passed, "failed": failed},
                "results": self._results,
            }, f, ensure_ascii=False, indent=2)

        # --- Try LLM-generated narrative report ---
        md_content = self._generate_narrative_report(total, passed, failed, json_path.name)
        
        if md_content is None:
            # Fallback to table-based report
            md_content = self._generate_table_report(total, passed, failed, json_path.name)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"\n{'='*60}")
        print(f"📋 Test Report: {md_path.resolve()}")
        print(f"   JSON Data:   {json_path.resolve()}")
        print(f"   Results:     {passed}/{total} passed")
        print(f"{'='*60}\n")

        return str(md_path)

    def _generate_narrative_report(self, total: int, passed: int, failed: int,
                                    json_filename: str) -> Optional[str]:
        """
        Use LLM to generate a rich, narrative markdown report. Returns None if LLM fails.
        """
        if not self.llms:
            return None

        # Build a structured summary for the LLM
        results_summary = []
        for i, r in enumerate(self._results, 1):
            entry = {
                "index": i,
                "test_name": r.get("test_name", f"check_{i}"),
                "type": r.get("type", "single"),
                "passed": r["passed"],
            }
            if r.get("type") == "conversation":
                entry["scores"] = r.get("scores", {})
                entry["analysis"] = r.get("analysis", "")
                # Summarize conversation length
                conv_log = r.get("conversation_log", [])
                entry["num_turns"] = len([t for t in conv_log if t["role"] == "user"])
                # Include a few excerpts
                if conv_log:
                    entry["first_user_msg"] = conv_log[0]["content"][:100] if conv_log else ""
                    entry["last_ai_msg"] = conv_log[-1]["content"][:200] if conv_log else ""
            else:
                entry["input"] = r.get("input", "")[:150]
                entry["output"] = r.get("output", "")[:300]
                entry["criteria"] = r.get("criteria", "")
                entry["verdict"] = r.get("verdict", "")

            if r.get("error"):
                entry["error"] = r["error"]
            results_summary.append(entry)

        prompt = f"""You are a QA engineer writing a professional test report for the N.E.K.O. AI assistant application.

Test Session Summary: {passed}/{total} checks passed, {failed} failed.
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Here are the detailed results:

{json.dumps(results_summary, ensure_ascii=False, indent=2)}

Write a well-structured Markdown report with the following sections:

1. **Title**: "# N.E.K.O. Test Report — <date>"
2. **Executive Summary**: 2-3 sentences summarizing overall test health. Mention pass rate, any concerning failures, and overall AI quality.
3. **Test Results Overview**: A markdown table showing each test with its result (✅/❌), plus any scores for conversation tests.
4. **Detailed Analysis**: For each test (especially conversation tests), write 1-2 sentences explaining what was tested and how the AI performed. Include quality dimension scores if available.
5. **Recommendations**: If there are failures or low scores, suggest areas for improvement. If all passed, note strengths.

End with: `_JSON data: [{json_filename}]({json_filename})_`

Write the report in Chinese (since this is a Chinese-language AI assistant), but keep technical terms in English. Keep it professional but readable.
Do NOT wrap the output in markdown code fences — output the raw markdown directly."""

        response = self._call_llm(prompt)
        if response:
            # Clean up any accidental code fences
            content = response.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
            return content
        return None

    def _generate_table_report(self, total: int, passed: int, failed: int,
                                json_filename: str) -> str:
        """
        Fallback: generate a simple table-based report (original behavior).
        """
        lines = [
            f"# N.E.K.O. Test Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Summary",
            f"- **Total checks**: {total}",
            f"- **Passed**: {passed}",
            f"- **Failed**: {failed}",
            "",
        ]

        # Single-check results table
        single_results = [r for r in self._results if r.get("type") != "conversation"]
        if single_results:
            lines.append("## Single-Check Results")
            lines.append("")
            lines.append("| # | Test | Input | Output (truncated) | Criteria | Result |")
            lines.append("|---|---|---|---|---|---|")
            for i, r in enumerate(single_results, 1):
                icon = "✅" if r["passed"] else "❌"
                inp = r.get("input", "").replace("|", "\\|").replace("\n", " ")[:60]
                out = r.get("output", "").replace("|", "\\|").replace("\n", " ")[:80]
                crit = r.get("criteria", "").replace("|", "\\|")[:60]
                name = r.get("test_name") or f"check_{i}"
                error_note = f" ⚠️ {r['error']}" if r.get("error") else ""
                lines.append(f"| {i} | {name} | {inp} | {out} | {crit} | {icon}{error_note} |")
            lines.append("")

        # Conversation results
        conv_results = [r for r in self._results if r.get("type") == "conversation"]
        if conv_results:
            lines.append("## Conversation Evaluation Results")
            lines.append("")
            for r in conv_results:
                name = r.get("test_name", "conversation")
                icon = "✅" if r["passed"] else "❌"
                lines.append(f"### {name} {icon}")
                lines.append("")
                scores = r.get("scores", {})
                if scores:
                    lines.append("| Dimension | Score |")
                    lines.append("|---|---|")
                    for dim, score in scores.items():
                        lines.append(f"| {dim} | {score}/10 |")
                    lines.append("")
                analysis = r.get("analysis")
                if analysis:
                    lines.append(f"> {analysis}")
                    lines.append("")
                conv_log = r.get("conversation_log", [])
                if conv_log:
                    lines.append(f"*{len([t for t in conv_log if t['role'] == 'user'])} rounds of conversation*")
                    lines.append("")

        lines.append(f"_JSON data: [{json_filename}]({json_filename})_")
        lines.append("")

        return "\n".join(lines)
