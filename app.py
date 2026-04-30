import streamlit as st
import json
import os
import requests
from groq import Groq
from dotenv import load_dotenv
from audio_recorder_streamlit import audio_recorder
import tempfile
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Check API Keys
if "GROQ_API_KEY" not in os.environ or os.environ["GROQ_API_KEY"] == "your_groq_api_key_here":
    st.error("Please set your GROQ_API_KEY in the .env file.")
    st.stop()

if "GEMINI_API_KEY" in os.environ and os.environ["GEMINI_API_KEY"] != "your_gemini_api_key_here":
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
else:
    st.error("Please set your GEMINI_API_KEY in the .env file.")
    st.stop()

# Initialize Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Local XTTS v2 voice server (your voice agent)
TTS_SERVER = "http://localhost:5000"

st.set_page_config(page_title="AI Interview Coach", page_icon="🤖", layout="centered")

# --- Helper Functions ---

def get_available_voices():
    """Fetch cloned voices from the local XTTS server."""
    try:
        resp = requests.get(f"{TTS_SERVER}/voices", timeout=3)
        if resp.status_code == 200:
            return resp.json().get("voices", [])
    except requests.exceptions.ConnectionError:
        pass
    return []

def text_to_speech(text):
    """Generate speech via the local XTTS v2 voice cloning server."""
    voice_id = _get_active_voice_id()
    if not voice_id:
        return None  # No voice available — silent text-only fallback
    try:
        resp = requests.post(
            f"{TTS_SERVER}/speak",
            json={"text": text, "voice_id": voice_id},
            timeout=60
        )
        if resp.status_code == 200:
            return resp.content  # WAV bytes
    except requests.exceptions.ConnectionError:
        pass  # Voice server offline — silent fallback
    return None


def _get_active_voice_id():
    """Silently auto-select the first available voice on the server."""
    voices = get_available_voices()
    if voices:
        return voices[0]["voice_id"]
    return None

# Minimum WAV size for a valid ~0.5 second recording (44-byte header + ~8000 bytes of audio)
_MIN_AUDIO_BYTES = 8_044

def transcribe_audio(audio_bytes):
    # Guard: reject suspiciously small recordings before hitting the API
    if len(audio_bytes) < _MIN_AUDIO_BYTES:
        return ""

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
        temp_audio.write(audio_bytes)
        temp_audio_path = temp_audio.name
        
    try:
        with open(temp_audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(temp_audio_path), file.read()),
                model="whisper-large-v3",
                response_format="text"
            )
        os.remove(temp_audio_path)
        return transcription
    except Exception as e:
        st.error(f"Transcription error: {e}")
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        return ""

def evaluate_response(user_text, question_data):
    try:
        model = genai.GenerativeModel("gemini-2.5-flash", generation_config={"response_mime_type": "application/json"})
        
        prompt = f"""
        You are an expert AI Interview Evaluator. The user has just answered an interview question.
        Analyze their response based on the evaluation criteria and score them on 5 dimensions (1-10).
        
        Question: {question_data.get('question')}
        Evaluation Criteria: {question_data.get('evaluation_criteria')}
        User's Answer: {user_text}
        
        Respond ONLY with a valid JSON object exactly matching this schema:
        {{
          "clarity": integer,
          "relevance": integer,
          "technical_accuracy": integer,
          "communication_skills": integer,
          "completeness": integer,
          "feedback_notes": "string (brief specific observation)"
        }}
        """
        
        response = model.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        st.error(f"Evaluation error: {e}")
        return None

def generate_coach_report(interview_history, evaluation_scores, setup_data):
    """Use Gemini to generate a structured markdown coaching report."""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")

        # Build transcript string
        transcript = "\n".join(
            [f"{'Interviewer' if m['role'] == 'assistant' else 'Candidate'}: {m['content']}"
             for m in interview_history]
        )

        # Build scores summary
        scores_summary = ""
        for i, ev in enumerate(evaluation_scores):
            s = ev["scores"]
            scores_summary += (
                f"\nQ{i+1}: {ev['question']}\n"
                f"  Scores — Clarity: {s.get('clarity')}, Relevance: {s.get('relevance')}, "
                f"Technical: {s.get('technical_accuracy')}, Communication: {s.get('communication_skills')}, "
                f"Completeness: {s.get('completeness')}\n"
                f"  Note: {s.get('feedback_notes', '')}\n"
            )

        role = setup_data.get("interview_category", "the role")
        difficulty = setup_data.get("difficulty_level", "")

        prompt = f"""
You are an expert interview coach reviewing a mock interview for a {difficulty} {role} position.

Below is the full interview transcript and per-question evaluation scores.

TRANSCRIPT:
{transcript}

EVALUATION SCORES:
{scores_summary}

Generate a detailed, encouraging, and actionable coaching report in Markdown.
You MUST include exactly these three H2 sections, in this order:

## ✅ Strengths
(Bullet list of 3-5 specific, evidence-based strengths observed in the answers.)

## ⚠️ Gaps & Areas to Improve
(Bullet list of 3-5 specific weaknesses with a brief explanation of why each matters for this role.)

## 🎯 3-Drill Practice Plan
(Numbered list of exactly 3 concrete, actionable drills the candidate should do this week.
Each drill must include: what to do, why it helps, and an estimated time e.g. "30 min/day".)

Be specific to the answers given. Do not be generic. Do not add any extra sections.
"""

        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"**Coach report generation failed:** {e}"

