import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .business import build_business_gateway
from .checkpointer import open_checkpointer, to_psycopg_dsn
from .config import Settings, get_settings
from .constants import ErrorCode
from .graph import GraphService, build_graph
from .model_factory import AgentModel
from .policy_store import PolicyStore
from .schemas import (
    ApiResponse,
    AuditEventView,
    CaseDraftView,
    ConversationResponse,
    ConversationView,
    CreateConversationRequest,
    ErrorResponse,
    MessageRequest,
)
from .service import ConversationService
from .store import CaseDraftNotFoundError, ConversationNotFoundError, InMemoryStore

logger = logging.getLogger(__name__)


def _trace_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-Id", "").strip()
    return supplied[:64] or str(uuid4())


def _error_response(code: ErrorCode | str, message: str, trace_id: str, *, retryable: bool = False) -> JSONResponse:
    payload = ErrorResponse(code=code, message=message, retryable=retryable, trace_id=trace_id).model_dump(mode="json")
    return JSONResponse(status_code=_status_for_error(str(code)), content=payload)


def _status_for_error(code: str) -> int:
    return {
        ErrorCode.VALIDATION_ERROR.value: 400,
        ErrorCode.UNAUTHORIZED.value: 401,
        ErrorCode.CONVERSATION_NOT_FOUND.value: 404,
        ErrorCode.CASE_DRAFT_NOT_FOUND.value: 404,
        ErrorCode.POLICY_NOT_FOUND.value: 422,
        ErrorCode.TOOL_TIMEOUT.value: 504,
        ErrorCode.TOOL_UNAVAILABLE.value: 503,
        ErrorCode.AGENT_LIMIT_EXCEEDED.value: 429,
        ErrorCode.AGENT_BUDGET_EXCEEDED.value: 429,
        ErrorCode.CASE_DRAFT_VALIDATION_FAILED.value: 422,
    }.get(code, 500)


async def _postgres_ready(database_url: str) -> bool:
    def probe() -> bool:
        import psycopg

        with psycopg.connect(to_psycopg_dsn(database_url), connect_timeout=2) as connection:
            connection.execute("SELECT 1")
        return True

    try:
        return await asyncio.to_thread(probe)
    except Exception:  # noqa: BLE001 - readiness 必须降级为 down，不能让健康检查抛出 500。
        return False


