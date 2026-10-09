#!/usr/bin/env python3
"""统一启动本地电影推荐系统的后端、前端或全部服务。"""

# 只释放本项目占用的 8000 和 3000 端口
# python3 run.py stop

# 释放端口后重新启动前后端
# python3 run.py restart
import argparse
import os
import signal
import shutil
import socket
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
ML_VENV_PYTHON = ROOT / ".venv-ml" / "bin" / "python"
LOCAL_NODE_BIN = ROOT / ".tools" / "node-v22.23.2-darwin-arm64" / "bin"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def listener_pids(port: int) -> list[int]:
    """Return local listener PIDs without matching outbound connections."""
    lsof = shutil.which("lsof")
    if not lsof:
        raise SystemExit("未找到 lsof，无法自动识别端口进程。")
    result = subprocess.run(
        [lsof, "-tiTCP:" + str(port), "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise SystemExit(f"检查端口 {port} 失败：{result.stderr.strip() or 'lsof 错误'}")
    return sorted({int(line) for line in result.stdout.splitlines() if line.strip().isdigit()})


def process_working_directory(pid: int) -> Path | None:
    """Read a process cwd so unrelated applications are never terminated."""
    lsof = shutil.which("lsof")
    if not lsof:
        return None
    result = subprocess.run(
        [lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            try:
                return Path(line[1:]).resolve()
            except OSError:
                return None
    return None


def is_project_process(pid: int) -> tuple[bool, Path | None]:
    cwd = process_working_directory(pid)
    if cwd is None:
        return False, None
    try:
        cwd.relative_to(ROOT)
        return True, cwd
    except ValueError:
        return False, cwd


def stop_project_services(backend_port: int, frontend_port: int) -> None:
    ports = (("后端", backend_port), ("前端", frontend_port))
    targets: dict[int, tuple[str, int, Path]] = {}
    skipped: list[str] = []

    for name, port in ports:
        for pid in listener_pids(port):
            allowed, cwd = is_project_process(pid)
            if allowed and cwd is not None:
                targets[pid] = (name, port, cwd)
            else:
                location = str(cwd) if cwd else "无法读取工作目录"
                skipped.append(f"{name}端口 {port}：PID {pid}（{location}）")

    if skipped:
        details = "\n".join(f"- {item}" for item in skipped)
        raise SystemExit(
            "以下端口由非本项目进程占用，为避免误杀已停止操作：\n"
            f"{details}\n请手动确认后关闭对应程序。"
        )

    if not targets:
        print(f"端口 {backend_port} 和 {frontend_port} 均已释放，无需处理。")
        return

    for pid, (name, port, cwd) in targets.items():
        print(f"正在停止{name}进程 PID {pid}（端口 {port}，目录 {cwd}）…")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(port_in_use(port) for _, port in ports):
            print(f"后端端口 {backend_port}、前端端口 {frontend_port} 已释放。")
            return
        time.sleep(0.2)

    still_occupied = [str(port) for _, port in ports if port_in_use(port)]
    raise SystemExit(
        f"端口 {', '.join(still_occupied)} 在 5 秒后仍未释放，请检查对应进程。"
    )


def frontend_environment(port: int, backend_port: int) -> tuple[dict[str, str], str]:
    env = os.environ.copy()
    if LOCAL_NODE_BIN.exists():
        env["PATH"] = f"{LOCAL_NODE_BIN}{os.pathsep}{env.get('PATH', '')}"
    npm = shutil.which("npm", path=env.get("PATH"))
    if not npm:
        raise SystemExit("未找到 npm，请保留项目的 .tools 目录或安装 Node.js。")
    env.update(
        BROWSER="none",
        HOST="127.0.0.1",
        PORT=str(port),
        REACT_APP_API_URL=f"http://localhost:{backend_port}",
    )
    return env, npm


def backend_command(port: int, reload: bool) -> list[str]:
    python = ML_VENV_PYTHON if ML_VENV_PYTHON.exists() else VENV_PYTHON
    if not python.exists():
        raise SystemExit("未找到 .venv，请先按 README 安装 Python 依赖。")
    command = [
        str(python),
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if reload:
        command.append("--reload")
    return command


def frontend_command(port: int, backend_port: int) -> tuple[list[str], dict[str, str]]:
    env, npm = frontend_environment(port, backend_port)
    if not (FRONTEND / "node_modules").exists():
        subprocess.run([npm, "install"], cwd=FRONTEND, env=env, check=True)
    return [npm, "start"], env


def ensure_ports(mode: str, backend_port: int, frontend_port: int) -> None:
    ports = []
    if mode in {"backend", "all"}:
        ports.append(("后端", backend_port))
    if mode in {"frontend", "all"}:
        ports.append(("前端", frontend_port))
    occupied = [f"{name}端口 {port}" for name, port in ports if port_in_use(port)]
    if occupied:
        raise SystemExit(
            f"{'、'.join(occupied)} 已被占用。\n"
            "如果是本项目上次残留的进程，请执行：python3 run.py stop\n"
            "也可以执行：python3 run.py restart"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 TMDB 智能电影推荐系统")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("all", "backend", "frontend", "stop", "restart"),
        default="all",
    )
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--reload", action="store_true", help="后端代码改动时自动重载")
    args = parser.parse_args()

    if args.mode == "stop":
        stop_project_services(args.backend_port, args.frontend_port)
        return
    if args.mode == "restart":
        stop_project_services(args.backend_port, args.frontend_port)
        args.mode = "all"

    ensure_ports(args.mode, args.backend_port, args.frontend_port)
    if args.mode == "backend":
        backend = backend_command(args.backend_port, args.reload)
        subprocess.run(backend, cwd=ROOT, check=False)
        return
    if args.mode == "frontend":
        frontend, frontend_env = frontend_command(args.frontend_port, args.backend_port)
        subprocess.run(frontend, cwd=FRONTEND, env=frontend_env, check=False)
        return

    backend = backend_command(args.backend_port, args.reload)
    frontend, frontend_env = frontend_command(args.frontend_port, args.backend_port)
    print(f"后端：http://127.0.0.1:{args.backend_port}")
    print(f"前端：http://localhost:{args.frontend_port}")
    processes = [
        subprocess.Popen(backend, cwd=ROOT),
        subprocess.Popen(frontend, cwd=FRONTEND, env=frontend_env),
    ]
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
