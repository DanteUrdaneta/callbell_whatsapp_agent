from fastapi import FastAPI, Request, status, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from core.callbell import send_callbell_message
from dotenv import load_dotenv
from core.db import DB
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from modules.tools import history
from agents import agent
from groq import AsyncGroq
import traceback
import httpx
import os

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Error: SUPABASE_URL or SUPABASE_KEY not exist in  .env")

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))
app = FastAPI() 
db = DB(url=SUPABASE_URL, key=SUPABASE_KEY)



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
    lead_uuid = payload.uuid

    lead = self.get_lead(phone_number)

    if lead:
        lead_status = lead.get("status")
        
        if lead_status == "success":
            return "can't send reply because the lead status is successful"
        
    
    
    if payload.attachments and len(payload.attachments) > 0:
        file_url = payload.attachments[0]

    try: 
    
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(file_url)
        
        if response.status_code == 200:
            audio_bytes = response.content
            
            transcription = await groq_client.audio.transcriptions.create(
            file=("audio.ogg", audio_bytes),
            model="whisper-large-v3"
            )

            user_message = transcription.text
            print(f"user: {user_message}")
    except Exception as audio_err:
            print(f"⚠️ Error processing the audio : {str(audio_err)}")
            traceback.print_exc()
    try:
        
        db_history = db.get_chat_history(phone_number=lead_phone, limit=5)
        
        complete_user_message = f"(uuid: {lead_uuid}, phone_numer: {lead_phone})"

        ai_response = await agent.run(user_message, message_history = history(db_history))

        try:
            db.update_history_message(
                    phone_number=lead_phone,
                    user_message=user_message,
                    ai_message=ai_response.output
                )

        except ValueError:
            lead = db.create_new_lead(lead_phone)
            
            db.update_history_message(
                    phone_number=lead_phone,
                    user_message=user_message,
                    ai_message=ai_response.output
                )

        await send_callbell_message(to_phone=lead_phone, text_content=ai_response.output)         
    
    
        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
