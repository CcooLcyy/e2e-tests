"""环境与用例模型。"""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from urllib.parse import urlsplit


def validate_slavesim_url(value, *, required=False):
    """统一校验管理页地址，子路径用于反向代理，禁止混入凭据与参数。"""
    if not isinstance(value, str):
        raise ValueError("slaveSim 管理页地址必须是字符串")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("slaveSim 管理页地址不能包含控制字符")
    value = value.strip()
    if not value:
        if required:
            raise ValueError("请先填写 slaveSim 管理页地址")
        return ""
    try:
        parts = urlsplit(value)
        port = parts.port
        valid = (parts.scheme in ("http", "https") and parts.hostname
                 and not parts.username and not parts.password and "@" not in parts.netloc
                 and (port is None or port > 0) and not parts.netloc.endswith(":")
                 and not parts.query and not parts.fragment
                 and not any(char.isspace() for char in value) and "\\" not in value)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("slaveSim 管理页地址须为完整的 HTTP/HTTPS 地址，不能包含账号密码、空白、查询参数或片段，端口须有效")
    return value


@dataclass
class Environment:
    name: str = "默认测试环境"
    app_path: str = ""
    driver_path: str = ""
    mode: str = "fresh"
    workspace_path: str = ""
    baseline_path: str = ""
    timeout: float = 20
    close_app: bool = True
    slavesim_url: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})

    def validate(self):
        self.slavesim_url = validate_slavesim_url(self.slavesim_url)
        if not self.name.strip():
            raise ValueError("环境名称不能为空")
        for label, value in (("上位机", self.app_path), ("驱动", self.driver_path)):
            if not value or not Path(value).is_file() or Path(value).suffix.lower() != ".exe":
                raise ValueError(f"{label}必须选择已有的 exe 文件")
        if self.mode not in ("fresh", "workspace"):
            raise ValueError("运行模式必须是全新副本或固定工作区")
        try:
            timeout = float(self.timeout)
        except (ValueError, TypeError) as error:
            raise ValueError("超时必须是正数") from error
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("超时必须是有限正数")
        self.timeout = timeout
        if self.mode == "workspace":
            if not self.workspace_path:
                raise ValueError("固定工作区路径不能为空")
            workspace = Path(self.workspace_path).resolve()
            source = Path(self.app_path).resolve().parent
            if workspace == source or source.is_relative_to(workspace):
                raise ValueError("固定工作区不能是原安装目录或其父目录")
            if workspace.exists() and not workspace.is_dir():
                raise ValueError("固定工作区必须是目录")
        if self.baseline_path and not Path(self.baseline_path).is_dir():
            raise ValueError("配置基线必须是已有目录")


@dataclass(frozen=True)
class CaseInfo:
    id: str
    name: str
    description: str = ""
    group: str = "未分组"


def save_environment(path, environment):
    environment.validate()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".pending")
    temporary.write_text(json.dumps(environment.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def load_environment(path):
    return Environment.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
