#! /usr/bin/python3
# Copyright 2023 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import re

from functools import partial
from argparse import ArgumentParser
from decimal import Decimal


FILTER_DIRS = [".profiler", "HCCL_PROF", "timeline", "query", 'sqlite', 'log']
RANK_ID_POS = 1000


# 获取时间差异文件中的node和时间差的对应关系，保存到字典中
def get_node_time_diff(time_diff_file_path):
    node_diff = {}
    if not time_diff_file_path:
        return None
    with open(time_diff_file_path, 'r+', encoding='utf-8') as f:
        all_time_diff = json.load(f)
        node_idx = 0
        for ip, timediff in all_time_diff.items():
            node_diff[node_idx] = timediff
            node_idx += 1
        return node_diff


def get_path_dir(path: str) -> list:
    """
    check result path exist JOB dir
    path : result path
    """
    path_dir_filter = filter(partial(_path_dir_filter_func, root_dir=path), os.listdir(path))
    sub_dirs = list(path_dir_filter)
    if not sub_dirs:
        message = f"The path \"{path}\" does not have PROF dir. Please check the path."
        print(message)
    return sub_dirs


def _path_dir_filter_func(sub_path, root_dir):
    return sub_path not in FILTER_DIRS and os.path.isdir(os.path.realpath(os.path.join(root_dir, sub_path)))


def natural_sort(files):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(files, key=alphanum_key)


def get_timeline_info(args, prof_dirs):
    timeline_info = {}

    for prof in prof_dirs:
        pro_path = os.path.join(args.input, prof)

        # 从info.json读取rank_id
        rank_id = get_rank_id_from_info_json(pro_path)
        ts_difference_us = 0
        if rank_id is None:
            print(f"WARN, There is not rank id info in {pro_path}")
            continue

        timeline_path = get_timeline_path(pro_path, args.type)

        if os.path.exists(timeline_path):
            timeline_info[rank_id] = (timeline_path, ts_difference_us)
        else:
            print(f"WARN, The file \"{timeline_path}\" does not exist.")
    return timeline_info


def get_timeline_path(pro_path, type):
    for root, dirs, files in os.walk(pro_path):
        for dir_ in dirs:
            if 'ASCEND_PROFILER_OUTPUT' == dir_ and type == 'pytorch':
                timeline_path = os.path.realpath(os.path.join(root, dir_, 'trace_view.json'))
                return timeline_path

        for file_ in sorted(files, reverse=True):
            if 'msprof' in file_:
                timeline_path = os.path.join(root, file_)
                return timeline_path
    return

def get_absolute_ts_start_info(pro_path) -> float:
    for root, dirs, files in os.walk(pro_path):
        for file in files:
            if "start_info" in file and ".done" not in file:
                start_json = os.path.join(root, file)
                break
    if start_json:
        with open(start_json, "r+") as f:
            info = json.load(f)
        ts_us = float(info.get("collectionTimeBegin", 0))
        ts_ns = float(info.get("clockMonotonicRaw", 0))
        if not ts_us and not ts_ns:
            return 0
    return ts_us-ts_ns/1000

def get_rank_id_from_info_json(pro_path):
    info_json = ""
    rank_id = None
    for root, dirs, files in os.walk(pro_path):
        for file in files:
            if "info.json." in file and ".done" not in file:
                info_json = os.path.join(root, file)
                break

    if info_json:
        with open(info_json, "r+") as f:
            info = json.load(f)
        rank_id = info.get("rank_id")
    return rank_id


def merge_timeline_general(args):
    """合并e2e profiling生成的msprof*.json"""
    prof_dir = get_path_dir(args.input)
    timeline_info = get_timeline_info(args, prof_dir)
    timeline_files_dict = {}

    # 合并部分profiling items
    process_list = args.items.split(",") if args.items else None

    # 合并部分rank
    if args.rank:
        rank_ids = [int(rank_id) for rank_id in args.rank.split(",")]
    else:
        rank_ids = list(timeline_info.keys())

    for rank_id in rank_ids:
        if not timeline_info.get(rank_id):
            print(f"main.py: error rank_id '{rank_id}' ")
            return
        timeline_files_dict[rank_id] = timeline_info.get(rank_id)
    merge_timeline_events(timeline_files_dict, process_list)


