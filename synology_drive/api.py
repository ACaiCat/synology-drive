from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal, Self, TypeVar

import httpx

from .models import (
    ADV_SHARE_ROLES,
    API_ADV_SHARING,
    API_ADV_SHARING_PUBLIC,
    API_TASKS,
    DEFAULT_API_PREFIX,
    DEFAULT_TIMEOUT,
    ERR_NO_ADV_SHARE_LINK,
    AdvanceShareLink,
    AsyncTask,
    FileList,
    FileListFilter,
    FileSortBy,
    FileSortDirection,
    LoginData,
    ShareLink,
    ShareUnlockResult,
    SynologyDriveAPIError,
    SynologyDriveError,
    SynologyDriveTimeoutError,
    SynoResponse,
    TaskList,
    TeamFolderList,
    UploadTask,
)
from .paths import resolve_path

WEBAPI_ENDPOINT = "/webapi/entry.cgi"
logger = logging.getLogger("synology_drive")
T = TypeVar("T")


class SynologyDriveClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_prefix: str = DEFAULT_API_PREFIX,
        verify: bool | str = True,
        timeout: float = DEFAULT_TIMEOUT,
        verbose: bool = False,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = "/" + api_prefix.strip("/")
        self.timeout = timeout
        self.verbose = verbose
        self._owns_client = client is None
        self._http = client or httpx.Client(
            base_url=self.base_url,
            verify=verify,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        self._sid: str | None = None
        self._did: str | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    @property
    def session_id(self) -> str | None:
        return self._sid

    @property
    def device_id(self) -> str | None:
        return self._did

    def _log(self, message: str) -> None:
        if self.verbose:
            logger.info("[syno] %s", message)

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}{self.api_prefix}/{endpoint.lstrip('/')}"

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        model: type[SynoResponse[T]],
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> SynoResponse[T]:
        url = self._url(endpoint)
        query = {k: v for k, v in (params or {}).items() if v is not None}
        self._log(f"{method} {url} params={query or '{}'} body={json_body or '{}'}")

        response = self._http.request(
            method,
            url,
            json=json_body,
            params=query or None,
            timeout=timeout if timeout is not None else self.timeout,
        )

        return self._parse_response(response, model=model, context=f"{method} {url}")

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        model: type[SynoResponse[T]],
        context: str,
    ) -> SynoResponse[T]:
        if self.verbose:
            self._log(f"<- {response.status_code} {response.text[:2000]}")

        if response.status_code >= 400:
            raise SynologyDriveError(
                f"HTTP {response.status_code}：{context} —— {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SynologyDriveError(
                f"响应不是合法 JSON：{response.text[:300]}"
            ) from exc

        if not isinstance(payload, dict):
            raise SynologyDriveError(f"响应不是对象：{payload!r}")

        parsed = model.model_validate(payload)
        if not parsed.success:
            code = parsed.error.code if parsed.error else -1
            raise SynologyDriveAPIError(code, payload)
        return parsed

    def _webapi_request(
        self,
        api: str,
        method: str,
        *,
        model: type[SynoResponse[T]],
        version: int = 1,
        params: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> SynoResponse[T]:
        url = f"{self.base_url}{WEBAPI_ENDPOINT}"
        payload: dict[str, Any] = {"api": api, "method": method, "version": version}
        if params:
            # 只丢 None。空串是有意义的（清密码就是发空串），必须留着。
            payload.update({k: v for k, v in params.items() if v is not None})
        if authenticated:
            if not self._sid:
                raise SynologyDriveError(f"调用 {api}.{method} 需要先登录")
            payload["_sid"] = self._sid

        self._log(f"POST {url} {payload}")
        response = self._http.post(url, data=payload, timeout=self.timeout)
        return self._parse_response(response, model=model, context=f"{api}.{method}")

    def login(
        self, account: str, passwd: str, *, otp: str | None = None
    ) -> SynoResponse[LoginData]:
        body: dict[str, Any] = {"format": "sid", "account": account, "passwd": passwd}
        if otp:
            body["otp_code"] = otp

        result = self._request(
            "POST", "/login", model=SynoResponse[LoginData], json_body=body
        )

        if result.data:
            self._sid = result.data.sid
            self._did = result.data.did
        if self._sid:
            self._http.cookies.set("id", self._sid)
        return result

    def logout(self) -> SynoResponse[dict[str, Any]]:
        if not self._sid:
            raise SynologyDriveError("还没登录，没有 sid 可以登出")
        result = self._request(
            "POST",
            "/logout",
            model=SynoResponse[dict[str, Any]],
            json_body={"_sid": self._sid},
        )
        self._sid = None
        self._did = None
        self._http.cookies.clear()
        return result

    def list_team_folders(
        self,
        *,
        sort_by: str | None = None,
        sort_direction: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SynoResponse[TeamFolderList]:
        return self._request(
            "GET",
            "/team-folder",
            model=SynoResponse[TeamFolderList],
            params={
                "sort_by": sort_by,
                "sort_direction": sort_direction,
                "limit": limit,
                "offset": offset,
            },
        )

    def list_ancestors(
        self, path: str | None = None, *, file_id: str | None = None
    ) -> SynoResponse[FileList]:
        return self._request(
            "GET",
            "/files/ancestors",
            model=SynoResponse[FileList],
            params={"path": resolve_path(path, file_id, None)},
        )

    def list_files(
        self,
        path: str | None = None,
        *,
        parent_id: str | None = None,
        sort_by: FileSortBy | None = None,
        sort_direction: FileSortDirection | None = None,
        limit: int | None = None,
        offset: int | None = None,
        file_filter: FileListFilter | dict[str, Any] | None = None,
        extra: Sequence[str] | None = None,
    ) -> SynoResponse[FileList]:
        body: dict[str, Any] = {}
        if file_filter is not None:
            if isinstance(file_filter, FileListFilter):
                # exclude_none：没填的条件不发出去，避免服务端把 null 当成"筛 null"
                body["filter"] = file_filter.model_dump(exclude_none=True)
            else:
                body["filter"] = file_filter
        if extra:
            body["extra"] = list(extra)

        return self._request(
            "POST",
            "/files/list",
            model=SynoResponse[FileList],
            params={
                "path": resolve_path(path, None, parent_id),
                "sort_by": sort_by,
                "sort_direction": sort_direction,
                "limit": limit,
                "offset": offset,
            },
            json_body=body,
        )

    def upload_from_dsm(
        self,
        dsm_paths: Sequence[str] | str,
        path: str | None = None,
        *,
        parent_id: str | None = None,
        conflict_action: Literal["overwrite", "autorename", "stop", "version", "skip"]
        | None = None,
    ) -> SynoResponse[UploadTask]:
        if isinstance(dsm_paths, str):
            dsm_paths = [dsm_paths]
        if not dsm_paths:
            raise ValueError("dsm_paths 不能为空")

        body: dict[str, Any] = {
            "dsm_paths": list(dsm_paths),
            "path": resolve_path(path, None, parent_id),
        }
        if conflict_action is not None:
            body["conflict_action"] = conflict_action

        return self._request(
            "PUT",
            "/files/upload-from-dsm",
            model=SynoResponse[UploadTask],
            json_body=body,
            timeout=max(self.timeout, 300.0),  # 大文件别用默认超时
        )

    def create_share_link(
        self, path: str | None = None, *, file_id: str | None = None
    ) -> SynoResponse[ShareLink]:
        return self._request(
            "POST",
            "/sharing/create-link",
            model=SynoResponse[ShareLink],
            json_body={"path": resolve_path(path, file_id, None)},
        )

    def list_tasks(self, *, version: int = 1) -> SynoResponse[TaskList]:
        return self._webapi_request(
            API_TASKS, "list", model=SynoResponse[TaskList], version=version
        )

    def find_task(self, task_id: str, *, version: int = 1) -> AsyncTask | None:
        for task in self.list_tasks(version=version).require_data().items:
            if task.task_id == task_id:
                return task
        return None

    def get_task(self, task_id: str, *, version: int = 1) -> SynoResponse[AsyncTask]:
        return self._webapi_request(
            API_TASKS,
            "get",
            model=SynoResponse[AsyncTask],
            version=version,
            params={"task_id": task_id},
        )

    def delete_task(
        self, task_id: str, *, version: int = 1
    ) -> SynoResponse[dict[str, Any]]:
        return self._webapi_request(
            API_TASKS,
            "delete",
            model=SynoResponse[dict[str, Any]],
            version=version,
            params={"task_id": task_id},
        )

    def wait_for_task(
        self,
        task_id: str,
        *,
        interval: float = 1.0,
        timeout: float = 300.0,
        on_progress: Callable[[AsyncTask], None] | None = None,
        version: int = 1,
    ) -> AsyncTask:
        deadline = time.monotonic() + timeout
        last: AsyncTask | None = None
        while True:
            task = self.find_task(task_id, version=version)
            if task is not None:
                last = task
                if on_progress is not None:
                    on_progress(task)
                if task.finished:
                    return task
            elif last is not None:
                return last
            if time.monotonic() >= deadline:
                if last is None:
                    raise SynologyDriveTimeoutError(
                        f"等了 {timeout} 秒，Tasks.list 里一直没出现过任务 {task_id}"
                    )
                raise SynologyDriveTimeoutError(
                    f"任务 {task_id} 等了 {timeout} 秒还没结束"
                    f"（status={last.status} progress={last.progress}）"
                )
            time.sleep(interval)

    def get_adv_share_link(
        self, path: str | None = None, *, file_id: str | None = None, version: int = 1
    ) -> SynoResponse[AdvanceShareLink]:
        return self._webapi_request(
            API_ADV_SHARING,
            "get",
            model=SynoResponse[AdvanceShareLink],
            version=version,
            params={"path": resolve_path(path, file_id, None)},
        )

    def create_adv_share_link(
        self, path: str | None = None, *, file_id: str | None = None, version: int = 1
    ) -> SynoResponse[AdvanceShareLink]:
        return self._webapi_request(
            API_ADV_SHARING,
            "create",
            model=SynoResponse[AdvanceShareLink],
            version=version,
            params={"path": resolve_path(path, file_id, None)},
        )

    def create_or_get_adv_share_link(
        self, path: str | None = None, *, file_id: str | None = None, version: int = 1
    ) -> SynoResponse[AdvanceShareLink]:
        try:
            return self.get_adv_share_link(path, file_id=file_id, version=version)
        except SynologyDriveAPIError as exc:
            if exc.code != ERR_NO_ADV_SHARE_LINK:
                raise
        return self.create_adv_share_link(path, file_id=file_id, version=version)

    def update_adv_share_link(
        self,
        path: str | None = None,
        *,
        file_id: str | None = None,
        sharing_link: str,
        role: Literal[
            "accesser",
            "previewer",
            "viewer",
            "commenter",
            "editor",
            "preview_commenter",
        ]
        | None = None,
        protect_password: str | None = None,
        due_date: int | None = None,
        version: int = 1,
    ) -> SynoResponse[AdvanceShareLink]:
        if role is not None and role not in ADV_SHARE_ROLES:
            raise ValueError(f"role 只能是 {', '.join(ADV_SHARE_ROLES)}，收到 {role!r}")
        return self._webapi_request(
            API_ADV_SHARING,
            "update",
            model=SynoResponse[AdvanceShareLink],
            version=version,
            params={
                "path": resolve_path(path, file_id, None),
                "sharing_link": sharing_link,
                "role": role,
                "protect_password": protect_password,
                "due_date": due_date,
            },
        )

    def delete_adv_share_link(
        self,
        path: str | None = None,
        *,
        file_id: str | None = None,
        sharing_link: str,
        version: int = 1,
    ) -> SynoResponse[dict[str, Any]]:
        return self._webapi_request(
            API_ADV_SHARING,
            "delete",
            model=SynoResponse[dict[str, Any]],
            version=version,
            params={
                "path": resolve_path(path, file_id, None),
                "sharing_link": sharing_link,
            },
        )

    def verify_share_password(
        self, sharing_link: str, password: str = "", *, version: int = 1
    ) -> SynoResponse[ShareUnlockResult]:
        return self._webapi_request(
            API_ADV_SHARING_PUBLIC,
            "auth",
            model=SynoResponse[ShareUnlockResult],
            version=version,
            params={"sharing_link": sharing_link, "password": password},
            authenticated=False,
        )
