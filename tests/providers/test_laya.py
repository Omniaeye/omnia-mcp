# Copyright 2026 OMNIA EYE Corporation
# SPDX-License-Identifier: Apache-2.0
"""Controlled tokenizer/runtime/worker doubles; these are not model-quality measurements."""

import asyncio
import json
import multiprocessing
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from omnia_mcp.errors import OmniaError
from omnia_mcp.providers import laya

REVISION = "a" * 40
QUESTIONS = {"q": {"type": "choice", "instructions": "Choose", "criteria": {"a": "yes", "b": "no"}}}
RESPONSE = {
    "model": "laya-rl-agent",
    "answers": {
        "q": {
            "type": "choice",
            "choice": "a",
            "probabilities": {"a": 0.9, "b": 0.1},
            "confidence": 0.531,
            "answer_confidence": 0.9,
        }
    },
}


def settings(**changes):
    values = {
        "laya_model": "english",
        "laya_revision": REVISION,
        "laya_device": "cpu",
        "laya_threads": 2,
        "laya_max_len": 512,
        "laya_head_max_len": 192,
        "laya_offline": True,
        "request_timeout_seconds": 2.0,
    }
    return SimpleNamespace(**(values | changes))


class Tokenizer:
    mask_token = "[MASK]"

    def __init__(self):
        self.seen = []

    def __call__(self, text, **kwargs):
        assert kwargs.get("truncation") is False
        self.seen.append(text)
        return {"input_ids": list(range(len(text)))}


@pytest.fixture
def common(monkeypatch):
    module = ModuleType("laya.common")
    module.QTYPES = {"choice": 0, "score": 1, "noul": 2}
    module.temp_bucket = lambda kind, count: f"{kind}:{count}"
    module.serialize_state = lambda state: (
        state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    )

    def render(question):
        if question["t"] == "choice":
            return [f"{key}: {value}" for key, value in question["crit"].items()]
        if question["t"] == "score":
            return [f"level {index}: {value}" for index, value in enumerate(question["crit"])]
        return ["false: no, the statement does not hold", "true: yes, the statement holds"]

    module.render_options = render
    monkeypatch.setitem(sys.modules, "laya.common", module)
    return module


def test_complete_budget_accepts_exact_boundary_without_truncation(common):
    tokenizer = Tokenizer()
    # 22 instruction chars + (7 + 6) option positions + 4 separators + 1 state = 40.
    instruction = len("choice question: Choose")
    options = len(" a: yes") + 1 + len(" b: no") + 1
    budget = 4 + instruction + options + 1
    laya.check_token_budget(tokenizer, "x", QUESTIONS, max_len=budget, head_max_len=96)
    with pytest.raises(OmniaError) as caught:
        laya.check_token_budget(tokenizer, "xx", QUESTIONS, max_len=budget, head_max_len=96)
    assert caught.value.code == "context_budget_exceeded"


@pytest.mark.parametrize(
    "question",
    [
        {"type": "choice", "instructions": "Choose", "criteria": {"a": "x" * 49, "b": "short"}},
        {"type": "choice", "instructions": "x" * 200, "criteria": {"a": "yes", "b": "no"}},
        {
            "type": "choice",
            "instructions": "Choose",
            "criteria": {str(i): "x" * 20 for i in range(6)},
        },
    ],
)
def test_option_instruction_and_head_limits_fail_before_inference(common, question):
    with pytest.raises(OmniaError) as caught:
        laya.check_token_budget(
            Tokenizer(), "input", {"q": question}, max_len=512, head_max_len=100
        )
    assert caught.value.code == "context_budget_exceeded"


def test_structured_unicode_and_mask_serialization_match_native(common):
    tokenizer = Tokenizer()
    question = {
        "type": "choice",
        "instructions": {"texto": "ação [MASK]"},
        "criteria": {"a": "yes", "b": "no"},
    }
    laya.check_token_budget(
        tokenizer, {"texto": "olá [MASK]"}, {"q": question}, max_len=512, head_max_len=192
    )
    assert '{"texto": "olá  "}' in tokenizer.seen
    assert 'choice question: {"texto": "ação  "}' in tokenizer.seen
    assert all("[MASK]" not in text for text in tokenizer.seen)


def test_only_active_calibration_bucket_can_fail(common):
    agent = SimpleNamespace(
        temperature_raw=[1.0, 1.0, 1.0],
        temperature=[1.0, 1.0, 1.0],
        temperature_by_options_raw={"0:11": 0.1},
        temperature_by_options={"0:11": 0.5},
    )
    laya.check_temperatures(agent, QUESTIONS)
    agent.temperature_by_options_raw["0:2"] = 0.1
    agent.temperature_by_options["0:2"] = 0.5
    with pytest.raises(OmniaError) as caught:
        laya.check_temperatures(agent, QUESTIONS)
    assert caught.value.code == "calibration_requires_review"


@pytest.fixture
def pinned_runtime(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshots" / REVISION
    snapshot.mkdir(parents=True)
    for name in (
        "rl_agent_config.json",
        "model.safetensors",
        "encoder/config.json",
        "tokenizer/tokenizer_config.json",
        "tokenizer/tokenizer.json",
    ):
        path = snapshot / name
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"max_len":512}' if name == "rl_agent_config.json" else "{}")
    calls = []
    hub = ModuleType("huggingface_hub")

    def download(**kwargs):
        calls.append(kwargs)
        return str(snapshot)

    hub.snapshot_download = download
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    torch.set_num_threads = lambda threads: None
    native = ModuleType("laya")
    native.Agent = lambda path, **kwargs: SimpleNamespace(device=kwargs["device"], loaded_path=path)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "laya", native)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(laya.metadata, "version", lambda name: laya.LAYA_VERSION)
    config = laya.LocalConfig("english", REVISION, "cpu", 2, 512, 192, True)
    return laya._LocalRuntime(config), snapshot, calls, native


