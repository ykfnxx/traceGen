"""Hand-calculated histories exercise prefix accounting independently of synthesis."""
import unittest
from experiments.analyze_prefix_reuse import PrefixHistory, aggregate


class PrefixReuseAccounting(unittest.TestCase):
    def test_prefix_history_cross_session_overlap_and_same_time(self):
        history=PrefixHistory()
        rows=[history.observe('a',0,[1,2,3]),
              history.observe('a',1,[1,2,4]),
              history.observe('b',1,[1,2,4,5]),
              history.observe('a',2,[1,2,4,5]),
              history.observe('a',3,[])]
        self.assertEqual([r['reused'] for r in rows],[0,2,3,4,0])
        self.assertEqual([r['within'] for r in rows],[0,2,0,3,0])
        self.assertEqual([r['cross'] for r in rows],[0,0,3,4,0])
        self.assertEqual([r['cross_extra'] for r in rows],[0,0,3,1,0])
        self.assertEqual([r['strictly_earlier'] for r in rows],[0,2,2,4,0])
        stats=aggregate(rows)
        self.assertEqual(stats['blocks'],14)
        self.assertEqual(stats['reused_rate'],9/14)
        self.assertEqual(stats['within_rate'],5/14)
        self.assertEqual(stats['cross_extra_rate'],4/14)
        self.assertEqual(stats['strictly_earlier_rate'],8/14)
        self.assertEqual(stats['empty_requests'],1)
        self.assertEqual(stats['fully_reused_nonempty_requests'],1)
        self.assertEqual(len(history.first_owner),5)
        self.assertEqual(len(history.multiple_owners),4)

    def test_cross_session_reuse_from_template_resampling(self):
        h=PrefixHistory()
        h.observe('a',0,[1,2],origin=('swiss',0))
        copy=h.observe('b',1,[1,2],origin=('swiss',0))
        self.assertEqual(copy['cross_extra_same_template_only'],2)
        self.assertEqual(copy['cross_extra_other_template'],0)
        other=h.observe('c',2,[1,2],origin=('swiss',1))
        self.assertEqual(other['cross_extra_other_template'],2)

    def test_old_branch_revisit_counts_beyond_previous_request(self):
        h=PrefixHistory()
        h.observe('a',0,[1,2,3])
        h.observe('a',1,[1,4])
        result=h.observe('a',2,[1,2,3])
        self.assertEqual(result['within'],3)
        self.assertEqual(result['cross'],0)

    def test_empty_requests_have_no_vacuous_full_hit(self):
        result=aggregate([PrefixHistory().observe('a',0,[])])
        self.assertIsNone(result['reused_rate'])
        self.assertEqual(result['fully_reused_nonempty_requests'],0)

    def test_inconsistent_prefix_identity_is_rejected(self):
        h=PrefixHistory();h.observe('a',0,[1,2])
        with self.assertRaisesRegex(ValueError,'prefix parent'):
            h.observe('b',1,[3,2])
        with self.assertRaisesRegex(ValueError,'duplicate identity'):
            PrefixHistory().observe('a',0,[1,1])


if __name__=='__main__':
    unittest.main()
