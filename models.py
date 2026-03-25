from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Group(db.Model):
    __tablename__ = 'groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    runners = db.relationship('Runner', backref='group', order_by='Runner.run_order')


class Runner(db.Model):
    __tablename__ = 'runners'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    knox_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False)
    run_order = db.Column(db.Integer, nullable=False)
    password = db.Column(db.String(20), nullable=False)

    problem_text = db.Column(db.Text, nullable=False)
    problem_type = db.Column(db.String(5), nullable=False)
    correct_answer = db.Column(db.String(200), nullable=False)

    status = db.Column(db.String(20), default='waiting')  # waiting|active|completed|skipped
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    attempts = db.Column(db.Integer, default=0)
    submitted_answer = db.Column(db.String(200), nullable=True)
    next_runner_password = db.Column(db.String(20), nullable=True)


class AttemptLog(db.Model):
    __tablename__ = 'attempt_logs'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    runner_id = db.Column(db.Integer, db.ForeignKey('runners.id'), nullable=False)
    submitted_answer = db.Column(db.String(200))
    is_correct = db.Column(db.Boolean)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
