import subprocess
import sys
import zipfile
from pathlib import Path


def test_build_creates_distribution_with_config_and_run_scripts():
    project = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(project / "config" / "build.py")],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    archive = project / "dist" / "jira-analytics-1.1.0.zip"
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        requirements = next(name for name in names if name.endswith("/requirements.txt"))
        assert "jira==" in zf.read(requirements).decode("utf-8")
    assert "jira-analytics-1.1.0/config.json" in names
    assert "jira-analytics-1.1.0/config/autorun.bat" in names
    assert "jira-analytics-1.1.0/config/autorun.sh" in names
    assert "jira-analytics-1.1.0/libs/utils/analytics.py" in names
    assert "jira-analytics-1.1.0/libs/utils/client.py" in names
