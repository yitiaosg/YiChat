"""Full-sheet cutters and the 25 offcut nodes; no production database access."""
import itertools
import unittest
import ycards as yc
from test_ycards import design

class CutsTest(unittest.TestCase):
    def product(self,cuts=(),corners='round'):
        return yc.product_geometry(yc.validate_design(design(corners=corners)),{'layers':[],'cuts':list(cuts)},'factory')

    def test_each_outer_corner_die_only_touches_one_card(self):
        for q,index,cell in [('tl',0,1),('tr',1,4),('bl',2,13),('br',3,16)]:
            for loss,value in [({'missing':True},0),({'offset':1.25},4.43)]:
                products=self.product([dict(axis=q,line=0,**loss)])
                changed=[(min(p['cells']),i,r) for p in products for i,r in enumerate(p['corners']) if r!=3.18]
                self.assertEqual(changed,[(cell,index,value)])

    def test_single_full_line_missing_yields_four_pairs(self):
        for corners,axis,line in itertools.product(['round','square'],['x','y'],range(1,4)):
            products=self.product([dict(axis=axis,line=line,missing=True)],corners)
            self.assertEqual(len(products),12)
            pairs=[p['cells'] for p in products if len(p['cells'])==2]
            self.assertEqual(len(pairs),4)
            self.assertTrue(all(b-a==(1 if axis=='x' else 4) for a,b in pairs))

    def test_offcuts_removed_only_by_cutters_that_pass_node(self):
        products=self.product([dict(axis='x',line=1,missing=True)])
        at=lambda p,x,y:[s for s in p['offcuts'] if s['node']==[x,y]]
        top=next(p for p in products if p['cells']==[1,2])
        self.assertTrue(all(not s['removed'] for s in at(top,1,0)))
        self.assertTrue(all(s['removed'] for s in at(top,1,1)))
        bottom=next(p for p in products if p['cells']==[13,14])
        self.assertTrue(all(not s['removed'] for s in at(bottom,1,4)))
        nodes={tuple(s['node']) for p in products for s in p['offcuts']}
        self.assertEqual(nodes,set(itertools.product(range(5),repeat=2)))

    def test_all_missing_line_combinations_partition_cells(self):
        lines=list(itertools.product(['x','y'],range(1,4)))
        for bits in itertools.product([False,True],repeat=6):
            cuts=[dict(axis=axis,line=line,missing=True,offset=3) for (axis,line),missing in zip(lines,bits) if missing]
            products=self.product(cuts)
            self.assertEqual(sorted(c for p in products for c in p['cells']),list(range(1,17)))
            self.assertEqual(len(products),(4-sum(bits[:3]))*(4-sum(bits[3:])))
            self.assertEqual(len({min(p['cells']) for p in products}),len(products))
            self.assertAlmostEqual(sum((p['bounds'][2]-p['bounds'][0])*(p['bounds'][3]-p['bounds'][1]) for p in products),16*53.98*85.6)

    def test_missing_cut_does_not_apply_simultaneous_skew(self):
        loss=dict(axis='x',line=1,missing=True,offset=10)
        self.assertEqual(self.product([loss]),self.product([dict(axis='x',line=1,missing=True)]))
