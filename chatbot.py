"""
chatbot.py – AI Workout & Nutrition Coach powered by OpenAI API & Local Fitness Dataset.

Features:
  - Injects live user workout dataset (reps, speed, calories, streaks, body metrics)
  - References comprehensive exercises dataset (METs, form cues, routines)
  - Calls OpenAI API (gpt-4o-mini) using provided/env API key
  - Includes robust offline dataset fallback engine if API key is not yet set
"""

import json
import os
from datetime import datetime
from dotenv import load_dotenv

# Automatically load environment variables from .env
load_dotenv()

DATASET_PATH = os.path.join(os.path.dirname(__file__), "data", "fitness_dataset.json")

_DATASET_CACHE = None


def load_dataset():
    """Loads and caches the fitness knowledge dataset."""
    global _DATASET_CACHE
    if _DATASET_CACHE is None:
        if os.path.exists(DATASET_PATH):
            try:
                with open(DATASET_PATH, "r", encoding="utf-8") as f:
                    _DATASET_CACHE = json.load(f)
            except Exception as e:
                print(f"[chatbot] Error reading dataset file: {e}")
                _DATASET_CACHE = {}
        else:
            _DATASET_CACHE = {}
    return _DATASET_CACHE


def format_user_dataset_context(user):
    """Summarizes user's personal profile and workout sessions for LLM injection."""
    if not user:
        return "No user profile data available."

    username = user.get("username", "Athlete")
    age = user.get("age", "N/A")
    height = user.get("height", "N/A")
    weight = user.get("weight", "N/A")
    blood_group = user.get("blood_group", "N/A")
    badges = user.get("badges", [])
    sessions = user.get("sessions", [])

    # BMI calculation
    bmi_str = "N/A"
    try:
        h_m = float(height) / 100.0
        w_kg = float(weight)
        if h_m > 0 and w_kg > 0:
            bmi = round(w_kg / (h_m * h_m), 1)
            bmi_str = f"{bmi}"
    except (ValueError, TypeError):
        pass

    # Sessions aggregation
    total_sessions = len(sessions)
    total_reps = sum(s.get("count", 0) for s in sessions)
    total_time_secs = sum(s.get("total_time", 0) for s in sessions)
    total_calories = round(sum(s.get("calories", 0) for s in sessions), 1)

    recent_sessions_summary = []
    for s in sessions[-5:]:  # Last 5 workouts
        recent_sessions_summary.append(
            f"- {s.get('date', 'Recent')}: {s.get('exercise', 'Workout')} | "
            f"{s.get('count', 0)} reps | {round(s.get('total_time', 0), 1)}s | "
            f"Avg Speed: {round(s.get('average_speed', 0), 2)}s/rep | "
            f"Burned: {round(s.get('calories', 0), 1)} kcal"
        )
    recent_text = "\n".join(recent_sessions_summary) if recent_sessions_summary else "No workouts logged yet."

    context = f"""
--- USER PERSONAL DATASET ---
Username: {username}
Age: {age} | Height: {height} cm | Weight: {weight} kg | BMI: {bmi_str}
Blood Group: {blood_group}
Badges Unlocked: {', '.join(badges) if badges else 'None yet'}
Total Workouts Logged: {total_sessions}
Total Cumulative Reps: {total_reps}
Total Active Time: {round(total_time_secs / 60, 1)} minutes
Total Calories Burned: {total_calories} kcal

Recent 5 Workout Logs:
{recent_text}
----------------------------
"""
    return context.strip()


