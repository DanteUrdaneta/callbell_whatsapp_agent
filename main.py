from fastapi import FastAPI, Request, status, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional


app = FastAPI() 


class CallbellPayload(BaseModel):
    event: str                 
    payload: Dict[str, Any]


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
async def callbell_webhook(
        payload: CallbellPayload, 
    request: Request
):
    print(payload)
    return {"status": "success", "message": "Event processed"}
