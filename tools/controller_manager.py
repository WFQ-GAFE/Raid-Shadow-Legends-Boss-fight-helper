"""Controller lifecycle, cancellation, bounded logs and worker telemetry."""
from __future__ import annotations
import copy
import json
import locale
import os
import secrets
import re
import hashlib
import sys
from functools import lru_cache
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from agent_ipc import AgentIpc, reload_block_reason
from boss_modes import MODE_SPECS, normalize_mode, mode_spec
from chimera_runtime import AGENT, PROJECT_ROOT, RESOURCE_ROOT as BUNDLE_ROOT, USER_STRATEGY, worker_command, require_expected_account
from controller_pause import ControllerPauseEvent, signal_controller_pause
from inject_probe import seed_battle_context, seed_selection_context
from strategy_storage import atomic_write_json, revision
from decision_journal import DecisionJournal
from desktop_lifecycle import remove_session_file

class LaunchCancelled(Exception):
    pass

def utf8_subprocess_environment() -> dict[str, str]:
    """Keep Python worker output decodable regardless of Windows code page."""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def decode_worker_output(value: bytes | str) -> str:
    """Decode packaged worker output without destroying legacy CP936 bytes."""
    if isinstance(value, str):
        return value
    encodings = ("utf-8-sig", locale.getpreferredencoding(False), "gb18030")
    attempted: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in attempted:
            continue
        attempted.add(normalized)
        try:
            return value.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def controller_exit_label(code: int, stop_requested: bool) -> str:
    if code == 6:
        return "安全中断"
    if code == 7:
        return "已免费重整"
    if code == 8:
        return "游戏内暂停"
    if code == 0 and stop_requested:
        return "已暂停"
    if code == 0:
        return "已完成"
    return f"异常停止（代码 {code}）"


def diagnostic_console_text(line: str) -> str:
    # Keep user-facing explanations, excluding account binding and raw guard
    # dumps. Structured whitelisted records carry the decision inputs instead.
    if "账户" in line or "玩家 ID" in line:
        return "[账户相关提示已省略]"
    line = line.split("；校验详情：", 1)[0]
    line = re.sub(r"0x[0-9a-fA-F]{6,}", "[address]", line)
    line = re.sub(r"(?i)(['\"]?(?:context|pointer|generator|modePtr|skillDataPtr|token|password|secret|authorization|cookie)['\"]?\s*[:=]\s*)(?:['\"][^'\"]*['\"]|[^,;\s}]+)", r"\1[redacted]", line)
    return line[:12000] + (" [truncated]" if len(line) > 12000 else "")


