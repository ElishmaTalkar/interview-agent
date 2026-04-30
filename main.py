import json, os, tempfile
from typing import List, Optional

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# ── Clients ────────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

TTS_SERVER     = "http://localhost:5000"
GROQ_MODEL     = "llama-3.1-8b-instant"
MIN_AUDIO_BYTES = 8_044

app = FastAPI(title="InterviewIQ API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Helpers ────────────────────────────────────────────────────────────────────
def load_prompt(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

# ── Pydantic models ────────────────────────────────────────────────────────────
class IntakeRequest(BaseModel):
    role: str
    background: str
    focus: str
    difficulty: str
    company: Optional[str] = ""

class AgentTurnRequest(BaseModel):
    history: List[dict]
    current_question: dict
    probe_count: int
    is_first: bool = False

class EvaluateRequest(BaseModel):
    user_text: str
    question: dict

class ReportRequest(BaseModel):
    history: List[dict]
    scores: List[dict]
    setup: dict


# ── Serve frontend ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_html():
    with open("index.html", "r") as f:
        return f.read()


# ── Intake + Question Bank ─────────────────────────────────────────────────────
@app.post("/api/intake")
async def intake(req: IntakeRequest):
    # Step 1 – intake agent
    intake_resp = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": load_prompt("intake_agent.txt")},
            {"role": "user", "content": (
                f"Target Role: {req.role}\nBackground: {req.background}\n"
                f"Focus: {req.focus}\nDifficulty: {req.difficulty}\nCompany: {req.company}"
            )},
        ],
        model=GROQ_MODEL, temperature=0.2, response_format={"type": "json_object"},
    )
    setup = json.loads(intake_resp.choices[0].message.content)

    # Step 2 – question bank
    qb_resp = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": load_prompt("question_bank_agent.txt")},
            {"role": "user", "content": (
                f"Category: {setup['interview_category']}\n"
                f"Difficulty: {setup['difficulty_level']}\n"
                f"Topics: {setup.get('metadata',{}).get('suggested_topics',[])}"
            )},
        ],
        model=GROQ_MODEL, temperature=0.7, response_format={"type": "json_object"},
    )
    qb = json.loads(qb_resp.choices[0].message.content)

    return {"setup": setup, "questions": qb.get("questions", [])}


# ── Transcribe audio ───────────────────────────────────────────────────────────
@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    if len(audio_bytes) < MIN_AUDIO_BYTES:
        raise HTTPException(400, "Recording too short — please speak for at least 1 second.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
        f.write(audio_bytes)
        path = f.name

    try:
        with open(path, "rb") as f:
            result = groq_client.audio.transcriptions.create(
                file=(os.path.basename(path), f.read()),
                model="whisper-large-v3",
                response_format="text",
            )
        return {"text": result}
    finally:
        if os.path.exists(path):
            os.remove(path)


# ── Agent turn ─────────────────────────────────────────────────────────────────
@app.post("/api/agent-turn")
async def agent_turn(req: AgentTurnRequest):
    # First message – just ask the question
    if req.is_first:
        text = req.current_question["question"]
        return {"action": "question", "text": text, "audio_url": _tts_url(text)}

    # Decide probe or next
    ctx = "\n".join(
        f"{'Interviewer' if m['role']=='assistant' else 'Candidate'}: {m['content']}"
        for m in req.history[-4:]
    )
    resp = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": load_prompt("interviewer_agent.txt")},
            {"role": "user", "content": (
                f"Question: {req.current_question['question']}\n"
                f"Criteria: {req.current_question['evaluation_criteria']}\n"
                f"Suggested probe: {req.current_question['follow_up_probe']}\n"
                f"probe_count: {req.probe_count}\n\n"
                f"Conversation:\n{ctx}"
            )},
        ],
        model=GROQ_MODEL, temperature=0.3, response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    text = data.get("response_text", "Let's move on.")
    return {
        "action": data.get("action", "next"),
        "text": text,
        "audio_url": _tts_url(text),
    }


# ── Silent evaluator ───────────────────────────────────────────────────────────
@app.post("/api/evaluate")
async def evaluate(req: EvaluateRequest):
    prompt = (
        f"{load_prompt('evaluator_agent.txt')}\n\n"
        f"Question: {req.question.get('question')}\n"
        f"Criteria: {req.question.get('evaluation_criteria')}\n"
        f"Answer: {req.user_text}"
    )
    resp = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile", temperature=0.2, response_format={"type": "json_object"}
    )
    return json.loads(resp.choices[0].message.content)


# ── Coaching report ────────────────────────────────────────────────────────────
@app.post("/api/report")
async def report(req: ReportRequest):
    transcript = "\n".join(
        f"{'Interviewer' if m['role']=='assistant' else 'Candidate'}: {m['content']}"
        for m in req.history
    )
    scores_txt = "\n".join(
        f"Q{i+1}: {ev['question']}\n  {ev['scores']}"
        for i, ev in enumerate(req.scores)
    )
    role     = req.setup.get("interview_category", "the role")
    diff     = req.setup.get("difficulty_level", "")

    prompt = (
        f"TRANSCRIPT:\n{transcript}\n\nSCORES:\n{scores_txt}"
    )
    
    resp = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": load_prompt("coach_agent.txt").replace("{diff}", diff).replace("{role}", role)},
            {"role": "user", "content": prompt}
        ],
        model="llama-3.3-70b-versatile", temperature=0.3
    )
    return {"report": resp.choices[0].message.content}


# ── TTS helper ─────────────────────────────────────────────────────────────────
def _tts_url(text: str) -> Optional[str]:
    """Returns a data-URI wav if TTS server is available, else None."""
    try:
        voices_resp = requests.get(f"{TTS_SERVER}/voices", timeout=3)
        if voices_resp.status_code != 200:
            return None
        voices = voices_resp.json().get("voices", [])
        if not voices:
            return None
        voice_id = voices[0]["voice_id"]
        resp = requests.post(
            f"{TTS_SERVER}/speak",
            json={"text": text, "voice_id": voice_id},
            timeout=60,
        )
        if resp.status_code == 200:
            import base64
            b64 = base64.b64encode(resp.content).decode()
            return f"data:audio/wav;base64,{b64}"
    except Exception:
        pass
    return None


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
