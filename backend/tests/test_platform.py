from fastapi.testclient import TestClient

from qa_intelligence import store
from qa_intelligence.main import app


def test_project_requirement_generation_and_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    with TestClient(app) as client:
        project_root = tmp_path / "repo"
        project_root.mkdir()
        created = client.post("/api/projects", json={"name": "demo", "root": str(project_root)})
        assert created.status_code == 201
        project_id = created.json()["id"]

        req = client.post(f"/api/projects/{project_id}/requirements", json={
            "title": "Inicio de sesión", "body": "El usuario debe iniciar sesión con credenciales válidas."
        })
        assert req.status_code == 201
        requirement_id = req.json()["id"]
        analysis = client.post(f"/api/projects/{project_id}/requirements/{requirement_id}/analyze")
        assert analysis.status_code == 200
        assert analysis.json()["executed"] is False

        generated = client.post(f"/api/projects/{project_id}/requirements/{requirement_id}/generate-cases")
        assert generated.status_code == 201
        assert generated.json()["generated"] == 3

        report = client.get(f"/api/projects/{project_id}/report.html")
        assert report.status_code == 200
        assert "Informe de calidad" in report.text

        overview = client.get("/api/overview").json()
        assert overview["counts"]["test_cases"] == 3


def test_local_runner_records_real_pytest_and_junit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "runner.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    with TestClient(app) as client:
        project_root = tmp_path / "test-repo"
        project_root.mkdir()
        (project_root / "test_sample.py").write_text("def test_sample():\n    assert 2 + 2 == 4\n", encoding="utf-8")
        project_id = client.post("/api/projects", json={"name": "runner", "root": str(project_root)}).json()["id"]

        result = client.post(f"/api/projects/{project_id}/runs")
        assert result.status_code == 201
        assert result.json()["status"] == "passed"
        assert "1 passed" in result.json()["output"]

        junit = client.get(f"/api/projects/{project_id}/runs/{result.json()['id']}/junit.xml")
        assert junit.status_code == 200
        assert "testsuite" in junit.text


def test_project_path_must_exist_and_html_is_escaped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "validation.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    with TestClient(app) as client:
        rejected = client.post("/api/projects", json={"name": "missing", "root": str(tmp_path / "absent")})
        assert rejected.status_code == 400

        root = tmp_path / "repo"
        root.mkdir()
        project_id = client.post("/api/projects", json={"name": "<script>alert(1)</script>", "root": str(root)}).json()["id"]
        page = client.get(f"/api/projects/{project_id}/report.html")
        assert "&lt;script&gt;" in page.text
        assert "<script>alert(1)</script>" not in page.text


def test_chat_is_local_persisted_and_never_claims_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "chat.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    with TestClient(app) as client:
        root = tmp_path / "repo"
        root.mkdir()
        project_id = client.post("/api/projects", json={"name": "chat", "root": str(root)}).json()["id"]
        answer = client.post(f"/api/projects/{project_id}/chat", json={"message": "¿Ejecutaste pytest?"})
        assert answer.status_code == 201
        assert answer.json()["executed"] is False
        assert "No he ejecutado" in answer.json()["answer"]
        advice = client.post(f"/api/projects/{project_id}/chat", json={"message": "¿Qué casos de prueba recomiendas?"})
        assert advice.json()["executed"] is False
        assert "Puedo proponer escenarios" in advice.json()["answer"]
        history = client.get(f"/api/projects/{project_id}/chat").json()
        assert [entry["role"] for entry in history] == ["user", "assistant", "user", "assistant"]


def test_security_scan_returns_pattern_locations_without_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "security.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    with TestClient(app) as client:
        root = tmp_path / "secure-repo"
        root.mkdir()
        (root / ".env").write_text("AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF\n", encoding="utf-8")
        project_id = client.post("/api/projects", json={"name": "security", "root": str(root)}).json()["id"]
        response = client.post(f"/api/projects/{project_id}/security-scan")
        assert response.status_code == 200
        report = response.json()
        assert report["executed"] is False
        assert report["finding_count"] == 1
        assert report["findings"][0]["rule"] == "aws-access-key"
        assert "AKIA1234567890ABCDEF" not in response.text
