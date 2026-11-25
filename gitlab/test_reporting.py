import unittest
from reporting import summary


class ReportingTests(unittest.TestCase):
    def test_partial_failure_explicit(self):
        text=summary({'191':{'status':'failed','error':'Source backup stale'},
            '185':{'status':'success','delivery':{'run':'20261120T010000-1','targets':{'nas243':True,'nas240':True,'win254':True},'verification':'pending_md5'}}},65)
        self.assertIn('部分失败',text);self.assertIn('191',text);self.assertIn('未做MD5',text)
        self.assertNotIn('MD5通过',text)
    def test_md5_is_distinct(self):
        text=summary({'1':{'status':'success','verification':'md5_verified','targets':{'nas243':True}}},1)
        self.assertIn('MD5通过',text)
    def test_check_does_not_claim_backup(self):
        text=summary({'1':{'status':'success'}},5,action='check')
        self.assertIn('未执行备份',text)
    def test_unknown_md5_not_verified(self):
        self.assertIn('未做MD5',summary({'1':{'status':'success'}},5))


if __name__=='__main__':unittest.main()
