"""
features.py – supporting feature functions for the fitness app.

Provides:
    init_features(app, db)      – registers extra Flask routes
    check_posture(key, lm, mp)  – AI posture tip for a given exercise
    compute_streaks(sessions)   – current & longest daily streak
    estimate_calories(name, t, weight, reps) -> {"kcal": float}
    speed_recommendation(avg_secs_per_rep)   -> str advice
"""

from datetime import datetime, timedelta


# ─────────────────────────────────────────────── calorie estimation
_MET = {
    "knee raises": 3.5, "knee_raises": 3.5,
    "squats": 5.0,
    "pushups": 4.0, "push ups": 4.0,
    "lunges": 4.5,
    "plank": 3.0,
    "sit ups": 4.0, "sit_ups": 4.0,
    "jumping jacks": 8.0, "jumping_jacks": 8.0,
    "bicep curls": 3.0, "bicep_curls": 3.0,
    "shoulder press": 4.0, "shoulder_press": 4.0,
}


def estimate_calories(exercise_name, total_time_secs, weight_kg, reps):
    key = str(exercise_name).lower().strip()
    met = _MET.get(key, 4.0)
    hours = max(total_time_secs, 0) / 3600.0
    kcal = round(met * weight_kg * hours, 2)
    return {"kcal": kcal, "met": met}


