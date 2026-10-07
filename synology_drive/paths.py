def id_path(file_id: str) -> str:
    """`id:file_id` 形式的路径。"""
    return file_id if str(file_id).startswith("id:") else f"id:{file_id}"


def team_folder_path(name: str, *parts: str) -> str:
    """`/team-folders/{团队文件夹名}/{相对路径}` 形式的路径。"""
    segments = [name, *parts]
    cleaned = [str(s).strip("/") for s in segments if str(s).strip("/")]
    return "/team-folders/" + "/".join(cleaned)


def resolve_path(path: str | None, file_id: str | None, parent_id: str | None) -> str:
    """从 path / file_id / parent_id 里挑出最终要用的 path。"""
    if path and file_id:
        raise ValueError("path 和 file_id 只能给一个")
    if path:
        return path
    if file_id:
        return id_path(file_id)
    if parent_id:
        return id_path(parent_id)
    raise ValueError("必须提供 path 或 file_id / parent_id")
