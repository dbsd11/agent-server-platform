# LLM Client - 封装大模型调用
import os
import json
from typing import Dict, Any, List, Optional
from openai import OpenAI
from logger import logger


class LLMClient:
    """
    大模型客户端封装

    支持阿里云百炼 DashScope API，使用 OpenAI 兼容接口
    """

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = os.getenv("LLM_MODEL", "qwen-plus")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "4096"))
        self.enable_thinking = os.getenv("LLM_ENABLE_THINKING", "true").lower() == "true"
        self.timeout = int(os.getenv("LLM_TIMEOUT", "120"))  # 默认 120 秒超时

        if not self.api_key:
            logger.warning("DASHSCOPE_API_KEY not configured, LLM features disabled")
            self.client = None
        else:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout
            )
            logger.info(f"LLM client initialized: model={self.model}, timeout={self.timeout}s")

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> Optional[str]:
        """
        发送聊天请求

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            temperature: 温度参数，控制随机性

        Returns:
            模型回复内容，失败返回 None
        """
        import time

        if not self.client:
            logger.error("LLM client not initialized")
            return None

        try:
            start_time = time.time()
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=self.max_tokens,
                extra_body={"enable_thinking": self.enable_thinking} if self.enable_thinking else None
            )
            elapsed_time = time.time() - start_time

            if completion.choices:
                content = completion.choices[0].message.content
                logger.info(f"LLM response received in {elapsed_time:.2f}s, length: {len(content)} chars")
                logger.debug(f"LLM response: {content[:100]}...")
                return content
            else:
                logger.warning(f"LLM returned empty response after {elapsed_time:.2f}s")
                return None

        except Exception as e:
            elapsed_time = time.time() - start_time if 'start_time' in locals() else 0
            logger.error(f"LLM call failed after {elapsed_time:.2f}s: {str(e)}")
            return None

    def decompose_goal(self, goal: str, context: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        使用大模型智能分解目标为子任务

        Args:
            goal: 用户目标描述
            context: 上下文信息（优先级、超时等）

        Returns:
            子任务列表，每个子任务包含 goal, type, priority, timeout_seconds, context (含 role 和 system_prompt)
            失败返回 None
        """
        if not self.client:
            return None

        # 构造提示词
        prompt = f"""你是一个任务分解专家。请将以下目标分解为多个子任务，每个子任务由一个专门角色的智能代理来完成。

目标：{goal}

上下文信息：
- 优先级：{context.get('priority', 0)}
- 超时时间：{context.get('timeout_seconds', 3600)}秒

重要要求：
1. 将目标分解为 2-5 个子任务
2. **每个子任务必须指定执行角色的专家代理**
3. 每个子任务的 context 中必须包含：
   - "role": 代理的角色/专长（如"数学家"、"数据分析师"、"编程专家"等）
   - "system_prompt": 系统提示词，定义该角色的能力和行为
   - "question": 该角色需要回答的具体问题或任务
4. 子任务之间应该有清晰的逻辑关系

示例：
如果目标是"计算 1+1"，应该分解为：
- 子任务1: "计算数学表达式的结果"
  context: {{
    "role": "数学家",
    "system_prompt": "你是一个专业的数学家，擅长各种数学计算和证明。请给出准确、简洁的答案。",
    "question": "请计算 1+1 的结果"
  }}

如果目标是"分析销售数据并生成报告"，应该分解为：
- 子任务1: "分析销售数据趋势"
  context: {{
    "role": "数据分析师",
    "system_prompt": "你是一个资深数据分析师，擅长从数据中发现趋势和洞察。请用数据说话，给出专业分析。",
    "question": "分析以下销售数据的趋势和关键洞察..."
  }}

返回格式（严格遵循）：
```json
{{
  "subtasks": [
    {{
      "goal": "子任务描述",
      "type": "execution",
      "priority": 0,
      "timeout_seconds": 3600,
      "context": {{
        "role": "角色名称",
        "system_prompt": "系统提示词",
        "question": "具体问题或任务"
      }}
    }}
  ]
}}
```

只返回 JSON，不要其他内容。"""

        messages = [
            {"role": "system", "content": "你是一个专业的任务分解助手，擅长将复杂目标分解为多个角色明确的子任务。"},
            {"role": "user", "content": prompt}
        ]

        try:
            import time
            start_time = time.time()
            response = self.chat(messages, temperature=0.3)
            elapsed_time = time.time() - start_time

            if not response:
                return None

            # 提取 JSON（可能被 markdown 代码块包裹）
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()

            # 解析 JSON
            data = json.loads(json_str)
            subtasks = data.get("subtasks", [])

            # 验证和补充字段
            validated_subtasks = []
            for task in subtasks:
                if "goal" in task:
                    validated_task = {
                        "goal": task["goal"],
                        "type": task.get("type", "execution"),
                        "priority": task.get("priority", context.get("priority", 0)),
                        "timeout_seconds": task.get("timeout_seconds", context.get("timeout_seconds", 3600)),
                        "context": task.get("context", {})
                    }
                    validated_subtasks.append(validated_task)

            if validated_subtasks:
                logger.info(f"LLM decomposed goal into {len(validated_subtasks)} subtasks in {elapsed_time:.2f}s")
                return validated_subtasks
            else:
                logger.warning(f"LLM returned empty subtask list after {elapsed_time:.2f}s")
                return None


        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Goal decomposition failed: {str(e)}")
            return None


# 全局单例
llm_client = LLMClient()
