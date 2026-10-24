"""
test_app.py – Comprehensive automated test suite and system diagnostics for FitTrack.

Can be run:
  1. CLI:
        python test_app.py
  2. With pytest:
        pytest test_app.py -v
  3. Interactive Web Dashboard:
        /admin/tests  (Admin privileges required)
"""

import json
import os
import platform
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta

# Fix Windows console UTF-8 encoding if possible
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ───────────────────────────────────────────────────────── Result Container
class TestResult:
    def __init__(self, name, category, passed, detail="", duration=0.0):
        self.name = name
        self.category = category
        self.passed = passed
        self.detail = detail
        self.duration = duration

    def to_dict(self):
        return {
            "name": self.name,
            "category": self.category,
            "passed": self.passed,
            "detail": self.detail,
            "duration": round(self.duration * 1000, 2),  # in ms
        }


def _run(name, category, fn):
    t0 = time.perf_counter()
    try:
        fn()
        return TestResult(name, category, True, "Passed successfully", time.perf_counter() - t0)
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        return TestResult(name, category, False, f"{type(e).__name__}: {tb}", time.perf_counter() - t0)


# ═════════════════════════════════════════════════════════ TEST DEFINITIONS

# ── CATEGORY 1: FEATURES & EXERCISE LOGIC ──────────────────────────────────
def test_features_estimate_calories_normal():
    from features import estimate_calories
    r = estimate_calories("squats", 3600, 70, 100)
    assert r["kcal"] == round(5.0 * 70 * 1.0, 2), f"Expected MET 5.0 * 70kg * 1hr = 350, got {r}"
    assert r["met"] == 5.0


def test_features_estimate_calories_zero_time():
    from features import estimate_calories
    r = estimate_calories("squats", 0, 70, 10)
    assert r["kcal"] == 0.0


def test_features_estimate_calories_unknown_exercise():
    from features import estimate_calories
    r = estimate_calories("pilates_fusion", 3600, 80, 50)
    assert r["kcal"] == round(4.0 * 80, 2)  # default MET is 4.0


def test_features_speed_recommendation_excellent():
    from features import speed_recommendation
    r = speed_recommendation(2.0)
    assert "excellent" in r.lower()


def test_features_speed_recommendation_fast():
    from features import speed_recommendation
    r = speed_recommendation(0.5)
    assert "fast" in r.lower()


def test_features_speed_recommendation_slow():
    from features import speed_recommendation
    r = speed_recommendation(8.0)
    assert "slow" in r.lower()


def test_features_speed_recommendation_zero():
    from features import speed_recommendation
    r = speed_recommendation(0)
    assert "no speed" in r.lower()


def test_features_compute_streaks_empty():
    from features import compute_streaks
    r = compute_streaks([])
    assert r == {"current": 0, "longest": 0}


def test_features_compute_streaks_single_today():
    from features import compute_streaks
    today = datetime.now().strftime("%Y-%m-%d")
    r = compute_streaks([{"date": today, "count": 10}])
    assert r["current"] >= 1
    assert r["longest"] >= 1


def test_features_compute_streaks_consecutive_days():
    from features import compute_streaks
    base = datetime(2026, 1, 1)
    sessions = [{"date": (base + timedelta(days=i)).strftime("%Y-%m-%d"), "count": 10} for i in range(5)]
    r = compute_streaks(sessions)
    assert r["longest"] == 5


def test_features_compute_streaks_broken():
    from features import compute_streaks
    sessions = [
        {"date": "2026-01-01", "count": 10},
        {"date": "2026-01-02", "count": 15},
        {"date": "2026-01-05", "count": 20},  # Gap on 3rd & 4th
    ]
    r = compute_streaks(sessions)
    assert r["longest"] == 2


def test_features_compute_streaks_invalid_date_handled():
    from features import compute_streaks
    sessions = [{"date": "invalid-date-string", "count": 10}]
    r = compute_streaks(sessions)
    assert r == {"current": 0, "longest": 0}


def test_features_check_posture_unsupported():
    from features import check_posture
    r = check_posture("unsupported_exercise", [], None)
    assert r is None


# ── CATEGORY 2: DATABASE LAYER (OFFLINE SQLITE) ───────────────────────────
def _temp_sqlite():
    from db_layer import SQLiteDB
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return SQLiteDB(f.name), f.name


def _clean_sqlite(db, path):
    try:
        db.close()
    except Exception:
        pass
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def test_db_set_and_get_user():
    db, path = _temp_sqlite()
    try:
        db.set_user("john@example.com", {"username": "JohnDoe", "email": "john@example.com", "age": 28})
        user = db.get_user("john@example.com")
        assert user is not None
        assert user["username"] == "JohnDoe"
        assert user["age"] == 28
    finally:
        _clean_sqlite(db, path)


