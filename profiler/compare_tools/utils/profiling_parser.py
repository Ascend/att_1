from abc import abstractmethod
from math import ceil

from utils.compare_event import KernelEvent
from utils.constant import Constant
from utils.file_reader import FileReader
from utils.trace_event_data import TraceEventData


class ProfilingParser:
    def __init__(self, args: any, path_dict: dict):
        self._args = args
        self._profiling_path = path_dict.get(Constant.PROFILING_PATH)
        self._torch_op_data = None
        self._kernel_dict = None
        self._memory_list = None
        self._communication_data = None
        self._communication_task_data = None
    
    @property
    def file_path(self) -> str:
        return self._profiling_path

    @property
    def json_path(self) -> str:
        return self._json_path

    @property
    def torch_op_data(self) -> list:
        if self._torch_op_data is None:
            self.get_torch_op_data()
        return self._torch_op_data

    @property
    def kernel_dict(self) -> dict:
        if self._kernel_dict is None:
            self.get_kernel_dict()
        return self._kernel_dict

    @property
    def memory_list(self) -> list:
        if self._memory_list is None:
            self.get_memory_list()
        return self._memory_list

    @property
    def communication_data(self) -> dict:
        if self._communication_data is None:
            self.get_communication_data()
        return self._communication_data

    @property
    def communication_task_data(self) -> dict:
        if self._communication_task_data is None:
            self.get_communication_data()
        return self._communication_task_data

    @abstractmethod
    def get_torch_op_data(self):
        raise NotImplementedError

    @abstractmethod
    def get_kernel_dict(self):
        raise NotImplementedError

    @abstractmethod
    def get_memory_list(self):
        raise NotImplementedError


class GPUProfilingParser(ProfilingParser):
    def __init__(self, args: any, path_dict: dict):
        super().__init__(args, path_dict)
        self._json_path = path_dict.get(Constant.PROFILING_PATH)

    def get_torch_op_data(self):
        torch_op_list = []
        json_data = FileReader.read_trace_file(self._json_path)
        total_events = json_data.get("traceEvents", [])
        for event in total_events:
            if event.get("cat", "").lower() == "cpu_op" or event.get("cat", "").lower() == "user_annotation":
                torch_op_list.append(event)
        self._torch_op_data = torch_op_list

    def get_kernel_dict(self):
        flow_kernel_dict = {}
        json_data = FileReader.read_trace_file(self._json_path)
        total_events = json_data.get("traceEvents", [])
        flow_cat = (self._args.gpu_flow_cat,) if self._args.gpu_flow_cat else ("async_gpu", "async_cpu_to_gpu", "ac2g")

        flow_start_dict, flow_end_dict, kernel_dict = {}, {}, {}
        for event in total_events:
            if event.get("cat", "") in flow_cat and event.get("ph") == "s":
                flow_start_dict[event.get("id")] = event
            elif event.get("cat", "") in flow_cat and event.get("ph") == "f":
                flow_end_dict[event.get("id")] = event
            elif event.get("cat", "").lower() == "kernel" and event.get("name", "").split("_")[0].lower() != "ncclkernel":
                kernel_dict["{}-{}-{}".format(event.get("pid"), event.get("tid"), event.get("ts"))] = event

        for flow_id, start_flow in flow_start_dict.items():
            end_flow = flow_end_dict.get(flow_id)
            if end_flow is None:
                continue
            kernel_event = kernel_dict.get(
                "{}-{}-{}".format(end_flow.get("pid"), end_flow.get("tid"), end_flow.get("ts")))
            if kernel_event is None:
                continue
            flow_kernel_dict.setdefault(start_flow.get("ts"), []).append(KernelEvent(kernel_event, Constant.GPU))
        self._kernel_dict = flow_kernel_dict

    def get_memory_list(self):
        self._memory_list = []
        memory_events = []
        json_data = FileReader.read_trace_file(self._json_path)
        total_events = json_data.get("traceEvents", [])
        for event in total_events:
            if event.get("name", "").lower() == "[memory]":
                memory_events.append(event)
        memory_events.sort(key=lambda x: x.get("ts", 0))
        addr_dict = {}
        for memory_event in memory_events:
            args = memory_event.get("args", {})
            if args.get("Device Type", -1) != 1:
                continue
            allocate_bytes = args.get("Bytes", 0) / Constant.BYTE_TO_KB
            record = addr_dict.get(args.get("Addr"))
            if allocate_bytes > 0:
                if record:
                    self._memory_list.append(record)
                addr_dict[args.get("Addr")] = {Constant.SIZE: allocate_bytes,
                                               Constant.TS: memory_event.get("ts", 0),
                                               Constant.ALLOCATION_TIME: memory_event.get("ts", 0)}
            if allocate_bytes < 0 and record:
                if abs(allocate_bytes) == record.get(Constant.SIZE):
                    record[Constant.RELEASE_TIME] = memory_event.get("ts", 0)
                    self._memory_list.append(record)
                del addr_dict[args.get("Addr")]

    def get_communication_data(self):
        self._communication_data, self._communication_task_data = [], {}
        json_data = FileReader.read_trace_file(self._json_path)
        total_events = json_data.get("traceEvents", [])
        for data in total_events:
            if data.get("cat", "").lower() == "kernel" and data.get("name", "").split("_")[0].lower() == "ncclkernel":
                self._communication_data.append(data)


