# Copyright 2026 OMNIA EYE Corporation
# SPDX-License-Identifier: Apache-2.0
"""Pinned local Laya adapter with complete-input checks and isolated native execution.

PyPI laya 0.3.20 has no revision constructor argument. Resolve an immutable Hub
snapshot first, then use the public Agent(local_path) API. Do not substitute the
OMNIA fork bearing the same version number or patch the dependency at runtime.
"""

from __future__ import annotations

import asyncio
import json
import math
import multiprocessing
import os
import re
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from importlib import metadata, util
from pathlib import Path
from typing import Any

from ..errors import OmniaError
from .base import copy_request

LAYA_VERSION = "0.3.20"
MODEL_REPOSITORY = "convaiinnovations/laya"
MODEL_FOLDERS = {
    "english": "",
    "multilingual": "multilingual",
    "typed-decisions": "typed-decisions",
}


@dataclass(frozen=True)
class LocalConfig:
    model: str
    revision: str
    device: str
    threads: int
    max_len: int
    head_max_len: int
    offline: bool
    cpu_amp: str = ""
    mps_amp_min_rows: int = 5


def _validate_questions(questions: dict[str, Any]) -> None:
    if not 1 <= len(questions) <= 16:
        raise OmniaError(
            "unsupported_request", "Laya accepts one to sixteen questions per evaluation."
        )
    for question in questions.values():
        if set(question) - {"type", "instructions", "criteria"}:
            raise OmniaError(
                "unsupported_request", "The local adapter does not support these question fields."
            )
        if (
            not isinstance(question.get("instructions"), (str, dict, list))
            or not question["instructions"]
        ):
            raise OmniaError(
                "invalid_request", "Instructions must be nonempty text or structured JSON."
            )
        kind, criteria = question.get("type"), question.get("criteria")
        if kind == "choice":
            valid = isinstance(criteria, dict) and 2 <= len(criteria) <= 16
            valid = valid and all(isinstance(key, str) and key for key in criteria)
        elif kind == "score":
            valid = isinstance(criteria, list) and 2 <= len(criteria) <= 16
        elif kind == "noul":
            valid = criteria is None or (
                isinstance(criteria, dict) and set(criteria) <= {"false", "true"}
            )
        else:
            valid = False
        if not valid:
            raise OmniaError(
                "unsupported_request",
                "The question exceeds the local adapter's supported contract.",
            )


def check_token_budget(
    tokenizer: Any, state: Any, questions: dict[str, Any], *, max_len: int, head_max_len: int
) -> None:
    """Match Laya's exact serialization and reject every truncating branch before inference."""
    from laya.common import render_options, serialize_state

    mask = tokenizer.mask_token
    if not isinstance(mask, str) or not mask:
        raise OmniaError(
            "incompatible_checkpoint", "The checkpoint tokenizer lacks its required mask token."
        )

    def size(value: str) -> int:
        return len(
            tokenizer(value.replace(mask, " "), add_special_tokens=False, truncation=False)[
                "input_ids"
            ]
        )

    state_size = size(serialize_state(state))
    for question in questions.values():
        instructions = question["instructions"]
        if not isinstance(instructions, str):
            instructions = json.dumps(instructions, ensure_ascii=False)
        internal = {"t": question["type"], "ins": instructions, "crit": question.get("criteria")}
        lengths = [size(" " + option) for option in render_options(internal)]
        if any(length > 48 for length in lengths):
            raise OmniaError(
                "context_budget_exceeded",
                "A criterion exceeds the local checkpoint's token budget.",
            )
        option_size = sum(length + 1 for length in lengths)
        instruction_size = size(f"{question['type']} question: {instructions}")
        if head_max_len - option_size < 16 or instruction_size > head_max_len - option_size:
            raise OmniaError(
                "context_budget_exceeded", "Instructions and criteria exceed the local head budget."
            )
        if 4 + instruction_size + option_size + state_size > max_len:
            raise OmniaError(
                "context_budget_exceeded", "The complete state exceeds the local context budget."
            )


