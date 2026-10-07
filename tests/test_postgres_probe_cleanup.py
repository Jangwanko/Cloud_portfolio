"""Exercise the actual probe wrapper with a synthetic NSS file creator."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "k8s/values/postgresql-lite-probe-patch.json"

@pytest.mark.parametrize("exit_code", [0, 7])
def test_probe_cleanup_preserves_result_and_other_files(tmp_path, exit_code):
    bash = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
    if not bash or not Path(bash).exists():
        pytest.skip("Bash required for probe execution")
    probe = json.loads(PATCH.read_text())["spec"]["template"]["spec"]["containers"][0]
    script = probe["livenessProbe"]["exec"]["command"][2]
    values = (ROOT / "k8s/values/postgresql-lite-values.yaml").read_text()
    for line in script.strip().splitlines():
        assert line in values
    assert probe["readinessProbe"]["exec"]["command"][2] == script
    # Keep the test's own temporary directories within pytest's isolated fixture.
    script = script.replace("/tmp/pg-probe.XXXXXXXXXX", "./pg-probe.XXXXXXXXXX")
    fixture = tmp_path / "probe.sh"
    fixture.write_text('printf passwd > "$(mktemp)"\nprintf group > "$(mktemp)"\nexit '+str(exit_code)+'\n')
    sentinel = tmp_path / "unrelated"
    sentinel.write_text("keep")
    for _ in range(3):
        result = subprocess.run([bash, "-ec", script, "pg-probe", "probe.sh"], cwd=tmp_path)
        assert result.returncode == exit_code
        assert not list(tmp_path.glob("pg-probe.*"))
        assert sentinel.read_text() == "keep"
