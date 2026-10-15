import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .auth import Principal, authenticate
from .checkpointer import open_checkpointer, to_psycopg_dsn
from .config import Settings, get_settings
from .constants import ErrorCode
from .graph import GraphService, build_graph
from .knowledge_base import KnowledgeStore
from .model_factory import AgentModel
from .postgres_store import PostgresStore
from .schemas import (
    ApiResponse,
    AuditEventView,
    ConversationResponse,
    ConversationView,
    CreateConversationRequest,
    ErrorResponse,
    MessageRequest,
)
from .service import ConversationService
from .store import ConversationNotFoundError, InMemoryStore, StoreRequestError

logger = logging.getLogger(__name__)


def _trace_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-Id", "").strip()
    return supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else str(uuid4())


def _error_response(
    code: ErrorCode | str, message: str, trace_id: str, *, retryable: bool = False
) -> JSONResponse:
    payload = ErrorResponse(
        code=code, message=message, retryable=retryable, trace_id=trace_id
    ).model_dump(mode="json")
    return JSONResponse(status_code=_status_for_error(str(code)), content=payload)


def _status_for_error(code: str) -> int:
    return {
        ErrorCode.VALIDATION_ERROR.value: 400,
        ErrorCode.UNAUTHORIZED.value: 401,
        ErrorCode.CONVERSATION_NOT_FOUND.value: 404,
        ErrorCode.POLICY_NOT_FOUND.value: 422,
        ErrorCode.TOOL_TIMEOUT.value: 504,
        ErrorCode.TOOL_UNAVAILABLE.value: 503,
        ErrorCode.AGENT_LIMIT_EXCEEDED.value: 429,
        ErrorCode.AGENT_BUDGET_EXCEEDED.value: 429,
    }.get(code, 500)


