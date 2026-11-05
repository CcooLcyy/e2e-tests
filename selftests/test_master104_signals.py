"""主站遥信只读判定契约；不操作真实主站，不运行协议。"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from testbench.master104_signals import Master104PointObserver


STAMP = datetime.now().astimezone().replace(microsecond=0) - timedelta(seconds=5)
GOOD = "单点 有效 当前值 未被取代 未被封锁"


def snapshot(*, value="分", stamp=None, quality=GOOD, connected="正常", paused="正常", partial=False):
    """构造明确的遥信表，不用窗口采集时间代替点更新时间。"""
    return {"identity": {"path": "C:/ProIEC104Client.exe", "pid": 1, "hwnd": 2, "created": 3},
            "observed_at": datetime.now().astimezone().isoformat(), "partial": partial,
            "controls": [],
            "tables": [{"visible": True, "partial": False, "columns": [],
                        "rows": [["连接状态", connected], ["暂停状态", paused]]},
                       {"visible": True, "partial": False, "panel_title": "遥信信息",
                        "columns": ["序号", "信息地址", "信息描述", "值", "品质描述", "更新时间"],
                        "rows": [["1", "0001H", "AGC增闭锁", value, quality,
                                  (stamp or STAMP).strftime("%Y-%m-%d %H:%M:%S")],
                                 ["2", "0002H", "AGC减闭锁", "分", GOOD,
                                  STAMP.strftime("%Y-%m-%d %H:%M:%S")]]}]}


class Session:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)

    def snapshot(self, *, table_scope=None):
        assert table_scope == "signals"
        return deepcopy(next(self.snapshots))

    @staticmethod
    def _check_stop():
        pass


def observer(*snapshots):
    return Master104PointObserver(Session(snapshots), {"increase": 1, "decrease": 2})


def test_accepts_fresh_target_without_requiring_unchanged_companion_timestamp():
    """变化点需有新时间，未变化伴随点仍须为有效值但不要求变位。"""
    app = observer(snapshot(), snapshot(value="合", stamp=datetime.now().astimezone()))
    baseline = app.checkpoint()
    result = app.wait_state({"increase": True, "decrease": False}, baseline, 1, fresh_keys=("increase",))
    assert result["points"]["increase"]["value"] is True
    assert result["points"]["decrease"]["value"] is False


@pytest.mark.parametrize("changes", [{"connected": "中断"}, {"paused": "暂停"}, {"partial": True},
                                     {"quality": "单点 无效 当前值 未被取代 未被封锁"},
                                     {"quality": "单点 有效 非当前值 未被取代 未被封锁"},
                                     {"quality": "单点 有效 当前值 被取代 未被封锁"},
                                     {"quality": "单点 有效 当前值 未被取代 被封锁"},
                                     {"quality": "单点 有效 当前值"}, {"value": "未知"}])
def test_checkpoint_rejects_unreliable_observation(changes):
    """断线、不完整表、未知值及非GOOD品质均不能作为可靠业务基线。"""
    with pytest.raises((ValueError, RuntimeError)):
        observer(snapshot(**changes)).checkpoint()


def test_duplicate_ioa_is_rejected():
    """重复IOA不能任取第一行作为目标。"""
    data = snapshot()
    data["tables"][1]["rows"].append(list(data["tables"][1]["rows"][0]))
    with pytest.raises(RuntimeError, match="唯一"):
        observer(data).checkpoint()


def test_instance_change_is_rejected():
    """不能使用重开的主站实例替代原用例现场。"""
    changed = snapshot(value="合", stamp=datetime.now().astimezone())
    changed["identity"]["pid"] = 99
    app = observer(snapshot(), changed)
    baseline = app.checkpoint()
    with pytest.raises(RuntimeError, match="身份"):
        app.wait_state({"increase": True}, baseline, 1, fresh_keys=("increase",))


def test_old_point_with_new_snapshot_time_is_not_fresh(monkeypatch):
    """重新采集旧点表不能证明收到本轮遥信。"""
    app = observer(snapshot(), snapshot(value="合"))
    baseline = app.checkpoint()
    ticks = iter([0, 0, 0, 0, 2])
    monkeypatch.setattr("testbench.master104_signals.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("testbench.master104_signals.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError):
        app.wait_state({"increase": True}, baseline, 1, fresh_keys=("increase",))


def test_timestamp_newer_than_point_but_before_checkpoint_is_not_fresh(monkeypatch):
    """晚于旧点但早于当前操作边界的历史变位仍不可判通过。"""
    app = observer(snapshot(), snapshot(value="合", stamp=STAMP + timedelta(seconds=1)))
    baseline = app.checkpoint()
    ticks = iter([0, 0, 0, 0, 2])
    monkeypatch.setattr("testbench.master104_signals.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("testbench.master104_signals.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError):
        app.wait_state({"increase": True}, baseline, 1, fresh_keys=("increase",))


def test_native_tables_preserve_cells_and_report_unreadable_tables():
    """原生采集逐格读取可见ListView；单格错误须保留不完整证据。"""
    from testbench.master104_helper import collect_table

    class Item:
        def text(self):
            return "单元格"

    class Table:
        handle = 1
        def class_name(self): return "SysListView32"
        def control_id(self): return 2
        def is_visible(self): return True
        def columns(self): return [{"text": "列1"}, {"text": "列2"}]
        def item_count(self): return 1
        def get_item(self, row, column): return Item()

    data = collect_table(Table())
    assert data["columns"] == ["列1", "列2"]
    assert data["rows"] == [["单元格", "单元格"]]
    assert data["partial"] is False
    class Broken(Table):
        def get_item(self, row, column): raise RuntimeError("读取失败")
    broken = collect_table(Broken())
    assert broken["partial"] is True and broken["errors"]


def test_signals_scope_never_reads_unrelated_growing_message_rows():
    """遥信范围只读相关表，正常接收的报文列表增长不能造成业务采集失败。"""
    from testbench.master104_helper import collect_table

    class MessageLog:
        handle = 40
        def class_name(self): return "SysListView32"
        def control_id(self): return 99
        def is_visible(self): return True
        def columns(self): return [{"text": "时间"}, {"text": "接收报文"}]
        def item_count(self): pytest.fail("不能逐行扫描无关报文列表")
        def get_item(self, *args): pytest.fail("不能读取无关报文单元格")

    assert collect_table(MessageLog(), table_scope="signals") is None


def test_signals_scope_reads_known_status_table_even_with_blank_headers():
    """已确认control_id=4的两列状态表即使列名为空也必须读取。"""
    from testbench.master104_helper import collect_table

    class StatusTable:
        handle = 40
        def class_name(self): return "SysListView32"
        def control_id(self): return 4
        def is_visible(self): return True
        def columns(self): return [{"text": ""}, {"text": ""}]
        def item_count(self): return 2
        def get_item(self, row, column):
            value = [["连接状态", "正常"], ["暂停状态", "正常"]][row][column]
            return type("Item", (), {"text": lambda self: value})()

    result = collect_table(StatusTable(), table_scope="signals")
    assert not result["partial"]
    assert result["rows"] == [["连接状态", "正常"], ["暂停状态", "正常"]]


def test_missing_pause_state_is_not_treated_as_unpaused():
    """连接正常不能代替明确的未暂停证据。"""
    data = snapshot()
    data["tables"][0]["rows"].pop()
    with pytest.raises(RuntimeError, match="暂停"):
        observer(data).checkpoint()


def test_future_point_timestamp_is_rejected():
    """主站点更新时间超过本机时钟容差时不能判为本轮有效观察。"""
    with pytest.raises(RuntimeError, match="本机"):
        observer(snapshot(stamp=datetime.now().astimezone() + timedelta(minutes=1))).checkpoint()


def test_unknown_table_scope_is_rejected_before_native_access():
    """未知采集范围不得触发原生窗口访问。"""
    from testbench.master104_helper import collect
    with pytest.raises(ValueError, match="范围"):
        collect({"table_scope": "arbitrary"})


def test_full_signal_snapshot_does_not_read_growing_log_texts(tmp_path, monkeypatch):
    """完整辅助进程采集也不能通过texts()间接扫描持续增长的报文行。"""
    from testbench import master104_helper as helper

    identity = {"path": str(tmp_path / "ProIEC104Client.exe"), "pid": 1, "hwnd": 2, "created": 3}
    class Table:
        def __init__(self, control_id, columns, rows):
            self.handle, self._columns, self._rows = control_id + 10, columns, rows
            self._id = control_id
        def class_name(self): return "SysListView32"
        def control_id(self): return self._id
        def is_visible(self): return True
        def rectangle(self): return SimpleNamespace(left=0, top=0, right=100, bottom=100)
        def window_text(self): return ""
        def parent(self):
            return SimpleNamespace(window_text=lambda: "遥信信息" if self._id == 8 else "报文信息",
                                   parent=lambda: None)
        def texts(self): pytest.fail("signals范围不得调用ListView.texts扫描日志")
        def columns(self): return [{"text": value} for value in self._columns]
        def item_count(self):
            if self._id == 99: pytest.fail("不得读取无关报文行数")
            return len(self._rows)
        def get_item(self, row, col):
            value = self._rows[row][col]
            return SimpleNamespace(text=lambda: value)

    log = Table(99, ["时间", "报文"], [])
    status = Table(4, ["", ""], [["连接状态", "正常"], ["暂停状态", "正常"]])
    signal_data = snapshot()["tables"][1]
    signals = Table(8, signal_data["columns"], signal_data["rows"])
    root = SimpleNamespace(handle=2, window_text=lambda: "ProIEC104Client",
                           descendants=lambda: [log, status, signals])
    desktop = SimpleNamespace(window=lambda handle: SimpleNamespace(wrapper_object=lambda: root),
                              windows=lambda **kwargs: [root])
    monkeypatch.setattr(helper, "_native", lambda: (lambda **kwargs: desktop,
                        SimpleNamespace(), SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (0, 1))))
    monkeypatch.setattr(helper, "_verify", lambda expected, *args: expected)
    result = helper.collect({"action": "snapshot", "path": identity["path"],
                             "identity": identity, "table_scope": "signals"})
    assert not result["partial"]
    assert len(result["tables"]) == 2
    assert all(table["control_id"] != 99 for table in result["tables"])


def test_signal_reader_distinguishes_visible_setpoint_table_with_identical_headers():
    """遥调、遥信同时可见且列名相同时仍只读取遥信所属面板。"""
    data = snapshot()
    setpoints = deepcopy(data["tables"][1])
    setpoints["panel_title"] = "遥调信息"
    data["tables"].append(setpoints)
    assert observer(data).checkpoint()["points"]["increase"]["value"] is False
    del data["tables"][1]["panel_title"]
    with pytest.raises(RuntimeError, match="唯一"):
        observer(data).checkpoint()


def test_signal_scope_filters_setpoint_panel_before_reading_any_rows():
    """相同六列表头不足以确认遥信，遥调面板不触达逐行读取。"""
    from testbench.master104_helper import collect_table

    class SetpointTable:
        handle = 30
        def class_name(self): return "SysListView32"
        def control_id(self): return 30
        def is_visible(self): return True
        def columns(self): return [{"text": title} for title in snapshot()["tables"][1]["columns"]]
        def parent(self): return SimpleNamespace(window_text=lambda: "遥调信息", parent=lambda: None)
        def item_count(self): pytest.fail("不能读取遥调面板行数")
        def get_item(self, *args): pytest.fail("不能读取遥调面板单元格")

    assert collect_table(SetpointTable(), table_scope="signals") is None


def test_ordinary_snapshot_does_not_add_full_table_scans(tmp_path, monkeypatch):
    """普通主站检查保持原文本采集，不因新增遥信业务逐格扫描大型报文表。"""
    from testbench import master104_helper as helper
    identity = {"path": str(tmp_path / "ProIEC104Client.exe"), "pid": 1, "hwnd": 2, "created": 3}
    class LogTable:
        handle = 10
        def class_name(self): return "SysListView32"
        def control_id(self): return 99
        def is_visible(self): return True
        def window_text(self): return "原有报文窗口"
        def texts(self): return ["原有文本采集"]
        def rectangle(self): return SimpleNamespace(left=0, top=0, right=1, bottom=1)
        def columns(self): pytest.fail("普通快照不应新增逐格扫描")
    root = SimpleNamespace(handle=2, window_text=lambda: "ProIEC104Client", descendants=lambda: [LogTable()])
    desktop = SimpleNamespace(window=lambda handle: SimpleNamespace(wrapper_object=lambda: root),
                              windows=lambda **kwargs: [root])
    monkeypatch.setattr(helper, "_native", lambda: (lambda **kwargs: desktop,
                        SimpleNamespace(), SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (0, 1))))
    monkeypatch.setattr(helper, "_verify", lambda expected, *args: expected)
    result = helper.collect({"action": "snapshot", "path": identity["path"], "identity": identity})
    assert result["tables"] == []
    assert result["controls"][0]["texts"] == ["原有文本采集"]
