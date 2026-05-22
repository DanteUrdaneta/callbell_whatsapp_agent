from fastapi import FastAPI, Request, status, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from core.callbell import send_callbell_message
from dotenv import load_dotenv
from core.db import DB
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from agents import agent
import os

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Error: SUPABASE_URL or SUPABASE_KEY not exist in  .env")

app = FastAPI() 
db = DB(url=SUPABASE_URL, key=SUPABASE_KEY)


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

class CallbellPayload(BaseModel):
    to_number: str = Field(..., alias="to")
    from_number: str = Field(..., alias="from")
    text: Optional[str] = None  
    uuid: str
    status: str
    channel: str
    contact: Dict[str, Any]
    createdAt: str
    attachments: Optional[List[str]] = None


class CallbellWebhook(BaseModel):
    event: str                 
    payload: CallbellPayload


app.add_middleware(
	    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def index():
    return "hello world"


@app.post("/webhook/callbell", status_code=status.HTTP_200_OK)
async def callbell_webhook(webhook_data: CallbellWebhook):

    payload = webhook_data.payload

    if payload.status != "received":
        return {"status": "ignored", "message": "Message was not received"}
    
    lead_phone = payload.from_number
    user_message = payload.text

    ai_response = await agent.run(user_message)
    
    db.update_history_message(
                phone_number=customer_phone,
            user_message=user_message,
            ai_message=ai_response.output
        )
    
    
    try:
        
        lead = db.create_new_lead(lead_phone)

        await send_callbell_message(to_phone=lead_phone, text_content=ai_response.output)         
    
    
        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
