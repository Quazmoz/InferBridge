"""OpenAI-compatible request/response models (Pydantic v2).

These mirror the subset of the OpenAI API the server implements: chat
completions (with streaming + tool calling), multimodal image inputs, and the
Responses API used by tools like n8n. They are plain data models with no
OpenVINO dependency.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app import multimodal
from runtime import device_check

# --- Chat messages ---------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: Any = None  # str, list of content parts, or None (OpenAI spec allows all three)
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


# --- Tool / function calling ----------------------------------------------


class FunctionDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=16_384)
    parameters: dict[str, Any] | None = None


class ToolDefinition(BaseModel):
    type: str = Field(default="function", pattern=r"^function$")
    function: FunctionDefinition


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


# --- Chat completions ------------------------------------------------------


class StreamOptions(BaseModel):
    include_usage: bool | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1, max_length=4096)
    max_tokens: int | None = Field(default=512, ge=1)
    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float | None = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool | None = False
    stream_options: StreamOptions | None = None
    tools: list[ToolDefinition] | None = Field(default=None, max_length=128)
    tool_choice: Any | None = None  # "auto" | "none" | "required" | {type, function}
    stop: str | list[str] | None = None  # stop sequence(s) that end generation
    seed: int | None = None  # seed the sampler for reproducible output (best effort)
    response_format: dict[str, Any] | None = None  # JSON object or JSON Schema constraint
    lora_path: str | None = None  # path to safetensors LoRA weights
    lora_alpha: float | None = Field(default=1.0, gt=0.0)

    @model_validator(mode="before")
    @classmethod
    def preflight_request_images(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        messages = value.get("messages") or []
        contents = [
            message.content if isinstance(message, ChatMessage) else message.get("content")
            for message in messages
            if isinstance(message, ChatMessage | dict)
        ]
        roles = [
            message.role if isinstance(message, ChatMessage) else str(message.get("role", "user"))
            for message in messages
            if isinstance(message, ChatMessage | dict)
        ]
        multimodal.preflight_request_contents(contents, roles=roles)
        return value


class ChatCompletionMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatCompletionMessage
    finish_reason: str


class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionResponseChoice]
    usage: UsageInfo | None = None


# --- Responses API (n8n / OpenAI-compatible clients) ---------------------


class ResponseFunctionTool(BaseModel):
    """Responses-style flat function tool with Chat Completions compatibility."""

    type: str = Field(default="function", pattern=r"^function$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=16_384)
    parameters: dict[str, Any] | None = None
    strict: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_chat_completions_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        function = value.get("function")
        if not isinstance(function, dict):
            return value
        return {
            "type": value.get("type", "function"),
            "name": function.get("name"),
            "description": function.get("description"),
            "parameters": function.get("parameters"),
            "strict": function.get("strict"),
        }

    def as_chat_tool(self) -> ToolDefinition:
        return ToolDefinition(
            type="function",
            function=FunctionDefinition(
                name=self.name,
                description=self.description,
                parameters=self.parameters,
            ),
        )


class ResponseTextConfig(BaseModel):
    format: dict[str, Any] | None = None

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        format_type = value.get("type", "text")
        if format_type not in {"text", "json_object", "json_schema"}:
            raise ValueError(
                "Responses text.format.type must be text, json_object, or json_schema."
            )
        if format_type == "json_schema":
            schema = value.get("schema")
            name = value.get("name")
            if not isinstance(schema, dict):
                raise ValueError("Responses json_schema format requires a schema object.")
            if not isinstance(name, str) or not name or len(name) > 64:
                raise ValueError("Responses json_schema format requires a 1-64 character name.")
            normalized = name.replace("_", "").replace("-", "")
            if not normalized.isalnum():
                raise ValueError(
                    "Responses json_schema format name may contain only letters, numbers, _ and -."
                )
        return value


class ResponseRequest(BaseModel):
    model: str
    input: Any  # string or list of {role, content} messages
    instructions: str | None = Field(default=None, max_length=multimodal.MAX_REQUEST_TEXT_CHARS)
    max_output_tokens: int | None = Field(default=512, ge=1)
    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float | None = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool | None = False
    tools: list[ResponseFunctionTool] | None = Field(default=None, max_length=128)
    tool_choice: Any | None = None
    parallel_tool_calls: bool | None = True
    text: ResponseTextConfig | None = None
    response_format: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    seed: int | None = None
    lora_path: str | None = None
    lora_alpha: float | None = Field(default=1.0, gt=0.0)

    @field_validator("input")
    @classmethod
    def validate_multimodal_input(cls, value: Any) -> Any:
        if isinstance(value, str):
            multimodal.preflight_request_contents([value])
            return value
        if not isinstance(value, list):
            raise ValueError("Responses 'input' must be a string or a list of messages.")
        if len(value) > 4096:
            raise ValueError("Responses input may contain at most 4,096 messages.")
        if not all(isinstance(message, dict) for message in value):
            raise ValueError("Each Responses input message must be an object.")
        multimodal.preflight_request_contents(
            [message.get("content") for message in value],
            roles=[str(message.get("role", "user")) for message in value],
        )
        return value

    @field_validator("tool_choice")
    @classmethod
    def validate_tool_choice(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            if value in {"auto", "none", "required"}:
                return value
            raise ValueError("Responses tool_choice must be auto, none, required, or a function.")
        if not isinstance(value, dict) or value.get("type") != "function":
            raise ValueError(
                "InferBridge Responses tool_choice supports auto, none, required, or a function."
            )
        name = value.get("name")
        if name is None and isinstance(value.get("function"), dict):
            name = value["function"].get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Responses function tool_choice requires a function name.")
        return value

    @model_validator(mode="after")
    def validate_output_format_aliases(self):
        if (
            self.response_format is not None
            and self.text is not None
            and self.text.format is not None
        ):
            raise ValueError("Use either Responses text.format or response_format, not both.")
        return self

    def chat_tools(self) -> list[ToolDefinition] | None:
        if not self.tools:
            return None
        return [tool.as_chat_tool() for tool in self.tools]

    def chat_tool_choice(self) -> Any:
        choice = self.tool_choice
        if not isinstance(choice, dict):
            return choice
        name = choice.get("name")
        if name is None and isinstance(choice.get("function"), dict):
            name = choice["function"].get("name")
        return {"type": "function", "function": {"name": name}}

    def generation_response_format(self) -> dict[str, Any] | None:
        """Translate Responses text.format to the shared OpenVINO generation shape."""

        if self.response_format is not None:
            return self.response_format
        if self.text is None or self.text.format is None:
            return None
        response_format = self.text.format
        format_type = response_format.get("type", "text")
        if format_type == "text":
            return None
        if format_type == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_format.get("name"),
                "schema": response_format.get("schema"),
                "strict": response_format.get("strict"),
            },
        }

    def response_text_payload(self) -> dict[str, Any]:
        """Return the OpenAI-style text configuration echoed in Response objects."""

        if self.text is not None:
            return self.text.model_dump(exclude_none=True)
        if self.response_format is None:
            return {"format": {"type": "text"}}

        format_type = self.response_format.get("type", "text")
        if format_type == "json_schema":
            schema = self.response_format.get("json_schema") or {}
            format_payload = {
                "type": "json_schema",
                "name": schema.get("name") or "response",
                "schema": schema.get("schema") or {},
            }
            if schema.get("strict") is not None:
                format_payload["strict"] = schema["strict"]
            return {"format": format_payload}
        if format_type == "json_object":
            return {"format": {"type": "json_object"}}
        return {"format": {"type": "text"}}


class ResponseOutputMessage(BaseModel):
    type: str = "message"
    id: str
    status: str = "completed"
    role: str = "assistant"
    content: list[dict[str, Any]]


class ResponseFunctionCall(BaseModel):
    type: str = "function_call"
    id: str
    call_id: str
    name: str
    arguments: str
    status: str = "completed"


class ResponseUsage(BaseModel):
    input_tokens: int
    input_tokens_details: dict[str, int] = Field(default_factory=lambda: {"cached_tokens": 0})
    output_tokens: int
    output_tokens_details: dict[str, int] = Field(default_factory=lambda: {"reasoning_tokens": 0})
    total_tokens: int


class ResponseObject(BaseModel):
    id: str
    object: str = "response"
    created_at: int
    completed_at: int | None = None
    model: str
    output: list[ResponseOutputMessage | ResponseFunctionCall]
    status: str = "completed"
    error: dict[str, Any] | None = None
    incomplete_details: dict[str, Any] | None = None
    instructions: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    tool_choice: Any | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    parallel_tool_calls: bool | None = None
    text: dict[str, Any] | None = None
    usage: ResponseUsage | None = None


# --- Model lifecycle requests ---------------------------------------------


class ModelLoadRequest(BaseModel):
    model: str
    device: str | None = None  # override the server default device for this load
    draft_model: str | None = (
        None  # catalog model id or path to draft model for speculative decoding
    )


class ModelConvertRequest(BaseModel):
    model: str
    device: str | None = None  # optional device to use if loading after conversion
    load_after: bool = True
    weight_format: str | None = Field(default=None, pattern=r"^(int4|int8|fp16)$")
    group_size: int | None = Field(default=None, ge=-1)
    ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    sym: bool | None = None
    trust_remote_code: bool | None = None


class ModelUnloadRequest(BaseModel):
    model: str


class ModelDeleteRequest(BaseModel):
    model: str


class ModelRegisterRequest(BaseModel):
    model_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="Filesystem-safe model identifier, for example smollm2-135m-int4.",
    )
    name: str = Field(min_length=1, max_length=160)
    source_model: str = Field(min_length=1, max_length=240)
    backend: str = Field(
        default="openvino-genai",
        pattern=r"^(openvino-genai|openvino-embeddings|openvino-vlm)$",
    )
    weight_format: str = Field(default="int4", pattern=r"^(int4|int8|fp16)$")
    recommended_device: str = Field(default="NPU", min_length=1, max_length=64)
    max_context_len: int = Field(default=2048, ge=128, le=262144)
    max_output_tokens: int = Field(default=512, ge=0, le=65536)
    description: str | None = None
    trust_remote_code: bool = False

    @field_validator("recommended_device")
    @classmethod
    def validate_recommended_device(cls, value: str) -> str:
        try:
            return device_check.validate_device_expression(value)
        except device_check.DeviceValidationError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_token_budget(self):
        if self.backend == "openvino-embeddings":
            if self.max_output_tokens != 0:
                raise ValueError("Embedding models must use max_output_tokens=0.")
        elif self.max_output_tokens < 1 or self.max_output_tokens >= self.max_context_len:
            raise ValueError(
                "Generation models require max_output_tokens between 1 and max_context_len - 1."
            )
        return self


# --- Benchmark requests ---------------------------------------------------


class BenchmarkRunRequest(BaseModel):
    model: str | None = None
    models: list[str] | None = None
    devices: list[str] = Field(default_factory=lambda: ["CPU", "GPU", "NPU", "AUTO"])
    prompt: str | None = None
    max_tokens: int = Field(default=64, ge=1, le=4096)
    runs: int = Field(default=1, ge=1, le=10)


# --- Conversation export --------------------------------------------------


class ChatExportRequest(BaseModel):
    messages: list[ChatMessage] = Field(max_length=4096)
    model: str | None = None
    device: str | None = None

    @model_validator(mode="before")
    @classmethod
    def preflight_export_images(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        messages = value.get("messages") or []
        multimodal.preflight_request_contents(
            [
                message.content if isinstance(message, ChatMessage) else message.get("content")
                for message in messages
                if isinstance(message, ChatMessage | dict)
            ],
            roles=[
                message.role
                if isinstance(message, ChatMessage)
                else str(message.get("role", "user"))
                for message in messages
                if isinstance(message, ChatMessage | dict)
            ],
        )
        return value


# --- Embeddings -----------------------------------------------------------


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    user: str | None = None
    encoding_format: str | None = Field(default="float", pattern=r"^(float|base64)$")

    @field_validator("input")
    @classmethod
    def validate_embedding_input(cls, value: str | list[str]) -> str | list[str]:
        texts = [value] if isinstance(value, str) else value
        if not texts:
            raise ValueError("Embedding input cannot be empty.")
        if len(texts) > 256:
            raise ValueError("Embedding input may contain at most 256 texts.")
        if not all(isinstance(text, str) for text in texts):
            raise ValueError("Every embedding input must be a string.")
        if any(not text for text in texts):
            raise ValueError("Embedding input strings cannot be empty.")
        total_chars = sum(len(text) for text in texts)
        if total_chars > multimodal.MAX_REQUEST_TEXT_CHARS:
            raise ValueError(
                f"Embedding input may contain at most {multimodal.MAX_REQUEST_TEXT_CHARS:,} characters."
            )
        return value


class EmbeddingResponseData(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float] | str


class EmbeddingResponseUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingResponseData]
    model: str
    usage: EmbeddingResponseUsage


class DownloadCustomRequest(BaseModel):
    model_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="Filesystem-safe model identifier, for example smollm2-135m-int4.",
    )
    name: str = Field(min_length=1, max_length=160)
    source_model: str = Field(min_length=1, max_length=240)
    backend: str = Field(
        default="openvino-genai",
        pattern=r"^(openvino-genai|openvino-embeddings|openvino-vlm)$",
    )
    weight_format: str = Field(default="int4", pattern=r"^(int4|int8|fp16)$")
    group_size: int | None = Field(default=None, ge=-1)
    ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    sym: bool | None = None
    recommended_device: str = Field(default="CPU", min_length=1, max_length=64)
    max_context_len: int = Field(default=2048, ge=128, le=262144)
    max_output_tokens: int = Field(default=512, ge=0, le=65536)
    description: str | None = None
    load_after: bool = True
    trust_remote_code: bool = False

    @field_validator("recommended_device")
    @classmethod
    def validate_recommended_device(cls, value: str) -> str:
        try:
            return device_check.validate_device_expression(value)
        except device_check.DeviceValidationError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_token_budget(self):
        if self.backend == "openvino-embeddings":
            if self.max_output_tokens != 0:
                raise ValueError("Embedding models must use max_output_tokens=0.")
        elif self.max_output_tokens < 1 or self.max_output_tokens >= self.max_context_len:
            raise ValueError(
                "Generation models require max_output_tokens between 1 and max_context_len - 1."
            )
        return self
