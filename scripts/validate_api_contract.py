#!/usr/bin/env python3
"""Validate InferBridge through its public HTTP contract.

This validator is deliberately dependency-free so it can run against a packaged or
remote server without importing the application package.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET_RE = re.compile(
    r"Bearer\s+\S+|hf_[A-Za-z0-9_=-]{8,}|token\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users)/)[^\r\n]+")


class ValidationError(RuntimeError):
    pass


@dataclass(slots=True)
class Check:
    name: str
    status: str
    duration_ms: float
    detail: str = ""


class Client:
    def __init__(self, base_url: str, api_key: str | None, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.timeout = timeout

    def _headers(self, auth: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        auth: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers=self._headers(auth),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return (
                    response.status,
                    {key.lower(): value for key, value in response.headers.items()},
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                {key.lower(): value for key, value in exc.headers.items()},
                exc.read(),
            )
        except urllib.error.URLError as exc:
            raise ValidationError(f"Cannot reach {request.full_url}: {exc.reason}") from exc

    def json(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        auth: bool = True,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        status, _, body = self.request(method, path, payload, auth=auth)
        if status not in expected:
            raise ValidationError(f"{method} {path} returned HTTP {status}: {body[:400]!r}")
        try:
            return json.loads(body.decode())
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{method} {path} returned invalid JSON") from exc

    def sse(self, path: str, payload: dict[str, Any], *, first_event_only: bool = False) -> str:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={**self._headers(), "Accept": "text/event-stream"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise ValidationError(f"POST {path} returned HTTP {exc.code}") from exc
        lines: list[str] = []
        try:
            while line := response.readline():
                decoded = line.decode(errors="replace")
                lines.append(decoded)
                if first_event_only and decoded.startswith("data: "):
                    break
        finally:
            response.close()
        return "".join(lines)


def sanitize(value: Any, api_key: str | None = None) -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = _SECRET_RE.sub("[redacted]", text)
    return _PATH_RE.sub("[local-path]", text)[:1000]


def parse_sse(text: str) -> tuple[list[str], list[dict[str, Any]], bool]:
    events: list[str] = []
    payloads: list[dict[str, Any]] = []
    done = False
    for line in text.splitlines():
        if line.startswith("event: "):
            events.append(line[7:].strip())
        elif line.startswith("data: "):
            raw = line[6:].strip()
            if raw == "[DONE]":
                done = True
            else:
                payloads.append(json.loads(raw))
    return events, payloads, done


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


class Validator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.client = Client(args.base_url, args.api_key, args.timeout)
        self.checks: list[Check] = []
        self.server: dict[str, Any] = {}

    def check(self, name: str, callback: Callable[[], str | tuple[str, str]]) -> None:
        started = time.perf_counter()
        try:
            result = callback()
            status, detail = result if isinstance(result, tuple) else ("pass", result)
        except Exception as exc:  # noqa: BLE001
            status, detail = "fail", sanitize(exc, self.args.api_key)
        self.checks.append(
            Check(name, status, round((time.perf_counter() - started) * 1000, 1), detail)
        )

    def wait(self) -> None:
        deadline = time.monotonic() + self.args.startup_timeout
        while time.monotonic() < deadline:
            try:
                status, _, _ = self.client.request("GET", "/health", auth=False)
                if status == 200:
                    return
            except ValidationError:
                pass
            time.sleep(1)
        raise ValidationError("Server did not become healthy before startup timeout")

    def health(self) -> str:
        data = self.client.json("GET", "/health", auth=False)
        require(data.get("status") in {"ok", "busy"}, "Unexpected health status")
        if self.args.expect_real:
            require(not data.get("mock"), "Server fell back to mock mode")
        if self.args.expect_mock:
            require(bool(data.get("mock")), "Expected mock mode")
        self.server = {
            "version": data.get("version"),
            "mock": bool(data.get("mock")),
            "openvino": bool(data.get("openvino")),
            "default_device": data.get("device"),
        }
        live = self.client.json("GET", "/health/live", auth=False)
        require(live.get("status") == "ok", "Liveness probe failed")
        return f"version={data.get('version')} mock={bool(data.get('mock'))}"

    def auth(self) -> str | tuple[str, str]:
        if not self.args.api_key:
            return "skip", "No API key supplied"
        status, _, _ = self.client.request("GET", "/v1/models", auth=False)
        require(status == 401, f"Unauthenticated request returned HTTP {status}")
        self.client.json("GET", "/v1/models")
        return "Bearer key enforced"

    def devices(self) -> str:
        data = self.client.json("GET", "/v1/devices")
        available = data.get("available") or []
        self.server["available_devices"] = available
        if self.args.device in {"CPU", "GPU", "NPU"} and not data.get("mock"):
            require(self.args.device in available, f"{self.args.device} is not visible")
        return f"available={','.join(available) or '(none)'}"

    def models(self) -> str:
        data = self.client.json("GET", "/v1/models")
        ids = {item.get("id") for item in data.get("data", [])}
        require(self.args.model in ids, f"Missing model {self.args.model}")
        if self.args.include_embeddings:
            require(self.args.embedding_model in ids, "Embedding model is not cataloged")
        return f"catalog={len(ids)}"

    def load(self, model: str, device: str | None) -> str:
        payload: dict[str, Any] = {"model": model}
        if device:
            payload["device"] = device
        self.client.json("POST", "/v1/models/load", payload)
        deadline = time.monotonic() + self.args.load_timeout
        while time.monotonic() < deadline:
            data = self.client.json("GET", "/v1/system/status")
            entries = data.get("models", {}).get("available", [])
            entry = next((item for item in entries if item.get("id") == model), None)
            require(entry is not None, f"{model} missing from system status")
            if entry.get("is_loaded"):
                return f"loaded on {entry.get('device') or 'unknown'}"
            if entry.get("status") == "error" or entry.get("error"):
                raise ValidationError(entry.get("error") or "Model load failed")
            time.sleep(1)
        raise ValidationError(f"Timed out loading {model}")

    def chat(self) -> str:
        data = self.client.json(
            "POST",
            "/v1/chat/completions",
            {
                "model": self.args.model,
                "messages": [{"role": "user", "content": "Confirm local inference."}],
                "temperature": 0,
                "max_tokens": 48,
                "seed": 7,
                "stop": ["UNUSED_CERT_STOP"],
            },
        )
        choice = (data.get("choices") or [{}])[0]
        require(choice.get("message", {}).get("role") == "assistant", "Bad chat shape")
        require(data.get("usage", {}).get("total_tokens", 0) > 0, "Usage missing")
        return f"finish_reason={choice.get('finish_reason')}"

    def chat_stream(self) -> str:
        text = self.client.sse(
            "/v1/chat/completions",
            {
                "model": self.args.model,
                "messages": [{"role": "user", "content": "Stream a short reply."}],
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_tokens": 48,
                "temperature": 0,
            },
        )
        _, payloads, done = parse_sse(text)
        require(done, "Chat stream did not finish with [DONE]")
        require(any(item.get("choices") for item in payloads), "No streamed choices")
        require(any("usage" in item for item in payloads), "Stream usage missing")
        return f"events={len(payloads)}"

    def cancellation(self) -> str:
        self.client.sse(
            "/v1/chat/completions",
            {
                "model": self.args.model,
                "messages": [{"role": "user", "content": "Generate several sentences."}],
                "stream": True,
                "max_tokens": 128,
            },
            first_event_only=True,
        )
        time.sleep(0.25)
        self.chat()
        return "follow-up request succeeded"

    def tools_and_json(self) -> str | tuple[str, str]:
        tool_data = self.client.json(
            "POST",
            "/v1/chat/completions",
            {
                "model": self.args.model,
                "messages": [{"role": "user", "content": "Weather in London?"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
                "temperature": 0,
            },
        )
        message = (tool_data.get("choices") or [{}])[0].get("message", {})
        json_data = self.client.json(
            "POST",
            "/v1/chat/completions",
            {
                "model": self.args.model,
                "messages": [{"role": "user", "content": 'Return {"status":"ok"}.'}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 48,
            },
        )
        content = (json_data.get("choices") or [{}])[0].get("message", {}).get("content")
        strict_json = False
        try:
            strict_json = isinstance(json.loads(content or ""), dict)
        except json.JSONDecodeError:
            pass
        if message.get("tool_calls") and strict_json:
            return "tool call and strict JSON demonstrated"
        return (
            "warn",
            "Requests were accepted; model/runtime did not demonstrate both optional outputs",
        )

    def responses(self) -> str:
        data = self.client.json(
            "POST",
            "/v1/responses",
            {
                "model": self.args.model,
                "input": "Confirm Responses API compatibility.",
                "temperature": 0,
                "max_output_tokens": 48,
            },
        )
        require(data.get("object") == "response", "Bad Responses object")
        text = self.client.sse(
            "/v1/responses",
            {
                "model": self.args.model,
                "input": "Stream a short response.",
                "stream": True,
                "temperature": 0,
                "max_output_tokens": 48,
            },
        )
        events, _, done = parse_sse(text)
        required = {
            "response.created",
            "response.output_text.delta",
            "response.completed",
        }
        require(done and required.issubset(events), "Responses SSE sequence is incomplete")
        return "streaming and non-streaming passed"

    def embeddings(self) -> str | tuple[str, str]:
        if not self.args.include_embeddings:
            return "skip", "Not requested"
        self.load(self.args.embedding_model, "CPU")
        data = self.client.json(
            "POST",
            "/v1/embeddings",
            {"model": self.args.embedding_model, "input": ["one", "two"]},
        )
        vectors = data.get("data") or []
        require(len(vectors) == 2, "Expected two vectors")
        size = len(vectors[0].get("embedding") or [])
        require(size > 0, "Embedding vector is empty")
        return f"dimensions={size}"

    def metrics(self) -> str:
        data = self.client.json("GET", "/v1/system/status")
        metrics = data.get("metrics", {}).get("per_model", {}).get(self.args.model, {})
        requests = int(metrics.get("requests") or 0)
        require(requests > 0, "Request metrics did not record generation")
        return f"requests={requests}"

    def benchmark(self) -> str | tuple[str, str]:
        if not self.args.run_benchmark:
            return "skip", "Not requested"
        status, _, _ = self.client.request("POST", "/v1/models/unload", {"model": self.args.model})
        require(status == 200, f"Benchmark preflight unload returned HTTP {status}")
        try:
            data = self.client.json(
                "POST",
                "/v1/benchmarks/run",
                {
                    "model": self.args.model,
                    "devices": [self.args.device or "CPU"],
                    "max_tokens": 16,
                    "runs": 1,
                },
            )
        finally:
            self.load(self.args.model, self.args.device)
        results = data.get("results") or []
        require(any(item.get("success") for item in results), "Benchmark failed")
        return "benchmark succeeded"

    def lifecycle(self) -> str | tuple[str, str]:
        if not self.args.exercise_lifecycle:
            return "skip", "Not requested"
        status, _, _ = self.client.request("POST", "/v1/models/unload", {"model": self.args.model})
        require(status == 200, f"Unload returned HTTP {status}")
        status, _, _ = self.client.request(
            "POST",
            "/v1/chat/completions",
            {"model": self.args.model, "messages": [{"role": "user", "content": "x"}]},
        )
        require(status in {409, 503}, "Unloaded model guard failed")
        return self.load(self.args.model, self.args.device)

    def run(self) -> dict[str, Any]:
        self.wait()
        self.check("Health and probes", self.health)
        self.check("API-key enforcement", self.auth)
        self.check("Device discovery", self.devices)
        self.check("Model catalog", self.models)
        self.check("Model load", lambda: self.load(self.args.model, self.args.device))
        if self.args.profile in {"openwebui", "full"}:
            self.check("Open WebUI chat", self.chat)
            self.check("Open WebUI streaming", self.chat_stream)
            self.check("Stream cancellation recovery", self.cancellation)
            self.check("Tool and structured-output requests", self.tools_and_json)
            self.check("Request metrics", self.metrics)
        if self.args.profile in {"n8n", "full"}:
            self.check("n8n Responses API", self.responses)
        self.check("Embeddings", self.embeddings)
        self.check("Benchmark", self.benchmark)
        self.check("Model lifecycle", self.lifecycle)
        summary = {
            status: sum(check.status == status for check in self.checks)
            for status in ("pass", "warn", "skip", "fail")
        }
        return {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "profile": self.args.profile,
            "model": self.args.model,
            "embedding_model": self.args.embedding_model if self.args.include_embeddings else None,
            "requested_device": self.args.device,
            "server": self.server,
            "summary": summary,
            "checks": [asdict(check) for check in self.checks],
        }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# InferBridge API Validation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Profile: `{report['profile']}`",
        f"- Model: `{report['model']}`",
        f"- Device: `{report.get('requested_device') or 'server default'}`",
        "",
        f"**Result:** {summary['pass']} passed, {summary['warn']} warnings, "
        f"{summary['skip']} skipped, {summary['fail']} failed.",
        "",
        "| Check | Status | Duration | Detail |",
        "|---|---:|---:|---|",
    ]
    for check in report["checks"]:
        detail = check["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {check['name']} | **{check['status'].upper()}** | "
            f"{check['duration_ms']:.1f} ms | {detail} |"
        )
    lines += [
        "",
        "> Reports exclude API keys, prompts, generated text, hostnames, usernames, and full local paths.",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--profile", choices=("core", "openwebui", "n8n", "full"), default="full")
    result.add_argument("--model", default="tinyllama-1.1b-chat-fp16")
    result.add_argument("--embedding-model", default="bge-small-en-v1.5")
    result.add_argument("--device")
    result.add_argument("--api-key", default=os.getenv("OV_LLM_API_KEY") or None)
    result.add_argument("--timeout", type=float, default=120)
    result.add_argument("--startup-timeout", type=float, default=60)
    result.add_argument("--load-timeout", type=float, default=900)
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--expect-real", action="store_true")
    mode.add_argument("--expect-mock", action="store_true")
    result.add_argument("--include-embeddings", action="store_true")
    result.add_argument("--run-benchmark", action="store_true")
    result.add_argument("--exercise-lifecycle", action="store_true")
    result.add_argument("--output-json", type=Path)
    result.add_argument("--output-markdown", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = Validator(args).run()
    except ValidationError as exc:
        print(
            f"Validation could not start: {sanitize(exc, args.api_key)}",
            file=sys.stderr,
        )
        return 1
    rendered = markdown(report)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 1 if report["summary"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
