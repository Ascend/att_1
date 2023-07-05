import json
import os


class ProfilingInfo:
    def __init__(self):
        self.cube_time = 0.0
        self.vector_time = 0.0
        self.large_kernel = 0.0
        self.communication_not_overlapped = 0.0
        self.scheduling_ratio = 0.0
        self.memory_used = 0.0
        self.e2e_time = 0.0
        self.scheduling_time = 0.0

    def __setattr__(self, key, value):
        if value < 0:
            raise ValueError(f"The {key} value shouldn't be less than 0.")
        super().__setattr__(key, value)


def read_json_file(path):
    if not os.path.isfile(path):
        raise ValueError(f'The path "{path}" is not a valid json file.')
    with open(path, 'r', encoding='utf-8') as json_handler:
        data = json.load(json_handler)
    return data
