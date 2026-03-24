"""Tests for interview question generation and plan regeneration."""
from agent_baton.models.pmo import InterviewQuestion, InterviewAnswer


def test_interview_question_to_dict():
    q = InterviewQuestion(
        id="q1",
        question="What testing strategy?",
        context="Plan has no test phase",
        answer_type="choice",
        choices=["unit", "integration", "both"],
    )
    d = q.to_dict()
    assert d["id"] == "q1"
    assert d["answer_type"] == "choice"
    assert d["choices"] == ["unit", "integration", "both"]


def test_interview_question_from_dict():
    d = {"id": "q2", "question": "Timeout?", "context": "Long task", "answer_type": "text"}
    q = InterviewQuestion.from_dict(d)
    assert q.id == "q2"
    assert q.choices is None


def test_interview_answer_to_dict():
    a = InterviewAnswer(question_id="q1", answer="both")
    d = a.to_dict()
    assert d == {"question_id": "q1", "answer": "both"}


def test_interview_answer_from_dict():
    d = {"question_id": "q1", "answer": "both"}
    a = InterviewAnswer.from_dict(d)
    assert a.question_id == "q1"
    assert a.answer == "both"


def test_interview_question_roundtrip():
    q = InterviewQuestion(
        id="q1", question="Testing?", context="No tests",
        answer_type="choice", choices=["unit", "e2e"],
    )
    assert InterviewQuestion.from_dict(q.to_dict()).to_dict() == q.to_dict()


def test_interview_question_to_dict_omits_none_choices():
    q = InterviewQuestion(id="q1", question="What?", context="ctx", answer_type="text")
    d = q.to_dict()
    assert "choices" not in d


def test_interview_question_to_dict_includes_empty_choices():
    q = InterviewQuestion(id="q1", question="What?", context="ctx", answer_type="choice", choices=[])
    d = q.to_dict()
    assert d["choices"] == []


def test_interview_answer_roundtrip():
    a = InterviewAnswer(question_id="q1", answer="yes")
    assert InterviewAnswer.from_dict(a.to_dict()).to_dict() == a.to_dict()