def merge_timeline_custom(args):
    """合并指定目录里所有timeline文件"""
    timeline_files = natural_sort(os.listdir(args.input))
    timeline_files_dict = {}
    for idx, timeline_file in enumerate(timeline_files):
        timeline_files_dict[idx] = (os.path.join(args.input, timeline_file),0)
    # 合并部分profiling items
    process_list = args.items.split(",") if args.items else None
    merge_timeline_events(timeline_files_dict, process_list)


def merge_timeline_events(timeline_file_dict, process_list):
    """
    输入需要合并的timeline文件路径及对应的rank_id/id、需要合并的process_list、校准时间差node_time_diff
    输出合并timeline
    """
    new_events = []
    for rank_id, data_tuple in timeline_file_dict.items():
        timeline_file_path = data_tuple[0]
        ts_difference_us = data_tuple[1]
        node = rank_id // 8
        print("rank id: ", rank_id, "timeline file: ", timeline_file_path)

        try:
            with open(timeline_file_path, 'r+') as f:
                cur_events = json.load(f)
        except Exception as err:
            print("[ERROR] %s" % err)
            return

        proc_pid_dict = {}
        for event in cur_events:
            if event.get("name") == "process_name" and event.get("ph") == "M":
                if event.get("args"):
                    proc_pid_dict[event["args"].get("name")] = event.get("pid")
        process_list_tmp = process_list if process_list else list(proc_pid_dict.keys())
        # 提取待合并的items的pid
        merged_pids = set()
        for pro in process_list_tmp:
            if pro not in proc_pid_dict.keys():
                print(f"main.py: error argument --items: invalid choice: '{pro}' (choose from {list(proc_pid_dict.keys())})")
                return
            merged_pids.add(proc_pid_dict.get(pro))

        for event in cur_events:

            # 只合并特定数据项
            if merged_pids and event.get('pid') not in merged_pids:
                continue

            # 当前节点间时间误差可用时，进行时间校准
            if event.get("ts") and ts_difference_us:
                event["ts"] = str(Decimal(str(event["ts"])) + Decimal(str(ts_difference_us)))

            # convert tid to int
            if isinstance(event.get("tid"), str):
                event["tid"] = int(''.join(x for x in event["tid"] if x.isdigit()))

            # 进程名加上rank_id区分不同rank
            if event.get("name") == "process_name" and event.get("ph") == "M":
                if event.get("args") is not None and event["args"].get("name") is not None:
                    event["args"]["name"] = event["args"]["name"] + f"_{rank_id}"

            #modify connect id
            if event.get('id') and (event.get('ph') == 's' or event.get('ph') == 'f'):
                event['id'] = float(event.get('id')) * RANK_ID_POS + rank_id

            new_events.append(event)
    out_path = f"{args.output}.json"
    if os.path.exists(out_path):
        print(f"File {out_path} existed before and is now overwritten.")
        os.remove(out_path)
    try:
        with open(out_path, 'w') as f:
            json.dump(new_events, f)
    except FileNotFoundError:
        print(f"Param -o (output path) is not exists, please check it.")
        return
    print(f"timeline merged output path: {out_path}")


def parse_args():
    parser = ArgumentParser(description="Merge timeline for multi card")
    parser.add_argument("-i", "--input", default=None, help="root dir of PROF_* data")
    parser.add_argument("-o", "--output", default="./merged", help="save path of merged.json ")
    parser.add_argument("--rank", default=None, help="List of ranks to be merged. By default, all ranks are merged")
    parser.add_argument("--items", default=None, help="Specify the data items (python，CANN，Ascend Hardware，HCCL，..)to be merged. in the timeline.")
    parser.add_argument("--type", choices=('pytorch', 'e2e', 'custom'), help="Customize the timeline file to be merged.")
    arg = parser.parse_args()
    return arg


if __name__ == "__main__":
    args = parse_args()
    print("========================== start merge timeline ====================")
    if args.type == "custom":
        merge_timeline_custom(args)
    else:
        merge_timeline_general(args)