# --- Session State Initialization ---
if "question_bank" not in st.session_state:
    st.session_state.question_bank = None

if "current_question_index" not in st.session_state:
    st.session_state.current_question_index = 0
    st.session_state.interview_history = []
    st.session_state.probe_count = 0
    st.session_state.agent_turn = True
    st.session_state.agent_audio_bytes = None
    st.session_state.agent_latest_text = ""
    st.session_state.interview_complete = False
    st.session_state.evaluation_scores = []  # Part 4: silent score accumulator

# --- UI Routing ---
if st.session_state.question_bank is None:
    st.title("🤖 AI Interview Coach - Intake & Setup")
    st.markdown("Please provide your details below so the Intake Agent can configure your interview.")

    # --- Intake Form ---
    with st.form("intake_form"):
        target_role = st.text_input("Target Role", placeholder="e.g. Senior Frontend Engineer")
        short_bio = st.text_area("Short Bio", placeholder="e.g. 5 years of experience with React, TypeScript, and Node.js...")
        focus_area = st.selectbox("Focus Area", ["behavioral", "technical", "mixed"])
        
        submitted = st.form_submit_button("Submit & Generate Setup")

    if submitted:
        if not target_role or not short_bio:
            st.warning("Please fill in both the Target Role and Short Bio.")
        else:
            with st.spinner("Intake Agent is analyzing your profile..."):
                system_prompt = """
                You are an expert AI Interview Intake Agent. Your job is to analyze the user's target role, short bio, and requested focus area, and output a JSON object configuring the interview.
                
                You must respond ONLY with a valid JSON object matching this schema exactly. Do not include any markdown formatting, code blocks, or conversational text.
                
                {
                  "interview_category": "string (e.g. System Design, React Frontend, Behavioral Leadership, etc.)",
                  "difficulty_level": "string (e.g. Entry-Level, Mid-Level, Senior, Staff)",
                  "metadata": {
                    "role_keywords": ["string"],
                    "suggested_topics": ["string"]
                  }
                }
                """
                
                user_prompt = f"""
                Target Role: {target_role}
                Short Bio: {short_bio}
                Focus Area: {focus_area}
                """
                
                try:
                    chat_completion = client.chat.completions.create(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        model="llama-3.1-8b-instant",
                        temperature=0.2, 
                        response_format={"type": "json_object"}
                    )
                    
                    setup_data = json.loads(chat_completion.choices[0].message.content)
                    st.session_state.setup_data = setup_data
                    
                    # Part 2: Question Bank Generation
                    qb_system_prompt = """
                    You are an expert AI Interviewer. Based on the interview configuration provided, generate a bank of 5 interview questions.
                    Rank them by priority (1 being highest priority).
                    
                    You must respond ONLY with a valid JSON object matching this schema exactly:
                    {
                      "questions": [
                        {
                          "priority": "integer",
                          "question": "string",
                          "follow_up_probe": "string",
                          "evaluation_criteria": "string"
                        }
                      ]
                    }
                    """
                    
                    qb_user_prompt = f"""
                    Interview Configuration:
                    Category: {setup_data.get('interview_category')}
                    Difficulty: {setup_data.get('difficulty_level')}
                    Role Keywords: {setup_data.get('metadata', {}).get('role_keywords', [])}
                    Suggested Topics: {setup_data.get('metadata', {}).get('suggested_topics', [])}
                    """
                    
                    qb_chat_completion = client.chat.completions.create(
                        messages=[
                            {"role": "system", "content": qb_system_prompt},
                            {"role": "user", "content": qb_user_prompt}
                        ],
                        model="llama-3.1-8b-instant",
                        temperature=0.7,
                        response_format={"type": "json_object"}
                    )
                    
                    qb_data = json.loads(qb_chat_completion.choices[0].message.content)
                    
                    st.session_state.question_bank = qb_data.get("questions", [])
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"An error occurred: {e}")

