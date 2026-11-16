"""宿主只接受 saveStruct 的结构数据子集，不执行脚本。"""
import unittest

from plugins.kirikiri.struct_data import decode


class KiriKiriDataTests(unittest.TestCase):
    def test_decodes_native_const_dictionary_and_event_array(self):
        self.assertEqual(decode('(const) %["seq"=>3,"events"=>(const) [(const) %["kind"=>"up"]]]'),
                         {'seq': 3, 'events': [{'kind': 'up'}]})

    def test_rejects_script_duplicate_fields_truncation_and_extra_records(self):
        for text in ('Scripts.eval("1")', '%["a"=>1,"a"=>2]', '%["a"=>', '%[] %[]',
                     '%["a"=>true]', '%["a"=>1.5]', '%["a"=>["x"]]garbage'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode(text)

    def test_limits_nesting(self):
        with self.assertRaisesRegex(ValueError, '嵌套过深'):
            decode('%["x"=>' + '[' * 18 + '1' + ']' * 18 + ']')


if __name__ == '__main__':
    unittest.main()
