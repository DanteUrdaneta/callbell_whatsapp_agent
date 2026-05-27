from pyairtable import Api
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

ACCESS_TOKEN = "pat6Ous9xAoGdGJjp.35a64a6e9cf2c3a9b6dde787db02b051f15097a3341368ab08aa0d81553472f9"
BASE_ID = "app8KjdUhteh6fbEf"
TABLE_NAME = "RESUMEN"

api = Api(ACCESS_TOKEN)


def get_table(table_name):
    table = api.table(BASE_ID, table_name)
    print("--- getting table info ---")
    records = table.all()
    for record in records:
        print(record['fields'])
    return records


def history(db_history: list) -> list[ModelMessage]:
  
    agent_history: list[ModelMessage] = []
    
    for msg in db_history:
        user_request = ModelRequest(parts=[UserPromptPart(content=msg["user_message"])])
        agent_history.append(user_request)
        
  
        ai_response = ModelResponse(parts=[TextPart(content=msg["ai_message"])])
        agent_history.append(ai_response)
        
    return agent_history 
    