else:
    # --- Part 3: Voice Interview Loop ---
    st.title("🎙️ AI Interview Session")
    
    # Check if interview is over
    if st.session_state.current_question_index >= len(st.session_state.question_bank) or st.session_state.interview_complete:

        # --- Inject CSS for results screen ---
        st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

        .report-banner {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            border-radius: 16px;
            padding: 36px 32px;
            margin-bottom: 28px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 8px 32px rgba(0,0,0,0.35);
        }
        .report-banner h1 {
            font-family: 'Inter', sans-serif;
            font-size: 2.4rem;
            font-weight: 800;
            background: linear-gradient(90deg, #a78bfa, #60a5fa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0 0 8px 0;
        }
        .report-banner p {
            font-family: 'Inter', sans-serif;
            color: rgba(255,255,255,0.6);
            font-size: 1rem;
            margin: 0;
        }
        .score-card {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.09);
            border-radius: 12px;
            padding: 18px 14px;
            text-align: center;
            backdrop-filter: blur(6px);
        }
        .score-label {
            font-family: 'Inter', sans-serif;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.45);
            margin-bottom: 6px;
        }
        .score-value {
            font-family: 'Inter', sans-serif;
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
        }
        .score-bar-bg {
            background: rgba(255,255,255,0.08);
            border-radius: 999px;
            height: 5px;
            margin-top: 10px;
            overflow: hidden;
        }
        .score-bar-fill {
            height: 5px;
            border-radius: 999px;
        }
        .report-section {
            background: rgba(255,255,255,0.025);
            border-radius: 14px;
            padding: 24px 28px;
            margin-bottom: 16px;
            border-left: 4px solid;
        }
        .report-strengths  { border-color: #4ade80; }
        .report-gaps       { border-color: #fb923c; }
        .report-drills     { border-color: #60a5fa; }
        .report-section h2 {
            font-family: 'Inter', sans-serif;
            font-size: 1.2rem;
            font-weight: 700;
            margin-top: 0;
        }
        </style>
        """, unsafe_allow_html=True)

        # --- Banner ---
        setup = st.session_state.get("setup_data", {})
        role_label = setup.get("interview_category", "Interview")
        difficulty_label = setup.get("difficulty_level", "")
        st.markdown(f"""
        <div class="report-banner">
            <h1>Interview Complete 🎉</h1>
            <p>{difficulty_label} · {role_label}</p>
        </div>
        """, unsafe_allow_html=True)

        # --- Score Dashboard ---
        scores_list = st.session_state.evaluation_scores
        if scores_list:
            st.markdown("### 📊 Performance Dashboard")
            dims = {
                "Clarity": "clarity",
                "Relevance": "relevance",
                "Technical": "technical_accuracy",
                "Communication": "communication_skills",
                "Completeness": "completeness",
            }
            dim_colors = ["#a78bfa", "#60a5fa", "#34d399", "#fb923c", "#f472b6"]
            cols = st.columns(len(dims))
            for col, (label, key), color in zip(cols, dims.items(), dim_colors):
                values = [ev["scores"].get(key, 0) for ev in scores_list if ev.get("scores")]
                avg = round(sum(values) / len(values), 1) if values else 0
                pct = int(avg * 10)
                col.markdown(f"""
                <div class="score-card">
                    <div class="score-label">{label}</div>
                    <div class="score-value" style="color:{color}">{avg}</div>
                    <div class="score-bar-bg">
                        <div class="score-bar-fill" style="width:{pct}%;background:{color}"></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            st.write("")

        # --- Generate Coach Report (once) ---
        if "coach_report" not in st.session_state:
            with st.spinner("🤖 Coach is reviewing your performance and writing your report..."):
                st.session_state.coach_report = generate_coach_report(
                    st.session_state.interview_history,
                    st.session_state.evaluation_scores,
                    st.session_state.get("setup_data", {})
                )

        # --- Render Report Sections ---
        st.markdown("### 📋 Coach Feedback Report")
        report_text = st.session_state.coach_report

        # Split into sections and apply styled containers
        import re
        section_patterns = [
            (r"## ✅ Strengths(.*?)(?=## |$)", "report-section report-strengths"),
            (r"## ⚠️ Gaps & Areas to Improve(.*?)(?=## |$)", "report-section report-gaps"),
            (r"## 🎯 3-Drill Practice Plan(.*?)(?=## |$)", "report-section report-drills"),
        ]
        section_titles = [
            "## ✅ Strengths",
            "## ⚠️ Gaps & Areas to Improve",
            "## 🎯 3-Drill Practice Plan",
        ]
        section_classes = ["report-section report-strengths", "report-section report-gaps", "report-section report-drills"]

        rendered_any = False
        for title, css_class in zip(section_titles, section_classes):
            pattern = re.compile(re.escape(title) + r"(.*?)(?=## |\Z)", re.DOTALL)
            match = pattern.search(report_text)
            if match:
                body = match.group(1).strip()
                st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
                st.markdown(f"{title}\n\n{body}")
                st.markdown('</div>', unsafe_allow_html=True)
                rendered_any = True

        if not rendered_any:
            # Fallback: render raw markdown
            st.markdown(report_text)

        # --- Expandable Transcript ---
        with st.expander("📄 Full Interview Transcript"):
            for msg in st.session_state.interview_history:
                speaker = "🤖 Interviewer" if msg["role"] == "assistant" else "🗣️ You"
                st.markdown(f"**{speaker}:** {msg['content']}")
                st.write("")

        st.write("")
        if st.button("🔄 Start New Interview", type="primary", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
        st.stop()
    
    current_q_data = st.session_state.question_bank[st.session_state.current_question_index]
    
    # If it is the agent's turn to speak
    if st.session_state.agent_turn:
        with st.spinner("Interviewer is thinking..."):
            if len(st.session_state.interview_history) == 0 or st.session_state.probe_count == 0:
                # Ask the base question
                agent_text = current_q_data.get('question')
                st.session_state.interview_history.append({"role": "assistant", "content": agent_text})
                st.session_state.agent_latest_text = agent_text
                st.session_state.agent_audio_bytes = text_to_speech(agent_text)
                st.session_state.agent_turn = False
                st.rerun()
            else:
                # Evaluate the user's last answer and decide whether to probe or move on
                eval_system_prompt = """
                You are an expert AI Interviewer. Analyze the user's latest response to your question.
                Decide whether to 'probe' (ask a follow-up question) or 'next' (move to the next topic).
                
                Respond ONLY with a valid JSON object matching this schema exactly:
                {
                  "action": "string (either 'probe' or 'next')",
                  "response_text": "string (what you will say to the user)"
                }
                """
                
                # Build context
                chat_context = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in st.session_state.interview_history[-3:]])
                
                eval_user_prompt = f"""
                Current Question Base: {current_q_data.get('question')}
                Evaluation Criteria: {current_q_data.get('evaluation_criteria')}
                Suggested Follow-up Probe: {current_q_data.get('follow_up_probe')}
                
                Recent Conversation:
                {chat_context}
                
                If the user answered well or doesn't know, choose 'next'.
                If the user's answer is lacking and you haven't probed much, choose 'probe'.
                """
                
                try:
                    eval_completion = client.chat.completions.create(
                        messages=[
                            {"role": "system", "content": eval_system_prompt},
                            {"role": "user", "content": eval_user_prompt}
                        ],
                        model="llama-3.1-8b-instant",
                        temperature=0.3,
                        response_format={"type": "json_object"}
                    )
                    
                    eval_data = json.loads(eval_completion.choices[0].message.content)
                    action = eval_data.get("action", "next")
                    agent_text = eval_data.get("response_text", "Let's move on.")
                    
                    st.session_state.interview_history.append({"role": "assistant", "content": agent_text})
                    st.session_state.agent_latest_text = agent_text
                    st.session_state.agent_audio_bytes = text_to_speech(agent_text)
                    
                    if action == "next" or st.session_state.probe_count >= 2:
                        st.session_state.current_question_index += 1
                        st.session_state.probe_count = 0
                    else:
                        st.session_state.probe_count += 1
                        
                    st.session_state.agent_turn = False
                    st.rerun()
                except Exception as e:
                    st.error(f"Evaluation error: {e}")
                    st.session_state.agent_turn = False
    
    # Display the conversation history
    for msg in st.session_state.interview_history:
        if msg["role"] == "assistant":
            st.chat_message("assistant").write(msg["content"])
        else:
            st.chat_message("user").write(msg["content"])
    
    # Play Agent Audio if available
    if st.session_state.agent_audio_bytes:
        st.audio(st.session_state.agent_audio_bytes, format="audio/wav", autoplay=True)
    
    # User Input Turn
    st.write("---")
    st.write("🎤 **Your Turn to Speak**")
    audio_bytes = audio_recorder(text="Click to record, click to stop", recording_color="#e84118", neutral_color="#6ab04c")
    
    if audio_bytes:
        # Guard: warn user if they clicked too briefly
        if len(audio_bytes) < _MIN_AUDIO_BYTES:
            st.toast("⚠️ Recording too short — hold the button while speaking, then click to stop.", icon="🎙️")
        else:
            with st.spinner("Transcribing your answer..."):
                user_text = transcribe_audio(audio_bytes)
            if user_text:
                st.session_state.interview_history.append({"role": "user", "content": user_text})
                
                # --- Part 4: Silent Background Evaluation ---
                score = evaluate_response(user_text, current_q_data)
                if score:
                    st.session_state.evaluation_scores.append({
                        "question_index": st.session_state.current_question_index,
                        "question": current_q_data.get("question"),
                        "user_answer": user_text,
                        "scores": score
                    })
                # --- End Evaluation ---
                
                st.session_state.agent_turn = True
                st.session_state.agent_audio_bytes = None  # clear previous audio
                st.rerun()
