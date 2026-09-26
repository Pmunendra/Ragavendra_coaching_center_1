"""
Demo/dummy data seeder for NEET, EAMCET and Govt Jobs categories.

Run once, after the database tables exist (the app creates them automatically
on first boot via db.create_all()):

    cd exam_portal
    python seed_neet_eamcet_govt.py

Safe to re-run - every insert is guarded by a "does this already exist"
check, so running it twice won't create duplicates. It only adds data; it
never deletes or modifies anything that already exists.

Creates, for NEET / EAMCET / Govt Jobs:
  - 2 courses each (6 total)
  - Course -> Subject -> Chapter study-material structure, with a real
    (small) PDF note + a video-class placeholder in each chapter
  - Daily / Weekly / Monthly Grand Test mock exams with sample questions,
    each with a ready-to-use ExamLink
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.extensions import db
from app.models import (
    Subject, Course, MaterialSubject, MaterialChapter, CourseMaterial,
    Exam, ExamLink, Question, Option, User,
)


def get_or_create_subject(name):
    s = Subject.query.filter_by(name=name).first()
    if not s:
        s = Subject(name=name)
        db.session.add(s)
        db.session.flush()
    return s


def get_or_create_course(title, **kwargs):
    c = Course.query.filter_by(title=title).first()
    if c:
        return c, False
    c = Course(title=title, **kwargs)
    db.session.add(c)
    db.session.flush()
    return c, True


def get_or_create_material_subject(course, name):
    s = MaterialSubject.query.filter_by(course_id=course.id, name=name).first()
    if not s:
        s = MaterialSubject(course_id=course.id, name=name, order_index=len(course.material_subjects))
        db.session.add(s)
        db.session.flush()
    return s


def get_or_create_chapter(subject, name):
    ch = MaterialChapter.query.filter_by(subject_id=subject.id, name=name).first()
    if not ch:
        ch = MaterialChapter(subject_id=subject.id, name=name, order_index=subject.chapters.count())
        db.session.add(ch)
        db.session.flush()
    return ch


def make_sample_pdf(path, title, body_lines):
    """A small, real, openable PDF - not a fake byte string - so the
    secure PDF.js viewer actually has something valid to render."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    c = pdf_canvas.Canvas(path, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(colors.HexColor("#1a237e"))
    c.drawString(20 * mm, height - 30 * mm, title)
    c.setFont("Helvetica", 11)
    c.setFillColor(colors.HexColor("#333333"))
    y = height - 45 * mm
    for line in body_lines:
        c.drawString(20 * mm, y, line)
        y -= 8 * mm
    c.setFont("Helvetica-Oblique", 9)
    c.setFillColor(colors.HexColor("#999999"))
    c.drawString(20 * mm, 15 * mm, "Sample demo content - Kalyani Exam Hub")
    c.showPage()
    c.save()


def add_material(course, chapter, title, kind, upload_folder, body_lines=None, video_url=None):
    existing = CourseMaterial.query.filter_by(course_id=course.id, chapter_id=chapter.id, title=title).first()
    if existing:
        return existing

    file_path = None
    if kind == "video":
        material_type = "video"
    else:
        material_type = kind
        folder = os.path.join(upload_folder, "course_materials")
        os.makedirs(folder, exist_ok=True)
        import uuid
        file_path = f"{uuid.uuid4().hex}.pdf"
        make_sample_pdf(os.path.join(folder, file_path), title, body_lines or ["Demo content."])

    m = CourseMaterial(
        course_id=course.id, chapter_id=chapter.id, title=title, material_type=material_type,
        file_path=file_path, video_url=video_url, access_duration_days=None,
        order_index=chapter.materials.count(),
    )
    db.session.add(m)
    db.session.flush()
    return m