# ─────────────────────────────────────────────── streak computation
def compute_streaks(sessions):
    if not sessions:
        return {"current": 0, "longest": 0}

    dates = set()
    for s in sessions:
        raw = s.get("date", "")
        try:
            d = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
            dates.add(d)
        except (ValueError, TypeError):
            continue

    if not dates:
        return {"current": 0, "longest": 0}

    sorted_desc = sorted(dates, reverse=True)
    today = datetime.now().date()

    current = 0
    cursor = today
    for d in sorted_desc:
        if d == cursor or (current == 0 and d == today - timedelta(days=1)):
            if current == 0 and d != today:
                cursor = d
            current += 1
            cursor -= timedelta(days=1)
        elif d < cursor:
            break

    asc = sorted(dates)
    longest, run = 1, 1
    for i in range(1, len(asc)):
        if (asc[i] - asc[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    return {"current": current, "longest": longest}


# ─────────────────────────────────────────────── speed recommendation
def speed_recommendation(avg_secs_per_rep):
    if avg_secs_per_rep <= 0:
        return "No speed data yet. Complete a workout to get recommendations."
    rpm = 60.0 / avg_secs_per_rep
    if avg_secs_per_rep < 0.8:
        return f"Your pace ({rpm:.1f} reps/min) is very fast. Slow down to improve form and reduce injury risk."
    elif avg_secs_per_rep < 1.5:
        return f"Your pace ({rpm:.1f} reps/min) is fast. Consider slowing slightly to maximise muscle engagement."
    elif avg_secs_per_rep <= 3.0:
        return f"Your pace ({rpm:.1f} reps/min) is excellent! Keep up the great work and focus on maintaining form."
    elif avg_secs_per_rep <= 5.0:
        return f"Your pace ({rpm:.1f} reps/min) is moderate. Try to pick up the tempo a little for better cardio benefit."
    else:
        return f"Your pace ({rpm:.1f} reps/min) is slow. Gradually increase your speed while keeping good form."


# ─────────────────────────────────────────────── posture correction
def _lm(landmarks, part, mp_pose):
    try:
        idx = getattr(mp_pose.PoseLandmark, part).value
        return landmarks[idx]
    except (AttributeError, IndexError):
        return None


def _visible(lm, threshold=0.4):
    return lm is not None and lm.visibility > threshold


def check_posture(exercise_key, landmarks, mp_pose):
    key = str(exercise_key).lower().replace(" ", "_")
    try:
        if key == "squats":
            return _check_squat(landmarks, mp_pose)
        elif key == "pushups":
            return _check_pushup(landmarks, mp_pose)
        elif key == "knee_raises":
            return _check_knee_raise(landmarks, mp_pose)
        elif key == "lunges":
            return _check_lunge(landmarks, mp_pose)
    except Exception:
        pass
    return None


def _check_squat(landmarks, mp_pose):
    l_shoulder = _lm(landmarks, "LEFT_SHOULDER", mp_pose)
    l_hip      = _lm(landmarks, "LEFT_HIP", mp_pose)
    l_knee     = _lm(landmarks, "LEFT_KNEE", mp_pose)
    l_ankle    = _lm(landmarks, "LEFT_ANKLE", mp_pose)
    if not all(_visible(x) for x in [l_shoulder, l_hip, l_knee, l_ankle]):
        return None
    if l_knee.x > l_ankle.x + 0.08:
        return "Keep your knees behind your toes!"
    if abs(l_shoulder.x - l_hip.x) > 0.12:
        return "Keep your back straight – don't lean too far forward!"
    return None


def _check_pushup(landmarks, mp_pose):
    l_shoulder = _lm(landmarks, "LEFT_SHOULDER", mp_pose)
    l_hip      = _lm(landmarks, "LEFT_HIP", mp_pose)
    l_ankle    = _lm(landmarks, "LEFT_ANKLE", mp_pose)
    if not all(_visible(x) for x in [l_shoulder, l_hip, l_ankle]):
        return None
    mid_y = (l_shoulder.y + l_ankle.y) / 2
    if l_hip.y < mid_y - 0.05:
        return "Lower your hips – keep your body in a straight line!"
    if l_hip.y > mid_y + 0.05:
        return "Raise your hips – avoid sagging in the middle!"
    return None


def _check_knee_raise(landmarks, mp_pose):
    l_hip  = _lm(landmarks, "LEFT_HIP", mp_pose)
    l_knee = _lm(landmarks, "LEFT_KNEE", mp_pose)
    if not all(_visible(x) for x in [l_hip, l_knee]):
        return None
    if l_knee.y > l_hip.y + 0.05:
        return "Lift your knee higher for full range of motion!"
    return None


def _check_lunge(landmarks, mp_pose):
    l_knee  = _lm(landmarks, "LEFT_KNEE", mp_pose)
    l_ankle = _lm(landmarks, "LEFT_ANKLE", mp_pose)
    if not all(_visible(x) for x in [l_knee, l_ankle]):
        return None
    if l_knee.x > l_ankle.x + 0.08:
        return "Keep your front knee behind your toes!"
    return None


# ─────────────────────────────────────────────── init_features
def init_features(app, db):
    """Register optional feature routes and Jinja2 filters."""
    from flask import jsonify, render_template, request, session, redirect, url_for, send_file
    from functools import wraps
    import io

    def _login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if "email" not in session:
                return redirect(url_for("login"))
            return fn(*a, **kw)
        return wrapper

    # ── /progress ─────────────────────────────────────────────────────────
    @app.route("/progress")
    @_login_required
    def progress():
        user = db.get_user(session["email"])
        if not user:
            return redirect(url_for("login"))
        sessions = user.get("sessions", [])
        streaks = compute_streaks(sessions)
        total_reps = sum(s.get("count", 0) for s in sessions)
        total_cal  = round(sum(s.get("calories", 0) for s in sessions), 1)
        total_time = round(sum(s.get("total_time", 0) for s in sessions) / 60, 1)
        return render_template(
            "progress.html",
            user=user, sessions=sessions, streaks=streaks,
            total_reps=total_reps, total_calories=total_cal,
            total_time_mins=total_time,
        )

    # ── /measurements ─────────────────────────────────────────────────────
    @app.route("/measurements", methods=["GET", "POST"])
    @_login_required
    def measurements():
        email = session["email"]
        user = db.get_user(email) or {}
        if request.method == "POST":
            f = request.form
            mlog = user.get("measurements", [])
            mlog.append({
                "date":   f.get("date", datetime.now().strftime("%Y-%m-%d")),
                "weight": float(f.get("weight") or user.get("weight", 70)),
                "chest":  float(f.get("chest") or 0),
                "waist":  float(f.get("waist") or 0),
                "hips":   float(f.get("hips") or 0),
            })
            user["measurements"] = mlog
            db.set_user(email, user)
            return redirect(url_for("measurements"))
        return render_template("measurements.html", user=user,
                               measurements=user.get("measurements", []))

    # ── /reminders ────────────────────────────────────────────────────────
    @app.route("/reminders", methods=["GET", "POST"])
    @_login_required
    def reminders():
        email = session["email"]
        user = db.get_user(email) or {}
        if request.method == "POST":
            f = request.form
            rems = user.get("reminders", [])
            rems.append({
                "time":    f.get("time", "08:00"),
                "message": f.get("message", "Time to workout!"),
                "active":  True,
            })
            user["reminders"] = rems
            db.set_user(email, user)
            return redirect(url_for("reminders"))
        return render_template("reminders.html", user=user,
                               reminders=user.get("reminders", []))

    # ── /pdf_report ───────────────────────────────────────────────────────
    @app.route("/pdf_report")
    @_login_required
    def pdf_report():
        user = db.get_user(session["email"]) or {}
        sessions = user.get("sessions", [])
        lines = [
            f"Fitness Report for {user.get('username', 'User')}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "Date,Exercise,Reps,Time(s),Calories",
        ]
        for s in sessions:
            lines.append(
                f"{s.get('date','')},{s.get('exercise','')},{s.get('count',0)},"
                f"{round(s.get('total_time',0),1)},{round(s.get('calories',0),1)}"
            )
        content = "\n".join(lines).encode("utf-8")
        return send_file(io.BytesIO(content), mimetype="text/plain",
                         as_attachment=True, download_name="fitness_report.txt")

    # ── Jinja2 filters ────────────────────────────────────────────────────
    @app.template_filter("format_date")
    def format_date_filter(value):
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d %b %Y")
        except (ValueError, TypeError):
            return value

    @app.template_filter("round2")
    def round2_filter(value):
        try:
            return round(float(value), 2)
        except (ValueError, TypeError):
            return value