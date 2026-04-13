from langchain.agents import Agent

from vingolf.api.routers.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain.agents.middleware import SummarizeMiddleware

agent = create_agent(
    model = "gpt-4-0613",
    description = "A helpful assistant that provides information about the weather.",
    tools = [],
    middleware = [
        SummarizeMiddleware(),

    ]
)