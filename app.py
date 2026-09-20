import json
import re
import sqlite3
import time
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(
    page_title="AI Master GS Revision & Test Platform",
    page_icon="🎯",
    layout="wide",
)

# Database setup with UNIQUE constraint to prevent duplicates
DB_FILE = "master_question_bank.db"


def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT UNIQUE,
            options TEXT,
            correct TEXT,
            explanation TEXT,
            asked INTEGER DEFAULT 0
        )
    """)
  conn.commit()
  conn.close()


init_db()

st.title("🎯 AI Master GS Revision & Test Platform (Bulletproof Edition)")
st.markdown(
    "Apne notes (Text, PDF, Images ya Camera) se smart MCQs banayein. Data"
    " feeding ab bilkul aasan aur surakshit hai!"
)

# Sidebar for Input & Uploads
with st.sidebar:
  st.header("📂 Data Input & Settings")
  api_key = st.text_input("Google Gemini API Key darj karein", type="password")
  st.markdown(
      "[Free API Key yahan se prapt"
      " karein](https://aistudio.google.com/app/apikey)"
  )

  st.markdown("---")
  st.subheader("Data Dene ka Tarika:")
  upload_option = st.radio(
      "Select Mode:",
      (
          "Text Paste Karein",
          "PDF / Images Upload Karein",
          "Camera se Photo Khinchein",
      ),
  )

  notes_text = ""
  uploaded_files = None
  camera_file = None

  if upload_option == "Text Paste Karein":
    notes_text = st.text_area(
        "Apne GS ke notes yahan paste karein:",
        height=200,
        placeholder="Yahan apna text likhein...",
    )
  elif upload_option == "PDF / Images Upload Karein":
    uploaded_files = st.file_uploader(
        "PDF ya Photos (JPG/PNG) select karein",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )
  else:
    camera_file = st.camera_input("Apne notes ki live photo khinchein")

  st.info(
      "💡 Tip: Duplicate questions automatic filter ho jayenge. Chahe text ho"
      " ya file, sabhi ke liye seamless feeding!"
  )
  build_bank_btn = st.button("🚀 Question Bank mein Sawal Jodein")


# Function with robust auto-retry for 503 / 429 errors
def call_gemini_with_retry(client, model, contents, config, max_retries=5):
  delay = 2
  for attempt in range(max_retries):
    try:
      return client.models.generate_content(
          model=model, contents=contents, config=config
      )
    except Exception as e:
      error_str = str(e)
      if (
          "503" in error_str
          or "UNAVAILABLE" in error_str
          or "high demand" in error_str
          or "RESOURCE_EXHAUSTED" in error_str
      ):
        if attempt < max_retries - 1:
          time.sleep(delay)
          delay *= 2
          continue
      raise e


# Handle Question Bank Generation
if build_bank_btn:
  if not api_key:
    st.error("Kripya apni Gemini API Key darj karein!")
  elif upload_option == "Text Paste Karein" and not notes_text.strip():
    st.error("Kripya notes text darj karein!")
  elif (
      upload_option == "PDF / Images Upload Karein" and not uploaded_files
  ):
    st.error("Kripya kam se kam ek PDF ya Image file select karein!")
  elif upload_option == "Camera se Photo Khinchein" and camera_file is None:
    st.error("Kripya pehle camera se photo khinchein!")
  else:
    with st.spinner(
        "AI aapke data ko analyze kar raha hai aur questions feed kar raha"
        " hai..."
    ):
      try:
        client = genai.Client(api_key=api_key)

        prompt = f"""
                You are an expert GS exam creator and educator. Thoroughly analyze all the provided study notes, documents, text, images, or camera captures. 
                CRITICAL INSTRUCTIONS:
                1. Comprehensive Coverage: Extract every single fact, date, concept, heading, and data point. Convert them into high-quality multiple-choice questions (MCQs).
                2. Language Matching (Strict): Detect the language of the source input. If the source text/notes are in Hindi, generate all questions, options, correct answers, and explanations strictly in HINDI. If they are in English, generate everything strictly in ENGLISH.
                
                Return ONLY a valid JSON array in this exact format, with no extra text or markdown wrapping outside JSON:
                [
                  {{
                    "question": "Question text in source language?",
                    "options": ["Option A", "Option B", "Option C", "Option D"],
                    "correct": "Exact matching string of the correct option",
                    "explanation": "Detailed explanation in source language."
                  }}
                ]
                """

        generation_config = types.GenerateContentConfig(
            max_output_tokens=8192, temperature=0.2
        )

        response = None
        if upload_option == "Text Paste Karein":
          full_prompt = f"{prompt}\n\nNotes Text:\n{notes_text.strip()}"
          response = call_gemini_with_retry(
              client,
              "gemini-2.5-flash",
              full_prompt,
              config=generation_config,
          )
        elif upload_option == "PDF / Images Upload Karein":
          contents_list = []
          for file in uploaded_files:
            file_bytes = file.getvalue()
            mime_type = file.type
            contents_list.append(
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            )
          contents_list.append(prompt)
          response = call_gemini_with_retry(
              client, "gemini-2.5-flash", contents_list, config=generation_config
          )
        elif upload_option == "Camera se Photo Khinchein":
          cam_bytes = camera_file.getvalue()
          contents_list = [
              types.Part.from_bytes(data=cam_bytes, mime_type="image/jpeg"),
              prompt,
          ]
          response = call_gemini_with_retry(
              client, "gemini-2.5-flash", contents_list, config=generation_config
          )

        raw_text = response.text.strip()

        # Safe JSON extraction using regex to prevent formatting crashes
        match = re.search(r"\[\s*\{.*\}\s*\]", raw_text, re.DOTALL)
        if match:
          json_str = match.group(0)
        else:
          json_str = raw_text

        if json_str.startswith("```json"):
          json_str = json_str[7:]
        if json_str.endswith("```"):
          json_str = json_str[:-3]

        questions_list = json.loads(json_str.strip())

        # Save to SQLite Database using INSERT OR IGNORE (Anti-Duplicate)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        added_count = 0

        for q in questions_list:
          cursor.execute(
              """
                        INSERT OR IGNORE INTO questions (question, options, correct, explanation, asked)
                        VALUES (?, ?, ?, ?, 0)
                    """,
              (
                  q["question"],
                  json.dumps(q["options"]),
                  q["correct"],
                  q["explanation"],
              ),
          )
          if cursor.rowcount > 0:
            added_count += 1

        conn.commit()
        conn.close()
        st.success(
            f"Safaltapoorvak {added_count} naye unique prashn Question Bank mein"
            " jod diye gaye hain!"
        )
      except json.JSONDecodeError:
        st.error(
            "Error: AI response ka format parse karne mein dikkat aayi. Kripya"
            " dobara koshish karein."
        )
      except Exception as e:
        st.error(
            f"Error: {e}. (Server par load zyada ho sakta hai, kripya 1 minute"
            " baad dobara koshish karein.)"
        )

# Check Database stats
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM questions")
total_q = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM questions WHERE asked = 0")
unasked_q = cursor.fetchone()[0]
conn.close()

st.markdown("---")
col1, col2, col3 = st.columns(3)
col1.metric("Bank mein Kul Prashn", total_q)
col2.metric("Bache hue Naye Prashn", unasked_q)
col3.metric("Puche ja chuke Prashn", total_q - unasked_q)

# --- QUESTION BANK MANAGEMENT SECTION ---
st.markdown("---")
with st.expander(
    "📋 Question Bank Management (Sawal Dekhein, Ek-Ek Delete Karein ya Poora"
    " Bank Reset Karein)",
    expanded=False,
):
  st.subheader("Feed kiye gaye sabhi Questions ki List")
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("SELECT id, question, correct, explanation FROM questions")
  all_questions = cursor.fetchall()
  conn.close()

  if not all_questions:
    st.info("Question Bank abhi khali hai.")
  else:
    st.write(f"Kul Saved Prashn: {len(all_questions)}")

    for idx, (q_id, q_text, q_correct, q_exp) in enumerate(all_questions, 1):
      cols = st.columns([0.85, 0.15])
      with cols[0]:
        st.markdown(
            f"**{idx}. (ID: {q_id}) {q_text}**\n\n*Sahi Uttar:*"
            f" `{q_correct}`\n\n*Spashtikaran:* {q_exp}"
        )
      with cols[1]:
        if st.button("Delete", key=f"del_q_{q_id}"):
          conn = sqlite3.connect(DB_FILE)
          cursor = conn.cursor()
          cursor.execute("DELETE FROM questions WHERE id = ?", (q_id,))
          conn.commit()
          conn.close()
          st.success(f"Prashn ID {q_id} safaltapoorvak delete kar diya gaya!")
          st.rerun()
      st.markdown("---")

    # Clear All Button
    if st.button(
        "⚠️ Sabhi Questions Ek Saath Delete Karein (Reset Bank)",
        type="primary",
    ):
      conn = sqlite3.connect(DB_FILE)
      cursor = conn.cursor()
      cursor.execute("DELETE FROM questions")
      conn.commit()
      conn.close()
      st.warning("Question Bank poori tarah clear kar diya gaya hai!")
      st.rerun()

# --- DAILY MOCK TEST SECTION ---
st.markdown("---")
st.subheader("📝 Miscellaneous Mock Test (No-Repeat Mode)")
test_size = st.slider(
    "Aaj ke test mein kitne prashn chahiye?", 5, 100, 25, step=5
)
start_test_btn = st.button("▶️ Test Shuru Karein")

if "current_test" not in st.session_state:
  st.session_state.current_test = None
if "test_submitted" not in st.session_state:
  st.session_state.test_submitted = False
if "final_answers" not in st.session_state:
  st.session_state.final_answers = {}

if start_test_btn:
  if total_q == 0:
    st.warning("Pehle sidebar se data dekar Question Bank mein sawal jodein!")
  else:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, question, options, correct, explanation FROM questions WHERE"
        " asked = 0 ORDER BY RANDOM() LIMIT ?",
        (test_size,),
    )
    rows = cursor.fetchall()

    if len(rows) < test_size:
      cursor.execute("UPDATE questions SET asked = 0")
      conn.commit()
      cursor.execute(
          "SELECT id, question, options, correct, explanation FROM questions"
          " ORDER BY RANDOM() LIMIT ?",
          (test_size,),
      )
      rows = cursor.fetchall()

    conn.close()

    if not rows:
      st.error("Koi prashn uplabdh nahi hai!")
    else:
      test_data = []
      for r in rows:
        test_data.append({
            "id": r[0],
            "question": r[1],
            "options": json.loads(r[2]),
            "correct": r[3],
            "explanation": r[4],
        })
      st.session_state.current_test = test_data
      st.session_state.test_submitted = False
      st.session_state.final_answers = {}
      st.success(f"Aaj ka {len(test_data)} prashnon ka test taiyar hai!")

# Render Test Form
if st.session_state.current_test:
  test_q = st.session_state.current_test
  st.markdown(f"### Mock Test (Total Questions: {len(test_q)})")

  with st.form("master_test_form"):
    for i, q in enumerate(test_q):
      st.markdown(f"**Prashn {i+1}: {q['question']}**")

      default_idx = None
      if st.session_state.test_submitted:
        prev_ans = st.session_state.final_answers.get(q["id"])
        if prev_ans in q["options"]:
          default_idx = q["options"].index(prev_ans)

      ans = st.radio(
          f"Vikalp chunen Q{i+1}",
          q["options"],
          key=f"master_q_{q['id']}",
          index=default_idx,
          disabled=st.session_state.test_submitted,
      )
      st.markdown("---")

    submit_btn = (
        False
        if st.session_state.test_submitted
        else st.form_submit_button("📥 Test Submit Karein")
    )

    if submit_btn:
      st.session_state.test_submitted = True
      ans_dict = {}
      for q in test_q:
        ans_dict[q["id"]] = st.session_state.get(f"master_q_{q['id']}")
      st.session_state.final_answers = ans_dict

      conn = sqlite3.connect(DB_FILE)
      cursor = conn.cursor()
      for q in test_q:
        cursor.execute("UPDATE questions SET asked = 1 WHERE id = ?", (q["id"],))
      conn.commit()
      conn.close()
      st.rerun()

# Show Results and Solutions
if st.session_state.test_submitted and st.session_state.current_test:
  test_q = st.session_state.current_test
  score = 0
  total = len(test_q)

  st.markdown("---")
  st.header("📊 Test Parinam aur Solution (Results & Explanations)")

  for i, q in enumerate(test_q):
    user_ans = st.session_state.final_answers.get(q["id"])
    correct_ans = q["correct"]

    if user_ans == correct_ans:
      score += 1
      st.success(
          f"**Prashn {i+1}: Sahi!**\n\nAapka uttar: `{user_ans}`\n\n**Spashtikaran:"
          f"** {q['explanation']}"
      )
    elif user_ans is None:
      st.warning(
          f"**Prashn {i+1}: Aapne uttar nahi diya.**\n\nSahi uttar:"
          f" `{correct_ans}`\n\n**Spashtikaran:** {q['explanation']}"
      )
    else:
      st.error(
          f"**Prashn {i+1}: Galat!**\n\nAapka uttar: `{user_ans}` | Sahi uttar:"
          f" `{correct_ans}`\n\n**Spashtikaran:** {q['explanation']}"
      )

  st.markdown("---")
  st.markdown("### 🏆 Aapka Kul Score")
  st.metric(label="Score", value=f"{score} / {total}")

  if st.button("🔄 Naya Test Shuru Karein (Reset Test)"):
    st.session_state.current_test = None
    st.session_state.test_submitted = False
    st.session_state.final_answers = {}
    st.rerun()
