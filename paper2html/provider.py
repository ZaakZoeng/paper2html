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


class ModelOutputError(ValueError):
    """A successful transport with invalid model JSON can be repaired."""
    def __init__(self, message, output=None):
        super().__init__(message)
        self.output = output if isinstance(output, str) and len(output) <= 60000 else None


class OutputTruncated(ModelOutputError, ProviderError):
    """Repair within the user's token budget rather than silently raising it."""


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
    metadata_lookup: bool = True

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
    text = text.lstrip('\ufeff').strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text:
        raise ModelOutputError('模型返回空文本，请检查接口的文本输出配置')
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        # Remove only commas followed by a closing delimiter OUTSIDE strings.
        chars, quoted, escaped = [], False, False
        for i, char in enumerate(text):
            if quoted:
                chars.append(char)
                if escaped: escaped = False
                elif char == '\\': escaped = True
                elif char == '"': quoted = False
            elif char == '"':
                quoted = True; chars.append(char)
            elif char == ',':
                following = i+1
                while following < len(text) and text[following].isspace():
                    following += 1
                if following < len(text) and text[following] in '}]':
                    continue
                chars.append(char)
            else:
                chars.append(char)
        try:
            value = json.loads(''.join(chars))
        except json.JSONDecodeError:
            raise ModelOutputError(f'模型正文不是有效 JSON（第 {exc.lineno} 行、第 {exc.colno} 列）；请修复语法并保留原有内容', text) from None
    if not isinstance(value, dict):
        raise ModelOutputError("模型输出必须为 JSON 对象", text)
    return value


class CompatibleProvider:
    """Chat Completions compatible HTTP protocol; no SDK or tool calling required."""

    def __init__(self, config):
        self.config = config
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.on_event = None

    def event(self, **event):
        if self.on_event:
            self.on_event(**event)

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
                from .store import now
                self.event(request_state='等待 API 响应', attempt=attempt+1, request_started_at=now())
                request = Request(endpoint, json.dumps(body).encode(), headers=headers)
                with build_opener(NoRedirect()).open(request, timeout=cfg.timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ProviderError("API 响应超过 8 MB")
                self.usage["calls"] += 1
                if not raw.strip():
                    raise ProviderError('API 返回空响应；请检查 Base URL 和 Chat Completions 接口')
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise ProviderError('API 响应不是有效 JSON；可能返回了网页或网关错误，请检查 Base URL') from None
                if not isinstance(result, dict):
                    raise ProviderError('API 响应顶层必须是 JSON 对象')
                usage = result.get("usage") or {}
                for key in ("prompt_tokens", "completion_tokens"):
                    value = usage.get(key, 0) if isinstance(usage, dict) else 0
                    if type(value) is int and value >= 0:
                        self.usage[key] += value
                choice = result["choices"][0]
                if choice.get("finish_reason") in ("length", "max_tokens"):
                    raise OutputTruncated("模型输出被截断；请压缩非必要措辞，优先保留必需结构、证据和图号，不要重复输出由程序装配的内容", choice.get('message', {}).get('content'))
                content = choice["message"].get("content")
                if not isinstance(content, str):
                    raise ProviderError("API 未返回文本，请确认模型支持 Chat Completions 文本输出")
                return parse_json(content)
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                # Upstream response bodies may echo credentials. Do not persist them.
                remedies = {401:'密钥无效或已过期', 403:'接口拒绝访问，请检查密钥权限、模型权限或网关限制',
                            404:'接口路径或模型不存在，请检查 Base URL 和模型 ID',
                            429:'服务限流或额度不足，重试后仍未成功'}
                raise ProviderError(f"模型 API 返回 HTTP {exc.code}；" + remedies.get(exc.code, '请检查服务状态与接口参数')) from None
            except (URLError, TimeoutError, ConnectionError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ProviderError("模型 API 连接失败或超时，请检查网络和 API 地址") from None
            except (KeyError, IndexError, TypeError, AttributeError):
                raise ProviderError("API 响应格式与 Chat Completions 不兼容") from None
            finally:
                self.event(request_state='API 调用结束')
