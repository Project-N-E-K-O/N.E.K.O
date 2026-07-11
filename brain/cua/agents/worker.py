# Modifications Copyright 2025-2026 Project N.E.K.O. Team
#
# This file is derived from simular-ai/Agent-S
# (https://github.com/simular-ai/Agent-S), licensed under the Apache License,
# Version 2.0; see brain/cua/AGENT_LICENSE.txt. Modified by the Project N.E.K.O. Team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import ast
import re
import textwrap
from typing import Any, Dict, List, Tuple

from brain.cua.agents.grounding import ACI
from utils.logger_config import get_module_logger
from brain.cua.core.module import BaseModule
from brain.cua.memory.procedural_memory import PROCEDURAL_MEMORY
from brain.cua.utils.common_utils import (
    call_llm_safe,
    extract_first_agent_function,
    parse_single_code_from_string,
    sanitize_code,
    split_thinking_response,
)

logger = get_module_logger(__name__, "Agent")


def _safe_eval_agent_call(code_string: str, agent: Any) -> Any:
    """安全地执行 agent.method() 调用，替代不安全的 eval()。

    使用 AST 解析验证代码仅为 agent 对象上的方法调用，
    参数通过 ast.literal_eval 安全解析（仅允许字面量），
    防止 LLM 生成的恶意代码执行任意 Python 表达式。
    """
    if code_string is None:
        raise ValueError("code_string is None, cannot evaluate")

    code_string = code_string.strip()
    if not code_string:
        raise ValueError("code_string is empty")

    # 使用 AST 解析，仅接受 agent.xxx() 形式的调用
    tree = ast.parse(code_string, mode="eval")
    node = tree.body
    if not isinstance(node, ast.Call):
        raise ValueError(f"Expected a function call, got {type(node).__name__}")

    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "agent"
    ):
        raise ValueError("Only agent.method() calls are allowed")

    method_name = func.attr
    method = getattr(agent, method_name, None)
    if method is None or not callable(method):
        raise AttributeError(f"agent has no callable method '{method_name}'")

    # 安全解析参数：仅允许字面量（数字、字符串、布尔、None、元组、列表、字典）
    args = [ast.literal_eval(arg) for arg in node.args]
    kwargs = {
        kw.arg: ast.literal_eval(kw.value)
        for kw in node.keywords
        if kw.arg is not None
    }
    return method(*args, **kwargs)