def add_mock_exam(title, test_category, course, subject_names, admin_id, marks=4, negative=1, duration=60):
    existing = Exam.query.filter_by(title=title).first()
    if existing:
        return existing

    exam = Exam(
        title=title, description=f"Auto-generated demo {test_category} test.",
        duration_minutes=duration, passing_percentage=35,
        negative_marking_enabled=negative > 0, negative_marks_per_wrong=negative,
        test_category=test_category, course_id=course.id if course else None,
        created_by=admin_id, is_active=True,
    )
    db.session.add(exam)
    db.session.flush()

    sample_bank = {
        "Physics": [("Unit of electric current is:", ["Volt", "Ampere", "Ohm", "Watt"], 1)],
        "Chemistry": [("The pH of a neutral solution is:", ["0", "7", "14", "1"], 1)],
        "Botany": [("Photosynthesis occurs mainly in:", ["Roots", "Leaves", "Stem", "Flower"], 1)],
        "Zoology": [("The human heart has how many chambers?", ["2", "3", "4", "5"], 2)],
        "Maths": [("Value of sin(90 deg) is:", ["0", "1", "-1", "0.5"], 1)],
        "General Studies": [("Who is known as the Father of the Indian Constitution?", ["Mahatma Gandhi", "B. R. Ambedkar", "Jawaharlal Nehru", "Sardar Patel"], 1)],
        "Reasoning": [("Find the odd one out:", ["Circle", "Square", "Triangle", "Red"], 3)],
        "English": [("Choose the correctly spelled word:", ["Recieve", "Receive", "Receeve", "Receve"], 1)],
        "Current Affairs": [("This is a placeholder current-affairs question for demo purposes.", ["Option A", "Option B", "Option C", "Option D"], 0)],
    }

    for subj_name in subject_names:
        subj = get_or_create_subject(subj_name)
        for q_text, opts, correct_idx in sample_bank.get(subj_name, [("Sample question for " + subj_name, ["Option A", "Option B", "Option C", "Option D"], 0)]):
            q = Question(
                exam_id=exam.id, subject_id=subj.id, question_type="single_choice",
                question_text=q_text, marks=marks, negative_marks=negative,
            )
            db.session.add(q)
            db.session.flush()
            for i, opt_text in enumerate(opts):
                db.session.add(Option(question_id=q.id, option_text=opt_text, is_correct=(i == correct_idx), order_index=i))

    db.session.flush()
    exam.recompute_total_marks()
    db.session.add(ExamLink(exam_id=exam.id))
    db.session.commit()
    return exam


