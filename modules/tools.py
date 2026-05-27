from pyairtable import Api
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

ACCESS_TOKEN = "pat6RbHdPN4t76tu5.ba6cd9280ae7305533c89723eb47e40e4b4790224ce6b854e8c5a7b16a15e208"
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
    
