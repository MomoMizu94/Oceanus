from fastapi import FastAPI
from fastapi.responses import JSONResponse
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from litellm import APIConnectionError, Timeout

from agent_core.model_client import call_completion


app = FastAPI(title="Oceanus API")


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    stream: Literal[False] = False

def model_error(status_code: int, message: str, code: str) -> JSONResponse:
    """ Build a JSON error response from model request """
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "server_error",
                "param": None,
                "code": code
            }
        }
    )

""" API ENDPOINTS """
@app.get("/health")
def health() -> dict[str, str]:
    """ Report that the server is responding """
    return {"status": "ok"}

@app.post("/v1/chat/completions")
def chat_completions(request: ChatRequest):
    """ Return one text completion or model connection error """
    messages = [
        message.model_dump()
        for message in request.messages
    ]

    try:
        response = call_completion(
            messages,
            model=request.model,
            use_tools=False
        )
    except Timeout:
        return model_error(
            504,
            "The model provider did not respond in time.",
            "model_timeout"
        )
    except APIConnectionError:
        return model_error(
            502,
            "Could not connect to the model provider.",
            "model_connection_error"
        ) 

    return response.model_dump(exclude_none=True)