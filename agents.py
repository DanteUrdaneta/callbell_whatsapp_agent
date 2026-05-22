from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from modules.tools import get_table
import asyncio

model = OpenAIChatModel(
        'gpt-4o',
    provider=OpenAIProvider(
            api_key="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"
    ),
)

system_prompt = """You are an assistant agent responsible for gathering information and following up on potential leads via WhatsApp; your job is to nurture these leads"""


agent = Agent(model, system_prompt=system_prompt)

@agent.tool
def get_table_information_airtable(ctx: RunContext):
    return get_table()

