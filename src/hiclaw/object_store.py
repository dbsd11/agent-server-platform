# 对象存储抽象 —— 对应 hiclaw 的 MinIO（S3/OSS 兼容）
#
# Worker 无状态，配置与产物持久化在对象存储：agents/<name>/…、
# shared/tasks/<id>/…、manager/…。LocalFileStore 用本地文件系统实现，
# 接口与 MinIO 对齐，日后可替换为 S3/MinIO 实现而不改上层代码。
from __future__ import annotations

import os
import json
import shutil
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class ObjectStore(ABC):
    """对象存储接口（put/get/list/exists/delete）。路径用正斜杠分隔。"""

    @abstractmethod
    def put(self, path: str, data: str) -> None: ...

    @abstractmethod
    def get(self, path: str) -> Optional[str]: ...

    @abstractmethod
    def get_bytes(self, path: str) -> Optional[bytes]: ...

    @abstractmethod
    def list(self, prefix: str) -> List[str]: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def delete(self, prefix: str) -> None: ...

    # 便捷方法：JSON 读写
    def put_json(self, path: str, obj: dict) -> None:
        self.put(path, json.dumps(obj, ensure_ascii=False, indent=2))

    def get_json(self, path: str) -> Optional[dict]:
        raw = self.get(path)
        return json.loads(raw) if raw is not None else None


class LocalFileStore(ObjectStore):
    """本地文件系统实现。root 即 bucket，path 即对象 key。"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _abs(self, path: str) -> str:
        # 防路径穿越：拼接后必须仍在 root 下
        full = os.path.normpath(os.path.join(self.root, path))
        if not full.startswith(self.root + os.sep) and full != self.root:
            raise ValueError(f"path escapes store root: {path}")
        return full

    def put(self, path: str, data: str) -> None:
        full = self._abs(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(data)

    def get(self, path: str) -> Optional[str]:
        full = self._abs(path)
        if not os.path.isfile(full):
            return None
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def get_bytes(self, path: str) -> Optional[bytes]:
        full = self._abs(path)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as f:
            return f.read()

    def list(self, prefix: str) -> List[str]:
        full = self._abs(prefix)
        if not os.path.exists(full):
            return []
        results: List[str] = []
        if os.path.isfile(full):
            results.append(prefix)
        else:
            for dirpath, _dirs, files in os.walk(full):
                for name in files:
                    abs_p = os.path.join(dirpath, name)
                    rel = os.path.relpath(abs_p, self.root).replace(os.sep, "/")
                    results.append(rel)
        return sorted(results)

    def exists(self, path: str) -> bool:
        return os.path.exists(self._abs(path))

    def delete(self, prefix: str) -> None:
        full = self._abs(prefix)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        elif os.path.isfile(full):
            os.remove(full)
