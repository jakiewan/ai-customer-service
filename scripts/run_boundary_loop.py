"""对真实运行服务重复执行边界测试，任何一轮失败立即停止。"""

import os
import re
import subprocess
import sys

ROUNDS = 3


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root}
    all_cases = 0
    for round_number in range(1, ROUNDS + 1):
        result = subprocess.run(
            [sys.executable, "scripts/live_boundary_e2e.py"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"LIVE_BOUNDARY_OK cases=(\d+)", output)
        cases = int(match.group(1)) if match else 0
        all_cases += cases
        print(f"ROUND={round_number} EXIT={result.returncode} CASES={cases}")
        if result.returncode != 0:
            print(output)
            return result.returncode
    print(f"BOUNDARY_LOOP_OK rounds={ROUNDS} cases_per_round={all_cases // ROUNDS} total_executions={all_cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
