"""Exercise both native runtimes together in fresh Windows/Linux processes."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize("sherpa_first", [True, False])
def test_native_runtime_import_order(sherpa_first):
    imports = ["import sherpa_onnx.lib._sherpa_onnx", "import onnxruntime"]
    if not sherpa_first:
        imports.reverse()
    code = "\n".join(imports + [
        "providers = onnxruntime.get_available_providers()",
        "assert 'CPUExecutionProvider' in providers, providers",
        "print(providers)",
    ])
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CPUExecutionProvider" in result.stdout