class NPUProfilingParser(ProfilingParser):
    def __init__(self, args: any, path_dict: str):
        super().__init__(args, path_dict)
        self._json_path = path_dict.get(Constant.TRACE_PATH)
        self._memory_data_path = path_dict.get(Constant.MEMORY_DATA_PATH)

    def get_torch_op_data(self):
        torch_op_list = []
        json_data = FileReader.read_trace_file(self._json_path)
        for event in json_data:
            if event.get("cat", "").lower() == "cpu_op":
                torch_op_list.append(event)
        self._torch_op_data = torch_op_list

    def get_kernel_dict(self):
        flow_kernel_dict = {}
        json_data = FileReader.read_trace_file(self._json_path)
        flow_cat = "async_npu"

        flow_start_dict, flow_end_dict, kernel_dict = {}, {}, {}
        for event in json_data:
            if event.get("cat", "") == flow_cat and event.get("ph") == "s":
                flow_start_dict[event.get("id")] = event
            elif event.get("cat", "") == flow_cat and event.get("ph") == "f":
                flow_end_dict[event.get("id")] = event
            elif event.get("ph") == "X" and event.get("cat", "") != 'cpu_op':
                kernel_dict["{}-{}-{}".format(event.get("pid"), event.get("tid"), event.get("ts"))] = event

        for flow_id, start_flow in flow_start_dict.items():
            end_flow = flow_end_dict.get(flow_id)
            if end_flow is None:
                continue
            kernel_event = kernel_dict.get(
                "{}-{}-{}".format(end_flow.get("pid"), end_flow.get("tid"), end_flow.get("ts")))
            if kernel_event is None:
                continue
            flow_kernel_dict.setdefault(start_flow.get("ts"), []).append(KernelEvent(kernel_event, Constant.NPU))
        self._kernel_dict = flow_kernel_dict

    def get_memory_list(self):
        self._memory_list = []
        enqueue_dict, dequeue_data = {}, []
        json_data = FileReader.read_trace_file(self._json_path)
        for data in json_data:
            if data.get("cat", "").lower() == "enqueue":
                enqueue_dict[data.get("args", {}).get("correlation_id", "")] = data
            elif data.get("cat", "").lower() == "dequeue":
                dequeue_data.append(data)

        if not self._memory_data_path:
            return
        memory_data = FileReader.read_csv_file(self._memory_data_path)
        for data in memory_data:
            if not data.get(Constant.ALLOCATION_TIME, 0):
                continue
            if "cann::" in data.get("Name", ""):
                ts_time = float(data.get(Constant.ALLOCATION_TIME, 0))
                match_dequeue_data = self._match_cann_memory_data(dequeue_data, ts_time)
                if match_dequeue_data is not None:
                    correlation_id = match_dequeue_data.get("args", {}).get("correlation_id", "")
                    ts = enqueue_dict.get(correlation_id, {}).get("ts", 0)
                    self._memory_list.append({Constant.SIZE: float(data.get(Constant.SIZE, 0)), Constant.TS: ts,
                                              Constant.NAME: data.get(Constant.NAME, ""),
                                              Constant.ALLOCATION_TIME: float(data.get(Constant.ALLOCATION_TIME, 0)),
                                              Constant.RELEASE_TIME: data.get(Constant.RELEASE_TIME, 0)})
            self._memory_list.append({Constant.SIZE: float(data.get(Constant.SIZE, 0)),
                                      Constant.TS: float(data.get(Constant.ALLOCATION_TIME, 0)),
                                      Constant.ALLOCATION_TIME: float(data.get(Constant.ALLOCATION_TIME, 0)),
                                      Constant.RELEASE_TIME: data.get(Constant.RELEASE_TIME, 0)})

    @classmethod
    def _match_cann_memory_data(cls, dequeue_data: list, ts_time: float):
        if not dequeue_data:
            return None
        right = len(dequeue_data) - 1
        left = 0
        while right > left:
            mid = left + ceil((right - left) / 2)
            if ts_time >= dequeue_data[mid].get("ts", 0):
                left = mid
            else:
                right = mid - 1
        end_time = dequeue_data[left].get("ts", 0) + dequeue_data[left].get("dur", 0)
        return dequeue_data[left] if end_time > ts_time else None

    def get_communication_data(self):
        def get_pid(json_data):
            pid = None
            for data in json_data:
                trace_event = TraceEventData(data)
                if not trace_event.is_process_meta():
                    continue
                if trace_event.is_hccl_process():
                    pid = trace_event.pid
                    break
            return pid
        
        def get_tid_list(pid, tid_list, json_data):
            for data in json_data:
                trace_event = TraceEventData(data)
                if not trace_event.is_thread_meta():
                    continue
                if trace_event.pid != pid:
                    continue
                if trace_event.is_communication_op_thread():
                    tid_list.append(trace_event.tid)
        
        def get_comm_data(pid, tid_list, json_data):
            for data in json_data:
                trace_event = TraceEventData(data)
                if not trace_event.is_x_mode():
                    continue
                if trace_event.pid != pid:
                    continue
                if trace_event.tid in tid_list:
                    self._communication_data.append(data)

        def get_comm_task_data(pid, tid_list, json_data):
            for data in json_data:
                trace_event = TraceEventData(data)
                if not trace_event.is_x_mode():
                    continue
                if trace_event.pid != pid:
                    continue
                if trace_event.tid in tid_list:
                    continue
                ts = trace_event.start_time
                for communication_op in self._communication_data:
                    comm_op_event = TraceEventData(communication_op)
                    if ts < comm_op_event.start_time or ts > comm_op_event.end_time:
                        continue
                    name_list = communication_op.get("name", "").split("_")
                    if len(name_list) >= 2:
                        self._communication_task_data.setdefault(name_list[1].lower(), []).append(data)
                    break

        self._communication_data, self._communication_task_data = [], {}
        json_data = FileReader.read_trace_file(self._json_path)

        pid = get_pid(json_data)
        if pid is None:
            return

        tid_list = []
        get_tid_list(pid, tid_list, json_data)
        if not tid_list:
            return

        get_comm_data(pid, tid_list, json_data)
        if not self._communication_data:
            return

        get_comm_task_data(pid, tid_list, json_data)
