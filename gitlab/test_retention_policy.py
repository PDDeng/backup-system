import unittest
from retention_policy import keep_cycles

class RetentionPolicyTests(unittest.TestCase):
    def test_destination_override_does_not_change_other_copies(self):
        c={'keep_days':15,'cleanup_mode':'delete'}
        self.assertEqual(keep_cycles(c,{'id':'win254','keep_days':7}),7)
        self.assertEqual(keep_cycles(c,{'id':'nas243'}),15)
        self.assertEqual(keep_cycles(c,{'id':'nas240'}),15)
        self.assertEqual(c,{'keep_days':15,'cleanup_mode':'delete'})
    def test_invalid_policy_is_rejected(self):
        for value in (0,-1,True,'7',3651):
            with self.assertRaises(ValueError):keep_cycles({'keep_days':15},{'keep_days':value})
    def test_legacy_default(self):self.assertEqual(keep_cycles({},{}),15)

if __name__=='__main__':unittest.main()
