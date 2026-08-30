"""自主回归循环：重复执行同一组功能与 RAG 用例，捕获偶发失败。"""

import os
import re
import subprocess
import sys

ROUNDS = 3


def main() -> int:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": project_root}
    round_results: list[int] = []
    for round_number in range(1, ROUNDS + 1):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "agent_service/tests"],
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"(\d+) passed", output)
        passed = int(match.group(1)) if match else 0
        round_results.append(passed)
        print(f"ROUND={round_number} EXIT={result.returncode} PASSED={passed}")
        if result.returncode != 0:
            print(output)
            return result.returncode
    if len(set(round_results)) != 1 or round_results[0] < 100:
        print(f"REGRESSION_LOOP_FAILED results={round_results}")
        return 1
    print(f"REGRESSION_LOOP_OK rounds={ROUNDS} cases_per_round={round_results[0]} total_executions={sum(round_results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