def test_db_user_not_found():
    db, path = _temp_sqlite()
    try:
        assert db.get_user("nonexistent@example.com") is None
    finally:
        _clean_sqlite(db, path)


def test_db_user_exists_check():
    db, path = _temp_sqlite()
    try:
        assert not db.user_exists("bob@example.com")
        db.set_user("bob@example.com", {"email": "bob@example.com"})
        assert db.user_exists("bob@example.com")
    finally:
        _clean_sqlite(db, path)


def test_db_update_user_fields():
    db, path = _temp_sqlite()
    try:
        db.set_user("alice@example.com", {"email": "alice@example.com", "weight": 60, "height": 165})
        db.update_user("alice@example.com", {"weight": 58, "target": "hypertrophy"})
        user = db.get_user("alice@example.com")
        assert user["weight"] == 58
        assert user["height"] == 165
        assert user["target"] == "hypertrophy"
    finally:
        _clean_sqlite(db, path)


def test_db_append_and_update_sessions():
    db, path = _temp_sqlite()
    try:
        db.set_user("test@fit.com", {"email": "test@fit.com", "sessions": []})
        session1 = {"date": "2026-03-01", "exercise": "squats", "count": 25, "total_time": 90, "calories": 18.5}
        db.append_session("test@fit.com", session1)
        u = db.get_user("test@fit.com")
        assert len(u["sessions"]) == 1
        assert u["sessions"][0]["exercise"] == "squats"

        # Bulk update sessions
        db.update_sessions("test@fit.com", [session1, {"date": "2026-03-02", "exercise": "pushups", "count": 30}])
        u = db.get_user("test@fit.com")
        assert len(u["sessions"]) == 2
    finally:
        _clean_sqlite(db, path)


def test_db_update_badges():
    db, path = _temp_sqlite()
    try:
        db.set_user("badge@test.com", {"email": "badge@test.com", "badges": []})
        db.update_badges("badge@test.com", ["Centurion", "7-Day Streak"])
        u = db.get_user("badge@test.com")
        assert "Centurion" in u.get("badges", [])
        assert "7-Day Streak" in u.get("badges", [])
    finally:
        _clean_sqlite(db, path)


def test_db_delete_field():
    db, path = _temp_sqlite()
    try:
        db.set_user("field@test.com", {"email": "field@test.com", "temp_token": "xyz123"})
        db.delete_field("field@test.com", "temp_token")
        u = db.get_user("field@test.com")
        assert "temp_token" not in u
    finally:
        _clean_sqlite(db, path)


def test_db_all_users_security():
    db, path = _temp_sqlite()
    try:
        db.set_user("u1@sec.com", {"email": "u1@sec.com", "password_hash": "argon2$hashed_secret"})
        users = db.all_users()
        assert len(users) >= 1
        for u in users:
            assert "password_hash" not in u, "Security flaw: password_hash leaked in all_users()"
            assert "password" not in u, "Security flaw: password leaked in all_users()"
    finally:
        _clean_sqlite(db, path)


def test_db_firebase_fallback_detection():
    from db_layer import _firebase_available
    is_available = _firebase_available()
    assert isinstance(is_available, bool)


# ── CATEGORY 3: SECURITY & FLASK ROUTES ───────────────────────────────────
_cached_client = None

def _get_flask_client():
    global _cached_client
    if _cached_client is None:
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        _cached_client = flask_app.app.test_client()
    return _cached_client


def test_route_index_redirect():
    client = _get_flask_client()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302, 308)


def test_route_login_accessible():
    client = _get_flask_client()
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"FitTrack" in resp.data or b"login" in resp.data.lower()


def test_route_register_accessible():
    client = _get_flask_client()
    resp = client.get("/register")
    assert resp.status_code == 200
    assert b"Create Account" in resp.data or b"register" in resp.data.lower()


def test_route_profile_requires_auth():
    client = _get_flask_client()
    resp = client.get("/profile", follow_redirects=False)
    # Anonymous request must be redirected to login
    assert resp.status_code in (302, 401, 403)


