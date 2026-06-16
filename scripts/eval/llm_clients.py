"""Unified LLM clients for the Echo evaluation experiments.

Four-model isolation (so no single model both produces and grades the signal —
the circularity trap Echo's whole thesis warns about):

    persona / simulated user   -> DeepSeek v4 flash (Aliyun MaaS)   [ECHO_PERSONA_*]
    independent evaluator      -> GLM-5.2 (Zhipu, thinking disabled)[ECHO_EVALUATOR_*]
    main agent under test      -> mimo-v2.5 (Xiaomi)                [XIAOMI_API_KEY + model.base_url]
    Echo's own signal models   -> Qwen qwen-plus (DashScope)        [config.yaml auxiliary.*]

The first three are called directly here. The Qwen signal models are invoked
*inside* Echo's plugin (nl_classifier / judge / reason_scorer) via Hermes'
auxiliary_client, so they are not wrapped here — the experiment drives them by
feeding signals through the plugin.

Every client tracks cumulative token usage (for Metric 3 — system overhead) and
retries transient failures. All keys come from ~/.hermes/.env or config.yaml;
nothing is hard-coded.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI

_ENV_PATH = pathlib.Path(os.path.expanduser("~/.hermes/.env"))
_CONFIG_PATH = pathlib.Path(os.path.expanduser("~/.hermes/config.yaml"))


def load_env() -> None:
    """Load ~/.hermes/.env into os.environ (standalone scripts get no Hermes bootstrap)."""
    if not _ENV_PATH.exists():
        return
    for ln in _ENV_PATH.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Usage:
    """Cumulative token accounting for one logical model role."""

    name: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, resp: Any) -> None:
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u is not None:
            self.prompt_tokens += int(getattr(u, "prompt_tokens", 0) or 0)
            self.completion_tokens += int(getattr(u, "completion_tokens", 0) or 0)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens, "errors": self.errors,
        }


class _BaseClient:
    def __init__(self, name: str, api_key: str, base_url: str, model: str,
                 extra_body: Optional[dict] = None):
        self.usage = Usage(name)
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=90)
        self.model = model
        self.extra_body = extra_body or {}

    def chat(self, messages: list[dict], *, max_tokens: int = 512,
             temperature: float = 0.7, retries: int = 4) -> str:
        last = None
        for attempt in range(retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model, messages=messages,
                    max_tokens=max_tokens, temperature=temperature,
                    extra_body=self.extra_body or None,
                )
                self.usage.add(resp)
                out = resp.choices[0].message.content
                return out if isinstance(out, str) else ""
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (attempt + 1))
        self.usage.errors += 1
        raise RuntimeError(f"{self.usage.name} chat failed after {retries}: {last}")

    def chat_json(self, messages: list[dict], *, max_tokens: int = 512,
                  temperature: float = 0.0) -> Optional[dict]:
        """Chat then tolerant-parse a JSON object from the reply."""
        txt = self.chat(messages, max_tokens=max_tokens, temperature=temperature)
        return _extract_json(txt)


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        t = "\n".join(lines[1:-1]) if len(lines) >= 3 else t
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0 or b < a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except json.JSONDecodeError:
        return None


def make_persona() -> _BaseClient:
    return _BaseClient(
        "persona(deepseek)",
        os.environ["ECHO_PERSONA_API_KEY"],
        os.environ["ECHO_PERSONA_BASE_URL"],
        os.environ.get("ECHO_PERSONA_MODEL", "deepseek-v4-flash"),
    )


def make_evaluator() -> _BaseClient:
    # GLM-5.2 is a thinking model; disable thinking so it returns a clean answer
    # instead of burning the token budget on reasoning.
    return _BaseClient(
        "evaluator(glm-5.2)",
        os.environ["ECHO_EVALUATOR_API_KEY"],
        "https://open.bigmodel.cn/api/paas/v4",
        os.environ.get("ECHO_EVALUATOR_MODEL", "glm-5.2"),
        extra_body={"thinking": {"type": "disabled"}},
    )


def make_agent() -> _BaseClient:
    """Main agent under test = mimo-v2.5 (Xiaomi), read from config.yaml + env."""
    import yaml

    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    model_cfg = cfg.get("model", {})
    base = model_cfg.get("base_url", "https://token-plan-cn.xiaomimimo.com/v1")
    model = model_cfg.get("default", "mimo-v2.5")
    key = os.environ.get("XIAOMI_API_KEY") or model_cfg.get("api_key", "")
    # mimo-v2.5 is a reasoning model; left on, its chain-of-thought eats the
    # token budget and the actual answer can come back empty/truncated. We
    # disable thinking so it answers directly. The same config is used for ALL
    # conditions, so the Echo-vs-baseline comparison stays fair.
    return _BaseClient("agent(mimo)", key, base, model,
                       extra_body={"thinking": {"type": "disabled"}})


if __name__ == "__main__":
    load_env()
    print("Verifying four-model isolation setup …\n")
    ok = True
    # persona
    try:
        p = make_persona()
        r = p.chat([{"role": "user", "content": "用一句话自我介绍"}], max_tokens=60)
        print(f"  persona(deepseek-v4-flash): OK -> {r[:60]!r}")
    except Exception as e:
        ok = False; print(f"  persona: FAIL {e}")
    # evaluator
    try:
        ev = make_evaluator()
        j = ev.chat_json([{"role": "user", "content": '给个评分,只输出 {"score": 5}'}], max_tokens=512)
        print(f"  evaluator(glm-5.2): OK -> {j}")
    except Exception as e:
        ok = False; print(f"  evaluator: FAIL {e}")
    # agent
    try:
        ag = make_agent()
        r = ag.chat([{"role": "user", "content": "say hi in 3 words"}], max_tokens=30)
        print(f"  agent(mimo-v2.5): OK -> {r[:60]!r}")
    except Exception as e:
        ok = False; print(f"  agent: FAIL {e}")
    # qwen signals via hermes aux client
    try:
        from agent.auxiliary_client import call_llm
        resp = call_llm(task="echo_judge",
                        messages=[{"role": "user", "content": "reply: ok"}],
                        max_tokens=10, temperature=0)
        print(f"  signals(qwen via aux): OK -> {resp.choices[0].message.content[:40]!r}")
    except Exception as e:
        ok = False; print(f"  signals(qwen): FAIL {e}")
    print("\nALL OK" if ok else "\nSOME CHECKS FAILED")
