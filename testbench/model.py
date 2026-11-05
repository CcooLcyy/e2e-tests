"""环境与用例模型。"""
from dataclasses import asdict, dataclass, field
from copy import deepcopy
import json
import logging
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
    timeout: float = 60
    close_app: bool = True
    annotation_enabled: bool = True
    slavesim_url: str = "http://192.168.1.219:16000"
    master104_enabled: bool = False
    master104_path: str = ""
    master104_backend: str = "win32"
    master104_timeout: float = 10
    modbus_manager_address: str = "192.168.1.219:16000"
    modbus_baud_rate: int = 9600
    modbus_poll_interval_ms: int = 1000
    modbus_acquisition_timeout: float = 30
    modbus_basic: dict = field(default_factory=dict)
    modbus_huawei: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{key: deepcopy(value) for key, value in data.items() if key in cls.__dataclass_fields__})

    def validate(self):
        self.validate_master104()
        if not isinstance(self.annotation_enabled, bool):
            raise ValueError("截图标注开关必须为布尔值")
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

    def validate_master104(self, *, required=False):
        """主站检查独立于 DSP 和驱动校验，禁用时保留未完成的可选配置。"""
        if not isinstance(self.master104_enabled, bool):
            raise ValueError("104 主站开关必须为布尔值")
        if not self.master104_enabled:
            if required:
                raise ValueError("请先启用 104 主站")
            return
        if (not isinstance(self.master104_path, str) or not self.master104_path.strip()
                or any(ord(char) < 32 for char in self.master104_path)):
            raise ValueError("104 主站必须选择已有的 ProIEC104Client.exe")
        path = Path(self.master104_path.strip())
        if path.name.lower() != "proiec104client.exe" or not path.is_file():
            raise ValueError("104 主站必须选择已有的 ProIEC104Client.exe")
        if self.master104_backend not in ("win32", "uia"):
            raise ValueError("104 主站后端必须是 win32 或 uia")
        try:
            if isinstance(self.master104_timeout, bool):
                raise ValueError("布尔值不是等待时间")
            timeout = float(self.master104_timeout)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError("104 主站等待时间必须是有限正数") from error
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("104 主站等待时间必须是有限正数")
        self.master104_path = str(path.resolve())
        self.master104_timeout = timeout

    def validate_for_cases(self, case_ids):
        """开始任务前检查所选用例需要的参数，允许保存未完成的RTU环境草稿。"""
        from .modbus_environment import validate_selected_modbus_cases
        validate_selected_modbus_cases(self.to_dict(), case_ids)


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
    logging.getLogger(__name__).info("已保存测试环境，截图自动标注：%s", environment.annotation_enabled)
    logging.getLogger(__name__).info("已保存 104 主站环境，启用：%s", environment.master104_enabled)
    logging.getLogger(__name__).info("已保存统一Modbus RTU环境，完整性在开始所选用例前检查")


def load_environment(path):
    return Environment.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