def test_pin_uses_official_snapshot_and_local_agent(pinned_runtime):
    runtime, snapshot, calls, native = pinned_runtime
    agent = runtime._load()
    assert Path(agent.loaded_path) == snapshot
    assert calls[0]["repo_id"] == "convaiinnovations/laya"
    assert calls[0]["revision"] == REVISION
    assert calls[0]["token"] is False
    assert calls[0]["local_files_only"] is True
    assert calls[0]["allow_patterns"] == [
        "rl_agent_config.json",
        "model.safetensors",
        "tokenizer/*",
        "encoder/*",
    ]


def test_revision_path_mismatch_rejects_agent_loading(pinned_runtime, monkeypatch):
    runtime, snapshot, calls, native = pinned_runtime
    monkeypatch.setattr(
        sys.modules["huggingface_hub"],
        "snapshot_download",
        lambda **kwargs: str(snapshot.parent / ("b" * 40)),
    )
    native.Agent = lambda *args, **kwargs: pytest.fail("must not load mismatched checkpoint")
    with pytest.raises(OmniaError) as caught:
        runtime._load()
    assert caught.value.code == "checkpoint_revision_mismatch"


def test_incomplete_and_oversized_checkpoint_rejected(pinned_runtime):
    runtime, snapshot, calls, native = pinned_runtime
    (snapshot / "rl_agent_config.json").write_text('{"max_len":128}')
    with pytest.raises(OmniaError) as caught:
        runtime._load()
    assert caught.value.code == "context_budget_exceeded"
    (snapshot / "tokenizer/tokenizer.json").unlink()
    with pytest.raises(OmniaError) as caught:
        runtime._load()
    assert caught.value.code == "checkpoint_incomplete"


def test_device_fallback_rejected(pinned_runtime):
    runtime, snapshot, calls, native = pinned_runtime
    native.Agent = lambda *args, **kwargs: SimpleNamespace(device="cuda")
    with pytest.raises(OmniaError) as caught:
        runtime._load()
    assert caught.value.code == "device_fallback_rejected"


async def test_cancellation_retains_local_capacity(monkeypatch):
    provider = laya.LayaProvider(settings())
    started, release = threading.Event(), threading.Event()

    def controlled(state, questions):
        started.set()
        release.wait(timeout=2)
        return {"result": RESPONSE}

    monkeypatch.setattr(provider, "_exchange", controlled)
    first = asyncio.create_task(provider.evaluate("evidence", QUESTIONS))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("second", QUESTIONS)
        assert caught.value.code == "provider_busy"
        release.set()
        for _ in range(50):
            if not provider.capabilities()["busy"]:
                break
            await asyncio.sleep(0.01)
        assert await provider.evaluate("third", QUESTIONS) == RESPONSE
    finally:
        release.set()
        await provider.aclose()


async def test_timeout_retains_capacity_and_native_confidence(monkeypatch):
    provider = laya.LayaProvider(settings(request_timeout_seconds=0.01))
    release = threading.Event()
    monkeypatch.setattr(
        provider, "_exchange", lambda *args: (release.wait(timeout=2), {"result": RESPONSE})[1]
    )
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "provider_timeout"
        assert provider.capabilities()["busy"]
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("second", QUESTIONS)
        assert caught.value.code == "provider_busy"
    finally:
        release.set()
        await provider.aclose()


async def test_validation_failure_does_not_start_worker(monkeypatch):
    provider = laya.LayaProvider(settings(laya_revision=""))
    monkeypatch.setattr(provider, "_exchange", lambda *args: pytest.fail("worker should not start"))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "provider_not_configured"
    finally:
        await provider.aclose()
    provider = laya.LayaProvider(settings())
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate(
                "evidence",
                {"q": {"type": "choice", "instructions": "Choose", "criteria": {"a": "only"}}},
            )
        assert caught.value.code == "unsupported_request"
        assert provider._process is None
    finally:
        await provider.aclose()


async def test_close_terminates_a_hung_isolated_child():
    provider = laya.LayaProvider(settings())
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=time.sleep, args=(30,), daemon=True)
    process.start()
    pid = process.pid
    provider._process = process
    started = time.monotonic()
    await provider.aclose()
    assert time.monotonic() - started < 4
    assert all(child.pid != pid for child in multiprocessing.active_children())


def test_native_precision_fallback_result_is_rejected(common):
    config = laya.LocalConfig("english", REVISION, "cpu", 2, 512, 192, True)
    runtime = laya._LocalRuntime(config)
    agent = SimpleNamespace(
        device="cpu",
        dtype="bf16",
        amp_enabled=True,
        tok=Tokenizer(),
        temperature_raw=[1.0, 1.0, 1.0],
        temperature=[1.0, 1.0, 1.0],
        temperature_by_options_raw={},
        temperature_by_options={},
    )

    def predict(*args, **kwargs):
        agent.dtype, agent.amp_enabled = "float32", False
        return RESPONSE

    agent.predict = predict
    runtime.agent = agent
    with pytest.raises(OmniaError) as caught:
        runtime.evaluate("input", QUESTIONS)
    assert caught.value.code == "precision_fallback_rejected"


def test_precision_environment_is_frozen_in_identity(monkeypatch):
    monkeypatch.setenv("LAYA_CPU_AMP", "bf16")
    provider = laya.LayaProvider(settings())
    monkeypatch.setenv("LAYA_CPU_AMP", "")
    assert provider.identity()["cpu_amp"] == "bf16"
    provider._executor.shutdown()
