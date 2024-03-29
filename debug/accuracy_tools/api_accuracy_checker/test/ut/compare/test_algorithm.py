import unittest
import numpy as np
from api_accuracy_checker.compare import algorithm as alg

class TestAlgorithmMethods(unittest.TestCase):

    def test_compare_torch_tensor(self):
        cpu_output = np.array([1, 2, 3])
        npu_output = np.array([1, 2, 3])
        compare_alg = alg.get_max_rel_err
        self.assertEqual(alg.compare_torch_tensor(cpu_output, npu_output, compare_alg), (0.0, True, ''))

    def test_compare_bool_tensor(self):
        cpu_output = np.array([True, False, True])
        npu_output = np.array([True, False, True])
        self.assertEqual(alg.compare_bool_tensor(cpu_output, npu_output), (0.0, True, ''))

    def test_get_msg_and_handle_value(self):
        b_value = np.array([1.0, 2.0, 3.0])
        n_value = np.array([1.0, 2.0, 3.0])
        self.assertEqual(alg.get_msg_and_handle_value(b_value, n_value), (b_value, n_value, ''))

    def test_get_max_rel_err(self):
        b_value = np.array([1.0, 2.0, 3.0])
        n_value = np.array([1.0, 2.0, 3.0])
        self.assertEqual(alg.get_max_rel_err(b_value, n_value), (0.0, True, ''))

    def test_get_max_abs_err(self):
        b_value = np.array([1.0, 2.0, 3.0])
        n_value = np.array([1.0, 2.0, 3.0])
        self.assertEqual(alg.get_max_abs_err(b_value, n_value), (0.0, True, ''))

    def test_get_rel_err_ratio_thousandth(self):
        b_value = np.array([1.0, 2.0, 3.0])
        n_value = np.array([1.0, 2.0, 3.0])
        self.assertEqual(alg.get_rel_err_ratio_thousandth(b_value, n_value), (1.0, True, ''))

    def test_get_rel_err_ratio_ten_thousandth(self):
        b_value = np.array([1.0, 2.0, 3.0])
        n_value = np.array([1.0, 2.0, 3.0])
        self.assertEqual(alg.get_rel_err_ratio_ten_thousandth(b_value, n_value), (1.0, True, ''))

    def test_max_rel_err_standard(self):
        max_rel_errs = [0.0001, 0.0002, 0.0003]
        result, arr = alg.max_rel_err_standard(max_rel_errs)
        self.assertEqual(result, True)
        self.assertTrue((arr == np.array([True, True, True])).all())

    def test_cosine_standard(self):
        compare_result = [0.9999, 0.9999, 0.9999]
        result, arr = alg.cosine_standard(compare_result)
        self.assertEqual(result, True)
        self.assertTrue((arr == np.array([True, True, True])).all())

    def test_cosine_sim(self):
        cpu_output = np.array([1.0, 2.0, 3.0])
        npu_output = np.array([1.0, 2.0, 3.0])
        self.assertEqual(alg.cosine_sim(cpu_output, npu_output), (1.0, True, ''))

    def test_compare_uint8_data(self):
        b_value = np.array([1, 2, 3], dtype=np.uint8)
        n_value = np.array([1, 2, 3], dtype=np.uint8)
        self.assertEqual(alg.compare_uint8_data(b_value, n_value), (1, True))

    def test_compare_builtin_type(self):
        bench_out = 1
        npu_out = 1
        self.assertEqual(alg.compare_builtin_type(bench_out, npu_out), (True, True, ''))

    def test_flatten_compare_result(self):
        result = [[1, 2], [3, 4]]
        self.assertEqual(alg.flatten_compare_result(result), [1, 2, 3, 4])