class Worker(BaseModule):
    def __init__(
        self,
        engine_params: Dict,
        grounding_agent: ACI,
        platform: str = "ubuntu",
        max_trajectory_length: int = 8,
        enable_reflection: bool = True,
    ):
        """
        Worker receives the main task and generates actions, without the need of hierarchical planning
        Args:
            engine_params: Dict
                Parameters for the multimodal engine
            grounding_agent: Agent
                The grounding agent to use
            platform: str
                OS platform the agent runs on (darwin, linux, windows)
            max_trajectory_length: int
                The amount of images turns to keep
            enable_reflection: bool
                Whether to enable reflection
        """
        super().__init__(engine_params, platform)

        self.grounding_agent = grounding_agent
        self.max_trajectory_length = max_trajectory_length
        self.enable_reflection = enable_reflection
        self.temperature = engine_params.get("temperature", 0.0)
        self.use_thinking = engine_params.get("model", "") in [
            "claude-3-7-sonnet-20250219"
        ]
        self.reset()

    def reset(self):
        if self.platform != "linux":
            skipped_actions = ["set_cell_values"]
        else:
            skipped_actions = []

        sys_prompt = PROCEDURAL_MEMORY.construct_simple_worker_procedural_memory(
            type(self.grounding_agent), skipped_actions=skipped_actions
        ).replace("CURRENT_OS", self.platform)

        self.generator_agent = self._create_agent(sys_prompt)
        self.reflection_agent = self._create_agent(
            PROCEDURAL_MEMORY.REFLECTION_ON_TRAJECTORY
        )

        self.turn_count = 0
        self.worker_history = []
        self.reflections = []
        self.cost_this_turn = 0
        self.screenshot_inputs = []

    # Flushing strategy dependant on model context limits
    def flush_messages(self):
        engine_type = self.engine_params.get("engine_type", "")

        # Flush strategy for long-context models: keep all text, only keep latest images
        if engine_type in ["anthropic", "openai", "gemini"]:
            max_images = self.max_trajectory_length
            for agent in [self.generator_agent, self.reflection_agent]:
                # keep latest k images
                img_count = 0
                for i in range(len(agent.messages) - 1, -1, -1):
                    for j in range(len(agent.messages[i]["content"])):
                        if "image" in agent.messages[i]["content"][j].get("type", ""):
                            img_count += 1
                            if img_count > max_images:
                                del agent.messages[i]["content"][j]

        # Flush strategy for non-long-context models: drop full turns
        else:
            # generator msgs are alternating [user, assistant], so 2 per round
            if len(self.generator_agent.messages) > 2 * self.max_trajectory_length + 1:
                self.generator_agent.messages.pop(1)
                self.generator_agent.messages.pop(1)
            # reflector msgs are all [(user text, user image)], so 1 per round
            if len(self.reflection_agent.messages) > self.max_trajectory_length + 1:
                self.reflection_agent.messages.pop(1)

    def generate_next_action(
        self,
        instruction: str,
        obs: Dict,
    ) -> Tuple[Dict, List]:
        """
        Predict the next action(s) based on the current observation.
        """
        agent = self.grounding_agent
        generator_message = (
            ""
            if self.turn_count > 0
            else "The initial screen is provided. No action has been taken yet."
        )

        # Load the task into the system prompt
        if self.turn_count == 0:
            self.generator_agent.add_system_prompt(
                self.generator_agent.system_prompt.replace(
                    "TASK_DESCRIPTION", instruction
                )
            )

        # Get the per-step reflection
        reflection = None
        reflection_thoughts = None
        if self.enable_reflection:
            # Load the initial message
            if self.turn_count == 0:
                text_content = textwrap.dedent(
                    f"""
                    Task Description: {instruction}
                    Current Trajectory below:
                    """
                )
                updated_sys_prompt = (
                    self.reflection_agent.system_prompt + "\n" + text_content
                )
                self.reflection_agent.add_system_prompt(updated_sys_prompt)
                self.reflection_agent.add_message(
                    text_content="The initial screen is provided. No action has been taken yet.",
                    image_content=obs["screenshot"],
                    role="user",
                )
            # Load the latest action
            else:
                self.reflection_agent.add_message(
                    text_content=self.worker_history[-1],
                    image_content=obs["screenshot"],
                    role="user",
                )
                full_reflection = call_llm_safe(
                    self.reflection_agent,
                    temperature=self.temperature,
                    use_thinking=self.use_thinking,
                )
                reflection, reflection_thoughts = split_thinking_response(
                    full_reflection
                )
                self.reflections.append(reflection)
                generator_message += f"REFLECTION: You may use this reflection on the previous action and overall trajectory:\n{reflection}\n"
                logger.debug("REFLECTION: %s", reflection)

        # Add finalized message to conversation
        generator_message += f"\nCurrent Text Buffer = [{','.join(agent.notes)}]\n"
        self.generator_agent.add_message(
            generator_message, image_content=obs["screenshot"], role="user"
        )

        full_plan = call_llm_safe(
            self.generator_agent,
            temperature=self.temperature,
            use_thinking=self.use_thinking,
        )
        plan, plan_thoughts = split_thinking_response(full_plan)
        # NOTE: currently dropping thinking tokens from context
        self.worker_history.append(plan)
        logger.debug("FULL PLAN:\n%s", full_plan)
        self.generator_agent.add_message(plan, role="assistant")

        # Use the grounding agent to convert agent_action("desc") into agent_action([x, y])
        try:
            agent.assign_coordinates(plan, obs)
            plan_code = parse_single_code_from_string(plan.split("Grounded Action")[-1])
            plan_code = sanitize_code(plan_code)
            plan_code = extract_first_agent_function(plan_code)
            exec_code = _safe_eval_agent_call(plan_code, agent)
        except Exception as e:
            logger.error("Error in parsing plan code: %s", e)
            plan_code = "agent.wait(1.0)"
            exec_code = _safe_eval_agent_call(plan_code, agent)

        executor_info = {
            "full_plan": full_plan,
            "executor_plan": plan,
            "plan_thoughts": plan_thoughts,
            "plan_code": plan_code,
            "reflection": reflection,
            "reflection_thoughts": reflection_thoughts,
        }
        self.turn_count += 1

        self.screenshot_inputs.append(obs["screenshot"])
        self.flush_messages()

        return executor_info, [exec_code]