def test_route_admin_requires_admin_privileges():
    client = _get_flask_client()
    # Anonymous request
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_route_admin_tests_blocked_for_anonymous():
    client = _get_flask_client()
    resp = client.get("/admin/tests", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_route_admin_dashboard_allowed_for_admin():
    client = _get_flask_client()
    with client.session_transaction() as sess:
        sess["email"] = "admin@fittrack.local"
        sess["is_admin"] = True
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert b"Admin Dashboard" in resp.data


def test_template_admin_tests_rendered_cleanly():
    import app as flask_app
    with flask_app.app.test_request_context():
        from flask import render_template
        html = render_template(
            "admin_tests.html",
            results=[{"name": "Smoke Test", "category": "System", "passed": True, "detail": "OK", "duration": 1.2}],
            stats={"total": 1, "passed": 1, "failed": 0, "pass_rate": 100.0, "duration_ms": 1.2},
            diagnostics={"platform": "Test OS", "python_version": "3.11", "database_mode": "SQLITE", "database_size_kb": 10, "total_users": 1, "timestamp": "now"}
        )
        assert "FitTrack System Inspector" in html
        assert "Smoke Test" in html


# ── CATEGORY 4: SYSTEM INTEGRITY & TEMPLATES ──────────────────────────────
def test_system_template_files_integrity():
    required_templates = [
        "login.html", "register.html", "profile.html", "admin.html",
        "progress.html", "measurements.html", "reminders.html",
        "forgot_password.html", "leaderboard.html", "training.html",
        "admin_tests.html"
    ]
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    missing = [t for t in required_templates if not os.path.isfile(os.path.join(templates_dir, t))]
    assert not missing, f"Missing templates: {missing}"


def test_system_sqlite_connection_ready():
    from db_layer import get_db
    db = get_db()
    assert db is not None
    assert hasattr(db, "get_user")
    assert hasattr(db, "all_users")


# ── CATEGORY 5: AI CHATBOT & FITNESS DATASET ──────────────────────────────
def test_chatbot_dataset_loaded():
    import chatbot
    ds = chatbot.load_dataset()
    assert isinstance(ds, dict)
    assert "exercises" in ds
    assert "squats" in ds["exercises"]


def test_chatbot_user_context_formatting():
    import chatbot
    dummy = {
        "username": "TestUser", "weight": 75, "height": 178,
        "sessions": [{"exercise": "squats", "count": 20, "total_time": 40, "calories": 25}]
    }
    ctx = chatbot.format_user_dataset_context(dummy)
    assert "TestUser" in ctx
    assert "75" in ctx
    assert "squats" in ctx


def test_chatbot_offline_fallback_answers():
    import chatbot
    resp = chatbot.ask_fitness_coach("tell me about squats form", {"username": "Alex"})
    assert resp["success"] is True
    assert "Squats" in resp["reply"] or "squat" in resp["reply"].lower()


def test_route_chat_auth_and_response():
    client = _get_flask_client()
    # Ensure fresh anonymous state
    with client.session_transaction() as sess:
        sess.clear()
    anon = client.post("/api/chat", json={"message": "hello"}, follow_redirects=False)
    assert anon.status_code in (302, 401, 403)

    # Authenticated user
    with client.session_transaction() as sess:
        sess["email"] = "alexjmt1@gmail.com"
    auth_resp = client.post("/api/chat", json={"message": "how many calories did I burn?"})
    assert auth_resp.status_code == 200
    data = auth_resp.get_json()
    assert data["success"] is True
    assert "reply" in data


# ═════════════════════════════════════════════════════════ TEST RUNNER

ALL_TEST_CASES = [
    # Features & Exercise
    ("Calories MET Calculation",          "Features", test_features_estimate_calories_normal),
    ("Calories Zero-Time Handling",       "Features", test_features_estimate_calories_zero_time),
    ("Calories Fallback MET",             "Features", test_features_estimate_calories_unknown_exercise),
    ("Speed Pace: Excellent",             "Features", test_features_speed_recommendation_excellent),
    ("Speed Pace: Too Fast Warning",      "Features", test_features_speed_recommendation_fast),
    ("Speed Pace: Slow Guidance",         "Features", test_features_speed_recommendation_slow),
    ("Speed Pace: Empty/Zero Input",      "Features", test_features_speed_recommendation_zero),
    ("Streaks: Empty Session List",       "Features", test_features_compute_streaks_empty),
    ("Streaks: Single Workout Today",     "Features", test_features_compute_streaks_single_today),
    ("Streaks: Consecutive 5-Day Run",    "Features", test_features_compute_streaks_consecutive_days),
    ("Streaks: Broken Streak Gap",        "Features", test_features_compute_streaks_broken),
    ("Streaks: Bad Timestamp Resilience", "Features", test_features_compute_streaks_invalid_date_handled),
    ("Posture: Unsupported Exercise Safe", "Features", test_features_check_posture_unsupported),

    # Database & Storage
    ("SQLite: Set & Get User Record",     "Database", test_db_set_and_get_user),
    ("SQLite: User Not Found Query",      "Database", test_db_user_not_found),
    ("SQLite: Existence Verification",    "Database", test_db_user_exists_check),
    ("SQLite: Update User Profile",       "Database", test_db_update_user_fields),
    ("SQLite: Session Logging & Append",  "Database", test_db_append_and_update_sessions),
    ("SQLite: Badge Assignment",          "Database", test_db_update_badges),
    ("SQLite: Field Deletion",            "Database", test_db_delete_field),
    ("SQLite: Security Hash Sanitization","Database", test_db_all_users_security),
    ("Firebase: Fallback Auto-Detection", "Database", test_db_firebase_fallback_detection),

    # Security & Routes
    ("Route: Root Redirect (/ -> Auth)",  "Security & Routes", test_route_index_redirect),
    ("Route: Login Form Render",          "Security & Routes", test_route_login_accessible),
    ("Route: Registration Form Render",   "Security & Routes", test_route_register_accessible),
    ("Security: Profile Blocks Anon",     "Security & Routes", test_route_profile_requires_auth),
    ("Security: Admin Dashboard Protected","Security & Routes", test_route_admin_requires_admin_privileges),
    ("Security: Admin Tests Protected",   "Security & Routes", test_route_admin_tests_blocked_for_anonymous),
    ("Security: Admin Dashboard Authorized","Security & Routes", test_route_admin_dashboard_allowed_for_admin),

    # System Integrity
    ("Integrity: Template Files Exist",   "System Health", test_system_template_files_integrity),
    ("Integrity: SQLite Connection Ready","System Health", test_system_sqlite_connection_ready),
    ("Template: Admin Tests Console Renders", "System Health", test_template_admin_tests_rendered_cleanly),

    # AI Chatbot & Dataset
    ("Chatbot: Knowledge Dataset Loaded", "AI Chatbot", test_chatbot_dataset_loaded),
    ("Chatbot: User Context Formatting",  "AI Chatbot", test_chatbot_user_context_formatting),
    ("Chatbot: Offline Fallback Engine",  "AI Chatbot", test_chatbot_offline_fallback_answers),
    ("Chatbot: Route /api/chat Auth",     "AI Chatbot", test_route_chat_auth_and_response),
]


def run_all_tests():
    """Runs all registered tests and returns a list of TestResult objects."""
    results = []
    for name, category, fn in ALL_TEST_CASES:
        results.append(_run(name, category, fn))
    return results


def get_system_diagnostics():
    """Gathers live system environment telemetry."""
    from db_layer import get_db, SQLITE_FILE
    db = get_db()
    
    db_size_kb = 0
    if os.path.exists(SQLITE_FILE):
        try:
            db_size_kb = round(os.path.getsize(SQLITE_FILE) / 1024, 2)
        except Exception:
            pass

    user_count = 0
    try:
        user_count = len(db.all_users())
    except Exception:
        pass

    return {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python_version": sys.version.split()[0],
        "database_mode": getattr(db, "mode", "sqlite").upper(),
        "database_file": SQLITE_FILE if getattr(db, "mode", "") == "sqlite" else "Firebase Firestore",
        "database_size_kb": db_size_kb,
        "total_users": user_count,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def print_cli_summary(results):
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    total_duration = sum(r.duration for r in results) * 1000

    print("\n" + "=" * 70)
    print(f"  FITTRACK AUTOMATED DIAGNOSTIC SUITE  |  {passed}/{total} Passed")
    print(f"  Total Duration: {total_duration:.1f}ms  |  Status: {'ALL PASS' if passed == total else 'FAILURES DETECTED'}")
    print("=" * 70)

    current_cat = None
    for r in results:
        if r.category != current_cat:
            current_cat = r.category
            print(f"\n  [{current_cat.upper()}]")
        status = "[ PASS ]" if r.passed else "[ FAIL ]"
        ms = f"{r.duration * 1000:6.1f}ms"
        print(f"    {status}  {r.name:<40} {ms}")
        if not r.passed:
            print(f"              └─ Error: {r.detail}")

    print("\n" + "=" * 70)
    if passed == total:
        print("  ✓ All systems verified and fully functional.")
    else:
        print(f"  ✗ {total - passed} test(s) failed. Please review error traces above.")
    print("=" * 70 + "\n")
    return passed == total


if __name__ == "__main__":
    test_results = run_all_tests()
    success = print_cli_summary(test_results)
    sys.exit(0 if success else 1)