async def _mcp_ready(mcp_url: str) -> bool:
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {"globalcare": {"transport": "streamable_http", "url": mcp_url}}
        )
        return bool(await client.get_tools())
    except Exception:  # noqa: BLE001 - MCP 会话/传输任意失败都应被报告为 down。
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="GlobalCare Agent API",
        version="0.1.0",
        description="售后 Case 智能受理 Agent 的 REST 与 SSE 接口。",
    )
    app_settings = settings or get_settings()
    allowed_origins = [origin.strip() for origin in app_settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Demo-Token", "X-Request-Id"],
    )
    policy_dir = Path(app_settings.policy_dir)
    if not policy_dir.is_absolute():
        policy_dir = Path(__file__).resolve().parents[1] / "data" / "policies"
    policy_store = PolicyStore(
        policy_dir,
        backend=app_settings.policy_backend,
        database_url=app_settings.database_url,
        embedding_mode=app_settings.embedding_mode,
        embedding_model=app_settings.embedding_model,
        embedding_dimensions=app_settings.embedding_dimensions,
    )
    model = AgentModel(
        mode=app_settings.llm_mode,
        api_key=app_settings.minimax_api_key,
        base_url=app_settings.minimax_base_url,
        model_name=app_settings.minimax_model,
        temperature=app_settings.llm_temperature,
    )
    query_mode = "mcp" if app_settings.mcp_mode.lower() in {"http", "streamable_http"} else app_settings.business_mode
    query_gateway = build_business_gateway(
        query_mode,
        base_url=app_settings.business_base_url,
        timeout_seconds=app_settings.business_timeout_seconds,
        mcp_url=app_settings.mcp_url,
    )
    draft_gateway = build_business_gateway(
        app_settings.business_mode,
        base_url=app_settings.business_base_url,
        timeout_seconds=app_settings.business_timeout_seconds,
        mcp_url=app_settings.mcp_url,
    )
    store = InMemoryStore()
    graph_kwargs = {
        "model": model,
        "policy_store": policy_store,
        "query_gateway": query_gateway,
        "draft_gateway": draft_gateway,
        "max_steps": app_settings.max_graph_steps,
    }
    graph = build_graph(**graph_kwargs)
    conversation_service = ConversationService(
        store=store,
        graph_service=GraphService(
            graph, max_steps=app_settings.max_graph_steps, token_budget=app_settings.token_budget
        ),
    )
    app.state.settings = app_settings
    app.state.policy_store = policy_store
    app.state.store = store
    app.state.conversation_service = conversation_service

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with open_checkpointer(app_settings.checkpointer_mode, app_settings.database_url) as checkpointer:
            if app_settings.checkpointer_mode.lower() == "postgres":
                conversation_service.graph_service.graph = build_graph(**graph_kwargs, checkpointer=checkpointer)
                app.state.checkpointer_state = "up"
            else:
                app.state.checkpointer_state = "memory"
            yield

    app.router.lifespan_context = lifespan

    async def demo_token_dependency(
        x_demo_token: Annotated[str | None, Header(alias="X-Demo-Token")] = None,
    ) -> str:
        if not x_demo_token or x_demo_token != app_settings.demo_token:
            raise HTTPException(status_code=401, detail=ErrorCode.UNAUTHORIZED.value)
        return x_demo_token

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, _: RequestValidationError):
        return _error_response(ErrorCode.VALIDATION_ERROR, "请求字段校验失败", _trace_id(request))

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        code = ErrorCode.UNAUTHORIZED if exc.status_code == 401 else ErrorCode.INTERNAL_ERROR
        if exc.status_code == 404:
            code = ErrorCode.CONVERSATION_NOT_FOUND
        return _error_response(code, str(exc.detail), _trace_id(request))

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found_handler(request: Request, _: ConversationNotFoundError):
        return _error_response(ErrorCode.CONVERSATION_NOT_FOUND, "会话不存在", _trace_id(request))

    @app.exception_handler(CaseDraftNotFoundError)
    async def draft_not_found_handler(request: Request, _: CaseDraftNotFoundError):
        return _error_response(ErrorCode.CASE_DRAFT_NOT_FOUND, "Case Draft 不存在", _trace_id(request))

    @app.exception_handler(PermissionError)
    async def permission_exception_handler(request: Request, _: PermissionError):
        return _error_response(ErrorCode.UNAUTHORIZED, "Demo Token 无效或无权访问", _trace_id(request))

    @app.get("/healthz", response_model=ApiResponse[dict[str, str]], tags=["system"])
    async def healthz(request: Request):
        trace_id = _trace_id(request)
        return ApiResponse(
            data={"status": "ok", "service": app_settings.app_name, "version": app_settings.app_version},
            trace_id=trace_id,
        )

    @app.get("/readyz", response_model=ApiResponse[dict[str, str]], tags=["system"])
    async def readyz(request: Request):
        trace_id = _trace_id(request)
        # memory 模式是明确的离线 fallback，不伪装成 PostgreSQL 已连接。
        postgres_state = "degraded"
        if app_settings.checkpointer_mode.lower() == "postgres":
            postgres_state = "up" if await _postgres_ready(app_settings.database_url) else "down"
        # 根据实际 PolicyStore 实例判断 pgvector 是否创建成功。
        pgvector_state = "up" if policy_store.pg_vector_store is not None else "degraded"
        mcp_state = "degraded"
        if app_settings.mcp_mode.lower() in {"http", "streamable_http"}:
            mcp_state = "up" if await _mcp_ready(app_settings.mcp_url) else "down"
        model_state = "configured" if app_settings.llm_mode.lower() == "mock" or app_settings.minimax_api_key else "missing"
        return ApiResponse(
            data={
                "status": "ready",
                "postgres": postgres_state,
                "pgvector": pgvector_state,
                "mcp": mcp_state,
                "model_config": model_state,
            },
            trace_id=trace_id,
        )

    @app.post("/api/v1/conversations", response_model=ApiResponse[ConversationResponse], tags=["conversation"])
    async def create_conversation(
        request: Request,
        body: CreateConversationRequest,
        token: str = Depends(demo_token_dependency),
    ):
        result = await conversation_service.create_conversation(token, body)
        return ApiResponse(data=result, trace_id=_trace_id(request))

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages/stream",
        responses={200: {"content": {"text/event-stream": {}}}},
        tags=["conversation"],
    )
    async def stream_message(
        request: Request,
        conversation_id: UUID,
        body: MessageRequest,
        token: str = Depends(demo_token_dependency),
    ):
        await store.get_conversation(conversation_id, token)
        trace_id = _trace_id(request)

        async def event_stream():
            async for event in conversation_service.stream_message(conversation_id, token, body, trace_id):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Trace-Id": trace_id},
        )

    @app.get("/api/v1/conversations/{conversation_id}", response_model=ApiResponse[ConversationView], tags=["conversation"])
    async def get_conversation(
        request: Request,
        conversation_id: UUID,
        token: str = Depends(demo_token_dependency),
    ):
        result = await conversation_service.conversation_view(conversation_id, token)
        return ApiResponse(data=result, trace_id=_trace_id(request))

    @app.get(
        "/api/v1/conversations/{conversation_id}/audit",
        response_model=ApiResponse[list[AuditEventView]],
        tags=["conversation"],
    )
    async def get_conversation_audit(
        request: Request,
        conversation_id: UUID,
        token: str = Depends(demo_token_dependency),
    ):
        result = await conversation_service.audit_views(conversation_id, token)
        return ApiResponse(data=result, trace_id=_trace_id(request))

    @app.get("/api/v1/case-drafts/{case_draft_id}", response_model=ApiResponse[CaseDraftView], tags=["case-draft"])
    async def get_case_draft(
        request: Request,
        case_draft_id: UUID,
        token: str = Depends(demo_token_dependency),
    ):
        result = await conversation_service.draft_view(case_draft_id, token)
        return ApiResponse(data=result, trace_id=_trace_id(request))

    return app


app = create_app()
