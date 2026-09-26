import click
from app.extensions import db
from app.models import User, Exam, Subject, Question, Option, ExamLink


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(name, email, password):
        """Create an administrator account: flask create-admin"""
        if User.query.filter_by(email=email.lower()).first():
            click.echo("A user with that email already exists.")
            return
        user = User(name=name, email=email.lower(), role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin '{email}' created successfully.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create a demo subject, exam, questions, and a public link."""
        if Exam.query.filter_by(title="Demo General Knowledge Test").first():
            click.echo("Demo exam already exists.")
            return

        subject = Subject(name="General Knowledge", description="Demo subject")
        db.session.add(subject)
        db.session.flush()

        exam = Exam(
            title="Demo General Knowledge Test",
            description="A short demo exam covering general knowledge.",
            duration_minutes=15,
            passing_percentage=40,
            negative_marking_enabled=True,
            negative_marks_per_wrong=0.25,
            instructions="This exam has 5 questions. Do not refresh the page. "
                         "Do not switch tabs. Timer will auto-submit your exam.",
            created_by=None,
        )
        db.session.add(exam)
        db.session.flush()

        questions = [
            dict(
                question_type="single_choice",
                question_text="What is the capital of France?",
                marks=1, negative_marks=0.25,
                options=[("Paris", True), ("Berlin", False), ("Rome", False), ("Madrid", False)],
            ),
            dict(
                question_type="multiple_choice",
                question_text="Which of the following are prime numbers?",
                marks=2, negative_marks=0.5,
                options=[("2", True), ("3", True), ("4", False), ("9", False)],
            ),
            dict(
                question_type="true_false",
                question_text="The sun rises in the West.",
                marks=1, negative_marks=0.25,
                options=[("True", False), ("False", True)],
            ),
            dict(
                question_type="integer",
                question_text="What is 12 x 8?",
                marks=2, negative_marks=0,
                correct_integer_answer=96,
            ),
            dict(
                question_type="fill_blank",
                question_text="The largest planet in our solar system is ______.",
                marks=2, negative_marks=0,
                correct_text_answer="Jupiter",
            ),
        ]

        for idx, q in enumerate(questions):
            question = Question(
                exam_id=exam.id,
                subject_id=subject.id,
                question_type=q["question_type"],
                question_text=q["question_text"],
                marks=q["marks"],
                negative_marks=q.get("negative_marks", 0),
                correct_integer_answer=q.get("correct_integer_answer"),
                correct_text_answer=q.get("correct_text_answer"),
                explanation="Auto-generated demo explanation.",
                order_index=idx,
            )
            db.session.add(question)
            db.session.flush()
            for oidx, (text, is_correct) in enumerate(q.get("options", [])):
                db.session.add(Option(
                    question_id=question.id, option_text=text,
                    is_correct=is_correct, order_index=oidx,
                ))

        exam.recompute_total_marks()

        link = ExamLink(exam_id=exam.id)
        db.session.add(link)
        db.session.commit()

        click.echo(f"Demo exam created. Public link token: {link.token}")
        click.echo(f"Visit: /exam/{link.token}")
