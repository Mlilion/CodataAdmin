from typing import Any

from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.team import Team

from app.config.setting import settings


class AgnoFactory:
    """Agno 工厂类 - 统一管理 Agent、Team 创建逻辑"""

    # 配置常量
    AGENT_DESCRIPTION = (
        "你是一个数据分析助手，可以基于语义模型的权威口径回答业务数据问题，"
        "也可以回答一般性问题。"
    )
    # 语义取数的行为约束（参考方案 §5「LLM 只碰两处」：NL→意图、结果解读）。
    # 工具接上了还不够 —— 不写清怎么用，模型会凭记忆猜 code、自己编 SQL、给裸数字。
    AGENT_INSTRUCTIONS = [
        "保持回答简洁明了",
        "如果不确定，请说明",
        # ---- 取数流程 ----
        "涉及业务数据（收入、用户数、订单等）时，必须先调 search_semantic 找到指标 code，"
        "不要凭记忆猜 code；再调 get_model_context 取权威口径；最后用 query_indicator 取数。",
        "绝对不要自己写 SQL。口径 SQL 由系统从已审核的权威口径取得，你只提供指标、"
        "时间窗、拆分维度和过滤条件。",
        "时间窗几乎总是必填。用户没说时间就问他，不要默认查全部（会全表扫描且结果无意义）。",
        # ---- 结果解读 ----
        "报告数字时必须带出 caliber 里的口径声明（指标全名、单位、口径定义、模型版本），"
        "脱离口径的裸数字用户无法判断可信度。",
        "如果返回的 knowledge 里有 caveat 类说明，必须一并告知用户 —— 那是口径护栏。",
        "结果 truncated=true 时要说明数据被截断、不完整。",
        # ---- 边界与澄清 ----
        "命中 not_buildable=true 的指标时，如实说明本模型算不了它以及正确出处，"
        "绝不用其他指标凑一个近似值，也不要编造。",
        "命中 needs_clarify=true 的指标时，先向用户澄清口径选择，不要自行决定。",
        "取数失败时看 errors 里的 rule 与 allowed：按 allowed 改正参数后重试；"
        "rule=caliber_break 表示时间窗跨越口径分水岭、数据不可比，要如实告知用户，"
        "不要给出一个看似正确的数字。",
    ]
    AGENT_EXPECTED_OUTPUT = "中文回答；涉及数据时附口径声明与必要的口径提示"
    AGENT_TEMPERATURE = 0.7
    NUM_HISTORY_RUNS = 3
    REQUEST_TIMEOUT = 60.0  # LLM 请求总超时（秒），流式响应需放长
    CONNECT_TIMEOUT = 10.0  # TCP 连接超时（秒）

    def create_agent(
        self,
        user_id: str,
        dept_id: str,
        session_id: str,
        db: Any | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> Team:
        """
        创建带 Agent 的 Team 实例。

        参数:
        - user_id (str): 用户标识。
        - dept_id (str): 部门/团队标识。
        - session_id (str): 会话 ID。
        - db (Any | None): Agno 持久化数据库实例，可选。
        - model_config (dict | None): 运行时模型配置，覆盖系统默认。
            支持字段：base_url, api_key, model_id, temperature。

        返回:
        - Team: 配置好的 Team。
        """
        # 优先使用运行时配置，否则 fallback 到系统 settings
        base_url = settings.OPENAI_BASE_URL
        api_key = settings.OPENAI_API_KEY
        model_id = settings.OPENAI_MODEL
        temperature = self.AGENT_TEMPERATURE

        if model_config:
            base_url = model_config.get("base_url") or base_url
            api_key = model_config.get("api_key") or api_key
            model_id = model_config.get("model_id") or model_id
            if isinstance(model_config.get("temperature"), (int, float)):
                temperature = float(model_config["temperature"])

        # 创建 Agent
        # 语义取数工具（P4）：search_semantic 找指标 → get_model_context 取权威口径
        # → query_indicator 取数。agent **不写 SQL**，只填参数，故天然免疫丢口径条件。
        from app.api.v1.module_semantic.retrieval.tools import semantic_tools

        fastapiadmin_agent = Agent(
            id=user_id,
            name="fastapiadmin_agent",
            role="You are a helpful AI assistant",
            description=self.AGENT_DESCRIPTION,
            tools=semantic_tools(),
        )

        # 创建 Team
        fastapiadmin_team = Team(
            id=dept_id,
            user_id=user_id,
            session_id=session_id,
            model=OpenAILike(
                id=model_id,
                api_key=api_key,
                base_url=base_url,
                temperature=temperature,
                timeout=self.REQUEST_TIMEOUT,
            ),
            members=[fastapiadmin_agent],
            instructions=self.AGENT_INSTRUCTIONS,
            expected_output=self.AGENT_EXPECTED_OUTPUT,
            add_datetime_to_context=True,
            add_history_to_context=True,
            markdown=True,
            num_history_runs=self.NUM_HISTORY_RUNS,
            input_schema=None,
            output_schema=None,
            parse_response=True,
            read_chat_history=True,
            db=db,
        )

        return fastapiadmin_team