async def _postgres_ready(database_url: str) -> bool:
    def probe() -> bool:
        import psycopg

        with psycopg.connect(
            to_psycopg_dsn(database_url), connect_timeout=2
        ) as connection:
            connection.execute("SELECT 1")
        return True

    try:
        return await asyncio.to_thread(probe)
    except (
        Exception
    ):  # noqa: BLE001 - readiness 必须降级为 down，不能让健康检查抛出 500。
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="GlobalCare Agent API",
        version="0.1.0",
        description="商品与售后知识咨询及官方入口引导的 REST 与 SSE 接口。",
    )
    app_settings = settings or get_settings()
    allowed_origins = [
        origin.strip()
        for origin in app_settings.cors_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Demo-Token", "X-Request-Id"],
    )
    policy_dir = Path(app_settings.policy_dir)
    if not policy_dir.is_absolute():
        policy_dir = Path(__file__).resolve().parents[2] / policy_dir
    policy_store = KnowledgeStore(
        policy_dir,
        backend=app_settings.policy_backend,
        embedding_mode=app_settings.embedding_mode,
        embedding_model=app_settings.embedding_model,
        embedding_dimensions=app_settings.embedding_dimensions,
        milvus_uri=app_settings.milvus_uri,
        milvus_db=app_settings.milvus_db,
        redis_url=app_settings.redis_url,
        allow_fallback=app_settings.policy_allow_fallback,
        require_approved=app_settings.environment in {"staging", "production"},
    )
    model = AgentModel(
        mode=app_settings.llm_mode,
        api_key=app_settings.minimax_api_key,
        base_url=app_settings.minimax_base_url,
        model_name=app_settings.minimax_model,
        temperature=app_settings.llm_temperature,
        allow_fallback=app_settings.llm_allow_fallback,
    )
    store = (
        PostgresStore(
            app_settings.database_url, app_settings.message_lock_timeout_seconds
        )
        if app_settings.persistence_mode == "postgres"
        else InMemoryStore(app_settings.message_lock_timeout_seconds)
    )
    graph_kwargs = {
        "model": model,
        "policy_store": policy_store,
        "purchase_url": app_settings.purchase_url,
        "aftersales_url": app_settings.aftersales_url,
        "max_steps": app_settings.max_graph_steps,
        "max_tool_calls": app_settings.max_tool_calls,
    }
    graph = build_graph(**graph_kwargs)
    conversation_service = ConversationService(
        store=store,
        graph_service=GraphService(
            graph,
            max_steps=app_settings.max_graph_steps,
            token_budget=app_settings.token_budget,
        ),
        run_timeout_seconds=app_settings.agent_run_timeout_seconds,
        max_active_runs=app_settings.max_active_runs,
    )
    app.state.settings = app_settings
    app.state.policy_store = policy_store
    app.state.store = store
    app.state.conversation_service = conversation_service

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            if isinstance(store, PostgresStore):
                await store.open()
            async with open_checkpointer(
                app_settings.checkpointer_mode, app_settings.database_url
            ) as checkpointer:
                if app_settings.checkpointer_mode.lower() == "postgres":
                    conversation_service.graph_service.graph = build_graph(
                        **graph_kwargs, checkpointer=checkpointer
                    )
                    app.state.checkpointer_state = "up"
                else:
                    app.state.checkpointer_state = "memory"
                yield
        finally:
            if isinstance(store, PostgresStore):
                await store.close()

    app.router.lifespan_context = lifespan

    async def demo_token_dependency(
        x_demo_token: Annotated[str | None, Header(alias="X-Demo-Token")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        access_cookie: Annotated[str | None, Cookie(alias="globalcare_access_token")] = None,
    ) -> Principal:
        return authenticate(
            app_settings,
            authorization or (f"Bearer {access_cookie}" if access_cookie else None),
            x_demo_token,
        )

    @app.get("/", include_in_schema=False)
    async def web_index():
        response = FileResponse(Path(__file__).resolve().parents[2] / "frontend" / "index.html")
        # 仅回环开发页面接收运行入口的身份票据，生产由既有登录系统提供。
        if (
            app_settings.environment in {"local", "development"}
            and app_settings.auth_mode == "jwt"
            and app_settings.web_access_token
        ):
            response.set_cookie(
                "globalcare_access_token", app_settings.web_access_token,
                httponly=True, samesite="strict", path="/",
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, _: RequestValidationError):
        return _error_response(
            ErrorCode.VALIDATION_ERROR, "请求字段校验失败", _trace_id(request)
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        code = (
            ErrorCode.UNAUTHORIZED
            if exc.status_code == 401
            else ErrorCode.INTERNAL_ERROR
        )
        if exc.status_code == 404:
            code = ErrorCode.CONVERSATION_NOT_FOUND
        return _error_response(code, str(exc.detail), _trace_id(request))

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found_handler(
        request: Request, _: ConversationNotFoundError
    ):
        return _error_response(
            ErrorCode.CONVERSATION_NOT_FOUND, "会话不存在", _trace_id(request)
        )

    @app.exception_handler(StoreRequestError)
    async def store_request_exception_handler(request: Request, exc: StoreRequestError):
        return JSONResponse(
            status_code=409,
            content={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "trace_id": _trace_id(request),
            },
        )

    @app.exception_handler(PermissionError)
    async def permission_exception_handler(request: Request, _: PermissionError):
        return _error_response(
            ErrorCode.UNAUTHORIZED, "Demo Token 无效或无权访问", _trace_id(request)
        )

    @app.get("/healthz", response_model=ApiResponse[dict[str, str]], tags=["system"])
    async def healthz(request: Request):
        trace_id = _trace_id(request)
        return ApiResponse(
            data={
                "status": "ok",
                "service": app_settings.app_name,
                "version": app_settings.app_version,
            },
            trace_id=trace_id,
        )

    @app.get("/readyz", response_model=ApiResponse[dict[str, str]], tags=["system"])
    async def readyz(request: Request):
        trace_id = _trace_id(request)
        # memory 模式是明确的离线 fallback，不伪装成 PostgreSQL 已连接。
        postgres_state = "degraded"
        if (
            app_settings.checkpointer_mode == "postgres"
            or app_settings.persistence_mode == "postgres"
        ):
            postgres_state = (
                "up" if await _postgres_ready(app_settings.database_url) else "down"
            )
        store_state = (
            "up"
            if isinstance(store, InMemoryStore)
            else "up" if await store.ready() else "down"
        )
        # memory 后端如实报告 degraded；milvus 后端必须真实探测 Milvus 与 Redis。
        try:
            async with asyncio.timeout(4):
                knowledge_state = await asyncio.to_thread(policy_store.probe)
        except Exception:  # noqa: BLE001 - 探测超时视为 down。
            knowledge_state = {"milvus": "down", "redis": "down"}
        model_state = (
            "configured"
            if app_settings.llm_mode.lower() == "mock" or app_settings.minimax_api_key
            else "missing"
        )
        ready = (
            store_state == "up"
            and model_state != "missing"
            and postgres_state != "down"
            and (
                app_settings.policy_backend != "milvus"
                or set(knowledge_state.values()) == {"up"}
            )
        )
        payload = ApiResponse(
            data={
                "status": "ready" if ready else "not_ready",
                "store": store_state,
                "postgres": postgres_state,
                "milvus": knowledge_state["milvus"],
                "redis": knowledge_state["redis"],
                "knowledge_backend": policy_store.active_backend,
                "model_config": model_state,
            },
            trace_id=trace_id,
        )
        return JSONResponse(
            status_code=200 if ready else 503, content=payload.model_dump(mode="json")
        )

    @app.post(
        "/api/v1/conversations",
        response_model=ApiResponse[ConversationResponse],
        tags=["conversation"],
    )
    async def create_conversation(
        request: Request,
        body: CreateConversationRequest,
        token: Principal = Depends(demo_token_dependency),
    ):
        result = await conversation_service.create_conversation(
            token.subject_key, body, actor_context=token.actor_context
        )
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
        token: Principal = Depends(demo_token_dependency),
    ):
        await store.get_conversation(conversation_id, token.subject_key)
        trace_id = _trace_id(request)

        async def event_stream():
            async for event in conversation_service.stream_message(
                conversation_id, token.subject_key, body, trace_id
            ):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Trace-Id": trace_id,
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/v1/conversations/{conversation_id}",
        response_model=ApiResponse[ConversationView],
        tags=["conversation"],
    )
    async def get_conversation(
        request: Request,
        conversation_id: UUID,
        token: Principal = Depends(demo_token_dependency),
    ):
        result = await conversation_service.conversation_view(
            conversation_id, token.subject_key
        )
        return ApiResponse(data=result, trace_id=_trace_id(request))

    @app.get(
        "/api/v1/conversations/{conversation_id}/audit",
        response_model=ApiResponse[list[AuditEventView]],
        tags=["conversation"],
    )
    async def get_conversation_audit(
        request: Request,
        conversation_id: UUID,
        token: Principal = Depends(demo_token_dependency),
    ):
        result = await conversation_service.audit_views(
            conversation_id, token.subject_key
        )
        return ApiResponse(data=result, trace_id=_trace_id(request))

    return app


app = create_app()