def _offline_dataset_fallback(user_query, user_context, dataset):
    """
    Intelligent local dataset engine when OpenAI API key is unavailable or quota is exceeded.
    Extracts answers directly from the dataset and user metrics.
    """
    q = user_query.lower()
    exercises = dataset.get("exercises", {})
    programs = dataset.get("training_programs", {})
    nutrition = dataset.get("nutrition_guidelines", {})

    # Check for specific exercise query
    for key, ex in exercises.items():
        if key.replace("_", " ") in q or key in q or ex["name"].lower() in q:
            cues = "\n".join([f"• {c}" for c in ex.get("form_cues", [])])
            mistakes = "\n".join([f"• {m}" for m in ex.get("common_mistakes", [])])
            return (
                f"### 🏋️ **{ex['name']} Exercise Profile**\n\n"
                f"**Category:** {ex.get('category')} | **MET Burn Rate:** {ex.get('met')}\n"
                f"**Target Muscles:** {', '.join(ex.get('target_muscles', []))}\n"
                f"**Recommended Tempo:** {ex.get('optimal_speed_range')}\n\n"
                f"#### **Key Form Cues:**\n{cues}\n\n"
                f"#### **Common Mistakes to Avoid:**\n{mistakes}\n\n"
                f"💡 *Tip: Keep logging your reps to watch your pace improve on the progress charts!*"
            )

    # Check for calorie / progress / workout summary
    if any(k in q for k in ["calorie", "kcal", "how much", "how many", "burn", "stat", "progress", "summary", "my workout", "record"]):
        return (
            f"### 📊 **Your Current Workout Analysis**\n\n"
            f"Here is what your profile dataset shows:\n\n"
            f"{user_context}\n\n"
            f"**Coach Tip:** Consistency is key! Aim to maintain your streak and progressively increase reps or decrease rest time between sets."
        )

    # Check for workout plan / routine
    if any(k in q for k in ["routine", "program", "plan", "recommend", "today", "schedule", "split", "beginner"]):
        prog = programs.get("beginner_fat_loss", {})
        routine_lines = "\n".join([f"1. **{item['exercise'].replace('_', ' ').title()}**: {item['target']} (Rest: {item['rest']})" for item in prog.get("routine", [])])
        return (
            f"### ⚡ **Recommended Workout Routine: {prog.get('name')}**\n\n"
            f"**Frequency:** {prog.get('weekly_frequency')}\n\n"
            f"{routine_lines}\n\n"
            f"Ready to begin? Head over to **Start Workout** in the sidebar to track reps with real-time AI camera feedback!"
        )

    # Check for diet / nutrition / food
    if any(k in q for k in ["diet", "food", "protein", "eat", "nutrition", "weight loss", "fat loss", "muscle gain"]):
        loss = nutrition.get("fat_loss", {})
        gain = nutrition.get("muscle_gain", {})
        return (
            f"### 🥗 **Nutrition & Macronutrient Guide**\n\n"
            f"**For Fat Loss:**\n"
            f"• **Calories:** {loss.get('calorie_target')}\n"
            f"• **Protein:** {loss.get('protein_intake')}\n"
            f"• **Hydration:** {loss.get('hydration')}\n\n"
            f"**For Muscle Gain:**\n"
            f"• **Calories:** {gain.get('calorie_target')}\n"
            f"• **Protein:** {gain.get('protein_intake')}\n\n"
            f"Focus on whole food sources: eggs, chicken, lentils, oats, Greek yogurt, and green vegetables."
        )

    # General greeting or generic question
    return (
        f"Hello! I'm **FitBot**, your personal fitness advisor. 🦾\n\n"
        f"I have direct access to your personal workout history and our exercise database. You can ask me:\n"
        f"- *'Analyze my recent workouts and calories'* 📈\n"
        f"- *'How can I improve my squat form and speed?'* 🏋️\n"
        f"- *'Suggest a 4-day workout routine for muscle building'* ⚡\n"
        f"- *'What is my recommended daily protein intake?'* 🥗\n\n"
        f"*(Note: You can connect your OpenAI API key anytime in Chat Settings for open-ended conversation!)*"
    )


def ask_fitness_coach(user_query, user, chat_history=None, api_key=None):
    """
    Main entry point for generating AI coach responses.
    Uses OpenAI API when key is available; falls back gracefully to local dataset.
    """
    dataset = load_dataset()
    user_context = format_user_dataset_context(user)
    key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()

    # Filter out empty or placeholder keys
    is_placeholder = not key or key.strip().lower() in ("", "sk-...", "none", "")

    if is_placeholder:
        reply = _offline_dataset_fallback(user_query, user_context, dataset)
        return {
            "success": True,
            "reply": reply,
            "source": "local_dataset",
            "model": "FitTrack Local Dataset Engine",
            "needs_api_key": True,
            "notice": "Using local fitness dataset. Add your real OpenAI API key to .env for GPT-4o reasoning!"
        }

    # Attempt OpenAI Completion
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key.strip())
        model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

        exercises_summary = json.dumps(dataset.get("exercises", {}), indent=2)

        system_prompt = f"""You are FitBot, an elite, motivational, and highly knowledgeable AI Fitness Coach and Nutrition Advisor embedded inside the FitTrack web application.

You have direct access to:
1. The user's active workout logs and personal dataset.
2. The comprehensive exercise knowledge dataset (MET burn rates, form cues, mistakes, tempo).

=== USER FITNESS DATASET ===
{user_context}

=== EXERCISE KNOWLEDGE DATASET ===
{exercises_summary}

GUIDELINES:
- Greet warmly and refer to the user's specific data (e.g. mention their logged reps, workouts, weight, calories, or streaks when relevant).
- Provide structured, practical, actionable fitness advice with formatting (bullet points, bold text).
- Emphasize proper exercise biomechanics, safety, and progressive overload.
- Keep responses engaging, encouraging, and under 250 words unless the user asks for a comprehensive routine.
"""

        messages = [{"role": "system", "content": system_prompt}]

        # Add recent conversation history (up to last 6 messages)
        if chat_history and isinstance(chat_history, list):
            for msg in chat_history[-6:]:
                role = "assistant" if msg.get("role") == "bot" or msg.get("role") == "assistant" else "user"
                content = str(msg.get("content") or msg.get("text", "")).strip()
                if content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_query})

        # OpenAI API call (gpt-4o-mini or configured model)
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=600,
            temperature=0.7,
        )

        reply_text = response.choices[0].message.content

        return {
            "success": True,
            "reply": reply_text,
            "source": "openai",
            "model": model_name,
            "needs_api_key": False
        }

    except Exception as e:
        error_msg = str(e)
        print(f"[chatbot] OpenAI API call error: {error_msg}")
        # Graceful fallback to local dataset engine with explanation
        fallback_reply = _offline_dataset_fallback(user_query, user_context, dataset)
        return {
            "success": True,
            "reply": fallback_reply,
            "source": "local_dataset_fallback",
            "model": "FitTrack Local Dataset Engine (Fallback)",
            "needs_api_key": True,
            "notice": f"OpenAI Notice: {error_msg}. Displaying answer from local dataset."
        }
