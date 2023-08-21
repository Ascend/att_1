# 定义API INFO，保存基本信息，用于后续结构体的落盘，注意考虑random场景及真实数据场景
import os
import inspect
import torch
import torch_npu
from api_accuracy_checker.common.config import msCheckerConfig
from api_accuracy_checker.common.utils import print_error_log
from api_accuracy_checker.common.config import msCheckerConfig
from api_accuracy_checker.dump.utils import write_npy

class APIInfo:
    def __init__(self, api_name, is_forward):
        self.rank = os.getpid()
        self.api_name = api_name
        self.save_real_data = msCheckerConfig.real_data
        self.torch_object_key = {'device': self.analyze_device_in_kwargs, 'dtype': self.analyze_dtype_in_kwargs}
        self.is_forward = is_forward
        self.args_num = 0
        
    def analyze_element(self, element, is_save_data, save_path, forward_path='forward_real_data', backward_path='backward_real_data'):
        if isinstance(element, (list, tuple)):
            out = []
            for item in element:
                out.append(self.analyze_element(item, is_save_data, save_path, forward_path, backward_path))
        elif isinstance(element, dict):
            out = {}
            for key, value in element.items():
                if key in self.torch_object_key.keys():
                    fun = self.torch_object_key[key]
                    out[key] = fun(value)
                else:
                    out[key] = self.analyze_element(value, is_save_data, save_path, forward_path, backward_path)

        elif isinstance(element, torch.Tensor):
            out = self.analyze_tensor(element, is_save_data, save_path, forward_path, backward_path)

        elif self.is_builtin_class(element):
            out = self.analyze_builtin(element)
        else:
            msg = f"Type {type(element)} is unsupported at analyze_element"
            print_error_log(msg)

            raise NotImplementedError(msg)
        return out


    def analyze_tensor(self, arg, is_save_data, save_path, forward_path, backward_path):
        single_arg = {}
        if not is_save_data:
            
            single_arg.update({'type' : 'torch.Tensor'})
            single_arg.update({'dtype' : str(arg.dtype)})
            single_arg.update({'shape' : arg.shape})
            single_arg.update({'Max' : self.transfer_types(self.get_tensor_extremum(arg,'max'), str(arg.dtype))})
            single_arg.update({'Min' : self.transfer_types(self.get_tensor_extremum(arg,'min'), str(arg.dtype))})
            single_arg.update({'requires_grad': arg.requires_grad})
            
        else:
            api_args = self.api_name + '*' + str(self.args_num)
            if self.is_forward:
                forward_real_data_path = os.path.join(save_path, forward_path)

                file_path = os.path.join(forward_real_data_path, f'{api_args}.npy')
            else:
                backward_real_data_path = os.path.join(save_path, backward_path)
                file_path = os.path.join(backward_real_data_path, f'{api_args}.npy')
            self.args_num += 1
            npy_path = write_npy(file_path, arg.contiguous().cpu().detach().numpy())
            single_arg.update({'type' : 'torch.Tensor'})
            single_arg.update({'datapath' : npy_path})
            single_arg.update({'requires_grad': arg.requires_grad})
        return single_arg

    def analyze_builtin(self, arg):
        single_arg = {}
        if isinstance(arg, slice):
            single_arg.update({'type' : "slice"})
            single_arg.update({'value' : [arg.start, arg.stop, arg.step]})
        else:
            single_arg.update({'type' : self.get_type_name(str(type(arg)))})
            single_arg.update({'value' : arg})
        return single_arg

    def transfer_types(self, data, dtype):
        if 'int' in dtype or 'bool' in dtype:
            return int(data)
        else:
            return float(data)

    def is_builtin_class(self, element):
        if element is None or isinstance(element, (bool,int,float,str,slice)):
            return True
        return False
        
    def analyze_device_in_kwargs(self, element):
        single_arg = {}
        single_arg.update({'type' : 'torch.device'})
        if not isinstance(element, str):
            
            if hasattr(element, "index"):
                device_value = element.type + ":" + str(element.index)
                single_arg.update({'value' : device_value})
            else:
                device_value = element.type
        else:
            single_arg.update({'value' : element})
        return single_arg
    
    def analyze_dtype_in_kwargs(self, element):
        single_arg = {}
        single_arg.update({'type' : 'torch.dtype'})
        single_arg.update({'value' : str(element)})
        return single_arg
    
    def get_tensor_extremum(self, data, operator):
        if data.dtype is torch.bool:
            if operator == 'max':
                return True in data
            elif operator == 'min':
                return False not in data
        if operator == 'max':
            return torch._C._VariableFunctionsClass.max(data).item()
        else:
            return torch._C._VariableFunctionsClass.min(data).item()
    
    def get_type_name(self, name):

        left = name.index("'")
        right = name.rindex("'")
        return name[left + 1 : right]



class ForwardAPIInfo(APIInfo):
    def __init__(self, name, args, kwargs):
        super().__init__(name, is_forward=True)
        self.analyze_api_input(args, kwargs)
        self.analyze_api_call_stack() 
    
    def analyze_api_input(self, args, kwargs):
        args_info_list = self.analyze_element(args, self.save_real_data, msCheckerConfig.dump_path)
        kwargs_info_dict = self.analyze_element(kwargs, self.save_real_data, msCheckerConfig.dump_path)
        self.api_info_struct = {self.api_name: {"args":args_info_list, "kwargs":kwargs_info_dict}}

    def analyze_api_call_stack(self):
        stack_str = []
        for (_, path, line, func, code, _) in inspect.stack()[3:]:
            if not code: continue
            stack_line = " ".join([
                "File", ", ".join([path, " ".join(["line", str(line)]), " ".join(["in", func]),
                                " ".join(["\n", code[0].strip()])])])
            stack_str.append(stack_line)
        self.stack_info_struct = {self.api_name: stack_str}
    

class BackwardAPIInfo(APIInfo):
    def __init__(self, name, grads):
        super().__init__(name, is_forward=False)
        self.analyze_api_input(grads)
    
    def analyze_api_input(self, grads):
        grads_info_list = self.analyze_element(grads, self.save_real_data, msCheckerConfig.dump_path)
        self.grad_info_struct = {self.api_name:grads_info_list}


class ForwardErrorAPIInfo(APIInfo):
    def __init__(self, name, args, kwargs):
        super().__init__(name, is_forward=True)
        self.analyze_api_input(args, kwargs)
    
    def analyze_api_input(self, args, kwargs):
        self.analyze_element(args, True, msCheckerConfig.error_data_path, forward_path='forward_error_data')
        self.analyze_element(kwargs, True, msCheckerConfig.error_data_path, forward_path='forward_error_data')


class BackwardErrorAPIInfo(APIInfo):
    def __init__(self, name, grads):
        super().__init__(name, is_forward=False)
        self.analyze_element(grads, True, msCheckerConfig.error_data_path, backward_path='backward_error_data')