def check_temperatures(agent: Any, questions: dict[str, Any]) -> None:
    """Reject altered calibration only for buckets actually used by this request."""
    from laya.common import QTYPES, temp_bucket

    try:
        for question in questions.values():
            kind = QTYPES[question["type"]]
            count = 2 if question["type"] == "noul" else len(question["criteria"])
            bucket = temp_bucket(kind, count)
            raw = float(agent.temperature_by_options_raw.get(bucket, agent.temperature_raw[kind]))
            applied = float(agent.temperature_by_options.get(bucket, agent.temperature[kind]))
            if not math.isfinite(raw) or not math.isfinite(applied) or raw != applied:
                raise OmniaError(
                    "calibration_requires_review",
                    "The active checkpoint temperature was corrected on load.",
                )
    except (AttributeError, TypeError, KeyError, ValueError, IndexError):
        raise OmniaError(
            "incompatible_checkpoint", "Checkpoint calibration metadata is unavailable."
        ) from None


class _LocalRuntime:
    """Exists only inside the isolated process. Native dependency imports stay lazy."""

    def __init__(self, config: LocalConfig):
        self.config = config
        self.agent: Any = None

    def _load(self) -> Any:
        try:
            if metadata.version("laya") != LAYA_VERSION:
                raise OmniaError(
                    "incompatible_runtime", "Install the supported public Laya runtime version."
                )
            import torch
            from huggingface_hub import snapshot_download
            from laya import Agent
        except (ImportError, metadata.PackageNotFoundError):
            raise OmniaError(
                "provider_not_installed", "Install the optional local inference dependencies."
            ) from None

        config = self.config
        if config.device == "cuda" and not torch.cuda.is_available():
            raise OmniaError(
                "device_unavailable", "The requested local inference device is unavailable."
            )
        if config.device == "mps" and not (
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        ):
            raise OmniaError(
                "device_unavailable", "The requested local inference device is unavailable."
            )
        torch.set_num_threads(config.threads)
        folder = MODEL_FOLDERS[config.model]
        prefix = folder + "/" if folder else ""
        snapshot = Path(
            snapshot_download(
                repo_id=MODEL_REPOSITORY,
                revision=config.revision,
                token=False,
                local_files_only=config.offline,
                allow_patterns=[
                    prefix + item
                    for item in (
                        "rl_agent_config.json",
                        "model.safetensors",
                        "tokenizer/*",
                        "encoder/*",
                    )
                ],
            )
        )
        # Check the logical snapshot name, not resolve(): HF file symlinks point to blobs.
        if snapshot.name != config.revision or snapshot.parent.name != "snapshots":
            raise OmniaError(
                "checkpoint_revision_mismatch",
                "The resolved checkpoint is not the selected immutable snapshot.",
            )
        checkpoint = snapshot / folder if folder else snapshot
        required = (
            "rl_agent_config.json",
            "model.safetensors",
            "tokenizer/tokenizer_config.json",
            "tokenizer/tokenizer.json",
            "encoder/config.json",
        )
        if any(not (checkpoint / name).is_file() for name in required):
            raise OmniaError(
                "checkpoint_incomplete", "Required local checkpoint files are missing."
            )
        # Require local encoder/tokenizer assets, so Agent has no reason to fetch an unpinned base model.
        with (checkpoint / "rl_agent_config.json").open(encoding="utf-8") as handle:
            checkpoint_config = json.load(handle)
        limit = checkpoint_config.get("max_len")
        if type(limit) is not int or config.max_len > limit:
            raise OmniaError(
                "context_budget_exceeded",
                "The configured context exceeds the checkpoint's declared budget.",
            )
        agent = Agent(str(checkpoint), device=config.device, fast=False, compile=False)
        self._check_device(agent)
        return agent

    def _check_device(self, agent: Any) -> None:
        if str(agent.device) != self.config.device:
            raise OmniaError(
                "device_fallback_rejected", "The runtime changed the requested inference device."
            )

    def evaluate(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        if self.agent is None:
            self.agent = self._load()
        self._check_device(self.agent)
        check_temperatures(self.agent, questions)
        check_token_budget(
            self.agent.tok,
            state,
            questions,
            max_len=self.config.max_len,
            head_max_len=self.config.head_max_len,
        )
        precision = (
            str(getattr(self.agent, "dtype", "")),
            bool(getattr(self.agent, "amp_enabled", False)),
        )
        result = self.agent.predict(
            state, questions, max_len=self.config.max_len, head_max_len=self.config.head_max_len
        )
        # Laya may change device on GPU OOM; never accept that as the requested run.
        self._check_device(self.agent)
        current_precision = (
            str(getattr(self.agent, "dtype", "")),
            bool(getattr(self.agent, "amp_enabled", False)),
        )
        if current_precision != precision:
            raise OmniaError(
                "precision_fallback_rejected", "The runtime changed its inference precision."
            )
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise OmniaError(
                "invalid_provider_response",
                "The local runtime returned an invalid response envelope.",
            )
        return result


def _error_payload(error: OmniaError) -> dict[str, Any]:
    return {"error": {"code": error.code, "message": str(error), "retryable": error.retryable}}


def _worker_main(connection: Any, config: LocalConfig) -> None:
    # Native Laya prints device fallback warnings. Keep all dependency output away
    # from MCP stdio; never globally redirect the parent interpreter's streams.
    with open(os.devnull, "w", encoding="utf-8") as sink:
        # Redirect native C/Rust writes as well as Python print(), only in the child.
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        sys.stdout = sink
        sys.stderr = sink
        os.environ["LAYA_CPU_AMP"] = config.cpu_amp
        os.environ["LAYA_MPS_AMP_MIN_ROWS"] = str(config.mps_amp_min_rows)
        runtime = _LocalRuntime(config)
        try:
            while True:
                request = connection.recv()
                if request is None:
                    return
                try:
                    reply = {"result": runtime.evaluate(*request)}
                except OmniaError as error:
                    reply = _error_payload(error)
                except Exception:
                    reply = {
                        "error": {
                            "code": "local_inference_failed",
                            "message": "The local inference runtime failed.",
                            "retryable": True,
                        }
                    }
                connection.send(reply)
        except (EOFError, BrokenPipeError, OSError):
            return
        finally:
            connection.close()


class LayaProvider:
    def __init__(self, settings: Any):
        cpu_amp = (
            "bf16" if os.environ.get("LAYA_CPU_AMP", "").lower() in {"bf16", "bfloat16"} else ""
        )
        try:
            mps_rows = int(os.environ.get("LAYA_MPS_AMP_MIN_ROWS", "5"))
        except ValueError:
            mps_rows = 5
        if not 1 <= mps_rows <= 65536:
            mps_rows = 5
        self._config = LocalConfig(
            settings.laya_model,
            settings.laya_revision,
            settings.laya_device,
            settings.laya_threads,
            settings.laya_max_len,
            settings.laya_head_max_len,
            settings.laya_offline,
            cpu_amp,
            mps_rows,
        )
        self._timeout = float(settings.request_timeout_seconds)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="omnia-laya-supervisor"
        )
        self._inflight: Future[Any] | None = None
        self._process: Any = None
        self._connection: Any = None
        self._closed = False

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "laya",
            "model": self._config.model,
            "revision": self._config.revision,
            "runtime_version": LAYA_VERSION,
            "repository": MODEL_REPOSITORY,
            "device": self._config.device,
            "max_len": self._config.max_len,
            "head_max_len": self._config.head_max_len,
            "threads": self._config.threads,
            "cpu_amp": self._config.cpu_amp,
            "mps_amp_min_rows": self._config.mps_amp_min_rows,
        }

    def capabilities(self) -> dict[str, Any]:
        try:
            installed = util.find_spec("laya") is not None
        except (ValueError, ImportError):
            installed = False
        with self._lock:
            busy = self._inflight is not None and not self._inflight.done()
        return {
            "provider": "laya",
            "configured": bool(re.fullmatch("[0-9a-f]{40}", self._config.revision)),
            "installed": installed,
            "remote": False,
            "offline": self._config.offline,
            "question_types": ["choice", "score", "noul"],
            "max_questions": 16,
            "max_choice_options": 16,
            "max_score_levels": 16,
            "score_semantics": "zero_based_expected_level",
            "confidence_semantics": "choice_score_normalized_entropy;noul_max_probability",
            "answer_confidence_semantics": "maximum_answer_probability",
            "complete_input_required": True,
            "concurrency": 1,
            "busy": busy,
        }

    def _validate_config(self) -> None:
        config = self._config
        if not re.fullmatch("[0-9a-f]{40}", config.revision):
            raise OmniaError(
                "provider_not_configured", "Select an immutable 40-character checkpoint revision."
            )
        if config.model not in MODEL_FOLDERS or config.device not in {"cpu", "cuda", "mps"}:
            raise OmniaError(
                "unsupported_configuration", "The selected local model or device is unsupported."
            )
        if not (1 <= config.threads <= 64 and 32 <= config.head_max_len < config.max_len <= 8192):
            raise OmniaError("unsupported_configuration", "The local runtime budgets are invalid.")

    def _exchange(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._lock:
                if self._closed:
                    return {
                        "error": {
                            "code": "provider_closed",
                            "message": "The provider has been closed.",
                            "retryable": False,
                        }
                    }
                if self._process is None or not self._process.is_alive():
                    if self._connection is not None:
                        self._connection.close()
                    if self._process is not None:
                        self._process.join(timeout=0)
                        self._process.close()
                    context = multiprocessing.get_context("spawn")
                    parent, child = context.Pipe()
                    process = context.Process(
                        target=_worker_main,
                        args=(child, self._config),
                        name="omnia-laya-worker",
                        daemon=True,
                    )
                    process.start()
                    child.close()
                    self._process, self._connection = process, parent
                connection = self._connection
            connection.send((state, questions))
            return connection.recv()
        except Exception:
            return {
                "error": {
                    "code": "local_worker_failed",
                    "message": "The local inference worker stopped.",
                    "retryable": True,
                }
            }

    def _release(self, future: Future[Any]) -> None:
        with self._lock:
            if self._inflight is future:
                self._inflight = None

    async def evaluate(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        self._validate_config()
        state, questions = copy_request(state, questions)
        _validate_questions(questions)
        with self._lock:
            if self._closed:
                raise OmniaError("provider_closed", "The provider has been closed.")
            if self._inflight is not None and not self._inflight.done():
                raise OmniaError(
                    "provider_busy", "A local evaluation is still running.", retryable=True
                )
            future = self._executor.submit(self._exchange, state, questions)
            self._inflight = future
        future.add_done_callback(self._release)
        try:
            async with asyncio.timeout(self._timeout):
                # Caller cancellation does not cancel the concurrent Future or free its slot.
                reply = await asyncio.shield(asyncio.wrap_future(future))
        except TimeoutError:
            raise OmniaError(
                "provider_timeout",
                "The local evaluation exceeded its deadline; its worker is still occupied.",
                retryable=True,
            ) from None
        if "error" in reply:
            error = reply["error"]
            raise OmniaError(
                error["code"], error["message"], retryable=error.get("retryable", False)
            )
        return reply["result"]

    def _stop_worker(self) -> None:
        with self._lock:
            process, connection = self._process, self._connection
            busy = self._inflight is not None and not self._inflight.done()
        if process is not None:
            if not busy and connection is not None:
                with suppress(OSError, EOFError, BrokenPipeError):
                    connection.send(None)
            process.join(timeout=1.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
            if connection is not None:
                connection.close()
            if not process.is_alive():
                process.close()
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def aclose(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        await asyncio.to_thread(self._stop_worker)
