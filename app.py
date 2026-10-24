import io
import os
import time
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import cv2
import mediapipe as mp
import numpy as np
import pandas as pd
from flask import (Flask, Response, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db_layer import get_db
# pyrefly: ignore[missing-import]
from features import (check_posture, compute_streaks, estimate_calories,
                      init_features, speed_recommendation)

app = Flask(__name__)
# Set SECRET_KEY in your environment for production.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# ------------------------------------------------------------ Database layer
# Automatically uses Firebase if firebase.json is present, else SQLite offline.
db = get_db()
print(f"[app] Running in {'Firebase' if db.mode == 'firebase' else 'SQLite (offline)'} mode")

init_features(app, db)  # registers progress, measurements, reminders, PDF, filters

# --------------------------------------------------------------- MediaPipe
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils

# -------------------------------------------------------------- Exercises
EXERCISES = {
    "knee_raises": {
        "name": "Knee Raises",
        "description": "Lift your knees to strengthen core and legs",
        "joints": ["LEFT_HIP", "LEFT_KNEE", "LEFT_ANKLE"],
        "target_angle": 80,
        "threshold": 25,
        "optimal_speed_range": (1.0, 2.0),   # seconds per rep
        "image": "3_highkneeswarmup_1x1_v2 (1).png",
    },
    "squats": {
        "name": "Squats",
        "description": "Lower your body to strengthen legs and glutes",
        "joints": ["LEFT_HIP", "LEFT_KNEE", "LEFT_ANKLE"],
        "target_angle": 90,
        "threshold": 15,
        "optimal_speed_range": (1.5, 3.0),
        "image": "squats.png",
    },
    "pushups": {
        "name": "Pushups",
        "description": "Upper body strength exercise",
        "joints": ["LEFT_SHOULDER", "LEFT_ELBOW", "LEFT_WRIST"],
        "target_angle": 90,
        "threshold": 15,
        "optimal_speed_range": (1.5, 3.0),
        "image": "pushups.png",
    },
}
MANUAL_EXERCISES = ["knee_raises", "squats", "pushups", "lunges", "plank", "sit_ups",
                    "jumping_jacks", "bicep_curls", "shoulder_press"]


def label_for(key):
    return key.replace("_", " ").title()


# ----------------------------------------------- Global (single-user) state
# NOTE: shared by all users. Fine for a single-user kiosk/demo only.
count = 0
target_count = 0
position = None
exercise_started = False
is_paused = False
feedback_message = "Begin Exercise!"
start_time = None
last_rep_time = None
exercise = None
paused_time = 0
pause_start = None
rep_speeds = []
session_saved = False


def reset_state():
    global count, target_count, position, exercise_started, is_paused
    global feedback_message, start_time, last_rep_time, exercise
    global paused_time, pause_start, rep_speeds, session_saved
    count = 0
    target_count = 0
    position = None
    exercise_started = False
    is_paused = False
    feedback_message = "Begin Exercise!"
    start_time = None
    last_rep_time = None
    exercise = None
    paused_time = 0
    pause_start = None
    rep_speeds = []
    session_saved = False


# ------------------------------------------------------------------ Helpers
def calculate_angle(a, b, c):
    a = np.array([a.x, a.y] if hasattr(a, "x") else a)
    b = np.array([b.x, b.y] if hasattr(b, "x") else b)
    c = np.array([c.x, c.y] if hasattr(c, "x") else c)
    ba, bc = a - b, c - b
    denom = np.linalg.norm(ba) * np.linalg.norm(bc)
    if denom == 0:
        return 0.0
    cosine = np.dot(ba, bc) / denom
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def user_weight(user):
    try:
        w = float(user.get("weight", 70))
        return w if w > 0 else 70.0
    except (TypeError, ValueError):
        return 70.0


def build_session(exercise_name, reps, total_time, avg_seconds_per_rep, weight, date=None):
    cal = estimate_calories(exercise_name, total_time, weight, reps)
    return {
        "date": date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": reps,
        "total_time": total_time,
        "average_speed": avg_seconds_per_rep,          # always seconds per rep
        "reps_per_min": round(60 / avg_seconds_per_rep, 1) if avg_seconds_per_rep else 0,
        "calories": cal["kcal"],                        # estimate
        "exercise": exercise_name,
    }


def evaluate_badges(email):
    """Recompute badges from ALL saved sessions (no double counting)."""
    user = db.get_user(email)
    if not user:
        return
    sessions = user.get("sessions", [])
    badges = list(user.get("badges", []))
    total_reps = sum(s.get("count", 0) for s in sessions)
    total_cal = sum(s.get("calories", 0) for s in sessions)
    longest = compute_streaks(sessions)["longest"]

    checks = {
        "Consistent": len(sessions) >= 5,
        "Century": total_reps >= 100,
        "Speedster": any(s.get("total_time", 999) < 30 and s.get("count", 0) >= 10 for s in sessions),
        "Calorie Burner": total_cal >= 500,
        "Marathoner": any(s.get("count", 0) >= 50 for s in sessions),
        "7-Day Streak": longest >= 7,
    }
    for name, ok in checks.items():
        if ok and name not in badges:
            badges.append(name)
    db.update_badges(email, badges)


def store_session(email, session_data):
    db.append_session(email, session_data)
    evaluate_badges(email)


def finalize_live_session(total_time):
    """Save the current live workout once."""
    global session_saved
    if session_saved or "email" not in session or not exercise or count == 0:
        return False
    email = session["email"]
    user = db.get_user(email)
    avg = sum(rep_speeds) / len(rep_speeds) if rep_speeds else 0
    store_session(email, build_session(exercise["name"], count, total_time, avg, user_weight(user or {})))
    session_saved = True
    return True


def admin_required_json(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "email" not in session or not session.get("is_admin"):
            return jsonify({"success": False, "error": "Unauthorized access"}), 403
        return fn(*a, **kw)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "email" not in session:
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


# ------------------------------------------------------------ Video frames
def put(image, text, y, color):
    cv2.putText(image, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)


def generate_frames():
    global count, position, exercise_started, feedback_message, start_time, last_rep_time
    cap = cv2.VideoCapture(0)
    try:
        with mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5) as pose:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    print("Failed to capture frame")
                    break
                image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = pose.process(image)
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

                if is_paused:
                    cv2.putText(image, "PAUSED", (image.shape[1] // 2 - 50, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                elif results.pose_landmarks and exercise_started and exercise and target_count > 0:
                    landmarks = results.pose_landmarks.landmark
                    try:
                        joints = [getattr(mp_pose.PoseLandmark, j).value for j in exercise["joints"]]
                        if not all(landmarks[j].visibility > 0.5 for j in joints):
                            feedback_message = "Ensure all joints are visible"
                            put(image, feedback_message, 120, (0, 0, 255))
                        else:
                            p1, p2, p3 = (landmarks[j] for j in joints)
                            angle = calculate_angle([p1.x, p1.y], [p2.x, p2.y], [p3.x, p3.y])
                            now = time.time()

                            if angle > exercise["target_angle"] + exercise["threshold"]:
                                position = "up"
                                feedback_message = "Good! Now return to start position"
                            elif angle < exercise["target_angle"] - exercise["threshold"]:
                                if position == "up":
                                    count += 1
                                    position = "down"
                                    if count == 1 and not start_time:
                                        start_time = now
                                    if last_rep_time:
                                        rep_time = now - last_rep_time
                                        rep_speeds.append(rep_time)
                                        lo, hi = exercise["optimal_speed_range"]
                                        if rep_time < lo:
                                            feedback_message = "Too fast! Slow down."
                                        elif rep_time > hi:
                                            feedback_message = "Too slow! Speed up."
                                        else:
                                            feedback_message = "Good pace! Keep going."
                                    last_rep_time = now

                            # AI posture correction overrides generic feedback
                            key = exercise["name"].lower().replace(" ", "_")
                            tip = check_posture(key, landmarks, mp_pose)
                            if tip:
                                feedback_message = tip

                            if count >= target_count > 0:
                                exercise_started = False
                                total_time = (now - start_time - paused_time) if start_time else 0
                                feedback_message = f"Done! Time: {total_time:.1f}s"
                                finalize_live_session(total_time)

                            put(image, f'Exercise: {exercise["name"]}', 30, (255, 255, 255))
                            put(image, f"Count: {count}/{target_count}", 60, (0, 255, 0))
                            put(image, f"Angle: {angle:.1f}", 90, (0, 255, 255))
                            put(image, feedback_message, 120, (0, 0, 255))
                        mp_drawing.draw_landmarks(image, results.pose_landmarks, mp_pose.POSE_CONNECTIONS)
                    except Exception as e:
                        print(f"Error processing pose: {e}")
                        feedback_message = "Adjust your position to be visible"
                        put(image, feedback_message, 120, (0, 0, 255))
                else:
                    if not results.pose_landmarks:
                        feedback_message = "Adjust your position to be visible"
                    put(image, feedback_message, 120, (0, 0, 255))

                ok, buffer = cv2.imencode(".jpg", image)
                if ok:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
    finally:
        cap.release()


# ------------------------------------------------------------------- Auth
@app.route("/")
def index():
    return redirect(url_for("profile" if "email" in session else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        try:
            db.firebase_get_user_by_email(email)   # validates existence (no-op in SQLite offline)
            data = db.get_user(email)
            ok = False
            if data:
                if "password_hash" in data:
                    ok = check_password_hash(data["password_hash"], password)
                elif data.get("password") == password:       # legacy plaintext -> upgrade
                    ok = True
                    db.update_user(email, {"password_hash": generate_password_hash(password)})
                    db.delete_field(email, "password")
            if ok:
                session["email"] = email
                session["username"] = data["username"]
                session["is_admin"] = bool(data.get("is_admin", False))
                return redirect(url_for("profile"))
        except Exception:
            pass
        return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    reset_state()
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        try:
            # NOTE: this only generates a link; you must email it yourself
            # (SMTP/SendGrid) or use the Firebase client SDK sendPasswordResetEmail.
            db.firebase_generate_reset_link(email)
            return render_template("forgot_password.html", message="If that email exists, a reset link was sent")
        except NotImplementedError:
            # SQLite offline mode – no email support
            return render_template("forgot_password.html",
                                   message="Password reset is not available in offline mode")
        except Exception:
            return render_template("forgot_password.html", message="If that email exists, a reset link was sent")
    return render_template("forgot_password.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        email = f["email"].strip().lower()
        if db.user_exists(email):
            return render_template("register.html", error="Email already registered")
        try:
            db.firebase_create_user(email, f["password"])  # no-op in SQLite mode
            db.set_user(email, {
                "username": f["username"],
                "email": email,
                "password_hash": generate_password_hash(f["password"]),
                "age": f["age"],
                "height": f["height"],
                "weight": f["weight"],
                "blood_group": f["blood_group"],
                "sessions": [],
                "badges": [],
                "is_admin": False,
                "daily_goal_reps": 20,
                "weekly_goal_reps": 150,
            })
            session["email"] = email
            session["username"] = f["username"]
            session["is_admin"] = False
            return redirect(url_for("profile"))
        except Exception as e:
            print(f"Registration error: {e}")
            return render_template("register.html", error="Registration failed. Please try again")
    return render_template("register.html")


# ---------------------------------------------------------------- Profile
@app.route("/profile")
@login_required
def profile():
    user = db.get_user(session["email"])
    if not user:
        return redirect(url_for("login"))
    sessions = user.get("sessions", [])
    return render_template(
        "profile.html",
        user=user,
        sessions=sessions,
        session_dates=[s["date"] for s in sessions],
        session_counts=[s["count"] for s in sessions],
        session_total_times=[s["total_time"] for s in sessions],
        session_average_speeds=[s.get("average_speed", 0) for s in sessions],
        session_calories=[s.get("calories", 0) for s in sessions],
        session_exercises=[s.get("exercise", "Unknown") for s in sessions],
    )


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session.get("username"),
                           exercises=[label_for(k) for k in MANUAL_EXERCISES])


@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    email = session["email"]
    if request.method == "POST":
        f = request.form
        db.update_user(email, {
            "username": f["username"], "age": f["age"], "height": f["height"],
            "weight": f["weight"], "blood_group": f["blood_group"],
        })
        session["username"] = f["username"]
        return redirect(url_for("profile"))
    return render_template("edit_profile.html", user=db.get_user(email))


@app.route("/manual_log", methods=["GET", "POST"])
@login_required
def manual_log():
    if request.method == "POST":
        try:
            reps = int(request.form["count"])
            total_time = float(request.form["total_time"])
            key = request.form["exercise"]
            if reps <= 0 or total_time <= 0 or key not in MANUAL_EXERCISES:
                raise ValueError
        except (ValueError, KeyError):
            return render_template("manual_log.html", exercises=MANUAL_EXERCISES,
                                   error="Please enter valid values")
        email = session["email"]
        user = db.get_user(email)
        store_session(email, build_session(label_for(key), reps, total_time,
                                           total_time / reps, user_weight(user or {}),
                                           date=request.form["date"]))
        return redirect(url_for("profile"))
    return render_template("manual_log.html", exercises=MANUAL_EXERCISES)


@app.route("/export_sessions")
@login_required
def export_sessions():
    user = db.get_user(session["email"])
    df = pd.DataFrame(user.get("sessions", []) if user else [])
    if df.empty:
        flash("No sessions to export")
        return redirect(url_for("profile"))
    out = io.BytesIO(df.to_csv(index=False).encode("utf-8"))
    return send_file(out, mimetype="text/csv", as_attachment=True,
                     download_name="session_history.csv")


# ------------------------------------------------------------------ Admin
@app.route("/admin")
def admin():
    if "email" not in session or not session.get("is_admin"):
        return redirect(url_for("login"))
    users = db.all_users()
    return render_template("admin.html", users=users,
                           now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def _admin_payload(require_index=False):
    d = request.get_json(force=True, silent=True) or {}
    if not d.get("email"):
        return None
    if require_index and d.get("session_index") is None:
        return None
    return d


@app.route("/admin/update_session", methods=["POST"])
@admin_required_json
def update_session():
    d = _admin_payload(require_index=True)
    if not d or not all([d.get("date"), d.get("exercise")]) or d.get("count") is None or d.get("total_time") is None:
        return jsonify({"success": False, "error": "Missing required fields"})
    try:
        reps, total_time, idx = int(d["count"]), float(d["total_time"]), int(d["session_index"])
        user = db.get_user(d["email"])
        if not user:
            return jsonify({"success": False, "error": "User not found"})
        sessions = user.get("sessions", [])
        if idx < 0 or idx >= len(sessions):
            return jsonify({"success": False, "error": "Session not found"})
        sessions[idx] = build_session(d["exercise"], reps, total_time,
                                      total_time / reps if reps else 0,
                                      user_weight(user), date=d["date"])
        db.update_sessions(d["email"], sessions)
        evaluate_badges(d["email"])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/delete_session", methods=["POST"])
@admin_required_json
def delete_session():
    d = _admin_payload(require_index=True)
    if not d:
        return jsonify({"success": False, "error": "Missing required fields"})
    try:
        idx = int(d["session_index"])
        user = db.get_user(d["email"])
        if not user:
            return jsonify({"success": False, "error": "User not found"})
        sessions = user.get("sessions", [])
        if idx < 0 or idx >= len(sessions):
            return jsonify({"success": False, "error": "Session not found"})
        del sessions[idx]
        db.update_sessions(d["email"], sessions)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/add_user", methods=["POST"])
@admin_required_json
def admin_add_user():
    d = request.get_json(force=True, silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    if not email or not username or not password:
        return jsonify({"success": False, "error": "Email, username and password are required"})
    if db.user_exists(email):
        return jsonify({"success": False, "error": "Email already registered"})
    try:
        db.firebase_create_user(email, password)  # no-op in SQLite mode
        db.set_user(email, {
            "username": username,
            "email": email,
            "password_hash": generate_password_hash(password),
            "age": d.get("age", ""),
            "height": d.get("height", ""),
            "weight": d.get("weight", ""),
            "blood_group": d.get("blood_group", ""),
            "sessions": [],
            "badges": [],
            "is_admin": bool(d.get("is_admin", False)),
            "daily_goal_reps": 20,
            "weekly_goal_reps": 150,
        })
        return jsonify({"success": True, "email": email, "username": username})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/add_session", methods=["POST"])
@admin_required_json
def add_session():
    d = _admin_payload()
    if not d or not all([d.get("date"), d.get("exercise")]) or d.get("count") is None or d.get("total_time") is None:
        return jsonify({"success": False, "error": "Missing required fields"})
    try:
        reps, total_time = int(d["count"]), float(d["total_time"])
        user = db.get_user(d["email"])
        if not user:
            return jsonify({"success": False, "error": "User not found"})
        store_session(d["email"], build_session(d["exercise"], reps, total_time,
                                                total_time / reps if reps else 0,
                                                user_weight(user), date=d["date"]))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ------------------------------------------------- Admin Automated Diagnostics
@app.route("/admin/tests")
def admin_tests():
    if "email" not in session or not session.get("is_admin"):
        flash("Admin privileges required to access diagnostics.", "error")
        return redirect(url_for("login"))
    import test_app
    results = [r.to_dict() for r in test_app.run_all_tests()]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    stats = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round((passed / total * 100), 1) if total else 0,
        "duration_ms": round(sum(r["duration"] for r in results), 1)
    }
    diagnostics = test_app.get_system_diagnostics()
    return render_template("admin_tests.html",
                           results=results,
                           stats=stats,
                           diagnostics=diagnostics,
                           now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@app.route("/admin/run_tests_api", methods=["POST"])
def admin_run_tests_api():
    if "email" not in session or not session.get("is_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    import test_app
    results = [r.to_dict() for r in test_app.run_all_tests()]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    stats = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round((passed / total * 100), 1) if total else 0,
        "duration_ms": round(sum(r["duration"] for r in results), 1)
    }
    diagnostics = test_app.get_system_diagnostics()
    return jsonify({
        "success": True,
        "results": results,
        "stats": stats,
        "diagnostics": diagnostics
    })


# ------------------------------------------------- Leaderboard / advice
@app.route("/leaderboard")
@login_required
def leaderboard():
    rows = []
    for u in db.all_users():
        s = u.get("sessions", [])
        rows.append({
            "username": u.get("username", "Unknown"),
            "total_reps": sum(x.get("count", 0) for x in s),
            "total_time": round(sum(x.get("total_time", 0) for x in s), 2),
            "total_sessions": len(s),
            "badges": len(u.get("badges", [])),
            "streak": compute_streaks(s)["current"],
        })
    rows.sort(key=lambda r: r["total_reps"], reverse=True)
    return render_template("leaderboard.html", leaderboard=rows)


@app.route("/recommendations")
@login_required
def recommendations():
    user = db.get_user(session["email"])
    sessions = user.get("sessions", []) if user else []
    speeds = [s["average_speed"] for s in sessions if s.get("average_speed", 0) > 0]
    if not speeds:
        return render_template("recommendation.html", error="No session data found")
    avg = float(np.mean(speeds))                       # seconds per rep
    return render_template(
        "recommendation.html",
        recommendations=[speed_recommendation(avg)],
        avg_speed=round(avg, 2),                        # seconds per rep
        reps_per_min=round(60 / avg, 1),
    )


# --------------------------------------------------------- AI Chatbot (Profile)
@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "Message is required"}), 400

    history = data.get("history", [])
    user = db.get_user(session.get("email", ""))

    # Priority: explicit key in request > session saved key > env variable
    user_api_key = data.get("api_key") or session.get("openai_api_key")

    import chatbot
    response = chatbot.ask_fitness_coach(
        user_query=message,
        user=user,
        chat_history=history,
        api_key=user_api_key
    )
    return jsonify(response)


@app.route("/api/set_openai_key", methods=["POST"])
@login_required
def api_set_openai_key():
    data = request.get_json(force=True, silent=True) or {}
    key = (data.get("api_key") or "").strip()
    if key:
        session["openai_api_key"] = key
        return jsonify({"success": True, "message": "OpenAI API Key saved for this session!"})
    else:
        session.pop("openai_api_key", None)
        return jsonify({"success": True, "message": "API key cleared. Using local dataset engine."})


# --------------------------------------------------------- Live training
@app.route("/select_exercise", methods=["GET", "POST"])
@login_required
def select_exercise():
    global exercise, count, target_count, position, exercise_started, is_paused
    global feedback_message, start_time, last_rep_time, paused_time, pause_start
    global rep_speeds, session_saved

    if request.method == "POST":
        name = request.form.get("exercise")
        target = request.form.get("target")
        if name in EXERCISES and target:
            try:
                n = int(target)
                if n <= 0 or n > 1000:
                    raise ValueError
            except ValueError:
                return render_template("select_exercise.html", error="Please enter a valid target count",
                                       exercises=EXERCISES.values())
            reset_state()
            target_count = n
            exercise = EXERCISES[name]
            exercise_started = True
            session["exercise_name"] = name
            session["exercise_image"] = exercise["image"]
            session["target"] = n
            return redirect(url_for("training"))
        return render_template("select_exercise.html", error="Invalid exercise or target count",
                               exercises=EXERCISES.values())
    return render_template("select_exercise.html", exercises=EXERCISES.values())


@app.route("/training")
@login_required
def training():
    if not exercise:
        return redirect(url_for("select_exercise"))
    return render_template("training.html")


@app.route("/video_feed")
@login_required
def video_feed():
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/get_count")
@login_required
def get_count():
    total = time.time() - start_time - paused_time if start_time else 0
    return jsonify({
        "count": count,
        "target": target_count,
        "feedback": feedback_message,
        "total_time": total,
        "exercise_complete": count >= target_count > 0,
        "is_paused": is_paused,
    })


@app.route("/set_target", methods=["POST"])
@login_required
def set_target():
    global target_count, count, position, exercise_started, feedback_message
    global start_time, last_rep_time, paused_time, is_paused, rep_speeds, session_saved
    try:
        n = int((request.get_json(force=True) or {}).get("target", 0))
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return jsonify({"success": False, "error": "Target must be positive"})
    target_count = n
    count = 0
    position = None
    exercise_started = True
    is_paused = False
    feedback_message = "Begin Exercise!"
    start_time = None          # starts on first rep so setup time isn't counted
    last_rep_time = None
    paused_time = 0
    rep_speeds = []
    session_saved = False
    session["target"] = n
    return jsonify({"success": True, "target": n})


@app.route("/pause_resume", methods=["POST"])
@login_required
def pause_resume():
    global is_paused, pause_start, paused_time
    if not exercise_started:
        return jsonify({"success": False, "error": "No active exercise"})
    is_paused = not is_paused
    if is_paused:
        pause_start = time.time()
    else:
        if pause_start:
            paused_time += time.time() - pause_start
        pause_start = None
    return jsonify({"success": True, "is_paused": is_paused})


@app.route("/save_session", methods=["POST"])
@login_required
def save_session():
    if count == 0 or not exercise:
        return jsonify({"success": False, "error": "No exercise data to save"})
    if session_saved:
        return jsonify({"success": True, "already_saved": True})
    total = time.time() - start_time - paused_time if start_time else 0
    finalize_live_session(total)
    return jsonify({"success": True})


@app.route("/debug_session")
def debug_session():
    if not app.debug:
        return jsonify({"error": "Not available"}), 404
    return jsonify(dict(session))


@app.template_filter("mean")
def mean_filter(values):
    return sum(values) / len(values) if values else 0


if __name__ == "__main__":
    app.run(debug=True, threaded=True)