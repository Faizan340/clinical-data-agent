from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.service import ask

app = FastAPI(title="Clinical Data Analyst Agent")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    tenant_id: int = 1


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ask")
def ask_endpoint(req: AskRequest):
    return ask(req.question, req.tenant_id)