@lru_cache(maxsize=1)
def diagnostic_build_identity() -> dict:
    result = {}
    try:
        result['version'] = (BUNDLE_ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        result['agentSha256'] = hashlib.sha256(AGENT.read_bytes()).hexdigest()
        if getattr(sys, 'frozen', False):
            result['executableSha256'] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    except OSError:
        result['someBuildFieldsUnavailable'] = True
    return result


class ControllerManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[Any] | None = None
        self.preparation_process: subprocess.Popen[Any] | None = None
        self.pid: int | None = None
        self.boss_mode = "chimera"
        self.status = "已停止"
        self.error: str | None = None
        self.logs_by_mode: dict[str, deque[str]] = {
            mode: deque(maxlen=800) for mode in MODE_SPECS
        }
        self.stop_requested = False
        self.preparing = False
        self.closing = False
        self.pause_event: ControllerPauseEvent | None = None
        self.pause_token = ""
        self.worker_thread: threading.Thread | None = None
        self.config_path: Path | None = None
        self.running_revision: str | None = None
        self.strategy_id: str | None = None
        self.telemetry: dict[str, dict[str, Any]] = {mode: {} for mode in MODE_SPECS}
        self.log_session = secrets.token_hex(8)
        self.log_sequences = {mode: 0 for mode in MODE_SPECS}
        self.log_epochs = {mode: 0 for mode in MODE_SPECS}
        self.journal = DecisionJournal(PROJECT_ROOT / "logs")
        self.critical_journal = DecisionJournal(PROJECT_ROOT / "logs" / "critical", max_bytes=2 * 1024 * 1024, keep_files=16)
        self.diagnostic_run: str | None = None
        self.decision_numbers = {mode: 0 for mode in MODE_SPECS}

    def record_diagnostic(self, event: str, mode: str, **fields: Any) -> None:
        was_disabled = self.journal.disabled
        written = self.journal.write(event, bossMode=mode, run=self.diagnostic_run,
                                     strategyId=self.strategy_id, runningRevision=self.running_revision,
                                     decisionNumber=self.decision_numbers[mode], **fields)
        important_console = event == "console" and any(word in fields.get("message", "")
            for word in ("结算", "重整", "暂停", "停止", "失败", "退出", "异常"))
        if event in {"start", "stop", "lifecycle"} or important_console:
            critical_was_disabled = self.critical_journal.disabled
            critical_written = self.critical_journal.write(event, bossMode=mode, run=self.diagnostic_run,
                strategyId=self.strategy_id, runningRevision=self.running_revision,
                decisionNumber=self.decision_numbers[mode], **fields)
            if not critical_written and not critical_was_disabled and written:
                self.logs_by_mode[mode].append("关键事件日志无法保存，请检查日志目录；普通诊断日志仍可用。")
                self.log_sequences[mode] += 1
        if not written and not was_disabled:
            self.logs_by_mode[mode].append(f"出手诊断无法保存：{self.journal.error}；本次仍可查看窗口日志。")
            self.log_sequences[mode] += 1

    def append(self, line: str, boss_mode: str | None = None) -> None:
        line = str(line).rstrip()
        if not line:
            return
        with self.lock:
            mode = normalize_mode(boss_mode or self.boss_mode)
            if line.startswith("@@raid-telemetry "):
                try:
                    value = json.loads(line[len("@@raid-telemetry "):])
                    if isinstance(value, dict):
                        self.telemetry[mode].update(value)
                        if isinstance(value.get("decision"), dict):
                            self.decision_numbers[mode] += 1
                            self.record_diagnostic("decision", mode, decision=value["decision"])
                        if isinstance(value.get("command"), dict):
                            self.record_diagnostic("command", mode, command=value["command"])
                        if isinstance(value.get("lifecycle"), dict):
                            self.record_diagnostic("lifecycle", mode, lifecycle=value["lifecycle"])
                        return
                except ValueError:
                    pass
            self.logs_by_mode[mode].append(line)
            self.log_sequences[mode] += 1
            self.record_diagnostic("console", mode, message=diagnostic_console_text(line))

    def clear_logs(self, boss_mode: str) -> None:
        with self.lock:
            mode = normalize_mode(boss_mode)
            self.logs_by_mode[mode].clear()
            self.log_epochs[mode] += 1

    def snapshot(self, log_mode: str | None = None, after: str | None = None) -> dict[str, Any]:
        with self.lock:
            selected_log_mode = normalize_mode(log_mode or self.boss_mode)
            running = self.preparing or (
                self.process is not None and self.process.poll() is None
            )
            sequence = self.log_sequences[selected_log_mode]
            prefix = f"{self.log_session}:{selected_log_mode}:{self.log_epochs[selected_log_mode]}:"
            logs = list(self.logs_by_mode[selected_log_mode])
            reset = True
            if isinstance(after, str) and after.startswith(prefix):
                try:
                    previous = int(after[len(prefix):])
                    if sequence - len(logs) <= previous <= sequence:
                        logs = logs[len(logs) - (sequence - previous):] if sequence > previous else []
                        reset = False
                except ValueError:
                    pass
            return {
                "running": running,
                "status": self.status,
                "pid": self.pid,
                "bossMode": self.boss_mode,
                "logMode": selected_log_mode,
                "logs": logs,
                "logsReset": reset,
                "logCursor": prefix + str(sequence),
                "runningRevision": self.running_revision,
                "strategyId": self.strategy_id,
                "telemetry": copy.deepcopy(self.telemetry[selected_log_mode]),
                "error": self.error,
                "diagnosticPath": str(self.journal.path) if self.journal.path else None,
                "diagnosticError": self.journal.error,
                "criticalDiagnosticPath": str(self.critical_journal.path) if self.critical_journal.path else None,
                "criticalDiagnosticError": self.critical_journal.error,
            }

    def start(
        self, pid: int, account_name: str, user_id: int, boss_mode: str,
        *, config: dict[str, Any] | None = None, strategy_id: str | None = None,
    ) -> None:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            if self.closing:
                raise RuntimeError("主工具正在关闭")
            if self.preparing or (self.process is not None and self.process.poll() is None):
                raise RuntimeError("控制器已经在运行")
            self.pid = pid
            self.boss_mode = boss_mode
            self.status = "正在准备代理…"
            self.error = None
            self.stop_requested = False
            self.preparing = True
            self.pause_token = secrets.token_hex(16)
            self.pause_event = ControllerPauseEvent(pid, self.pause_token)
            try:
                self.pause_event.open()
                self.config_path = PROJECT_ROOT / "out" / "controller-sessions" / (self.pause_token + ".json") if config is not None else None
                if self.config_path is not None:
                    atomic_write_json(self.config_path, config)
                self.running_revision = revision(config) if config is not None else None
            except Exception:
                self.preparing = False
                self.pause_event.close()
                raise
            self.strategy_id = strategy_id
            self.diagnostic_run = secrets.token_hex(8)
            self.decision_numbers[boss_mode] = 0
            self.record_diagnostic("start", boss_mode, strategyName=config.get("name") if config else None,
                objectives=copy.deepcopy(config.get("objectives", {})) if config else {},
                strategy={key: copy.deepcopy(config[key]) for key in
                    ('name', 'mode', 'bossMode', 'scope', 'objectives', 'safety', 'rules', 'team', 'trialRecipes',
                     'executionMode', 'strategyFlow', 'strategyTree')
                    if config and key in config},
                build=diagnostic_build_identity(),
                logFormat=2)
            if self.journal.path is not None and not self.journal.disabled:
                self.append(f"诊断自动保存到：{self.journal.directory}（普通日志约 256 MB，关键事件另存约 32 MB）", boss_mode)
            self.telemetry[boss_mode] = {}
            self.append(
                f"正在为游戏内账户 {account_name} 准备{mode_spec(boss_mode)['label']}接管。"
            )
        self.worker_thread = threading.Thread(
            target=self._prepare_and_run,
            args=(pid, account_name, user_id, boss_mode),
            daemon=True,
            name="chimera-controller-launch",
        )
        self.worker_thread.start()

    def _check_cancelled(self) -> None:
        with self.lock:
            if self.closing or self.stop_requested:
                raise LaunchCancelled()

    def _run_injector(self, arguments: list[str], timeout: int) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        self._check_cancelled()
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [*worker_command("injector"), "--pid", str(arguments[0]), "--agent", str(AGENT), *arguments[1:]]
        with self.lock:
            self._check_cancelled()
            process = subprocess.Popen(
                command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=creation_flags, env=utf8_subprocess_environment(),
            )
            self.preparation_process = process
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    with self.lock:
                        cancelled = self.closing or self.stop_requested
                    # Read-only discovery may be terminated immediately. A
                    # dispatched native load/reload is allowed to finish its
                    # bounded operation before cancelling subsequent steps.
                    if cancelled and '--check-only' in arguments:
                        process.terminate()
                        process.communicate(timeout=3)
                        raise LaunchCancelled()
                    if time.monotonic() >= deadline:
                        process.kill()
                        process.communicate(timeout=3)
                        raise subprocess.TimeoutExpired(process.args, timeout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
            with self.lock:
                self.preparation_process = None
        self._check_cancelled()
        result = subprocess.CompletedProcess(
            process.args, process.returncode,
            decode_worker_output(stdout), decode_worker_output(stderr),
        )
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
        return result, payload if isinstance(payload, dict) else {}

    def _prepare_and_run(
        self, pid: int, account_name: str, user_id: int, boss_mode: str
    ) -> None:
        try:
            self._check_cancelled()
            require_expected_account(pid, account_name, user_id)
            check, check_payload = self._run_injector([str(pid), "--check-only"], 20)
            if check.returncode:
                raise RuntimeError(check_payload.get("reason") or check.stderr.strip() or "代理检查失败")

            if check_payload.get("agentLoaded"):
                reload_reason = reload_block_reason(check_payload.get("agentStatus"))
                if reload_reason:
                    raise RuntimeError(
                        "旧版或状态不明的代理仍驻留在游戏进程中。请完全退出并重新启动 Raid 客户端；"
                        "为避免再次闪退，工具已阻止在线卸载或重载。"
                    )

            if check_payload.get("agentLoaded") and (
                not check_payload.get("agentCompatible") or not check_payload.get("agentReady")
            ):
                previous_lifecycle: dict[str, Any] = {}
                try:
                    with AgentIpc(pid) as ipc:
                        previous_lifecycle = ipc.lifecycle() or {}
                except (FileNotFoundError, ValueError, OSError):
                    pass
                if previous_lifecycle.get("screen") == "result":
                    raise RuntimeError("当前停留在战绩结算画面，不会在此时更新代理")
                self.append("正在只更新所选游戏账户的代理版本…", boss_mode)
                reload_result, reload_payload = self._run_injector([str(pid), "--reload"], 35)
                if reload_result.returncode:
                    raise RuntimeError(
                        reload_payload.get("reason")
                        or reload_result.stderr.strip()
                        or "所选账户代理更新失败"
                    )
                screen = previous_lifecycle.get("screen")
                self._check_cancelled()
                if screen == "battle":
                    context = (previous_lifecycle.get("battle") or {}).get("context")
                    if isinstance(context, int) and context > 0:
                        if not seed_battle_context(pid, AGENT, context).get("accepted"):
                            raise RuntimeError("更新后恢复当前奇美拉战斗失败")
                elif screen == "team_selection":
                    context = (previous_lifecycle.get("selection") or {}).get("context")
                    if isinstance(context, int) and context > 0:
                        if not seed_selection_context(pid, AGENT, context).get("accepted"):
                            raise RuntimeError("更新后恢复奇美拉队伍界面失败")
                require_expected_account(pid, account_name, user_id)
                check_payload = {"agentLoaded": True, "agentCompatible": True, "agentReady": True}

            if not check_payload.get("agentLoaded"):
                self.append("代理尚未载入，正在载入所选账户…", boss_mode)
                load_result, load_payload = self._run_injector([str(pid)], 35)
                if load_result.returncode:
                    raise RuntimeError(load_payload.get("reason") or load_result.stderr.strip() or "代理载入失败")
                require_expected_account(pid, account_name, user_id)

            require_expected_account(pid, account_name, user_id)
            self._check_cancelled()

            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            controller_arguments = [
                    *worker_command("controller"),
                    "--parent-pid",
                    str(os.getpid()),
                    "--pid",
                    str(pid),
                    "--account-name",
                    account_name,
                    "--account-user-id",
                    str(user_id),
                    "--config",
                    str(self.config_path or USER_STRATEGY),
                    "--boss-mode",
                    boss_mode,
                    "--agent",
                    str(AGENT),
                    "--capability-cache",
                    str(PROJECT_ROOT / "cache" / "chimera-skill-capabilities.json"),
                    "--capability-seed",
                    str(BUNDLE_ROOT / "data" / "chimera-skill-capabilities.json"),
                    "--bootstrap-current",
                    "--execute",
                ]
            controller_arguments.append("--auto-start")
            if self.pause_token:
                controller_arguments.extend(["--pause-token", self.pause_token])
            with self.lock:
                self._check_cancelled()
                process = subprocess.Popen(
                    controller_arguments,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    creationflags=creation_flags,
                    env=utf8_subprocess_environment(),
                )
                self.process = process
                self.status = f"正在运行 · {account_name}"
            self.append("控制器已启动。", boss_mode)
            assert process.stdout is not None
            for line in process.stdout:
                self.append(decode_worker_output(line), boss_mode)
            code = process.wait()
            with self.lock:
                self.status = controller_exit_label(code, self.stop_requested)
                self.record_diagnostic("lifecycle", boss_mode, lifecycle={"event": "controller_exit", "exitCode": code, "stopRequested": self.stop_requested, "closing": self.closing})
                self.append(self.status + "。" if code == 0 else f"控制器已退出，代码 {code}。", boss_mode)
        except LaunchCancelled:
            with self.lock:
                self.status = "已关闭" if self.closing else "已暂停"
        except Exception as error:
            with self.lock:
                if self.closing or self.stop_requested:
                    self.error = None
                    self.status = "已关闭" if self.closing else "已暂停"
                    self.record_diagnostic("lifecycle", boss_mode, lifecycle={"event": "preparation_cancelled", "interruptedErrorType": type(error).__name__})
                else:
                    self.error = str(error)
                    self.status = "启动失败"
                    self.append(f"启动失败：{error}", boss_mode)
        finally:
            # Do not orphan a worker if decoding its output or another host
            # operation fails after Popen. Keep cancellation alive until exit.
            with self.lock:
                unfinished = self.process
                if unfinished is not None and unfinished.poll() is None and self.pause_event is not None:
                    self.pause_event.signal()
            if unfinished is not None and unfinished.poll() is None:
                try:
                    unfinished.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    unfinished.terminate()
                    try:
                        unfinished.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        unfinished.kill()
                        unfinished.wait(timeout=2.0)
            with self.lock:
                self.record_diagnostic("stop", boss_mode, status=self.status)
                if self.pause_event is not None:
                    self.pause_event.close()
                    self.pause_event = None
                if self.config_path is not None:
                    if not remove_session_file(self.config_path):
                        self.record_diagnostic("lifecycle", boss_mode, lifecycle={"event": "temporary_config_cleanup_deferred"})
                    self.config_path = None
                self.process = None
                self.preparing = False
                self.stop_requested = False

    def stop(self, pid: int | None) -> None:
        with self.lock:
            self.stop_requested = True
            self.status = "正在暂停接管…"
            process = self.process
            actual_pid = self.pid
            if self.pause_event is not None:
                if not self.pause_event.signal():
                    raise RuntimeError("暂停信号发送失败；请重试")
                return
        if process is not None and process.poll() is None:
            target = actual_pid
            if not isinstance(target, int) or not signal_controller_pause(target):
                with self.lock:
                    self.stop_requested = False
                    self.status = "暂停信号发送失败"
                raise RuntimeError("暂停信号发送失败；控制器仍保持运行")
            self.append("已请求暂停；正在等待控制器清理接管会话。", self.boss_mode)
            return
        with self.lock:
            if not self.preparing:
                self.status = "已停止"
                self.stop_requested = False

    def request_shutdown(self) -> None:
        """Signal cancellation from the window callback without blocking it."""
        with self.lock:
            self.closing = True
            self.stop_requested = True
            self.status = "正在关闭…"
            if self.pause_event is not None:
                self.pause_event.signal()

    def shutdown(self, timeout: float = 8.0) -> None:
        self.request_shutdown()
        with self.lock:
            process = self.process
            target = self.pid
            owns_pause_event = self.pause_event is not None
            if self.pause_event is not None:
                self.pause_event.signal()

        if process is not None and process.poll() is None:
            if isinstance(target, int) and not owns_pause_event:
                signal_controller_pause(target)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)

        if self.worker_thread is not None and self.worker_thread is not threading.current_thread():
            # Initialization helpers have a 35-second hard operation limit.
            # Keep the extraction parent alive until they release its files.
            self.worker_thread.join(timeout=max(timeout, 40.0))
        with self.lock:
            self.status = "已关闭"
