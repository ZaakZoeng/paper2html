"""Provider boundary. Workflow code only depends on complete_json()."""
import json
import re
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProviderError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API key to a redirected endpoint.


@dataclass
class ModelConfig:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    max_tokens: int = 8000
    timeout: int = 180
    json_mode: bool = False
    vision: bool = True

    def __post_init__(self):
        if not isinstance(self.base_url, str):
            raise ValueError("API 地址须为文本")
        self.base_url = self.base_url.strip().rstrip("/")
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("API 地址须为不含凭据、查询参数的 HTTP(S) 地址")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("远程 API 必须使用 HTTPS；HTTP 仅支持本机服务")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("请填写模型名称")
        if not isinstance(self.api_key, str) or "\n" in self.api_key or "\r" in self.api_key:
            raise ValueError("API 密钥格式不正确")
        if not 1024 <= self.max_tokens <= 32768 or not 10 <= self.timeout <= 600:
            raise ValueError("max_tokens 应为 1024–32768，timeout 应为 10–600 秒")


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("模型输出必须为 JSON 对象")
    return value


class CompatibleProvider:
    """Chat Completions compatible HTTP protocol; no SDK or tool calling required."""

    def __init__(self, config):
        self.config = config
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete_json(self, system, user):
        return self._complete(system, user)

    def complete_json_with_images(self, system, user, images):
        if not self.config.vision:
            raise ProviderError("当前配置为纯文本模型，无法核对复杂原图")
        return self._complete(system, user, images)

    def _complete(self, system, user, images=None):
        cfg = self.config
        if len(system) + len(user) > 180000:
            raise ProviderError("单次输入超过 180000 字符上限，请缩小论文范围")
        content = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": uri}} for uri in images]
        body = {"model": cfg.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": content}],
            "max_tokens": cfg.max_tokens}
        if cfg.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = "Bearer " + cfg.api_key
        endpoint = cfg.base_url if cfg.base_url.endswith("/chat/completions") else cfg.base_url + "/chat/completions"
        for attempt in range(3):
            try:
                request = Request(endpoint, json.dumps(body).encode(), headers=headers)
                with build_opener(NoRedirect()).open(request, timeout=cfg.timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ProviderError("API 响应超过 8 MB")
                    result = json.loads(raw)
                self.usage["calls"] += 1
                usage = result.get("usage") or {}
                for key in ("prompt_tokens", "completion_tokens"):
                    self.usage[key] += int(usage.get(key) or 0)
                choice = result["choices"][0]
                if choice.get("finish_reason") in ("length", "max_tokens"):
                    raise ProviderError("模型输出被截断；请提高 max_tokens 或使用输出容量更大的模型")
                content = choice["message"].get("content")
                if not isinstance(content, str):
                    raise ProviderError("API 未返回文本，请确认模型支持 Chat Completions 文本输出")
                return parse_json(content)
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                # Upstream response bodies may echo credentials. Do not persist them.
                raise ProviderError(f"模型 API 返回 HTTP {exc.code}；请检查地址、密钥、额度与接口参数") from None
            except (URLError, TimeoutError, ConnectionError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ProviderError("模型 API 连接失败或超时，请检查网络和 API 地址") from None
            except (KeyError, IndexError, TypeError):
                raise ProviderError("API 响应格式与 Chat Completions 不兼容") from None
