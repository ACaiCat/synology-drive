"""Synology Drive response models, constants, and path helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_API_PREFIX = "/api/SynologyDrive/default/v2"

WEBAPI_ENDPOINT = "/webapi/entry.cgi"

API_TASKS = "SYNO.SynologyDrive.Tasks"
API_ADV_SHARING = "SYNO.SynologyDrive.AdvanceSharing"
API_ADV_SHARING_PUBLIC = "SYNO.SynologyDrive.AdvanceSharing.Public"

DEFAULT_TIMEOUT = 30.0

TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_FINISHED = "finished"

ADV_SHARE_ROLES = (
    "accesser",
    "previewer",
    "viewer",
    "commenter",
    "editor",
    "preview_commenter",
)


ERR_NO_ADV_SHARE_LINK = 1036

FileSortBy = Literal["modified_time", "size", "owner", "type", "name"]
FileSortDirection = Literal["asc", "desc"]

ERROR_MESSAGES = {
    400: "账号或密码错误",
    401: "账号已被停用",
    402: "账号已被停用",
    403: "需要两步验证码（OTP）",
    404: "两步验证码错误",
    407: "IP 已被封禁",
    408: "密码已过期",
    102: "没有这个 API，检查 api / version 拼对了没有",
    103: "这个 API 没有这个方法名",
    104: "这个 API 不支持请求的 version",
    105: "账号权限不够（这个接口的 authLevel 比当前账号高）",
    106: "没有登录，或会话无效",
    107: "会话被另一个登录顶掉了",
    119: "会话已过期，需要重新登录",
    ERR_NO_ADV_SHARE_LINK: "这个文件还没有高级分享链接",
    1037: "分享链接的访问密码不对",
}


class SynologyDriveError(Exception):
    """本客户端的通用异常。"""


class SynologyDriveAPIError(SynologyDriveError):
    """服务端返回了 success=false。"""

    def __init__(self, code: int, payload: dict[str, Any] | None = None):
        self.code = code
        self.payload = payload
        detail = ""
        if isinstance(payload, dict):
            # error 里除了 code 还可能有说明，规范没写但服务端会给。真机上它藏在
            # error.errors.message 里（外面那层 error.message 通常是空的），
            # 比如 {"code": 1000, "errors": {"line": 61, "message": "get task failed"}}。
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                nested = error.get("errors")
                if not message and isinstance(nested, dict):
                    message = nested.get("message")
                detail = str(message or "")
        hint = ERROR_MESSAGES.get(code)
        message = f"接口返回失败：error.code={code}"
        if detail:
            message += f"，{detail}"
        if hint:
            message += f"（{hint}）"
        super().__init__(message)


class SynologyDriveTimeoutError(SynologyDriveError):
    """等异步任务的结果等超时了。"""


class SynoModel(BaseModel):
    """所有模型的基类：允许服务端多返回字段，不同 DSM 版本字段有出入。"""

    model_config = ConfigDict(extra="allow")


class SynoError(SynoModel):
    code: int = Field(description="错误码。")


T = TypeVar("T")


class SynoResponse(SynoModel, Generic[T]):
    """统一响应信封：`{success, data, error}`。"""

    success: bool
    data: T | None = None
    error: SynoError | None = None

    def require_data(self) -> T:
        """取出 `data`。success=true 时一定有值，没有就说明服务端不符合规范。"""
        if self.data is None:
            raise SynologyDriveError("接口返回 success=true，但没有 data 字段")
        return self.data


class LoginData(SynoModel):
    """登录返回的数据。"""

    did: str | None = Field(default=None, description="设备 ID。")
    sid: str | None = Field(default=None, description="后续 API 调用所需的会话 ID。")


class CapabilityInfo(SynoModel):
    """权限信息，字段在文件和团队文件夹上是同一套。"""

    can_download: bool | None = None
    can_sync: bool | None = None
    can_organize: bool | None = None
    can_rename: bool | None = None
    can_encrypt: bool | None = None
    can_share: bool | None = None
    can_delete: bool | None = None
    can_comment: bool | None = None
    can_write: bool | None = None
    can_preview: bool | None = None
    can_read: bool | None = None


class FileInfo(SynoModel):
    """一个文件或文件夹（规范里的 Files.FileInfo_v3_0，字段按需取用）。"""

    file_id: str | None = Field(default=None, description="文件的 ID。")
    name: str | None = Field(default=None, description="文件名。")
    type: str | None = Field(default=None, description="file 或 dir。")
    content_type: str | None = Field(
        default=None, description="dir、document、image、audio、video、file。"
    )
    parent_id: str | None = Field(default=None, description="父文件夹的 ID。")
    path: str | None = Field(default=None, description="当前分类导航中的路径。")
    display_path: str | None = Field(default=None, description="当前用户可访问的路径。")
    dsm_path: str | None = Field(default=None, description="DSM 共享文件夹上的完整路径。")
    size: int | None = Field(default=None, description="文件大小。")
    modified_time: int | None = Field(default=None, description="最近修改时间（epoch 秒）。")
    created_time: int | None = Field(default=None, description="创建时间（epoch 秒）。")
    permanent_link: str | None = Field(default=None, description="永久链接 UUID。")
    capabilities: CapabilityInfo | None = None

    @property
    def id(self) -> str | None:
        """`file_id` 的别名。"""
        return self.file_id

    @property
    def is_dir(self) -> bool:
        return self.type == "dir" or self.content_type == "dir"

    @property
    def modified_at(self) -> datetime | None:
        """最近修改时间，转成本地时区。"""
        if not self.modified_time:
            return None
        return datetime.fromtimestamp(self.modified_time, tz=timezone.utc).astimezone()


class FileListFilter(SynoModel):
    """`files/list` 请求体里的 `filter`（规范里的 Files.FileListFilter_0）。

    四个条件都是可选的，给了就筛，没给就不筛。
    """

    extensions: list[str] | None = Field(default=None, description="文件扩展名。")
    type: list[str] | None = Field(
        default=None,
        description=(
            "文件类型，取值 file / dir / image。指定 image 时会把预定义的一批图片扩展名"
            "并进 extensions 一起筛。"
        ),
    )
    label_id: str | None = Field(default=None, description="文件上标签的 ID。")
    starred: bool | None = Field(default=None, description="是否已加星标。")


class FileList(SynoModel):
    """一个文件夹的内容，`files/list` 和 `files/ancestors` 的 data 形状一样。"""

    total: int = 0
    items: list[FileInfo] = Field(default_factory=list)


class TeamFolderInfo(SynoModel):
    """一个团队文件夹（规范里的 Team Folders.TeamFolderInfo_0）。"""

    name: str | None = Field(default=None, description="文件夹名称。")
    file_id: str | None = Field(default=None, description="根目录的 ID。")
    team_id: str | None = Field(default=None, description="团队文件夹的 ID。")
    disable_download: bool | None = None
    enable_versioning: bool | None = None
    keep_versions: int | None = None
    capabilities: CapabilityInfo | None = None

    @property
    def id(self) -> str | None:
        """`file_id` 的别名。"""
        return self.file_id


class TeamFolderList(SynoModel):
    total: int = 0
    items: list[TeamFolderInfo] = Field(default_factory=list)


class UploadTask(SynoModel):
    """`upload-from-dsm` 的 data：上传是异步任务，只返回任务 ID。"""

    async_task_id: str | None = Field(default=None, description="异步任务的 ID。")


class ShareLink(SynoModel):
    """`sharing/create-link` 的 data。"""

    link_id: str | None = Field(default=None, description="URL 中的 ID 部分。")
    url: str | None = Field(default=None, description="指向此文件的分享链接。")


class AsyncTaskResult(SynoModel):
    """异步任务的执行结果（`SYNO.SynologyDrive.Tasks` 的 `result` 字段）。

    字段名来自前端 `_addExistTasks` 读的那几个，加处理程序二进制里的字符串表。
    """

    action: str | None = Field(default=None, description="做的动作，如 copy / download。")
    names: list[str] = Field(default_factory=list, description="涉及的文件名。")
    params: dict[str, Any] = Field(default_factory=dict, description="这次操作的原始参数。")
    total_size: int | None = Field(default=None, description="总字节数。")
    task_id: str | None = Field(default=None, description="任务 ID（和外层那个一致）。")
    errors: list[Any] = Field(
        default_factory=list,
        description="出错的条目。前端就是拿它非空来判断这次任务失败没有，元素形状没确认。",
    )

    @field_validator("errors", mode="before")
    @classmethod
    def normalize_null_errors(cls, value: Any) -> Any:
        """DSM uses JSON null for a completed task with no errors."""
        return [] if value is None else value


class AsyncTask(SynoModel):
    """一个异步任务（`Tasks.list` / `Tasks.get` 的条目）。"""

    task_id: str | None = Field(default=None, description="任务 ID。")
    status: str | None = Field(
        default=None, description=f"状态，{TASK_STATUS_IN_PROGRESS} 或 {TASK_STATUS_FINISHED}。"
    )
    progress: float | None = Field(default=None, description="进度百分比，0–100。")
    result: AsyncTaskResult | None = None

    @property
    def finished(self) -> bool:
        """跑完了没有。判断依据就是前端 `"finished"===t.status` 那一行。"""
        return self.status == TASK_STATUS_FINISHED

    @property
    def failed(self) -> bool:
        """完成了但结果里有错误条目 —— 也就是"跑完了，但没跑成"。

        只看 `result.errors`，因为没结束的任务谈不上失败；DSM 也没有一个单独的失败状态。
        """
        return self.finished and bool(self.result and self.result.errors)


class TaskList(SynoModel):
    """`Tasks.list` 的 data。"""

    total: int = 0
    items: list[AsyncTask] = Field(default_factory=list)


class AdvanceShareLink(SynoModel):
    """一个高级分享链接（`AdvanceSharing.get` / `create` / `update` 的 data）。

    字段名来自前端 `_onSaveLink` 发出去的那套参数，以及处理程序的字符串表。
    """

    url: str | None = Field(default=None, description="分享链接的完整 URL。")
    sharing_link: str | None = Field(default=None, description="链接 ID，后续 update/delete 要用。")
    permanent_id: int | str | None = Field(
        default=None, description="永久链接 ID。真机回来的是整数，这里两种都收。"
    )
    role: str | None = Field(default=None, description="访问者角色，取值见 ADV_SHARE_ROLES。")
    due_date: int | None = Field(default=None, description="有效期（epoch 秒），0 表示不过期。")
    protect_password: Any = Field(
        default=None,
        description="访问密码。官方前端用它传明文；服务端回来的是密码本身还是布尔标记没确认，"
        "所以这里不写死类型，用 has_password 判断有没有设。",
    )
    capabilities: CapabilityInfo | None = None
    uid: int | None = Field(default=None, description="链接所有者的 uid。")

    @property
    def has_password(self) -> bool:
        """这个链接要不要密码才能打开。"""
        return bool(self.protect_password)

    @property
    def expires_at(self) -> datetime | None:
        """有效期，转成本地时区；没过期时间就是 None。"""
        if not self.due_date:
            return None
        return datetime.fromtimestamp(self.due_date, tz=timezone.utc).astimezone()


class ShareUnlockResult(SynoModel):
    """`AdvanceSharing.Public.auth` 的 data。

    真机回来的是 `{"sharing_token": "..."}`：解锁成功后拿到一个凭据，用它去访问分享内容。
    密码不对时这个接口回 `error.code=1037`。
    """

    sharing_token: str | None = Field(default=None, description="解锁凭据。")
