#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# Copyright (C) 2019-2020. Huawei Technologies Co., Ltd. All rights reserved.
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
"""

import json
import multiprocessing
import os.path
import stat
import sys

import numpy as np
import pandas as pd

from ..advisor.advisor import Advisor
from ..common.utils import check_compare_param, add_time_as_suffix, \
    print_warn_log, print_error_log, CompareException, Const,\
    CompareConst, format_value, check_file_not_exists
from ..common.file_check_util import FileChecker, FileCheckConst, change_mode


def correct_data(result):
    if result == CompareConst.NAN:
        return result
    if float(result) > 0.99999:
        return '1.0'
    return result


def cosine_similarity(n_value, b_value):
    np.seterr(divide='ignore', invalid='ignore')
    if len(n_value) == 1:
        return "unsupported", "This tensor is scalar."
    num = n_value.dot(b_value)
    a_norm = np.linalg.norm(n_value)
    b_norm = np.linalg.norm(b_value)
    message = ''
    if a_norm <= Const.FLOAT_EPSILON and b_norm <= Const.FLOAT_EPSILON:
        result = '1.0'
    elif a_norm <= Const.FLOAT_EPSILON:
        message = 'Cannot compare by Cosine Similarity, All the data is Zero in npu dump data.'
        result = CompareConst.NAN
    elif b_norm <= Const.FLOAT_EPSILON:
        message = 'Cannot compare by Cosine Similarity, All the data is Zero in Bench dump data.'
        result = CompareConst.NAN
    else:
        cos = num / (a_norm * b_norm)
        if np.isnan(cos):
            message = 'Cannot compare by Cosine Similarity, the dump data has NaN.'
            result = CompareConst.NAN
        else:
            result = format_value(cos)
    result = correct_data(result)
    return result, message


def get_rmse(n_value, b_value):
    if len(n_value) == 0 and len(b_value) == 0:
        rmse = '0'
    elif len(n_value) == 0:
        rmse = CompareConst.NAN
    elif len(b_value) == 0:
        rmse = CompareConst.NAN
    else:
        rmse = np.linalg.norm(n_value - b_value) / np.sqrt(len(n_value))
    if np.isnan(rmse):
        rmse = CompareConst.NAN
    return rmse, ""


def get_mape(n_value, b_value):
    if len(n_value) == 0 and len(b_value) == 0:
        mape = '0'
    elif len(n_value) == 0:
        mape = CompareConst.NAN
    elif len(b_value) == 0:
        mape = CompareConst.NAN
    elif not np.all(n_value) and not np.all(b_value):
        mape = '0'
    elif not np.all(b_value):
        mape = CompareConst.NAN
    else:
        mape_val = np.sum(np.abs((n_value - b_value) / b_value)) / len(b_value) * 100
        mape = CompareConst.NAN if np.isnan(mape_val) else str(round(mape_val, 4)) + '%'
    return mape, ""


def get_max_abs_err(n_value, b_value):
    temp_res = n_value - b_value
    max_value = np.max(np.abs(temp_res))
    return format_value(max_value), ""


def get_max_relative_err(n_value, b_value):
    np.seterr(divide='ignore', invalid='ignore')
    if b_value.dtype in CompareConst.FLOAT_TYPE:
        zero_mask = (b_value == 0)
        b_value[zero_mask] += np.finfo(b_value.dtype).eps
        n_value[zero_mask] += np.finfo(b_value.dtype).eps
    else:
        n_value, b_value = n_value.astype(float), b_value.astype(float)
        zero_mask = (b_value == 0)
        b_value[zero_mask] += np.finfo(float).eps
        n_value[zero_mask] += np.finfo(float).eps
    relative_err = np.divide((n_value - b_value), b_value)
    max_relative_err = np.max(np.abs(relative_err))
    if np.isnan(max_relative_err):
        message = 'Cannot compare by MaxRelativeError, the data contains nan in dump data.'
        return CompareConst.NAN, message
    return format_value(max_relative_err), ""


def check_op(npu_dict, bench_dict, fuzzy_match):
    a_op_name = npu_dict["op_name"]
    b_op_name = bench_dict["op_name"]
    struct_match = check_struct_match(npu_dict, bench_dict)
    if not fuzzy_match:
        return a_op_name == b_op_name and struct_match
    is_match = True
    try:
        is_match = fuzzy_check_op(a_op_name, b_op_name)
    except Exception as err:
        print_warn_log("%s and %s can not fuzzy match." % (a_op_name, b_op_name))
        is_match = False
    return is_match and struct_match


def check_struct_match(npu_dict, bench_dict):
    npu_struct_in = npu_dict.get("input_struct")
    bench_struct_in = bench_dict.get("input_struct")
    npu_struct_out = npu_dict.get("output_struct")
    bench_struct_out = bench_dict.get("output_struct")
    is_match = npu_struct_in == bench_struct_in and npu_struct_out == bench_struct_out
    if not is_match:
        if len(npu_struct_in) == 0 or len(bench_struct_in) == 0 or len(npu_struct_in) != len(bench_struct_in):
            return False
        struct_in_is_match = check_type_shape_match(npu_struct_in, bench_struct_in)
        struct_out_is_match = check_type_shape_match(npu_struct_out, bench_struct_out)
        is_match = struct_in_is_match and struct_out_is_match
    return is_match


def check_type_shape_match(npu_struct, bench_struct):
    shape_type_match = False
    for npu_type_shape, bench_type_shape in zip(npu_struct, bench_struct):
        npu_type = npu_type_shape[0]
        npu_shape = npu_type_shape[1]
        bench_type = bench_type_shape[0]
        bench_shape = bench_type_shape[1]
        shape_match = npu_shape == bench_shape
        type_match = npu_type == bench_type
        if not type_match:
            if [npu_type, bench_type] in [["torch.float16", "torch.float32"], ["torch.float32", "torch.float16"],
                                          ["torch.float16", "torch.bfloat16"], ["torch.bfloat16", "torch.float16"]]:
                type_match = True
            else:
                type_match = False
        shape_type_match = shape_match and type_match
        if not shape_type_match:
            return False
    return shape_type_match


def fuzzy_check_op(npu_name_list, bench_name_list):
    if len(npu_name_list) == 0 or len(bench_name_list) == 0 or len(npu_name_list) != len(bench_name_list):
        return False
    is_match = True
    for npu_name, bench_name in zip(npu_name_list, bench_name_list):
        is_match = fuzzy_check_name(npu_name, bench_name)
        if not is_match:
            break
    return is_match


def fuzzy_check_name(npu_name, bench_name):
    if "forward" in npu_name and "forward" in bench_name:
        is_match = rename_api(npu_name, "forward") == rename_api(bench_name, "forward")
    elif "backward" in npu_name and "backward" in bench_name:
        is_match = rename_api(npu_name, "backward") == rename_api(bench_name, "backward")
    else:
        is_match = npu_name == bench_name
    return is_match


def rename_api(npu_name, process):
    npu_split = npu_name.split(process)
    torch_func_index, in_out = npu_split[0], npu_split[1]
    torch_func_split = torch_func_index.rsplit("_", 2)
    torch_func = str(torch_func_split[0]) + str(in_out)
    return torch_func


def merge_tensor(tensor_list):
    op_dict = {}
    op_dict["op_name"] = []
    op_dict["input_struct"] = []
    op_dict["output_struct"] = []
    op_dict["summery"] = []
    op_dict["stack_info"] = []

    for tensor in tensor_list:
        if tensor[0].find("stack_info") != -1:
            op_dict["stack_info"].append(tensor[1])
            break
        op_dict["op_name"].append(tensor[0])
        if tensor[0].find("input") != -1:
            op_dict["input_struct"].append((tensor[3], tensor[4]))
        elif tensor[0].find("output") != -1:
            op_dict["output_struct"].append((tensor[3], tensor[4]))

        if tensor[1] <= Const.DUMP_RATIO_MAX:
            op_dict["summery"].append(tensor[5])

    return op_dict


def read_op(ops_queue, pkl_file_handle, stack_mode):
    tensor_list = []
    read_err = False
    read_output_flag = {"last_line": False, "curr_line": False}
    end_flag = "stack_info" if stack_mode is True else "output"

    while True:
        curr_pos = pkl_file_handle.tell()
        tensor_line = pkl_file_handle.readline()
        if len(tensor_line) == 0 and not read_output_flag.get("curr_line"):
            read_err = True
            break
        if tensor_line == '\n':
            continue
        if len(tensor_line) != 0:
            tensor_data = json.loads(tensor_line)
            read_output_flag["last_line"] = read_output_flag.get("curr_line")
            read_output_flag["curr_line"] = True if tensor_data[0].find(end_flag) != -1 else False

        if (read_output_flag.get("last_line") and not read_output_flag.get("curr_line")) \
                or (len(tensor_line) == 0 and read_output_flag.get("curr_line")):  # end of file scenario
            ops_queue.append(merge_tensor(tensor_list))
            # the pos of the handle needs to restore to the start of the next api.
            pkl_file_handle.seek(curr_pos, 0)
            break
        tensor_list.append(tensor_data)

    return not read_err


def match_op(npu_queue, bench_queue, fuzzy_match):
    for b_index, b_op in enumerate(bench_queue[0: -1]):
        if check_op(npu_queue[-1], b_op, fuzzy_match):
            return len(npu_queue) - 1, b_index
    if check_op(npu_queue[-1], bench_queue[-1], fuzzy_match):
        return len(npu_queue) - 1, len(bench_queue) - 1
    for n_index, n_op in enumerate(npu_queue[0: -1]):
        if check_op(n_op, bench_queue[-1], fuzzy_match):
            return n_index, len(bench_queue) - 1
    return -1, -1


def get_accuracy(result, n_dict, b_dict):
    index_out = 0
    npu_stack_info = n_dict.get("stack_info", None)
    bench_stack_info = b_dict.get("stack_info", None)

    for index, n_name in enumerate(n_dict["op_name"]):
        b_name = b_dict["op_name"][index]
        if n_name.find("input") != -1:
            n_struct = n_dict["input_struct"][index]
            b_struct = b_dict["input_struct"][index]
        else:
            n_struct = n_dict["output_struct"][index_out]
            b_struct = b_dict["output_struct"][index_out]
            index_out += 1
        err_msg = ""
        accuracy_check_res = CompareConst.ACCURACY_CHECK_YES

        result_item = [n_name, b_name, n_struct[0], b_struct[0], n_struct[1], b_struct[1], " ", " ", " "]

        summery_data = n_dict.get("summery")[index]
        result_item.extend(summery_data)

        summery_data = b_dict.get("summery")[index]
        result_item.extend(summery_data)
        result_item.append(accuracy_check_res)
        result_item.append(err_msg)
        if npu_stack_info and bench_stack_info and index == 0:
            result_item.extend(npu_stack_info)

        result.append(result_item)


def _do_multi_process(input_parma, result_path):
    try:
        _handle_multi_process(compare_ops, input_parma, result_path, multiprocessing.Manager().RLock())
    except FileNotFoundError as error:
        print("File not Found. compare failed!")
        return
    except IOError as error:
        print("IOEError. compare failed!")
        return


def read_dump_path(result_path):
    try:
        csv_pd = pd.read_csv(result_path)
        npu_dump_name_list = csv_pd.iloc[0:, 0].tolist()
        bench_dump_name_list = csv_pd.iloc[0:, 1].tolist()
        op_name_mapping_dict = {}
        for index, _ in enumerate(npu_dump_name_list):
            npu_dump_name = npu_dump_name_list[index]
            bench_dump_name = bench_dump_name_list[index]
            op_name_mapping_dict[npu_dump_name] = [npu_dump_name, bench_dump_name]
        return op_name_mapping_dict
    except FileNotFoundError as e:
        print_error_log('{} file is not found.'.format(result_path))
        raise CompareException(CompareException.OPEN_FILE_ERROR) from e
    except IOError as e:
        print_error_log('{} read csv failed.'.format(result_path))
        raise CompareException(CompareException.READ_FILE_ERROR) from e


def _handle_multi_process(func, input_parma, result_path, lock):
    process_num = int((multiprocessing.cpu_count() + 1) / 2)
    op_name_mapping_dict = read_dump_path(result_path)
    op_names = []
    for _ in range(process_num):
        op_names.append([])
    all_op_names = list(op_name_mapping_dict.keys())
    for i, op_name in enumerate(all_op_names):
        op_names[i % process_num].append(op_name)
    all_tasks = []
    pool = multiprocessing.Pool(process_num)

    def err_call(args):
        print_error_log('multiprocess compare failed! season:{}'.format(args))
        try:
            pool.terminate()
            if os.path.exists(result_path):
                os.remove(result_path)
        except OSError as e:
            print_error_log("pool terminate failed")

    for process_idx, fusion_op_names in enumerate(op_names):
        idx = [process_num, process_idx]
        task = pool.apply_async(func,
                                args=(idx, fusion_op_names, op_name_mapping_dict, result_path, lock, input_parma),
                                error_callback=err_call)
        all_tasks.append(task)
    pool.close()
    pool.join()


def compare_ops(idx, fusion_op_names, dump_path_dict, result_path, lock, input_parma):
    cos_result = []
    max_err_result = []
    max_relative_err_result = []
    err_mess = []
    is_print_compare_log = input_parma.get("is_print_compare_log")
    for i, op_name in enumerate(fusion_op_names):
        if is_print_compare_log:
            print("start comapre: {}".format(op_name))
        cos_sim, max_abs_err, max_relative_err, err_msg = compare_by_op(op_name, dump_path_dict, input_parma)
        if is_print_compare_log:
            print("[{}] Compare result: cosine {}, max_abs_err {}, max_relative_err {}, {}".format(op_name, cos_sim, max_abs_err, max_relative_err, err_msg))
        cos_result.append(cos_sim)
        max_err_result.append(max_abs_err)
        max_relative_err_result.append(max_relative_err)
        err_mess.append(err_msg)
    _save_cmp_result(idx, cos_result, max_err_result, max_relative_err_result, err_mess, result_path, lock)


def _save_cmp_result(idx, cos_result, max_err_result, max_relative_err_result, err_msg, result_path, lock):
    lock.acquire()
    try:
        csv_pd = pd.read_csv(result_path, dtype=str)
        process_num = idx[0]
        process_idx = idx[1]
        for i, _ in enumerate(cos_result):
            process_index = i * process_num + process_idx
            csv_pd.loc[process_index, CompareConst.COSINE] = cos_result[i]
            csv_pd.loc[process_index, CompareConst.MAX_ABS_ERR] = max_err_result[i]
            csv_pd.loc[process_index, CompareConst.MAX_RELATIVE_ERR] = max_relative_err_result[i]
            csv_pd.loc[process_index, CompareConst.ERROR_MESSAGE] = err_msg[i]
            csv_pd.loc[process_index, CompareConst.ACCURACY] = check_accuracy(cos_result[i], max_err_result[i])
        csv_pd.to_csv(result_path, index=False)
    except FileNotFoundError as e:
        print_error_log('{} file is not found.'.format(result_path))
        raise CompareException(CompareException.OPEN_FILE_ERROR) from e
    except IOError as e:
        print_error_log('{} read csv failed.'.format(result_path))
        raise CompareException(CompareException.READ_FILE_ERROR) from e
    finally:
        lock.release()


def check_accuracy(cos, max_abs_err):
    if cos == CompareConst.SHAPE_UNMATCH:
        return CompareConst.ACCURACY_CHECK_UNMATCH
    if cos == CompareConst.NAN or max_abs_err == CompareConst.NAN:
        return CompareConst.NAN
    if cos == "N/A" or max_abs_err == "N/A":
        return CompareConst.ACCURACY_CHECK_NO
    try:
        cos, max_abs_err = float(cos), float(max_abs_err)
    except ValueError:
        print_warn_log("Cosine or MaxAbsErr can not get float value.")
        return CompareConst.NAN
    if cos < CompareConst.COS_THRESHOLD and max_abs_err > CompareConst.MAX_ABS_ERR_THRESHOLD:
        return CompareConst.ACCURACY_CHECK_NO
    if cos < CompareConst.COS_MAX_THRESHOLD or max_abs_err > CompareConst.MAX_ABS_ERR_MAX_THRESHOLD:
        return CompareConst.ACCURACY_CHECK_NO
    return CompareConst.ACCURACY_CHECK_YES


def compare_by_op(op_name, op_name_mapping_dict, input_parma):
    npu_bench_name_list = op_name_mapping_dict[op_name]
    if npu_bench_name_list[1] == CompareConst.NAN:
        return CompareConst.NAN, CompareConst.NAN, CompareConst.NAN, CompareConst.NO_BENCH
    try:
        n_path = os.path.join(input_parma.get("npu_dump_data_dir"), npu_bench_name_list[0] + ".npy")
        b_path = os.path.join(input_parma.get("bench_dump_data_dir"), npu_bench_name_list[1] + ".npy")
        n_path_checker = FileChecker(n_path, FileCheckConst.FILE, FileCheckConst.READ_ABLE,
                                     FileCheckConst.NUMPY_SUFFIX)
        b_path_checker = FileChecker(b_path, FileCheckConst.FILE, FileCheckConst.READ_ABLE,
                                     FileCheckConst.NUMPY_SUFFIX)
        n_path = n_path_checker.common_check()
        b_path = b_path_checker.common_check()
        n_value = np.load(n_path)
        b_value = np.load(b_path)
    except IOError as error:
        return CompareConst.NAN, CompareConst.NAN, CompareConst.NAN, "Dump file:{} not found.".format(error.filename)
    if len(n_value.shape) == 0:
        if n_value.dtype == bool:
            n_value = n_value.astype(float)
            b_value = b_value.astype(float)
        max_abs_err, _ = get_max_abs_err(n_value, b_value)
        max_relative_err, _ = get_max_relative_err(n_value, b_value)
        return "unsupported", max_abs_err, max_relative_err, "This is type of scalar data, can not compare."
    if n_value.size == 0:
        return "unsupported", 0, 0, "This is empty data, can not compare."
    if n_value.shape != b_value.shape:
        return CompareConst.SHAPE_UNMATCH, CompareConst.SHAPE_UNMATCH, CompareConst.SHAPE_UNMATCH, "Shape of NPU and bench Tensor do not match. Skipped."
    if n_value.dtype != b_value.dtype:
        print_warn_log("Dtype of NPU and bench Tensor do not match:{}".format(op_name))
        err_msg = " Dtype of NPU and bench Tensor do not match."
    else:
        err_msg = ""

    n_value, b_value = handle_inf_nan(n_value, b_value)
    if n_value is CompareConst.NAN or b_value is CompareConst.NAN:
        return "N/A", "N/A", "N/A",  "The position of inf or nan in NPU and bench Tensor do not match."

    n_value = n_value.reshape(-1).astype(float)
    b_value = b_value.reshape(-1).astype(float)
    err_msg = ""
    cos_sim, message = cosine_similarity(n_value, b_value)

    max_abs_err, _ = get_max_abs_err(n_value, b_value)
    max_relative_err, message = get_max_relative_err(n_value, b_value)

    if not err_msg:
        err_msg += message
    else:
        err_msg = err_msg + ' ' + message

    if npu_bench_name_list[0] != npu_bench_name_list[1]:
        err_msg += " Fuzzy matching data, the comparison accuracy may be affected."
    return cos_sim, max_abs_err, max_relative_err, err_msg


def handle_inf_nan(n_value, b_value):
    n_inf = np.isinf(n_value)
    b_inf = np.isinf(b_value)
    n_nan = np.isnan(n_value)
    b_nan = np.isnan(b_value)
    if np.any(n_inf) or np.any(b_inf) or np.any(n_nan) or np.any(b_nan):
        if np.array_equal(n_inf, b_inf) and np.array_equal(n_nan, b_nan):
            n_value[n_inf] = 0
            b_value[b_inf] = 0
            n_value[n_nan] = 0
            b_value[b_nan] = 0
        else:
            return CompareConst.NAN, CompareConst.NAN
    return n_value, b_value


def compare(input_parma, output_path, stack_mode=False, auto_analyze=True,
            fuzzy_match=False):
    try:
        npu_pkl, bench_pkl = check_compare_param(input_parma, output_path, stack_mode,
                                                 auto_analyze, fuzzy_match)
    except CompareException as error:
        print_error_log('Compare failed. Please check the arguments and do it again!')
        sys.exit(error.code)
    compare_core(input_parma, output_path, npu_pkl, bench_pkl, stack_mode=stack_mode,
                 auto_analyze=auto_analyze, fuzzy_match=fuzzy_match)


def compare_core(input_parma, output_path, npu_pkl, bench_pkl, stack_mode=False, auto_analyze=True,
                 suffix='', fuzzy_match=False):
    result = compare_process(npu_pkl, bench_pkl, stack_mode, fuzzy_match)
    npu_pkl.close()
    bench_pkl.close()

    columns = [CompareConst.NPU_NAME, CompareConst.BENCH_NAME, CompareConst.NPU_DTYPE, CompareConst.BENCH_DTYPE,
                CompareConst.NPU_SHAPE, CompareConst.BENCH_SHAPE, CompareConst.COSINE, CompareConst.MAX_ABS_ERR,
                   CompareConst.MAX_RELATIVE_ERR]
    columns.extend([CompareConst.NPU_MAX, CompareConst.NPU_MIN, CompareConst.NPU_MEAN])
    columns.extend([CompareConst.BENCH_MAX, CompareConst.BENCH_MIN, CompareConst.BENCH_MEAN])
    columns.extend([CompareConst.ACCURACY, CompareConst.ERROR_MESSAGE])
    if stack_mode:
        columns.extend([CompareConst.STACK])
    result_df = pd.DataFrame(result, columns=columns)

    file_name = add_time_as_suffix("compare_result" + suffix)
    file_path = os.path.join(os.path.realpath(output_path), file_name)
    check_file_not_exists(file_path)
    with os.fdopen(os.open(file_path, os.O_RDWR | os.O_CREAT, stat.S_IWUSR | stat.S_IRUSR | stat.S_IRGRP), 'w+') as fout:
        result_df.to_csv(fout, index=False)

    _do_multi_process(input_parma, file_path)
    change_mode(file_path, FileCheckConst.DATA_FILE_AUTHORITY)
    if auto_analyze:
        advisor = Advisor(file_path, output_path)
        advisor.analysis()


def parse(pkl_file, module_name_prefix):
    pkl_handle = open(pkl_file, "r")
    done = False
    title_printed = False
    while not done:
        pkl_line = pkl_handle.readline()
        if pkl_line == '\n':
            continue
        if len(pkl_line) == 0:
            done = True
            break

        msg = json.loads(pkl_line)
        info_prefix = msg[0]
        if not info_prefix.startswith(module_name_prefix):
            continue

        if info_prefix.find("stack_info") != -1:
            print("\nTrace back({}):".format(msg[0]))
            for item in reversed(msg[1]):
                print("  File \"{}\", line {}, in {}".format(item[0], item[1], item[2]))
                print("    {}".format(item[3]))
            continue
        if len(msg) > 5:
            summery_info = "  [{}][dtype: {}][shape: {}][max: {}][min: {}][mean: {}]" \
                .format(msg[0], msg[3], msg[4], msg[5][0], msg[5][1], msg[5][2])
            if not title_printed:
                print("\nStatistic Info:")
                title_printed = True
            print(summery_info)
    pkl_handle.close()


def compare_process(npu_pkl_handle, bench_pkl_handle, stack_mode, fuzzy_match):
    if fuzzy_match:
        print_warn_log("This task uses fuzzy matching, which may affect the accuracy of the comparison.")
    npu_ops_queue = []
    bench_ops_queue = []
    result = []
    while True:
        npu_file_flag = read_op(npu_ops_queue, npu_pkl_handle, stack_mode)
        bench_file_flag = read_op(bench_ops_queue, bench_pkl_handle, stack_mode)
        if (not npu_file_flag and not bench_file_flag) \
                or (len(npu_ops_queue) == 0 or len(bench_ops_queue) == 0):
            break
        n_match_point, b_match_point = match_op(npu_ops_queue, bench_ops_queue, fuzzy_match)
        if n_match_point == -1 and b_match_point == -1:
            continue
        n_match_data = npu_ops_queue[n_match_point]
        b_match_data = bench_ops_queue[b_match_point]
        un_match_data = npu_ops_queue[0: n_match_point]
        for npu_data in un_match_data:
            get_un_match_accuracy(result, npu_data)
        get_accuracy(result, n_match_data, b_match_data)
        del npu_ops_queue[0: n_match_point + 1]
        del bench_ops_queue[0: b_match_point + 1]
    if npu_ops_queue:
        for npu_data in npu_ops_queue:
            get_un_match_accuracy(result, npu_data)
    return result


def get_un_match_accuracy(result, n_dict):
    index_out = 0
    npu_stack_info = n_dict.get("stack_info", None)
    bench_name, bench_type, bench_shape = CompareConst.NAN, CompareConst.NAN, CompareConst.NAN
    for index, n_name in enumerate(n_dict["op_name"]):
        if n_name.find("input") != -1:
            n_struct = n_dict["input_struct"][index]
        else:
            n_struct = n_dict["output_struct"][index_out]
            index_out += 1
        err_msg = CompareConst.NO_BENCH
        accuracy_check_res = CompareConst.NAN

        result_item = [n_name, bench_name, n_struct[0], bench_type, n_struct[1], bench_shape, " ", " ", " "]
        summery_data = n_dict.get("summery")[index]
        result_item.extend(summery_data)
        summery_data = [CompareConst.NAN]*3
        result_item.extend(summery_data)
        result_item.append(accuracy_check_res)
        result_item.append(err_msg)
        if npu_stack_info and index == 0:
            result_item.extend(npu_stack_info)
        result.append(result_item)
