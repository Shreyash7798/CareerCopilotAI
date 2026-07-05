from pathlib import Path

from docx import Document

from app.cv_parser import parse_cv

SAMPLE = """Rahul Sharma
Mumbai, India | rahul.sharma@example.com | +91 98765 43210

Summary
Management consultant with 3 years of experience in business transformation,
operations consulting and supply chain across manufacturing clients.

Work Experience
PwC India
Senior Associate, Consulting
Jan 2023 - Present
- Led process improvement and cost optimization for manufacturing clients
- Built financial modeling and business case for supply chain redesign

Knest Manufacturers LLP
Analyst
Jun 2021 - Dec 2022
- Managed procurement and inventory management initiatives

Skills
Excel, PowerPoint, Power BI, SQL, Project Management, Stakeholder Management
"""


def _make_docx(tmp_path: Path) -> Path:
    doc = Document()
    for line in SAMPLE.splitlines():
        doc.add_paragraph(line)
    path = tmp_path / "cv.docx"
    doc.save(str(path))
    return path


def test_parse_cv_txt(tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    parsed = parse_cv(path)
    assert parsed["full_name"] == "Rahul Sharma"
    assert parsed["email"] == "rahul.sharma@example.com"
    assert parsed["experience_years"] == 3.0
    assert "Excel" in parsed["skills"]
    assert "Supply Chain" in parsed["skills"]
    assert any("PwC" in e or "Knest" in e for e in parsed["employers"])


def test_parse_cv_docx(tmp_path):
    path = _make_docx(tmp_path)
    parsed = parse_cv(path)
    assert parsed["email"] == "rahul.sharma@example.com"
    assert parsed["experience_years"] == 3.0
    assert parsed["skills"]
