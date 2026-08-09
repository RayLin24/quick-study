"""Docker 沙箱（design.md 4.5 / ADR-005）。

- 镜像预装依赖（构建期联网），Demo 运行时 --network none（默认禁网）
- 资源限额：内存/CPU/进程数；只读挂载 + 临时 /tmp
- 通过标准：退出码 0 且 stdout 非空 且 stdout_expect 模式全部命中
  （"退出码0 + 空输出"判失败——防静默空转）
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_PYTHON = "quickstudy-sandbox-python:3.11"
IMAGES = {"python": IMAGE_PYTHON}


@dataclass
class RunResult:
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    error: str = ""

    def passed(self, stdout_expect: list[str] | None = None) -> tuple[bool, str]:
        if self.timed_out:
            return False, "执行超时"
        if self.error:
            return False, self.error
        if self.exit_code != 0:
            return False, f"退出码 {self.exit_code}"
        if not self.stdout.strip():
            return False, "退出码 0 但无任何输出（疑似静默空转）"
        for pat in (stdout_expect or []):
            if pat not in self.stdout:
                return False, f"输出缺少预期模式: {pat!r}"
        return True, ""


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=15, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_image(tech: str = "python") -> str:
    """镜像不存在则按 demo/images/{tech}/Dockerfile 构建（构建期联网，此后离线复用）。"""
    image = IMAGES[tech]
    check = subprocess.run(["docker", "image", "inspect", image],
                           capture_output=True, timeout=30)
    if check.returncode == 0:
        return image
    dockerfile_dir = Path(__file__).parent / "images" / tech
    log.info("构建沙箱镜像 %s（仅构建期联网）…", image)
    build = subprocess.run(["docker", "build", "-q", "-t", image, str(dockerfile_dir)],
                           capture_output=True, text=True, timeout=1800)
    if build.returncode != 0:
        raise RuntimeError(f"沙箱镜像构建失败: {build.stderr[-500:]}")
    return image


def run_demo(demo_dir: Path, command: str, tech: str = "python",
             timeout_s: int = 120) -> RunResult:
    """在禁网沙箱中运行 Demo。demo_dir 只读挂载到 /work。"""
    image = ensure_image(tech)
    cmd = ["docker", "run", "--rm",
           "--network", "none",
           "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
           "--read-only", "--tmpfs", "/tmp:rw,noexec,size=64m",
           "-e", "PYTHONDONTWRITEBYTECODE=1",
           "-e", "PYTEST_ADDOPTS=-p no:cacheprovider",
           "-v", f"{Path(demo_dir).resolve()}:/work:ro",
           "-w", "/work", image,
           "sh", "-c", command]
    import time

    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, encoding="utf-8", errors="replace")
        return RunResult(exit_code=proc.returncode, stdout=proc.stdout,
                         stderr=proc.stderr, duration_s=time.monotonic() - t0)
    except subprocess.TimeoutExpired as e:
        return RunResult(stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
                         stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
                         duration_s=time.monotonic() - t0, timed_out=True)
