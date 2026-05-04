# AI Mock Interview Coach

An AI-first prototype for running realistic mock interviews. It uses a multi-agent architecture to dynamically adapt to a candidate's background, ask relevant questions, probe deeper into weak answers, and provide highly specific, actionable coaching feedback at the end.

## 🚀 Setup and Run Instructions

1. **Install Dependencies**
   Make sure you have Python 3.9+ installed.
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Variables**
   Create a `.env` file in the root directory and add your Groq API key:
   ```env
   GROQ_API_KEY=your_groq_api_key_here
   ```

3. **Voice Configuration**
   The app now supports three levels of voice generation:
   - **Integrated Browser Voice (Default)**: Works out of the box with no setup.
   - **ElevenLabs (Cloud)**: Add an `ELEVENLABS_API_KEY` to your `.env` for premium high-quality voices.
   - **Local XTTS (Optional)**: If you prefer a local server, start it at `http://localhost:5000`.

4. **Start the API Server**
   ```bash
   python main.py
   ```
   Navigate to `http://localhost:8000` in your browser.


---

## 🏗 Architecture Overview

The system is powered by **5 distinct AI agents**, decoupled from each other to ensure role clarity and independent prompt engineering. They are orchestrated by a FastAPI backend.

1. **Intake Agent**: Analyzes the user's initial form (Role, Background, Focus) and structures it into a JSON metadata payload defining the core theme of the interview.
2. **Question Bank Agent**: Reads the intake JSON and generates a ranked list of 7 tailored questions. Crucially, it attaches *expected evaluation criteria* and *follow-up probes* to each question, setting up the Interviewer and Evaluator for success.
3. **Interviewer Agent**: The "actor". Takes the current question, the chat history, and the candidate's last answer, and decides whether to *probe* (if the answer was vague or missed criteria) or *move on* to the next question.
4. **Silent Evaluator Agent**: Runs asynchronously in the background. It scores each response on 5 dimensions (Clarity, Relevance, Technical Accuracy, Communication, Completeness) by cross-referencing the candidate's answer with the Question Bank Agent's criteria.
5. **Coach Agent**: The final step. Uses the massive `llama3-70b-8192` model. It reads the raw transcript and the Silent Evaluator's scores to output a brutally honest, evidence-based Markdown report containing Strengths, Gaps, and a 3-Drill Practice Plan.

---

## 💡 Key Design Decisions & Tradeoffs

- **Separation of Interviewing and Evaluating**: In early tests, asking one agent to act as the interviewer *and* privately score the candidate caused latency spikes and "character breaks" where the interviewer sounded like a robot. Splitting them allowed the Interviewer (`llama-3.1-8b-instant`) to respond incredibly fast, while the Evaluator takes its time in the background.
- **FastAPI + Vanilla JS SPA**: We migrated away from Streamlit to a lightweight FastAPI + Vanilla JS stack. This allowed us to build custom browser microphone integrations and implement fluid UI elements (Live Scores, Audio Waveforms) that Streamlit couldn't support efficiently.
- **Model Selection**: We use the blazing fast `llama-3.1-8b` for conversational turns (Intake, Interviewer) to ensure sub-second latency for voice interactions. We use the much smarter `llama3-70b-8192` for the final Coaching Report to ensure strict factual grounding and eliminate generic "hallucinated" feedback.
- **Prompts in File System**: All agent prompts are stored as clean `.txt` files in the `prompts/` directory to make iteration and version control much easier than managing massive string literals in code.

---

## 📄 Example Transcripts

Here are examples of how the system handles different types of candidates.

### Transcript 1: The Strong Candidate
**Role**: Senior React Engineer
**Interviewer**: Tell me about a time you had to optimize the performance of a complex React application.
**Candidate**: In my last role, our main dashboard was taking 4 seconds to render because we were fetching 50MB of raw data on mount and causing massive re-renders across 20 child components. I implemented `useMemo` for the heavy data aggregations, switched to React Query for caching, and virtualized the massive data tables using `react-window`. Render time dropped to under 300ms.
**Interviewer**: That's a massive improvement. What trade-offs did you face when implementing windowing for the data tables?
*System Decision: Action = "probe". The answer was strong, but the interviewer probed for technical depth since this is a Senior role.*
**Candidate**: The main trade-off was breaking native browser search (Cmd+F), since the DOM nodes were no longer there. We had to build a custom search overlay that queried the raw data array instead.
**Interviewer**: Great point. Let's move on to the next question...
*System Decision: Action = "next". Candidate clearly demonstrated mastery. No further probes needed.*

### Transcript 2: The Weak / Vague Candidate
**Role**: Product Manager
**Interviewer**: Can you describe a time when you had to prioritize features with competing stakeholder demands?
**Candidate**: Yeah, that happens a lot. I usually just talk to the engineers and figure out what's easiest to build first. Then I tell marketing we'll get to their stuff later.
**Interviewer**: I understand that communication is key. However, how exactly do you evaluate the *business value* of the competing features before deciding which one is easiest to build?
*System Decision: Action = "probe". Candidate failed the evaluation criteria of "demonstrating a structured framework for prioritization".*
**Candidate**: I mean, I guess I just look at what the CEO wants. If they want it, we do it.
**Interviewer**: Let's pivot slightly. Once the CEO decides on a feature, how do you measure its success after launch?
*System Decision: Action = "next". Probe count reached 1. The candidate failed to provide a framework, so the system cuts its losses and moves on rather than getting stuck in a loop.*

### Transcript 3: The Edge Case (Off-Topic)
**Role**: Data Analyst
**Interviewer**: Tell me about a complex SQL query you wrote to solve a business problem.
**Candidate**: Honestly, I hate writing SQL. I usually just ask ChatGPT to write it for me and then I copy-paste it into Snowflake. I prefer using Python pandas anyway.
**Interviewer**: It's true that tools like ChatGPT are helpful, and pandas is powerful. But as a Data Analyst, you will occasionally need to understand and debug the SQL directly. How would you approach debugging a slow-running query that ChatGPT generated for you?
*System Decision: Action = "probe". System gracefully handles the curveball by acknowledging the candidate's pivot, but redirects them back to the core competency required for the role.*