def run():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(role="admin").first()
        admin_id = admin.id if admin else None
        upload_folder = app.config["UPLOAD_FOLDER"]

        # ------------------------------------------------------------
        # Global exam-bank subjects
        # ------------------------------------------------------------
        for name in ["Physics", "Chemistry", "Botany", "Zoology", "Maths",
                     "General Studies", "Reasoning", "English", "Current Affairs"]:
            get_or_create_subject(name)
        db.session.commit()

        # ------------------------------------------------------------
        # Courses
        # ------------------------------------------------------------
        neet1, _ = get_or_create_course(
            "NEET 2026 — Complete Biology, Physics & Chemistry Batch",
            faculty="Dr. Lakshmi Prasanna", subjects="Physics, Chemistry, Botany, Zoology",
            duration_label="12 Months", duration_days=365, price=9999, discount_percent=25,
            description="Full NEET syllabus with daily practice, weekly tests and monthly grand tests.",
            features="180+ recorded video classes\nDaily practice questions\nWeekly & monthly tests\nAI performance analysis",
        )
        neet2, _ = get_or_create_course(
            "NEET Crash Course — 45 Day Rapid Revision",
            faculty="Dr. Arjun Rao", subjects="Physics, Chemistry, Biology",
            duration_label="45 Days", duration_days=45, price=1999, discount_percent=15,
            description="High-speed revision batch for the final stretch before NEET.",
            features="45-day structured plan\nDaily mock tests\nHigh-yield notes",
        )
        eamcet1, _ = get_or_create_course(
            "TS/AP EAMCET — Engineering Stream Complete Batch",
            faculty="Prof. Srinivas Chary", subjects="Maths, Physics, Chemistry",
            duration_label="10 Months", duration_days=300, price=8499, discount_percent=30,
            description="Complete EAMCET Engineering stream preparation with rank-focused practice.",
            features="Chapter-wise video classes\nPrevious 10-year papers\nWeekly rank tests",
        )
        eamcet2, _ = get_or_create_course(
            "EAMCET Agriculture & Medical Stream Batch",
            faculty="Dr. Swathi Reddy", subjects="Physics, Chemistry, Biology",
            duration_label="8 Months", duration_days=240, price=7499, discount_percent=20,
            description="Focused batch for EAMCET Agriculture & Medical stream aspirants.",
            features="Bilingual (English/Telugu) notes\nDaily quizzes\nMonthly grand test",
        )
        govt1, _ = get_or_create_course(
            "APPSC Group 1 — Complete Batch (Prelims + Mains)",
            faculty="Dr. Ramesh Kumar", subjects="Polity, History, Geography, Economy, General Studies",
            duration_label="12 Months", duration_days=365, price=11999, discount_percent=35,
            description="End-to-end APPSC Group 1 preparation, Prelims to Mains, with mentor support.",
            features="Prelims + Mains coverage\nDaily current affairs\nAnswer-writing practice",
        )
        govt2, _ = get_or_create_course(
            "SSC CGL / Bank PO — Govt Jobs Combo Batch",
            faculty="Kavya Sharma", subjects="Quantitative Aptitude, Reasoning, English, General Knowledge",
            duration_label="6 Months", duration_days=180, price=4999, discount_percent=25,
            description="Combined preparation batch for SSC CGL and Bank PO exams.",
            features="Daily speed-maths practice\nSectional mock tests\nInterview guidance",
        )
        db.session.commit()

        # ------------------------------------------------------------
        # Study materials: Course -> Subject -> Chapter -> Material
        # ------------------------------------------------------------
        material_plan = {
            neet1: [
                ("Physics", "Laws of Motion", "Newton's Laws — Notes", ["Newton's First Law: A body remains at rest...", "Newton's Second Law: F = ma", "Newton's Third Law: Action-reaction pairs"]),
                ("Botany", "Photosynthesis", "Photosynthesis — Notes", ["Light reactions occur in the thylakoid membrane.", "The Calvin cycle fixes CO2 into glucose.", "Chlorophyll a and b absorb different wavelengths."]),
            ],
            neet2: [
                ("Chemistry", "Chemical Bonding", "Chemical Bonding — Quick Notes", ["Ionic bonds form via electron transfer.", "Covalent bonds share electron pairs.", "Hybridization determines molecular geometry."]),
            ],
            eamcet1: [
                ("Maths", "Trigonometry", "Trigonometric Ratios — Notes", ["sin, cos and tan are the primary ratios.", "Key identities: sin^2(x) + cos^2(x) = 1", "Unit circle approach simplifies problem-solving."]),
                ("Physics", "Kinematics", "Kinematics — Notes", ["Equations of motion: v = u + at, s = ut + 1/2at^2", "Projectile motion combines horizontal and vertical components."]),
            ],
            eamcet2: [
                ("Biology", "Human Physiology", "Human Physiology — Notes", ["The digestive system breaks down food into nutrients.", "The circulatory system transports oxygen and nutrients."]),
            ],
            govt1: [
                ("Polity", "Fundamental Rights", "Fundamental Rights — Notes", ["Articles 12-35 of the Indian Constitution.", "Right to Equality, Right to Freedom, Right against Exploitation.", "Right to Constitutional Remedies — Article 32."]),
                ("History", "Modern History", "Modern Indian History — Notes", ["The Revolt of 1857 was a major turning point.", "Indian National Congress was founded in 1885.", "Quit India Movement launched in 1942."]),
            ],
            govt2: [
                ("Quantitative Aptitude", "Percentages", "Percentages — Notes", ["Percentage = (Value / Total) x 100", "Common shortcuts for percentage-to-fraction conversion."]),
                ("Reasoning", "Blood Relations", "Blood Relations — Notes", ["Approach every blood-relation question with a family tree diagram.", "Watch for gender-neutral terms like 'parent' or 'sibling'."]),
            ],
        }

        for course, entries in material_plan.items():
            for subj_name, chapter_name, note_title, body_lines in entries:
                subj = get_or_create_material_subject(course, subj_name)
                chapter = get_or_create_chapter(subj, chapter_name)
                add_material(course, chapter, note_title, "notes", upload_folder, body_lines=body_lines)
                add_material(
                    course, chapter, f"{chapter_name} — Video Class", "video", upload_folder,
                    video_url="https://www.youtube.com/embed/dQw4w9WgXcQ",
                )
        db.session.commit()

        # ------------------------------------------------------------
        # Mock tests: Daily / Weekly / Monthly Grand Test per category
        # ------------------------------------------------------------
        add_mock_exam("NEET Daily Biology Quiz", "daily", neet1, ["Botany", "Zoology"], admin_id, marks=4, negative=1, duration=20)
        add_mock_exam("NEET Weekly Physics & Chemistry Test", "weekly", neet1, ["Physics", "Chemistry"], admin_id, marks=4, negative=1, duration=60)
        add_mock_exam("NEET Monthly Grand Test", "monthly", None, ["Physics", "Chemistry", "Botany", "Zoology"], admin_id, marks=4, negative=1, duration=180)

        add_mock_exam("EAMCET Daily Maths Quiz", "daily", eamcet1, ["Maths"], admin_id, marks=1, negative=0, duration=20)
        add_mock_exam("EAMCET Weekly Physics Test", "weekly", eamcet1, ["Physics", "Maths"], admin_id, marks=1, negative=0, duration=60)
        add_mock_exam("EAMCET Monthly Grand Test", "monthly", None, ["Maths", "Physics", "Chemistry"], admin_id, marks=1, negative=0, duration=150)

        add_mock_exam("Govt Jobs Daily GK Quiz", "daily", govt1, ["General Studies", "Current Affairs"], admin_id, marks=1, negative=0.25, duration=15)
        add_mock_exam("Govt Jobs Weekly Polity & Reasoning Test", "weekly", govt2, ["Reasoning", "General Studies"], admin_id, marks=1, negative=0.25, duration=45)
        add_mock_exam("Govt Jobs Monthly Grand Test", "monthly", None, ["General Studies", "Reasoning", "English", "Current Affairs"], admin_id, marks=1, negative=0.25, duration=120)

        print("Demo data seeded successfully:")
        print(f"  Courses: {Course.query.count()}")
        print(f"  Material subjects: {MaterialSubject.query.count()}")
        print(f"  Chapters: {MaterialChapter.query.count()}")
        print(f"  Materials: {CourseMaterial.query.count()}")
        print(f"  Exams: {Exam.query.count()}")


if __name__ == "__main__":
    run()
